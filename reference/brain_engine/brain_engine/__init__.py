"""
Brain Engine - Universal AI Agent Chassis

A production-ready framework for building intelligent conversational agents.
Incorporates memory systems inspired by Mem0, A-MEM (Zettelkasten), and
layered memory architecture from autonomous memory systems research.
"""

__version__ = "0.1.0"
__author__ = "Cedra"

from brain_engine.intent_controller.classifier import IntentClassifier
from brain_engine.intent_controller.intents import Intent
from brain_engine.state_manager.slot_manager import SlotManager, SlotInfo
from brain_engine.state_manager.state_machine import StateMachine
from brain_engine.state_manager.dedup_checker import DedupChecker
from brain_engine.memory.semantic_memory import SemanticMemory
from brain_engine.memory.episodic_memory import EpisodicMemory
from brain_engine.memory.working_memory import WorkingMemory
from brain_engine.memory.context_compressor import ContextCompressor
from brain_engine.prompt_assembler.assembler import PromptAssembler
from brain_engine.guardrails.repeat_check import RepeatCheck
from brain_engine.guardrails.hallucination_check import HallucinationCheck
from brain_engine.guardrails.format_check import FormatCheck
from brain_engine.guardrails.regenerator import Regenerator
from brain_engine.streaming.ag_ui_emitter import AGUIEmitter
from brain_engine.streaming.event_types import EventType
from brain_engine.streaming.state_broadcaster import StateBroadcaster

__all__ = [
    "IntentClassifier",
    "Intent",
    "SlotManager",
    "SlotInfo",
    "StateMachine",
    "DedupChecker",
    "SemanticMemory",
    "EpisodicMemory",
    "WorkingMemory",
    "ContextCompressor",
    "PromptAssembler",
    "RepeatCheck",
    "HallucinationCheck",
    "FormatCheck",
    "Regenerator",
    "AGUIEmitter",
    "EventType",
    "StateBroadcaster",
]
