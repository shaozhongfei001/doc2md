"""015 图片解析流水线（image parse pipeline）服务。

职责（spec §2 / plan §5）：把 `route_type=IMAGE` 的内容解析为
`parsed_text.md / parsed_metadata.json / parse_manifest.json` 三件套，并把
`assets/preprocess/{op}.png` 索引进 metadata。

三条不可让渡的约束：

1. **D-2 诚实性红线**：`OcrResult.mock is True` → `UNSUPPORTED_WITH_HINT / SKIPPED`，
   parsed 目录**零写入**（`if ocr_result.mock:` 位于写盘之前；A015-06 以变异强制该分支）。
2. **依赖缺失不降级**：`--engine paddleocr` + 依赖缺失 → `DEPENDENCY_MISSING`（CLI 退出码 1），
   绝不允许「paddleocr 缺失 → 偷偷用 mock VLM 落盘」。
3. **质量评分作用在源图**而非预处理产物：实测本机 `binarize` 会把 `GaussianBlur(6)` 的模糊图
   Laplacian 方差从 `0.58` 拉到 `2758`（自适应阈值在模糊边缘造出伪高频），若对预处理产物评分
   会把模糊图误判为清晰图、走错真值表分支。故**路由用输入质量**，预处理产物只作 OCR 输入。

测试缝：`ImageParserService(..., candidates_loader=..., ocr_client=..., vlm_client=..., scorer=...,
preprocessor=..., paddleocr_available=...)` —— 无 MySQL / 无 paddleocr 也能驱动全链路。
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml
from sqlalchemy import select

from app.adapters import vlm_adapter
from app.adapters.image_preprocess import PreprocessOutput, preprocess_image
from app.adapters.image_quality import ImageQualityResult, score_image
from app.adapters.ocr_paddle import (
    EngineDependencyMissing,
    PaddleOcrClient,
    check_paddleocr_available,
)
from app.adapters.vlm_adapter import OcrResult, VlmClient, build_vlm_client
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
from app.models.file import KbFileContent, KbFileInstance
from app.models.vault import KbRawVaultObject

logger = logging.getLogger(__name__)

# --- 契约常量（spec §6；`parser_name="image_ocr"` 是 016 才扩展白名单的新值）-------------
PARSER_NAME = "image_ocr"
PARSER_ADAPTER_VERSION = "015_mvp_v1"
PARSER_PROFILE_PADDLEOCR = "image_paddleocr_v1"
PARSER_PROFILE_VLM = "image_vlm_v1"
ROUTE_TYPE_IMAGE = RouteType.IMAGE.value

# --- 引擎 ------------------------------------------------------------------------------
ENGINE_AUTO = "auto"
ENGINE_PADDLEOCR = "paddleocr"
ENGINE_VLM = "vlm"
SUPPORTED_ENGINES: tuple[str, ...] = (ENGINE_AUTO, ENGINE_PADDLEOCR, ENGINE_VLM)

DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_LIMIT = 100

# --- status / decision 词表（spec §3.2）-------------------------------------------------
STATUS_SUCCESS = "SUCCESS"
STATUS_SKIPPED = "SKIPPED"
STATUS_FAILED = "FAILED"
STATUS_EMPTY = "EMPTY"

DECISION_PARSED = "PARSED"
DECISION_ALREADY_SUCCESS = "ALREADY_SUCCESS"
DECISION_ROUTE_MISMATCH = "ROUTE_MISMATCH"
DECISION_UNSUPPORTED_WITH_HINT = "UNSUPPORTED_WITH_HINT"
DECISION_DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
DECISION_ENGINE_ERROR = "ENGINE_ERROR"
DECISION_TIMEOUT = "TIMEOUT"
DECISION_OUTPUT_CONTRACT_VIOLATION = "OUTPUT_CONTRACT_VIOLATION"

SKIP_REASON_LIMIT = "parse_limit_reached"
SKIP_REASON_IDEMPOTENT = "idempotent_success_manifest"
SKIP_REASON_MOCK_RESULT = "mock_result_rejected"
SKIP_REASON_VLM_MOCK = "vlm_adapter_is_mock"
SKIP_REASON_NO_TRUSTED_ENGINE = "no_trusted_engine"
SKIP_REASON_MISSING_BIN = "missing_original_bin"
SKIP_REASON_DEPENDENCY_MISSING = "engine_dependency_missing"
SKIP_REASON_TIMEOUT = "engine_timeout"
SKIP_REASON_ENGINE_ERROR = "engine_error"

REPORT_TYPE = "parse_image_report"
REPORT_PREFIX = "parse_image_report_"

STATUS_CONTENT_REGISTERED = "CONTENT_REGISTERED"
STATUS_DISCOVERED = "DISCOVERED"

IN_SCOPE_ROUTE_TYPES = frozenset({RouteType.IMAGE})

DEFAULT_IMAGE_PARSE_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "image_parse.yaml"
)


def _utc_iso() -> str:
    return datetime.now(UTC).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


# ======================================================================================
# 配置
# ======================================================================================
@dataclass(frozen=True)
class ImageParseConfig:
    """`config/image_parse.yaml` 解析结果（缺文件/缺字段时回退冻结默认值）。"""

    adapter_version: str = PARSER_ADAPTER_VERSION
    scorer: str = "auto"
    blur_threshold: float = 100.0
    pillow_blur_threshold: float = 20.0
    preprocess_enabled: bool = True
    preprocess_ops: tuple[str, ...] = ("deskew", "denoise", "binarize")
    engine_default: str = ENGINE_AUTO
    paddleocr_lang: str = "ch"
    paddleocr_use_gpu: bool = False
    vlm_adapter: str = vlm_adapter.ADAPTER_MOCK
    vlm_endpoint: str | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_limit: int = DEFAULT_MAX_LIMIT


def _section(source: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = source.get(key)
    return value if isinstance(value, Mapping) else {}


def load_image_parse_config(path: Path | str | None = None) -> ImageParseConfig:
    """读取 `config/image_parse.yaml`；缺失/损坏时回退内置默认值（不抛）。"""

    config_path = Path(path) if path is not None else DEFAULT_IMAGE_PARSE_CONFIG_PATH
    if not config_path.is_file():
        logger.warning("image_parse 配置缺失，使用内置默认值: %s", config_path)
        return ImageParseConfig()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:  # pragma: no cover - 配置损坏降级
        logger.warning("image_parse 配置不可解析，使用内置默认值: %s", exc)
        return ImageParseConfig()
    if not isinstance(raw, Mapping):  # pragma: no cover - 防御
        return ImageParseConfig()

    quality = _section(raw, "quality")
    preprocess = _section(raw, "preprocess")
    engines = _section(raw, "engines")
    paddleocr_cfg = _section(engines, "paddleocr")
    vlm_cfg = _section(engines, "vlm")
    ops = preprocess.get("ops")

    return ImageParseConfig(
        adapter_version=str(_section(raw, "parser").get("adapter_version", PARSER_ADAPTER_VERSION)),
        scorer=str(quality.get("scorer", "auto")),
        blur_threshold=float(quality.get("blur_threshold", 100.0)),
        pillow_blur_threshold=float(quality.get("pillow_blur_threshold", 20.0)),
        preprocess_enabled=bool(preprocess.get("enabled", True)),
        preprocess_ops=(
            tuple(str(op) for op in ops)
            if isinstance(ops, Sequence) and not isinstance(ops, str)
            else ImageParseConfig.preprocess_ops
        ),
        engine_default=str(engines.get("default", ENGINE_AUTO)),
        paddleocr_lang=str(paddleocr_cfg.get("lang", "ch")),
        paddleocr_use_gpu=bool(paddleocr_cfg.get("use_gpu", False)),
        vlm_adapter=str(vlm_cfg.get("adapter", vlm_adapter.ADAPTER_MOCK)),
        vlm_endpoint=vlm_cfg.get("endpoint"),
        timeout_seconds=int(raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
        max_limit=int(raw.get("max_limit", DEFAULT_MAX_LIMIT)),
    )


# ======================================================================================
# 产物路径（D-5：复用既有 4 键，不新增 `ParsedArtifactPaths` 键）
# ======================================================================================
@dataclass(frozen=True)
class ImageParsedPaths:
    parsed_dir: Path
    parsed_text: Path
    parsed_metadata: Path
    parse_manifest: Path
    assets_dir: Path


def build_image_parsed_paths(parsed_root: Path | str, sha256: str) -> ImageParsedPaths:
    """`parsed/by_hash/{sha[:2]}/{sha[2:4]}/{sha}/` + `assets/preprocess/`。"""

    parsed_dir = build_parsed_content_dir(Path(parsed_root), sha256)
    artifacts = build_parsed_artifact_paths(parsed_dir)
    return ImageParsedPaths(
        parsed_dir=artifacts["parsed_dir"],
        parsed_text=artifacts["parsed_text"],
        parsed_metadata=artifacts["parsed_metadata"],
        parse_manifest=artifacts["parse_manifest"],
        assets_dir=parsed_dir / "assets" / "preprocess",
    )


# ======================================================================================
# 候选（把 DB 依赖收敛到 loader，使 plan/execute/persist 可离线驱动）
# ======================================================================================
@dataclass(frozen=True)
class ParseCandidate:
    content_uid: str
    sha256: str
    file_ext: str | None
    mime_type: str | None
    master_file_instance_uid: str | None
    original_path: Path


CandidateLoader = Callable[[int | None], list[ParseCandidate]]


# ======================================================================================
# 引擎路由真值表（plan §6）
# ======================================================================================
@dataclass(frozen=True)
class EngineRoute:
    decision: str
    engine_actual: str | None = None
    parser_profile: str | None = None
    fallback_reason: str | None = None
    skip_reason: str | None = None
    warnings: tuple[str, ...] = ()


def resolve_engine_route(
    *,
    requested: str,
    is_clear: bool,
    quality_available: bool,
    paddleocr_available: bool,
    vlm_is_mock: bool,
) -> EngineRoute:
    """plan §6 真值表（纯函数，可穷举断言）。

    `quality_available=False`（评分失败）→ 按 D-3 fail-safe 到 **VLM 路径**（等价「非清晰」）。
    """

    if requested == ENGINE_PADDLEOCR:
        # 显式 paddleocr：可用即用（低质仅告警），缺失即显式失败，绝不降级。
        if not paddleocr_available:
            return EngineRoute(
                decision=DECISION_DEPENDENCY_MISSING,
                skip_reason=SKIP_REASON_DEPENDENCY_MISSING,
            )
        warnings: tuple[str, ...] = ()
        if not (is_clear and quality_available):
            warnings = (
                "低质量图被 --engine paddleocr 强制走 PaddleOCR（auto 下真值表会改走 VLM）",
            )
        return EngineRoute(
            decision=DECISION_PARSED,
            engine_actual=ENGINE_PADDLEOCR,
            parser_profile=PARSER_PROFILE_PADDLEOCR,
            warnings=warnings,
        )

    if requested == ENGINE_VLM:
        # 显式 vlm：mock 适配器不具备识别能力 → 结构化不支持，零落盘。
        if vlm_is_mock:
            return EngineRoute(
                decision=DECISION_UNSUPPORTED_WITH_HINT,
                skip_reason=SKIP_REASON_VLM_MOCK,
            )
        return EngineRoute(
            decision=DECISION_PARSED,
            engine_actual=ENGINE_VLM,
            parser_profile=PARSER_PROFILE_VLM,
        )

    if is_clear and quality_available:
        if paddleocr_available:
            return EngineRoute(
                decision=DECISION_PARSED,
                engine_actual=ENGINE_PADDLEOCR,
                parser_profile=PARSER_PROFILE_PADDLEOCR,
            )
        if not vlm_is_mock:
            return EngineRoute(
                decision=DECISION_PARSED,
                engine_actual=ENGINE_VLM,
                parser_profile=PARSER_PROFILE_VLM,
                fallback_reason="paddleocr_missing",
            )
        return EngineRoute(
            decision=DECISION_UNSUPPORTED_WITH_HINT,
            skip_reason=SKIP_REASON_NO_TRUSTED_ENGINE,
        )

    if not vlm_is_mock:
        return EngineRoute(
            decision=DECISION_PARSED,
            engine_actual=ENGINE_VLM,
            parser_profile=PARSER_PROFILE_VLM,
        )
    return EngineRoute(
        decision=DECISION_UNSUPPORTED_WITH_HINT,
        skip_reason=SKIP_REASON_NO_TRUSTED_ENGINE,
    )


@dataclass
class ImageParsePlan:
    content_uid: str
    sha256: str
    route_type: str
    decision: str
    status: str
    parsed_dir: str
    source_vault_path: str
    parsed_text_path: str
    parsed_metadata_path: str
    parse_manifest_path: str
    assets_dir: str
    rule_name: str = ""
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
            "rule_name": self.rule_name,
            "skip_reason": self.skip_reason,
            "dry_run_action": self.dry_run_action,
        }


@dataclass
class ImageParseItem:
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
    parsed_text_path: str | None = None
    parsed_metadata_path: str | None = None
    parse_manifest_path: str | None = None
    parser_profile: str | None = None
    engine_requested: str | None = None
    engine_actual: str | None = None
    fallback_reason: str | None = None
    quality_score: float | None = None
    quality_method: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """D-2：**不得**把引擎原文写进条目（mock/伪造文本不进报告）。"""

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
            "parser_profile": self.parser_profile,
            "engine": {
                "requested": self.engine_requested,
                "actual": self.engine_actual,
                "fallback_reason": self.fallback_reason,
            },
            "image_quality": {"score": self.quality_score, "method": self.quality_method},
            "warnings": list(self.warnings),
        }


@dataclass
class ImageParseError:
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
class ImageParseManyResult:
    dry_run: bool = False
    total_candidates: int = 0
    in_scope_candidates: int = 0
    parsed_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    unsupported_count: int = 0
    empty_count: int = 0
    report_path: Path | None = None
    items: list[ImageParseItem] = field(default_factory=list)
    errors: list[ImageParseError] = field(default_factory=list)
    plans: list[ImageParsePlan] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "total_candidates": self.total_candidates,
            "in_scope_candidates": self.in_scope_candidates,
            "parsed_count": self.parsed_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "unsupported_count": self.unsupported_count,
            "empty_count": self.empty_count,
            "report_path": None if self.report_path is None else self.report_path.as_posix(),
            "items": [item.to_dict() for item in self.items],
            "errors": [error.to_dict() for error in self.errors],
        }


class _EngineTimeout(RuntimeError):
    """引擎调用超时（`timeout_seconds` 触发的看门狗）。"""


def _call_with_timeout(callback: Callable[[], OcrResult], timeout_seconds: int) -> OcrResult:
    """线程看门狗：引擎卡死时也能保证「单条不拖垮整批」。

    超时只把该条标 FAILED 并放行（worker 为 daemon 线程，不阻塞进程退出）。
    """

    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["value"] = callback()
        except BaseException as exc:  # noqa: BLE001 - 原样透传，由调用方分类
            box["error"] = exc

    worker = threading.Thread(target=_run, name="image-parse-engine", daemon=True)
    worker.start()
    worker.join(timeout_seconds if timeout_seconds else None)
    if worker.is_alive():
        raise _EngineTimeout(f"引擎调用超过 {timeout_seconds}s 未返回")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _requested_profile(engine: str) -> str | None:
    """幂等键里的 profile：显式引擎 → 固定 profile；auto → 不约束 profile。"""

    if engine == ENGINE_PADDLEOCR:
        return PARSER_PROFILE_PADDLEOCR
    if engine == ENGINE_VLM:
        return PARSER_PROFILE_VLM
    return None


class ImageParserService:
    """015 图片解析服务（无 paddleocr / 无 MySQL 环境同样可构造与测试）。"""

    def __init__(
        self,
        config: AppConfig,
        *,
        image_config: ImageParseConfig | None = None,
        ocr_client: Any | None = None,
        vlm_client: Any | None = None,
        scorer: Callable[..., ImageQualityResult] | None = None,
        preprocessor: Callable[..., PreprocessOutput] | None = None,
        candidates_loader: CandidateLoader | None = None,
        paddleocr_available: bool | None = None,
    ) -> None:
        ensure_readonly(config)
        self.config = config
        self.image_config = (
            image_config if image_config is not None else load_image_parse_config()
        )
        self._ocr_client_override = ocr_client
        # 未知 adapter → ValueError（fail-fast，绝不静默降级为 mock）
        self._vlm_client = (
            vlm_client
            if vlm_client is not None
            else build_vlm_client({"adapter": self.image_config.vlm_adapter})
        )
        self._scorer = scorer if scorer is not None else score_image
        self._preprocessor = preprocessor if preprocessor is not None else preprocess_image
        self._candidates_loader = candidates_loader
        self._paddleocr_available_override = paddleocr_available
        engine = create_db_engine(config)
        self.session_factory = create_session_factory(engine)


    # ----------------------------------------------------------------------------------
    # 主入口
    # ----------------------------------------------------------------------------------
    def parse_many(
        self,
        *,
        sha256: str | None = None,
        content_uid: str | None = None,
        limit: int | None = None,
        engine: str = ENGINE_AUTO,
        dry_run: bool = False,
        force: bool = False,
        timeout_seconds: int | None = None,
        register: bool = False,
        registry_service: Any | None = None,
    ) -> ImageParseManyResult:
        engine = str(engine).strip().lower()
        if engine not in SUPPORTED_ENGINES:
            raise ValueError(
                f"不支持的 engine: {engine!r}；可选 {', '.join(SUPPORTED_ENGINES)}"
            )
        effective_limit = self.image_config.max_limit if limit is None else limit
        effective_timeout = (
            self.image_config.timeout_seconds if timeout_seconds is None else timeout_seconds
        )

        result = ImageParseManyResult(dry_run=dry_run)
        candidates = self._load_candidates(
            sha256=sha256, content_uid=content_uid, limit=effective_limit
        )
        result.total_candidates = len(candidates)

        in_scope_actions = 0
        for candidate in candidates:
            plan = self._build_plan(
                candidate=candidate, force=force, dry_run=dry_run, engine=engine
            )
            result.plans.append(plan)
            item = self._plan_to_item(plan)
            item.engine_requested = engine

            if plan.route_type == ROUTE_TYPE_IMAGE and plan.decision in (
                DECISION_PARSED,
                DECISION_ALREADY_SUCCESS,
            ):
                result.in_scope_candidates += 1

            if plan.decision == DECISION_PARSED:
                if effective_limit is not None and in_scope_actions >= effective_limit:
                    item.skip_reason = SKIP_REASON_LIMIT
                    item.status = STATUS_SKIPPED
                    item.dry_run_action = "would_skip"
                    result.items.append(item)
                    self._update_summary_counts(result, item)
                    continue
                if dry_run:
                    item.dry_run_action = "would_parse"
                    in_scope_actions += 1
                    result.items.append(item)
                    self._update_summary_counts(result, item)
                    continue

                item, error = self._execute_parse(
                    candidate=candidate,
                    plan=plan,
                    force=force,
                    engine=engine,
                    timeout_seconds=effective_timeout,
                )
                in_scope_actions += 1
                result.items.append(item)
                if error is not None:
                    result.errors.append(error)
                self._update_summary_counts(result, item)
                continue

            # 非 PARSED：路由不匹配 / 幂等命中 / 契约违反
            if dry_run:
                item.dry_run_action = "would_skip"
            if (
                plan.decision == DECISION_OUTPUT_CONTRACT_VIOLATION
                and plan.skip_reason == SKIP_REASON_MISSING_BIN
            ):
                result.errors.append(
                    ImageParseError(
                        content_uid=candidate.content_uid,
                        sha256=candidate.sha256,
                        code="MISSING_ORIGINAL_BIN",
                        message=f"original.bin 不存在: {plan.source_vault_path}",
                    )
                )
            result.items.append(item)
            self._update_summary_counts(result, item)

        if not dry_run:
            result.report_path = self._write_batch_report(
                result=result,
                engine=engine,
                sha256=sha256,
                content_uid=content_uid,
                limit=effective_limit,
                timeout_seconds=effective_timeout,
                force=force,
                register=register,
            )
            if register:
                self._register_report(
                    report_path=result.report_path, registry_service=registry_service
                )
        return result

    # ----------------------------------------------------------------------------------
    # 候选加载
    # ----------------------------------------------------------------------------------
    def _load_candidates(
        self,
        *,
        sha256: str | None = None,
        content_uid: str | None = None,
        limit: int | None = None,
    ) -> list[ParseCandidate]:
        if self._candidates_loader is not None:
            candidates = list(self._candidates_loader(limit))
            if sha256:
                candidates = [c for c in candidates if c.sha256 == sha256]
            elif content_uid:
                candidates = [c for c in candidates if c.content_uid == content_uid]
            return candidates
        return self._load_candidates_from_db(sha256=sha256, content_uid=content_uid)

    def _load_candidates_from_db(
        self, *, sha256: str | None = None, content_uid: str | None = None
    ) -> list[ParseCandidate]:
        """与 007 同口径的候选集：已注册内容 + 已复制到 vault 的对象。"""

        query = (
            select(KbFileContent)
            .where(KbFileContent.status == STATUS_CONTENT_REGISTERED)
            .where(KbFileContent.vault_status == VAULT_COPIED)
            .order_by(KbFileContent.id.asc())
        )
        if sha256:
            query = query.where(KbFileContent.sha256 == sha256)
        elif content_uid:
            query = query.where(KbFileContent.content_uid == content_uid)

        candidates: list[ParseCandidate] = []
        with self.session_factory() as session:
            for row in session.scalars(query).all():
                vault_object = session.scalar(
                    select(KbRawVaultObject).where(KbRawVaultObject.sha256 == row.sha256)
                )
                fallback_ext = self._resolve_fallback_ext(session, row)
                vault_dir = self._resolve_vault_dir(row, vault_object, row.sha256)
                candidates.append(
                    ParseCandidate(
                        content_uid=row.content_uid,
                        sha256=row.sha256,
                        file_ext=normalize_file_ext(row.file_ext) or fallback_ext,
                        mime_type=row.mime_type,
                        master_file_instance_uid=row.master_file_instance_uid,
                        original_path=build_vault_artifact_paths(vault_dir)["original_bin"],
                    )
                )
        return candidates

    def _resolve_fallback_ext(self, session: Any, row: KbFileContent) -> str | None:
        if not row.master_file_instance_uid:
            return None
        instance = session.scalar(
            select(KbFileInstance).where(
                KbFileInstance.file_instance_uid == row.master_file_instance_uid
            )
        )
        if instance is None:
            return None
        return normalize_file_ext(instance.file_ext or ext_from_path(instance.source_path))

    def _resolve_vault_dir(
        self, row: KbFileContent, vault_object: Any | None, sha256: str
    ) -> Path:
        if vault_object is not None and getattr(vault_object, "vault_dir", None):
            return Path(vault_object.vault_dir)
        return build_vault_dir(self.config.storage.raw_vault_root, sha256)

    # ----------------------------------------------------------------------------------
    # 计划
    # ----------------------------------------------------------------------------------
    def _build_plan(
        self,
        *,
        candidate: ParseCandidate,
        force: bool,
        dry_run: bool = False,
        engine: str = ENGINE_AUTO,
    ) -> ImageParsePlan:
        paths = build_image_parsed_paths(self.config.storage.parsed_root, candidate.sha256)
        original = Path(candidate.original_path)
        route_type, decision, rule_name, _hint, reason = match_route_type(
            file_ext=normalize_file_ext(candidate.file_ext),
            mime_type=candidate.mime_type,
            fallback_ext=None,
        )
        plan = ImageParsePlan(
            content_uid=candidate.content_uid,
            sha256=candidate.sha256,
            route_type=route_type.value,
            decision=DECISION_ROUTE_MISMATCH,
            status=STATUS_SKIPPED,
            parsed_dir=paths.parsed_dir.as_posix(),
            source_vault_path=original.as_posix(),
            parsed_text_path=paths.parsed_text.as_posix(),
            parsed_metadata_path=paths.parsed_metadata.as_posix(),
            parse_manifest_path=paths.parse_manifest.as_posix(),
            assets_dir=paths.assets_dir.as_posix(),
            rule_name=rule_name,
        )
        if decision != DECISION_ROUTE or route_type not in IN_SCOPE_ROUTE_TYPES:
            plan.skip_reason = f"route_type={route_type.value}; {reason}"
            return plan

        if not force and self._should_skip_idempotent(
            paths.parse_manifest, _requested_profile(engine)
        ):
            plan.decision = DECISION_ALREADY_SUCCESS
            plan.skip_reason = SKIP_REASON_IDEMPOTENT
            plan.dry_run_action = "would_skip"
            return plan

        if not original.is_file():
            plan.decision = DECISION_OUTPUT_CONTRACT_VIOLATION
            plan.status = STATUS_FAILED
            plan.skip_reason = SKIP_REASON_MISSING_BIN
            return plan

        if dry_run:
            route, _quality, _pre = self._preview_route(original, engine=engine)
            plan.decision = route.decision
            if route.decision == DECISION_PARSED:
                plan.status = STATUS_SUCCESS
                plan.dry_run_action = "would_parse"
            else:
                plan.status = (
                    STATUS_FAILED
                    if route.decision == DECISION_DEPENDENCY_MISSING
                    else STATUS_SKIPPED
                )
                plan.skip_reason = route.skip_reason
                plan.dry_run_action = "would_skip"
            return plan

        plan.decision = DECISION_PARSED
        plan.status = STATUS_SUCCESS
        plan.dry_run_action = "would_parse"
        return plan

    def _plan_to_item(self, plan: ImageParsePlan) -> ImageParseItem:
        return ImageParseItem(
            content_uid=plan.content_uid,
            sha256=plan.sha256,
            route_type=plan.route_type,
            decision=plan.decision,
            status=plan.status,
            parsed_dir=plan.parsed_dir,
            source_vault_path=plan.source_vault_path,
            skip_reason=plan.skip_reason,
            dry_run_action=plan.dry_run_action,
            assets_dir=plan.assets_dir,
            parsed_text_path=plan.parsed_text_path,
            parsed_metadata_path=plan.parsed_metadata_path,
            parse_manifest_path=plan.parse_manifest_path,
        )

    def _should_skip_idempotent(
        self, manifest_path: Path, requested_profile: str | None
    ) -> bool:
        """幂等键：SUCCESS 且 parser_name / adapter_version 一致，profile 与本次请求一致。"""

        if not manifest_path.is_file():
            return False
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if payload.get("status") != STATUS_SUCCESS:
            return False
        if payload.get("parser_name") != PARSER_NAME:
            return False
        if payload.get("parser_adapter_version") != PARSER_ADAPTER_VERSION:
            return False
        if requested_profile is not None and payload.get("parser_profile") != requested_profile:
            return False
        return True

    def _clear_parsed_outputs(self, paths: ImageParsedPaths) -> None:
        """`--force`：整目录清空（三件套 + assets 一并干净）。"""

        shutil.rmtree(paths.parsed_dir, ignore_errors=True)

    def _update_summary_counts(
        self, result: ImageParseManyResult, item: ImageParseItem
    ) -> None:
        if item.decision == DECISION_UNSUPPORTED_WITH_HINT:
            result.unsupported_count += 1
        elif item.status == STATUS_FAILED:
            result.failed_count += 1
        elif item.status == STATUS_EMPTY:
            result.empty_count += 1
        elif item.status == STATUS_SUCCESS:
            result.parsed_count += 1
        else:
            result.skipped_count += 1

    # ----------------------------------------------------------------------------------
    # 执行
    # ----------------------------------------------------------------------------------
    def _execute_parse(
        self,
        *,
        candidate: ParseCandidate,
        plan: ImageParsePlan,
        force: bool,
        engine: str,
        timeout_seconds: int,
    ) -> tuple[ImageParseItem, ImageParseError | None]:
        paths = build_image_parsed_paths(self.config.storage.parsed_root, plan.sha256)
        original = Path(plan.source_vault_path)
        item = self._plan_to_item(plan)
        item.engine_requested = engine

        if force:
            self._clear_parsed_outputs(paths)

        staging_dir = (
            self.config.storage.parsed_root / ".staging" / f"image_{uuid.uuid4().hex[:12]}"
        )
        staging_dir.mkdir(parents=True, exist_ok=True)
        try:
            route, quality, pre = self._route_for(original, staging_dir, engine=engine)
            item.parser_profile = route.parser_profile
            item.engine_actual = route.engine_actual
            item.fallback_reason = route.fallback_reason
            item.quality_score = quality.score
            item.quality_method = quality.method
            warnings = list(route.warnings) + list(pre.warnings)
            item.warnings = list(warnings)

            if route.decision == DECISION_DEPENDENCY_MISSING:
                item.decision = DECISION_DEPENDENCY_MISSING
                item.status = STATUS_FAILED
                item.skip_reason = SKIP_REASON_DEPENDENCY_MISSING
                return item, ImageParseError(
                    content_uid=candidate.content_uid,
                    sha256=candidate.sha256,
                    code="DEPENDENCY_MISSING",
                    message=(
                        "engine=paddleocr 依赖缺失：paddleocr 不可导入；"
                        "请安装 paddleocr 或改用 --engine vlm"
                    ),
                )

            if route.decision != DECISION_PARSED:
                item.decision = DECISION_UNSUPPORTED_WITH_HINT
                item.status = STATUS_SKIPPED
                item.skip_reason = route.skip_reason
                return item, None

            client = self._client_for(route.engine_actual or ENGINE_PADDLEOCR)
            try:
                ocr_result = _call_with_timeout(
                    lambda: client.recognize(
                        pre.output_path, timeout_seconds=timeout_seconds
                    ),
                    timeout_seconds,
                )
            except _EngineTimeout as exc:
                item.decision = DECISION_TIMEOUT
                item.status = STATUS_FAILED
                item.skip_reason = SKIP_REASON_TIMEOUT
                return item, ImageParseError(
                    content_uid=candidate.content_uid,
                    sha256=candidate.sha256,
                    code="TIMEOUT",
                    message=str(exc),
                )
            except Exception as exc:  # noqa: BLE001 - 单条失败不拖垮整批
                logger.warning("图片解析引擎异常 sha=%s: %s", candidate.sha256, exc)
                item.decision = DECISION_ENGINE_ERROR
                item.status = STATUS_FAILED
                item.skip_reason = SKIP_REASON_ENGINE_ERROR
                return item, ImageParseError(
                    content_uid=candidate.content_uid,
                    sha256=candidate.sha256,
                    code="ENGINE_ERROR",
                    message=f"{type(exc).__name__}: {exc}",
                )

            # ---- D-2 诚实性红线（A015-06 变异锚点：删掉本 if → 对应用例必 FAIL）----
            if ocr_result.mock:
                item.decision = DECISION_UNSUPPORTED_WITH_HINT
                item.status = STATUS_SKIPPED
                item.skip_reason = SKIP_REASON_MOCK_RESULT
                item.warnings.append(
                    "引擎返回 mock 结果（无真实识别能力），已拒绝落盘"
                )
                return item, None

            return (
                self._persist(
                    candidate=candidate,
                    plan=plan,
                    route=route,
                    quality=quality,
                    pre=pre,
                    ocr_result=ocr_result,
                    engine=engine,
                    warnings=warnings,
                ),
                None,
            )
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def _persist(
        self,
        *,
        candidate: ParseCandidate,
        plan: ImageParsePlan,
        route: EngineRoute,
        quality: ImageQualityResult,
        pre: PreprocessOutput,
        ocr_result: OcrResult,
        engine: str,
        warnings: list[str],
    ) -> ImageParseItem:
        paths = build_image_parsed_paths(self.config.storage.parsed_root, plan.sha256)
        paths.parsed_dir.mkdir(parents=True, exist_ok=True)
        generated_at = _utc_iso()

        text = ocr_result.text
        paths.parsed_text.write_text(text, encoding="utf-8")
        output_size_bytes = paths.parsed_text.stat().st_size
        output_hash = compute_sha256(paths.parsed_text)

        asset_entries = self._collect_preprocess_assets(pre.asset_files, paths)
        status = STATUS_EMPTY if not text.strip() else STATUS_SUCCESS
        source = Path(plan.source_vault_path)
        content_size_bytes = source.stat().st_size if source.is_file() else 0
        applied_ops = list(pre.applied_ops)

        metadata_payload: dict[str, Any] = {
            "parser_name": PARSER_NAME,
            "parser_profile": route.parser_profile,
            "parser_adapter_version": PARSER_ADAPTER_VERSION,
            "route_type": ROUTE_TYPE_IMAGE,
            "content_uid": candidate.content_uid,
            "sha256": plan.sha256,
            "source_vault_path": plan.source_vault_path,
            "generated_at": generated_at,
            # 评分作用在源图（见模块 docstring 第 3 条）
            "image_quality": {
                "score": quality.score,
                "method": quality.method,
                "preprocess_ops": applied_ops,
            },
            # 无真实置信度时写 null，不得编造（spec §8 规则 5）
            "ocr_confidence": {
                "mean": ocr_result.confidence_mean,
                "min": ocr_result.confidence_min,
                "sample_count": ocr_result.sample_count,
            },
            "engine": {
                "requested": engine,
                "actual": route.engine_actual,
                "fallback_reason": route.fallback_reason,
            },
            "extra": {
                "preprocess_ops": applied_ops,
                "preprocess_skipped_ops": list(pre.skipped_ops),
                "preprocess_ops_detail": [dict(detail) for detail in pre.ops_detail],
                "asset_files": asset_entries,
                "engine_meta": dict(ocr_result.engine_meta),
                "warnings": list(warnings),
            },
        }
        paths.parsed_metadata.write_text(
            json.dumps(metadata_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        manifest_payload: dict[str, Any] = {
            "content_uid": candidate.content_uid,
            "sha256": plan.sha256,
            "route_type": ROUTE_TYPE_IMAGE,
            "parser_name": PARSER_NAME,
            "parser_profile": route.parser_profile,
            "parser_adapter_version": PARSER_ADAPTER_VERSION,
            "source_vault_path": plan.source_vault_path,
            "status": status,
            "content_size_bytes": content_size_bytes,
            "output_size_bytes": output_size_bytes,
            "output_hash": output_hash,
            "generated_at": generated_at,
            "assets_dir": paths.assets_dir.as_posix(),
            "engine": dict(metadata_payload["engine"]),
            "image_quality": dict(metadata_payload["image_quality"]),
            "input_metadata": {
                "file_ext": normalize_file_ext(candidate.file_ext),
                "mime_type": candidate.mime_type,
                "rule_name": plan.rule_name,
            },
        }
        paths.parse_manifest.write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        item = self._plan_to_item(plan)
        item.decision = DECISION_PARSED
        item.status = status
        item.parser_profile = route.parser_profile
        item.engine_requested = engine
        item.engine_actual = route.engine_actual
        item.fallback_reason = route.fallback_reason
        item.quality_score = quality.score
        item.quality_method = quality.method
        item.warnings = list(warnings)
        return item

    def _collect_preprocess_assets(
        self, asset_files: Sequence[Path], paths: ImageParsedPaths
    ) -> list[dict[str, str]]:
        """把 staging 里的预处理产物复制进 `parsed/assets/preprocess/` 并建索引。"""

        entries: list[dict[str, str]] = []
        if asset_files:
            paths.assets_dir.mkdir(parents=True, exist_ok=True)
        for source in asset_files:
            target = paths.assets_dir / Path(source).name
            shutil.copy2(source, target)
            entries.append(
                {
                    "relative_path": target.relative_to(paths.parsed_dir).as_posix(),
                    "source_name": Path(source).name,
                    "sha256": compute_sha256(target),
                }
            )
        return entries

    # ----------------------------------------------------------------------------------
    # 质量 / 预处理 / 路由
    # ----------------------------------------------------------------------------------
    def _route_for(
        self, image_path: Path, staging_dir: Path, *, engine: str
    ) -> tuple[EngineRoute, ImageQualityResult, PreprocessOutput]:
        config = self.image_config
        ops = tuple(config.preprocess_ops) if config.preprocess_enabled else ()
        pre = (
            self._preprocessor(image_path, staging_dir, ops)
            if ops
            else PreprocessOutput(output_path=Path(image_path))
        )
        # 路由依据 = **源图**质量（见模块 docstring 第 3 条）
        quality = self._score(image_path)
        route = resolve_engine_route(
            requested=engine,
            is_clear=quality.score is not None
            and quality.score >= self._threshold_for(quality.method),
            quality_available=quality.score is not None,
            paddleocr_available=self._paddleocr_available(),
            vlm_is_mock=self._vlm_is_mock(),
        )
        return route, quality, pre

    def _preview_route(self, image_path: Path, *, engine: str) -> tuple[
        EngineRoute, ImageQualityResult, PreprocessOutput
    ]:
        """dry-run 专用：在系统临时目录里做同样的判定，**零 parsed_root 足迹**。"""

        with tempfile.TemporaryDirectory(prefix="image_parse_dryrun_") as tmp:
            return self._route_for(image_path, Path(tmp), engine=engine)

    def _score(self, image_path: Path) -> ImageQualityResult:
        try:
            return self._scorer(image_path, self.image_config.scorer)
        except Exception as exc:  # noqa: BLE001 - 评分失败 fail-safe 到 VLM 路径
            logger.warning("图像质量评分失败 %s: %s", image_path, exc)
            return ImageQualityResult(None, "unavailable", str(exc))

    def _threshold_for(self, method: str | None) -> float:
        if method == "pillow_gradient":
            return self.image_config.pillow_blur_threshold
        return self.image_config.blur_threshold

    def _paddleocr_available(self) -> bool:
        if self._paddleocr_available_override is not None:
            return self._paddleocr_available_override
        try:
            check_paddleocr_available()
        except EngineDependencyMissing:
            return False
        return True

    def _vlm_is_mock(self) -> bool:
        return vlm_adapter.client_is_mock(self._vlm_client)

    def _client_for(self, engine_actual: str) -> Any:
        if engine_actual == ENGINE_PADDLEOCR:
            if self._ocr_client_override is not None:
                return self._ocr_client_override
            return PaddleOcrClient(
                lang=self.image_config.paddleocr_lang,
                use_gpu=self.image_config.paddleocr_use_gpu,
            )
        if engine_actual == ENGINE_VLM:
            return self._vlm_client
        raise ValueError(f"未知引擎: {engine_actual!r}")  # pragma: no cover - 路由已收口

    # ----------------------------------------------------------------------------------
    # 报告 / 注册
    # ----------------------------------------------------------------------------------
    def _write_batch_report(
        self,
        *,
        result: ImageParseManyResult,
        engine: str,
        sha256: str | None,
        content_uid: str | None,
        limit: int | None,
        timeout_seconds: int,
        force: bool,
        register: bool,
    ) -> Path:
        reports_root = Path(self.config.storage.reports_root)
        reports_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        report_path = reports_root / f"{REPORT_PREFIX}{stamp}.json"
        payload = {
            "report_type": REPORT_TYPE,
            "parser_name": PARSER_NAME,
            "parser_adapter_version": PARSER_ADAPTER_VERSION,
            "generated_at": _utc_iso(),
            "filters": {
                "sha256": sha256,
                "content_uid": content_uid,
                "limit": limit,
                "engine": engine,
                "timeout_seconds": timeout_seconds,
                "force": force,
                "register": register,
                "dry_run": result.dry_run,
            },
            "summary": {
                "total": result.total_candidates,
                "in_scope": result.in_scope_candidates,
                "parsed": result.parsed_count,
                "skipped": result.skipped_count,
                "failed": result.failed_count,
                "unsupported": result.unsupported_count,
            },
            "items": [item.to_dict() for item in result.items],
            "errors": [error.to_dict() for error in result.errors],
        }
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return report_path

    def _register_report(self, *, report_path: Path, registry_service: Any | None) -> None:
        service = registry_service
        if service is None:
            from app.services.parse_registry import ParseRegistryService

            service = ParseRegistryService(self.session_factory)
        service.register_parse_report(report_path=report_path)

