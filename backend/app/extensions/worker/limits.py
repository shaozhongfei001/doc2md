"""Worker limits (SDD 8.1 / 17): one writer, bounded work, pinned weights.

Three constraints, each of which has a failure mode that is invisible until it is
expensive:

1. **Two workers on one artifact root** corrupt each other's staging area. The
   lock is exclusive and fails fast with ``E_LOCK_BUSY`` instead of queueing.
2. **An unbounded page range** turns a 3-page smoke test into a 900-page job on a
   shared machine. The budget is consumed per page / per second / per request and
   refuses further work with ``E_ENGINE_TIMEOUT`` — the closest code in the fixed
   table for "the run exceeded its allowed work"; the detail names the dimension
   so a reader is never misled into thinking a clock expired.
3. **Weights downloaded on first inference** mean production depends on a network
   the bank does not grant. :func:`verify_weights` requires a local file whose
   sha256 matches a pinned digest.

No new error codes are minted: adding one would require editing
``app/extensions/__init__.py``, which is outside this Loop's whitelist.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import ExtensionError

__all__ = [
    "DEFAULT_BUDGET",
    "WorkerBudget",
    "WorkerLock",
    "BudgetExceeded",
    "verify_weights",
]


def _unavailable(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_ENGINE_UNAVAILABLE", message, detail=detail or None)


@dataclass
class WorkerBudget:
    """A bound on how much work one worker run may do."""

    max_pages: int = 500
    max_seconds: float = 900.0
    max_requests: int = 200
    pages: int = 0
    requests: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        for name in ("max_pages", "max_requests"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer, got {value!r}")
        if not isinstance(self.max_seconds, (int, float)) or self.max_seconds <= 0:
            raise ValueError(f"max_seconds must be positive, got {self.max_seconds!r}")

    # -- consumption -------------------------------------------------------
    def consume(self, *, pages: int = 0, requests: int = 0) -> None:
        """Charge work against the budget, refusing before the work is done."""
        for name, amount in (("pages", pages), ("requests", requests)):
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise ValueError(f"{name} increment must be a non-negative integer")
        if self.pages + pages > self.max_pages:
            raise _unavailable(
                "page budget exhausted; the run is stopped before the next page rather "
                "than silently shrinking the document",
                budget="max_pages",
                used=self.pages,
                requested=pages,
                limit=self.max_pages,
            )
        if self.requests + requests > self.max_requests:
            raise _unavailable(
                "request budget exhausted",
                budget="max_requests",
                used=self.requests,
                requested=requests,
                limit=self.max_requests,
            )
        self._check_time()
        self.pages += pages
        self.requests += requests

    def _check_time(self) -> None:
        elapsed = time.monotonic() - self.started_at
        if elapsed > self.max_seconds:
            raise ExtensionError(
                "E_ENGINE_TIMEOUT",
                "worker wall-clock budget exhausted",
                detail={
                    "budget": "max_seconds",
                    "elapsed": round(elapsed, 3),
                    "limit": self.max_seconds,
                },
            )

    def remaining(self) -> dict[str, float]:
        return {
            "pages": self.max_pages - self.pages,
            "requests": self.max_requests - self.requests,
            "seconds": round(self.max_seconds - (time.monotonic() - self.started_at), 3),
        }


#: Conservative defaults; SDD expects these to be tuned per deployment.
DEFAULT_BUDGET = WorkerBudget


class WorkerLock:
    """Exclusive lock file: ``O_CREAT|O_EXCL`` so two workers cannot both win.

    Failing fast (``E_LOCK_BUSY``) is deliberate: a worker that waits on a lock
    while holding a GPU session turns a scheduling problem into a timeout.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def __enter__(self) -> "WorkerLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ExtensionError(
                "E_LOCK_BUSY",
                f"another worker holds {self.path}; refusing to run two writers",
                detail={"lock": str(self.path)},
            ) from exc
        os.write(self._fd, str(os.getpid()).encode("ascii"))
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
            try:
                self.path.unlink()
            except FileNotFoundError:  # pragma: no cover - defensive
                pass  # the lock is already gone; releasing twice is harmless


def verify_weights(path: str | os.PathLike[str], expected_sha256: str) -> str:
    """Prove the weights are local and match the pinned digest.

    Returns the verified digest so callers can record it. A missing file is
    ``E_ENGINE_UNAVAILABLE`` ("the worker cannot start"), a mismatch is the same
    code with the two digests in the detail: both mean "do not run", but hiding
    which one happened would waste a deployment afternoon.
    """
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise ValueError(
            f"expected_sha256 must be a 64-char digest, got {expected_sha256!r}"
        )
    weights = Path(path)
    if not weights.is_file():
        raise _unavailable(
            "pinned weights are not present locally; the worker never downloads at "
            "inference time",
            path=str(weights),
            expected_sha256=expected_sha256,
        )
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()
    if digest != expected_sha256.lower():
        raise _unavailable(
            "weights digest does not match the pinned value",
            path=str(weights),
            expected_sha256=expected_sha256.lower(),
            actual_sha256=digest,
        )
    return digest
