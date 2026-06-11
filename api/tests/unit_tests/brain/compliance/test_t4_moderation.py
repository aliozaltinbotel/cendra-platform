"""T4 moderation module behaviour (Art. 50 disclosure + PII redaction)."""

from unittest.mock import Mock

import core.moderation.cendra_brain.cendra_brain as cendra_brain_module
from core.brain.compliance.encryption import KeyHandle
from core.moderation.cendra_brain.cendra_brain import CendraBrainModeration

TENANT = "11111111-1111-1111-1111-111111111111"


def _module(config=None) -> CendraBrainModeration:
    mod = CendraBrainModeration.__new__(CendraBrainModeration)
    mod.config = config or {}
    mod.tenant_id = TENANT
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


def test_hash_redaction_uses_tenant_custody(monkeypatch):
    service = Mock()
    service.hash_key_for.return_value = KeyHandle(kid="kid", key_bytes=b"0123456789abcdef")
    monkeypatch.setattr(cendra_brain_module, "BrainCustodyService", Mock(return_value=service))

    result = _module({"redaction_strategy": "hash", "disclose": False}).moderation_for_outputs(
        "Reach me at john@example.com"
    )

    assert result.flagged
    assert "<email:" in result.text
    assert "john@example.com" not in result.text
    service.hash_key_for.assert_called_once_with(TENANT, "moderation_pii_redaction")


def test_hash_redaction_falls_back_to_mask_when_custody_missing(monkeypatch):
    service = Mock()
    service.hash_key_for.side_effect = cendra_brain_module.BrainCustodyError("missing")
    monkeypatch.setattr(cendra_brain_module, "BrainCustodyService", Mock(return_value=service))

    result = _module({"redaction_strategy": "hash", "disclose": False}).moderation_for_outputs(
        "Reach me at john@example.com"
    )

    assert result.flagged
    assert "<email:" not in result.text
    assert "john@example.com" not in result.text
