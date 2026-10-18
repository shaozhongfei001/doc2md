"""T502 / ``PI-F04-07``: continuation is a *candidate*, and conflicts never join.

The dangerous failure mode here is the opposite of a crash: two unrelated tables
get merged into one "authoritative" table and everything downstream inherits a
wrong denominator. So the negatives below assert that similar-looking tables stay
``SEPARATE`` / ``NEEDS_REVIEW``.
"""

from __future__ import annotations

import pytest

from app.extensions import ExtensionError
from app.extensions.tables.continuation import (
    CONTINUATION_CODE,
    propose_continuation,
)

EVIDENCE = ["docx:tbl:1", "docx:tbl:2"]


def page(
    table_id: str,
    *,
    page_index: int = 1,
    caption: str = "表1 贷款余额",
    unit_raw: str = "万元",
    currency: str = "人民币",
    period: str = "2025H1",
    table_type: str = "LOAN_BALANCE",
    column_paths: tuple[tuple[str, ...], ...] = (("h0",), ("h1",)),
    column_count: int = 2,
    column_widths: tuple[float, ...] = (60.0, 40.0),
    repeats_header: bool | None = True,
    ends_at_page_bottom: bool | None = True,
    starts_at_page_top: bool | None = True,
) -> dict:
    return {
        "table_id": table_id,
        "page_index": page_index,
        "caption": caption,
        "unit_raw": unit_raw,
        "currency": currency,
        "period": period,
        "table_type": table_type,
        "column_paths": column_paths,
        "column_count": column_count,
        "column_widths": column_widths,
        "repeats_header": repeats_header,
        "ends_at_page_bottom": ends_at_page_bottom,
        "starts_at_page_top": starts_at_page_top,
    }


def codes(result) -> list[str]:
    return [reason.code for reason in result.reasons]


# ------------------------------------------------------------------ happy path
def test_adjacent_compatible_pages_yield_a_candidate_not_a_join():
    result = propose_continuation(
        page("T1", page_index=1),
        page("T2", page_index=2),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "JOIN_CANDIDATE"
    assert result.continuation["review_decision_ref"] is None
    assert result.continuation["previous_table_id"] == "T1"
    assert result.continuation["next_table_id"] == "T2"
    assert result.continuation["relation_id"] == "CONT-T1--T2"
    for dimension in ("page_adjacency", "caption", "unit_raw", "period", "column_paths"):
        assert dimension in result.continuation["compared_dimensions"]


def test_two_originals_are_never_merged_into_one_payload():
    result = propose_continuation(page("T1"), page("T2", page_index=2), evidence_ids=EVIDENCE)
    # The payload describes a *relation*; it carries no combined table and no cells.
    assert set(result.continuation) == {
        "relation_id",
        "previous_table_id",
        "next_table_id",
        "decision",
        "compared_dimensions",
        "evidence_ids",
        "review_decision_ref",
    }


def test_headerless_continuation_page_is_only_a_candidate():
    result = propose_continuation(
        page("T1"),
        page("T2", page_index=2, column_paths=(), repeats_header=False),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "JOIN_CANDIDATE"
    assert "NEXT_PAGE_HEADERLESS" in codes(result)


def test_review_decision_is_required_for_confirmed_join():
    without = propose_continuation(page("T1"), page("T2", page_index=2), evidence_ids=EVIDENCE)
    with_ref = propose_continuation(
        page("T1"),
        page("T2", page_index=2),
        evidence_ids=EVIDENCE,
        review_decision_ref="RV-0007",
    )
    assert without.decision == "JOIN_CANDIDATE"
    assert with_ref.decision == "CONFIRMED_JOIN"
    assert with_ref.continuation["review_decision_ref"] == "RV-0007"


# ------------------------------------------------------- conflicts never join
@pytest.mark.parametrize(
    "field_name, other_value, expected_code",
    [
        ("currency", "美元", "CURRENCY_CONFLICT"),
        ("unit_raw", "元", "UNIT_RAW_CONFLICT"),
        ("period", "2025H2", "PERIOD_CONFLICT"),
    ],
)
def test_data_conflicts_are_separate(field_name, other_value, expected_code):
    """Currency, unit and period are the data; two different values cannot be one table."""
    result = propose_continuation(
        page("T1"),
        page("T2", page_index=2, **{field_name: other_value}),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "SEPARATE"
    assert expected_code in codes(result)


def test_a_real_caption_mismatch_is_reviewable_not_separate():
    """A caption is a label, not the data.

    ``qa-l20`` found the severity inverted: a caption differing by one character was
    judged SEPARATE (no review possible), while *changing column definitions* — much
    stronger evidence of a different table — only reached NEEDS_REVIEW.
    """
    result = propose_continuation(
        page("T1"),
        page("T2", page_index=2, caption="表2 存款余额"),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "NEEDS_REVIEW"
    assert "CAPTION_CONFLICT" in codes(result)
    conflict = [r for r in result.reasons if r.code == "CAPTION_CONFLICT"][0]
    assert conflict.severity == "SOFT"
    assert result.reasons  # the comparison trail is still reported


@pytest.mark.parametrize(
    "continued_caption",
    ["续表1 贷款余额", "（续）表1 贷款余额", "表1 贷款余额（续）"],
)
def test_a_continuation_marker_is_the_same_label(continued_caption):
    """The commonest real shape of a long table on the next page must not be rejected."""
    result = propose_continuation(
        page("T1"),
        page("T2", page_index=2, caption=continued_caption),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "JOIN_CANDIDATE"
    assert "CAPTION_CONTINUATION_MARKER" in codes(result)
    assert "CAPTION_CONFLICT" not in codes(result)


def test_a_different_label_still_blocks_a_join():
    # Normalisation must not become "all captions match".
    result = propose_continuation(
        page("T1"),
        page("T2", page_index=2, caption="表9 其他"),
        evidence_ids=EVIDENCE,
    )
    assert result.decision != "JOIN_CANDIDATE"


def test_same_column_count_is_not_enough_to_join():
    result = propose_continuation(
        page("T1"),
        page("T2", page_index=2, unit_raw="元"),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "SEPARATE"


def test_column_count_mismatch_is_separate():
    # Paths are left unknown on purpose: only the width of the grid differs, which
    # is a separate-table signal rather than a header ambiguity.
    result = propose_continuation(
        page("T1"),
        page("T2", page_index=2, column_count=3, column_paths=None),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "SEPARATE"
    assert "COLUMN_COUNT_MISMATCH" in codes(result)


def test_changed_column_paths_are_needs_review():
    result = propose_continuation(
        page("T1"),
        page("T2", page_index=2, column_paths=(("x0",), ("x1",))),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "NEEDS_REVIEW"
    assert "COLUMN_PATH_CHANGED" in codes(result)


def test_column_width_drift_is_needs_review():
    result = propose_continuation(
        page("T1"),
        page("T2", page_index=2, column_widths=(30.0, 70.0)),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "NEEDS_REVIEW"
    assert "COLUMN_WIDTH_DRIFT" in codes(result)


def test_width_comparison_uses_ratios_not_absolute_scale():
    # Same layout at a different scale must not be reported as drift.
    result = propose_continuation(
        page("T1", column_widths=(60.0, 40.0)),
        page("T2", page_index=2, column_widths=(120.0, 80.0)),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "JOIN_CANDIDATE"


def test_unknown_unit_on_one_side_is_needs_review_not_a_match():
    result = propose_continuation(
        page("T1"),
        page("T2", page_index=2, unit_raw=None),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "NEEDS_REVIEW"
    assert "UNIT_RAW_UNKNOWN" in codes(result)


def test_unknown_page_cannot_establish_adjacency():
    result = propose_continuation(
        page("T1"),
        page("T2", page_index=None),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "NEEDS_REVIEW"
    assert "PAGE_UNKNOWN" in codes(result)


# ------------------------------------------------------- structural refusals
def test_same_page_tables_are_not_chained():
    result = propose_continuation(
        page("T1", page_index=3),
        page("T2", page_index=3),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "SEPARATE"
    assert "SAME_PAGE_NOT_CHAINED" in codes(result)


def test_non_adjacent_pages_are_separate():
    result = propose_continuation(
        page("T1", page_index=1),
        page("T2", page_index=4),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "SEPARATE"
    assert "PAGES_NOT_ADJACENT" in codes(result)


def test_previous_table_ending_mid_page_is_separate():
    result = propose_continuation(
        page("T1", ends_at_page_bottom=False),
        page("T2", page_index=2),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "SEPARATE"
    assert "PREVIOUS_TABLE_ENDS_MID_PAGE" in codes(result)


def test_different_source_version_is_separate():
    result = propose_continuation(
        page("T1"),
        page("T2", page_index=2),
        evidence_ids=EVIDENCE,
        same_source_version=False,
    )
    assert result.decision == "SEPARATE"
    assert "SOURCE_VERSION_DIFFERS" in codes(result)


def test_different_table_type_is_separate():
    result = propose_continuation(
        page("T1"),
        page("T2", page_index=2, table_type="DEPOSIT_BALANCE"),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "SEPARATE"
    assert "TYPE_MISMATCH" in codes(result)


# -------------------------------------------------------------- input refusals
def test_relation_without_evidence_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        propose_continuation(page("T1"), page("T2", page_index=2), evidence_ids=[])
    assert excinfo.value.code == CONTINUATION_CODE
    assert "evidence" in str(excinfo.value)


@pytest.mark.parametrize(
    "bad_next, match",
    [
        ({"page_index": 2}, "table_id"),
        ({"table_id": "T2", "page_index": "2"}, "page_index"),
        ({"table_id": "T2", "column_count": 0}, "column_count"),
        ({"table_id": "T2", "column_paths": "h0"}, "column_paths"),
        ({"table_id": "T2", "column_paths": (("h0", " "),)}, "column_paths"),
        ({"table_id": "T2", "column_widths": (0.0,)}, "column_widths"),
        ({"table_id": "T2", "repeats_header": "yes"}, "repeats_header"),
    ],
)
def test_malformed_signatures_are_refused(bad_next, match):
    with pytest.raises(ExtensionError) as excinfo:
        propose_continuation(page("T1"), bad_next, evidence_ids=EVIDENCE)
    assert excinfo.value.code == CONTINUATION_CODE
    assert match in str(excinfo.value)


def test_normalisation_does_not_mangle_a_caption_that_merely_contains_the_character():
    """TL probe: stripping every '续' would turn '持续经营说明' into '持经营说明'.

    Only affixes are removed, so a caption whose *content* contains the marker
    character is left alone.
    """
    from app.extensions.tables.continuation import _normalise_caption

    assert _normalise_caption("持续经营说明") == "持续经营说明"
    assert _normalise_caption("续贷余额") == "续贷余额"
    assert _normalise_caption("表1 贷款余额") == "表1贷款余额"
    assert _normalise_caption("续表1 贷款余额") == "表1贷款余额"


def test_a_hard_conflict_still_wins_over_a_soft_one():
    # Downgrading caption to NEEDS_REVIEW must not soften the data comparisons: a
    # row that also differs in currency is still SEPARATE.
    result = propose_continuation(
        page("T1"),
        page("T2", page_index=2, caption="表2 存款余额", currency="美元"),
        evidence_ids=EVIDENCE,
    )
    assert result.decision == "SEPARATE"
    assert "CURRENCY_CONFLICT" in codes(result)
