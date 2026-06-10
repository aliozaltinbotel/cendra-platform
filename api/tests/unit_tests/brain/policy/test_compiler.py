"""Semantic pass and runtime artefacts of :class:`OwnerPolicyCompiler`."""

from __future__ import annotations

import pytest

from core.brain.planning.styles import PlannerStyleId
from core.brain.policy.ast import (
    OwnerBlock,
    PolicyDocument,
)
from core.brain.policy.compiler import (
    OwnerPolicyCompiler,
    derived_style_id,
)
from core.brain.policy.errors import (
    OwnerPolicyCompileError,
)


def _block(
    owner_id: str,
    *,
    style_id: PlannerStyleId | None = None,
    jurisdiction: str | None = None,
    forbid: tuple[str, ...] = (),
) -> OwnerBlock:
    return OwnerBlock(
        owner_id=owner_id,
        style_id=style_id,
        jurisdiction=jurisdiction,
        forbid=forbid,
    )


@pytest.fixture
def compiler() -> OwnerPolicyCompiler:
    return OwnerPolicyCompiler()


def test_empty_document_compiles_to_empty_artefacts(
    compiler: OwnerPolicyCompiler,
) -> None:
    """Document with zero owners produces empty maps."""
    compiled = compiler.compile(PolicyDocument(owners=()))
    assert compiled.styles == {}
    assert compiled.owner_style == {}
    assert compiled.jurisdictions == {}


def test_style_only_block_registers_owner(
    compiler: OwnerPolicyCompiler,
) -> None:
    """Block with only ``style`` registers a derived spec."""
    document = PolicyDocument(owners=(_block("owner_a", style_id=PlannerStyleId.COOPERATIVE),))
    compiled = compiler.compile(document)
    assert "owner_a" in compiled.owner_style
    derived = compiled.owner_style["owner_a"]
    assert derived == derived_style_id("owner_a")
    assert derived in compiled.styles


def test_forbid_extends_base_denylist(
    compiler: OwnerPolicyCompiler,
) -> None:
    """The owner's ``forbid`` list is unioned with the base denylist."""
    document = PolicyDocument(
        owners=(
            _block(
                "owner_a",
                style_id=PlannerStyleId.COMPLIANCE_STRICT,
                forbid=("send_message",),
            ),
        )
    )
    compiled = compiler.compile(document)
    spec = compiled.styles[derived_style_id("owner_a")]
    # Base compliance_strict already forbids CHARGE_FEE etc.;
    # the owner extends the list with SEND_MESSAGE.
    assert "charge_fee" in spec.denylist
    assert "send_message" in spec.denylist


def test_jurisdiction_recorded_separately(
    compiler: OwnerPolicyCompiler,
) -> None:
    """Jurisdictions land in their own mapping."""
    document = PolicyDocument(
        owners=(
            _block(
                "owner_a",
                style_id=PlannerStyleId.COOPERATIVE,
                jurisdiction="BCN",
            ),
        )
    )
    compiled = compiler.compile(document)
    assert compiled.jurisdictions["owner_a"] == "BCN"


def test_block_without_style_or_forbid_skipped(
    compiler: OwnerPolicyCompiler,
) -> None:
    """An owner with only jurisdiction does not produce a spec."""
    document = PolicyDocument(owners=(_block("owner_a", jurisdiction="BCN"),))
    compiled = compiler.compile(document)
    assert compiled.owner_style == {}
    assert compiled.styles == {}
    assert compiled.jurisdictions["owner_a"] == "BCN"


def test_duplicate_owner_raises(
    compiler: OwnerPolicyCompiler,
) -> None:
    """Two blocks for the same owner raise compile error."""
    document = PolicyDocument(
        owners=(
            _block("owner_a", style_id=PlannerStyleId.COOPERATIVE),
            _block(
                "owner_a",
                style_id=PlannerStyleId.AGGRESSIVE_REVENUE,
            ),
        )
    )
    with pytest.raises(
        OwnerPolicyCompileError,
        match="duplicate owner",
    ):
        compiler.compile(document)


def test_forbid_only_block_falls_back_to_cooperative(
    compiler: OwnerPolicyCompiler,
) -> None:
    """A bare forbid layers on top of cooperative."""
    document = PolicyDocument(
        owners=(
            _block(
                "owner_a",
                forbid=("release_code",),
            ),
        )
    )
    compiled = compiler.compile(document)
    spec = compiled.styles[derived_style_id("owner_a")]
    assert spec.style_id is PlannerStyleId.COOPERATIVE
    assert "release_code" in spec.denylist
