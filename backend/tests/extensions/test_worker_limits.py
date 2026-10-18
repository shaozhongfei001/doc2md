"""T401: worker limits (lock / budget / weights) and the PP-StructureV3 adapter.

Both halves guard the same thing — a worker that cannot prove it is running under
control. The adapter refuses schema drift instead of returning an empty table; the
limits refuse a second writer and unbounded work instead of degrading quietly.
"""

from __future__ import annotations

import hashlib
import time

import pytest

from app.extensions import ExtensionError
from app.extensions.adapters.paddle_structure import normalise_response
from app.extensions.worker.limits import (
    WorkerBudget,
    WorkerLock,
    verify_weights,
)

PAGE_HTML = (
    "<table>"
    "<tr><th colspan='2'>金额</th></tr>"
    "<tr><td>本金</td><td>(1,234)</td></tr>"
    "</table>"
)


def response(**overrides) -> dict:
    payload = {
        "table_res_list": [
            {
                "pred_html": PAGE_HTML,
                "cell_box_list": [
                    [[0, 0], [100, 0], [100, 20], [0, 20]],
                    [[0, 20], [50, 20], [50, 40], [0, 40]],
                    [[50, 20], [100, 20], [100, 40], [50, 40]],
                ],
                "table_ocr_pred": {
                    "rec_polys": [
                        [[0, 20], [50, 20], [50, 40], [0, 40]],
                        [[50, 20], [100, 20], [100, 40], [50, 40]],
                    ],
                    "rec_texts": ["本金", "(1,234)"],
                    "rec_scores": [0.98, 0.91],
                },
            }
        ]
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------- worker lock
def test_second_worker_cannot_take_the_lock(tmp_path):
    lock_path = tmp_path / "worker.lock"
    with WorkerLock(lock_path):
        with pytest.raises(ExtensionError) as excinfo:
            with WorkerLock(lock_path):
                pass
    assert excinfo.value.code == "E_LOCK_BUSY"


def test_lock_is_released_and_carries_the_pid(tmp_path):
    lock_path = tmp_path / "worker.lock"
    with WorkerLock(lock_path):
        assert lock_path.read_text().isdigit()
    assert not lock_path.exists()
    with WorkerLock(lock_path):  # re-acquirable after release
        assert lock_path.exists()


# ---------------------------------------------------------------------- budget
def test_page_budget_stops_before_the_next_page():
    budget = WorkerBudget(max_pages=2, max_requests=10)
    budget.consume(pages=2)
    with pytest.raises(ExtensionError) as excinfo:
        budget.consume(pages=1)
    assert excinfo.value.code == "E_ENGINE_UNAVAILABLE"
    assert excinfo.value.detail["budget"] == "max_pages"


def test_request_budget_is_separate_from_pages():
    budget = WorkerBudget(max_pages=10, max_requests=1)
    budget.consume(pages=1, requests=1)
    with pytest.raises(ExtensionError) as excinfo:
        budget.consume(requests=1)
    assert excinfo.value.detail["budget"] == "max_requests"


def test_wall_clock_budget_uses_the_timeout_code():
    budget = WorkerBudget(max_pages=10, max_seconds=1.0)
    budget.started_at = time.monotonic() - 5.0
    with pytest.raises(ExtensionError) as excinfo:
        budget.consume(pages=1)
    assert excinfo.value.code == "E_ENGINE_TIMEOUT"
    assert excinfo.value.detail["budget"] == "max_seconds"


def test_budget_accumulates_and_reports_remaining():
    budget = WorkerBudget(max_pages=5, max_requests=5, max_seconds=60.0)
    budget.consume(pages=2, requests=3)
    remaining = budget.remaining()
    assert remaining["pages"] == 3
    assert remaining["requests"] == 2
    assert remaining["seconds"] <= 60.0


@pytest.mark.parametrize("kwargs", [{"max_pages": 0}, {"max_requests": -1}, {"max_seconds": 0}])
def test_invalid_budget_is_refused(kwargs):
    with pytest.raises(ValueError):
        WorkerBudget(**kwargs)


# --------------------------------------------------------------------- weights
def test_missing_weights_mean_the_worker_cannot_start(tmp_path):
    with pytest.raises(ExtensionError) as excinfo:
        verify_weights(tmp_path / "missing.pdparams", "a" * 64)
    assert excinfo.value.code == "E_ENGINE_UNAVAILABLE"
    assert "never downloads" in str(excinfo.value)


def test_weight_digest_mismatch_reports_both_digests(tmp_path):
    weights = tmp_path / "model.pdparams"
    weights.write_bytes(b"not-the-model")
    with pytest.raises(ExtensionError) as excinfo:
        verify_weights(weights, "b" * 64)
    detail = excinfo.value.detail
    assert detail["expected_sha256"] == "b" * 64
    assert detail["actual_sha256"] == hashlib.sha256(b"not-the-model").hexdigest()


def test_matching_weight_digest_is_returned(tmp_path):
    weights = tmp_path / "model.pdparams"
    weights.write_bytes(b"model-bytes")
    digest = hashlib.sha256(b"model-bytes").hexdigest()
    assert verify_weights(weights, digest) == digest


def test_malformed_expected_digest_is_a_programming_error(tmp_path):
    weights = tmp_path / "m.bin"
    weights.write_bytes(b"x")
    with pytest.raises(ValueError):
        verify_weights(weights, "not-a-digest")


# ------------------------------------------------------------- paddle adapter
def test_html_cells_keep_spans_and_text():
    result = normalise_response(response())
    table = result.tables[0]
    assert [cell["text"] for cell in table.cells] == ["金额", "本金", "(1,234)"]
    assert table.cells[0]["colspan"] == 2
    assert table.cells[1]["row"] == 1 and table.cells[2]["col"] == 1


def test_boxes_are_not_attached_by_list_order_by_default():
    result = normalise_response(response())
    table = result.tables[0]
    assert table.box_association == "UNAVAILABLE"
    assert "polygon" not in table.cells[0]
    assert "CELL_BOX_ORDER_UNVERIFIED" in [issue["code"] for issue in result.issues]
    # No cells have polygons, so no token binding may be claimed.
    assert table.binding is None


def test_documented_order_binds_boxes_only_when_counts_agree():
    result = normalise_response(response(), cell_box_order="DOCUMENTED")
    table = result.tables[0]
    assert table.box_association == "DOCUMENTED_ORDER"
    assert table.cells[0]["polygon"] == [[0.0, 0.0], [100.0, 0.0], [100.0, 20.0], [0.0, 20.0]]


def test_documented_order_with_mismatched_counts_is_refused():
    payload = response()
    payload["table_res_list"][0]["cell_box_list"] = [[[0, 0], [10, 0], [10, 10], [0, 10]]]
    result = normalise_response(payload, cell_box_order="DOCUMENTED")
    assert result.tables[0].box_association == "DOCUMENTED_COUNT_MISMATCH"
    assert "CELL_BOX_COUNT_MISMATCH" in [issue["code"] for issue in result.issues]


def test_token_text_is_preserved_verbatim():
    result = normalise_response(response(), cell_box_order="DOCUMENTED")
    texts = [token["text"] for token in result.tables[0].tokens]
    assert texts == ["本金", "(1,234)"]


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda p: p.pop("table_res_list"), "table_res_list"),
        (lambda p: p["table_res_list"][0].pop("pred_html"), "pred_html"),
        (lambda p: p["table_res_list"][0]["table_ocr_pred"]["rec_texts"].pop(), "disagree in length"),
        (lambda p: p["table_res_list"][0]["table_ocr_pred"]["rec_scores"].__setitem__(0, 1.4), "[0, 1]"),
        (lambda p: p["table_res_list"][0].__setitem__("pred_html", "   "), "pred_html"),
    ],
)
def test_schema_drift_is_reported_not_swallowed(mutate, match):
    payload = response()
    mutate(payload)
    with pytest.raises(ExtensionError) as excinfo:
        normalise_response(payload)
    assert excinfo.value.code == "E_ENGINE_SCHEMA_DRIFT"
    assert match in str(excinfo.value)


def test_empty_html_keeps_an_issue_instead_of_dropping_the_table():
    payload = response()
    payload["table_res_list"][0]["pred_html"] = "<table></table>"
    payload["table_res_list"][0]["cell_box_list"] = []
    result = normalise_response(payload)
    assert len(result.tables) == 1
    assert "EMPTY_TABLE_HTML" in [issue["code"] for issue in result.issues]


def test_every_output_carries_the_lock_caveat():
    result = normalise_response(response())
    assert result.tables[0].to_dict()["engine_lock_note"].startswith("field names follow")
