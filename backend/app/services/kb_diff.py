"""P1-L1 · T-D2MD-01：按库扫描的增量 diff（正式实现）。

对应 docs/v2-tasks/loops/P1-L1-PLAN.md §二 与 02-CONTRACTS.md §4.1/§4.3/§5：
- 身份 = 文件字节 sha256（复用 ``app.core.ids.compute_sha256``）；
- 复合键 = (file_id, sha256)，file_id 取相对扫描根的受控 posix 路径
  （与 source_path 分离，不允许任意主机路径）；
- ``set_digest = sha256(sorted ACTIVE (file_id, sha256, read_set_digest))``，
  只覆盖 ``status=ACTIVE`` 条目；
- ``cursor = "cursor-" + set_digest``：内容寻址，ACTIVE 集合变化即推进，
  无变化/重放时保持不变（A1「单调推进」与 A2「重放相同」同时成立）；
- 删除写 TOMBSTONE + revoked_at + reason=SOURCE_REMOVED，不物理删条目；
- 清单损坏/结构不符抛 ``KB_MANIFEST_CORRUPT``，禁止静默按空库重建；
- 判定优先级：FULL_REBUILD_REQUESTED > removed > added/changed
  > parser_fingerprint 变化 > NO_CHANGE。

【范围声明 / 判断，非事实，供独立 QA 复核】
1) 本 loop 无产物四件套，``read_set_digest`` 退化为「源文件读取集」的
   规范序 sha256（对齐 ``parse_artifact/store.read_set_digest`` 的
   ``(role, relative_path, sha256, size_bytes)`` 口径）。Loop 3 接入四件套后
   应由 ``store.read_set_digest`` 取代，届时本函数口径须显式对齐。
2) 扫描枚举所有普通文件（不按扩展名过滤），跳过隐藏项、清单文件自身，
   以及「含独立 kb-manifest.json 的子目录」（独立 KB 根隔离）。
3) ``parser_fingerprint`` 由调用方传入；CLI 默认沿用上轮清单登记值。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.core.ids import compute_sha256
from app.core.parsed_paths import KB_MANIFEST_FILENAME

SCHEMA_KB_MANIFEST = "llmke.doc2md.kb-manifest/1"
SCHEMA_KB_DIFF = "llmke.kb-diff/1"
DEFAULT_PARSER_FINGERPRINT = "fp-unset"
STATUS_ACTIVE = "ACTIVE"
STATUS_TOMBSTONE = "TOMBSTONE"


class KBManifestCorruptError(ValueError):
    """清单损坏/版本不符；对齐 KB_STATE_CORRUPT 口径，禁止按空库重建。"""


@dataclass(frozen=True)
class DiffResult:
    """02-CONTRACTS.md §4.3 ``llmke.kb-diff/1``。"""

    schema: str = SCHEMA_KB_DIFF
    kb: str = ""
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    cursor: str = ""
    set_digest: str = ""
    reason: str = ""
    # 内部明细（本轮 ACTIVE 条目），供 build_next_manifest 使用；不进入 stdout JSON。
    active_entries: list[dict] = field(
        default_factory=list, repr=False, compare=False
    )

    def to_dict(self) -> dict:
        """stdout 契约字段：确定性（无时间戳/随机值），保证两次 dry-run 逐字节相同。"""
        return {
            "schema": self.schema,
            "kb": self.kb,
            "added": list(self.added),
            "changed": list(self.changed),
            "removed": list(self.removed),
            "unchanged": list(self.unchanged),
            "cursor": self.cursor,
            "set_digest": self.set_digest,
            "reason": self.reason,
        }


def _corrupt(source: Path | str, message: str) -> KBManifestCorruptError:
    return KBManifestCorruptError(f"KB_MANIFEST_CORRUPT: {message} (at {source})")


def _validate_manifest(data: object, source: Path | str) -> None:
    if not isinstance(data, dict):
        raise _corrupt(source, "manifest root is not a JSON object")
    if data.get("schema") != SCHEMA_KB_MANIFEST:
        raise _corrupt(source, "schema mismatch (expected llmke.doc2md.kb-manifest/1)")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise _corrupt(source, "entries missing or not a list")
    seen_active: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise _corrupt(source, f"entries[{index}] is not an object")
        file_id = entry.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            raise _corrupt(source, f"entries[{index}].file_id invalid")
        status = entry.get("status")
        if status not in (STATUS_ACTIVE, STATUS_TOMBSTONE):
            raise _corrupt(source, f"entries[{index}].status invalid")
        if status == STATUS_ACTIVE:
            sha = entry.get("sha256")
            if not isinstance(sha, str) or len(sha) != 64:
                raise _corrupt(source, f"entries[{index}].sha256 invalid")
            if file_id in seen_active:
                raise _corrupt(source, f"duplicate ACTIVE file_id {file_id!r}")
            seen_active.add(file_id)


def load_kb_manifest(path: Path) -> dict | None:
    """读取上轮清单。

    - 文件不存在 -> ``None``（合法空库，首次扫描）；
    - 文件存在但不可解析/结构不符 -> ``KBManifestCorruptError``
      （绝不静默按空库重建，R-5）。
    """
    p = Path(path)
    if not p.exists():
        return None
    if not p.is_file():
        raise _corrupt(p, "manifest path is not a regular file")
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _corrupt(p, f"unreadable manifest: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _corrupt(p, f"invalid JSON: {exc}") from exc
    _validate_manifest(data, p)
    return data


def _is_under(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _collect_source_files(scan_root: Path) -> list[Path]:
    """枚举本库源文件（确定性排序）。

    跳过：隐藏项、``kb-manifest.json`` 自身、以及任何「含独立 kb-manifest.json
    的子目录」（独立 KB 根隔离，允许一个夹具目录下并存多个互不干扰的语料）。
    """
    root = Path(scan_root)
    if not root.is_dir():
        raise FileNotFoundError(f"Scan path is not a directory: {root}")
    nested_roots = {
        manifest.parent
        for manifest in root.rglob(KB_MANIFEST_FILENAME)
        if manifest.parent != root
    }
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if path.name == KB_MANIFEST_FILENAME:
            continue
        if any(_is_under(path, nested) for nested in nested_roots):
            continue
        files.append(path)
    return sorted(files)


def _read_set_digest(file_id: str, sha256: str, size_bytes: int) -> str:
    """源文件读取集的规范序 sha256（见模块 docstring 范围声明 1）。"""
    canonical = json.dumps(
        [{"file_id": file_id, "sha256": sha256, "size_bytes": size_bytes}],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_source_entry(root: Path, path: Path) -> dict:
    relative = path.relative_to(root).as_posix()
    sha = compute_sha256(path)
    size = path.stat().st_size
    return {
        "file_id": relative,
        "sha256": sha,
        "source_path": relative,
        "read_set_digest": _read_set_digest(relative, sha, size),
        "size_bytes": size,
        "status": STATUS_ACTIVE,
        "route_type": "UNKNOWN",
        "parser_adapter_version": "kb_diff_v1",
    }


def compute_set_digest(triples: list[tuple[str, str, str]]) -> str:
    """``sha256(sorted ACTIVE (file_id, sha256, read_set_digest))``，与顺序无关。"""
    normalized = sorted((str(a), str(b), str(c)) for a, b, c in triples)
    canonical = json.dumps(
        [list(item) for item in normalized],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_kb_diff(
    *,
    kb: str,
    scan_path: Path,
    previous_manifest: dict | None,
    parser_fingerprint: str,
    full_rebuild: bool = False,
) -> DiffResult:
    """计算本轮 DiffResult（纯函数：不写任何文件、不依赖网络/时间）。"""
    root = Path(scan_path)
    disk_entries = [
        _build_source_entry(root, path) for path in _collect_source_files(root)
    ]
    disk_by_id = {entry["file_id"]: entry for entry in disk_entries}
    disk_ids = set(disk_by_id)

    if previous_manifest is None:
        active_prev: dict[str, dict] = {}
        previous_fp: str | None = None
    else:
        _validate_manifest(previous_manifest, "<in-memory>")
        active_prev = {
            entry["file_id"]: entry
            for entry in previous_manifest["entries"]
            if entry["status"] == STATUS_ACTIVE
        }
        previous_fp = previous_manifest.get("parser_fingerprint")

    active_ids = set(active_prev)
    added = sorted(disk_ids - active_ids)
    removed = sorted(active_ids - disk_ids)
    common = active_ids & disk_ids
    changed_content = sorted(
        fid
        for fid in common
        if disk_by_id[fid]["sha256"] != active_prev[fid]["sha256"]
    )
    unchanged = sorted(common - set(changed_content))
    fp_changed = previous_fp is not None and previous_fp != parser_fingerprint

    # 判定优先级：FULL_REBUILD > removed > added/changed > fingerprint > NO_CHANGE
    if full_rebuild:
        reason = "FULL_REBUILD_REQUESTED"
        changed = sorted(disk_ids)
        unchanged = []
    elif removed:
        reason = "FILE_SET_CHANGED"
        changed = changed_content
    elif added:
        reason = "FILE_SET_CHANGED"
        changed = changed_content
    elif changed_content:
        reason = "CONTENT_CHANGED"
        changed = changed_content
    elif fp_changed:
        reason = "PARSER_FINGERPRINT_CHANGED"
        changed = sorted(active_ids)  # 所有 ACTIVE 都标 changed（A3 / R-3）
        unchanged = []
    else:
        reason = "NO_CHANGE"
        changed = []

    # 本轮 ACTIVE = 磁盘上全部条目；removed 转 TOMBSTONE，不计入 ACTIVE
    active_entries = [dict(disk_by_id[fid]) for fid in sorted(disk_ids)]
    set_digest = compute_set_digest(
        [
            (e["file_id"], e["sha256"], e["read_set_digest"])
            for e in active_entries
        ]
    )
    cursor = "cursor-" + set_digest

    return DiffResult(
        schema=SCHEMA_KB_DIFF,
        kb=kb,
        added=added,
        changed=changed,
        removed=removed,
        unchanged=unchanged,
        cursor=cursor,
        set_digest=set_digest,
        reason=reason,
        active_entries=active_entries,
    )


def build_next_manifest(
    result: DiffResult,
    previous_manifest: dict | None,
    *,
    parser_fingerprint: str,
    generated_at: str,
) -> dict:
    """由本轮 DiffResult 组装下一版 kb-manifest.json（纯函数，不写文件）。

    - 本轮 ACTIVE 整表登记；
    - 本轮 removed 的上轮 ACTIVE 条目转 TOMBSTONE（revoked_at/reason）；
    - 既有 TOMBSTONE 保留（不物理删），且不重复生成；
    - 重放（无增删）时 entries/cursor/set_digest 均稳定。
    """
    prev_active: dict[str, dict] = {}
    prev_tombstones: list[dict] = []
    if previous_manifest:
        for entry in previous_manifest.get("entries", []):
            if entry.get("status") == STATUS_ACTIVE:
                prev_active[entry["file_id"]] = dict(entry)
            elif entry.get("status") == STATUS_TOMBSTONE:
                prev_tombstones.append(dict(entry))

    active_entries = [dict(entry) for entry in result.active_entries]
    active_ids = {entry["file_id"] for entry in active_entries}

    tombstones = [
        dict(entry)
        for entry in prev_tombstones
        if entry["file_id"] not in active_ids
    ]
    for file_id in result.removed:
        old = prev_active.get(file_id)
        tombstones.append(
            {
                "file_id": file_id,
                "sha256": old["sha256"] if old else "",
                "status": STATUS_TOMBSTONE,
                "revoked_at": generated_at,
                "reason": "SOURCE_REMOVED",
            }
        )

    entries = active_entries + tombstones
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for entry in sorted(
        entries, key=lambda e: (e["file_id"], e.get("status", ""))
    ):
        key = (entry["file_id"], entry.get("status", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)

    return {
        "schema": SCHEMA_KB_MANIFEST,
        "kb": result.kb,
        "cursor": result.cursor,
        "set_digest": result.set_digest,
        "generated_at": generated_at,
        "parser_fingerprint": parser_fingerprint,
        "entries": deduped,
    }


def write_kb_manifest(path: Path, manifest: dict) -> None:
    """整表写回（原子替换，临时文件名带 uuid；dry-run 不会调用本函数）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = p.with_name(f"{p.name}.tmp-{uuid.uuid4().hex[:8]}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
