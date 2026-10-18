"""T704 / ``PI-F04-09``: outbox events are idempotent and never fake an ACK."""

from __future__ import annotations

import pytest

from app.extensions import ExtensionError
from app.extensions.release.consumers import Outbox


def outbox_with_event(**overrides) -> Outbox:
    outbox = Outbox()
    kwargs = {
        "event_id": "E1",
        "generation_id": "G1",
        "event_type": "REBUILD_TABLE",
        "payload": {"table_id": "T1"},
        "target": "search-index",
    }
    kwargs.update(overrides)
    outbox.enqueue(**kwargs)
    return outbox


def test_event_starts_pending():
    outbox = outbox_with_event()
    assert outbox.events["E1"]["status"] == "PENDING"


def test_replaying_the_same_event_is_idempotent():
    outbox = outbox_with_event()
    again = outbox.enqueue(
        event_id="E1",
        generation_id="G1",
        event_type="REBUILD_TABLE",
        payload={"table_id": "T1"},
        target="search-index",
    )
    assert len(outbox.events) == 1
    assert again["payload_digest"] == outbox.events["E1"]["payload_digest"]


def test_same_id_with_different_content_is_a_conflict():
    outbox = outbox_with_event()
    with pytest.raises(ExtensionError) as excinfo:
        outbox.enqueue(
            event_id="E1",
            generation_id="G1",
            event_type="REBUILD_TABLE",
            payload={"table_id": "T2"},
        )
    assert excinfo.value.code == "E_VERSION_CONFLICT"


def test_ack_marks_the_event_delivered():
    outbox = outbox_with_event()
    record = outbox.ack("E1", generation_id="G1", consumer_version="idx-1.2", ack_token="tok-1")
    assert record["status"] == "ACKED"
    assert outbox.events["E1"]["status"] == "ACKED"


def test_replayed_ack_is_idempotent():
    outbox = outbox_with_event()
    first = outbox.ack("E1", generation_id="G1", consumer_version="idx-1.2", ack_token="tok-1")
    again = outbox.ack("E1", generation_id="G1", consumer_version="idx-1.2", ack_token="tok-1")
    assert first == again


def test_a_second_consumer_cannot_claim_the_same_event():
    outbox = outbox_with_event()
    outbox.ack("E1", generation_id="G1", consumer_version="idx-1.2", ack_token="tok-1")
    with pytest.raises(ExtensionError) as excinfo:
        outbox.ack("E1", generation_id="G1", consumer_version="idx-2.0", ack_token="tok-2")
    assert "different token" in str(excinfo.value)


def test_late_ack_for_an_older_generation_is_refused():
    outbox = outbox_with_event(generation_id="G2")
    with pytest.raises(ExtensionError) as excinfo:
        outbox.ack("E1", generation_id="G1", consumer_version="idx-1.2", ack_token="tok-1")
    assert excinfo.value.code == "E_VERSION_CONFLICT"
    assert excinfo.value.detail["ack_generation"] == "G1"


def test_ack_for_an_unknown_event_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        Outbox().ack("E404", generation_id="G1", consumer_version="v", ack_token="t")
    assert "cannot create an event" in str(excinfo.value)


def test_unconnected_target_is_reported_not_connected_and_never_acked():
    outbox = outbox_with_event()
    report = outbox.report(connected={"other-system"})
    row = report["events"][0]
    assert row["status"] == "NOT_CONNECTED"
    assert report["acked"] == 0
    assert report["not_connected"] == 1
    # Nothing was fabricated: the stored event is still pending.
    assert outbox.events["E1"]["status"] == "PENDING"


def test_report_counts_pending_acked_and_unconnected():
    outbox = outbox_with_event()
    outbox.enqueue(
        event_id="E2", generation_id="G1", event_type="REBUILD_TABLE", payload={"n": 2}, target="search-index"
    )
    outbox.ack("E1", generation_id="G1", consumer_version="idx-1.2", ack_token="tok-1")
    report = outbox.report(connected={"search-index"})
    assert report["acked"] == 1
    assert report["pending"] == 1
    assert report["not_connected"] == 0
