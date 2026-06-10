# ruff: noqa: RUF001
# RUF001 / RUF002 / RUF003 (ambiguous unicode) suppressed file-wide —
# the prompt fixtures must use the literal Turkish / Russian letters
# the live LLM emits.
"""Tests for the ``missing_info_extractor._SYSTEM_PROMPT`` contract.

A1.a (2026-05-20 round-2) tightens the prompt's output schema so
the LLM stops emitting the English boilerplate template
``"Guest needs <topic> which is not in the knowledge base"`` inside
``intervention_reason``.  The boilerplate is the root cause of
tester complaints #1 (literal English noise in PM Chat) and #6
(TR/EN language drift when the topic itself is Turkish), both of
which PR #326 patches *downstream* via a sanitiser.

These tests pin the prompt-side contract so the sanitiser becomes
defence-in-depth rather than load-bearing:

* The EN boilerplate is no longer present anywhere in the prompt's
  JSON schema — the LLM has no template to copy.
* The bare-topic rule is explicitly stated and replicates the
  Turkish / English / Russian examples PM Chat actually sees, so
  the LLM has a worked example for each surface language.
* The hard-rule on empty / non-empty pairing is preserved
  (regression anchor — PR #326's behaviour relies on it).
* The topic-verbatim rule (Aybüke 2026-05-18 fix) is preserved.
"""

from __future__ import annotations

from brain_engine.conversation import missing_info_extractor

_PROMPT = missing_info_extractor._SYSTEM_PROMPT


# ── EN boilerplate is gone from the output schema ─────────────────


def _deferred_schema_block() -> str:
    """Return the JSON object that follows ``When the AI deferred:``.

    The contract under test is the *schema example* the LLM copies —
    not the surrounding prose, which may legitimately reference the
    legacy boilerplate as a negative instruction.
    """
    marker = "When the AI deferred:"
    start = _PROMPT.index(marker)
    end = _PROMPT.index("}", start)
    return _PROMPT[start : end + 1]


def test_deferred_schema_uses_bare_topic_placeholder() -> None:
    """The deferred-branch JSON example must use the bare
    ``<topic>`` placeholder so the LLM copies the noun phrase
    verbatim without an English frame."""
    assert '"intervention_reason": "<topic>"' in _deferred_schema_block()


def test_deferred_schema_drops_en_boilerplate_text() -> None:
    """The schema example must NOT carry the old EN template that
    was the source of tester complaints #1 and #6."""
    schema = _deferred_schema_block()
    assert "which is not in the knowledge base" not in schema
    assert "Guest needs <topic>" not in schema


# ── Bare-topic rule explicit ──────────────────────────────────────


def test_prompt_states_bare_topic_rule_explicitly() -> None:
    """An explicit rule is more reliable than a schema example —
    the LLM must be told NOT to wrap the topic in an English
    template, in plain prose."""
    assert "bare topic noun phrase" in _PROMPT
    assert "Do NOT wrap it in any English template" in _PROMPT


def test_prompt_shows_worked_examples_for_each_surface_language() -> None:
    """One example per live surface language so the LLM cannot
    default to the EN template just because TR / RU examples are
    missing."""
    assert "geç çıkış ücreti" in _PROMPT  # Turkish
    assert '"intervention_reason": "parking"' in _PROMPT  # English
    assert "пароль Wi-Fi" in _PROMPT  # Russian


# ── PM-question is a full sentence, not a bare topic ──────────────


def test_deferred_schema_carries_pm_question_field() -> None:
    """The deferred-branch JSON example must request a separate
    ``pm_question`` field — the full guest-language sentence PM Chat
    surfaces (tester 2026-06-10: bare two-word escalations are
    noise)."""
    assert '"pm_question":' in _deferred_schema_block()


def test_prompt_states_pm_question_full_sentence_rule() -> None:
    """The PM-question rule must demand a complete sentence in the
    guest's language — not a bare noun phrase."""
    assert "PM-question shape" in _PROMPT
    assert "complete, natural sentence" in _PROMPT
    assert "SAME language the guest used" in _PROMPT


def test_prompt_pins_pm_question_to_conversation_language() -> None:
    """The language rule must anchor on the explicit ISO-coded
    conversation language injected into the user message — pinning
    beats letting the LLM infer the language (tester 2026-06-10: a
    Turkish property context leaked Turkish onto an English
    thread)."""
    assert "Conversation language (ISO 639-1)" in _PROMPT
    assert 'conversation language is "en", pm_question' in _PROMPT


def test_prompt_pairs_pm_question_with_empty_hard_rule() -> None:
    """When there is no gap, pm_question must be empty too — pins the
    invariant so an answered turn never leaks a stray PM escalation."""
    assert "intervention_reason AND pm_question MUST also be empty" in _PROMPT


# ── Pre-existing rules preserved ──────────────────────────────────


def test_prompt_preserves_hard_rule_on_empty_pairing() -> None:
    """PR #326's downstream behaviour relies on the prompt-side
    invariant: when ``missing_information`` is empty the model
    must also leave ``intervention_reason`` empty."""
    assert "when missing_information is empty, intervention_reason" in _PROMPT


def test_prompt_preserves_topic_verbatim_rule() -> None:
    """Aybüke 2026-05-18 fix — the topic must be copied verbatim,
    never paraphrased to an adjacent concept."""
    assert "copied verbatim" in _PROMPT
    assert "Do NOT infer adjacent topics" in _PROMPT


def test_prompt_preserves_confirmation_offer_rule() -> None:
    """Aybüke 2026-05-18 confirmation-offer guard — closing offers
    after a substantive answer must not flip the verdict."""
    assert "Confirmation-offer rule" in _PROMPT
    assert "ANSWERED" in _PROMPT


# ── Behavioural sanity ────────────────────────────────────────────


def test_prompt_still_classifies_into_answered_or_deferred() -> None:
    """Both verdict branches must still appear in the prompt — the
    LLM still chooses between answered and deferred."""
    assert "→ answered" in _PROMPT
    assert "→ deferred" in _PROMPT


def test_prompt_returns_json_object_shape() -> None:
    """The downstream caller parses the model output as JSON; the
    prompt must keep the ``Return JSON`` directive."""
    assert "Return JSON" in _PROMPT
