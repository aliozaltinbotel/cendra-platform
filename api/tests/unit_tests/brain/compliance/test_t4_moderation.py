"""T4 moderation module behaviour (Art. 50 disclosure + PII redaction)."""

from core.moderation.cendra_brain.cendra_brain import CendraBrainModeration


def _module(config=None) -> CendraBrainModeration:
    mod = CendraBrainModeration.__new__(CendraBrainModeration)
    mod.config = config or {}
    return mod


def test_outputs_get_pii_redaction_and_disclosure():
    result = _module().moderation_for_outputs("Reach me at john@example.com")
    assert result.flagged
    assert "john@example.com" not in result.text
    assert "AI" in result.text


def test_disclosure_not_duplicated():
    first = _module().moderation_for_outputs("hello")
    second = _module().moderation_for_outputs(first.text)
    assert second.text.count("AI assistant") == first.text.count("AI assistant")


def test_inputs_redacted():
    result = _module().moderation_for_inputs({"note": "call +1 555 123 4567"}, query="mail a@b.co")
    assert result.flagged
    assert "a@b.co" not in result.query


def test_flags_off_pass_through():
    mod = _module({"redact_outputs": False, "disclose": False})
    result = mod.moderation_for_outputs("Reach me at john@example.com")
    assert not result.flagged
    assert result.text == "Reach me at john@example.com"
