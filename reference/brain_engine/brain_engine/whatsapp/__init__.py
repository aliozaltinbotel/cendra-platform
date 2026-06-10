"""WhatsApp channel — booking assistant without property context."""

from brain_engine.whatsapp.channel_service import (
    BookingParameters,
    WhatsAppRequest,
    WhatsAppResponse,
    process_whatsapp_message,
)

__all__ = [
    "BookingParameters",
    "WhatsAppRequest",
    "WhatsAppResponse",
    "process_whatsapp_message",
]
