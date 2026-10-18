"""Release preparation, route switching and rollback (SDD 13.3, ``PI-F03-03``).

Three sentences drive every rule here:

    "prepare_release锁定完整input/read-set及对账报告；LOCAL_READY仅表示本地产物完成，
     ACTIVE还需全部必需consumer的真实ACK。"

    "路由切换采用expected_previous release与scope；新任务读取并pin当前route_epoch，
     旧任务不被覆写。"

    "rollback只变指定scope的当前指针…绝不回退access epoch或墓碑。source revoke优先于任何回退目标。"

Consequences worth stating explicitly, because each one is a way to fake success:

* ``LOCAL_READY`` is never upgraded to ``ACTIVE`` without a real ACK per required
  consumer. A simulated ACK stays ``SIMULATED`` and cannot reach ``ACTIVE``.
* Switching is a CAS on the pointer: a caller holding a stale
  ``expected_previous_release`` gets ``E_VERSION_CONFLICT``.
* Rollback moves the read pointer **only**. The access ledger and tombstones come
  back unchanged, and a target that includes a revoked version is refused —
  otherwise a rollback would quietly re-open content that was withdrawn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError

LOCAL_READY = "LOCAL_READY"
ACTIVE = "ACTIVE"

__all__ = [
    "ACTIVE",
    "LOCAL_READY",
    "Release",
    "activate",
    "prepare_release",
    "rollback",
    "switch_route",
]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


def _conflict(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_VERSION_CONFLICT", message, detail=detail or None)


@dataclass(frozen=True)
class Release:
    release_id: str
    generation_id: str
    scope: str
    read_set: tuple[str, ...]
    reconcile_clean: bool
    status: str = LOCAL_READY
    acks: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "generation_id": self.generation_id,
            "scope": self.scope,
            "read_set": list(self.read_set),
            "reconcile_clean": self.reconcile_clean,
            "status": self.status,
            "acks": {name: dict(ack) for name, ack in self.acks.items()},
        }


def prepare_release(
    *,
    release_id: str,
    generation_id: str,
    scope: str,
    read_set: Sequence[str],
    reconcile_clean: bool,
) -> Release:
    """Lock the read-set and the reconciliation verdict; status is LOCAL_READY.

    A dirty reconciliation does not produce a release at all: publishing a version
    whose denominator does not add up would make every later comparison meaningless.
    """
    for name, value in (("release_id", release_id), ("generation_id", generation_id), ("scope", scope)):
        if not isinstance(value, str) or not value:
            raise _input_error(f"{name} must be a non-empty string, got {value!r}")
    if isinstance(read_set, (str, bytes)) or not isinstance(read_set, Sequence) or not read_set:
        raise _input_error("read_set must be a non-empty sequence of source versions")
    for item in read_set:
        if not isinstance(item, str) or not item:
            raise _input_error(f"read_set contains {item!r}")
    if not isinstance(reconcile_clean, bool):
        raise _input_error("reconcile_clean must be the reconciliation verdict, an explicit bool")
    if not reconcile_clean:
        raise _input_error(
            "reconciliation has blockers; a release may not be prepared from a batch whose "
            "denominator does not reconcile"
        )
    return Release(
        release_id=release_id,
        generation_id=generation_id,
        scope=scope,
        read_set=tuple(read_set),
        reconcile_clean=True,
    )


def activate(
    release: Release,
    *,
    required_consumers: Sequence[str],
    acks: Mapping[str, Mapping[str, Any]],
) -> Release:
    """Promote to ACTIVE only when every required consumer has a real ACK.

    An ACK must carry ``status="ACKED"`` **and** name this release's generation;
    an ACK for a previous generation proves the consumer has not caught up.
    """
    if not isinstance(release, Release):
        raise _input_error("release must be a Release instance")
    if isinstance(required_consumers, (str, bytes)) or not isinstance(required_consumers, Sequence):
        raise _input_error("required_consumers must be a sequence of names")
    if not required_consumers:
        raise _input_error(
            "required_consumers is empty; a release cannot become ACTIVE without any "
            "consumer, otherwise ACTIVE means nothing"
        )

    missing: list[str] = []
    simulated: list[str] = []
    stale: list[str] = []
    for name in required_consumers:
        ack = acks.get(name)
        if ack is None:
            missing.append(name)
            continue
        if ack.get("status") == "SIMULATED":
            simulated.append(name)
            continue
        if ack.get("status") != "ACKED" or ack.get("generation_id") != release.generation_id:
            stale.append(name)

    if missing or simulated or stale:
        raise ExtensionError(
            "E_CONSUMER_NOT_CONNECTED",
            "not every required consumer acknowledged this generation; the release stays "
            "LOCAL_READY",
            detail={
                "missing": missing,
                "simulated": simulated,
                "stale_or_wrong_generation": stale,
                "release_id": release.release_id,
            },
        )

    return Release(
        release_id=release.release_id,
        generation_id=release.generation_id,
        scope=release.scope,
        read_set=release.read_set,
        reconcile_clean=release.reconcile_clean,
        status=ACTIVE,
        acks={name: dict(acks[name]) for name in required_consumers},
    )


def switch_route(
    *,
    expected_previous_release: str,
    current_release: str,
    target_release: str,
    scope: str,
    route_epoch: int,
) -> dict[str, Any]:
    """CAS the read pointer for ``scope`` and bump ``route_epoch``.

    ``route_epoch`` is what new tasks pin, so an in-flight task keeps reading the
    version it started with (SDD: "旧任务不被覆写").
    """
    for name, value in (
        ("expected_previous_release", expected_previous_release),
        ("current_release", current_release),
        ("target_release", target_release),
        ("scope", scope),
    ):
        if not isinstance(value, str) or not value:
            raise _input_error(f"{name} must be a non-empty string, got {value!r}")
    if isinstance(route_epoch, bool) or not isinstance(route_epoch, int) or route_epoch < 0:
        raise _input_error(f"route_epoch must be a non-negative integer, got {route_epoch!r}")
    if expected_previous_release != current_release:
        raise _conflict(
            "the route pointer moved since this switch was prepared; re-read the current "
            "release and prepare again rather than forcing the switch",
            expected=expected_previous_release,
            current=current_release,
        )
    if target_release == current_release:
        raise _input_error("target release equals the current release; nothing to switch")

    return {
        "scope": scope,
        "previous_release": current_release,
        "current_release": target_release,
        "route_epoch": route_epoch + 1,
    }


def rollback(
    *,
    expected_current_release: str,
    current_release: str,
    previous_target: str,
    scope: str,
    route_epoch: int,
    access_ledger: Mapping[str, Any],
    tombstones: Sequence[str],
    target_contents: Sequence[str] = (),
) -> dict[str, Any]:
    """Move the read pointer back for one scope — and nothing else.

    ``access_ledger`` and ``tombstones`` are echoed back untouched on purpose: a
    rollback that restored an access epoch would re-open withdrawn content, and a
    rollback that dropped a tombstone would let a revoked version resurrect.
    """
    for name, value in (
        ("expected_current_release", expected_current_release),
        ("current_release", current_release),
        ("previous_target", previous_target),
        ("scope", scope),
    ):
        if not isinstance(value, str) or not value:
            raise _input_error(f"{name} must be a non-empty string, got {value!r}")

    if expected_current_release != current_release:
        raise _conflict(
            "the route pointer moved since the rollback was prepared",
            expected=expected_current_release,
            current=current_release,
        )
    if previous_target == current_release:
        raise _input_error("rollback target equals the current release; nothing to roll back")

    tombstone_set = {str(item) for item in tombstones}
    blocked = sorted(tombstone_set.intersection(target_contents))
    if blocked:
        raise ExtensionError(
            "E_SCOPE_DENIED",
            "the rollback target contains revoked content; source revocation takes "
            "priority over any rollback target",
            detail={"scope": scope, "revoked": blocked},
        )

    return {
        "scope": scope,
        "previous_release": current_release,
        "current_release": previous_target,
        "route_epoch": route_epoch + 1,
        "access_ledger": dict(access_ledger),
        "tombstones": list(tombstones),
        "note": (
            "only the read pointer for this scope moved; the access ledger and tombstones "
            "are unchanged and tasks started after the switch keep their own pin"
        ),
    }
