"""Context module — context engineering with BrainZFS integration.

Implements intelligent context management for the Brain Engine:
automatic offloading of large tool results to COW storage,
conversation summarization with rollback safety, token budget
management, and progressive disclosure.

Components:
    - ContextManager: Main orchestrator for context lifecycle.
    - Offloader: Tool result offloading strategy.
    - ContextSummarizer: Conversation compression with snapshots.
    - TokenCounter: Token budget tracking and estimation.
"""

from brain_engine.context.manager import ContextManager
from brain_engine.context.offloader import Offloader, OffloadResult
from brain_engine.context.summarizer import ContextSummarizer, SummaryResult
from brain_engine.context.token_counter import TokenCounter

__all__ = [
    "ContextManager",
    "ContextSummarizer",
    "Offloader",
    "OffloadResult",
    "SummaryResult",
    "TokenCounter",
]
