"""Canonical identity for the DOC2MD-19CAP extension layer.

Contract basis
--------------
``contracts/doc2md-19cap/v1.0.0/identity.py`` is the *reference* implementation and
``contracts/doc2md-19cap/v1.0.0/identity_vectors.json`` fixes the expected digests.
This module is the product-side implementation; it must reproduce those vectors
bit-for-bit (enforced by ``backend/tests/extensions/test_contracts.py``).

Rules reproduced here (see ``contracts/.../IDENTITY_AND_PATHS.md``):

* canonical JSON is UTF-8, ``sort_keys``, compact separators, explicit ``null``,
  and **never** ``NaN`` / ``Infinity``;
* set-like inputs are sorted by the caller *before* hashing, while order-carrying
  arrays (header paths, reading order, polygons) must never be sorted;
* no Unicode NFC folding and no float rounding;
* ids are opaque to non-Python consumers unless they pass the shared vectors.

Legacy ids are explicitly **out of scope**: ``content_uid`` stays ``sha256`` and
``evidence_uid`` keeps its v1 formula in ``app.services.evidence_chain``.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "IdentityError",
    "canonical_json",
    "digest",
    "source_version_id",
    "job_key",
    "generation_id",
    "object_id",
    "candidate_evidence_id",
    "source_access_key",
    "sorted_set_values",
    "assert_order_preserving",
]

_BANNED_SEQUENCE_TYPES = (bytes, bytearray, memoryview)


class IdentityError(ValueError):
    """Raised when an identity input cannot be canonicalised deterministically."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _reject_binary(value: Any, path: str) -> None:
    """Reject payloads that would hash differently across transports."""
    if isinstance(value, _BANNED_SEQUENCE_TYPES):
        raise IdentityError(
            "E_IDENTITY_BINARY_INPUT",
            f"{path} is a binary buffer; identity inputs must be JSON values",
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise IdentityError(
            "E_IDENTITY_NON_FINITE",
            f"{path} is {value!r}; NaN/Infinity never enter an identity digest",
        )
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise IdentityError(
                    "E_IDENTITY_NON_STRING_KEY",
                    f"{path} has a non-string key {key!r}",
                )
            _reject_binary(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_binary(item, f"{path}[{index}]")


def canonical_json(value: Any) -> bytes:
    """Serialise ``value`` to the canonical byte string used by every new id.

    The output is deterministic for a given Python value and is *not* a
    normalising encoder: it refuses ambiguous inputs instead of guessing.
    """
    _reject_binary(value, "$")
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:  # Decimal, sets, NaN, ...
        raise IdentityError(
            "E_IDENTITY_NOT_CANONICAL",
            f"value is not canonical-JSON serialisable: {exc}",
        ) from exc
    return text.encode("utf-8")


def digest(domain: str, payload: Any) -> str:
    """Return the lowercase SHA-256 hex digest of ``{domain, payload}``."""
    if not isinstance(domain, str) or not domain:
        raise IdentityError("E_IDENTITY_BAD_DOMAIN", "domain must be a non-empty string")
    if "/" not in domain:
        raise IdentityError(
            "E_IDENTITY_BAD_DOMAIN",
            f"domain {domain!r} must carry an explicit version suffix, e.g. 'source-version/1'",
        )
    return hashlib.sha256(
        canonical_json({"domain": domain, "payload": payload})
    ).hexdigest()


def source_version_id(payload: Mapping[str, Any]) -> str:
    """``sv-<sha256>`` for a source version registration."""
    return "sv-" + digest("source-version/1", dict(payload))


def job_key(payload: Mapping[str, Any]) -> str:
    """``<sha256>`` of the enhancement job input (read-set, profile, locks, patches)."""
    return digest("enhancement-job/1", dict(payload))


def generation_id(payload: Mapping[str, Any]) -> str:
    """``<sha256>`` of a generation; must never contain the output's own id."""
    return digest("enhancement-generation/1", dict(payload))


def object_id(
    kind: str,
    generation: str,
    source_locator: Any,
    local_path: str,
) -> str:
    """``<kind>-<sha256>`` for an object inside one generation.

    ``kind`` is a short lowercase type tag (``cell``, ``table``, ``note`` …).
    Legacy IR ``block_id`` values are **not** rewritten with this function.
    """
    if not isinstance(kind, str) or not kind or kind != kind.lower():
        raise IdentityError(
            "E_IDENTITY_BAD_KIND", f"kind must be a non-empty lowercase tag, got {kind!r}"
        )
    if not isinstance(local_path, str) or not local_path:
        raise IdentityError("E_IDENTITY_BAD_PATH", "local_path must be a non-empty string")
    return kind + "-" + digest(
        "enhancement-object/1",
        {
            "generation_id": generation,
            "kind": kind,
            "source_locator": source_locator,
            "local_path": local_path,
        },
    )


def candidate_evidence_id(
    source_version: str,
    generation: str,
    locator: Any,
    observation_class: str,
) -> str:
    """``ce-<sha256>`` for candidate evidence (never the legacy ``evidence_uid``)."""
    return "ce-" + digest(
        "candidate-evidence/1",
        {
            "source_version_id": source_version,
            "generation_id": generation,
            "locator": locator,
            "observation_class": observation_class,
        },
    )


def source_access_key(source_id: str, external_object_id: str) -> str:
    """``access-<sha256>`` lease key; one lease per source *object*, not per source."""
    for name, value in (("source_id", source_id), ("external_object_id", external_object_id)):
        if not isinstance(value, str) or not value:
            raise IdentityError("E_IDENTITY_BAD_ACCESS_KEY", f"{name} must be non-empty")
    return "access-" + digest(
        "source-access-key/1",
        {"source_id": source_id, "external_object_id": external_object_id},
    )


def sorted_set_values(values: Iterable[str]) -> list[str]:
    """Return a sorted, de-duplicated list for *set-like* identity inputs."""
    seen: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise IdentityError(
                "E_IDENTITY_NON_STRING_VALUE", "set-like identity values must be strings"
            )
        if value not in seen:
            seen.append(value)
    return sorted(seen)


def assert_order_preserving(values: Sequence[Any], field: str) -> None:
    """Guard the opposite mistake: sorting an order-carrying array.

    Header paths, reading order and polygons carry meaning, so a caller that
    accidentally pipes them through :func:`sorted_set_values` loses evidence.
    This helper documents and enforces the intent at the call site.
    """
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise IdentityError(
            "E_IDENTITY_BAD_SEQUENCE", f"{field} must be an order-carrying sequence"
        )
