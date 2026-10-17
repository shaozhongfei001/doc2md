from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.core.config import AppConfig, ensure_readonly
from app.core.database import create_db_engine, create_session_factory
from app.core.parser_routing import (
    DECISION_ROUTE,
    DECISION_UNKNOWN,
    DECISION_UNSUPPORTED,
    ROUTING_RULES_VERSION,
    ROUTING_RULES_VERSIONS,
    RouteType,
    ext_from_path,
    match_route_type,
    normalize_file_ext,
    route_pdf_with_sniff,
)
from app.core.pdf_sniffing import (
    REASON_SNIFF_SKIPPED,
    PdfSniffConfig,
    PdfSniffResult,
    load_sniff_config,
    resolve_pdf_sniff,
)
from app.core.vault_paths import (
    VAULT_COPIED,
    build_vault_artifact_paths,
    build_vault_dir,
)
from app.models.file import KbFileContent, KbFileInstance
from app.models.vault import KbRawVaultObject

logger = logging.getLogger(__name__)

STATUS_CONTENT_REGISTERED = "CONTENT_REGISTERED"
STATUS_DISCOVERED = "DISCOVERED"


@dataclass
class RouteError:
    sha256: str
    message: str


@dataclass
class ParserRouteDecision:
    content_uid: str
    sha256: str
    file_ext: str | None
    mime_type: str | None
    vault_path: str | None
    route_type: RouteType
    decision: str
    rule_name: str | None
    reason: str
    future_parser_hint: str
    parse_status: str | None
    vault_uid: str | None = None
    # 014：仅当实际执行了内容嗅探时存在；非 PDF / v1 路径为 None（报告中不出现该键）。
    pdf_sniff: PdfSniffResult | None = None

    def to_dict(self) -> dict:
        payload = {
            "content_uid": self.content_uid,
            "sha256": self.sha256,
            "vault_path": self.vault_path,
            "file_ext": self.file_ext,
            "mime_type": self.mime_type,
            "route_type": self.route_type.value,
            "decision": self.decision,
            "rule_name": self.rule_name,
            "reason": self.reason,
            "future_parser_hint": self.future_parser_hint,
            "parse_status": self.parse_status,
            "vault_uid": self.vault_uid,
        }
        if self.pdf_sniff is not None:
            payload["pdf_sniff"] = self.pdf_sniff.evidence()
        return payload


@dataclass
class ParserRouteResult:
    candidates: int = 0
    routed: int = 0
    skipped: int = 0
    unknown: int = 0
    unsupported: int = 0
    errors: list[RouteError] = field(default_factory=list)
    decisions: list[ParserRouteDecision] = field(default_factory=list)
    report_path: Path | None = None
    routing_rules_version: str = ROUTING_RULES_VERSION
    pdf_sniffed: int = 0
    pdf_digital: int = 0
    pdf_scanned: int = 0
    pdf_cache_hits: int = 0
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {
            "candidates": self.candidates,
            "routed": self.routed,
            "skipped": self.skipped,
            "unknown": self.unknown,
            "unsupported": self.unsupported,
            "errors": [
                {"sha256": item.sha256, "message": item.message} for item in self.errors
            ],
            "report_path": self.report_path.as_posix() if self.report_path else None,
            "routing_rules_version": self.routing_rules_version,
            "pdf_sniffed": self.pdf_sniffed,
            "pdf_digital": self.pdf_digital,
            "pdf_scanned": self.pdf_scanned,
            "pdf_cache_hits": self.pdf_cache_hits,
            "dry_run": self.dry_run,
        }


class ParserRouterService:
    def __init__(self, config: AppConfig) -> None:
        ensure_readonly(config)
        self.config = config
        engine = create_db_engine(config)
        self.session_factory = create_session_factory(engine)

    def route_parsers(
        self,
        limit: int | None = None,
        sha256: str | None = None,
        content_uid: str | None = None,
        *,
        rules_version: str = ROUTING_RULES_VERSION,
        use_sniff_cache: bool = True,
        dry_run: bool = False,
    ) -> ParserRouteResult:
        if rules_version not in ROUTING_RULES_VERSIONS:
            raise ValueError(f"Unsupported routing rules version: {rules_version}")

        result = ParserRouteResult(routing_rules_version=rules_version, dry_run=dry_run)
        # v2 才装载嗅探配置；v1 冻结路径不做任何文件读取。
        sniff_config = (
            load_sniff_config() if rules_version == ROUTING_RULES_VERSION else None
        )
        contents = self._load_candidates(
            sha256=sha256,
            content_uid=content_uid,
            limit=limit,
        )
        result.candidates = len(contents)

        for content in contents:
            try:
                decision = self._route_content(
                    content,
                    rules_version=rules_version,
                    use_sniff_cache=use_sniff_cache,
                    dry_run=dry_run,
                    sniff_config=sniff_config,
                )
                result.decisions.append(decision)
                if decision.decision == DECISION_ROUTE:
                    result.routed += 1
                elif decision.decision == DECISION_UNKNOWN:
                    result.unknown += 1
                elif decision.decision == DECISION_UNSUPPORTED:
                    result.unsupported += 1
                self._accumulate_pdf_counters(result, decision)
            except Exception as exc:
                logger.exception("Parser routing failed for sha256=%s", content.sha256)
                result.errors.append(
                    RouteError(sha256=content.sha256 or "", message=str(exc))
                )

        if dry_run:
            logger.info("Dry run: parser route report not written")
            result.report_path = None
        else:
            result.report_path = self._write_report(result)
        return result

    @staticmethod
    def _accumulate_pdf_counters(
        result: ParserRouteResult,
        decision: ParserRouteDecision,
    ) -> None:
        if decision.pdf_sniff is None:
            return
        result.pdf_sniffed += 1
        if decision.pdf_sniff.from_cache:
            result.pdf_cache_hits += 1
        if decision.route_type == RouteType.PDF_DIGITAL:
            result.pdf_digital += 1
        elif decision.route_type == RouteType.PDF_SCANNED_OR_IMAGE:
            result.pdf_scanned += 1

    def _load_candidates(
        self,
        sha256: str | None,
        content_uid: str | None,
        limit: int | None,
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
            if limit is not None:
                query = query.limit(limit)
            return list(session.scalars(query).all())

    def _route_content(
        self,
        content: KbFileContent,
        *,
        rules_version: str = ROUTING_RULES_VERSION,
        use_sniff_cache: bool = True,
        dry_run: bool = False,
        sniff_config: PdfSniffConfig | None = None,
    ) -> ParserRouteDecision:
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
            vault_path = db_content.vault_path
            vault_uid: str | None = None
            if vault_object is not None:
                if not vault_path:
                    vault_path = vault_object.vault_path
                vault_uid = vault_object.vault_uid
            elif not vault_path:
                logger.warning("vault_path missing for sha256=%s", sha256)

            fallback_ext = self._resolve_fallback_ext(session, db_content)
            normalized_ext = normalize_file_ext(db_content.file_ext)

            route_type, decision, rule_name, future_parser_hint, reason = match_route_type(
                file_ext=normalized_ext,
                mime_type=db_content.mime_type,
                fallback_ext=fallback_ext,
            )

            if vault_path is None:
                reason = f"{reason}; vault_path is null in database metadata"

            # 014 §7.3：v2 仅对 PDF 分支叠加内容嗅探；v1 冻结路径不读任何文件。
            pdf_sniff: PdfSniffResult | None = None
            if (
                rules_version == ROUTING_RULES_VERSION
                and route_type == RouteType.PDF_DIGITAL
            ):
                vault_dir = (
                    Path(vault_path)
                    if vault_path
                    else build_vault_dir(self.config.storage.raw_vault_root, sha256)
                )
                original_bin = build_vault_artifact_paths(vault_dir)["original_bin"]
                if original_bin.is_file():
                    (
                        route_type,
                        decision,
                        rule_name,
                        future_parser_hint,
                        reason,
                        pdf_sniff,
                    ) = self._sniff_pdf_route(
                        sha256=sha256,
                        original_bin=original_bin,
                        use_sniff_cache=use_sniff_cache,
                        dry_run=dry_run,
                        sniff_config=sniff_config,
                    )
                else:
                    # D-3：vault 路径缺失 / original.bin 不存在 → 回退 004 判定 + WARNING。
                    reason = f"{reason}; {REASON_SNIFF_SKIPPED}"
                    logger.warning(
                        "PDF sniff skipped for sha256=%s: original.bin not accessible",
                        sha256,
                    )

            logger.info(
                "Parser route processed sha256=%s route_type=%s decision=%s rule=%s",
                sha256,
                route_type.value,
                decision,
                rule_name,
            )

            return ParserRouteDecision(
                content_uid=db_content.content_uid,
                sha256=sha256,
                file_ext=normalized_ext or fallback_ext,
                mime_type=db_content.mime_type,
                vault_path=vault_path,
                route_type=route_type,
                decision=decision,
                rule_name=rule_name,
                reason=reason,
                future_parser_hint=future_parser_hint,
                parse_status=db_content.parse_status,
                vault_uid=vault_uid,
                pdf_sniff=pdf_sniff,
            )

    def _sniff_pdf_route(
        self,
        *,
        sha256: str,
        original_bin: Path,
        use_sniff_cache: bool = True,
        dry_run: bool = False,
        sniff_config: PdfSniffConfig | None = None,
    ) -> tuple[RouteType, str, str, str, str, PdfSniffResult]:
        """014 §7.3 嗅探 + 判定段（不触 DB，供确定性单测直接调用）。

        缓存命中时不调用 `sniff_pdf`；`dry_run` 时不写缓存。
        """

        config = sniff_config or load_sniff_config()
        sniff = resolve_pdf_sniff(
            original_bin,
            sha256,
            config=config,
            cache_root=self.config.storage.sniff_cache_root,
            use_cache=use_sniff_cache,
            write_cache=not dry_run,
        )
        route_type, decision, rule_name, hint, reason = route_pdf_with_sniff(sniff)
        return route_type, decision, rule_name, hint, reason, sniff

    def _resolve_fallback_ext(
        self,
        session,
        content: KbFileContent,
    ) -> str | None:
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

    def _write_report(self, result: ParserRouteResult) -> Path | None:
        reports_root = self.config.storage.reports_root
        reports_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        generated_at = datetime.now(UTC).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
        report_path = reports_root / f"parser_route_report_{timestamp}.json"

        payload = {
            "report_type": "parser_route_report",
            "pipeline_version": self.config.pipeline_version,
            "routing_rules_version": result.routing_rules_version,
            "generated_at": generated_at,
            "summary": {
                "candidates": result.candidates,
                "routed": result.routed,
                "skipped": result.skipped,
                "unknown": result.unknown,
                "unsupported": result.unsupported,
                "errors": len(result.errors),
                # 014：PDF 嗅探计数（v1 运行恒为 0）。
                "pdf_sniffed": result.pdf_sniffed,
                "pdf_digital": result.pdf_digital,
                "pdf_scanned": result.pdf_scanned,
                "pdf_cache_hits": result.pdf_cache_hits,
            },
            "decisions": [item.to_dict() for item in result.decisions],
            "errors": [
                {"sha256": item.sha256, "message": item.message} for item in result.errors
            ],
        }

        try:
            report_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            logger.info("Wrote parser route report: %s", report_path)
            return report_path
        except Exception as exc:
            logger.exception("Failed to write parser route report")
            result.errors.append(RouteError(sha256="", message=f"Report write failed: {exc}"))
            return None
