"""Parser for the owner-policy DSL.

The :class:`OwnerPolicyParser` is a thin shell around a Lark
parser configured with the grammar in :mod:`grammar.lark`.  Lark is
imported lazily inside :meth:`parse` so pods that never receive an
owner-policy upload pay zero startup cost — important because the
DSL is new infrastructure not every tenant uses yet.

Parsing produces a :class:`PolicyDocument`; semantic validation
(unknown style ids, duplicate owners, etc.) lives in
:mod:`brain_engine.owner_policy.compiler`.
"""

from __future__ import annotations

from importlib import resources
from typing import Any

from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.owner_policy.ast import (
    ComparisonOp,
    NumericConstraint,
    NumericMetric,
    OwnerBlock,
    PolicyDocument,
)
from brain_engine.owner_policy.errors import (
    OwnerPolicyCompileError,
    OwnerPolicyParseError,
)
from brain_engine.planner.styles import PlannerStyleId


__all__ = ["OwnerPolicyParser"]


_GRAMMAR_RESOURCE = "grammar.lark"


class OwnerPolicyParser:
    """Parse owner-policy DSL source into a :class:`PolicyDocument`.

    The parser is stateless — Lark instances are cached on first
    use to amortise grammar-compilation cost across calls.
    """

    def __init__(self) -> None:
        self._lark: Any | None = None

    def parse(self, source: str) -> PolicyDocument:
        """Return the :class:`PolicyDocument` for ``source``.

        Args:
            source: DSL document text.

        Raises:
            OwnerPolicyParseError: When ``source`` does not match
                the grammar.
            OwnerPolicyCompileError: When a value (style id /
                action kind / numeric metric / comparison op) does
                not map to a known enum member.
        """
        tree = self._build_tree(source)
        owners = tuple(self._owner_blocks(tree))
        return PolicyDocument(owners=owners)

    # ── internals ─────────────────────────────────────────────── #

    def _build_tree(self, source: str) -> Any:
        from lark import Lark
        from lark.exceptions import LarkError

        if self._lark is None:
            grammar = (
                resources.files("brain_engine.owner_policy")
                .joinpath(_GRAMMAR_RESOURCE)
                .read_text(encoding="utf-8")
            )
            self._lark = Lark(grammar, parser="lalr", start="start")
        try:
            return self._lark.parse(source)
        except LarkError as exc:
            raise OwnerPolicyParseError(str(exc)) from exc

    def _owner_blocks(self, tree: Any) -> list[OwnerBlock]:
        """Walk the parse tree and yield :class:`OwnerBlock` nodes."""
        blocks: list[OwnerBlock] = []
        for owner_tree in tree.children:
            blocks.append(self._build_owner_block(owner_tree))
        return blocks

    def _build_owner_block(self, owner_tree: Any) -> OwnerBlock:
        owner_id = _strip_quotes(owner_tree.children[0])
        style_id: PlannerStyleId | None = None
        jurisdiction: str | None = None
        forbid: list[CardActionKind] = []
        numeric: list[NumericConstraint] = []
        for stmt in owner_tree.children[1:]:
            kind = stmt.data
            if kind == "style_stmt":
                style_id = self._read_style(stmt)
            elif kind == "jurisdiction_stmt":
                jurisdiction = _strip_quotes(stmt.children[0])
            elif kind == "forbid_stmt":
                forbid.extend(
                    self._read_action_kind(t)
                    for t in stmt.children
                )
            elif kind == "numeric_stmt":
                numeric.append(self._read_numeric(stmt))
        return OwnerBlock(
            owner_id=owner_id,
            style_id=style_id,
            jurisdiction=jurisdiction,
            forbid=tuple(forbid),
            numeric_constraints=tuple(numeric),
        )

    @staticmethod
    def _read_style(stmt: Any) -> PlannerStyleId:
        token = str(stmt.children[0])
        try:
            return PlannerStyleId(token)
        except ValueError as exc:
            raise OwnerPolicyCompileError(
                f"unknown style name {token!r}"
            ) from exc

    @staticmethod
    def _read_action_kind(token: Any) -> CardActionKind:
        value = str(token)
        try:
            return CardActionKind(value)
        except ValueError as exc:
            raise OwnerPolicyCompileError(
                f"unknown action kind {value!r}"
            ) from exc

    @staticmethod
    def _read_numeric(stmt: Any) -> NumericConstraint:
        """Parse ``METRIC OP INT`` into a :class:`NumericConstraint`."""
        metric_token, op_token, int_token = stmt.children
        metric_value = str(metric_token)
        op_value = str(op_token)
        try:
            metric = NumericMetric(metric_value)
        except ValueError as exc:
            raise OwnerPolicyCompileError(
                f"unknown numeric metric {metric_value!r}"
            ) from exc
        try:
            op = ComparisonOp(op_value)
        except ValueError as exc:
            raise OwnerPolicyCompileError(
                f"unknown comparison op {op_value!r}"
            ) from exc
        try:
            value = int(str(int_token))
        except ValueError as exc:
            raise OwnerPolicyCompileError(
                f"expected integer literal, got {int_token!r}"
            ) from exc
        return NumericConstraint(metric=metric, op=op, value=value)


def _strip_quotes(token: Any) -> str:
    """Return the literal value of an ``ESCAPED_STRING`` token."""
    raw = str(token)
    if len(raw) < 2 or raw[0] != '"' or raw[-1] != '"':
        raise OwnerPolicyParseError(
            f"expected quoted string, got {raw!r}"
        )
    return raw[1:-1]
