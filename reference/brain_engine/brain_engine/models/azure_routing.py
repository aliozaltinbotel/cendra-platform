"""Azure OpenAI routing — single switch flipping all engine LLM traffic.

Most call sites talk to LLMs through ``litellm.acompletion(model="gpt-4o-mini",
...)``.  In production the tenant has only an Azure OpenAI resource —
public ``api.openai.com`` returns 401, so naked OpenAI calls fail closed
and the conversation pipeline falls back to error messages.

Touching every caller would mean editing ~30 files.  Instead this module
ships a one-call bootstrap (:func:`configure_litellm_for_azure`) that
sets the ``AZURE_API_*`` env vars litellm reads for ``azure/...`` models
and registers a model-alias map so unprefixed identifiers
(``gpt-4o-mini``, ``gpt-4o``) are rewritten on the fly to the tenant's
deployments.  After the bootstrap runs the same legacy call lands on
Azure transparently.

The module also exposes a small factory used by ``ProblemSolver``
and other call sites that build OpenAI clients directly and need an
explicit Azure-aware constructor.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

# Default API version — matches the value already pinned in the dev /
# prod deploy yaml so an unset env still picks the same Azure surface
# the rest of the engine talks to.
_DEFAULT_API_VERSION: Final[str] = "2024-08-01-preview"

# Default embedding deployment — Brain Engine targets text-embedding-3-large
# in production; deployed alongside the chat models on the tenant's
# ``botel-llm`` resource.  Falls back so the env can stay minimal in dev.
_DEFAULT_EMBEDDING_DEPLOYMENT: Final[str] = "text-embedding-3-large"


@dataclass(slots=True, frozen=True)
class AzureOpenAIConfig:
    """Snapshot of ``AZURE_OPENAI_*`` env vars.

    Frozen so a configured object can be cached at startup and shared
    across the process without anyone mutating it underneath the
    litellm wiring.
    """

    api_key: str = ""
    endpoint: str = ""
    api_version: str = _DEFAULT_API_VERSION
    chat_deployment: str = ""
    chat_mini_deployment: str = ""
    embedding_deployment: str = _DEFAULT_EMBEDDING_DEPLOYMENT

    def is_complete(self) -> bool:
        """Return ``True`` when the bare minimum for routing is present.

        ``chat_mini_deployment`` is the call site's lowest common
        denominator — almost every helper defaults to ``gpt-4o-mini``.
        Without it the alias map cannot rewrite the dominant traffic
        and the bootstrap must short-circuit.
        """
        return bool(self.api_key and self.endpoint and self.chat_mini_deployment)


def load_azure_openai_config() -> AzureOpenAIConfig:
    """Read ``AZURE_OPENAI_*`` env vars into an :class:`AzureOpenAIConfig`.

    Tolerates missing values: each field falls back to a sensible
    default rather than raising, so callers can call this at startup
    even on a partially-configured tenant and rely on
    :meth:`AzureOpenAIConfig.is_complete` for the readiness gate.
    """
    fallback_deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
    chat = (
        os.environ.get("AZURE_OPENAI_GPT4O_DEPLOYMENT", "").strip()
        or fallback_deployment.strip()
        or "gpt-4o"
    )
    chat_mini = (
        os.environ.get("AZURE_OPENAI_GPT4O_MINI_DEPLOYMENT", "").strip()
        or fallback_deployment.strip()
        or "gpt-4o-mini"
    )
    embedding = (
        os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "").strip()
        or _DEFAULT_EMBEDDING_DEPLOYMENT
    )
    return AzureOpenAIConfig(
        api_key=os.environ.get("AZURE_OPENAI_API_KEY", "").strip(),
        endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/"),
        api_version=(
            os.environ.get("AZURE_OPENAI_API_VERSION", "").strip()
            or _DEFAULT_API_VERSION
        ),
        chat_deployment=chat,
        chat_mini_deployment=chat_mini,
        embedding_deployment=embedding,
    )


def configure_litellm_for_azure(
    config: AzureOpenAIConfig | None = None,
) -> bool:
    """Route all litellm traffic to Azure OpenAI.

    Side effects:
        * Sets ``AZURE_API_KEY``, ``AZURE_API_BASE``, ``AZURE_API_VERSION``
          env vars consumed by litellm when ``model="azure/..."``.
        * Registers ``litellm.model_alias_map`` rewriting ``gpt-4o`` and
          ``gpt-4o-mini`` to ``azure/<deployment>`` so legacy callers
          land on Azure without a code change.

    The function never raises — when the config is incomplete or
    litellm is not importable the call is a no-op and a warning lands
    in the log so an operator can see the routing is degraded.

    Args:
        config: Pre-loaded snapshot.  When ``None`` the env is
            re-read so callers can wire startup once without having to
            thread the config explicitly.

    Returns:
        ``True`` when routing was installed, ``False`` otherwise.
    """
    cfg = config or load_azure_openai_config()
    if not cfg.is_complete():
        logger.warning(
            "azure_openai_routing_skipped — missing API key / endpoint "
            "/ chat-mini deployment; LLM callers will keep falling "
            "through to public OpenAI",
        )
        return False
    os.environ["AZURE_API_KEY"] = cfg.api_key
    os.environ["AZURE_API_BASE"] = cfg.endpoint
    os.environ["AZURE_API_VERSION"] = cfg.api_version
    try:
        import litellm  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "azure_openai_routing_skipped — litellm not installed",
        )
        return False
    alias_map = {
        "gpt-4o-mini": f"azure/{cfg.chat_mini_deployment}",
        "gpt-4o": f"azure/{cfg.chat_deployment}",
    }
    existing = getattr(litellm, "model_alias_map", None)
    if isinstance(existing, dict):
        existing.update(alias_map)
        litellm.model_alias_map = existing
    else:
        litellm.model_alias_map = alias_map
    logger.info(
        "azure_openai_routing_configured endpoint=%s chat=%s mini=%s "
        "embedding=%s",
        cfg.endpoint,
        cfg.chat_deployment,
        cfg.chat_mini_deployment,
        cfg.embedding_deployment,
    )
    return True


def build_async_azure_openai_client(config: AzureOpenAIConfig) -> Any:
    """Construct an ``openai.AsyncAzureOpenAI`` client.

    Centralised so call sites stop instantiating ``AsyncOpenAI``
    directly with ``OPENAI_API_KEY`` — that path bypasses the tenant's
    Azure routing and was producing 401s on the dev cluster.

    Raises:
        ImportError: When ``openai`` is not installed.
        ValueError: When the supplied config does not carry the
            minimum endpoint / api_key pair.
    """
    if not (config.api_key and config.endpoint):
        raise ValueError(
            "AzureOpenAIConfig is missing api_key or endpoint",
        )
    import openai  # type: ignore[import-not-found]

    return openai.AsyncAzureOpenAI(
        api_key=config.api_key,
        azure_endpoint=config.endpoint,
        api_version=config.api_version,
    )


__all__ = [
    "AzureOpenAIConfig",
    "build_async_azure_openai_client",
    "configure_litellm_for_azure",
    "load_azure_openai_config",
]
