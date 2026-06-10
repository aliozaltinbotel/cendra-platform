"""Optional LLM-based narrative rewriter.

Takes the deterministic text produced by :class:`TextRenderer` and asks
a :class:`~brain_engine.models.base.BaseChatModel` to re-voice it with
cause-and-effect phrasing.  The renderer is **opt-in**: callers must
pass ``use_llm=True`` on the service, and the endpoint defaults to
``False`` so the system stays fully functional when no API key is set.

Graceful degradation: any exception from the chat model is swallowed
and the skeleton text is returned unchanged.  The narrative layer must
never fail because of an optional enrichment.
"""

from __future__ import annotations

from typing import Final, Sequence

import structlog

from brain_engine.narrative.models import RenderStyle, TimelineEvent

__all__ = ["LLMNarrativeRenderer"]


logger = structlog.get_logger(__name__)


_DEFAULT_SYSTEM_PROMPT: Final[str] = (
    "You rewrite a property operations log into a short, cohesive "
    "narrative for a property owner. Rules:\n"
    "1. Preserve every date exactly as given.\n"
    "2. Do not introduce facts that are not in the skeleton.\n"
    "3. Prefer cause-and-effect phrasing when two events are related.\n"
    "4. Return plain text only — no Markdown, no headings, no emoji.\n"
    "5. Keep the same language as the skeleton."
)

_DEFAULT_MAX_TOKENS: Final[int] = 800


class LLMNarrativeRenderer:
    """Rewrites a deterministic skeleton with an LLM.

    The rewriter is deliberately conservative: the system prompt
    forbids new facts and the skeleton itself is embedded in the user
    message so the model always has the ground truth available.
    """

    def __init__(
        self,
        chat_model: object,
        *,
        system_prompt: str | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        self._chat_model = chat_model
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT
        self._max_tokens = max_tokens

    async def rewrite(
        self,
        skeleton: str,
        events: Sequence[TimelineEvent],
        *,
        style: RenderStyle = RenderStyle.CONCISE,
    ) -> str:
        """Return a rewritten version of ``skeleton`` (or the skeleton)."""
        if not skeleton.strip():
            return skeleton

        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": self._build_user_prompt(skeleton, events, style),
            },
        ]
        try:
            model = self._chat_model
            response = await model.invoke(messages)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - graceful degradation
            logger.warning(
                "narrative.llm_rewrite_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return skeleton

        content = getattr(response, "content", "") or ""
        rewritten = content.strip()
        return rewritten or skeleton

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #

    def _build_user_prompt(
        self,
        skeleton: str,
        events: Sequence[TimelineEvent],
        style: RenderStyle,
    ) -> str:
        verbosity = (
            "Keep it brief — one short paragraph."
            if style is RenderStyle.CONCISE
            else "You may use two or three short paragraphs."
        )
        return (
            f"{verbosity}\n"
            f"Total events: {len(events)}.\n\n"
            f"Skeleton to rewrite:\n{skeleton}"
        )
