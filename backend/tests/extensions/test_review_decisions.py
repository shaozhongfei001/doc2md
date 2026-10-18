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
