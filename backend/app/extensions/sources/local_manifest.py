"""Local manifest connector — the only adapter usable without a bank interface.

The manifest is a JSON document written by whoever owns the sample corpus::

    {
      "source_id": "demo_bank_policy",
      "selection_scope": {"purpose": ["PREVISIT"], "classification": ["INTERNAL"]},
      "authorization_snapshot_ref": "authz-demo-1",
      "objects": [
        {"external_object_id": "policy_demo_01", "source_revision": "r1",
         "content_sha256": "...", "snapshot_id": null},
        {"external_object_id": "policy_demo_02", "source_revision": null,
         "content_sha256": "...", "snapshot_id": "snap-2"}
      ],
      "revoked": [{"external_object_id": "policy_demo_03", "reason": "withdrawn"}]
    }

Properties that matter for the ledger
-------------------------------------
* Ordering is the array index, encoded as a zero-padded decimal ``source_order_token``.
  That is a *declared* order, which is the strongest thing a local file can offer;
  the sync layer still refuses to use arrival order when no token exists.
* Deletions and revocations are only reported when the manifest says so. A manifest
  without a ``revoked`` array reports ``reports_revocations=False`` — the sync layer
  then refuses to turn a missing object into a delete.
* ``probe()`` distinguishes ``NOT_CONNECTED`` (file absent) from ``PROBE_FAILED``
  (unreadable/malformed) from ``UNSUPPORTED`` (declared capability gap) so a caller
  can never mistake "no interface" for "nothing changed".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import ExtensionError
from .base import (
    CapabilityReport,
    EventPage,
    SourceCapabilities,
    SourceEventPayload,
    capability_gaps,
)

__all__ = ["LocalManifestConnector", "manifest_scope_hash"]


def manifest_scope_hash(source_id: str, scope: dict[str, Any]) -> str:
    """Stable hash of the selection scope, so a scope change cannot reuse a cursor."""
    payload = json.dumps(
        {"source_id": source_id, "scope": scope}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ManifestObject:
    external_object_id: str
    source_revision: str | None
    content_sha256: str
    snapshot_id: str | None


class LocalManifestConnector:
    """Deterministic, file-backed ``SourceAdapter``."""

    adapter_name = "local_manifest"

    def __init__(self, manifest_path: Path | str) -> None:
        self.manifest_path = Path(manifest_path)

    # ------------------------------------------------------------------ reading
    def _load(self) -> dict[str, Any]:
        if not self.manifest_path.is_file():
            raise ExtensionError(
                "E_ENGINE_UNAVAILABLE",
                f"local manifest not found: {self.manifest_path}",
                detail={"adapter": self.adapter_name},
            )
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExtensionError(
                "E_ENGINE_SCHEMA_DRIFT", f"local manifest is unreadable: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ExtensionError("E_ENGINE_SCHEMA_DRIFT", "local manifest must be an object")
        if not payload.get("source_id"):
            raise ExtensionError("E_ENGINE_SCHEMA_DRIFT", "local manifest has no source_id")
        objects = payload.get("objects")
        if not isinstance(objects, list) or not objects:
            raise ExtensionError(
                "E_ENGINE_SCHEMA_DRIFT", "local manifest needs a non-empty objects array"
            )
        return payload

    def capabilities(self) -> SourceCapabilities:
        payload = self._load()
        return SourceCapabilities(
            incremental_events=True,
            cursor=True,
            full_enumeration=True,
            reports_deletions="deleted" in payload,
            reports_revocations="revoked" in payload,
            authorization_lease=bool(payload.get("access_lease_seconds")),
        )

    def probe(self) -> CapabilityReport:
        try:
            payload = self._load()
        except ExtensionError as exc:
            state = "NOT_CONNECTED" if exc.code == "E_ENGINE_UNAVAILABLE" else "PROBE_FAILED"
            return CapabilityReport(
                adapter=self.adapter_name,
                source_id=str(self.manifest_path),
                declared=SourceCapabilities(False, False, False, False, False),
                reachable=False,
                state=state,
                gaps=("NOT_CONNECTED",) if state == "NOT_CONNECTED" else ("PROBE_FAILED",),
                detail=exc.to_dict(),
            )
        caps = self.capabilities()
        gaps = tuple(capability_gaps(caps))
        # A manifest is reachable even when it cannot report revocations; the gap is
        # recorded, not hidden, and it is the caller's job to keep those objects
        # out of any delete reconciliation.
        return CapabilityReport(
            adapter=self.adapter_name,
            source_id=payload["source_id"],
            declared=caps,
            reachable=True,
            state="CONNECTED" if not gaps else "CONNECTED_WITH_GAPS",
            gaps=gaps,
            detail={"objects": len(payload["objects"])},
        )

    # ------------------------------------------------------------------ paging
    def _objects(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for index, raw in enumerate(payload["objects"]):
            if not isinstance(raw, dict) or not raw.get("external_object_id"):
                raise ExtensionError(
                    "E_ENGINE_SCHEMA_DRIFT", f"objects[{index}] lacks external_object_id"
                )
            if not raw.get("content_sha256"):
                raise ExtensionError(
                    "E_ENGINE_SCHEMA_DRIFT", f"objects[{index}] lacks content_sha256"
                )
            entries.append(raw)
        return entries

    def enumerate_all(self, *, scope: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload = self._load()
        return [dict(item) for item in self._objects(payload)]

    def fetch_page(
        self, *, cursor: str | None, page_size: int, scope: dict[str, Any] | None = None
    ) -> EventPage:
        if page_size <= 0:
            raise ExtensionError("E_INPUT_INVALID", "page_size must be positive")
        payload = self._load()
        entries = self._objects(payload)
        scope_payload = scope if scope is not None else payload.get("selection_scope") or {}

        try:
            start = int(cursor) if cursor is not None else 0
        except (TypeError, ValueError) as exc:
            raise ExtensionError(
                "E_INPUT_INVALID", f"cursor is not a manifest offset: {cursor!r}"
            ) from exc
        if start < 0 or start > len(entries):
            raise ExtensionError(
                "E_INPUT_INVALID",
                f"cursor {start} is outside the manifest range 0..{len(entries)}",
            )

        window = entries[start : start + page_size]
        events: list[SourceEventPayload] = []
        for offset, raw in enumerate(window):
            index = start + offset
            object_id = str(raw["external_object_id"])
            revision = raw.get("source_revision")
            snapshot_id = raw.get("snapshot_id")
            version_kind = "SOURCE_NATIVE" if revision else "LOCAL_SNAPSHOT"
            version_id = _version_id(
                str(payload["source_id"]), object_id, version_kind, revision, snapshot_id,
                str(raw["content_sha256"]),
            )
            events.append(
                SourceEventPayload(
                    event_id=f"{payload['source_id']}:{index}:{object_id}",
                    external_object_id=object_id,
                    event_type="UPSERT",
                    source_version_id=version_id,
                    payload_hash=hashlib.sha256(
                        json.dumps(raw, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")).encode("utf-8")
                    ).hexdigest(),
                    source_order_token=f"{index:08d}",
                    access_revision="1",
                    observed_at=str(raw.get("observed_at") or "1970-01-01T00:00:00Z"),
                )
            )

        # Explicit revocations are appended once, after the last object page, so a
        # partial enumeration never emits a revocation it has not been told about.
        revoked = payload.get("revoked") or []
        end = start + len(window)
        if revoked and end >= len(entries):
            for offset, raw in enumerate(revoked):
                object_id = str(raw.get("external_object_id") or "")
                if not object_id:
                    raise ExtensionError(
                        "E_ENGINE_SCHEMA_DRIFT", "revoked entry lacks external_object_id"
                    )
                events.append(
                    SourceEventPayload(
                        event_id=f"{payload['source_id']}:revoke:{object_id}",
                        external_object_id=object_id,
                        event_type="REVOKE",
                        source_version_id=None,
                        payload_hash=hashlib.sha256(
                            json.dumps(raw, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")
                        ).hexdigest(),
                        source_order_token=f"{len(entries) + offset:08d}",
                        access_revision="2",
                        observed_at=str(raw.get("observed_at") or "1970-01-01T00:00:00Z"),
                    )
                )

        complete = end >= len(entries)
        return EventPage(
            source_id=str(payload["source_id"]),
            events=tuple(events),
            next_cursor=str(end) if not complete else None,
            enumeration_complete=complete,
            selection_scope_hash=manifest_scope_hash(str(payload["source_id"]), scope_payload),
            authorization_snapshot_ref=str(payload.get("authorization_snapshot_ref") or "authz-local"),
        )


def _version_id(
    source_id: str,
    external_object_id: str,
    version_kind: str,
    source_revision: str | None,
    snapshot_id: str | None,
    content_sha256: str,
) -> str:
    """Same formula as ``identity.source_version_id``; kept local to avoid a cycle."""
    from .. import identity

    return identity.source_version_id(
        {
            "source_id": source_id,
            "external_object_id": external_object_id,
            "version_kind": version_kind,
            "source_revision": source_revision,
            "snapshot_id": snapshot_id,
            "content_sha256": content_sha256,
            "legacy_query_ref_hash": None,
        }
    )
