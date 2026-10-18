"""T404 / ``PI-F04-05``: engine cell text first, geometry only as a candidate.

The tests are mostly refusals: a token that is *nearly* inside a cell is exactly
where an alignment layer silently produces a plausible but wrong number.
"""

from __future__ import annotations

import pytest

from app.extensions import ExtensionError
from app.extensions.tables.ocr_alignment import (
    align_tokens,
    coverage,
    intersection_area,
)


def rect(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


LEFT = rect(0, 0, 100, 100)
RIGHT = rect(100, 0, 200, 100)


def cells(text=None, engine_confidence=None, polygon=None) -> list[dict]:
    """Two cells: L is the one under test, R is the untouched neighbour."""
    return [
        {
            "cell_id": "L",
            "polygon": polygon if polygon is not None else LEFT,
            "text": text,
            "engine_confidence": engine_confidence,
        },
        {"cell_id": "R", "polygon": RIGHT, "text": None, "engine_confidence": None},
    ]


def token(token_id: str = "t1", polygon=None, text: str = "100", **extra) -> dict:
    payload = {
        "token_id": token_id,
        "text": text,
        "polygon": polygon if polygon is not None else rect(10, 10, 90, 90),
    }
    payload.update(extra)
    return payload


def by_id(result) -> dict[str, dict]:
    return {cell["cell_id"]: cell for cell in result.cells}


def codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


# --------------------------------------------------------------- geometry math
def test_intersection_area_and_coverage_of_a_contained_token():
    assert intersection_area(rect(10, 10, 90, 90), LEFT) == pytest.approx(6400.0)
    assert coverage(rect(10, 10, 90, 90), LEFT) == pytest.approx(1.0)


def test_coverage_of_a_half_outside_token_is_half():
    assert coverage(rect(50, 0, 150, 100), LEFT) == pytest.approx(0.5)
    assert coverage(rect(50, 0, 150, 100), RIGHT) == pytest.approx(0.5)


# ------------------------------------------------------------------ engine text
def test_engine_cell_text_is_kept_and_the_conflict_is_recorded():
    result = align_tokens(cells(**{"text": "1,000"}), [token()])
    cell = by_id(result)["L"]
    assert cell["text"] == "1,000"
    assert cell["text_alignment"] == "ENGINE_CELL_TEXT"
    assert cell["token_ids"] == []
    assert "ENGINE_TEXT_OVERRIDES_OCR" in codes(result)
    assert cell["candidates"] == ["t1"]


def test_engine_confidence_is_never_estimated():
    result = align_tokens(cells(**{"text": "1,000"}), [token()])
    assert by_id(result)["L"]["engine_confidence"] is None
    result2 = align_tokens(
        cells(**{"text": "1,000", "engine_confidence": 0.87}), [token()]
    )
    assert by_id(result2)["L"]["engine_confidence"] == pytest.approx(0.87)


# ----------------------------------------------------------- geometric binding
def test_single_covering_token_is_bound_with_verbatim_text():
    result = align_tokens(cells(), [token(text="-1,234", confidence=0.93)])
    cell = by_id(result)["L"]
    assert cell["text"] == "-1,234"
    assert cell["text_origin"] == "ENGINE_OCR"
    assert cell["text_alignment"] == "GEOMETRIC_CANDIDATE"
    assert cell["token_ids"] == ["t1"]
    assert cell["engine_confidence"] == pytest.approx(0.93)


def test_token_text_is_not_cleaned():
    for raw in ("(500)", "12%", "3,000.50万元"):
        result = align_tokens(cells(), [token(text=raw)])
        assert by_id(result)["L"]["text"] == raw


def test_token_below_coverage_threshold_is_not_bound():
    result = align_tokens(cells(), [token(polygon=rect(50, 0, 150, 100))])
    cell = by_id(result)["L"]
    assert cell["text"] is None
    assert cell["state"] == "EMPTY"
    assert "NO_TOKEN_COVERAGE" in codes(result)


def test_two_tokens_covering_the_same_cell_are_ambiguous():
    result = align_tokens(
        cells(),
        [token("t1", polygon=rect(5, 5, 50, 95)), token("t2", polygon=rect(55, 5, 95, 95))],
    )
    cell = by_id(result)["L"]
    assert cell["text"] is None
    assert cell["state"] == "AMBIGUOUS"
    assert sorted(cell["candidates"]) == ["t1", "t2"]
    assert "AMBIGUOUS_TOKEN_ASSIGNMENT" in codes(result)


def test_clear_winner_is_bound_and_the_runner_up_is_only_a_candidate():
    # t2 is 0.81 inside L (30 of its 37px width) and 0.19 inside R, so it qualifies
    # in L but loses to t1 by more than the margin.
    result = align_tokens(
        cells(),
        [token("t1", polygon=rect(0, 0, 90, 100)), token("t2", polygon=rect(70, 0, 107, 100))],
    )
    cell = by_id(result)["L"]
    assert cell["text_origin"] == "ENGINE_OCR"
    assert cell["token_ids"] == ["t1"]
    assert cell["candidates"] == ["t1", "t2"]


def test_token_spanning_two_cells_is_never_split():
    # With the default threshold a 50/50 token simply covers neither cell...
    result = align_tokens(cells(), [token(polygon=rect(50, 0, 150, 100))])
    assert by_id(result)["L"]["text"] is None
    assert by_id(result)["R"]["text"] is None
    assert result.to_cell_updates()[0]["text"] is None

    # ...and with a threshold low enough to qualify in both, it is refused outright.
    loose = align_tokens(cells(), [token(polygon=rect(50, 0, 150, 100))], coverage_threshold=0.4)
    assert "TOKEN_SPANS_CELLS" in codes(loose)
    assert by_id(loose)["L"]["text"] is None and by_id(loose)["R"]["text"] is None


def test_no_tokens_leaves_every_cell_null():
    result = align_tokens(cells(), [])
    assert [cell["state"] for cell in result.cells] == ["EMPTY", "EMPTY"]
    assert all(cell["text"] is None for cell in result.cells)


# ---------------------------------------------------------------- output shape
def test_cell_updates_carry_only_contract_fields():
    result = align_tokens(cells(), [token()])
    update = result.to_cell_updates()[0]
    assert set(update) == {
        "cell_id",
        "text",
        "text_origin",
        "text_alignment",
        "engine_confidence",
        "token_ids",
    }
    assert update["text_origin"] in {"SOURCE_XML", "ENGINE_OCR", "HUMAN_PATCH"}
    assert update["text_alignment"] in {
        "NATIVE",
        "ENGINE_CELL_TEXT",
        "GEOMETRIC_CANDIDATE",
        "HUMAN_CONFIRMED",
        "UNKNOWN",
    }


# -------------------------------------------------------------- input refusals
@pytest.mark.parametrize(
    "bad_tokens, match",
    [
        ([{"token_id": "t1", "text": "1", "polygon": rect(0, 0, 10, 10)}, {"token_id": "t1", "text": "2", "polygon": rect(0, 0, 10, 10)}], "duplicate token_id"),
        ([{"token_id": "", "text": "1", "polygon": rect(0, 0, 10, 10)}], "token_id"),
        ([{"token_id": "t1", "text": "", "polygon": rect(0, 0, 10, 10)}], "non-empty"),
        ([{"token_id": "t1", "text": "1", "polygon": rect(0, 0, 10, 0)}], "zero area"),
        ([{"token_id": "t1", "text": "1", "polygon": [[0, 0], [1, 0], [1, 1]]}], "four-corner"),
        ([{"token_id": "t1", "text": "1", "polygon": rect(0, 0, 10, 10), "confidence": 1.4}], "[0, 1]"),
    ],
)
def test_malformed_tokens_are_refused(bad_tokens, match):
    with pytest.raises(ExtensionError) as excinfo:
        align_tokens(cells(), bad_tokens)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert match in str(excinfo.value)


def test_duplicate_cell_ids_are_refused():
    with pytest.raises(ExtensionError) as excinfo:
        align_tokens([cells()[0], cells()[0]], [])
    assert "duplicate cell_id" in str(excinfo.value)


@pytest.mark.parametrize("threshold", [0.0, 1.5, "0.8"])
def test_invalid_coverage_threshold_is_refused(threshold):
    with pytest.raises(ExtensionError) as excinfo:
        align_tokens(cells(), [token()], coverage_threshold=threshold)
    assert "coverage_threshold" in str(excinfo.value)


def test_invalid_margin_threshold_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        align_tokens(cells(), [token()], margin_threshold=1.0)
    assert "margin_threshold" in str(excinfo.value)
