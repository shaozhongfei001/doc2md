from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select

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
    route_pdf_with_sniff,
)
from app.core.pdf_sniffing import REASON_SNIFF_SKIPPED, load_sniff_config, resolve_pdf_sniff
from app.core.vault_paths import VAULT_COPIED, build_vault_artifact_paths, build_vault_dir
from app.models.file import KbFileContent, KbFileInstance
from app.models.vault import KbRawVaultObject

logger = logging.getLogger(__name__)

PARSER_NAME = "mineru"
PARSER_PROFILE = "mineru_default_v1"

#: 017 additive：需要从 staging 保留到 ``parsed_dir/ir_source/`` 的结构化中间件
IR_SOURCE_FILENAMES: tuple[str, ...] = ("content_list.json", "middle.json")
PARSER_ADAPTER_VERSION = "007_mvp_v1"
MAGIC_PDF_CMD = "magic-pdf"

PARSE_MINERU_PDF_MAX_LIMIT = 100
DEFAULT_TIMEOUT_SECONDS = 600

STATUS_SUCCESS = "SUCCESS"
STATUS_SKIPPED = "SKIPPED"
STATUS_FAILED = "FAILED"
STATUS_EMPTY = "EMPTY"

DECISION_PARSED = "PARSED"
DECISION_ALREADY_SUCCESS = "ALREADY_SUCCESS"
DECISION_ROUTE_MISMATCH = "ROUTE_MISMATCH"
DECISION_TIMEOUT = "TIMEOUT"
DECISION_SUBPROCESS_ERROR = "SUBPROCESS_ERROR"
DECISION_OUTPUT_CONTRACT_VIOLATION = "OUTPUT_CONTRACT_VIOLATION"
DECISION_ASSET_INCOMPLETE = "ASSET_INCOMPLETE"

SKIP_REASON_LIMIT = "parse_limit_reached"
SKIP_REASON_IDEMPOTENT = "idempotent_success_manifest"

IN_SCOPE_ROUTE_TYPES = frozenset(
    {
        RouteType.PDF_DIGITAL,
        RouteType.PDF_SCANNED_OR_IMAGE,
    }
)

STATUS_CONTENT_REGISTERED = "CONTENT_REGISTERED"
STATUS_DISCOVERED = "DISCOVERED"


class SubprocessRunner(Protocol):
    def __call__(
        self,
        cmd: list[str],
        *,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass
class MagicPdfRunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass
class MineruParseError:
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
class MineruParsePlan:
    content_uid: str
    sha256: str
    route_type: str
    decision: str
    status: str
    parsed_dir: str
    original_bin: str
    parsed_text_path: str
    parsed_metadata_path: str
    parse_manifest_path: str
    assets_dir: str | None = None
    skip_reason: str | None = None
    dry_run_action: str | None = None
    rule_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_uid": self.content_uid,
            "sha256": self.sha256,
            "route_type": self.route_type,
            "decision": self.decision,
            "status": self.status,
            "parsed_dir": self.parsed_dir,
            "original_bin": self.original_bin,
            "parsed_text_path": self.parsed_text_path,
            "parsed_metadata_path": self.parsed_metadata_path,
            "parse_manifest_path": self.parse_manifest_path,
            "assets_dir": self.assets_dir,
            "skip_reason": self.skip_reason,
            "dry_run_action": self.dry_run_action,
            "rule_name": self.rule_name,
        }


@dataclass
class MineruParseItem:
    content_uid: str
    sha256: str
    route_type: str
    decision: str
    status: str
    parsed_dir: str | None = None
    source_vault_path: str | None = None
    skip_reason: str | None = None
    dry_run_action: str | None = None
    assets_dir: str | None = None

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
            "assets_dir": self.assets_dir,
        }


@dataclass
class MineruParseManyResult:
    total_candidates: int = 0
    in_scope_candidates: int = 0
    parsed_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    empty_count: int = 0
    timeout_count: int = 0
    partial_count: int = 0
    dry_run: bool = False
    items: list[MineruParseItem] = field(default_factory=list)
    errors: list[MineruParseError] = field(default_factory=list)
    report_path: Path | None = None
    plans: list[MineruParsePlan] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_candidates": self.total_candidates,
            "in_scope_candidates": self.in_scope_candidates,
            "parsed_count": self.parsed_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "empty_count": self.empty_count,
            "timeout_count": self.timeout_count,
            "partial_count": self.partial_count,
            "dry_run": self.dry_run,
            "items": [item.to_dict() for item in self.items],
            "errors": [error.to_dict() for error in self.errors],
            "report_path": self.report_path.as_posix() if self.report_path else None,
            "plans": [plan.to_dict() for plan in self.plans],
        }


def check_magic_pdf_available() -> None:
    if shutil.which(MAGIC_PDF_CMD) is None:
        raise RuntimeError(
            f"DEPENDENCY_MISSING: {MAGIC_PDF_CMD} not found on PATH; install MinerU / magic-pdf"
        )


def _utc_iso() -> str:
    return datetime.now(UTC).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _process_output_tail(run_result: MagicPdfRunResult, *, limit: int = 800) -> str:
    """返回子进程输出的尾部（单行化），用于把真实失败原因带进错误信息。

    magic-pdf 在解析器内部抛异常时**仍以 exit 0 退出**，只把 traceback 打到
    stdout；若仅报 ``output missing markdown content``，真实原因会被完全掩盖，
    排查只能靠复现。这里把 stdout/stderr 尾部附在消息里。
    """
    combined = "\n".join(
        part.strip() for part in (run_result.stdout, run_result.stderr) if part and part.strip()
    )
    if not combined:
        return f" | returncode={run_result.returncode} | output_tail=<empty>"
    tail = combined[-limit:].replace("\r", "")
    return f" | returncode={run_result.returncode} | output_tail={tail!r}"


def _assets_dir_for(parsed_dir: Path) -> Path:
    return parsed_dir / "assets"


class MineruPdfParserService:
    def __init__(
        self,
        config: AppConfig,
        *,
        magic_pdf_cmd: str = MAGIC_PDF_CMD,
        subprocess_runner: SubprocessRunner | None = None,
    ) -> None:
        ensure_readonly(config)
        self.config = config
        self.magic_pdf_cmd = magic_pdf_cmd
        self._subprocess_runner = subprocess_runner
        engine = create_db_engine(config)
        self.session_factory = create_session_factory(engine)

    def plan_one(
        self,
        *,
        sha256: str,
        force: bool = False,
    ) -> MineruParsePlan:
        content = self._load_single_content(sha256=sha256)
        return self._build_plan(content=content, force=force)

    def parse_one(
        self,
        *,
        sha256: str | None = None,
        content_uid: str | None = None,
        dry_run: bool = False,
        force: bool = False,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[MineruParseItem, MineruParseError | None]:
        target_sha = sha256 or content_uid
        if not target_sha:
            raise ValueError("Must provide sha256 or content_uid")

        content = self._load_single_content(sha256=target_sha)
        plan = self._build_plan(content=content, force=force, dry_run=dry_run)
        item = self._plan_to_item(plan)

        if dry_run:
            return item, None

        if plan.decision in (DECISION_ROUTE_MISMATCH, DECISION_ALREADY_SUCCESS):
            return item, None

        if plan.decision != DECISION_PARSED:
            return item, None

        return self._execute_parse(
            content=content,
            plan=plan,
            force=force,
            timeout_seconds=timeout_seconds,
        )

    def parse_many(
        self,
        *,
        sha256: str | None = None,
        content_uid: str | None = None,
        limit: int | None = None,
        dry_run: bool = False,
        force: bool = False,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        register: bool = False,
        registry_service: Any | None = None,
    ) -> MineruParseManyResult:
        result = MineruParseManyResult(dry_run=dry_run)
        contents = self._load_candidates(sha256=sha256, content_uid=content_uid)
        result.total_candidates = len(contents)

        in_scope_parse_actions = 0
        in_scope_values = {route_type.value for route_type in IN_SCOPE_ROUTE_TYPES}

        for content in contents:
            assert content.sha256 is not None
            plan = self._build_plan(content=content, force=force, dry_run=dry_run)
            result.plans.append(plan)

            if dry_run:
                item = self._plan_to_item(plan)
                if (
                    plan.route_type in in_scope_values
                    and plan.decision == DECISION_PARSED
                    and plan.skip_reason != SKIP_REASON_LIMIT
                ):
                    result.in_scope_candidates += 1
                    if limit is not None and in_scope_parse_actions >= limit:
                        item.skip_reason = SKIP_REASON_LIMIT
                        item.dry_run_action = "would_skip"
                    else:
                        item.dry_run_action = "would_parse"
                        in_scope_parse_actions += 1
                elif plan.decision == DECISION_ALREADY_SUCCESS:
                    item.dry_run_action = "would_skip"
                elif plan.decision == DECISION_ROUTE_MISMATCH:
                    item.dry_run_action = "would_skip"
                result.items.append(item)
                self._update_summary_counts(result, item)
                continue

            if (
                plan.route_type in in_scope_values
                and plan.decision == DECISION_PARSED
                and plan.skip_reason != SKIP_REASON_LIMIT
            ):
                result.in_scope_candidates += 1

            if plan.decision == DECISION_PARSED and limit is not None:
                if in_scope_parse_actions >= limit:
                    skip_item = self._plan_to_item(plan)
                    skip_item.skip_reason = SKIP_REASON_LIMIT
                    skip_item.status = STATUS_SKIPPED
                    result.items.append(skip_item)
                    self._update_summary_counts(result, skip_item)
                    continue

            if plan.decision in (DECISION_ROUTE_MISMATCH, DECISION_ALREADY_SUCCESS):
                item = self._plan_to_item(plan)
                result.items.append(item)
                self._update_summary_counts(result, item)
                continue

            if plan.decision != DECISION_PARSED:
                item = self._plan_to_item(plan)
                if plan.skip_reason == "missing_original_bin":
                    result.errors.append(
                        MineruParseError(
                            content_uid=plan.content_uid,
                            sha256=plan.sha256,
                            code="MISSING_ORIGINAL_BIN",
                            message=f"original.bin not found at {plan.original_bin}",
                        )
                    )
                result.items.append(item)
                self._update_summary_counts(result, item)
                continue

            item, error = self._execute_parse(
                content=content,
                plan=plan,
                force=force,
                timeout_seconds=timeout_seconds,
            )
            in_scope_parse_actions += 1
            result.items.append(item)
            if error is not None:
                result.errors.append(error)
            self._update_summary_counts(result, item)

        if not dry_run:
            result.report_path = self._write_batch_report(
                result=result,
                filters={
                    "sha256": sha256,
                    "content_uid": content_uid,
                    "limit": limit,
                    "force": force,
                    "timeout": timeout_seconds,
                    "register": register,
                },
            )

            if register and result.report_path is not None:
                from app.services.parse_registry import ParseRegistryService

                registry = registry_service or ParseRegistryService(self.config)
                registry.register_parse_report(report_path=result.report_path)

        return result

    def _plan_to_item(self, plan: MineruParsePlan) -> MineruParseItem:
        return MineruParseItem(
            content_uid=plan.content_uid,
            sha256=plan.sha256,
            route_type=plan.route_type,
            decision=plan.decision,
            status=plan.status,
            parsed_dir=plan.parsed_dir,
            source_vault_path=plan.original_bin,
            skip_reason=plan.skip_reason,
            dry_run_action=plan.dry_run_action,
            assets_dir=plan.assets_dir,
        )

    def _build_plan(
        self,
        *,
        content: KbFileContent,
        force: bool,
        dry_run: bool = False,
    ) -> MineruParsePlan:
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
        artifacts = build_parsed_artifact_paths(parsed_dir)
        assets_dir = _assets_dir_for(parsed_dir)
        vault_dir = self._resolve_vault_dir(db_content, vault_object, sha256)
        original_bin = build_vault_artifact_paths(vault_dir)["original_bin"]

        # 014 §7.4：与 router 复用同一嗅探管线，保证「router 判 scanned 的内容，
        # 007 不会按 digital 处理」。仅 PDF 分支生效，其余路径逐字节不变。
        if route_type == RouteType.PDF_DIGITAL:
            if original_bin.is_file():
                sniff = resolve_pdf_sniff(
                    original_bin,
                    sha256,
                    config=load_sniff_config(),
                    cache_root=self.config.storage.sniff_cache_root,
                    use_cache=True,
                    write_cache=not dry_run,
                )
                route_type, decision, rule_name, _hint, reason = route_pdf_with_sniff(sniff)
            else:
                # D-3：original.bin 不可达 → 不嗅探，沿用 004 判定并标注原因。
                reason = f"{reason}; {REASON_SNIFF_SKIPPED}"
                logger.warning(
                    "PDF sniff skipped for sha256=%s: original.bin not accessible", sha256
                )

        base = MineruParsePlan(
            content_uid=db_content.content_uid,
            sha256=sha256,
            route_type=route_type.value,
            decision=DECISION_ROUTE_MISMATCH,
            status=STATUS_SKIPPED,
            parsed_dir=parsed_dir.as_posix(),
            original_bin=original_bin.as_posix(),
            parsed_text_path=artifacts["parsed_text"].as_posix(),
            parsed_metadata_path=artifacts["parsed_metadata"].as_posix(),
            parse_manifest_path=artifacts["parse_manifest"].as_posix(),
            assets_dir=assets_dir.as_posix(),
            rule_name=rule_name,
        )

        if decision != DECISION_ROUTE or route_type not in IN_SCOPE_ROUTE_TYPES:
            base.skip_reason = f"route_type={route_type.value}; {reason}"
            base.decision = DECISION_ROUTE_MISMATCH
            return base

        if not force and self._should_skip_idempotent(artifacts["parse_manifest"]):
            base.decision = DECISION_ALREADY_SUCCESS
            base.skip_reason = SKIP_REASON_IDEMPOTENT
            base.dry_run_action = "would_skip"
            return base

        if not original_bin.is_file():
            base.decision = DECISION_OUTPUT_CONTRACT_VIOLATION
            base.status = STATUS_FAILED
            base.skip_reason = "missing_original_bin"
            return base

        base.decision = DECISION_PARSED
        base.status = STATUS_SUCCESS
        base.dry_run_action = "would_parse"
        return base

    def _execute_parse(
        self,
        *,
        content: KbFileContent,
        plan: MineruParsePlan,
        force: bool,
        timeout_seconds: int,
    ) -> tuple[MineruParseItem, MineruParseError | None]:
        sha256 = plan.sha256
        parsed_dir = Path(plan.parsed_dir)
        artifacts = build_parsed_artifact_paths(parsed_dir)
        assets_dir = _assets_dir_for(parsed_dir)
        original_bin = Path(plan.original_bin)
        route_type = RouteType(plan.route_type)

        with self.session_factory() as session:
            vault_object = session.scalar(
                select(KbRawVaultObject).where(KbRawVaultObject.sha256 == sha256)
            )

        if force:
            self._clear_parsed_outputs(artifacts, assets_dir)

        staging_root = self.config.storage.parsed_root / ".staging"
        run_id = uuid.uuid4().hex[:12]
        staging_dir = staging_root / f"mineru_{run_id}"
        staging_dir.mkdir(parents=True, exist_ok=True)

        try:
            run_result = self._invoke_magic_pdf(
                input_path=original_bin,
                output_dir=staging_dir,
                timeout_seconds=timeout_seconds,
            )

            item = self._plan_to_item(plan)

            if run_result.timed_out:
                error = MineruParseError(
                    content_uid=plan.content_uid,
                    sha256=sha256,
                    code=DECISION_TIMEOUT,
                    message=f"magic-pdf timed out after {timeout_seconds}s",
                )
                item.status = STATUS_FAILED
                item.decision = DECISION_TIMEOUT
                return item, error

            if run_result.returncode != 0:
                error = MineruParseError(
                    content_uid=plan.content_uid,
                    sha256=sha256,
                    code=DECISION_SUBPROCESS_ERROR,
                    message=(
                        f"magic-pdf exited with code {run_result.returncode}"
                        f"{_process_output_tail(run_result)}"
                    ),
                )
                item.status = STATUS_FAILED
                item.decision = DECISION_SUBPROCESS_ERROR
                return item, error

            return self._finalize_success_from_staging(
                content=content,
                plan=plan,
                artifacts=artifacts,
                assets_dir=assets_dir,
                route_type=route_type,
                source_vault_path=plan.original_bin,
                original_bin=original_bin,
                run_result=run_result,
                staging_dir=staging_dir,
                vault_object=vault_object,
            )
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)

    def _finalize_success_from_staging(
        self,
        *,
        content: KbFileContent,
        plan: MineruParsePlan,
        artifacts: dict[str, Path],
        assets_dir: Path,
        route_type: RouteType,
        source_vault_path: str,
        original_bin: Path,
        run_result: MagicPdfRunResult,
        staging_dir: Path,
        vault_object: KbRawVaultObject | None,
    ) -> tuple[MineruParseItem, MineruParseError | None]:
        markdown_path = self._find_primary_markdown(staging_dir)
        if markdown_path is None or not markdown_path.is_file():
            error = MineruParseError(
                content_uid=plan.content_uid,
                sha256=plan.sha256,
                code=DECISION_OUTPUT_CONTRACT_VIOLATION,
                message=(
                    "magic-pdf output missing markdown content"
                    f"{_process_output_tail(run_result)}"
                ),
            )
            item = self._plan_to_item(plan)
            item.status = STATUS_FAILED
            item.decision = DECISION_OUTPUT_CONTRACT_VIOLATION
            return item, error

        text = markdown_path.read_text(encoding="utf-8")
        asset_entries, asset_warnings = self._collect_assets(staging_dir, assets_dir)
        # 017 additive：保留 MinerU 结构化中间件（content_list / middle）供 Document IR 构建。
        # 旧 v1 产物无上述文件 → 返回空列表，manifest 增记空数组，既有行为零变化。
        ir_source_files = self._preserve_ir_source(staging_dir, artifacts["ir_source"])

        generated_at = _utc_iso()
        content_size_bytes = original_bin.stat().st_size if original_bin.is_file() else 0

        parsed_dir = artifacts["parsed_dir"]
        parsed_dir.mkdir(parents=True, exist_ok=True)
        artifacts["parsed_text"].write_text(text, encoding="utf-8")
        output_size = artifacts["parsed_text"].stat().st_size
        output_hash = compute_sha256(artifacts["parsed_text"])

        metadata_payload = {
            "parser_name": PARSER_NAME,
            "parser_profile": PARSER_PROFILE,
            "parser_adapter_version": PARSER_ADAPTER_VERSION,
            "route_type": route_type.value,
            "content_uid": content.content_uid,
            "sha256": plan.sha256,
            "source_vault_path": source_vault_path,
            "generated_at": generated_at,
            "library_version": "magic-pdf",
        }
        artifacts["parsed_metadata"].write_text(
            json.dumps(metadata_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        manifest_status = STATUS_EMPTY if not text.strip() else STATUS_SUCCESS
        item_decision = DECISION_PARSED
        item_status = manifest_status

        if asset_warnings:
            manifest_status = STATUS_FAILED
            item_status = STATUS_FAILED
            item_decision = DECISION_ASSET_INCOMPLETE

        manifest_payload: dict[str, Any] = {
            "content_uid": content.content_uid,
            "sha256": plan.sha256,
            "route_type": route_type.value,
            "parser_name": PARSER_NAME,
            "parser_profile": PARSER_PROFILE,
            "parser_adapter_version": PARSER_ADAPTER_VERSION,
            "source_vault_path": source_vault_path,
            "parsed_text_path": artifacts["parsed_text"].as_posix(),
            "parsed_metadata_path": artifacts["parsed_metadata"].as_posix(),
            "generated_at": generated_at,
            "status": manifest_status,
            "content_size_bytes": content_size_bytes,
            "input_metadata": {
                "file_ext": content.file_ext,
                "mime_type": content.mime_type,
                "rule_name": plan.rule_name,
                "vault_uid": vault_object.vault_uid if vault_object else None,
            },
            "output_size_bytes": output_size,
            "output_hash": output_hash,
        }
        if asset_entries or assets_dir.is_dir():
            manifest_payload["assets_dir"] = assets_dir.as_posix()
            manifest_payload["asset_files"] = asset_entries
        # 017 additive 字段：仅新增键，既有键与其语义逐字不变（A017-08/A017-09）
        manifest_payload["ir_source_files"] = ir_source_files
        if asset_warnings:
            manifest_payload["warnings"] = asset_warnings
            manifest_payload["error"] = {
                "code": DECISION_ASSET_INCOMPLETE,
                "message": "; ".join(asset_warnings),
            }

        artifacts["parse_manifest"].write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        item = self._plan_to_item(plan)
        item.status = item_status
        item.decision = item_decision
        item.assets_dir = assets_dir.as_posix() if assets_dir.is_dir() else None

        error: MineruParseError | None = None
        if item_decision == DECISION_ASSET_INCOMPLETE:
            error = MineruParseError(
                content_uid=plan.content_uid,
                sha256=plan.sha256,
                code=DECISION_ASSET_INCOMPLETE,
                message="; ".join(asset_warnings),
            )

        logger.info(
            "MinerU parse completed sha256=%s route_type=%s status=%s decision=%s",
            plan.sha256,
            route_type.value,
            item_status,
            item_decision,
        )
        return item, error

    def _stage_mineru_input(self, input_path: Path, output_dir: Path) -> Path:
        """为 MinerU 准备带 ``.pdf`` 后缀的输入副本。

        MinerU 按文件后缀分发解析器（``read_fn``），无扩展名会直接抛
        ``Unknown file suffix``；而 vault 原件统一为 ``original.bin``。
        副本落在 staging 目录内，由 ``_execute_parse`` 的 finally 一并清理。
        """
        staged = output_dir / f"{input_path.stem or 'document'}.pdf"
        if staged.exists():
            return staged
        try:
            os.link(input_path, staged)
        except OSError:
            shutil.copy2(input_path, staged)
        return staged

    def _invoke_magic_pdf(
        self,
        *,
        input_path: Path,
        output_dir: Path,
        timeout_seconds: int,
    ) -> MagicPdfRunResult:
        cmd = [
            self.magic_pdf_cmd,
            "-p",
            str(self._stage_mineru_input(input_path, output_dir)),
            "-o",
            str(output_dir),
            "-m",
            "auto",
        ]
        runner = self._subprocess_runner or self._default_subprocess_runner
        try:
            completed = runner(cmd, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return MagicPdfRunResult(
                returncode=-1,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
            )

        return MagicPdfRunResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            timed_out=False,
        )

    @staticmethod
    def _default_subprocess_runner(
        cmd: list[str],
        *,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )

    def _find_primary_markdown(self, root: Path) -> Path | None:
        if not root.exists():
            return None
        md_files = sorted(
            root.rglob("*.md"),
            key=lambda p: p.stat().st_size if p.is_file() else 0,
            reverse=True,
        )
        for path in md_files:
            if path.is_file():
                return path
        return None

    def _preserve_ir_source(
        self,
        staging_dir: Path,
        ir_source_dir: Path,
    ) -> list[dict[str, str]]:
        """把 staging 中的 MinerU 结构化中间件拷入 ``parsed_dir/ir_source/``（017 additive）。

        - ``content_list.json`` 必需、``middle.json`` 可选；两者都缺 → 返回空列表
          （旧 ``mineru_default_v1`` 产物即此情形，行为与 017 之前完全一致）。
        - 只搬运不解析：JSON 非法的判定属 017 服务层的 fail-closed 职责。
        """

        entries: list[dict[str, str]] = []
        for name in IR_SOURCE_FILENAMES:
            # MinerU 产物名带 PDF 前缀（如 ``rich_demo2_content_list.json``）→ 按后缀匹配，
            # 落盘时统一归一为 ``content_list.json`` / ``middle.json``（017 契约文件名）。
            candidates = sorted(staging_dir.rglob(f"*{name}"))
            if not candidates:
                continue
            ir_source_dir.mkdir(parents=True, exist_ok=True)
            target = ir_source_dir / name
            shutil.copy2(candidates[0], target)
            entries.append(
                {
                    "name": name,
                    "relative_path": f"{ir_source_dir.name}/{name}",
                    "sha256": compute_sha256(target),
                }
            )
        return entries

    def _collect_assets(
        self,
        staging_dir: Path,
        assets_dir: Path,
    ) -> tuple[list[dict[str, str]], list[str]]:
        if not staging_dir.exists():
            return [], []

        image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
        entries: list[dict[str, str]] = []
        warnings: list[str] = []

        images_dir = assets_dir / "images"
        for source in sorted(staging_dir.rglob("*")):
            if not source.is_file():
                continue
            if source.suffix.lower() not in image_exts:
                continue
            images_dir.mkdir(parents=True, exist_ok=True)
            target = images_dir / source.name
            try:
                shutil.copy2(source, target)
                entries.append(
                    {
                        "relative_path": target.relative_to(assets_dir).as_posix(),
                        "source_name": source.name,
                        "sha256": compute_sha256(target),
                    }
                )
            except OSError as exc:
                warnings.append(f"failed to copy asset {source.name}: {exc}")

        return entries, warnings

    def _should_skip_idempotent(self, manifest_path: Path) -> bool:
        if not manifest_path.is_file():
            return False
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return (
            payload.get("status") == STATUS_SUCCESS
            and payload.get("parser_name") == PARSER_NAME
            and payload.get("parser_adapter_version") == PARSER_ADAPTER_VERSION
        )

    def _clear_parsed_outputs(
        self,
        artifacts: dict[str, Path],
        assets_dir: Path,
    ) -> None:
        for key in ("parsed_text", "parsed_metadata", "parse_manifest"):
            path = artifacts[key]
            if path.is_file():
                path.unlink()
        if assets_dir.is_dir():
            shutil.rmtree(assets_dir, ignore_errors=True)

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

    def _load_single_content(self, *, sha256: str) -> KbFileContent:
        with self.session_factory() as session:
            content = session.scalar(select(KbFileContent).where(KbFileContent.sha256 == sha256))
            if content is None:
                raise RuntimeError(f"Content not found for sha256={sha256}")
            return content

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

    def _update_summary_counts(self, result: MineruParseManyResult, item: MineruParseItem) -> None:
        if item.status == STATUS_SUCCESS and item.decision == DECISION_PARSED:
            result.parsed_count += 1
        elif item.status == STATUS_EMPTY:
            result.empty_count += 1
        elif item.status == STATUS_FAILED:
            result.failed_count += 1
            if item.decision == DECISION_TIMEOUT:
                result.timeout_count += 1
            elif item.decision == DECISION_ASSET_INCOMPLETE:
                result.partial_count += 1
        elif item.status == STATUS_SKIPPED:
            result.skipped_count += 1

    def _write_batch_report(
        self,
        *,
        result: MineruParseManyResult,
        filters: dict[str, Any],
    ) -> Path:
        reports_root = self.config.storage.reports_root
        reports_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        report_path = reports_root / f"parse_mineru_pdf_report_{timestamp}.json"

        payload = {
            "report_type": "parse_mineru_pdf_report",
            "parser_name": PARSER_NAME,
            "parser_adapter_version": PARSER_ADAPTER_VERSION,
            "pipeline_version": self.config.pipeline_version,
            "generated_at": _utc_iso(),
            "dry_run": result.dry_run,
            "filters": filters,
            "summary": {
                "total_candidates": result.total_candidates,
                "in_scope_candidates": result.in_scope_candidates,
                "parsed_count": result.parsed_count,
                "skipped_count": result.skipped_count,
                "failed_count": result.failed_count,
                "empty_count": result.empty_count,
                "timeout_count": result.timeout_count,
                "partial_count": result.partial_count,
            },
            "items": [item.to_dict() for item in result.items],
            "errors": [error.to_dict() for error in result.errors],
        }

        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("Wrote parse mineru pdf report: %s", report_path)
        return report_path
