"""ContextAssembler — three-section memory layout for LLM context windows.

Arranges memory into three sections that exploit LLM attention patterns:

  [ESTABLISHED FACTS]      — extracted facts (Mem0/SemanticMemory), placed first
  [CONVERSATION SUMMARY]   — compressed older messages (ContextSummarizer)
  [RECENT MESSAGES]        — last N verbatim messages (high recency)

Why facts first:  "Lost in the Middle" research shows LLMs lose 40%+ accuracy
when facts are buried mid-context.  Placing facts at the top leverages primacy
bias — the model attends most strongly to the beginning of the context.

Token budget is enforced per-section so one bloated section can't starve the
others.  If total exceeds the budget, sections are trimmed in reverse priority
(summary first, then facts, recent messages are never trimmed).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

_TEMPLATE_DIR: Final[Path] = Path(__file__).parent.parent / "prompt_assembler" / "templates"
_TEMPLATE_NAME: Final[str] = "context_layout.txt"

# Default token budget per section (rough char-based estimate: 1 token ~ 4 chars)
_CHARS_PER_TOKEN: Final[int] = 4
_DEFAULT_TOTAL_BUDGET: Final[int] = 3000    # tokens
_DEFAULT_FACTS_RATIO: Final[float] = 0.30   # 30% for facts
_DEFAULT_SUMMARY_RATIO: Final[float] = 0.35  # 35% for summary
_DEFAULT_RECENT_RATIO: Final[float] = 0.35   # 35% for recent messages


@dataclass(frozen=True, slots=True)
class SectionBudget:
    """Per-section token budget computed from ratios.

    Attributes:
        facts: Max tokens for the facts section.
        summary: Max tokens for the summary section.
        recent: Max tokens for recent messages.
        total: Total budget across all sections.
    """

    facts: int
    summary: int
    recent: int
    total: int


@dataclass(frozen=True, slots=True)
class AssembledContext:
    """Result of context assembly.

    Attributes:
        text: Fully rendered context string ready for injection into the prompt.
        facts_count: Number of facts included.
        summary_tokens_est: Estimated token count for the summary section.
        recent_count: Number of recent messages included.
        total_chars: Total character count of the assembled context.
        sections_trimmed: Whether any section was trimmed to fit the budget.
    """

    text: str
    facts_count: int = 0
    summary_tokens_est: int = 0
    recent_count: int = 0
    total_chars: int = 0
    sections_trimmed: bool = False


class ContextAssembler:
    """Assembles three-section memory context for the LLM prompt.

    Combines established facts, conversation summary, and recent messages
    into a structured context block.  Renders via Jinja2 template for
    easy customization without code changes.

    Args:
        total_budget: Total token budget for the context block.
        facts_ratio: Fraction of budget allocated to facts (0.0-1.0).
        summary_ratio: Fraction allocated to conversation summary.
        recent_ratio: Fraction allocated to recent messages.
        template_dir: Override for the Jinja2 template directory.
    """

    def __init__(
        self,
        total_budget: int = _DEFAULT_TOTAL_BUDGET,
        facts_ratio: float = _DEFAULT_FACTS_RATIO,
        summary_ratio: float = _DEFAULT_SUMMARY_RATIO,
        recent_ratio: float = _DEFAULT_RECENT_RATIO,
        template_dir: Path | None = None,
    ) -> None:
        # Validate ratios sum to ~1.0
        ratio_sum = facts_ratio + summary_ratio + recent_ratio
        if not (0.99 <= ratio_sum <= 1.01):
            msg = f"Section ratios must sum to 1.0, got {ratio_sum:.2f}"
            raise ValueError(msg)

        self._budget = SectionBudget(
            facts=int(total_budget * facts_ratio),
            summary=int(total_budget * summary_ratio),
            recent=int(total_budget * recent_ratio),
            total=total_budget,
        )

        tpl_dir = template_dir or _TEMPLATE_DIR
        self._env = Environment(
            loader=FileSystemLoader(str(tpl_dir)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )

    # ── Public API ───────────────────────────────────────────── #

    def assemble(
        self,
        facts: Sequence[str] = (),
        summary: str = "",
        recent_messages: Sequence[dict[str, str]] = (),
    ) -> AssembledContext:
        """Build the three-section context block.

        Each section is trimmed to its token budget.  If the caller provides
        no data for a section, that section is omitted from the output.

        Args:
            facts: Extracted facts (from Mem0/SemanticMemory).
            summary: Compressed conversation summary.
            recent_messages: Last N messages as {"role": ..., "content": ...}.

        Returns:
            AssembledContext with rendered text and metadata.
        """
        trimmed = False

        # Trim facts to budget
        trimmed_facts, facts_trimmed = self._trim_facts(facts)
        trimmed = trimmed or facts_trimmed

        # Trim summary to budget
        trimmed_summary, summary_trimmed = self._trim_text(
            summary, self._budget.summary,
        )
        trimmed = trimmed or summary_trimmed

        # Recent messages: take from the end (most recent first)
        trimmed_recent = self._trim_recent(recent_messages)

        # Render via Jinja2
        template = self._env.get_template(_TEMPLATE_NAME)
        rendered = template.render(
            facts=trimmed_facts,
            summary=trimmed_summary,
            recent_messages=trimmed_recent,
        ).strip()

        result = AssembledContext(
            text=rendered,
            facts_count=len(trimmed_facts),
            summary_tokens_est=self._estimate_tokens(trimmed_summary),
            recent_count=len(trimmed_recent),
            total_chars=len(rendered),
            sections_trimmed=trimmed,
        )

        logger.debug(
            "Context assembled: %d facts, %d summary tokens, %d recent msgs, "
            "%d total chars, trimmed=%s",
            result.facts_count,
            result.summary_tokens_est,
            result.recent_count,
            result.total_chars,
            result.sections_trimmed,
        )

        return result

    @property
    def budget(self) -> SectionBudget:
        """Current section budget (read-only)."""
        return self._budget

    # ── Trimming helpers ─────────────────────────────────────── #

    def _trim_facts(
        self,
        facts: Sequence[str],
    ) -> tuple[list[str], bool]:
        """Keep as many facts as fit within the facts budget.

        Facts are added in order until the budget is exhausted.
        Returns the kept facts and whether trimming occurred.
        """
        max_chars = self._budget.facts * _CHARS_PER_TOKEN
        kept: list[str] = []
        char_count = 0

        for fact in facts:
            # +4 accounts for "- " prefix and newline
            cost = len(fact) + 4
            if char_count + cost > max_chars:
                return kept, True
            kept.append(fact)
            char_count += cost

        return kept, False

    def _trim_text(
        self,
        text: str,
        budget_tokens: int,
    ) -> tuple[str, bool]:
        """Truncate text to fit within a token budget."""
        if not text:
            return "", False
        max_chars = budget_tokens * _CHARS_PER_TOKEN
        if len(text) <= max_chars:
            return text, False
        return text[:max_chars] + "...", True

    def _trim_recent(
        self,
        messages: Sequence[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Keep the most recent messages that fit within the recent budget.

        Iterates from newest to oldest, keeping messages until the budget
        is exhausted, then reverses to maintain chronological order.
        """
        max_chars = self._budget.recent * _CHARS_PER_TOKEN
        kept: list[dict[str, str]] = []
        char_count = 0

        for msg in reversed(messages):
            content = msg.get("content", "")
            role = msg.get("role", "")
            # Overhead: "ROLE: content\n"
            cost = len(role) + len(content) + 3
            if char_count + cost > max_chars:
                break
            kept.append(msg)
            char_count += cost

        kept.reverse()
        return kept

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimate (1 token ~ 4 chars)."""
        return len(text) // _CHARS_PER_TOKEN if text else 0
