"""DOC2MD-19CAP extension layer.

This package is the *only* new product namespace introduced by workflow
``DOC2MD-19CAP`` (contract ``DOC2MD-19CAP-V1.0``). It is deliberately additive:

* it never writes ``parsed/``, ``raw_vault/``, ``curated/`` or ``sql/``;
* it never imports the legacy writer services (see ``io.read_bridge``);
* it never emits ``kb_*`` rows, and it does not create a second database.

Module map
----------
``identity``          canonical JSON + stable ids (source version, job, generation, objects)
``contracts``         closed-field Pydantic contracts for the 12 root payloads
``io.config``         extension config loading with fail-closed validation
``io.paths``          restricted ``extension_root`` resolution and traversal guards
``io.read_bridge``    read-only bridge onto the legacy triple artifacts
``io.artifact_store`` single-writer lock, staging, durable commit, pointer CAS
``runner``            thin orchestration used by the CLI (no business policy here)
``cli``               independent thin CLI; the 19 legacy commands are untouched

Error taxonomy
--------------
Every failure carries a stable ``code`` (``E_*``). ``EXIT_CODES`` maps codes to the
exit codes fixed by ``contracts/doc2md-19cap/v1.0.0/cli_contract.json``. The legacy
CLI keeps its own, unchanged exit codes.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "EXTENSION_LAYER_VERSION",
    "WORKFLOW_ID",
    "CONTRACT_VERSION",
    "ExtensionError",
    "EXIT_CODES",
    "exit_code_for",
]

WORKFLOW_ID = "DOC2MD-19CAP"
CONTRACT_VERSION = "DOC2MD-19CAP-V1.0"
EXTENSION_LAYER_VERSION = "1.0.0"


class ExtensionError(Exception):
    """A failure with a stable code and a contract-defined CLI exit code."""

    def __init__(self, code: str, message: str, *, detail: Any = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = detail
        self.exit_code = exit_code_for(code)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


#: ``cli_contract.json`` → ``error_exit_codes`` (0/2/3/4/5/6/7).
EXIT_CODES: dict[str, int] = {
    "request-completed": 0,
    "input-error": 2,
    "dependency-error": 3,
    "needs-review-or-not-requested-release-state": 4,
    "version-conflict": 5,
    "failed-item-or-batch": 6,
    "source-or-permission-denied": 7,
}

#: Code → semantic bucket. Unknown codes fall back to ``failed-item-or-batch``
#: so a failure never masquerades as success.
_CODE_BUCKET: dict[str, str] = {
    "E_INPUT_INVALID": "input-error",
    "E_PATH_ESCAPE": "input-error",
    "E_CONTRACT_INVALID": "input-error",
    "E_CONTRACT_UNKNOWN_ROOT": "input-error",
    "E_IDENTITY_BINARY_INPUT": "input-error",
    "E_IDENTITY_NON_FINITE": "input-error",
    "E_IDENTITY_NON_STRING_KEY": "input-error",
    "E_IDENTITY_NON_STRING_VALUE": "input-error",
    "E_IDENTITY_BAD_DOMAIN": "input-error",
    "E_IDENTITY_BAD_KIND": "input-error",
    "E_IDENTITY_BAD_PATH": "input-error",
    "E_IDENTITY_BAD_ACCESS_KEY": "input-error",
    "E_IDENTITY_BAD_SEQUENCE": "input-error",
    "E_IDENTITY_NOT_CANONICAL": "input-error",
    "E_CONFIG_INVALID": "input-error",
    "E_CONFIG_ROOT_FORBIDDEN": "input-error",
    "E_SCOPE_DENIED": "source-or-permission-denied",
    "E_SOURCE_REVOKED": "source-or-permission-denied",
    "E_ACCESS_STALE": "source-or-permission-denied",
    "E_ENGINE_UNAVAILABLE": "dependency-error",
    "E_ENGINE_SCHEMA_DRIFT": "dependency-error",
    "E_ENGINE_TIMEOUT": "dependency-error",
    "E_BASE_MISSING": "dependency-error",
    "NEEDS_BASE_PARSE": "dependency-error",
    "E_NOT_IMPLEMENTED": "dependency-error",
    "E_VERSION_CONFLICT": "version-conflict",
    "E_EVENT_COLLISION": "version-conflict",
    "E_SOURCE_VERSION_CONFLICT": "version-conflict",
    "E_REVIEW_REQUIRED": "needs-review-or-not-requested-release-state",
    "E_CONSUMER_NOT_CONNECTED": "needs-review-or-not-requested-release-state",
    "E_COMMIT_INCOMPLETE": "failed-item-or-batch",
    "E_EVIDENCE_MISMATCH": "failed-item-or-batch",
    # Structural / final-state uncertainty is a *review* outcome, not a batch failure:
    # SDD §14 routes these to "定位原XML并转复核" rather than "retry or drop".
    "E_DOCX_MERGE_INVALID": "needs-review-or-not-requested-release-state",
    "E_DOCX_REVISION_UNRESOLVED": "needs-review-or-not-requested-release-state",
    "E_TABLE_SPAN_INVALID": "needs-review-or-not-requested-release-state",
    "E_TABLE_ALIGNMENT_AMBIGUOUS": "needs-review-or-not-requested-release-state",
    "E_GEOMETRY_UNVERIFIED": "needs-review-or-not-requested-release-state",
    "E_GEOMETRY_NONINVERTIBLE": "input-error",
    "E_LOCK_BUSY": "failed-item-or-batch",
    "E_DRY_RUN_WRITE_BLOCKED": "failed-item-or-batch",
}


def exit_code_for(code: str) -> int:
    """Map a stable error code to its CLI exit code (never returns 0)."""
    bucket = _CODE_BUCKET.get(code, "failed-item-or-batch")
    return EXIT_CODES[bucket]
