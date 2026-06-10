"""State Manager - Tracks conversation state, slots, and deduplication."""

from brain_engine.state_manager.slot_manager import SlotManager, SlotInfo
from brain_engine.state_manager.state_machine import StateMachine
from brain_engine.state_manager.dedup_checker import DedupChecker

__all__ = ["SlotManager", "SlotInfo", "StateMachine", "DedupChecker"]
