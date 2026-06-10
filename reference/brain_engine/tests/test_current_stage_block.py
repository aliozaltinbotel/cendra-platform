"""Tests for ``_format_current_stage_block`` — derived stage prompt.

The helper translates ``(check_in, check_out, current_time)`` into
a strict-rule block the LLM reads in the primacy slot of the system
prompt.  Three timeline buckets matter:

* ``current_time < check_in``  ⇒ PRE-ARRIVAL  block (block release).
* ``check_in <= current_time <= check_out`` ⇒ IN STAY block (allow).
* ``current_time > check_out`` ⇒ empty (R13 stale block handles it).

Plus short-circuits: an ``expired`` status defers to the R12 block;
any unparseable date returns empty so the prompt stays byte-
identical for callers without calendar context.

Sandbox tester report 2026-05-20 anchored this work: the brain
refused to release the door code when the message timestamp was
already after check-in (``current_time = 2026-06-11T08:00+03:00``,
``check_in = 2026-06-10``, ``check_out = 2026-06-13``) because the
literal status was ``confirmed`` and no other signal in the prompt
told the LLM the guest had already arrived.
"""

from __future__ import annotations

from brain_engine.conversation.prompt_formatters import (
    _CURRENT_STAGE_IN_STAY_BLOCK,
    _CURRENT_STAGE_PRE_ARRIVAL_BLOCK,
    _format_current_stage_block,
)


# ── Pre-arrival window ───────────────────────────────────────────


def test_pre_arrival_emits_block_with_release_blocker() -> None:
    block = _format_current_stage_block(
        status="confirmed",
        check_in="2026-06-15",
        check_out="2026-06-18",
        current_time="2026-06-10T12:00:00+03:00",
    )
    assert block == _CURRENT_STAGE_PRE_ARRIVAL_BLOCK
    assert "PRE-ARRIVAL" in block
    assert "must NOT be released" in block
    assert "door code" in block.lower()
    assert "wifi password" in block.lower()


# ── In-stay window ───────────────────────────────────────────────


def test_in_stay_after_check_in_emits_release_allowed_block() -> None:
    """The original tester repro: current_time is the day AFTER
    check_in and the literal status is ``confirmed``.  The derived
    block must say IN STAY and explicitly allow release."""
    block = _format_current_stage_block(
        status="confirmed",
        check_in="2026-06-10",
        check_out="2026-06-13",
        current_time="2026-06-11T08:00:00+03:00",
    )
    assert block == _CURRENT_STAGE_IN_STAY_BLOCK
    assert "IN STAY" in block
    assert "IS allowed to be released" in block
    assert "do not refuse" in block.lower()


def test_in_stay_check_in_day_emits_in_stay_block() -> None:
    """Boundary: ``current_time`` falls on the check-in day itself
    (same date).  The guest may already be onsite — treat as IN
    STAY rather than PRE-ARRIVAL."""
    block = _format_current_stage_block(
        status="confirmed",
        check_in="2026-06-10",
        check_out="2026-06-13",
        current_time="2026-06-10T15:30:00+03:00",
    )
    assert block == _CURRENT_STAGE_IN_STAY_BLOCK


def test_in_stay_check_out_day_emits_in_stay_block() -> None:
    """Boundary: ``current_time`` falls on the check-out day itself.
    The guest is still onsite until the wall-clock check-out time —
    treat as IN STAY, not stale."""
    block = _format_current_stage_block(
        status="confirmed",
        check_in="2026-06-10",
        check_out="2026-06-13",
        current_time="2026-06-13T09:45:00+03:00",
    )
    assert block == _CURRENT_STAGE_IN_STAY_BLOCK


# ── Post-checkout window ─────────────────────────────────────────


def test_post_checkout_returns_empty_to_defer_to_stale_block() -> None:
    """When ``current_time`` is strictly after ``check_out`` the
    stronger R13 stale block handles the deferral.  This helper
    must return empty so the two blocks do not double up."""
    block = _format_current_stage_block(
        status="confirmed",
        check_in="2026-06-10",
        check_out="2026-06-13",
        current_time="2026-06-15T10:00:00+03:00",
    )
    assert block == ""


# ── Short-circuits ──────────────────────────────────────────────


def test_expired_status_short_circuits_to_empty() -> None:
    """When the literal status is ``expired`` the R12 block already
    fires; this helper must not double up the hard deferral."""
    block = _format_current_stage_block(
        status="expired",
        check_in="2026-06-10",
        check_out="2026-06-13",
        current_time="2026-06-11T08:00:00+03:00",
    )
    assert block == ""


def test_expired_status_case_insensitive_short_circuit() -> None:
    block = _format_current_stage_block(
        status="  Expired ",
        check_in="2026-06-10",
        check_out="2026-06-13",
        current_time="2026-06-11T08:00:00+03:00",
    )
    assert block == ""


def test_empty_inputs_collapse_to_empty() -> None:
    assert (
        _format_current_stage_block(
            status="",
            check_in="",
            check_out="",
            current_time="",
        )
        == ""
    )


def test_missing_current_time_returns_empty() -> None:
    """Without a message timestamp the bucket cannot be computed,
    so the block must not assert any stage."""
    block = _format_current_stage_block(
        status="confirmed",
        check_in="2026-06-10",
        check_out="2026-06-13",
        current_time="",
    )
    assert block == ""


def test_unparseable_check_in_returns_empty() -> None:
    block = _format_current_stage_block(
        status="confirmed",
        check_in="next week",
        check_out="2026-06-13",
        current_time="2026-06-11",
    )
    assert block == ""


def test_date_only_inputs_work_without_timestamp() -> None:
    """The helper should accept bare ``YYYY-MM-DD`` inputs on every
    field (some PMS / sandbox setups ship plain dates)."""
    block = _format_current_stage_block(
        status="confirmed",
        check_in="2026-06-10",
        check_out="2026-06-13",
        current_time="2026-06-11",
    )
    assert block == _CURRENT_STAGE_IN_STAY_BLOCK


# ── Service-level re-export ─────────────────────────────────────


def test_helper_is_re_exported_from_service_module() -> None:
    """The conversation service module re-exports the helper for
    callers that import from the legacy path."""
    from brain_engine.conversation.service import (
        _format_current_stage_block as svc_helper,
    )

    assert svc_helper is _format_current_stage_block
