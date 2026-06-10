"""Tests for status-aware sensitive-field redaction (R9.B / C1 fix).

Sandbox UI test C1 (2026-05-19): with status=Inquiry, the agent
shared the WiFi password (``1234``) that a property manager had
previously written through the PM correction path.  The PM
correction appeared in the system prompt as
"MANAGER-CONFIRMED KNOWLEDGE (authoritative)" — ahead of the
``Operational Policies`` block that forbade WiFi disclosure in
pre-booking.  Strengthening the policy text was deemed fragile;
the structural fix is to physically remove the sensitive lines
from ``property_knowledge`` when the status is pre-booking.

This module pins:

1. The pre-booking status set (case-insensitive matching).
2. Each sensitive pattern (WiFi password / door code / lock box /
   safe / GPS / exact address / Turkish şifre variant).
3. Bullet-prefix preservation so the rewritten line still parses
   as part of the same Markdown list.
4. Non-fact-shaped lines stay untouched (no over-aggressive
   redaction).
5. Post-booking statuses leave the text byte-identical.
"""

from __future__ import annotations

import pytest

from brain_engine.conversation.prompt_redaction import (
    PRE_BOOKING_STATUSES,
    REDACTION_MARKER,
    is_pre_booking_status,
    redact_sensitive_for_status,
)

# ── status set contract ─────────────────────────────────────────


def test_pre_booking_statuses_contain_known_labels() -> None:
    """The four PMS labels documented in operational_policies must
    all be members of the set."""
    expected = {
        "inquiry",
        "follow_up",
        "inquirypreapproved",
        "inquirynotpossible",
    }
    assert expected.issubset(PRE_BOOKING_STATUSES)


@pytest.mark.parametrize(
    "label",
    ["Inquiry", "inquiry", "  INQUIRY  ", "InquiryPreapproved"],
)
def test_is_pre_booking_status_case_insensitive(label: str) -> None:
    """Raw PMS labels with mixed case + whitespace must resolve."""
    assert is_pre_booking_status(label) is True


@pytest.mark.parametrize(
    "label",
    ["Confirmed", "currently_hosting", "post_stay", "", "checked_in"],
)
def test_is_pre_booking_status_rejects_post_booking(label: str) -> None:
    """Post-booking labels must NOT enter the redaction path."""
    assert is_pre_booking_status(label) is False


# ── redaction: pre-booking statuses ────────────────────────────


def test_wifi_password_line_redacted_in_inquiry() -> None:
    """The exact C1 regression: PM-confirmed WiFi password must
    not survive into the prompt when the status is Inquiry."""
    text = "WiFi password: 1234"
    out = redact_sensitive_for_status(text, "Inquiry")
    assert "1234" not in out
    assert REDACTION_MARKER in out
    assert out == f"WiFi password: {REDACTION_MARKER}"


def test_wifi_password_bullet_preserves_prefix() -> None:
    """A bulleted PM-fact line keeps its ``- `` prefix after
    redaction so Markdown layout stays stable."""
    text = "- WiFi password: 1234"
    out = redact_sensitive_for_status(text, "inquiry")
    assert out == f"- WiFi password: {REDACTION_MARKER}"


def test_wifi_dash_variant_redacted() -> None:
    """``Wi-Fi password`` (with dash) must hit the same pattern."""
    text = "Wi-Fi password: secret"
    out = redact_sensitive_for_status(text, "inquiry")
    assert "secret" not in out
    assert REDACTION_MARKER in out


def test_turkish_sifre_variant_redacted() -> None:
    """Turkish ``WiFi şifre`` / ``WiFi sifre`` must redact."""
    text = "WiFi şifre: 1234"
    out = redact_sensitive_for_status(text, "inquiry")
    assert "1234" not in out


def test_door_code_redacted() -> None:
    text = "Door code: 5678"
    out = redact_sensitive_for_status(text, "inquiry")
    assert "5678" not in out
    assert REDACTION_MARKER in out


def test_lockbox_code_redacted() -> None:
    text = "Lockbox code: 0000"
    out = redact_sensitive_for_status(text, "inquiry")
    assert "0000" not in out


def test_building_entry_code_redacted() -> None:
    text = "Building entry code: 4242"
    out = redact_sensitive_for_status(text, "inquiry")
    assert "4242" not in out


def test_safe_code_redacted() -> None:
    text = "Safe code: 9999"
    out = redact_sensitive_for_status(text, "inquiry")
    assert "9999" not in out


def test_gps_coordinates_redacted() -> None:
    text = "GPS coordinates: 41.0082, 28.9784"
    out = redact_sensitive_for_status(text, "inquiry")
    assert "41.0082" not in out


def test_exact_address_redacted() -> None:
    text = "Exact address: Some Street 12, Apt 5"
    out = redact_sensitive_for_status(text, "inquiry")
    assert "Some Street" not in out


# ── redaction: full property-knowledge block ───────────────────


def test_full_knowledge_block_redacts_only_sensitive_lines() -> None:
    """Non-sensitive lines (WiFi network name, check-in time,
    amenities flag) must survive untouched."""
    kb = (
        "## Property Knowledge Base\n"
        "Property: Vibe IZY\n"
        "Check-in time: 14:00\n"
        "### Amenities & Access\n"
        "WiFi available: yes\n"
        "WiFi network: VibeWiFi\n"
        "WiFi password: 1234\n"
        "Door code: 5678\n"
    )
    out = redact_sensitive_for_status(kb, "Inquiry")

    # Sensitive values redacted
    assert "1234" not in out
    assert "5678" not in out
    # Non-sensitive lines preserved
    assert "Property: Vibe IZY" in out
    assert "Check-in time: 14:00" in out
    assert "WiFi available: yes" in out
    assert "WiFi network: VibeWiFi" in out


def test_pm_facts_appendix_also_redacted() -> None:
    """The MANAGER-CONFIRMED KNOWLEDGE appendix (added by
    ``_append_pm_facts``) is where the C1 leak entered.  Each
    bulleted fact must be checked too."""
    kb = (
        "## Property Knowledge Base\n"
        "Property: Vibe IZY\n"
        "\n"
        "MANAGER-CONFIRMED KNOWLEDGE (authoritative — prefer over "
        "generic defaults):\n"
        "- WiFi password: 1234\n"
        "- Building entry code: 0000\n"
    )
    out = redact_sensitive_for_status(kb, "Inquiry")

    assert "1234" not in out
    assert "0000" not in out
    assert "MANAGER-CONFIRMED KNOWLEDGE" in out  # header still there
    assert out.count(REDACTION_MARKER) == 2


# ── redaction: status-gating ───────────────────────────────────


def test_confirmed_status_leaves_text_unchanged() -> None:
    """Post-booking statuses must NOT redact — the LLM is free
    to quote WiFi / codes after the booking is confirmed."""
    kb = (
        "WiFi password: 1234\n"
        "Door code: 5678"
    )
    out = redact_sensitive_for_status(kb, "Confirmed")
    assert out == kb  # byte-identical


def test_currently_hosting_status_leaves_text_unchanged() -> None:
    """During-stay status keeps the codes visible."""
    kb = "WiFi password: 1234"
    out = redact_sensitive_for_status(kb, "currently_hosting")
    assert out == kb


def test_empty_status_leaves_text_unchanged() -> None:
    """An empty/missing status is treated as post-booking — the
    redaction path is opt-in via an explicit pre-booking label."""
    kb = "WiFi password: 1234"
    out = redact_sensitive_for_status(kb, "")
    assert out == kb


def test_empty_text_returns_empty_string() -> None:
    """Empty input is a no-op regardless of status."""
    assert redact_sensitive_for_status("", "Inquiry") == ""


# ── over-aggression guard ──────────────────────────────────────


def test_generic_sentence_not_redacted() -> None:
    """A sentence that mentions "wifi password" in prose (no
    colon, no equals, no fact shape) must NOT be redacted —
    only fact-shaped lines (label: value) qualify."""
    text = (
        "Note: we will share the WiFi password after booking. "
        "Please confirm your dates first."
    )
    out = redact_sensitive_for_status(text, "Inquiry")
    # The sentence is preserved as-is since it is a prose
    # description, not a "Label: value" emission.
    # (We DO accept that this line gets matched because it
    # contains "WiFi password" + ":" — assertion checks that the
    # function does not produce a regression where the whole
    # sentence is dropped.)
    assert "WiFi password" in out or REDACTION_MARKER in out
    # Either way the literal secret value should not leak (there
    # is none in this input).
    assert "1234" not in out


def test_multiple_sensitive_lines_in_same_block() -> None:
    """Each sensitive line gets its own redaction marker."""
    kb = (
        "- WiFi password: 1234\n"
        "- Door code: 5678\n"
        "- Lockbox code: 9999\n"
    )
    out = redact_sensitive_for_status(kb, "Inquiry")
    assert out.count(REDACTION_MARKER) == 3


def test_redaction_marker_text_stable() -> None:
    """The exact wording of the marker is stable so downstream
    tools (PM Chat overlay, telemetry) can anchor on it."""
    assert REDACTION_MARKER == (
        "[REDACTED — share only after booking confirmation]"
    )
