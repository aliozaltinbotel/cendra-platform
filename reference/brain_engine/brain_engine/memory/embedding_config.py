"""Env-driven embedding model configuration (Sprint A).

Lets a deploy override the default ``all-MiniLM-L6-v2`` (384-dim)
encoder used by :class:`brain_engine.memory.semantic_memory.SemanticMemory`
without code changes.  The production target is
``mixedbread-ai/mxbai-embed-large-v1`` (1024-dim), which lifts
retrieval quality on the Botel multilingual guest base but ships
~700 MB of weights — so the swap is opt-in via ``BRAIN_EMBEDDING_MODEL``.

When the env var is unset the helpers below return the exact
pre-Sprint-A defaults, so existing pods, tests and Qdrant
collections built around 384-dim vectors keep working unchanged.

**Migration warning:** flipping ``BRAIN_EMBEDDING_MODEL`` to the
1024-dim variant *without* re-embedding existing Qdrant collections
will break similarity search at runtime.  Use the dedicated re-embed
job (Sprint A migration script, separate ticket) before flipping the
flag in production.
"""

from __future__ import annotations

import logging
import os
from typing import Final

logger = logging.getLogger(__name__)


# Pre-Sprint-A defaults — keep in sync with
# ``brain_engine.memory.semantic_memory._DEFAULT_EMBEDDING_MODEL`` and
# ``_DEFAULT_EMBEDDING_DIM``.  Changing either constant here without
# updating the corresponding default in semantic_memory.py is a bug.
DEFAULT_EMBEDDING_MODEL: Final[str] = "all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_DIM: Final[int] = 384

# Sprint A target.  1024-dim mxbai requires re-embedding any Qdrant
# collection originally created at 384 dims — see migration warning
# in the module docstring.
SPRINT_A_EMBEDDING_MODEL: Final[str] = (
    "mixedbread-ai/mxbai-embed-large-v1"
)
SPRINT_A_EMBEDDING_DIM: Final[int] = 1024

_MODEL_ENV: Final[str] = "BRAIN_EMBEDDING_MODEL"
_DIM_ENV: Final[str] = "BRAIN_EMBEDDING_DIM"

# Known model -> dim mapping so a deploy can pin just the model and
# get the matching dim resolved automatically.  An unknown model
# falls back to ``DEFAULT_EMBEDDING_DIM`` with a warning — operators
# should always set ``BRAIN_EMBEDDING_DIM`` explicitly when shipping
# a model that is not in this table.
_KNOWN_DIMS: Final[dict[str, int]] = {
    DEFAULT_EMBEDDING_MODEL: DEFAULT_EMBEDDING_DIM,
    SPRINT_A_EMBEDDING_MODEL: SPRINT_A_EMBEDDING_DIM,
}


def resolve_embedding_model() -> str:
    """Return the embedding model name to use.

    Honours ``BRAIN_EMBEDDING_MODEL`` when set and non-empty; falls
    back to the pre-Sprint-A default otherwise.
    """
    raw = os.environ.get(_MODEL_ENV, "").strip()
    return raw or DEFAULT_EMBEDDING_MODEL


def resolve_embedding_dim() -> int:
    """Return the embedding dimensionality matching the chosen model.

    Resolution order:

    1. Explicit ``BRAIN_EMBEDDING_DIM`` overrides everything.  Raises
       :class:`ValueError` if the env value is not a positive int —
       a malformed dim would silently corrupt collection creation.
    2. Otherwise look up the resolved model in :data:`_KNOWN_DIMS`.
    3. Otherwise fall back to :data:`DEFAULT_EMBEDDING_DIM` and warn.
       Operators shipping an unknown model must set the dim env var
       explicitly to avoid a Qdrant collection mismatch.
    """
    raw = os.environ.get(_DIM_ENV, "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(
                f"{_DIM_ENV} must be a positive integer, got {raw!r}",
            ) from exc
        if value <= 0:
            raise ValueError(
                f"{_DIM_ENV} must be a positive integer, got {value}",
            )
        return value

    model = resolve_embedding_model()
    if model in _KNOWN_DIMS:
        return _KNOWN_DIMS[model]

    logger.warning(
        "Unknown embedding model %r — falling back to dim=%d. "
        "Set %s explicitly to silence this warning.",
        model,
        DEFAULT_EMBEDDING_DIM,
        _DIM_ENV,
    )
    return DEFAULT_EMBEDDING_DIM


__all__ = [
    "DEFAULT_EMBEDDING_DIM",
    "DEFAULT_EMBEDDING_MODEL",
    "SPRINT_A_EMBEDDING_DIM",
    "SPRINT_A_EMBEDDING_MODEL",
    "resolve_embedding_dim",
    "resolve_embedding_model",
]
