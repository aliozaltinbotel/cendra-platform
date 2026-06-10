"""Behaviour of :class:`Z3OwnerPolicyVerifier`."""

from __future__ import annotations

import pytest

from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.owner_policy.ast import (
    OwnerBlock,
    PolicyDocument,
)
from brain_engine.owner_policy.errors import (
    OwnerPolicyCompileError,
)
from brain_engine.owner_policy.parser import OwnerPolicyParser
from brain_engine.owner_policy.z3_compiler import (
    OwnerVerifyOutcome,
    Z3OwnerPolicyVerifier,
)


_SAMPLE = """
owner "alpha" {
  jurisdiction = "BCN";
  forbid: charge_fee, issue_refund;
}

owner "beta" {
}
"""


@pytest.fixture
def verifier() -> Z3OwnerPolicyVerifier:
    document = OwnerPolicyParser().parse(_SAMPLE)
    return Z3OwnerPolicyVerifier(document)


def test_known_owners_round_trip(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """``known_owners`` lists every owner in the document."""
    assert set(verifier.known_owners()) == {"alpha", "beta"}


def test_allowed_action_returns_ok(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """An owner permits any action not in the forbid list."""
    result = verifier.verify(
        owner_id="alpha",
        action_kind=CardActionKind.SEND_MESSAGE,
        jurisdiction="BCN",
    )
    assert result.ok is True
    assert result.outcome is OwnerVerifyOutcome.OK


def test_forbidden_action_returns_typed_outcome(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """Action in the forbid list yields FORBIDDEN_ACTION."""
    result = verifier.verify(
        owner_id="alpha",
        action_kind=CardActionKind.CHARGE_FEE,
        jurisdiction="BCN",
    )
    assert result.outcome is OwnerVerifyOutcome.FORBIDDEN_ACTION
    assert "charge_fee" in result.rationale
    # Z3 actually ran — rationale includes the unsat verdict.
    assert "z3.check=unsat" in result.rationale


def test_jurisdiction_mismatch_returns_typed_outcome(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """Different jurisdiction than the owner pin yields mismatch."""
    result = verifier.verify(
        owner_id="alpha",
        action_kind=CardActionKind.SEND_MESSAGE,
        jurisdiction="PAR",
    )
    assert result.outcome is OwnerVerifyOutcome.JURISDICTION_MISMATCH
    assert "BCN" in result.rationale
    assert "PAR" in result.rationale
    assert "z3.check=unsat" in result.rationale


def test_unknown_owner_returns_typed_outcome(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """An owner not in the document yields UNKNOWN_OWNER."""
    result = verifier.verify(
        owner_id="ghost",
        action_kind=CardActionKind.SEND_MESSAGE,
    )
    assert result.outcome is OwnerVerifyOutcome.UNKNOWN_OWNER


def test_owner_without_jurisdiction_skips_check(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """Owner with no jurisdiction pin allows any caller jurisdiction."""
    result = verifier.verify(
        owner_id="beta",
        action_kind=CardActionKind.SEND_MESSAGE,
        jurisdiction="PAR",
    )
    assert result.ok is True


def test_no_caller_jurisdiction_skips_check(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """If caller omits jurisdiction the verifier does not penalise."""
    result = verifier.verify(
        owner_id="alpha",
        action_kind=CardActionKind.SEND_MESSAGE,
        jurisdiction=None,
    )
    assert result.ok is True


def test_constraints_for_known_owner(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """``constraints_for`` produces a regulator-replayable summary."""
    summary = verifier.constraints_for("alpha")
    assert summary["owner_id"] == "alpha"
    assert summary["jurisdiction"] == "BCN"
    assert summary["forbid"] == ["charge_fee", "issue_refund"]


def test_constraints_for_unknown_owner_is_empty(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    assert verifier.constraints_for("ghost") == {}


def test_duplicate_owner_block_rejected() -> None:
    """The verifier's index refuses two blocks with the same id."""
    document = PolicyDocument(
        owners=(
            OwnerBlock(
                owner_id="same",
                style_id=None,
                jurisdiction=None,
                forbid=(),
            ),
            OwnerBlock(
                owner_id="same",
                style_id=None,
                jurisdiction="BCN",
                forbid=(),
            ),
        ),
    )
    with pytest.raises(
        OwnerPolicyCompileError, match="duplicate owner"
    ):
        Z3OwnerPolicyVerifier(document)


def test_verify_result_ok_property() -> None:
    """``.ok`` is True only for the OK outcome."""
    document = OwnerPolicyParser().parse(_SAMPLE)
    verifier = Z3OwnerPolicyVerifier(document)
    ok = verifier.verify(
        owner_id="alpha",
        action_kind=CardActionKind.SEND_MESSAGE,
        jurisdiction="BCN",
    )
    forbidden = verifier.verify(
        owner_id="alpha",
        action_kind=CardActionKind.ISSUE_REFUND,
        jurisdiction="BCN",
    )
    assert ok.ok is True
    assert forbidden.ok is False
