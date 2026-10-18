"""T402 / ``PI-F04-02`` + ``PI-F04-03``: reading MinerU again instead of re-parsing.

Two kinds of test live here:

* **golden** -- the real MinerU artifacts already in the repository
  (``docs/demo/pdf-rich-to-text/ir_source``) are read, never rewritten. Nothing here
  is invented engine output pretending to be real.
* **refusals** -- a pairing layer that "almost" works is exactly where a caption and
  a coordinate silently end up on the wrong block, so most cases assert that the
  reader declines, keeps ``bbox = null`` and names the layers it compared.

The anti-positional cases matter most: an implementation built on
``zip(content_list, blocks)`` passes every happy-path sample in this file and fails
``test_reordered_middle_still_binds_by_text`` / ``test_index_field_is_not_an_alignment_key``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.extensions import ExtensionError
from app.extensions.adapters.mineru_reader import (
    ISSUE_ALREADY_BOUND,
    ISSUE_AMBIGUOUS,
    ISSUE_BBOX,
    ISSUE_MISSING_ASSET,
    ISSUE_MISSING_TEXT,
    ISSUE_NO_CANDIDATE,
    ISSUE_PAGE_COUNT,
    LAYER_ASSET,
    LAYER_ENGINE_ID,
    LAYER_TEXT,
    fingerprint,
    load_content_list,
    load_middle,
    read_mineru,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_DIR = REPO_ROOT / "docs" / "demo" / "pdf-rich-to-text" / "ir_source"
GOLDEN_CONTENT_LIST = GOLDEN_DIR / "content_list.json"
GOLDEN_MIDDLE = GOLDEN_DIR / "middle.json"
SHA = "a" * 64


# --------------------------------------------------------------------- builders
def span(text: str) -> dict:
    return {"bbox": [0, 0, 1, 1], "content": text, "type": "text"}


def text_block(index, bbox, text: str, *, block_type: str = "text", **extra) -> dict:
    """A middle text-ish block whose text is split into spans, as MinerU does."""
    half = len(text) // 2
    spans = [span(text[:half]), span(text[half:])] if len(text) > 2 else [span(text)]
    return {
        "type": block_type,
        "bbox": list(bbox),
        "index": index,
        "lines": [{"bbox": list(bbox), "spans": spans, "index": 0}],
        **extra,
    }


def asset_block(
    index, bbox, kind: str, *, caption: str | None, image_path: str | None
) -> dict:
    """A middle table/image block: caption child + body child carrying the asset."""
    children = []
    if caption is not None:
        children.append(
            {
                "type": f"{kind}_caption",
                "bbox": list(bbox),
                "lines": [{"bbox": list(bbox), "spans": [span(caption)]}],
                "index": index,
            }
        )
    body_span: dict = {"bbox": list(bbox), "type": kind, "content": ""}
    if image_path is not None:
        body_span["image_path"] = image_path
    children.append(
        {
            "type": f"{kind}_body",
            "bbox": list(bbox),
            "lines": [{"bbox": list(bbox), "spans": [body_span]}],
            "index": index + 1,
        }
    )
    return {"type": kind, "bbox": list(bbox), "index": index, "blocks": children}


def page(page_idx, blocks, *, preproc=None, **extra) -> dict:
    body = {"page_idx": page_idx, "page_size": [595.0, 842.0], "para_blocks": blocks}
    if preproc is not None:
        body["preproc_blocks"] = preproc
    body.update(extra)
    return body


def middle_of(*pages) -> dict:
    return {"pdf_info": list(pages), "_version_name": "1.3.12", "_parse_type": "txt"}


def text_entry(text: str, page_idx: int = 0, **extra) -> dict:
    return {"type": "text", "text": text, "page_idx": page_idx, **extra}


def image_entry(img_path=None, caption="图 1 季度营收趋势", page_idx: int = 0) -> dict:
    entry = {"type": "image", "img_caption": [caption] if caption else [], "page_idx": page_idx}
    if img_path is not None:
        entry["img_path"] = img_path
    return entry


def geometry(page_number: int = 1, *, unit_verified: bool = True, processed=(595, 842),
             original=(595, 842), matrix=None, unit="PT") -> dict:
    return {
        "geometry_id": f"geo-{page_number}",
        "source_sha256": SHA,
        "page_number": page_number,
        "original_width": original[0],
        "original_height": original[1],
        "processed_width": processed[0],
        "processed_height": processed[1],
        "crop_origin": [0.0, 0.0],
        "matrix": matrix or [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        "unit": unit,
        "unit_verified": unit_verified,
    }


def golden():
    return load_content_list(GOLDEN_CONTENT_LIST), load_middle(GOLDEN_MIDDLE)


def codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


# ----------------------------------------------------------------------- golden
def test_golden_real_artifact_binds_every_entry_by_content():
    """The repository's own MinerU output pairs one-to-one, with no issue raised."""
    content_list, middle = golden()
    result = read_mineru(content_list, middle)

    assert codes(result) == []
    assert len(result.entries) == len(content_list) == 4
    assert [(entry.type, entry.middle_index) for entry in result.bound] == [
        ("text", 0),
        ("text", 2),
        ("table", 5),
        ("image", 9),
    ]
    # Coordinates come from the bound block, not from a computed guess.
    assert result.entries[2].bbox == [57.0, 170.0, 521.0, 269.0]
    assert result.entries[3].bbox == [70.0, 313.0, 448.0, 440.0]
    assert {entry.bbox_source for entry in result.bound} == {"MIDDLE_BLOCK"}


def test_golden_without_geometry_is_page_only_and_unit_is_null():
    content_list, middle = golden()
    result = read_mineru(content_list, middle)

    for entry in result.bound:
        assert entry.evidence_class == "PAGE_ONLY"
        assert entry.unit is None
        assert entry.page_locator is None
        assert entry.page_only_reason == "NO_PAGE_GEOMETRY"


def test_reversed_content_list_order_still_binds_by_text():
    """Proves the reader never aligns on the content_list array position."""
    content_list, middle = golden()
    expected = {entry.content_list_index: entry.middle_index
                for entry in read_mineru(content_list, middle).entries}

    reversed_result = read_mineru(list(reversed(content_list)), middle)
    assert codes(reversed_result) == []
    for position, entry in enumerate(reversed_result.entries):
        original_index = len(content_list) - 1 - position
        # The recorded index follows the new array; the binding follows the content.
        assert entry.content_list_index == position
        assert entry.middle_index == expected[original_index]
    assert [entry.middle_index for entry in reversed_result.bound] == [9, 5, 2, 0]


def test_reordered_middle_still_binds_by_text():
    """Proves the reader never aligns on the middle block array position either."""
    content_list, middle = golden()
    shuffled = copy.deepcopy(middle)
    shuffled["pdf_info"][0]["para_blocks"].reverse()

    result = read_mineru(content_list, shuffled)
    assert codes(result) == []
    assert [(entry.type, entry.middle_index) for entry in result.bound] == [
        ("text", 0),
        ("text", 2),
        ("table", 5),
        ("image", 9),
    ]


# ------------------------------------------------------------------ ambiguity
def test_same_page_same_type_same_caption_is_ambiguous_not_index_bound():
    left = [40, 40, 400, 80]
    right = [40, 200, 400, 240]
    middle = middle_of(page(0, [text_block(11, left, "重复的段落"), text_block(12, right, "重复的段落")]))

    result = read_mineru([text_entry("重复的段落")], middle)

    assert len(result.bound) == 0
    entry = result.entries[0]
    assert entry.bbox is None
    assert entry.middle_index is None
    assert entry.mapping_issue.code == ISSUE_AMBIGUOUS
    assert entry.mapping_issue.layers == ("page", "type", LAYER_TEXT)
    assert entry.mapping_issue.candidates == ("11", "12")
    assert len(entry.mapping_issue.detail["surviving"]) == 2


def test_duplicate_asset_hash_is_ambiguous():
    bbox_a = [10, 10, 100, 100]
    bbox_b = [10, 200, 100, 300]
    same = "images/dup.jpg"
    middle = middle_of(
        page(
            0,
            [
                asset_block(3, bbox_a, "image", caption="图 1 同一张", image_path="dup.jpg"),
                asset_block(5, bbox_b, "image", caption="图 1 同一张", image_path="dup.jpg"),
            ],
        )
    )

    result = read_mineru([image_entry(same, caption="图 1 同一张")], middle)

    entry = result.entries[0]
    assert entry.bbox is None
    assert entry.mapping_issue.code == ISSUE_AMBIGUOUS
    assert LAYER_ASSET in entry.mapping_issue.layers
    assert entry.mapping_issue.candidates == ("3", "5")


def test_missing_asset_hash_on_entry_is_not_bound():
    middle = middle_of(
        page(0, [asset_block(3, [10, 10, 100, 100], "image", caption="图 1", image_path="x.jpg")])
    )

    result = read_mineru([image_entry(None, caption="图 1")], middle)

    entry = result.entries[0]
    assert entry.bbox is None
    assert entry.mapping_issue.code == ISSUE_MISSING_ASSET
    assert entry.mapping_issue.layers == ("page", "type")


def test_middle_without_asset_reference_is_not_bound():
    middle = middle_of(
        page(0, [asset_block(3, [10, 10, 100, 100], "image", caption="图 1", image_path=None)])
    )

    result = read_mineru([image_entry("images/x.jpg", caption="图 1")], middle)

    assert result.entries[0].bbox is None
    assert result.entries[0].mapping_issue.code == ISSUE_MISSING_ASSET


def test_one_block_cannot_be_the_geometry_of_two_entries():
    middle = middle_of(page(0, [text_block(7, [40, 40, 400, 80], "同一段文字")]))

    result = read_mineru([text_entry("同一段文字"), text_entry("同一段文字")], middle)

    first, second = result.entries
    assert first.bound and first.middle_index == 7
    assert second.bbox is None
    assert second.mapping_issue.code == ISSUE_ALREADY_BOUND
    assert second.mapping_issue.detail["bound_to_content_list_index"] == 0


# ----------------------------------------------------------------------- layers
def test_page_beyond_middle_is_page_count_mismatch():
    middle = middle_of(page(0, [text_block(1, [0, 0, 10, 10], "第一页")]))

    result = read_mineru([text_entry("第一页", page_idx=3)], middle)

    assert result.middle_page_count == 1
    assert result.content_list_page_count == 4
    entry = result.entries[0]
    assert entry.bbox is None
    assert entry.mapping_issue.code == ISSUE_PAGE_COUNT
    assert entry.mapping_issue.detail["middle_pages"] == [0]


def test_type_with_no_family_member_is_reported_as_no_candidate():
    middle = middle_of(page(0, [text_block(1, [0, 0, 10, 10], "只有正文")]))

    result = read_mineru([image_entry("images/x.jpg", caption="图 1")], middle)

    entry = result.entries[0]
    assert entry.bbox is None
    assert entry.mapping_issue.code == ISSUE_NO_CANDIDATE
    assert entry.mapping_issue.layers == ("page", "type")


def test_text_entry_without_text_is_not_bound():
    middle = middle_of(page(0, [text_block(1, [0, 0, 10, 10], "有文字")]))

    result = read_mineru([{"type": "text", "text": "", "page_idx": 0}], middle)

    entry = result.entries[0]
    assert entry.bbox is None
    assert entry.mapping_issue.code == ISSUE_MISSING_TEXT


def test_text_fingerprint_mismatch_is_no_candidate():
    middle = middle_of(page(0, [text_block(1, [0, 0, 10, 10], "甲段落")]))

    result = read_mineru([text_entry("乙段落")], middle)

    assert result.entries[0].mapping_issue.code == ISSUE_NO_CANDIDATE
    assert LAYER_TEXT in result.entries[0].mapping_issue.layers


def test_engine_local_id_hint_resolves_an_otherwise_ambiguous_page():
    middle = middle_of(
        page(
            0,
            [
                text_block(21, [0, 0, 10, 10], "同样的标题"),
                text_block(22, [0, 20, 10, 30], "同样的标题"),
            ],
        )
    )

    result = read_mineru([text_entry("同样的标题", middle_index=22)], middle)

    entry = result.entries[0]
    assert entry.bound
    assert entry.middle_index == 22
    assert entry.bbox == [0.0, 20.0, 10.0, 30.0]
    assert any(step.startswith(LAYER_ENGINE_ID) for step in entry.trace)


def test_index_field_is_not_an_alignment_key():
    """``index`` on a content_list entry is the array position, so it is ignored.

    The entries here carry deliberately misleading ``index`` values; honouring them
    would bind each entry to the wrong block while still looking correct.
    """
    middle = middle_of(
        page(
            0,
            [
                text_block(50, [0, 0, 10, 10], "甲段落"),
                text_block(60, [0, 20, 10, 30], "乙段落"),
            ],
        )
    )
    content_list = [
        {"type": "text", "text": "甲段落", "page_idx": 0, "index": 1},
        {"type": "text", "text": "乙段落", "page_idx": 0, "index": 0},
    ]

    result = read_mineru(content_list, middle)

    assert [(entry.content_list_index, entry.middle_index) for entry in result.bound] == [
        (0, 50),
        (1, 60),
    ]
    assert all("engine_id:unavailable_in_entry" in entry.trace for entry in result.bound)


def test_para_blocks_win_over_preproc_blocks():
    """``para_blocks`` is the structure ``content_list`` is derived from."""
    para = [text_block(77, [0, 0, 10, 10], "正文段落", block_type="list")]
    preproc = [text_block(99, [0, 0, 10, 10], "正文段落", block_type="text")]
    middle = middle_of(page(0, para, preproc=preproc))

    result = read_mineru([text_entry("正文段落")], middle)

    assert result.entries[0].middle_index == 77


def test_content_list_bbox_that_agrees_is_kept_and_labelled():
    middle = middle_of(page(0, [text_block(4, [10, 20, 110, 60], "带框段落")]))

    result = read_mineru([text_entry("带框段落", bbox=[10, 20, 110, 60])], middle)

    entry = result.entries[0]
    assert entry.bound
    assert entry.bbox_source == "CONTENT_LIST+MIDDLE"
    assert entry.content_list_bbox == [10.0, 20.0, 110.0, 60.0]


def test_content_list_bbox_disagreeing_with_middle_claims_nothing():
    middle = middle_of(page(0, [text_block(4, [10, 20, 110, 60], "带框段落")]))

    result = read_mineru([text_entry("带框段落", bbox=[500, 700, 590, 800])], middle)

    entry = result.entries[0]
    assert entry.bbox is None
    assert entry.middle_index is None
    assert entry.mapping_issue.code == ISSUE_BBOX
    assert entry.mapping_issue.detail["middle_bbox"] == [10.0, 20.0, 110.0, 60.0]
    assert entry.mapping_issue.detail["max_delta"] > entry.mapping_issue.detail["tolerance"]


# --------------------------------------------------------------------- geometry
def test_identity_geometry_yields_region_verified_and_a_page_locator():
    middle = middle_of(page(0, [text_block(4, [10, 20, 110, 60], "带框段落")]))

    result = read_mineru(
        [text_entry("带框段落")], middle, page_geometry=geometry(1)
    )

    entry = result.entries[0]
    assert entry.evidence_class == "REGION_VERIFIED"
    assert entry.unit == "PT"
    locator = entry.page_locator
    assert locator["kind"] == "PAGE_REGION"
    assert locator["level"] == "REGION_VERIFIED"
    assert locator["geometry_id"] == "geo-1"
    assert locator["source_sha256"] == SHA
    assert locator["page_no"] == 1
    assert locator["normalized_polygon"][0] == pytest.approx([10 / 595, 20 / 842])


def test_geometry_mapping_is_keyed_by_zero_based_page_idx():
    middle = middle_of(
        page(0, [text_block(4, [10, 20, 110, 60], "第一页")]),
        page(1, [text_block(5, [10, 20, 110, 60], "第二页")]),
    )

    result = read_mineru(
        [text_entry("第二页", page_idx=1)],
        middle,
        page_geometry={1: geometry(2)},
    )

    entry = result.entries[0]
    assert entry.page_no == 2
    assert entry.evidence_class == "REGION_VERIFIED"
    assert entry.page_locator["page_no"] == 2


def test_geometry_of_another_page_is_never_reused():
    middle = middle_of(page(0, [text_block(4, [10, 20, 110, 60], "第一页")]))

    result = read_mineru(
        [text_entry("第一页")], middle, page_geometry=geometry(2)
    )

    entry = result.entries[0]
    assert entry.bound  # the block is still identified ...
    assert entry.evidence_class == "PAGE_ONLY"  # ... but no region is claimed
    assert entry.unit is None
    assert entry.page_locator is None
    assert entry.page_only_reason == "PAGE_GEOMETRY_PAGE_MISMATCH"


def test_unverified_unit_geometry_degrades_to_page_only():
    middle = middle_of(page(0, [text_block(4, [10, 20, 110, 60], "带框段落")]))

    result = read_mineru(
        [text_entry("带框段落")],
        middle,
        page_geometry=geometry(1, unit_verified=False),
    )

    entry = result.entries[0]
    assert entry.evidence_class == "PAGE_ONLY"
    assert entry.unit == "PT"  # the declared unit is labelled, not promoted
    assert entry.page_locator["level"] == "PAGE_ONLY"
    assert entry.page_locator["normalized_polygon"] is None
    assert "E_GEOMETRY_UNVERIFIED" in entry.page_only_reason


def test_bbox_outside_the_processed_canvas_stays_page_only():
    middle = middle_of(page(0, [text_block(4, [10, 20, 400, 60], "带框段落")]))

    result = read_mineru(
        [text_entry("带框段落")],
        middle,
        page_geometry=geometry(1, processed=(200, 200), original=(200, 200)),
    )

    entry = result.entries[0]
    assert entry.evidence_class == "PAGE_ONLY"
    assert entry.page_locator["level"] == "PAGE_ONLY"
    assert "E_INPUT_INVALID" in entry.page_only_reason


def test_transformed_geometry_uses_four_corners():
    """A rotated+cropped page maps differently from a width/height rescale."""
    matrix = [[0.0, -1.0, 842.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    middle = middle_of(page(0, [text_block(4, [100, 100, 150, 120], "带框段落")]))

    result = read_mineru(
        [text_entry("带框段落")],
        middle,
        page_geometry=geometry(1, matrix=matrix),
    )

    entry = result.entries[0]
    assert entry.evidence_class == "REGION_VERIFIED"
    # Only the transformed polygon is claimed; the engine bbox stays untouched.
    assert entry.bbox == [100.0, 100.0, 150.0, 120.0]
    corners = entry.page_locator["normalized_polygon"]
    assert corners != [[100 / 595, 100 / 842], [150 / 595, 100 / 842],
                       [150 / 595, 120 / 842], [100 / 595, 120 / 842]]


# ---------------------------------------------------------------- fingerprints
def test_fingerprint_erases_whitespace_and_normalises_width_but_keeps_case():
    assert fingerprint("表 1\u3000主要经营指标（单位： 万元）") == "表1主要经营指标(单位:万元)"
    assert fingerprint("Table A -12.5%") == "TableA-12.5%"
    assert fingerprint("A") != fingerprint("a")


# ------------------------------------------------------------ schema drift paths
def test_content_list_that_is_not_a_list_is_schema_drift():
    with pytest.raises(ExtensionError) as raised:
        read_mineru({"type": "text"}, middle_of(page(0, [])))
    assert raised.value.code == "E_ENGINE_SCHEMA_DRIFT"


def test_entry_without_type_is_schema_drift():
    with pytest.raises(ExtensionError) as raised:
        read_mineru([{"text": "没有 type", "page_idx": 0}], middle_of(page(0, [])))
    assert raised.value.code == "E_ENGINE_SCHEMA_DRIFT"
    assert "content_list[0]" in raised.value.message


def test_entry_with_non_integer_page_idx_is_schema_drift():
    with pytest.raises(ExtensionError) as raised:
        read_mineru([{"type": "text", "text": "x", "page_idx": "0"}], middle_of(page(0, [])))
    assert raised.value.code == "E_ENGINE_SCHEMA_DRIFT"


def test_middle_block_without_bbox_is_schema_drift():
    block = {"type": "text", "index": 1, "lines": []}
    with pytest.raises(ExtensionError) as raised:
        read_mineru([text_entry("文字")], middle_of(page(0, [block])))
    assert raised.value.code == "E_ENGINE_SCHEMA_DRIFT"
    assert "bbox" in raised.value.message


def test_middle_without_pdf_info_is_schema_drift():
    with pytest.raises(ExtensionError) as raised:
        read_mineru([text_entry("文字")], {"pages": []})
    assert raised.value.code == "E_ENGINE_SCHEMA_DRIFT"


def test_duplicate_page_idx_is_schema_drift():
    middle = {"pdf_info": [page(0, []), page(0, [])]}
    with pytest.raises(ExtensionError) as raised:
        read_mineru([], middle)
    assert raised.value.code == "E_ENGINE_SCHEMA_DRIFT"
    assert "twice" in raised.value.message


def test_middle_page_without_blocks_key_is_schema_drift():
    with pytest.raises(ExtensionError) as raised:
        read_mineru([], {"pdf_info": [{"page_idx": 0}]})
    assert raised.value.code == "E_ENGINE_SCHEMA_DRIFT"


# ------------------------------------------------------------------- loaders
def test_load_golden_files_are_read_not_rewritten():
    before = GOLDEN_CONTENT_LIST.stat().st_mtime_ns, GOLDEN_MIDDLE.stat().st_mtime_ns
    content_list, middle = golden()
    assert isinstance(content_list, list) and len(content_list) == 4
    assert isinstance(middle, dict) and len(middle["pdf_info"]) == 1
    assert GOLDEN_CONTENT_LIST.stat().st_mtime_ns == before[0]
    assert GOLDEN_MIDDLE.stat().st_mtime_ns == before[1]


def test_load_content_list_missing_file_is_input_invalid(tmp_path):
    with pytest.raises(ExtensionError) as raised:
        load_content_list(tmp_path / "nope.json")
    assert raised.value.code == "E_INPUT_INVALID"


def test_load_middle_with_broken_json_is_input_invalid(tmp_path):
    path = tmp_path / "middle.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ExtensionError) as raised:
        load_middle(path)
    assert raised.value.code == "E_INPUT_INVALID"


def test_load_content_list_of_wrong_root_type_is_schema_drift(tmp_path):
    path = tmp_path / "content_list.json"
    path.write_text(json.dumps({"items": []}), encoding="utf-8")
    with pytest.raises(ExtensionError) as raised:
        load_content_list(path)
    assert raised.value.code == "E_ENGINE_SCHEMA_DRIFT"


# ---------------------------------------------------------------- result shape
def test_read_is_pure_and_does_not_mutate_its_inputs():
    content_list, middle = golden()
    content_before = copy.deepcopy(content_list)
    middle_before = copy.deepcopy(middle)

    read_mineru(content_list, middle, page_geometry={0: geometry(1)})

    assert content_list == content_before
    assert middle == middle_before


def test_unbound_entries_invent_nothing():
    content_list, middle = golden()
    result = read_mineru(content_list, middle)
    payload = result.to_dict()

    # Round-trippable, and no fabricated confidence/caption fields anywhere.
    json.dumps(payload, ensure_ascii=False)
    for entry in payload["entries"]:
        assert "confidence" not in entry
        assert "engine_confidence" not in entry
        assert "caption" not in entry
        if entry["mapping_issue"] is None:
            assert entry["bbox"] is not None
            assert entry["evidence_class"] in {"REGION_VERIFIED", "PAGE_ONLY"}


def test_unbound_entry_reports_null_coordinates_and_a_reason():
    middle = middle_of(page(0, [text_block(1, [0, 0, 10, 10], "甲")]))
    result = read_mineru([text_entry("乙")], middle)

    payload = result.entries[0].to_dict()
    assert payload["bbox"] is None
    assert payload["middle_index"] is None
    assert payload["bbox_source"] is None
    assert payload["page_locator"] is None
    assert payload["evidence_class"] == "PAGE_ONLY"
    assert payload["page_only_reason"] == ISSUE_NO_CANDIDATE
    assert payload["mapping_issue"]["code"] == ISSUE_NO_CANDIDATE


def test_empty_content_list_yields_no_entries_and_no_issues():
    result = read_mineru([], middle_of(page(0, [text_block(1, [0, 0, 1, 1], "x")])))

    assert result.entries == []
    assert result.issues == []
    assert result.middle_page_count == 1
    assert result.content_list_page_count == 0
