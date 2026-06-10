"""Tests for operational-policy → conversation-prompt wiring.

The policy registry at ``brain_engine.guardrails.operational_policies``
holds 46 rows mined from the operations xlsx.  Before this PR the
module was data-only — defined, exported, never imported anywhere
— so the SECURITY clause that forbids sharing WiFi passwords /
lock codes / GPS before booking had no enforcement path.  Sandbox
UI captured the regression: a guest with status="Inquiry" asked
"what is the wifi password" and the agent answered "1234".

The fix wires three pieces:

1. ``policies_for_status()`` is now case-insensitive so PMS labels
   like "Inquiry" (workbook canonical "inquiry") match.
2. ``format_policies_for_prompt()`` renders the matched policies
   as a Markdown block the LLM treats as instructions.
3. ``ConversationService._assemble_prompt`` calls both and splices
   the block into the system prompt next to the existing
   ``customer_guardrails`` block.

These tests pin each piece in isolation plus the integration
contract (the rendered prompt actually contains the SECURITY text
for an Inquiry-status request).
"""

from __future__ import annotations

import pytest

from brain_engine.conversation.models import (
    ConversationRequest,
    PipelineState,
    ReservationContext,
)
from brain_engine.conversation.service import _reservation_status
from brain_engine.guardrails.operational_policies import (
    POLICIES,
    format_policies_for_prompt,
    policies_for_status,
)

# -- policies_for_status: lookup contract ---------------------------------


def test_policies_for_status_matches_plain_inquiry() -> None:
    """The "Status: inquiry preapproved" row covers plain ``"inquiry"``.

    Pre-fix the statuses tuple held only the *preapproved* variants
    so a request with status="inquiry" surfaced none of the
    SECURITY text.  This pins that plain "inquiry" now hits the
    same policy.
    """
    titles = {p.title for p in policies_for_status("inquiry")}
    assert "Status: inquiry preapproved (channel exceptions)" in titles


def test_policies_for_status_is_case_insensitive() -> None:
    """PMS sometimes returns "Inquiry" (capitalised) — same hits."""
    canonical = policies_for_status("inquiry")
    capitalised = policies_for_status("Inquiry")
    whitespace = policies_for_status("  inquiry  ")
    assert canonical == capitalised == whitespace
    assert capitalised  # not the empty tuple


def test_policies_for_status_empty_input_returns_empty() -> None:
    """Status-less requests must yield no policies (not the full 46)."""
    assert policies_for_status("") == ()
    assert policies_for_status("   ") == ()


def test_policies_for_status_unknown_returns_empty() -> None:
    """An unrecognised status forwards an empty tuple — never raises."""
    assert policies_for_status("not_a_real_status_code") == ()


# -- format_policies_for_prompt: renderer contract -----------------------


def test_format_policies_for_prompt_empty_returns_empty_string() -> None:
    """Empty input → empty string so the assembled prompt stays
    byte-identical for callers that hit no matching rule."""
    assert format_policies_for_prompt(()) == ""


def test_format_policies_for_prompt_renders_title_and_text() -> None:
    """Every policy in the input must surface its title and text."""
    inquiry_policy = next(
        p
        for p in POLICIES
        if p.title == "Status: inquiry preapproved (channel exceptions)"
    )
    rendered = format_policies_for_prompt((inquiry_policy,))
    assert "Status: inquiry preapproved" in rendered
    assert "SECURITY" in rendered
    assert "Wi-Fi passwords" in rendered


def test_format_policies_for_prompt_has_section_header() -> None:
    """Rendered text starts with a stable Markdown header so the LLM
    treats the block as instructions, not free-form context."""
    p = POLICIES[0]
    rendered = format_policies_for_prompt((p,))
    assert rendered.startswith("## Operational Policies")


# -- _assemble_prompt: integration with ConversationService --------------


def _make_state_for_status(status: str) -> PipelineState:
    """Build a minimal PipelineState carrying a reservation status."""
    request = ConversationRequest(
        customer_id="C1",
        property_id="P1",
        guest_id="G1",
        message="what is the wifi password",
        reservation_context=ReservationContext(status=status),
    )
    return PipelineState(request=request)


def test_reservation_status_helper_returns_empty_without_context() -> None:
    """Module-level helper must defend against missing context."""
    request = ConversationRequest(
        customer_id="C1",
        property_id="P1",
        guest_id="G1",
        message="hi",
    )
    assert _reservation_status(request) == ""


def test_reservation_status_helper_returns_raw_label() -> None:
    """The helper forwards the PMS label verbatim — case normalisation
    is handled by ``policies_for_status``, not the extractor."""
    request = ConversationRequest(
        customer_id="C1",
        property_id="P1",
        guest_id="G1",
        message="hi",
        reservation_context=ReservationContext(status="Inquiry"),
    )
    assert _reservation_status(request) == "Inquiry"


def test_assemble_prompt_includes_security_clause_for_inquiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: an Inquiry-status request renders a prompt that
    carries the SECURITY clause forbidding WiFi-password disclosure.

    This is the regression guard against the Sandbox-UI screenshot
    where the agent answered "1234" to an Inquiry-status guest.
    """
    # Build a service instance lazily; ConversationService.__init__
    # touches a lot of infra we do not need for prompt assembly, so
    # we instantiate the bare class and call the pure method.
    from brain_engine.conversation.service import ConversationService
    from brain_engine.customer.models import CustomerSettings

    svc = ConversationService.__new__(ConversationService)
    state = _make_state_for_status("Inquiry")
    settings = CustomerSettings(customer_id="C1")

    out = svc._assemble_prompt(state, settings)

    assert (
        "Status: inquiry preapproved (channel exceptions)"
        in out.active_operational_policies
    )
    assert "SECURITY" in out.system_prompt
    assert "Wi-Fi passwords" in out.system_prompt


def test_assemble_prompt_no_status_skips_policy_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A status-less request must not render the policy block —
    we keep prompts byte-identical for legacy / smoke callers that
    do not carry a reservation context."""
    from brain_engine.conversation.service import ConversationService
    from brain_engine.customer.models import CustomerSettings

    svc = ConversationService.__new__(ConversationService)
    request = ConversationRequest(
        customer_id="C1",
        property_id="P1",
        guest_id="G1",
        message="hi",
    )
    state = PipelineState(request=request)
    settings = CustomerSettings(customer_id="C1")

    out = svc._assemble_prompt(state, settings)

    assert out.active_operational_policies == []
    assert "## Operational Policies" not in out.system_prompt
