"""014 PDF 内容嗅探（纯函数 + 文件缓存）。

红线（spec 014 §8）：
- 只读打开 vault 内 `original.bin`，绝不写入 / 移动 / 删除；
- 不写 DB、不写 parsed/curated/quarantine；
- fail-closed：任何不确定情形（加密 / 损坏 / 空页 / 依赖缺失）都不得返回
  `TEXT_LAYER_PRESENT`，由上层统一归入 `PDF_SCANNED_OR_IMAGE`；
- 嗅探自身异常不抛出，转化为 `SNIFF_ERROR` 结果。

Owner O-5 FROZEN 阈值：`digital_min_total_chars=100`、`digital_min_avg_chars_per_page=20`、
`pages_max=20`（`config/pdf_sniffing.yaml`，缺失时用本模块默认值）。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# 嗅探语义版本：写入缓存并参与三重校验（cache_type / sha256 / sniffer_version）。
# 注意：任何影响判定结果的改动（阈值、页均算法、状态判定规则）都必须 bump 本常量，
# 否则旧缓存会因 sniffer_version 未变而被静默复用（复核 [5b]）。
SNIFFER_VERSION = "pdf_sniff_v1"
CACHE_TYPE = "pdf_sniff_cache"

DEFAULT_PAGES_MAX = 20
DEFAULT_DIGITAL_MIN_TOTAL_CHARS = 100
DEFAULT_DIGITAL_MIN_AVG_CHARS_PER_PAGE = 20
DEFAULT_CACHE_ENABLED = True

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SNIFF_CONFIG_PATH = PROJECT_ROOT / "config" / "pdf_sniffing.yaml"

CONFIG_SECTION = "pdf_sniffing"

REASON_SNIFF_SKIPPED = "sniff skipped: original.bin not accessible"


class SniffStatus(str, Enum):
    TEXT_LAYER_PRESENT = "TEXT_LAYER_PRESENT"
    TEXT_LAYER_ABSENT = "TEXT_LAYER_ABSENT"
    ENCRYPTED = "ENCRYPTED"
    EMPTY_DOCUMENT = "EMPTY_DOCUMENT"
    SNIFF_ERROR = "SNIFF_ERROR"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"


# 可入缓存的「内容派生态」白名单。环境态（DEPENDENCY_MISSING：pypdf 未装）与
# 可能瞬时态（SNIFF_ERROR：IO / 权限 / 截断）**不写缓存**——否则环境会被固化成权威判定：
# 装上 pypdf 后仍会一直命中 `pdf_sniff_unavailable`（spec §7 规则 4 / plan §8.4 D-4）。
CACHEABLE_SNIFF_STATUSES = frozenset(
    {
        SniffStatus.TEXT_LAYER_PRESENT,
        SniffStatus.TEXT_LAYER_ABSENT,
        SniffStatus.ENCRYPTED,
        SniffStatus.EMPTY_DOCUMENT,
    }
)


@dataclass(frozen=True)
class PdfSniffConfig:
    """嗅探阈值与版本；与 `config/pdf_sniffing.yaml` 一一对应。"""

    sniffer_version: str = SNIFFER_VERSION
    pages_max: int = DEFAULT_PAGES_MAX
    digital_min_total_chars: int = DEFAULT_DIGITAL_MIN_TOTAL_CHARS
    digital_min_avg_chars_per_page: int = DEFAULT_DIGITAL_MIN_AVG_CHARS_PER_PAGE
    cache_enabled: bool = DEFAULT_CACHE_ENABLED


@dataclass(frozen=True)
class PdfSniffResult:
    status: SniffStatus
    text_chars: int = 0
    pages_total: int = 0
    pages_sampled: int = 0
    avg_chars_per_page: float = 0.0
    from_cache: bool = False
    sniffer_version: str = SNIFFER_VERSION
    detail: str = ""

    def evidence(self) -> dict:
        """报告 `decisions[].pdf_sniff` 证据块（plan §5.2）。"""
        return {
            "status": self.status.value,
            "text_chars": self.text_chars,
            "pages_total": self.pages_total,
            "pages_sampled": self.pages_sampled,
            "avg_chars_per_page": self.avg_chars_per_page,
            "from_cache": self.from_cache,
            "sniffer_version": self.sniffer_version,
        }

    def cache_payload(self, sha256: str) -> dict:
        return {
            "cache_type": CACHE_TYPE,
            "sha256": sha256,
            "sniffer_version": self.sniffer_version,
            "status": self.status.value,
            "text_chars": self.text_chars,
            "pages_total": self.pages_total,
            "pages_sampled": self.pages_sampled,
            "avg_chars_per_page": self.avg_chars_per_page,
            # `detail` 额外落到缓存，保证 SNIFF_ERROR 命中缓存时 reason 逐字段稳定（A014-06）。
            "detail": self.detail,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }


def load_sniff_config(config_path: Path | None = None) -> PdfSniffConfig:
    """读取嗅探阈值配置；文件缺失或非法时回退代码默认值（不抛）。"""

    path = Path(config_path) if config_path is not None else SNIFF_CONFIG_PATH
    try:
        if not path.is_file():
            return PdfSniffConfig()
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except Exception as exc:  # noqa: BLE001 - 配置损坏不阻断路由
        logger.warning("Failed to read pdf sniffing config %s: %s", path, exc)
        return PdfSniffConfig()

    section = data.get(CONFIG_SECTION) if isinstance(data, dict) else None
    if not isinstance(section, dict):
        if data:
            logger.warning("pdf sniffing config missing '%s' section: %s", CONFIG_SECTION, path)
        return PdfSniffConfig()

    try:
        return PdfSniffConfig(
            sniffer_version=str(section.get("sniffer_version", SNIFFER_VERSION)),
            pages_max=int(section.get("pages_max", DEFAULT_PAGES_MAX)),
            digital_min_total_chars=int(
                section.get("digital_min_total_chars", DEFAULT_DIGITAL_MIN_TOTAL_CHARS)
            ),
            digital_min_avg_chars_per_page=int(
                section.get(
                    "digital_min_avg_chars_per_page", DEFAULT_DIGITAL_MIN_AVG_CHARS_PER_PAGE
                )
            ),
            cache_enabled=bool(section.get("cache_enabled", DEFAULT_CACHE_ENABLED)),
        )
    except (TypeError, ValueError) as exc:
        logger.warning("Invalid pdf sniffing config values in %s: %s", path, exc)
        return PdfSniffConfig()


def _load_pdf_reader():
    """间接层：使「pypdf 未安装」可在测试中确定性地注入。"""

    from pypdf import PdfReader  # noqa: PLC0415 - 延迟导入，pypdf 缺失时降级而非崩溃

    return PdfReader


def sniff_pdf(original_bin: Path, *, config: PdfSniffConfig) -> PdfSniffResult:
    """只读探测 PDF 文本层。任何不确定情形 fail-closed（绝不 PRESENT）。"""

    if not isinstance(original_bin, Path):
        original_bin = Path(original_bin)

    try:
        pdf_reader_cls = _load_pdf_reader()
    except ImportError:
        return PdfSniffResult(
            status=SniffStatus.DEPENDENCY_MISSING,
            sniffer_version=config.sniffer_version,
            detail="pypdf is not installed",
        )

    try:
        with original_bin.open("rb") as handle:
            reader = pdf_reader_cls(handle, strict=False)
            if reader.is_encrypted:
                # 不解密、不尝试任何口令（plan §13 Q6）。
                return PdfSniffResult(
                    status=SniffStatus.ENCRYPTED,
                    sniffer_version=config.sniffer_version,
                    detail="pdf is encrypted",
                )

            pages = reader.pages
            pages_total = len(pages)
            if pages_total == 0:
                return PdfSniffResult(
                    status=SniffStatus.EMPTY_DOCUMENT,
                    sniffer_version=config.sniffer_version,
                    detail="pdf has zero pages",
                )

            pages_sampled = min(int(config.pages_max), pages_total)
            text_chars = 0
            for index in range(pages_sampled):
                extracted = pages[index].extract_text()
                text_chars += len(extracted or "")
            # 任一页抛异常即整档失败（不做部分采样折算）——异常由下方 except 捕获。
    except Exception as exc:  # noqa: BLE001 - fail-closed：损坏 / 截断 / 非法一律 SNIFF_ERROR
        return PdfSniffResult(
            status=SniffStatus.SNIFF_ERROR,
            sniffer_version=config.sniffer_version,
            detail=type(exc).__name__,
        )

    avg_chars_per_page = text_chars / pages_sampled if pages_sampled else 0.0
    is_present = (
        text_chars >= config.digital_min_total_chars
        and avg_chars_per_page >= config.digital_min_avg_chars_per_page
    )
    return PdfSniffResult(
        status=SniffStatus.TEXT_LAYER_PRESENT if is_present else SniffStatus.TEXT_LAYER_ABSENT,
        text_chars=text_chars,
        pages_total=pages_total,
        pages_sampled=pages_sampled,
        avg_chars_per_page=avg_chars_per_page,
        sniffer_version=config.sniffer_version,
        detail="" if is_present else "text layer below threshold",
    )


def cache_path_for(cache_root: Path, sha256: str) -> Path:
    return Path(cache_root) / "by_hash" / sha256[:2].lower() / f"{sha256}.json"


def load_sniff_cache(
    cache_root: Path,
    sha256: str,
    sniffer_version: str,
) -> PdfSniffResult | None:
    """三重校验（cache_type / sha256 / sniffer_version）任一不符即弃用并返回 None。"""

    path = cache_path_for(cache_root, sha256)
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 脏缓存只 WARNING，不阻断
        logger.warning("Discarding unreadable pdf sniff cache %s: %s", path, exc)
        return None

    if not isinstance(payload, dict):
        logger.warning("Discarding pdf sniff cache with non-object payload: %s", path)
        return None
    if payload.get("cache_type") != CACHE_TYPE:
        logger.warning("Discarding pdf sniff cache with wrong cache_type: %s", path)
        return None
    if payload.get("sha256") != sha256:
        logger.warning("Discarding pdf sniff cache with mismatched sha256: %s", path)
        return None
    if payload.get("sniffer_version") != sniffer_version:
        logger.warning("Discarding pdf sniff cache with stale sniffer_version: %s", path)
        return None

    try:
        status = SniffStatus(payload["status"])
        text_chars = int(payload.get("text_chars", 0))
        pages_total = int(payload.get("pages_total", 0))
        pages_sampled = int(payload.get("pages_sampled", 0))
        avg_chars_per_page = float(payload.get("avg_chars_per_page", 0.0))
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("Discarding malformed pdf sniff cache %s: %s", path, exc)
        return None

    return PdfSniffResult(
        status=status,
        text_chars=text_chars,
        pages_total=pages_total,
        pages_sampled=pages_sampled,
        avg_chars_per_page=avg_chars_per_page,
        from_cache=True,
        sniffer_version=sniffer_version,
        detail=str(payload.get("detail", "")),
    )


def save_sniff_cache(cache_root: Path, sha256: str, result: PdfSniffResult) -> None:
    """原子写（tmp → os.replace）。写失败仅 WARNING，不影响路由结果。"""

    path = cache_path_for(cache_root, sha256)
    tmp_path = path.parent / f".{sha256}.{os.getpid()}.tmp"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps(result.cache_payload(sha256), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
    except Exception as exc:  # noqa: BLE001 - 缓存是派生物，失败降级
        logger.warning("Failed to write pdf sniff cache %s: %s", path, exc)
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:  # pragma: no cover - 清理失败无影响
            pass


def resolve_pdf_sniff(
    original_bin: Path,
    sha256: str,
    *,
    config: PdfSniffConfig,
    cache_root: Path | None,
    use_cache: bool = True,
    write_cache: bool = True,
) -> PdfSniffResult:
    """router 与 007 共用的嗅探管线：查缓存 → miss 则嗅探 → 写缓存。"""

    cache_enabled = bool(config.cache_enabled) and cache_root is not None
    if use_cache and cache_enabled:
        cached = load_sniff_cache(cache_root, sha256, config.sniffer_version)  # type: ignore[arg-type]
        if cached is not None:
            return cached

    result = sniff_pdf(original_bin, config=config)

    # MUST-FIX：仅内容派生态入缓存；环境态 / 瞬时态不落盘（避免把「pypdf 未装」
    # 或一次 IO 失败固化成不可自愈的权威判定）。
    if write_cache and cache_enabled:
        if result.status in CACHEABLE_SNIFF_STATUSES:
            save_sniff_cache(cache_root, sha256, result)  # type: ignore[arg-type]
        else:
            logger.debug(
                "Skipping pdf sniff cache write for non-content status %s: %s",
                result.status.value,
                sha256,
            )
    return result
