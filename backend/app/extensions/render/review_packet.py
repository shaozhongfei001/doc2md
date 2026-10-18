"""Readable review packet for one table (SDD 9.3 / T504, ``PI-F04-08``).

Contract: "生成独立可读 rendered/review packet 及上下文，不改旧 parsed_text".

So this module **returns** text; it never writes a file. Persisting the packet is
the caller's job (``io.paths.assert_writable`` + ``io.artifact_store``), which is
what keeps the legacy ``parsed/`` tree structurally out of reach: there is no
path argument here to misuse.

The packet is also where ``L03-GAP-06`` gets closed. That gap was: a ``Table``
could pass its own closed contract while the enclosing
``DocumentEnhancement`` rejected it, because ``Enhancement.links`` requires
``omission_evidence_ids ⊆ evidence[].candidate_evidence_id``
(``contracts.py:585``). A packet that cites an omission the document never
registered is exactly the false-green that produced the gap, so it is refused
here at build time instead of surfacing three loops later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError

PACKET_CODE = "E_REVIEW_PACKET_INVALID"

__all__ = ["PACKET_CODE", "ReviewPacket", "build_review_packet"]


def _error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError(PACKET_CODE, message, detail=detail or None)


def _escape(text: str) -> str:
    """Keep a cell's text inside one markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


@dataclass(frozen=True)
class ReviewPacket:
    """Rendered markdown plus the structured context it was rendered from."""

    markdown: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"markdown": self.markdown, "payload": dict(self.payload)}


def _cells(table: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    cells = table.get("cells")
    if isinstance(cells, (str, bytes)) or not isinstance(cells, Sequence):
        raise _error("table.cells must be a sequence of cells")
    out: list[Mapping[str, Any]] = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, Mapping):
            raise _error(f"table.cells[{index}] must be a mapping")
        cell_id = cell.get("cell_id")
        if not isinstance(cell_id, str) or not cell_id:
            raise _error(f"table.cells[{index}] has no usable cell_id")
        out.append(cell)
    return out


def _evidence_ids(candidates: Sequence[Any]) -> list[str]:
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise _error(
            f"evidence_candidates must be a sequence, got {type(candidates).__name__}"
        )
    ids: list[str] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            raise _error(f"evidence_candidates[{index}] must be a mapping")
        candidate_id = candidate.get("candidate_evidence_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise _error(
                f"evidence_candidates[{index}] has no candidate_evidence_id: "
                f"{candidate_id!r}"
            )
        if candidate_id in ids:
            raise _error(f"duplicate candidate_evidence_id {candidate_id!r}")
        ids.append(candidate_id)
    return ids


def build_review_packet(
    table: Mapping[str, Any],
    *,
    semantics: Sequence[Mapping[str, Any]] = (),
    notes: Sequence[Mapping[str, Any]] = (),
    evidence_candidates: Sequence[Mapping[str, Any]] = (),
    blocked: Sequence[Mapping[str, Any]] = (),
    context: Mapping[str, Any] | None = None,
) -> ReviewPacket:
    """Render a review packet for ``table``.

    ``evidence_candidates`` is the document's ``evidence`` list (or the subset
    that applies); every omission id and every note evidence id must appear in
    it, otherwise the packet is refused with :data:`PACKET_CODE`.
    """
    if not isinstance(table, Mapping):
        raise _error(f"table must be a mapping, got {type(table).__name__}")
    table_id = table.get("table_id")
    if not isinstance(table_id, str) or not table_id:
        raise _error(f"table has no usable table_id: {table_id!r}")

    cells = _cells(table)
    cell_by_id = {cell["cell_id"]: cell for cell in cells}
    known_evidence = _evidence_ids(evidence_candidates)

    ctx = dict(context or {})
    for key in ("caption", "period", "unit_raw", "currency"):
        if key in ctx and ctx[key] is not None and not isinstance(ctx[key], str):
            raise _error(f"context.{key} must be a string or null")

    # --- GAP-06: an omission must exist in the document's evidence ----------
    omissions = list(table.get("omission_evidence_ids") or ())
    missing_omissions = [oid for oid in omissions if oid not in known_evidence]
    if missing_omissions:
        raise _error(
            "table cites omission evidence the document never registered "
            f"({missing_omissions}); the root contract "
            "(omission_evidence_ids ⊆ evidence[].candidate_evidence_id) would reject "
            "the enrichment",
            table_id=table_id,
            missing=missing_omissions,
            known=known_evidence,
        )

    # --- header paths must resolve to real cells ---------------------------
    rows: list[dict[str, Any]] = []
    used_notes: set[str] = set()
    for index, item in enumerate(semantics):
        if not isinstance(item, Mapping):
            raise _error(f"semantics[{index}] must be a mapping")
        data_cell_id = item.get("data_cell_id")
        if data_cell_id not in cell_by_id:
            raise _error(
                f"semantics[{index}] points at {data_cell_id!r}, which is not a cell of "
                f"table {table_id}",
                table_id=table_id,
            )
        column_path = list(item.get("column_header_cell_ids") or ())
        row_path = list(item.get("row_header_cell_ids") or ())
        for axis, path in (("column", column_path), ("row", row_path)):
            for header_id in path:
                if header_id not in cell_by_id:
                    raise _error(
                        f"cell {data_cell_id} has a dangling {axis} header reference "
                        f"{header_id!r}",
                        table_id=table_id,
                    )
                if header_id == data_cell_id:
                    raise _error(
                        f"cell {data_cell_id} is its own {axis} header",
                        table_id=table_id,
                    )
        note_ids = list(item.get("note_ids") or ())
        for note_id in note_ids:
            used_notes.add(note_id)
        rows.append(
            {
                "data_cell_id": data_cell_id,
                "row": cell_by_id[data_cell_id].get("row"),
                "col": cell_by_id[data_cell_id].get("col"),
                "text": cell_by_id[data_cell_id].get("text"),
                "text_origin": cell_by_id[data_cell_id].get("text_origin"),
                "engine_confidence": cell_by_id[data_cell_id].get("engine_confidence"),
                "column_header_cell_ids": column_path,
                "column_header_text": [
                    str(cell_by_id[h].get("text") or "") for h in column_path
                ],
                "row_header_cell_ids": row_path,
                "row_header_text": [str(cell_by_id[h].get("text") or "") for h in row_path],
                "unit_raw": item.get("unit_raw"),
                "state": item.get("state"),
                "note_ids": note_ids,
            }
        )

    note_rows: list[dict[str, Any]] = []
    for index, note in enumerate(notes):
        if not isinstance(note, Mapping):
            raise _error(f"notes[{index}] must be a mapping")
        note_id = note.get("note_id")
        if not isinstance(note_id, str) or not note_id:
            raise _error(f"notes[{index}] has no usable note_id")
        unknown_evidence = [
            eid for eid in (note.get("evidence_ids") or ()) if eid not in known_evidence
        ]
        if unknown_evidence:
            raise _error(
                f"note {note_id} cites evidence the document never registered "
                f"({unknown_evidence})",
                table_id=table_id,
                missing=unknown_evidence,
            )
        note_rows.append(
            {
                "note_id": note_id,
                "text": note.get("text"),
                "scope": note.get("scope"),
                "target_ids": list(note.get("target_ids") or ()),
                "state": note.get("state"),
                "evidence_ids": list(note.get("evidence_ids") or ()),
            }
        )

    known_notes = {note["note_id"] for note in note_rows}
    dangling_notes = sorted(used_notes - known_notes)
    if dangling_notes:
        raise _error(
            "cell semantics reference notes the table does not own",
            table_id=table_id,
            missing=dangling_notes,
        )

    blocked_rows = []
    for index, item in enumerate(blocked):
        if not isinstance(item, Mapping):
            raise _error(f"blocked[{index}] must be a mapping")
        blocked_rows.append(
            {
                "code": str(item.get("code") or "UNSPECIFIED"),
                "message": str(item.get("message") or ""),
                "cell_id": item.get("cell_id"),
            }
        )

    payload = {
        "table_id": table_id,
        "caption": ctx.get("caption"),
        "period": ctx.get("period"),
        "unit_raw": ctx.get("unit_raw"),
        "currency": ctx.get("currency"),
        "structure_state": table.get("structure_state"),
        "n_rows": table.get("n_rows"),
        "n_cols": table.get("n_cols"),
        "rows": rows,
        "notes": note_rows,
        "blocked": blocked_rows,
        "evidence_ids": known_evidence,
        "omission_evidence_ids": omissions,
        "generated_by": "DOC2MD-19CAP/L05/T504",
    }

    return ReviewPacket(markdown=_render(payload), payload=payload)


def _render(payload: Mapping[str, Any]) -> str:
    lines: list[str] = []
    title = payload.get("caption") or payload["table_id"]
    lines.append(f"# 复核包：{title}")
    lines.append("")
    lines.append(f"- 表 ID：`{payload['table_id']}`")
    lines.append(f"- 表题：{payload.get('caption') or '（未声明）'}")
    lines.append(f"- 期间：{payload.get('period') or '（未声明）'}")
    lines.append(f"- 单位：{payload.get('unit_raw') or '`null`（无明确单位）'}")
    lines.append(f"- 币种：{payload.get('currency') or '（未声明）'}")
    lines.append(
        f"- 结构状态：{payload.get('structure_state')}（{payload.get('n_rows')}×"
        f"{payload.get('n_cols')}）"
    )
    lines.append("")

    rows = payload["rows"]
    lines.append(f"## 数据格（{len(rows)}）")
    lines.append("")
    if not rows:
        lines.append("（无 cell semantics；本表尚未给出表头路径）")
    else:
        lines.append("| 行 | 列 | 内容 | 列标题路径 | 行标题路径 | 来源 | 置信度 | 状态 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for item in rows:
            column_path = " / ".join(item["column_header_text"]) or "（空）"
            row_path = " / ".join(item["row_header_text"]) or "（空）"
            confidence = item["engine_confidence"]
            lines.append(
                "| {row} | {col} | {text} | {cpath} | {rpath} | {origin} | {conf} | {state} |".format(
                    row=item["row"],
                    col=item["col"],
                    text=_escape(str(item["text"] or "")),
                    cpath=_escape(column_path),
                    rpath=_escape(row_path),
                    origin=item["text_origin"],
                    conf="`null`" if confidence is None else confidence,
                    state=item["state"],
                )
            )
    lines.append("")

    blocked = payload["blocked"]
    lines.append(f"## 被阻断项（{len(blocked)}）")
    lines.append("")
    if not blocked:
        lines.append("（无）")
    else:
        for item in blocked:
            target = f"（{item['cell_id']}）" if item.get("cell_id") else ""
            lines.append(f"- `{item['code']}`{target}：{item['message']}")
    lines.append("")

    notes = payload["notes"]
    lines.append(f"## 脚注与表外条件（{len(notes)}）")
    lines.append("")
    if not notes:
        lines.append("（无）")
    else:
        for item in notes:
            targets = "、".join(item["target_ids"]) or "（无目标）"
            lines.append(
                f"- `{item['note_id']}` [{item['scope']}] → {targets}"
                f"（{item['state']}）：{item['text']}"
            )
    lines.append("")

    lines.append("## 证据")
    lines.append("")
    if payload["evidence_ids"]:
        for evidence_id in payload["evidence_ids"]:
            lines.append(f"- `{evidence_id}`")
    else:
        lines.append("（文档未登记候选证据）")
    if payload["omission_evidence_ids"]:
        lines.append("")
        lines.append("省略位证据：")
        for evidence_id in payload["omission_evidence_ids"]:
            lines.append(f"- `{evidence_id}`")
    lines.append("")
    lines.append(f"生成者：{payload['generated_by']}")
    return "\n".join(lines) + "\n"
