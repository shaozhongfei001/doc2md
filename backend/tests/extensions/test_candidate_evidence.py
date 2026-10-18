"""T701 / ``PI-F05-06``: candidates carry provenance, and every read re-checks access."""

from __future__ import annotations

import pytest

from app.extensions import ExtensionError
from app.extensions.evidence.candidates import (
    AccessGuard,
    mark_stale,
    read_candidate,
    register_candidate,
)

DIGEST = "a" * 64


def locator(**overrides) -> dict:
    payload = {
        "kind": "PAGE_REGION",
        "source_sha256": DIGEST,
        "geometry_id": "G1",
        "page_no": 1,
        "level": "REGION_VERIFIED",
        "normalized_polygon": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2], [0.1, 0.2]],
    }
    payload.update(overrides)
    return payload


def candidate(**overrides) -> dict:
    kwargs = {
        "candidate_evidence_id": "EV-1",
        "source_version_id": "SV-1",
        "locator": locator(),
        "observation_class": "SOURCE_CONTENT",
    }
    kwargs.update(overrides)
    return register_candidate(**kwargs)


def guard(allowed: bool = True, source: str = "SV-1") -> AccessGuard:
    return AccessGuard(source_version_id=source, allowed=lambda: allowed, revision="R1")


# ------------------------------------------------------------- registration
def test_candidate_starts_unreviewed_and_keeps_its_class():
    built = candidate(observation_class="MODEL_INTERPRETATION")
    assert built["review_state"] == "UNREVIEWED"
    assert built["observation_class"] == "MODEL_INTERPRETATION"
    assert built["review_decision_ref"] is None


def test_unknown_observation_class_is_refused():
    with pytest.raises(ExtensionError):
        candidate(observation_class="GUESS")


def test_locator_must_name_a_kind_and_the_source_digest():
    with pytest.raises(ExtensionError) as excinfo:
        candidate(locator={"source_sha256": DIGEST})
    assert "locator.kind" in str(excinfo.value)

    with pytest.raises(ExtensionError) as excinfo:
        candidate(locator=locator(source_sha256="short"))
    assert "source_sha256" in str(excinfo.value)


def test_missing_locator_is_refused():
    with pytest.raises(ExtensionError):
        candidate(locator=None)


# ------------------------------------------------------------------- reads
def test_read_is_allowed_while_the_source_is_readable():
    read = read_candidate(candidate(), guard=guard(), purpose="export")
    assert read["candidate_evidence_id"] == "EV-1"


def test_read_is_refused_after_a_revocation():
    revoked = {"flag": True}
    active = AccessGuard(source_version_id="SV-1", allowed=lambda: revoked["flag"])
    candidate_entry = candidate()
    assert read_candidate(candidate_entry, guard=active)["candidate_evidence_id"] == "EV-1"
    revoked["flag"] = False  # the permission changes *between* reads
    with pytest.raises(ExtensionError) as excinfo:
        read_candidate(candidate_entry, guard=active)
    assert excinfo.value.code == "E_SCOPE_DENIED"


def test_guard_for_another_source_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        read_candidate(candidate(), guard=guard(source="SV-9"))
    assert "different source version" in str(excinfo.value)


def test_stale_candidate_is_not_exported_even_when_access_returns():
    stale = dict(candidate(), review_state="STALE")
    with pytest.raises(ExtensionError) as excinfo:
        read_candidate(stale, guard=guard(allowed=True))
    assert excinfo.value.code == "E_SCOPE_DENIED"


def test_read_requires_a_guard_object_not_a_boolean():
    with pytest.raises(ExtensionError) as excinfo:
        read_candidate(candidate(), guard=True)  # type: ignore[arg-type]
    assert "AccessGuard" in str(excinfo.value)


# ------------------------------------------------------------------- stale
def test_mark_stale_only_touches_the_revoked_source():
    entries = [
        candidate(candidate_evidence_id="EV-1"),
        candidate(candidate_evidence_id="EV-2", source_version_id="SV-2"),
    ]
    marked = mark_stale(entries, revoked_source_ids=["SV-2"])
    states = {item["candidate_evidence_id"]: item["review_state"] for item in marked}
    assert states == {"EV-1": "UNREVIEWED", "EV-2": "STALE"}
    # Nothing is deleted: a stale candidate still explains past output.
    assert len(marked) == 2


def test_revoked_sources_must_be_a_sequence():
    with pytest.raises(ExtensionError):
        mark_stale([candidate()], revoked_source_ids="SV-1")
