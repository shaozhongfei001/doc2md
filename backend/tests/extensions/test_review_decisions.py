"""T702: a decision is bound to four things, and a local operator is not a bank."""

from __future__ import annotations

import pytest

from app.extensions import ExtensionError
from app.extensions.review.decisions import is_decision_valid, record_decision

BINDING = {
    "patch_digest": "d" * 64,
    "base_generation_id": "G1",
    "access_revision": "R7",
    "scope": "table:T1",
}


def decision(**overrides):
    kwargs = {
        "decision_id": "D1",
        "reviewer": "local-operator",
        "outcome": "LOCAL_OPERATOR",
        **BINDING,
    }
    kwargs.update(overrides)
    return record_decision(**kwargs)


def test_local_operator_decision_records_the_binding():
    built = decision()
    assert built["outcome"] == "LOCAL_OPERATOR"
    assert built["reviewer"] == "local-operator"
    assert built["patch_digest"] == BINDING["patch_digest"]


def test_bank_approval_requires_a_connected_adapter():
    with pytest.raises(ExtensionError) as excinfo:
        decision(outcome="BANK_APPROVED")
    assert excinfo.value.code == "E_CONSUMER_NOT_CONNECTED"
    assert "not a bank approval" in str(excinfo.value)


def test_bank_approval_is_recordable_when_connected():
    built = decision(outcome="BANK_APPROVED", bank_auth_connected=True)
    assert built["outcome"] == "BANK_APPROVED"


def test_reviewer_must_not_be_blank():
    with pytest.raises(ExtensionError):
        decision(reviewer="   ")


def test_unknown_outcome_is_refused():
    with pytest.raises(ExtensionError):
        decision(outcome="APPROVED_BY_JSON")


# ------------------------------------------------------------------ validity
def test_valid_while_all_four_bindings_match():
    assert is_decision_valid(decision(), **BINDING) is True


@pytest.mark.parametrize(
    "changed",
    [
        {"patch_digest": "e" * 64},
        {"base_generation_id": "G2"},
        {"access_revision": "R8"},
        {"scope": "table:T2"},
    ],
)
def test_any_binding_change_invalidates_the_decision(changed):
    binding = dict(BINDING)
    binding.update(changed)
    assert is_decision_valid(decision(), **binding) is False


def test_rejected_outcome_never_counts_as_valid():
    rejected = decision(outcome="REJECTED")
    assert is_decision_valid(rejected, **BINDING) is False


def test_non_decision_payload_is_invalid_not_an_error():
    assert is_decision_valid(None, **BINDING) is False


# --------------------------- L39: reviewer policy (Owner D1, ALLOW_ALL today)
def test_the_default_policy_refuses_a_placeholder_reviewer():
    """Owner closed "全部放行" (2026-10-19): the shipped policy is now RESTRICTED.

    This test used to assert the opposite. It was written that way on purpose — the
    previous owner decision was "accept everyone" — so flipping it here is the
    decision changing, not a test being bent to fit the code.
    """
    with pytest.raises(ExtensionError) as excinfo:
        record_decision(
            decision_id="D-1",
            patch_digest="a" * 64,
            base_generation_id="GEN-1",
            access_revision="AR-1",
            scope="table:1",
            reviewer="---",
            outcome="LOCAL_OPERATOR",
        )
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "RESTRICTED" in str(excinfo.value)


def test_the_default_policy_accepts_a_plausible_identity():
    for reviewer in ("E12345", "张三", "reviewer.1", "ops@example.com"):
        decision = record_decision(
            decision_id="D-OK",
            patch_digest="a" * 64,
            base_generation_id="GEN-1",
            access_revision="AR-1",
            scope="table:1",
            reviewer=reviewer,
            outcome="LOCAL_OPERATOR",
        )
        assert decision["reviewer"] == reviewer

def test_a_restricted_policy_refuses_an_unlisted_reviewer():
    from app.extensions.review.authority import RESTRICTED, ReviewerPolicy

    with pytest.raises(ExtensionError) as excinfo:
        record_decision(
            decision_id="D-2",
            patch_digest="a" * 64,
            base_generation_id="GEN-1",
            access_revision="AR-1",
            scope="table:1",
            reviewer="---",
            outcome="LOCAL_OPERATOR",
            policy=ReviewerPolicy(mode=RESTRICTED, allowed=("E12345",)),
        )
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "RESTRICTED" in str(excinfo.value)


def test_a_restricted_policy_accepts_a_listed_reviewer_and_strips_it():
    from app.extensions.review.authority import RESTRICTED, ReviewerPolicy

    decision = record_decision(
        decision_id="D-3",
        patch_digest="a" * 64,
        base_generation_id="GEN-1",
        access_revision="AR-1",
        scope="table:1",
        reviewer="  E12345  ",
        outcome="LOCAL_OPERATOR",
        policy=ReviewerPolicy(mode=RESTRICTED, pattern=r"E\d{5}"),
    )
    assert decision["reviewer"] == "E12345"


def test_an_unknown_policy_mode_fails_at_construction():
    from app.extensions.review.authority import ReviewerPolicy

    with pytest.raises(ExtensionError) as excinfo:
        ReviewerPolicy(mode="ALLOW_ALL_typo")
    assert excinfo.value.code == "E_INPUT_INVALID"


def test_a_restricted_policy_with_no_rules_denies_everyone():
    """Misconfiguration must fail closed. An empty whitelist is not "no restriction".

    ``pattern=None`` is passed explicitly: the shipped default carries a pattern, so
    relying on the default would test the opposite of what this case is about.
    """
    from app.extensions.review.authority import RESTRICTED, ReviewerPolicy

    with pytest.raises(ExtensionError):
        record_decision(
            decision_id="D-4",
            patch_digest="a" * 64,
            base_generation_id="GEN-1",
            access_revision="AR-1",
            scope="table:1",
            reviewer="E12345",
            outcome="LOCAL_OPERATOR",
            policy=ReviewerPolicy(mode=RESTRICTED, allowed=(), pattern=None),
        )

def test_a_blank_reviewer_is_refused_under_every_policy():
    from app.extensions.review.authority import ALLOW_ALL, ReviewerPolicy

    with pytest.raises(ExtensionError) as excinfo:
        ReviewerPolicy(mode=ALLOW_ALL).check("   ")
    assert excinfo.value.code == "E_INPUT_INVALID"


def test_allow_all_still_strips_the_reviewer():
    """qa-l41: stripping was only asserted on the RESTRICTED path.

    The live policy is ALLOW_ALL, so this is the branch that actually stores the audit
    value today; leaving it unasserted means the stored value could start carrying
    whitespace without any test noticing.
    """
    from app.extensions.review.authority import ALLOW_ALL, ReviewerPolicy

    assert ReviewerPolicy(mode=ALLOW_ALL).check("  E12345  ") == "E12345"
