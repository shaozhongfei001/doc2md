"""T402 / ``PI-F04-02`` (read side) + ``PI-F04-03`` (coordinates): read MinerU again.

SDD 8.1:

    "先读既有MinerU content_list/middle；结构、页数和来源摘要均完整时直接适配，避免重复OCR。"

SDD 8.2:

    "MinerU middle与content_list匹配按页、type、asset hash、可核文本指纹和引擎局部ID
     分层执行；唯一匹配才绑定。相同caption/同页多个同型块有歧义时不按数组序号硬绑，
     留mapping issue。"

What this module does, and deliberately does not do
--------------------------------------------------
* It **reads** an existing engine result (``content_list.json`` + ``middle.json``) and
  pairs the flat list with the structured blocks. It never calls an engine, never
  touches the network, and never writes ``parsed/``.
* It binds a ``content_list`` entry to a ``middle`` block **only** when the layered
  comparison leaves exactly one candidate. Otherwise the entry keeps ``bbox = null``
  and carries a :class:`MappingIssue` naming the layers that were actually compared
  and the candidate set that survived them. Ambiguity is data, not something to
  break by picking the nearest array slot.
* It **never** uses a list position as an alignment key. The dangerous shortcut this
  file is written against is ``zip(content_list, pdf_info[page].blocks)``: it passes
  on every well-formed sample and silently mis-attributes captions and coordinates
  as soon as one block is dropped upstream. The only positional value kept here is
  the internal bookkeeping key used to detect that one block was bound twice
  (:data:`_Block.position`); it is never compared with a ``content_list`` index.
* Coordinates are **passed through**, with the unit labelled. No matrix maths lives
  here: when a page geometry is supplied the four corners are handed to
  :func:`app.extensions.geometry.transform.map_or_page_only`, which owns the inverse
  transform, the epsilon and the ``PAGE_ONLY`` downgrade.
* Nothing is invented. A missing bbox, caption or confidence stays ``None``; the
  engine's own ``score`` is never copied into a confidence field, because MinerU's
  span score is a recognition score and not the same claim.

Structural damage (a missing ``type``, a non-numeric ``bbox``, a ``pdf_info`` that is
not a list) raises ``E_ENGINE_SCHEMA_DRIFT`` for the whole document rather than
degrading into an empty table -- "不静默兜成空表". Pairing trouble is *not* damage and
therefore shows up as ``mapping_issue``, not as an exception.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .. import ExtensionError
from ..geometry.transform import PageGeometry, map_or_page_only

#: Issue codes. These are payload labels, not ``E_*`` contract codes: an unmapped
#: entry is a review outcome inside a successful read, so it must not pick a CLI
#: exit code. (Only ``E_ENGINE_SCHEMA_DRIFT`` / ``E_INPUT_INVALID`` are raised.)
ISSUE_AMBIGUOUS = "AMBIGUOUS_MATCH"
ISSUE_NO_CANDIDATE = "NO_CANDIDATE"
ISSUE_MISSING_ASSET = "MISSING_ASSET_HASH"
ISSUE_MISSING_TEXT = "MISSING_TEXT_FINGERPRINT"
ISSUE_PAGE_COUNT = "PAGE_COUNT_MISMATCH"
ISSUE_BBOX = "BBOX_DISAGREEMENT"
ISSUE_ALREADY_BOUND = "BLOCK_ALREADY_BOUND"

#: Layer ids, reported in order so a reviewer can see how far the comparison got.
LAYER_PAGE = "page"
LAYER_TYPE = "type"
LAYER_ASSET = "asset_hash"
LAYER_TEXT = "text_fingerprint"
LAYER_ENGINE_ID = "engine_id"

#: ``content_list`` type → ``middle`` block types it may be derived from.
#:
#: The aliasing is not a guess: in this repository's real MinerU artifact
#: (``docs/demo/pdf-rich-to-text/ir_source``) the same page yields
#: ``preproc_blocks = [title, text, table, image]`` but
#: ``para_blocks = [title, list, table, image]``, while ``content_list`` reports
#: ``[text, text, table, image]``. So a ``content_list`` "text" may come from a
#: middle "title" or "list". Keeping the exact-match-only rule would make every
#: paragraph of a real document unmappable; widening it to "any block" would make
#: the layer meaningless. The families below are the observed derivations.
TYPE_FAMILIES: dict[str, frozenset[str]] = {
    "text": frozenset({"text", "title", "list", "paragraph"}),
    "title": frozenset({"title", "text"}),
    "list": frozenset({"list", "text", "paragraph"}),
    "paragraph": frozenset({"paragraph", "text", "list"}),
    "table": frozenset({"table"}),
    "image": frozenset({"image"}),
    "equation": frozenset({"interline_equation", "equation"}),
    "interline_equation": frozenset({"interline_equation", "equation"}),
}

#: Types whose identity is carried by the asset file, not by readable text.
ASSET_TYPES = frozenset({"table", "image"})

#: ``content_list`` fields holding the caption / footnote, in fingerprint order.
ENTRY_CAPTION_KEYS: dict[str, tuple[str, str]] = {
    "table": ("table_caption", "table_footnote"),
    "image": ("img_caption", "img_footnote"),
}

#: ``middle`` child-block types holding the same caption / footnote.
MIDDLE_CAPTION_KINDS: dict[str, tuple[str, str]] = {
    "table": ("table_caption", "table_footnote"),
    "image": ("image_caption", "image_footnote"),
}

#: Tolerated disagreement between a ``content_list`` bbox and the bound middle bbox.
#: Both are supposed to be the same page-space rectangle; 1.0 is the same epsilon
#: ``geometry.transform`` starts from, and is wide enough for integer rounding.
BBOX_TOLERANCE = 1.0

__all__ = [
    "ASSET_TYPES",
    "BBOX_TOLERANCE",
    "ENTRY_CAPTION_KEYS",
    "ISSUE_ALREADY_BOUND",
    "ISSUE_AMBIGUOUS",
    "ISSUE_BBOX",
    "ISSUE_MISSING_ASSET",
    "ISSUE_MISSING_TEXT",
    "ISSUE_NO_CANDIDATE",
    "ISSUE_PAGE_COUNT",
    "LAYER_ASSET",
    "LAYER_ENGINE_ID",
    "LAYER_PAGE",
    "LAYER_TEXT",
    "LAYER_TYPE",
    "LayoutEntry",
    "MappingIssue",
    "MineruReadResult",
    "TYPE_FAMILIES",
    "fingerprint",
    "load_content_list",
    "load_middle",
    "read_mineru",
]


# --------------------------------------------------------------------- errors
def _schema_drift(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_ENGINE_SCHEMA_DRIFT", message, detail=detail or None)


def _input_invalid(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


# ------------------------------------------------------------------ normalize
def fingerprint(text: str) -> str:
    """The "可核文本指纹": NFKC, then every whitespace character removed.

    SDD says *comparable* text, and the only difference that is safe to erase is
    layout whitespace: MinerU reflows the same sentence into different span and line
    splits between ``middle`` and ``content_list`` (and emits full-width punctuation
    in one place and half-width in the other), so a byte comparison rejects a match
    that is visibly the same sentence.

    What is **not** erased, on purpose: case, digits, punctuation shape after NFKC,
    and negative signs -- the fields this project must not corrupt. Whitespace
    removal can in principle collapse "12 480" onto "12480"; that is accepted
    because the fingerprint is only ever used to *narrow* an already
    page-and-type-filtered candidate set, and a collision leaves two candidates,
    which is refused as ambiguous rather than guessed.
    """
    if not isinstance(text, str):
        raise _input_invalid(f"fingerprint input must be a string, got {type(text).__name__}")
    return "".join(unicodedata.normalize("NFKC", text).split())


def _lines_text(block: Mapping[str, Any]) -> str:
    """Concatenate the ``content`` of every span under ``lines`` (layout order)."""
    parts: list[str] = []

    def walk(node: Any, depth: int) -> None:
        if depth > 8 or not isinstance(node, (Mapping, list)):
            return
        if isinstance(node, Mapping):
            content = node.get("content")
            if isinstance(content, str):
                parts.append(content)
            for key, value in node.items():
                if key != "content":
                    walk(value, depth + 1)
            return
        for item in node:
            walk(item, depth + 1)

    walk(block.get("lines"), 0)
    return "".join(parts)


def _child_text(block: Mapping[str, Any], kinds: Sequence[str]) -> str:
    """Concatenate the text of the sub-blocks whose ``type`` is in ``kinds``."""
    children = block.get("blocks")
    if not _is_sequence(children):
        return ""
    parts: list[str] = []
    for child in children:
        if not isinstance(child, Mapping):
            continue
        if child.get("type") in kinds:
            parts.append(_lines_text(child))
    return "".join(parts)


def _asset_reference(block: Any, *, depth: int = 0) -> str | None:
    """First ``image_path`` / ``img_path`` found anywhere in the block."""
    if depth > 8:
        return None
    if isinstance(block, Mapping):
        for key in ("image_path", "img_path"):
            value = block.get(key)
            if isinstance(value, str) and value:
                return value
        for value in block.values():
            found = _asset_reference(value, depth=depth + 1)
            if found is not None:
                return found
        return None
    if isinstance(block, list):
        for item in block:
            found = _asset_reference(item, depth=depth + 1)
            if found is not None:
                return found
    return None


def _asset_key(path: Any) -> str | None:
    """Compare asset references by file name, case-folded; the directory is noise.

    ``content_list`` writes ``images/<hash>.jpg`` while ``middle`` writes
    ``<hash>.jpg`` for the same asset, so the stem alone is not comparable either --
    the extension stays in the key, and a rewritten extension is reported as a
    mismatch instead of being smoothed over.
    """
    if not isinstance(path, str) or not path.strip():
        return None
    return Path(path.strip()).name.lower() or None


def _engine_index(block: Mapping[str, Any]) -> int | None:
    """The engine's own local block id (``index``), kept as an int when integral."""
    value = block.get("index")
    if _is_number(value):
        number = float(value)
        if number.is_integer():
            return int(number)
    return None


def _bbox(value: Any, *, where: str) -> tuple[float, float, float, float]:
    if not _is_sequence(value) or len(value) != 4:
        raise _schema_drift(f"{where} must be a four-number bbox, got {value!r}", field=where)
    for item in value:
        if not _is_number(item):
            raise _schema_drift(f"{where} contains a non-numeric coordinate {item!r}", field=where)
    x0, y0, x1, y1 = (float(item) for item in value)
    if x1 < x0 or y1 < y0:
        raise _schema_drift(
            f"{where} is not a rectangle (x1 < x0 or y1 < y0): {value!r}", field=where
        )
    return (x0, y0, x1, y1)


# -------------------------------------------------------------------- payloads
@dataclass(frozen=True)
class MappingIssue:
    """Why a ``content_list`` entry was not bound. The candidate set is kept."""

    code: str
    message: str
    content_list_index: int | None = None
    page_idx: int | None = None
    layers: tuple[str, ...] = ()
    #: The engine's own local block ids that survived the layers ("?" when a block
    #: carries none). Array positions are never reported here, so a reader of the
    #: issue cannot mistake one for an alignment key.
    candidates: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "content_list_index": self.content_list_index,
            "page_idx": self.page_idx,
            "layers": list(self.layers),
            "candidates": list(self.candidates),
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class LayoutEntry:
    """One ``content_list`` entry, bound (coordinates present) or refused (nulls)."""

    content_list_index: int
    type: str
    page_idx: int
    page_no: int
    middle_index: int | None
    bbox: list[float] | None
    bbox_source: str | None
    content_list_bbox: list[float] | None
    unit: str | None
    evidence_class: str
    page_locator: Mapping[str, Any] | None
    page_only_reason: str | None
    trace: tuple[str, ...] = ()
    mapping_issue: MappingIssue | None = None

    @property
    def bound(self) -> bool:
        return self.mapping_issue is None and self.bbox is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_list_index": self.content_list_index,
            "type": self.type,
            "page_idx": self.page_idx,
            "page_no": self.page_no,
            "middle_index": self.middle_index,
            "bbox": self.bbox,
            "bbox_source": self.bbox_source,
            "content_list_bbox": self.content_list_bbox,
            "unit": self.unit,
            "evidence_class": self.evidence_class,
            "page_locator": None if self.page_locator is None else dict(self.page_locator),
            "page_only_reason": self.page_only_reason,
            "trace": list(self.trace),
            "mapping_issue": None if self.mapping_issue is None else self.mapping_issue.to_dict(),
        }


@dataclass(frozen=True)
class MineruReadResult:
    entries: list[LayoutEntry]
    issues: list[MappingIssue] = field(default_factory=list)
    #: Pages the engine's ``pdf_info`` actually contains.
    middle_page_count: int = 0
    #: ``max(page_idx) + 1`` as claimed by ``content_list`` (never inferred as truth).
    content_list_page_count: int = 0

    @property
    def bound(self) -> list[LayoutEntry]:
        return [entry for entry in self.entries if entry.bound]

    @property
    def unbound(self) -> list[LayoutEntry]:
        return [entry for entry in self.entries if not entry.bound]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "issues": [issue.to_dict() for issue in self.issues],
            "middle_page_count": self.middle_page_count,
            "content_list_page_count": self.content_list_page_count,
        }


# ------------------------------------------------------------------ validation
@dataclass(frozen=True)
class _Block:
    """A ``middle`` block plus the facts the layers compare."""

    page_idx: int
    position: int  # bookkeeping key only; never compared with a content_list index
    engine_index: int | None
    type: str
    bbox: tuple[float, float, float, float]
    text_fp: str | None
    asset_key: str | None

    @property
    def label(self) -> str:
        return "?" if self.engine_index is None else str(self.engine_index)


def _block_fingerprint(block: Mapping[str, Any], block_type: str) -> str | None:
    if block_type in ASSET_TYPES:
        caption, footnote = MIDDLE_CAPTION_KINDS[block_type]
        text = _child_text(block, (caption,)) + _child_text(block, (footnote,))
    else:
        text = _lines_text(block)
    text = fingerprint(text)
    return text or None


def _validate_middle(middle: Any) -> tuple[dict[int, list[_Block]], int]:
    if not isinstance(middle, Mapping):
        raise _schema_drift(
            f"middle.json must be a JSON object, got {type(middle).__name__}"
        )
    pdf_info = middle.get("pdf_info")
    if not _is_sequence(pdf_info):
        raise _schema_drift(
            "middle.json has no 'pdf_info' list; the engine schema changed and an empty "
            "page table would hide it"
        )

    pages: dict[int, list[_Block]] = {}
    for page_position, page in enumerate(pdf_info):
        if not isinstance(page, Mapping):
            raise _schema_drift(f"pdf_info[{page_position}] must be an object")
        page_idx = page.get("page_idx")
        if isinstance(page_idx, bool) or not isinstance(page_idx, int) or page_idx < 0:
            raise _schema_drift(
                f"pdf_info[{page_position}].page_idx must be a 0-based integer, got {page_idx!r}"
            )
        if page_idx in pages:
            raise _schema_drift(
                f"middle.json declares page_idx {page_idx} twice; the page identity needed "
                "for the first matching layer is ambiguous"
            )

        blocks: Any = None
        source_key = "para_blocks"
        seen_any_key = False
        for key in ("para_blocks", "preproc_blocks"):
            candidate = page.get(key)
            if candidate is None:
                continue
            seen_any_key = True
            if not _is_sequence(candidate):
                raise _schema_drift(f"pdf_info[{page_position}].{key} must be a list")
            # ``para_blocks`` is the structure ``content_list`` is derived from, so it
            # wins; ``preproc_blocks`` is used only when the final structure is absent
            # or empty (a blank page is legitimate and stays an empty block list).
            if blocks is None or (len(blocks) == 0 and len(candidate) > 0):
                blocks, source_key = candidate, key
        if blocks is None or not seen_any_key:
            raise _schema_drift(
                f"pdf_info[{page_position}] has neither 'para_blocks' nor 'preproc_blocks'; "
                "there is nothing to pair with"
            )

        rows: list[_Block] = []
        for position, block in enumerate(blocks):
            if not isinstance(block, Mapping):
                raise _schema_drift(
                    f"pdf_info[{page_position}].{source_key}[{position}] must be an object"
                )
            block_type = block.get("type")
            if not isinstance(block_type, str) or not block_type:
                raise _schema_drift(
                    f"pdf_info[{page_position}].{source_key}[{position}] has no usable 'type': "
                    f"{block_type!r}"
                )
            bbox = _bbox(
                block.get("bbox"),
                where=f"pdf_info[{page_position}].{source_key}[{position}].bbox",
            )
            rows.append(
                _Block(
                    page_idx=page_idx,
                    position=position,
                    engine_index=_engine_index(block),
                    type=block_type,
                    bbox=bbox,
                    text_fp=_block_fingerprint(block, block_type),
                    asset_key=_asset_key(_asset_reference(block)),
                )
            )
        pages[page_idx] = rows

    return pages, len(pdf_info)


@dataclass(frozen=True)
class _Entry:
    index: int
    type: str
    page_idx: int
    text_fp: str | None
    asset_key: str | None
    engine_id: int | None
    bbox: tuple[float, float, float, float] | None


def _entry_engine_id(raw: Mapping[str, Any], *, index: int) -> int | None:
    """An explicit pairing hint, never ``index``.

    ``content_list`` entries carry no engine local id in real MinerU output, and the
    tempting stand-in -- the object's ``index`` key -- is exactly the array position.
    Reading it would quietly restore positional alignment, so only a field that
    names the intent (``middle_index``) is honoured.
    """
    value = raw.get("middle_index")
    if value is None:
        return None
    if _is_number(value):
        number = float(value)
        if number.is_integer():
            return int(number)
    raise _schema_drift(
        f"content_list[{index}].middle_index must be an integer engine block id, got {value!r}"
    )


def _validate_content_list(content_list: Any) -> list[_Entry]:
    if not _is_sequence(content_list):
        raise _schema_drift(
            f"content_list must be a list of entries, got {type(content_list).__name__}"
        )

    entries: list[_Entry] = []
    for index, raw in enumerate(content_list):
        if not isinstance(raw, Mapping):
            raise _schema_drift(f"content_list[{index}] must be an object")
        entry_type = raw.get("type")
        if not isinstance(entry_type, str) or not entry_type:
            raise _schema_drift(
                f"content_list[{index}] has no usable 'type': {entry_type!r}; an entry that "
                "cannot enter the type layer must not be paired by guesswork"
            )
        page_idx = raw.get("page_idx")
        if isinstance(page_idx, bool) or not isinstance(page_idx, int) or page_idx < 0:
            raise _schema_drift(
                f"content_list[{index}].page_idx must be a 0-based integer, got {page_idx!r}"
            )

        text_fp: str | None
        if entry_type in ASSET_TYPES:
            caption_key, footnote_key = ENTRY_CAPTION_KEYS[entry_type]
            caption = raw.get(caption_key)
            footnote = raw.get(footnote_key)
            for name, value in ((caption_key, caption), (footnote_key, footnote)):
                if value is not None and not _is_sequence(value):
                    raise _schema_drift(
                        f"content_list[{index}].{name} must be a list of strings, got "
                        f"{type(value).__name__}"
                    )
                if _is_sequence(value):
                    for item in value:
                        if not isinstance(item, str):
                            raise _schema_drift(
                                f"content_list[{index}].{name} contains {item!r}"
                            )
            joined = "".join(caption or ()) + "".join(footnote or ())
            text_fp = fingerprint(joined) or None
        else:
            text = raw.get("text")
            if text is not None and not isinstance(text, str):
                raise _schema_drift(
                    f"content_list[{index}].text must be a string, got {type(text).__name__}"
                )
            text_fp = fingerprint(text or "") or None

        raw_bbox = raw.get("bbox")
        bbox = (
            None
            if raw_bbox is None
            else _bbox(raw_bbox, where=f"content_list[{index}].bbox")
        )

        entries.append(
            _Entry(
                index=index,
                type=entry_type,
                page_idx=page_idx,
                text_fp=text_fp,
                asset_key=_asset_key(raw.get("img_path")),
                engine_id=_entry_engine_id(raw, index=index),
                bbox=bbox,
            )
        )

    return entries


# ------------------------------------------------------------------- geometry
def _as_geometry(value: Any, *, where: str) -> PageGeometry:
    if isinstance(value, PageGeometry):
        return value
    if isinstance(value, Mapping):
        return PageGeometry.from_mapping(value)
    raise _input_invalid(
        f"{where} must be a PageGeometry or its mapping form, got {type(value).__name__}"
    )


def _geometry_for(page_idx: int, page_geometry: Any) -> tuple[PageGeometry | None, str | None]:
    """Resolve the geometry of ``page_idx`` (0-based) and say why it is missing.

    Accepted shapes:

    * ``None`` -- no geometry context at all;
    * one :class:`PageGeometry` (or its mapping form, recognised by ``page_number``)
      -- used only for the page it names;
    * a mapping ``page_idx -> geometry`` -- the 0-based engine page index is the key.

    A geometry for another page is never reused: it carries that page's
    ``source_sha256``, so applying it here would attach evidence to the wrong page.
    """
    if page_geometry is None:
        return None, "NO_PAGE_GEOMETRY"
    if isinstance(page_geometry, PageGeometry):
        geo = page_geometry
        if geo.page_number != page_idx + 1:
            return None, "PAGE_GEOMETRY_PAGE_MISMATCH"
        return geo, None
    if isinstance(page_geometry, Mapping):
        if "page_number" in page_geometry:
            geo = _as_geometry(page_geometry, where="page_geometry")
            if geo.page_number != page_idx + 1:
                return None, "PAGE_GEOMETRY_PAGE_MISMATCH"
            return geo, None
        for key in (page_idx, str(page_idx)):
            if key in page_geometry:
                geo = _as_geometry(page_geometry[key], where=f"page_geometry[{key!r}]")
                if geo.page_number != page_idx + 1:
                    return None, "PAGE_GEOMETRY_PAGE_MISMATCH"
                return geo, None
        return None, "NO_PAGE_GEOMETRY"
    raise _input_invalid(
        "page_geometry must be None, a PageGeometry, or a mapping of page_idx to geometry, "
        f"got {type(page_geometry).__name__}"
    )


def _corners(bbox: Sequence[float]) -> list[list[float]]:
    x0, y0, x1, y1 = bbox
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


# -------------------------------------------------------------------- matching
def _issue(
    code: str,
    message: str,
    *,
    entry: _Entry,
    layers: tuple[str, ...],
    candidates: Sequence[_Block] = (),
    detail: Mapping[str, Any] | None = None,
) -> MappingIssue:
    return MappingIssue(
        code=code,
        message=message,
        content_list_index=entry.index,
        page_idx=entry.page_idx,
        layers=layers,
        candidates=tuple(block.label for block in candidates),
        detail=dict(detail or {}),
    )


def _match(
    entry: _Entry,
    pages: Mapping[int, Sequence[_Block]],
    trace: list[str],
) -> tuple[_Block | None, MappingIssue | None, tuple[str, ...]]:
    """Run the layers in order; bind only when exactly one candidate survives.

    ``trace`` is filled in place with one step per layer ("layer:candidate count"), so
    the caller can report how far the comparison got even when it bound. ``layers``
    (returned) is the narrower list of layers that actually *filtered*, which is what
    an issue must state.
    """

    # --- layer 1: page -------------------------------------------------------
    blocks = pages.get(entry.page_idx)
    if blocks is None:
        trace.append(f"page:{entry.page_idx}->0 block(s)")
        return None, _issue(
            ISSUE_PAGE_COUNT,
            "content_list points at a page the middle file does not contain; no block can "
            "be compared, so no coordinate is claimed",
            entry=entry,
            layers=(LAYER_PAGE,),
            detail={"page_idx": entry.page_idx, "middle_pages": sorted(pages)},
        ), (LAYER_PAGE,)
    trace.append(f"page:{entry.page_idx}->{len(blocks)} block(s)")

    # --- layer 2: type -------------------------------------------------------
    family = TYPE_FAMILIES.get(entry.type, frozenset({entry.type}))
    candidates = [block for block in blocks if block.type in family]
    trace.append(f"type:{entry.type}->{len(candidates)}")
    if not candidates:
        return None, _issue(
            ISSUE_NO_CANDIDATE,
            "no block on this page has a type this entry can be derived from",
            entry=entry,
            layers=(LAYER_PAGE, LAYER_TYPE),
            detail={
                "accepted_types": sorted(family),
                "page_types": sorted({block.type for block in blocks}),
            },
        ), (LAYER_PAGE, LAYER_TYPE)

    layers: tuple[str, ...] = (LAYER_PAGE, LAYER_TYPE)

    # --- layer 3: asset hash -------------------------------------------------
    if entry.type in ASSET_TYPES:
        if entry.asset_key is None:
            return None, _issue(
                ISSUE_MISSING_ASSET,
                "the content_list entry names no asset file, so the asset layer cannot run "
                "and the block identity would rest on caption text alone",
                entry=entry,
                layers=layers,
                candidates=candidates,
            ), layers
        with_key = [block for block in candidates if block.asset_key is not None]
        if not with_key:
            return None, _issue(
                ISSUE_MISSING_ASSET,
                "no candidate block carries an image reference, so the asset hash cannot be "
                "compared on the middle side either",
                entry=entry,
                layers=layers,
                candidates=candidates,
            ), layers
        candidates = [block for block in with_key if block.asset_key == entry.asset_key]
        layers += (LAYER_ASSET,)
        trace.append(f"asset_hash:{entry.asset_key}->{len(candidates)}")
        if not candidates:
            return None, _issue(
                ISSUE_NO_CANDIDATE,
                "the asset file named by content_list is not referenced by any candidate "
                "block on this page",
                entry=entry,
                layers=layers,
                detail={"asset_key": entry.asset_key},
            ), layers

    # --- layer 4: verifiable text fingerprint --------------------------------
    if entry.text_fp is None:
        if entry.type not in ASSET_TYPES:
            return None, _issue(
                ISSUE_MISSING_TEXT,
                "the entry carries no text, so the only remaining identity would be array "
                "position; no coordinate is claimed",
                entry=entry,
                layers=layers,
                candidates=candidates,
            ), layers
        trace.append("text_fingerprint:unavailable_in_entry")
    else:
        with_fp = [block for block in candidates if block.text_fp is not None]
        if not with_fp:
            # The middle file gave no caption/paragraph text for these blocks. The
            # asset hash already established identity, so the layer is reported as
            # unavailable rather than as a refusal -- but it is recorded, never
            # silently treated as a pass.
            trace.append("text_fingerprint:unavailable_in_middle")
        else:
            candidates = [block for block in with_fp if block.text_fp == entry.text_fp]
            layers += (LAYER_TEXT,)
            trace.append(f"text_fingerprint->{len(candidates)}")
            if not candidates:
                return None, _issue(
                    ISSUE_NO_CANDIDATE,
                    "no candidate block carries the entry's text; the layers disagree, so "
                    "the pairing is refused",
                    entry=entry,
                    layers=layers,
                    detail={"text_fingerprint": entry.text_fp},
                ), layers

    # --- layer 5: engine local id -------------------------------------------
    if entry.engine_id is not None:
        candidates = [
            block for block in candidates if block.engine_index == entry.engine_id
        ]
        layers += (LAYER_ENGINE_ID,)
        trace.append(f"engine_id:{entry.engine_id}->{len(candidates)}")
        if not candidates:
            return None, _issue(
                ISSUE_NO_CANDIDATE,
                "the explicit middle_index hint matches no surviving candidate",
                entry=entry,
                layers=layers,
                detail={"middle_index": entry.engine_id},
            ), layers
    else:
        trace.append("engine_id:unavailable_in_entry")

    # --- verdict -------------------------------------------------------------
    if len(candidates) > 1:
        return None, _issue(
            ISSUE_AMBIGUOUS,
            "several blocks survive every layer; identical captions or same-page blocks of "
            "the same type are not resolved by list order",
            entry=entry,
            layers=layers,
            candidates=candidates,
            detail={
                "surviving": [
                    {
                        "middle_index": block.label,
                        "type": block.type,
                        "bbox": list(block.bbox),
                        "text_fingerprint": block.text_fp,
                    }
                    for block in candidates
                ]
            },
        ), layers

    return candidates[0], None, layers


# ----------------------------------------------------------------------- entry
def _build_entry(
    entry: _Entry,
    block: _Block,
    *,
    geometry: PageGeometry | None,
    page_only_reason: str | None,
    trace: Sequence[str],
) -> LayoutEntry:
    bbox = list(block.bbox)
    bbox_source = "MIDDLE_BLOCK"
    if entry.bbox is not None:
        bbox_source = "CONTENT_LIST+MIDDLE"

    unit = None if geometry is None else geometry.unit
    locator: Mapping[str, Any] | None = None
    evidence_class = "PAGE_ONLY"
    reason = page_only_reason

    if geometry is not None:
        try:
            region = map_or_page_only(geometry, _corners(block.bbox))
        except ExtensionError as exc:
            # ``map_or_page_only`` already degrades E_GEOMETRY_* to PAGE_ONLY; what can
            # still escape is E_INPUT_INVALID when the engine bbox lies outside the
            # processed canvas this geometry maps from. That is a real disagreement
            # between the engine's coordinate space and the geometry, so it is kept as
            # a reason and the evidence stays page-level.
            region = None
            reason = f"{exc.code}: {exc.message}"
        if region is not None:
            evidence_class = region.evidence_class
            reason = region.reason
            locator = {
                "kind": "PAGE_REGION",
                "source_sha256": region.source_sha256,
                "geometry_id": geometry.geometry_id,
                "page_no": geometry.page_number,
                "level": region.evidence_class,
                "normalized_polygon": region.normalized_polygon,
            }
        else:
            locator = {
                "kind": "PAGE_REGION",
                "source_sha256": geometry.source_sha256,
                "geometry_id": geometry.geometry_id,
                "page_no": geometry.page_number,
                "level": "PAGE_ONLY",
                "normalized_polygon": None,
            }

    return LayoutEntry(
        content_list_index=entry.index,
        type=entry.type,
        page_idx=entry.page_idx,
        page_no=entry.page_idx + 1,
        middle_index=block.engine_index,
        bbox=bbox,
        bbox_source=bbox_source,
        content_list_bbox=None if entry.bbox is None else list(entry.bbox),
        unit=unit,
        evidence_class=evidence_class,
        page_locator=locator,
        page_only_reason=reason,
        trace=tuple(trace) + ("bound",),
    )


def _unbound_entry(
    entry: _Entry,
    issue: MappingIssue,
    *,
    geometry: PageGeometry | None,
    trace: Sequence[str],
) -> LayoutEntry:
    """An entry with no block: coordinates, locator and middle id all stay ``None``.

    ``page_only_reason`` repeats the *pairing* code rather than a geometry reason: the
    question "why is there no coordinate" is answered by the mapping issue, and the
    geometry state is still readable from ``unit`` / ``page_locator``.
    """
    return LayoutEntry(
        content_list_index=entry.index,
        type=entry.type,
        page_idx=entry.page_idx,
        page_no=entry.page_idx + 1,
        middle_index=None,
        bbox=None,
        bbox_source=None,
        content_list_bbox=None if entry.bbox is None else list(entry.bbox),
        unit=None if geometry is None else geometry.unit,
        evidence_class="PAGE_ONLY",
        page_locator=None,
        page_only_reason=issue.code,
        trace=tuple(trace) + ("unbound",),
        mapping_issue=issue,
    )


# ----------------------------------------------------------------------- public
def read_mineru(
    content_list: Sequence[Mapping[str, Any]],
    middle: Mapping[str, Any],
    *,
    page_geometry: Any = None,
) -> MineruReadResult:
    """Pair an existing MinerU ``content_list`` with its ``middle`` file.

    ``content_list``: the engine's flat list (``type``, ``page_idx``, ``text`` /
    ``table_caption`` / ``img_path`` ...). ``middle``: the engine's structured
    document (``pdf_info[*].para_blocks`` preferred, ``preproc_blocks`` as fallback).

    ``page_geometry``: ``None``, one ``geometry.transform.PageGeometry``, or a mapping
    ``page_idx -> PageGeometry``. Without it every entry stays ``PAGE_ONLY`` with
    ``unit = None`` -- an unlabelled unit is not evidence.

    Raises ``E_ENGINE_SCHEMA_DRIFT`` for structural damage and ``E_INPUT_INVALID`` for
    an unusable argument. Ambiguity never raises: it is returned as ``mapping_issue``.
    """
    entries = _validate_content_list(content_list)
    pages, page_count = _validate_middle(middle)

    issues: list[MappingIssue] = []
    results: list[LayoutEntry] = []
    bound_keys: dict[tuple[int, int], int] = {}

    for entry in entries:
        geometry, page_only_reason = _geometry_for(entry.page_idx, page_geometry)
        trace: list[str] = []
        block, issue, layers = _match(entry, pages, trace)

        if block is not None and entry.bbox is not None:
            disagreement = max(
                abs(left - right) for left, right in zip(entry.bbox, block.bbox)
            )
            if disagreement > BBOX_TOLERANCE:
                middle_bbox = list(block.bbox)
                block = None
                trace.append(f"bbox:max_delta={disagreement:.3f}>{BBOX_TOLERANCE}")
                issue = MappingIssue(
                    code=ISSUE_BBOX,
                    message=(
                        "the entry's own bbox disagrees with the bound block's bbox; one of "
                        "the two is in a different coordinate space, so neither is claimed"
                    ),
                    content_list_index=entry.index,
                    page_idx=entry.page_idx,
                    layers=layers,
                    candidates=(),
                    detail={
                        "content_list_bbox": list(entry.bbox),
                        "middle_bbox": middle_bbox,
                        "max_delta": round(disagreement, 6),
                        "tolerance": BBOX_TOLERANCE,
                    },
                )

        if block is not None:
            key = (block.page_idx, block.position)
            owner = bound_keys.get(key)
            if owner is not None:
                block = None
                trace.append(f"one_to_one:already_bound_by={owner}")
                issue = MappingIssue(
                    code=ISSUE_ALREADY_BOUND,
                    message=(
                        "this middle block is already bound to another content_list entry; "
                        "one block cannot be the geometry of two entries"
                    ),
                    content_list_index=entry.index,
                    page_idx=entry.page_idx,
                    layers=layers,
                    candidates=(),
                    detail={"bound_to_content_list_index": owner},
                )
            else:
                bound_keys[key] = entry.index

        if block is None:
            assert issue is not None
            issues.append(issue)
            results.append(
                _unbound_entry(
                    entry,
                    issue,
                    geometry=geometry,
                    trace=trace,
                )
            )
            continue

        results.append(
            _build_entry(
                entry,
                block,
                geometry=geometry,
                page_only_reason=page_only_reason,
                trace=trace,
            )
        )

    claimed = max((entry.page_idx for entry in entries), default=-1) + 1
    return MineruReadResult(
        entries=results,
        issues=issues,
        middle_page_count=page_count,
        content_list_page_count=claimed,
    )


# ------------------------------------------------------------------------ load
def _load_json(path: Any) -> Any:
    if not isinstance(path, (str, Path)):
        raise _input_invalid(f"path must be a str or Path, got {type(path).__name__}")
    if isinstance(path, str) and not path.strip():
        raise _input_invalid("path must not be empty")
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise _input_invalid(f"no such MinerU artifact: {target}", path=str(target)) from exc
    except OSError as exc:
        raise _input_invalid(f"cannot read {target}: {exc}", path=str(target)) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _input_invalid(
            f"{target} is not valid JSON ({exc.msg} at line {exc.lineno})", path=str(target)
        ) from exc


def load_content_list(path: Any) -> list[dict[str, Any]]:
    """Read ``content_list.json`` read-only, refusing anything but a list of objects."""
    payload = _load_json(path)
    if not _is_sequence(payload):
        raise _schema_drift(
            f"content_list.json must hold a JSON array, got {type(payload).__name__}",
            path=str(path),
        )
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, Mapping):
            raise _schema_drift(
                f"content_list.json[{index}] must be an object, got {type(item).__name__}",
                path=str(path),
            )
        entries.append(dict(item))
    return entries


def load_middle(path: Any) -> dict[str, Any]:
    """Read ``middle.json`` read-only, refusing anything but a ``pdf_info`` object."""
    payload = _load_json(path)
    if not isinstance(payload, Mapping):
        raise _schema_drift(
            f"middle.json must hold a JSON object, got {type(payload).__name__}", path=str(path)
        )
    if not _is_sequence(payload.get("pdf_info")):
        raise _schema_drift(
            "middle.json has no 'pdf_info' list; an empty page table would be a silent "
            "downgrade",
            path=str(path),
        )
    return dict(payload)
