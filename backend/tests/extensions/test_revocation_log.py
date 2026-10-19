"""L47: revocation as an append-only mark stream."""

from __future__ import annotations

import json
import multiprocessing as mp
import pathlib

import pytest

from app.extensions import ExtensionError
from app.extensions.evidence.revocation_log import (
    RESTORE,
    REVOKE,
    append_mark,
    fold_marks,
    read_marks,
)


def mark(path, action=REVOKE, cid="EV-1", **overrides):
    kwargs = {
        "action": action,
        "candidate_evidence_id": cid,
        "reviewer": "E12345",
        "review_decision_ref": "RV-1",
        "reason": "r",
        "at": "2026-10-19T12:00:00+08:00",
        "previous_state": "ACCEPTED",
        "previous_decision_ref": "RV-OLD",
    }
    kwargs.update(overrides)
    return append_mark(path, **kwargs)


def test_revoke_appends_one_mark_and_folds_to_stale(tmp_path):
    log = tmp_path / "revocations.jsonl"
    written = mark(log)

    assert written is not None and written.seq == 1
    state = fold_marks(read_marks(log))["EV-1"]
    assert state["state"] == "STALE"
    assert state["review_decision_ref"] == "RV-1"


def test_restore_returns_to_the_recorded_state_and_ref(tmp_path):
    log = tmp_path / "revocations.jsonl"
    mark(log)
    mark(log, action=RESTORE, review_decision_ref="RV-2")

    state = fold_marks(read_marks(log))["EV-1"]
    assert state["state"] == "ACCEPTED"
    assert state["review_decision_ref"] == "RV-OLD"


def test_a_repeated_action_does_not_write_a_second_line(tmp_path):
    log = tmp_path / "revocations.jsonl"
    mark(log)
    assert mark(log) is None, "two identical revokes would read as two decisions"
    assert len(read_marks(log)) == 1


def test_sequence_numbers_are_dense_and_ordered(tmp_path):
    log = tmp_path / "revocations.jsonl"
    for index in range(5):
        mark(log, cid=f"EV-{index}")
    assert [item["seq"] for item in read_marks(log)] == [1, 2, 3, 4, 5]


def test_an_unknown_action_is_refused_before_anything_is_written(tmp_path):
    log = tmp_path / "revocations.jsonl"
    with pytest.raises(ExtensionError):
        mark(log, action="DELETE")
    assert read_marks(log) == []


def test_a_torn_line_is_refused_not_skipped(tmp_path):
    log = tmp_path / "revocations.jsonl"
    mark(log)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "act')
    with pytest.raises(ExtensionError) as excinfo:
        read_marks(log)
    assert "not valid JSON" in str(excinfo.value)


def _writer(path: str, worker: int, count: int) -> None:
    for index in range(count):
        append_mark(
            path,
            action=REVOKE,
            candidate_evidence_id=f"W{worker}-{index}",
            reviewer="E12345",
            review_decision_ref="RV-1",
            reason="r",
            at="2026-10-19T12:00:00+08:00",
        )


def test_two_processes_appending_get_unique_sequences(tmp_path):
    """Without the lock, two writers read the same last seq and hand out duplicates."""
    log = str(tmp_path / "revocations.jsonl")
    workers = [mp.Process(target=_writer, args=(log, worker, 25)) for worker in range(2)]
    for process in workers:
        process.start()
    for process in workers:
        process.join()
        assert process.exitcode == 0

    marks = read_marks(log)
    assert len(marks) == 50
    assert [item["seq"] for item in marks] == list(range(1, 51))
    assert len({item["candidate_evidence_id"] for item in marks}) == 50


def test_the_fold_is_the_only_source_of_current_state(tmp_path):
    log = tmp_path / "revocations.jsonl"
    mark(log)
    mark(log, action=RESTORE)
    mark(log, action=REVOKE)
    state = fold_marks(read_marks(log))["EV-1"]
    assert state["state"] == "STALE"
    assert state["seq"] == 3
