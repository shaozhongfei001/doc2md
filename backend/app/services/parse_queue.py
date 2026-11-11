"""P1-L2 · T-D2MD-02：解析队列构建与消费（正式实现）。

对应 `docs/v2-tasks/loops/P1-L2-PLAN.md` §二 B1/B2/B3 + R-1~R-7：

- 输入 = Loop 1 的 ``DiffResult``（``kb_diff.py`` 语义锁定，本模块只调用、不修改）；
- 队列成员 = ``added`` + ``changed``（``removed`` / TOMBSTONE 不入队，R-2）；
- 按 ``sha256`` 内容寻址去重（R-3），``file_ids`` 承载同文异源；
- 缺路由结果 → ``rejected`` 带 ``ROUTE_RESULT_MISSING``，不进队列（B3）；
- ``route_type=UNSUPPORTED`` → 保留在队列，``status=FAILED`` + ``fail_reason``（B2）；
- ``parser_adapter_version`` 与上轮 manifest 不一致 → 标 changed 重排队（R-5）；
- ``--dry-run`` 零写入、输出确定性（B1 / R-4）；
- 消费层：已完成项幂等跳过（复用 ``markitdown_parser._should_skip_idempotent``，R-6），
  未完成/失败项保留在队列可重试，``limit`` 只约束实际解析动作数。

**复用既有服务层、不重新实现解析**（PLAN §三）：
- ``DefaultRouteResolver`` 用 ``app.core.parser_routing.match_route_type``
  （既有路由规则纯函数，未重写路由表）；
- ``DefaultParseRunner`` 调度既有 ``MarkItDownParserService.parse_markitdown`` /
  ``MineruPdfParserService.parse_many``（按 route_type 分发，未写第二套解析器）；
- ``DefaultIdempotencyChecker`` 调用既有 ``MarkItDownParserService._should_skip_idempotent``；
- ``copy-to-vault`` / ``route-parsers`` 的产物是本队列的上游输入，
  由既有命令承担；L3 的 ``_reset_vault_state`` / ``--force`` / ``generated_at``
  按 PLAN §一 禁止触碰，本 loop 不改。

【判断 / 范围声明，供独立 QA 复核】
1) ``build-parse-queue`` 默认语料根为 ``DEFAULT_KB_CORPUS_ROOT``
   （= L1 夹具 ``backend/tests/fixtures/kb_diff_corpus``），因为 PLAN §四 的验收命令
   不传 ``--path`` 且要检查该目录 ``kb-manifest.json`` 的 sha256 不变；生产接入时
   由 ``--path`` / ``--cursor`` 注入，不需要改本模块语义。
2) ``DefaultRouteResolver`` 对所有 ACTIVE 条目都给出 ``route_type``（含 ``UNKNOWN``），
   「缺路由结果」由 ``--routes`` 注入的映射中缺失该 sha256 表达（B3）。
3) ``QUEUE_PARSER_ADAPTER_VERSION = "kb_diff_v1"`` 对齐 L1 清单登记值
   （TL 裁定 §八(2)：L3 再切 ``store.read_set_digest`` / 真实适配器版本口径）。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Protocol

from app.core.config import PROJECT_ROOT, AppConfig
from app.core.parsed_paths import (
    build_parsed_artifact_paths,
    build_parsed_content_dir,
)
from app.core.parser_routing import ext_from_path, match_route_type
from app.services.kb_diff import DiffResult

QUEUE_SCHEMA = "llmke.doc2md.parse-queue/1"
QUEUE_FILENAME = "parse_queue.json"
QUEUE_PARSER_ADAPTER_VERSION = "kb_diff_v1"
DEFAULT_KB_CORPUS_ROOT = (
    PROJECT_ROOT / "backend" / "tests" / "fixtures" / "kb_diff_corpus"
)

STATUS_PENDING = "PENDING"
STATUS_FAILED = "FAILED"
STATUS_DONE = "DONE"
STATUS_SKIPPED = "SKIPPED"
REASON_ROUTE_MISSING = "ROUTE_RESULT_MISSING"
REASON_ROUTE_UNSUPPORTED = "ROUTE_UNSUPPORTED"
REASON_PARSE_FAILED = "PARSE_FAILED"
DIFF_PARSER_VERSION_CHANGED = "PARSER_VERSION_CHANGED"

MINERU_ROUTE_TYPES = frozenset(
    {"PDF_DIGITAL", "PDF_SCANNED_OR_IMAGE", "IMAGE"}
)


@dataclass(frozen=True)
class RouteInfo:
    route_type: str
    parser_adapter_version: str


@dataclass(frozen=True)
class QueueItem:
    sha256: str
    file_ids: list[str]
    source_path: str
    route_type: str
    parser_adapter_version: str
    diff_reason: str
    status: str
    fail_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "sha256": self.sha256,
            "file_ids": list(self.file_ids),
            "source_path": self.source_path,
            "route_type": self.route_type,
            "parser_adapter_version": self.parser_adapter_version,
            "diff_reason": self.diff_reason,
            "status": self.status,
            "fail_reason": self.fail_reason,
        }


@dataclass
class BuildQueueResult:
    kb: str
    items: list[QueueItem] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    diff_reason: str = ""
    cursor: str = ""
    set_digest: str = ""
    dry_run: bool = False

    def to_dict(self) -> dict:
        """stdout 契约：确定性（无时间戳/随机值），保证两次 dry-run 逐字节相同。"""
        return {
            "schema": QUEUE_SCHEMA,
            "kb": self.kb,
            "dry_run": self.dry_run,
            "diff_reason": self.diff_reason,
            "cursor": self.cursor,
            "set_digest": self.set_digest,
            "items": [item.to_dict() for item in self.items],
            "rejected": list(self.rejected),
        }


@dataclass(frozen=True)
class ParseOutcome:
    ok: bool
    message: str = ""


@dataclass
class ConsumeQueueResult:
    queue: BuildQueueResult
    parsed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {
            "schema": QUEUE_SCHEMA,
            "kb": self.queue.kb,
            "dry_run": self.dry_run,
            "summary": {
                "parsed": len(self.parsed),
                "skipped": len(self.skipped),
                "failed": len(self.failed),
                "planned": len(self.planned),
            },
            "parsed": list(self.parsed),
            "skipped": list(self.skipped),
            "failed": list(self.failed),
            "planned": list(self.planned),
            "items": [item.to_dict() for item in self.queue.items],
        }


class ParseRunner(Protocol):
    def parse(self, item: QueueItem) -> ParseOutcome: ...


class DefaultRouteResolver:
    """复用既有路由规则 ``app.core.parser_routing.match_route_type``。"""

    def __init__(
        self, parser_adapter_version: str = QUEUE_PARSER_ADAPTER_VERSION
    ) -> None:
        self.parser_adapter_version = parser_adapter_version

    def resolve_all(self, *, diff: DiffResult) -> dict[str, RouteInfo | None]:
        routes: dict[str, RouteInfo | None] = {}
        for entry in diff.active_entries:
            ext = ext_from_path(entry["source_path"])
            route_type, _decision, _rule, _hint, _reason = match_route_type(
                file_ext=ext, mime_type=None
            )
            routes[entry["sha256"]] = RouteInfo(
                route_type=route_type.value,
                parser_adapter_version=self.parser_adapter_version,
            )
        return routes


class DefaultParseRunner:
    """调度既有 parse-* 服务层（不重新实现解析）。"""

    def __init__(self, app_config: AppConfig) -> None:
        self.config = app_config

    def parse(self, item: QueueItem) -> ParseOutcome:
        try:
            if item.route_type in MINERU_ROUTE_TYPES:
                from app.services.mineru_pdf_parser import MineruPdfParserService

                service = MineruPdfParserService(self.config)
                result = service.parse_many(sha256=item.sha256, dry_run=False)
                ok = result.failed_count == 0 and result.partial_count == 0
                return ParseOutcome(ok=ok, message=f"parsed={result.parsed_count}")
            from app.services.markitdown_parser import MarkItDownParserService

            service = MarkItDownParserService(self.config)
            result = service.parse_markitdown(sha256=item.sha256, dry_run=False)
            return ParseOutcome(
                ok=result.failed_count == 0,
                message=f"parsed={result.parsed_count}",
            )
        except Exception as exc:  # 失败项保留在队列，不静默吞掉（B2）
            return ParseOutcome(ok=False, message=str(exc))


class DefaultIdempotencyChecker:
    """复用既有 ``MarkItDownParserService._should_skip_idempotent``（PLAN R-6）。

    ``_should_skip_idempotent`` 只读取 ``parse_manifest.json``、不依赖实例状态，
    故用 ``object.__new__`` 绕过会创建 DB engine 的 ``__init__``（engine 是 lazy，
    但本切片不触碰 DB，B1 要求 DB 无新增记录）。
    """

    def __init__(self, app_config: AppConfig) -> None:
        self.config = app_config

    def __call__(self, sha256: str) -> bool:
        from app.services.markitdown_parser import MarkItDownParserService

        parsed_dir = build_parsed_content_dir(self.config.storage.parsed_root, sha256)
        paths = build_parsed_artifact_paths(parsed_dir)
        service = object.__new__(MarkItDownParserService)
        return bool(service._should_skip_idempotent(paths["parse_manifest"]))


def load_route_map(path: Path) -> dict[str, RouteInfo | None]:
    """读取注入的路由结果映射；``null`` / 缺失 key 表示「该 sha256 无路由结果」（B3）。"""
    p = Path(path)
    if not p.is_file():
        return {}
    payload = json.loads(p.read_text(encoding="utf-8"))
    routes: dict[str, RouteInfo | None] = {}
    for sha, value in payload.items():
        if value is None:
            routes[sha] = None
        elif isinstance(value, dict):
            routes[sha] = RouteInfo(
                route_type=str(value.get("route_type", "UNKNOWN")),
                parser_adapter_version=str(
                    value.get("parser_adapter_version", QUEUE_PARSER_ADAPTER_VERSION)
                ),
            )
    return routes


def build_parse_queue(
    *,
    kb: str,
    diff: DiffResult,
    previous_manifest: dict | None,
    routes: dict[str, RouteInfo | None],
    parser_adapter_version: str = QUEUE_PARSER_ADAPTER_VERSION,
    dry_run: bool = False,
) -> BuildQueueResult:
    """由 L1 DiffResult 构建队列（纯函数：不写文件、不连 DB、不调用解析器）。"""
    active_by_fid = {entry["file_id"]: entry for entry in diff.active_entries}
    already_queued = set(diff.added) | set(diff.changed)

    # 按 sha256 聚合候选（R-3 去重）；file_ids 承载同文异源。
    candidates: dict[str, dict] = {}

    def add(file_id: str, reason: str) -> None:
        entry = active_by_fid.get(file_id)
        if entry is None:
            return  # 已从磁盘删除：不入队、不复活（R-2）
        sha = entry["sha256"]
        slot = candidates.setdefault(
            sha,
            {"file_ids": [], "source_path": entry["source_path"], "reasons": []},
        )
        if file_id not in slot["file_ids"]:
            slot["file_ids"].append(file_id)
        if reason not in slot["reasons"]:
            slot["reasons"].append(reason)

    for file_id in sorted(diff.added):
        add(file_id, "added")
    for file_id in sorted(diff.changed):
        add(file_id, "changed")

    # R-5：上轮 ACTIVE 条目的适配器版本与当前不一致 → 标 changed 重排队。
    if previous_manifest:
        for entry in previous_manifest.get("entries", []):
            if entry.get("status") != "ACTIVE":
                continue
            file_id = entry["file_id"]
            if file_id in already_queued:
                continue
            if entry.get("parser_adapter_version") != parser_adapter_version:
                add(file_id, DIFF_PARSER_VERSION_CHANGED)

    items: list[QueueItem] = []
    rejected: list[dict] = []
    for sha in sorted(candidates):
        slot = candidates[sha]
        file_ids = sorted(slot["file_ids"])
        info = routes.get(sha)
        if info is None:
            rejected.append(
                {
                    "sha256": sha,
                    "file_ids": file_ids,
                    "reason": REASON_ROUTE_MISSING,
                }
            )
            continue
        reasons = slot["reasons"]
        diff_reason = reasons[0]
        for preferred in ("added", "changed"):
            if preferred in reasons:
                diff_reason = preferred
                break
        unsupported = info.route_type == "UNSUPPORTED"
        items.append(
            QueueItem(
                sha256=sha,
                file_ids=file_ids,
                source_path=slot["source_path"],
                route_type=info.route_type,
                parser_adapter_version=info.parser_adapter_version,
                diff_reason=diff_reason,
                status=STATUS_FAILED if unsupported else STATUS_PENDING,
                fail_reason=REASON_ROUTE_UNSUPPORTED if unsupported else None,
            )
        )

    return BuildQueueResult(
        kb=kb,
        items=items,
        rejected=rejected,
        diff_reason=diff.reason,
        cursor=diff.cursor,
        set_digest=diff.set_digest,
        dry_run=dry_run,
    )


def consume_queue(
    *,
    queue: BuildQueueResult,
    parse_runner: ParseRunner,
    skip_checker: Callable[[str], bool] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> ConsumeQueueResult:
    """消费队列：失败项保留可重试、已完成项幂等跳过、未完成项保留（R-6/B2）。"""
    checker = skip_checker or (lambda _sha: False)
    parsed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    planned: list[str] = []
    used = 0
    updated: list[QueueItem] = []

    for item in queue.items:
        if dry_run:
            planned.append(item.sha256)
            updated.append(item)
            continue
        if item.status in (STATUS_DONE, STATUS_SKIPPED):
            skipped.append(item.sha256)  # 已完成/已跳过：幂等，不再解析
            updated.append(item)
            continue
        if limit is not None and used >= limit:
            updated.append(item)  # 未完成项保留在队列（R-6）
            continue
        if item.status == STATUS_PENDING and checker(item.sha256):
            updated.append(replace(item, status=STATUS_SKIPPED))
            skipped.append(item.sha256)
            continue
        # PENDING 或 FAILED（重试）都会尝试解析，失败项不被静默丢弃（B2）
        used += 1
        outcome = parse_runner.parse(item)
        if outcome.ok:
            updated.append(replace(item, status=STATUS_DONE, fail_reason=None))
            parsed.append(item.sha256)
        else:
            updated.append(
                replace(
                    item,
                    status=STATUS_FAILED,
                    fail_reason=item.fail_reason or REASON_PARSE_FAILED,
                )
            )
            failed.append(item.sha256)

    queue.items = updated
    return ConsumeQueueResult(
        queue=queue,
        parsed=parsed,
        skipped=skipped,
        failed=failed,
        planned=planned,
        dry_run=dry_run,
    )


def load_parse_queue(path: Path) -> BuildQueueResult:
    """读取队列文件；不存在 = 空队列（R-1 的 parse 退出码 0 语义）。"""
    p = Path(path)
    if not p.is_file():
        return BuildQueueResult(kb="", dry_run=False)
    data = json.loads(p.read_text(encoding="utf-8"))
    items = [
        QueueItem(
            sha256=str(item["sha256"]),
            file_ids=list(item.get("file_ids", [])),
            source_path=str(item.get("source_path", "")),
            route_type=str(item.get("route_type", "UNKNOWN")),
            parser_adapter_version=str(
                item.get("parser_adapter_version", QUEUE_PARSER_ADAPTER_VERSION)
            ),
            diff_reason=str(item.get("diff_reason", "")),
            status=str(item.get("status", STATUS_PENDING)),
            fail_reason=item.get("fail_reason"),
        )
        for item in data.get("items", [])
    ]
    return BuildQueueResult(
        kb=str(data.get("kb", "")),
        items=items,
        rejected=list(data.get("rejected", [])),
        diff_reason=str(data.get("diff_reason", "")),
        cursor=str(data.get("cursor", "")),
        set_digest=str(data.get("set_digest", "")),
        dry_run=bool(data.get("dry_run", False)),
    )


def write_parse_queue(path: Path, result: BuildQueueResult) -> None:
    """整表写回（原子替换，临时文件名带 uuid；dry-run 不会调用本函数）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
    tmp = p.with_name(f"{p.name}.tmp-{uuid.uuid4().hex[:8]}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
