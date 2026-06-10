"""Factory for creating chat model instances from a provider:model string.

The ``init_chat_model`` function is the main public entry point for
obtaining a configured ``BaseChatModel`` instance.

Public ``api.openai.com`` is intentionally not registered as a
provider — the tenant routes all OpenAI traffic through Azure.

Example::

    llm = init_chat_model("anthropic:claude-sonnet-4-6")
    llm = init_chat_model("google_genai:gemini-2.5-flash")
    llm = init_chat_model("ollama:llama3")
    llm = init_chat_model(
        "azure_openai:gpt-4o-mini",
        azure_endpoint="https://botel-llm.openai.azure.com/",
        api_version="2024-08-01-preview",
        api_key="…",
    )
"""

from __future__ import annotations

from typing import Any

from brain_engine.models.base import BaseChatModel

# Provider name → (module_path, class_name)
_PROVIDER_REGISTRY: dict[str, tuple[str, str]] = {
    "anthropic": ("brain_engine.models.anthropic", "AnthropicChatModel"),
    "google_genai": ("brain_engine.models.google", "GoogleChatModel"),
    "ollama": ("brain_engine.models.ollama", "OllamaChatModel"),
    "azure_openai": (
        "brain_engine.models.azure_openai", "AzureOpenAIChatModel",
    ),
}

# Providers that take their model identifier under a non-default kwarg.
# Azure OpenAI uses ``deployment`` rather than ``model`` because the
# slot carries a deployment name, not a base model id.
_PROVIDER_MODEL_KW: dict[str, str] = {
    "azure_openai": "deployment",
}


def init_chat_model(
    model_string: str,
    **kwargs: Any,
) -> BaseChatModel:
    """Create a chat model from a ``provider:model`` string.

    Args:
        model_string: Format ``"provider:model_name"`` (e.g.
            ``"azure_openai:gpt-4o-mini"``).  If no provider prefix
            is given, defaults to ``azure_openai``.
        **kwargs: Additional keyword arguments forwarded to the
            provider constructor (e.g. ``temperature``, ``max_tokens``,
            ``api_key``).

    Returns:
        Configured ``BaseChatModel`` instance.

    Raises:
        ValueError: If the provider is not supported.
    """
    provider, model = _parse_model_string(model_string)
    cls = _resolve_provider_class(provider)
    model_kw = _PROVIDER_MODEL_KW.get(provider, "model")
    return cls(**{model_kw: model}, **kwargs)


def _parse_model_string(model_string: str) -> tuple[str, str]:
    """Split a model string into provider and model name.

    Args:
        model_string: E.g. ``"azure_openai:gpt-4o-mini"`` or just
            ``"gpt-4o"``.

    Returns:
        Tuple of (provider, model_name).
    """
    if ":" in model_string:
        provider, model = model_string.split(":", 1)
        return provider.strip(), model.strip()
    return "azure_openai", model_string.strip()


def _resolve_provider_class(provider: str) -> type[BaseChatModel]:
    """Lazily import and return the provider class.

    Args:
        provider: Provider identifier (azure_openai, anthropic, etc.).

    Returns:
        The provider's ``BaseChatModel`` subclass.

    Raises:
        ValueError: If the provider is not in the registry.
    """
    if provider not in _PROVIDER_REGISTRY:
        supported = ", ".join(sorted(_PROVIDER_REGISTRY))
        raise ValueError(
            f"Unknown provider '{provider}'. "
            f"Supported: {supported}"
        )

    module_path, class_name = _PROVIDER_REGISTRY[provider]

    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, class_name)
