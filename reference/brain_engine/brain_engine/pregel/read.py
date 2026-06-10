"""Channel Read — reads state from channels into node input.

Provides the read interface that nodes use to access channel
values during execution. Supports selective reading (only
subscribed channels) and fresh reads (current step only).
"""

from __future__ import annotations

from typing import Any

from brain_engine.channels.base import BaseChannel, EmptyChannelError


def read_channels(
    channels: dict[str, BaseChannel[Any, Any, Any]],
    select: str | list[str],
) -> Any:
    """Read values from one or more channels.

    Args:
        channels: All available channels.
        select: Single channel name or list of names.

    Returns:
        Single value (if str) or dict of values (if list).
    """
    if isinstance(select, str):
        return _read_single(channels, select)
    return _read_multiple(channels, select)


def read_available(
    channels: dict[str, BaseChannel[Any, Any, Any]],
    select: list[str],
) -> dict[str, Any]:
    """Read only channels that have available values.

    Skips channels that would raise EmptyChannelError.

    Args:
        channels: All available channels.
        select: Channel names to attempt reading.

    Returns:
        Dict of channel_name -> value for available channels.
    """
    result: dict[str, Any] = {}
    for name in select:
        ch = channels.get(name)
        if ch and ch.is_available():
            result[name] = ch.get()
    return result


# ── Internal ──────────────────────────────────────────────────────── #


def _read_single(
    channels: dict[str, BaseChannel[Any, Any, Any]],
    name: str,
) -> Any:
    """Read a single channel value.

    Args:
        channels: All channels.
        name: Channel to read.

    Returns:
        Channel value.

    Raises:
        EmptyChannelError: If channel not found or empty.
    """
    ch = channels.get(name)
    if ch is None:
        raise EmptyChannelError(f"Channel '{name}' not found")
    return ch.get()


def _read_multiple(
    channels: dict[str, BaseChannel[Any, Any, Any]],
    names: list[str],
) -> dict[str, Any]:
    """Read multiple channels into a dict.

    Args:
        channels: All channels.
        names: Channel names to read.

    Returns:
        Dict of name -> value.

    Raises:
        EmptyChannelError: If any channel is missing or empty.
    """
    result: dict[str, Any] = {}
    for name in names:
        result[name] = _read_single(channels, name)
    return result
