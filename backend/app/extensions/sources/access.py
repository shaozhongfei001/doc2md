"""Access ledger: revocation precedence, leases and anti-resurrection tombstones.

Contract basis: ``01_SDD.md`` §4.3, §6.2, §13.2.

Rules that this module exists to enforce
----------------------------------------
1. **A revocation takes effect immediately on the write path**, before any other
   event in the same page is processed. A page whose other objects fail to parse
   must not delay a revocation the project has already been told about.
2. **A tombstone blocks a later stale UPSERT.** Arrival order is not evidence; when
   the order token is incomparable the event is quarantined for a source-side check,
   and the object stays unreadable.
3. **RESTORE is not "newer therefore allowed".** It needs an explicit regrant
   reference; without one the object stays revoked.
4. **Reads check the lease.** An expired lease that cannot be re-verified is refused
   (``E_ACCESS_STALE``) rather than silently extended.

The ledger is a plain value object. Persisting it is the artifact store's job, so the
rules stay testable without a filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping

from .. import ExtensionError, identity

__all__ = ["AccessRecord", "AccessDecision", "AccessLedger"]

READ_STATES = ("ACTIVE",)


@dataclass(frozen=True)
class AccessRecord:
    source_id: str
    external_object_id: str
    access_epoch: int
    state: str  # ACTIVE | REVOKED | EXPIRED | QUARANTINED
    current_source_version_id: str | None
    permission_ref: str
    purpose_scopes: tuple[str, ...]
    authority_decision_ref: str | None = None
    last_deny_event_id: str | None = None
    lease_expires_at: str | None = None
    observed_at: str = "1970-01-01T00:00:00Z"
    last_order_token: str | None = None

    @property
    def access_key(self) -> str:
        return identity.source_access_key(self.source_id, self.external_object_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "external_object_id": self.external_object_id,
            "access_epoch": self.access_epoch,
            "state": self.state,
            "current_source_version_id": self.current_source_version_id,
            "permission_ref": self.permission_ref,
            "purpose_scopes": list(self.purpose_scopes),
            "authority_decision_ref": self.authority_decision_ref,
            "last_deny_event_id": self.last_deny_event_id,
            "lease_expires_at": self.lease_expires_at,
            "observed_at": self.observed_at,
            "last_order_token": self.last_order_token,
        }


@dataclass(frozen=True)
class AccessDecision:
    applied: bool
    state: str
    event_id: str
    external_object_id: str
    outcome: str  # APPLIED | ALREADY_REVOKED | STALE_EVENT | QUARANTINED | DUPLICATE
    reason: str
    epoch: int


@dataclass
class AccessLedger:
    """Per-object access state with tombstones that survive restarts."""

    records: dict[str, AccessRecord] = field(default_factory=dict)
    tombstones: dict[str, str] = field(default_factory=dict)  # object_id -> revoke event id

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _key(source_id: str, external_object_id: str) -> str:
        return f"{source_id}::{external_object_id}"

    def get(self, source_id: str, external_object_id: str) -> AccessRecord | None:
        return self.records.get(self._key(source_id, external_object_id))

    def is_tombstoned(self, source_id: str, external_object_id: str) -> bool:
        return self._key(source_id, external_object_id) in self.tombstones

    def as_dict(self) -> dict[str, Any]:
        return {
            "records": {k: v.as_dict() for k, v in sorted(self.records.items())},
            "tombstones": dict(sorted(self.tombstones.items())),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AccessLedger":
        records = {
            key: AccessRecord(
                source_id=value["source_id"],
                external_object_id=value["external_object_id"],
                access_epoch=int(value["access_epoch"]),
                state=str(value["state"]),
                current_source_version_id=value.get("current_source_version_id"),
                permission_ref=str(value.get("permission_ref") or ""),
                purpose_scopes=tuple(value.get("purpose_scopes") or ()),
                authority_decision_ref=value.get("authority_decision_ref"),
                last_deny_event_id=value.get("last_deny_event_id"),
                lease_expires_at=value.get("lease_expires_at"),
                observed_at=str(value.get("observed_at") or "1970-01-01T00:00:00Z"),
                last_order_token=value.get("last_order_token"),
            )
            for key, value in (payload.get("records") or {}).items()
        }
        return cls(records=records, tombstones=dict(payload.get("tombstones") or {}))

    # -------------------------------------------------------------------- apply
    def apply(self, event: Mapping[str, Any]) -> AccessDecision:
        """Apply one validated event. Revocations win over everything in the page."""
        source_id = str(event["source_id"])
        object_id = str(event["external_object_id"])
        event_type = str(event["event_type"])
        event_id = str(event["event_id"])
        order_token = event.get("source_order_token")
        key = self._key(source_id, object_id)
        record = self.records.get(key)

        if event_type in ("REVOKE", "DELETE"):
            epoch = (record.access_epoch if record else 0) + 1
            self.records[key] = AccessRecord(
                source_id=source_id,
                external_object_id=object_id,
                access_epoch=epoch,
                state="REVOKED",
                current_source_version_id=None,
                permission_ref=str(event.get("permission_ref") or (record.permission_ref if record else "unknown")),
                purpose_scopes=tuple(event.get("purpose_scopes") or (record.purpose_scopes if record else ())),
                authority_decision_ref=None,
                last_deny_event_id=event_id,
                lease_expires_at=None,
                observed_at=str(event.get("observed_at") or "1970-01-01T00:00:00Z"),
                last_order_token=str(order_token) if order_token is not None else None,
            )
            self.tombstones[key] = event_id
            return AccessDecision(
                applied=True,
                state="REVOKED",
                event_id=event_id,
                external_object_id=object_id,
                outcome="APPLIED",
                reason="revocation recorded; reads are refused immediately",
                epoch=epoch,
            )

        if event_type == "RESTORE":
            regrant = event.get("authority_decision_ref")
            if not regrant:
                return AccessDecision(
                    applied=False,
                    state=record.state if record else "REVOKED",
                    event_id=event_id,
                    external_object_id=object_id,
                    outcome="QUARANTINED",
                    reason=(
                        "RESTORE without an explicit regrant reference; a newer version is "
                        "not evidence that access was granted again"
                    ),
                    epoch=record.access_epoch if record else 0,
                )
            epoch = (record.access_epoch if record else 0) + 1
            self.records[key] = AccessRecord(
                source_id=source_id,
                external_object_id=object_id,
                access_epoch=epoch,
                state="ACTIVE",
                current_source_version_id=event.get("source_version_id"),
                permission_ref=str(event.get("permission_ref") or (record.permission_ref if record else "unknown")),
                purpose_scopes=tuple(event.get("purpose_scopes") or (record.purpose_scopes if record else ())),
                authority_decision_ref=str(regrant),
                last_deny_event_id=record.last_deny_event_id if record else None,
                lease_expires_at=event.get("lease_expires_at"),
                observed_at=str(event.get("observed_at") or "1970-01-01T00:00:00Z"),
                last_order_token=str(order_token) if order_token is not None else None,
            )
            self.tombstones.pop(key, None)
            return AccessDecision(
                applied=True,
                state="ACTIVE",
                event_id=event_id,
                external_object_id=object_id,
                outcome="APPLIED",
                reason="regrant recorded with an explicit authority reference",
                epoch=epoch,
            )

        # UPSERT
        if self.is_tombstoned(source_id, object_id):
            comparable = (
                record is not None
                and record.last_order_token is not None
                and order_token is not None
                and record.last_order_token.isdigit()
                and str(order_token).isdigit()
            )
            if comparable and int(str(order_token)) > int(record.last_order_token):
                outcome, reason = (
                    "STALE_EVENT",
                    "an upsert arrived after the revocation; access stays refused until a "
                    "RESTORE with a regrant reference",
                )
            else:
                outcome, reason = (
                    "QUARANTINED",
                    "upsert order is not comparable with the revocation; query the source "
                    "instead of trusting arrival order",
                )
            return AccessDecision(
                applied=False,
                state="REVOKED",
                event_id=event_id,
                external_object_id=object_id,
                outcome=outcome,
                reason=reason,
                epoch=record.access_epoch if record else 0,
            )

        epoch = record.access_epoch if record else 1
        self.records[key] = replace(
            record,
            state="ACTIVE",
            current_source_version_id=event.get("source_version_id"),
            observed_at=str(event.get("observed_at") or "1970-01-01T00:00:00Z"),
            last_order_token=str(order_token) if order_token is not None else (
                record.last_order_token if record else None
            ),
            lease_expires_at=event.get("lease_expires_at"),
            authority_decision_ref=event.get("authority_decision_ref")
            or record.authority_decision_ref,
        ) if record else AccessRecord(
            source_id=source_id,
            external_object_id=object_id,
            access_epoch=1,
            state="ACTIVE",
            current_source_version_id=event.get("source_version_id"),
            permission_ref=str(event.get("permission_ref") or "unknown"),
            purpose_scopes=tuple(event.get("purpose_scopes") or ()),
            observed_at=str(event.get("observed_at") or "1970-01-01T00:00:00Z"),
            last_order_token=str(order_token) if order_token is not None else None,
            lease_expires_at=event.get("lease_expires_at"),
        )
        return AccessDecision(
            applied=True,
            state="ACTIVE",
            event_id=event_id,
            external_object_id=object_id,
            outcome="APPLIED",
            reason="upsert recorded",
            epoch=epoch,
        )

    # --------------------------------------------------------------------- read
    def check_read(
        self,
        source_id: str,
        external_object_id: str,
        *,
        purpose: str | None = None,
        now: datetime | None = None,
    ) -> AccessRecord:
        """Raise when the object must not be read right now."""
        record = self.get(source_id, external_object_id)
        if record is None:
            raise ExtensionError(
                "E_SCOPE_DENIED", "object is not registered in the access ledger"
            )
        if record.state != "ACTIVE":
            raise ExtensionError(
                "E_SOURCE_REVOKED",
                f"access state is {record.state}; the source revoked this object",
                detail={"last_deny_event_id": record.last_deny_event_id},
            )
        if record.lease_expires_at:
            now = now or datetime.now(timezone.utc)
            expires = datetime.fromisoformat(record.lease_expires_at.replace("Z", "+00:00"))
            if now >= expires:
                raise ExtensionError(
                    "E_ACCESS_STALE",
                    "the access lease expired and could not be re-verified",
                    detail={"lease_expires_at": record.lease_expires_at},
                )
        if purpose is not None and record.purpose_scopes and purpose not in record.purpose_scopes:
            raise ExtensionError(
                "E_SCOPE_DENIED",
                f"purpose {purpose!r} is not in the granted scopes {list(record.purpose_scopes)}",
            )
        return record
