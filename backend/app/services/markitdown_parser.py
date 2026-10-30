from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.adapters.markitdown_adapter import (
    ERROR_PARSER_RUNTIME,
    PARSER_ADAPTER_VERSION,
    PARSER_NAME,
    MarkItDownAdapter,
    MarkItDownAdapterError,
)
from app.core.config import AppConfig, ensure_readonly
from app.core.database import create_db_engine, create_session_factory
from app.core.ids import compute_sha256
from app.core.parsed_paths import build_parsed_artifact_paths, build_parsed_content_dir
from app.core.parser_routing import (
    DECISION_ROUTE,
    RouteType,
    ext_from_path,
    match_route_type,
    normalize_file_ext,
)
from app.core.vault_paths import VAULT_COPIED, build_vault_artifact_paths, build_vault_dir
from app.services.document_ir_schema import validate_document_ir
from app.models.file import KbFileContent, KbFileInstance
from app.models.vault import KbRawVaultObject

logger = logging.getLogger(__name__)

STATUS_CONTENT_REGISTERED = "CONTENT_REGISTERED"
STATUS_DISCOVERED = "DISCOVERED"

PARSE_MARKITDOWN_MAX_LIMIT = 100

IN_SCOPE_ROUTE_TYPES = frozenset(
    {
        RouteType.DOCX,
        RouteType.PPTX,
        RouteType.XLSX,
        RouteType.TEXT_OR_MARKDOWN,
    }
)

STATUS_SUCCESS = "SUCCESS"
STATUS_SKIPPED = "SKIPPED"
STATUS_FAILED = "FAILED"
STATUS_EMPTY = "EMPTY"

SKIP_REASON_LIMIT = "parse_limit_reached"
SKIP_REASON_IDEMPOTENT = "idempotent_success_manifest"


@dataclass
class ParseMarkitdownError:
    content_uid: str
    sha256: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "content_uid": self.content_uid,
            "sha256": self.sha256,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class ParseMarkitdownItem:
    content_uid: str
    sha256: str
    route_type: str
    decision: str
    status: str
    parsed_dir: str | None = None
    source_vault_path: str | None = None
    skip_reason: str | None = None
    dry_run_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_uid": self.content_uid,
            "sha256": self.sha256,
            "route_type": self.route_type,
            "decision": self.decision,
            "status": self.status,
            "parsed_dir": self.parsed_dir,
            "source_vault_path": self.source_vault_path,
            "skip_reason": self.skip_reason,
            "dry_run_action": self.dry_run_action,
        }


@dataclass
class ParseMarkitdownResult:
    total_candidates: int = 0
    in_scope_candidates: int = 0
    parsed_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    empty_count: int = 0
    dry_run: bool = False
    items: list[ParseMarkitdownItem] = field(default_factory=list)
    errors: list[ParseMarkitdownError] = field(default_factory=list)
    report_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_candidates": self.total_candidates,
            "in_scope_candidates": self.in_scope_candidates,
            "parsed_count": self.parsed_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "empty_count": self.empty_count,
            "dry_run": self.dry_run,
            "items": [item.to_dict() for item in self.items],
            "errors": [error.to_dict() for error in self.errors],
            "report_path": self.report_path.as_posix() if self.report_path else None,
        }


class MarkItDownParserService:
    def __init__(
        self,
        config: AppConfig,
        adapter: MarkItDownAdapter | None = None,
    ) -> None:
        ensure_readonly(config)
        self.config = config
        self.adapter = adapter or MarkItDownAdapter()
        engine = create_db_engine(config)
        self.session_factory = create_session_factory(engine)

    def parse_markitdown(
        self,
        *,
        sha256: str | None = None,
        content_uid: str | None = None,
        limit: int | None = None,
        dry_run: bool = False,
    ) -> ParseMarkitdownResult:
        result = ParseMarkitdownResult(dry_run=dry_run)
        contents = self._load_candidates(sha256=sha256, content_uid=content_uid)
        result.total_candidates = len(contents)

        in_scope_parse_actions = 0
        in_scope_values = {route_type.value for route_type in IN_SCOPE_ROUTE_TYPES}

        for content in contents:
            error: ParseMarkitdownError | None = None
            try:
                item, error, consumed_parse_slot = self._process_content(
                    content=content,
                    dry_run=dry_run,
                    limit=limit,
                    in_scope_parse_actions=in_scope_parse_actions,
                )
            except Exception as exc:
                sha = content.sha256 or ""
                logger.exception("Unexpected parse failure for sha256=%s", sha)
                item = ParseMarkitdownItem(
                    content_uid=content.content_uid,
                    sha256=sha,
                    route_type=RouteType.UNKNOWN.value,
                    decision="ERROR",
                    status=STATUS_FAILED,
                )
                error = ParseMarkitdownError(
                    content_uid=content.content_uid,
                    sha256=sha,
                    code=ERROR_PARSER_RUNTIME,
                    message=str(exc),
                )
                consumed_parse_slot = True

            if (
                item.decision == DECISION_ROUTE
                and item.route_type in in_scope_values
                and item.skip_reason != SKIP_REASON_LIMIT
            ):
                result.in_scope_candidates += 1

            if consumed_parse_slot:
                in_scope_parse_actions += 1

            result.items.append(item)
            if error is not None:
                result.errors.append(error)
            self._update_summary_counts(result, item)

        result.report_path = self._write_report(
            result=result,
            filters={
                "sha256": sha256,
                "content_uid": content_uid,
                "limit": limit,
            },
        )
        return result

    def _load_candidates(
        self,
        sha256: str | None,
        content_uid: str | None,
    ) -> list[KbFileContent]:
        target_sha = sha256 or content_uid
        with self.session_factory() as session:
            query = select(KbFileContent).where(
                KbFileContent.sha256.isnot(None),
                KbFileContent.status == STATUS_CONTENT_REGISTERED,
                KbFileContent.vault_status == VAULT_COPIED,
            )
            if target_sha:
                query = query.where(KbFileContent.sha256 == target_sha)
            query = query.order_by(KbFileContent.id.asc())
            return list(session.scalars(query).all())

    def _process_content(
        self,
        *,
        content: KbFileContent,
        dry_run: bool,
        limit: int | None,
        in_scope_parse_actions: int,
    ) -> tuple[ParseMarkitdownItem, ParseMarkitdownError | None, bool]:
        sha256 = content.sha256
        assert sha256 is not None

        with self.session_factory() as session:
            db_content = session.scalar(
                select(KbFileContent).where(KbFileContent.sha256 == sha256)
            )
            if db_content is None:
                raise RuntimeError(f"Content not found for sha256={sha256}")

            vault_object = session.scalar(
                select(KbRawVaultObject).where(KbRawVaultObject.sha256 == sha256)
            )
            fallback_ext = self._resolve_fallback_ext(session, db_content)
            route_type, decision, rule_name, _hint, reason = match_route_type(
                file_ext=normalize_file_ext(db_content.file_ext),
                mime_type=db_content.mime_type,
                fallback_ext=fallback_ext,
            )

            parsed_dir = build_parsed_content_dir(self.config.storage.parsed_root, sha256)
            parsed_artifacts = build_parsed_artifact_paths(parsed_dir)
            vault_dir = self._resolve_vault_dir(db_content, vault_object, sha256)
            original_bin = build_vault_artifact_paths(vault_dir)["original_bin"]
            source_vault_path = original_bin.as_posix()

            base_item = ParseMarkitdownItem(
                content_uid=db_content.content_uid,
                sha256=sha256,
                route_type=route_type.value,
                decision=decision,
                status=STATUS_SKIPPED,
                parsed_dir=parsed_dir.as_posix(),
                source_vault_path=source_vault_path,
            )

            if decision != DECISION_ROUTE or route_type not in IN_SCOPE_ROUTE_TYPES:
                base_item.skip_reason = f"route_type={route_type.value}; {reason}"
                return base_item, None, False

            if limit is not None and in_scope_parse_actions >= limit:
                base_item.skip_reason = SKIP_REASON_LIMIT
                return base_item, None, False

            if self._should_skip_idempotent(parsed_artifacts["parse_manifest"]):
                base_item.skip_reason = SKIP_REASON_IDEMPOTENT
                base_item.dry_run_action = "would_skip" if dry_run else None
                return base_item, None, False

            if not original_bin.is_file():
                error = ParseMarkitdownError(
                    content_uid=db_content.content_uid,
                    sha256=sha256,
                    code="MISSING_ORIGINAL_BIN",
                    message=f"original.bin not found at {source_vault_path}",
                )
                if not dry_run:
                    self._write_failed_manifest(
                        artifacts=parsed_artifacts,
                        content=db_content,
                        route_type=route_type,
                        rule_name=rule_name,
                        source_vault_path=source_vault_path,
                        original_bin=original_bin,
                        error=error,
                    )
                base_item.status = STATUS_FAILED
                return base_item, error, True

            if dry_run:
                base_item.status = STATUS_SKIPPED
                base_item.dry_run_action = "would_parse"
                base_item.skip_reason = None
                return base_item, None, True

            content_size = original_bin.stat().st_size
            try:
                adapter_result = self.adapter.convert(
                    input_path=original_bin,
                    route_type=route_type,
                )
            except MarkItDownAdapterError as exc:
                error = ParseMarkitdownError(
                    content_uid=db_content.content_uid,
                    sha256=sha256,
                    code=exc.code,
                    message=exc.message,
                )
                self._write_failed_manifest(
                    artifacts=parsed_artifacts,
                    content=db_content,
                    route_type=route_type,
                    rule_name=rule_name,
                    source_vault_path=source_vault_path,
                    original_bin=original_bin,
                    error=error,
                    content_size_bytes=content_size,
                )
                base_item.status = STATUS_FAILED
                return base_item, error, True

            status = STATUS_EMPTY if not adapter_result.text.strip() else STATUS_SUCCESS
            self._write_success_artifacts(
                artifacts=parsed_artifacts,
                content=db_content,
                route_type=route_type,
                rule_name=rule_name,
                source_vault_path=source_vault_path,
                original_bin=original_bin,
                adapter_result=adapter_result,
                status=status,
                content_size_bytes=content_size,
                vault_object=vault_object,
            )
            base_item.status = status
            return base_item, None, True

    def _resolve_vault_dir(
        self,
        content: KbFileContent,
        vault_object: KbRawVaultObject | None,
        sha256: str,
    ) -> Path:
        if content.vault_path:
            return Path(content.vault_path)
        if vault_object is not None and vault_object.vault_path:
            return Path(vault_object.vault_path)
        return build_vault_dir(self.config.storage.raw_vault_root, sha256)

    def _resolve_fallback_ext(self, session, content: KbFileContent) -> str | None:
        if normalize_file_ext(content.file_ext):
            return None

        instances_query = (
            select(KbFileInstance)
            .where(
                KbFileInstance.sha256 == content.sha256,
                KbFileInstance.status == STATUS_DISCOVERED,
            )
            .order_by(KbFileInstance.created_at.asc(), KbFileInstance.id.asc())
        )
        instances = list(session.scalars(instances_query).all())
        if not instances:
            return None

        ordered: list[KbFileInstance] = []
        if content.master_file_instance_uid:
            master = session.scalar(
                select(KbFileInstance).where(
                    KbFileInstance.file_instance_uid == content.master_file_instance_uid
                )
            )
            if master is not None:
                ordered.append(master)

        for instance in instances:
            if instance not in ordered:
                ordered.append(instance)

        for instance in ordered:
            ext = ext_from_path(instance.file_name)
            if ext:
                return ext
            ext = ext_from_path(instance.source_path)
            if ext:
                return ext
        return None

    def _should_skip_idempotent(self, manifest_path: Path) -> bool:
        if not manifest_path.is_file():
            return False
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return (
            payload.get("status") == STATUS_SUCCESS
            and payload.get("parser_adapter_version") == PARSER_ADAPTER_VERSION
        )

    def _build_document_ir(
        self,
        *,
        content: KbFileContent,
        text: str,
        source_vault_path: str,
        original_bin: Path,
        warnings: list[str],
    ) -> dict[str, Any]:
        """按格式选 IR 出口：原生 Office 结构优先，失败降级并留痕。"""
        from app.services.document_ir_builder import build_from_markitdown_text

        ext = (content.file_ext or "").lower()
        native = None
        try:
            if ext == ".docx":
                from app.services.native_office_ir import build_from_native_docx

                native = build_from_native_docx
            elif ext in {".xlsx", ".xlsm"}:
                from app.services.native_office_ir import build_from_native_xlsx

                native = build_from_native_xlsx
        except Exception as exc:  # noqa: BLE001 - 导入失败也走降级并留痕
            warnings.append(f"native_ir_import_failed: {type(exc).__name__}: {str(exc)[:160]}")

        if native is not None:
            try:
                kwargs = dict(
                    path=original_bin,
                    text=text,
                    sha256=content.sha256,
                    content_uid=content.content_uid,
                    source_vault_path=source_vault_path,
                    adapter_version=PARSER_ADAPTER_VERSION,
                )
                if ext in {".xlsx", ".xlsm"}:
                    kwargs["suffix"] = ext  # vault 里原件是 original.bin，需按真实扩展名读
                return native(**kwargs).to_dict()
            except Exception as exc:  # noqa: BLE001 - 原生结构失败不得静默
                warnings.append(f"native_ir_failed: {type(exc).__name__}: {str(exc)[:200]}")

        return build_from_markitdown_text(
            text=text,
            sha256=content.sha256,
            content_uid=content.content_uid,
            source_vault_path=source_vault_path,
            adapter_version=PARSER_ADAPTER_VERSION,
        ).to_dict()

    def _write_success_artifacts(
        self,
        *,
        artifacts: dict[str, Path],
        content: KbFileContent,
        route_type: RouteType,
        rule_name: str,
        source_vault_path: str,
        original_bin: Path,
        adapter_result: object,
        status: str,
        content_size_bytes: int,
        vault_object: KbRawVaultObject | None,
    ) -> None:
        from app.adapters.markitdown_adapter import AdapterResult

        assert isinstance(adapter_result, AdapterResult)
        generated_at = datetime.now(UTC).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
        converted_at = generated_at

        parsed_dir = artifacts["parsed_dir"]
        parsed_dir.mkdir(parents=True, exist_ok=True)

        text = adapter_result.text
        artifacts["parsed_text"].write_text(text, encoding="utf-8")
        output_size = artifacts["parsed_text"].stat().st_size
        output_hash = compute_sha256(artifacts["parsed_text"])

        library_version = adapter_result.metadata.get("library_version", "unknown")

        # 017 接线：产出 Document IR 第 4 件。DOCX/XLSX 优先用**原生结构**（OOXML 表格矩阵、
        # xlsx 合并区域），不是文本降级；原生读取失败才退回文本 IR，并把原因写进 warnings，
        # 绝不静默把"没有表格结构"当成"表格已解析"。
        ir_warnings = list(adapter_result.warnings)
        ir_payload = self._build_document_ir(
            content=content,
            text=text,
            source_vault_path=source_vault_path,
            original_bin=original_bin,
            warnings=ir_warnings,
        )
        validate_document_ir(ir_payload, parsed_text=text)

        metadata_payload = {
            "parser_name": PARSER_NAME,
            "parser_adapter_version": PARSER_ADAPTER_VERSION,
            "route_type": route_type.value,
            # 017 接线：下游合同要求 content_uid 与 manifest/IR 三方一致（此前只有 manifest 有）
            "content_uid": content.content_uid,
            "source_vault_path": source_vault_path,
            "converted_at": converted_at,
            "library_version": library_version,
            "warnings": ir_warnings,
            "ir_source": (ir_payload.get("provenance") or {}).get("ir_source"),
            "tables": len(ir_payload.get("tables") or []),
            "extra": {
                key: value
                for key, value in adapter_result.metadata.items()
                if key not in {"library_version", "route_type"}
            },
        }
        artifacts["parsed_metadata"].write_text(
            json.dumps(metadata_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        artifacts["document_ir"].write_text(
            json.dumps(ir_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        manifest_payload = {
            "content_uid": content.content_uid,
            "sha256": content.sha256,
            "route_type": route_type.value,
            "parser_name": PARSER_NAME,
            "parser_adapter_version": PARSER_ADAPTER_VERSION,
            "source_vault_path": source_vault_path,
            "parsed_text_path": artifacts["parsed_text"].as_posix(),
            "parsed_metadata_path": artifacts["parsed_metadata"].as_posix(),
            "document_ir_path": artifacts["document_ir"].as_posix(),
            "ir_schema_version": ir_payload["schema_version"],
            "generated_at": generated_at,
            "status": status,
            "content_size_bytes": content_size_bytes,
            "input_metadata": {
                "file_ext": content.file_ext,
                "mime_type": content.mime_type,
                "rule_name": rule_name,
                "vault_uid": vault_object.vault_uid if vault_object else None,
            },
            "output_size_bytes": output_size,
            "output_hash": output_hash,
        }
        artifacts["parse_manifest"].write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        logger.info(
            "Parse completed sha256=%s route_type=%s status=%s parsed_dir=%s",
            content.sha256,
            route_type.value,
            status,
            parsed_dir,
        )

    def _write_failed_manifest(
        self,
        *,
        artifacts: dict[str, Path],
        content: KbFileContent,
        route_type: RouteType,
        rule_name: str,
        source_vault_path: str,
        original_bin: Path,
        error: ParseMarkitdownError,
        content_size_bytes: int | None = None,
    ) -> None:
        generated_at = datetime.now(UTC).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
        if content_size_bytes is None and original_bin.is_file():
            content_size_bytes = original_bin.stat().st_size
        elif content_size_bytes is None:
            content_size_bytes = 0

        artifacts["parsed_dir"].mkdir(parents=True, exist_ok=True)
        manifest_payload = {
            "content_uid": content.content_uid,
            "sha256": content.sha256,
            "route_type": route_type.value,
            "parser_name": PARSER_NAME,
            "parser_adapter_version": PARSER_ADAPTER_VERSION,
            "source_vault_path": source_vault_path,
            "parsed_text_path": artifacts["parsed_text"].as_posix(),
            "parsed_metadata_path": artifacts["parsed_metadata"].as_posix(),
            "generated_at": generated_at,
            "status": STATUS_FAILED,
            "content_size_bytes": content_size_bytes,
            "input_metadata": {
                "file_ext": content.file_ext,
                "mime_type": content.mime_type,
                "rule_name": rule_name,
                "vault_uid": None,
            },
            "error": {
                "code": error.code,
                "message": error.message,
            },
        }
        artifacts["parse_manifest"].write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.error(
            "Parse failed sha256=%s code=%s message=%s",
            content.sha256,
            error.code,
            error.message,
        )

    def _update_summary_counts(
        self,
        result: ParseMarkitdownResult,
        item: ParseMarkitdownItem,
    ) -> None:
        if item.status == STATUS_SUCCESS:
            result.parsed_count += 1
        elif item.status == STATUS_EMPTY:
            result.empty_count += 1
        elif item.status == STATUS_FAILED:
            result.failed_count += 1
        elif item.status == STATUS_SKIPPED:
            result.skipped_count += 1

    def _write_report(
        self,
        *,
        result: ParseMarkitdownResult,
        filters: dict[str, Any],
    ) -> Path:
        reports_root = self.config.storage.reports_root
        reports_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        generated_at = datetime.now(UTC).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
        report_path = reports_root / f"parse_markitdown_report_{timestamp}.json"

        payload = {
            "report_type": "parse_markitdown_report",
            "parser_name": PARSER_NAME,
            "parser_adapter_version": PARSER_ADAPTER_VERSION,
            "pipeline_version": self.config.pipeline_version,
            "generated_at": generated_at,
            "dry_run": result.dry_run,
            "filters": filters,
            "summary": {
                "total_candidates": result.total_candidates,
                "in_scope_candidates": result.in_scope_candidates,
                "parsed_count": result.parsed_count,
                "skipped_count": result.skipped_count,
                "failed_count": result.failed_count,
                "empty_count": result.empty_count,
            },
            "items": [item.to_dict() for item in result.items],
            "errors": [error.to_dict() for error in result.errors],
        }

        try:
            report_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise RuntimeError(f"Report directory not writable: {exc}") from exc

        logger.info("Wrote parse markitdown report: %s", report_path)
        return report_path
