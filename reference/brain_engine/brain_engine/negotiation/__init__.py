"""Negotiation subsystem — true ops-autonomy (Gap #4).

Orchestrates bounded multi-round negotiations with cleaners and
vendors.  Channel-agnostic by design: callers inject async ``send``
and ``receive`` callables so the same orchestrator drives WhatsApp,
Telegram or ElevenLabs voice sessions.
"""

from brain_engine.negotiation.models import (
    NegotiationDecision,
    NegotiationOffer,
    NegotiationOutcome,
    NegotiationRound,
    NegotiationTarget,
)
from brain_engine.negotiation.orchestrator import Negotiator
from brain_engine.negotiation.session import (
    NegotiationSession,
    NegotiationSessionManager,
)
from brain_engine.negotiation.vendor_channels import (
    VendorChannelRegistry,
    VendorChannelSpec,
)

__all__ = [
    "NegotiationDecision",
    "NegotiationOffer",
    "NegotiationOutcome",
    "NegotiationRound",
    "NegotiationSession",
    "NegotiationSessionManager",
    "NegotiationTarget",
    "Negotiator",
    "VendorChannelRegistry",
    "VendorChannelSpec",
]
