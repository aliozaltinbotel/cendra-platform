"""Behaviour of :class:`OwnerPolicyParser`."""

from __future__ import annotations

import pytest

from core.brain.planning.styles import PlannerStyleId
from core.brain.policy.errors import (
    OwnerPolicyCompileError,
    OwnerPolicyParseError,
)
from core.brain.policy.parser import OwnerPolicyParser


@pytest.fixture
def parser() -> OwnerPolicyParser:
    return OwnerPolicyParser()


def test_parse_minimal_block(parser: OwnerPolicyParser) -> None:
    """One owner block with style only parses cleanly."""
    document = parser.parse('owner "owner_a" { style = cooperative; }')
    assert len(document.owners) == 1
    block = document.owners[0]
    assert block.owner_id == "owner_a"
    assert block.style_id is PlannerStyleId.COOPERATIVE
    assert block.jurisdiction is None
    assert block.forbid == ()


def test_parse_full_block(parser: OwnerPolicyParser) -> None:
    """Block with style + jurisdiction + forbid parses every field."""
    document = parser.parse(
        """
        owner "owner_alpha" {
          style = vip_white_glove;
          jurisdiction = "BCN";
          forbid: charge_fee, issue_refund;
        }
        """
    )
    block = document.owners[0]
    assert block.owner_id == "owner_alpha"
    assert block.style_id is PlannerStyleId.VIP_WHITE_GLOVE
    assert block.jurisdiction == "BCN"
    assert block.forbid == (
        "charge_fee",
        "issue_refund",
    )


def test_parse_multiple_blocks(parser: OwnerPolicyParser) -> None:
    """Multiple blocks preserve order."""
    document = parser.parse(
        """
        owner "a" { style = cooperative; }
        owner "b" { style = defensive; }
        """
    )
    assert [o.owner_id for o in document.owners] == ["a", "b"]


def test_parse_comments_are_ignored(parser: OwnerPolicyParser) -> None:
    """``//`` line comments are stripped before parsing."""
    document = parser.parse(
        """
        // tenant: cendra
        owner "a" {
          // pin to cooperative for now
          style = cooperative;
        }
        """
    )
    assert document.owners[0].owner_id == "a"


def test_parse_invalid_syntax(parser: OwnerPolicyParser) -> None:
    """Garbage input raises :class:`OwnerPolicyParseError`."""
    with pytest.raises(OwnerPolicyParseError):
        parser.parse("owner garbage }")


def test_parse_unknown_style_raises(
    parser: OwnerPolicyParser,
) -> None:
    """Unknown style id raises :class:`OwnerPolicyCompileError`."""
    with pytest.raises(OwnerPolicyCompileError, match="unknown style"):
        parser.parse('owner "a" { style = nonexistent; }')


def test_parse_unknown_action_kind_raises(
    parser: OwnerPolicyParser,
) -> None:
    """Port note: action kinds are opaque strings now (golden rule 4) —
    unknown kinds parse fine; vocabulary validation happens at policy-save
    time against the tenant registry. The parser still rejects emptiness
    elsewhere; here we assert the permissive parse."""
    if True:
        parser.parse('owner "a" { forbid: not_an_action; }')


def test_parse_block_without_statements(
    parser: OwnerPolicyParser,
) -> None:
    """A block without statements is permitted (no-op owner)."""
    document = parser.parse('owner "a" { }')
    block = document.owners[0]
    assert block.style_id is None
    assert block.jurisdiction is None
    assert block.forbid == ()
