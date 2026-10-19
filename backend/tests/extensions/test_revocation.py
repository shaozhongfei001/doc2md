"""L40: the writer that turns L35's list into a state change."""

from __future__ import annotations

import copy

import pytest

from app.extensions import ExtensionError
from app.extensions.evidence.revocation import revoke_candidates
from app.extensions.review.authority import RESTRICTED, ReviewerPolicy


def registry() -> list[dict]:
    return [
        {"candidate_evidence_id": "EV-1", "review_state": "UNREVIEWED", "review_decision_ref": None},
        {"candidate_evidence_id": "EV-2", "review_state": "ACCEPTED", "review_decision_ref": "RV-OLD"},
        {"candidate_evidence_id": "EV-3", "review_state": "STALE", "review_decision_ref": "RV-OLD"},
    ]


def revoke(candidates, ids=("EV-1",), **overrides):
    kwargs = {
        "candidate_ids": list(ids),
        "review_decision_ref": "RV-1",
        "reviewer": "E12345",
        "reason": "1601 原始凭证已替换",
        "at": "2026-10-18T20:00:00+08:00",
    }
    kwargs.update(overrides)
    return revoke_candidates(candidates, **kwargs)


def test_revoking_sets_the_state_and_the_decision():
    updated, report = revoke(registry())

    assert report.revoked == ("EV-1",)
    assert report.changed is True
    first = updated[0]
    assert first["review_state"] == "STALE"
    assert first["review_decision_ref"] == "RV-1"
    assert first["revoked_by"] == "E12345"
    assert first["revoked_at"] == "2026-10-18T20:00:00+08:00"
    assert report.audit["reason"] == "1601 原始凭证已替换"
    assert report.audit["requested"] == 1


def test_revoking_twice_is_idempotent_and_reported():
    once, _ = revoke(registry())
    twice, report = revoke(once)

    assert report.revoked == ()
    assert report.already_revoked == ("EV-1",)
    assert report.changed is False
    assert twice == once, "a repeated call must not rewrite timestamps or add fields"


def test_an_unknown_id_is_reported_not_swallowed():
    """A typo must not read as success: the caller has to see what did not happen."""
    updated, report = revoke(registry(), ids=("EV-1", "EV-TYPO"))

    assert report.revoked == ("EV-1",)
    assert report.unknown == ("EV-TYPO",)
    assert updated[0]["review_state"] == "STALE"


def test_a_duplicate_id_is_deduplicated_and_reported():
    _, report = revoke(registry(), ids=("EV-1", "EV-1"))
    assert report.revoked == ("EV-1",)
    assert report.duplicates == ("EV-1",)


def test_revocation_requires_a_decision_and_a_reason():
    with pytest.raises(ExtensionError) as excinfo:
        revoke(registry(), review_decision_ref="  ")
    assert excinfo.value.code == "E_REVIEW_REQUIRED"

    with pytest.raises(ExtensionError) as excinfo:
        revoke(registry(), reason="")
    assert excinfo.value.code == "E_REVIEW_REQUIRED"


def test_the_input_registry_is_never_mutated():
    before = registry()
    snapshot = copy.deepcopy(before)

    updated, _ = revoke(before, ids=("EV-1", "EV-2"))

    assert before == snapshot, "revocation handed the caller's own objects back changed"
    assert updated is not before
    assert updated[0] is not before[0]


def test_a_malformed_batch_is_rejected_whole():
    """Half-revoked evidence is worse than untouched: the rest still reads as current."""
    broken = registry()
    broken.append("not-a-mapping")

    with pytest.raises(ExtensionError) as excinfo:
        revoke(broken, ids=("EV-1",))
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "candidates[3]" in str(excinfo.value)
    assert broken[0]["review_state"] == "UNREVIEWED", "nothing may change on a rejected batch"


def test_revocation_obeys_the_reviewer_policy():
    with pytest.raises(ExtensionError):
        revoke(
            registry(),
            policy=ReviewerPolicy(mode=RESTRICTED, allowed=("E99999",)),
            reviewer="---",
        )


def test_a_mapping_without_an_id_is_rejected_whole():
    """qa-l41 M9: the batch guard was being protected by an accident.

    ``test_a_malformed_batch_is_rejected_whole`` appends a *string*, which crashes at
    ``dict(item)`` — so the mutation "validate after copying, continue on bad entry"
    turned red through a ``ValueError``, not through the assertion. A malformed entry
    that is still a mapping (``{"no_id": 1}``) has to be refused on purpose: with the
    mutation in place it half-revokes and reports success.
    """
    broken = registry()
    broken.append({"no_id": 1})

    with pytest.raises(ExtensionError) as excinfo:
        revoke(broken, ids=("EV-1",))

    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "candidate_evidence_id" in str(excinfo.value)
    assert broken[0]["review_state"] == "UNREVIEWED", "nothing may change on a rejected batch"


# --------------------- L46: the list from a correction drives the writer
class _Outcome:
    def __init__(self, stale, unattributable=()):
        self.stale_candidate_evidence = tuple(stale)
        self.unattributable_evidence = tuple(unattributable)


def revoke_from(outcome, candidates=None, **overrides):
    from app.extensions.evidence.revocation import revoke_for_correction

    kwargs = {
        "review_decision_ref": "RV-1",
        "reviewer": "E12345",
        "reason": "订正后证据作废",
        "at": "2026-10-19T10:00:00+08:00",
    }
    kwargs.update(overrides)
    return revoke_for_correction(outcome, candidates if candidates is not None else registry(),
                                 **kwargs)


def test_the_correction_list_drives_the_writer():
    updated, report = revoke_from(_Outcome(["EV-1", "EV-2"]))

    assert report.revoked == ("EV-1", "EV-2")
    assert report.audit["source"] == "correction"
    assert updated[0]["review_state"] == "STALE"
    assert updated[1]["review_state"] == "STALE"


def test_an_empty_correction_list_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        revoke_from(_Outcome([]))
    assert excinfo.value.code == "E_INPUT_INVALID"


def test_unattributable_evidence_blocks_revocation_by_default():
    """Revoking "the rest" while some were not attributable is a partial success claim."""
    with pytest.raises(ExtensionError) as excinfo:
        revoke_from(_Outcome(["EV-1"], unattributable=["EV-ORPHAN"]))
    assert excinfo.value.code == "E_REVIEW_REQUIRED"
    assert "EV-ORPHAN" in str(excinfo.value)


def test_unattributable_can_be_accepted_explicitly_and_is_recorded():
    updated, report = revoke_from(
        _Outcome(["EV-1"], unattributable=["EV-ORPHAN"]), require_attributable=False
    )
    assert report.revoked == ("EV-1",)
    assert report.audit["unattributable_not_revoked"] == ["EV-ORPHAN"]
    assert updated[0]["review_state"] == "STALE"


def test_restore_returns_to_the_recorded_state_not_to_unreviewed():
    revoked, _ = revoke(registry(), ids=("EV-2",))  # EV-2 was ACCEPTED with RV-OLD
    assert revoked[1]["review_state"] == "STALE"

    from app.extensions.evidence.revocation import restore_candidates

    restored, report = restore_candidates(
        revoked,
        candidate_ids=["EV-2"],
        review_decision_ref="RV-2",
        reviewer="E12345",
        reason="误作废，恢复",
        at="2026-10-19T11:00:00+08:00",
    )
    assert report.restored == ("EV-2",)
    assert restored[1]["review_state"] == "ACCEPTED"
    assert restored[1]["review_decision_ref"] == "RV-OLD"
    assert "revoked_at" not in restored[1] and "previous_review_state" not in restored[1]


def test_restore_refuses_when_no_prior_state_was_recorded():
    from app.extensions.evidence.revocation import restore_candidates

    legacy = [
        {"candidate_evidence_id": "EV-X", "review_state": "STALE", "review_decision_ref": "R"}
    ]
    with pytest.raises(ExtensionError) as excinfo:
        restore_candidates(
            legacy,
            candidate_ids=["EV-X"],
            review_decision_ref="RV-3",
            reviewer="E12345",
            reason="r",
            at="2026-10-19T11:00:00+08:00",
        )
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "no recorded prior state" in str(excinfo.value)


def test_restore_refuses_a_candidate_that_was_never_revoked():
    from app.extensions.evidence.revocation import restore_candidates

    with pytest.raises(ExtensionError) as excinfo:
        restore_candidates(
            registry(),
            candidate_ids=["EV-1"],
            review_decision_ref="RV-4",
            reviewer="E12345",
            reason="r",
            at="2026-10-19T11:30:00+08:00",
        )
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "not STALE" in str(excinfo.value)


def test_revoke_restore_revoke_round_trip_is_stable():
    from app.extensions.evidence.revocation import restore_candidates

    first, _ = revoke(registry(), ids=("EV-2",))
    restored, _ = restore_candidates(
        first, candidate_ids=["EV-2"], review_decision_ref="RV-5", reviewer="E12345",
        reason="撤销", at="2026-10-19T12:00:00+08:00",
    )
    second, report = revoke(restored, ids=("EV-2",))

    assert report.revoked == ("EV-2",)
    assert second[1]["review_state"] == "STALE"
    assert second[1]["previous_review_state"] == "ACCEPTED"
