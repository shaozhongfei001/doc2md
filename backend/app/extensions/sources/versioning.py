"""Source version registration (T202).

Rules reproduced from ``01_SDD.md`` §6.1 and ``IDENTITY_AND_PATHS.md``:

* ``SOURCE_NATIVE`` needs a *real* revision string; without one the registration is
  ``LOCAL_SNAPSHOT`` and ``source_revision`` must stay ``null`` — a local snapshot is
  never dressed up as a source-system version.
* ``REPROCESS`` needs the bytes and a digest; ``REUSE_QUERY`` needs an authorised
  query reference and must not copy remote full text locally; ``METADATA_ONLY``
  registers without processing.
* The same ``(source, object, native revision)`` with a **different** digest is an
  isolation conflict (``E_SOURCE_VERSION_CONFLICT``): the old version is never
  overwritten. Same digest with changed ACL is a separate access event.
* Identity is content-addressed but source-scoped: equal bytes from two sources do
  not share an authorisation decision (D-01 is preserved, not weakened).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .. import ExtensionError, identity
from ..contracts import ContractError, SourceVersion, validate_root

__all__ = [
    "RegistrationResult",
    "VersionLedgerView",
    "register_source_version",
    "detect_revision_conflicts",
]

#: Fields that participate in ``source_version_id`` (order fixed by the contract).
IDENTITY_FIELDS = (
    "source_id",
    "external_object_id",
    "version_kind",
    "source_revision",
    "snapshot_id",
    "content_sha256",
    "legacy_query_ref_hash",
)


@dataclass(frozen=True)
class VersionLedgerView:
    """Read-only projection of what the ledger already holds for one object."""

    keyed_by_revision: dict[str, dict[str, Any]]

    @classmethod
    def empty(cls) -> "VersionLedgerView":
        return cls(keyed_by_revision={})

    @classmethod
    def from_entries(cls, entries: Iterable[Mapping[str, Any]]) -> "VersionLedgerView":
        keyed: dict[str, dict[str, Any]] = {}
        for entry in entries:
            revision = entry.get("version_kind"), entry.get("source_revision"), entry.get(
                "snapshot_id"
            )
            keyed[repr(revision)] = dict(entry)
        return cls(keyed_by_revision=keyed)

    def get(self, version_kind: str, revision: str | None, snapshot_id: str | None):
        return self.keyed_by_revision.get(repr((version_kind, revision, snapshot_id)))

    def all_versions(self) -> list[dict[str, Any]]:
        return list(self.keyed_by_revision.values())


@dataclass(frozen=True)
class RegistrationResult:
    version: SourceVersion
    source_version_id: str
    access_key: str
    is_new: bool
    conflicting_version_id: str | None = None


def _identity_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("legacy_query_ref") is None:
        query_hash = None
    else:
        query_hash = identity.digest(
            "legacy-query-ref/1", {"ref": payload["legacy_query_ref"]}
        )
    return {
        "source_id": payload["source_id"],
        "external_object_id": payload["external_object_id"],
        "version_kind": payload["version_kind"],
        "source_revision": payload["source_revision"],
        "snapshot_id": payload["snapshot_id"],
        "content_sha256": payload["content_sha256"],
        "legacy_query_ref_hash": query_hash,
    }


def detect_revision_conflicts(
    payload: Mapping[str, Any], ledger: VersionLedgerView
) -> str | None:
    """Return the existing version id when the same revision carries other bytes."""
    existing = ledger.get(
        str(payload["version_kind"]),
        payload.get("source_revision"),
        payload.get("snapshot_id"),
    )
    if existing is None:
        return None
    if existing.get("content_sha256") != payload.get("content_sha256"):
        return str(existing.get("source_version_id"))
    return None


def register_source_version(
    payload: Mapping[str, Any], *, ledger: VersionLedgerView | None = None
) -> RegistrationResult:
    """Validate, identify and (logically) register one source version.

    No persistence happens here: the caller commits the returned record through the
    L01 artifact store. This function is pure so the conflict rules are unit-testable
    without a filesystem.
    """
    ledger = ledger or VersionLedgerView.empty()
    document = dict(payload)

    # Normalise before validation so a snapshot cannot smuggle a revision in.
    if document.get("version_kind") == "SOURCE_NATIVE" and not document.get("source_revision"):
        raise ExtensionError(
            "E_INPUT_INVALID",
            "SOURCE_NATIVE requires an actual source_revision; use LOCAL_SNAPSHOT instead "
            "of inventing a revision number",
        )
    if document.get("version_kind") == "LOCAL_SNAPSHOT" and document.get("source_revision"):
        raise ExtensionError(
            "E_INPUT_INVALID",
            "LOCAL_SNAPSHOT must not carry source_revision; a local snapshot is not a "
            "source-system version",
        )

    try:
        version = validate_root("source-version", document)
    except ContractError as exc:
        raise ExtensionError("E_CONTRACT_INVALID", exc.message, detail=exc.errors) from exc

    declared = document.get("source_version_id")
    computed = identity.source_version_id(_identity_payload(document))
    if declared and declared != computed:
        raise ExtensionError(
            "E_VERSION_CONFLICT",
            "declared source_version_id does not match the canonical identity of its inputs",
            detail={"declared": declared, "computed": computed},
        )

    conflict = detect_revision_conflicts(document, ledger)
    if conflict is not None:
        raise ExtensionError(
            "E_SOURCE_VERSION_CONFLICT",
            "the same source object revision already exists with a different digest; "
            "the two versions must be isolated, not merged",
            detail={
                "existing_source_version_id": conflict,
                "incoming_content_sha256": document.get("content_sha256"),
            },
        )

    existing = ledger.get(
        str(document["version_kind"]),
        document.get("source_revision"),
        document.get("snapshot_id"),
    )
    return RegistrationResult(
        version=version,
        source_version_id=computed,
        access_key=identity.source_access_key(
            str(document["source_id"]), str(document["external_object_id"])
        ),
        is_new=existing is None,
        conflicting_version_id=conflict,
    )
