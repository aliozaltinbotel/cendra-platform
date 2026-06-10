"""Protocol definitions for Brain Engine dependency injection.

Uses structural subtyping (PEP 544) so that concrete implementations
don't need to explicitly inherit from these protocols — they just
need to have the right methods.

This follows the Dependency Inversion Principle from the guide:
high-level modules depend on abstractions, not concrete implementations.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    """Protocol for notification backends (Telegram, WhatsApp, etc.).

    Any class with a send_message method matching this signature
    satisfies this protocol.
    """

    async def send_message(
        self,
        *,
        target: str = "",
        chat_id: int = 0,
        text: str = "",
        owner_id: str = "",
    ) -> Any:
        """Send a text message to a target."""
        ...

    async def send_approval_request(
        self,
        *,
        owner_id: str,
        message: str,
        request_id: str,
    ) -> Any:
        """Send an approval request notification."""
        ...


@runtime_checkable
class VoiceClient(Protocol):
    """Protocol for voice call backends (ElevenLabs, etc.)."""

    async def make_call(
        self,
        *,
        phone_number: str,
        script: str = "",
        first_message: str = "",
        agent_phone_number_id: str | None = None,
    ) -> Any:
        """Initiate an outbound phone call."""
        ...

    async def get_call_status(self, call_id: str) -> Any:
        """Get status of an active/completed call."""
        ...

    async def get_transcript(self, call_id: str) -> Any:
        """Get transcript of a completed call."""
        ...


@runtime_checkable
class SlotStore(Protocol):
    """Protocol for slot value storage (SlotManager, etc.)."""

    def get_value(self, key: str, default: Any = None) -> Any:
        """Get a slot value by key."""
        ...

    def set_slot(self, key: str, value: Any) -> None:
        """Set a slot value."""
        ...

    def to_dict(self) -> dict[str, Any]:
        """Export all slots as a dict."""
        ...


@runtime_checkable
class MemoryStore(Protocol):
    """Protocol for memory backends (Redis, in-memory, etc.)."""

    async def get(self, key: str) -> Any:
        """Get a value by key."""
        ...

    async def set(self, key: str, value: Any) -> None:
        """Set a value by key."""
        ...

    async def keys(self, pattern: str) -> list[str]:
        """List keys matching a pattern."""
        ...


@runtime_checkable
class EventEmitter(Protocol):
    """Protocol for AG-UI event emission."""

    def flow_started(self, flow_name: str, state: str) -> Any:
        """Emit flow_started event."""
        ...

    def flow_completed(self, flow_name: str, result: dict[str, Any]) -> Any:
        """Emit flow_completed event."""
        ...

    def text_message_start(self) -> Any:
        """Start a text message."""
        ...

    def text_message_content(self, content: str) -> Any:
        """Emit text content."""
        ...

    def text_message_end(self) -> Any:
        """End a text message."""
        ...

    def slot_filled(self, slot_name: str, value: Any) -> Any:
        """Emit slot filled event."""
        ...
