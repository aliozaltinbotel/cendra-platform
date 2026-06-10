"""Tests for the multi-value disambiguation rule in the memory block.

Tester 2026-06-10: when a guest had given the same fact more than once
(two WhatsApp numbers across the conversation), the agent silently
answered with one of them instead of surfacing both and asking which.

The fix lives in the ``context_layout.txt`` template — the
``[ESTABLISHED FACTS]`` block now instructs the agent to list every
value with its date and ask the guest which to use, rather than
picking one.  These tests pin that the instruction is rendered when
facts are present and that the block (instruction included) is omitted
when there are none.
"""

from __future__ import annotations

from brain_engine.context.assembler import ContextAssembler

_DISAMBIGUATION_MARKER = "do NOT silently pick one"


def test_disambiguation_rule_rendered_with_facts() -> None:
    """With recalled facts present, the multi-value rule appears in the
    [ESTABLISHED FACTS] block alongside the facts themselves."""
    assembled = ContextAssembler().assemble(
        facts=[
            "From an earlier interaction (2026-06-08): "
            "My WhatsApp number is +39 371 5211257",
            "From an earlier interaction (2026-06-10): "
            "My WhatsApp number is +90 555 9876543",
        ],
    )
    text = assembled.text
    assert "[ESTABLISHED FACTS" in text
    assert _DISAMBIGUATION_MARKER in text
    assert "ask the guest which one to use" in text
    # Both values are still listed for the agent to surface.
    assert "+39 371 5211257" in text
    assert "+90 555 9876543" in text


def test_no_facts_means_no_block_and_no_rule() -> None:
    """No facts ⇒ the whole block (and its rule) is omitted, so an
    empty memory never injects a dangling instruction."""
    assembled = ContextAssembler().assemble(facts=[])
    assert "[ESTABLISHED FACTS" not in assembled.text
    assert _DISAMBIGUATION_MARKER not in assembled.text


def test_rule_precedes_the_fact_list() -> None:
    """The instruction must come before the bulleted values so the
    agent reads the rule in context, not after the data."""
    assembled = ContextAssembler().assemble(
        facts=["From an earlier interaction (2026-06-10): arrival 8 PM"],
    )
    text = assembled.text
    assert text.index(_DISAMBIGUATION_MARKER) < text.index("- From an earlier")
