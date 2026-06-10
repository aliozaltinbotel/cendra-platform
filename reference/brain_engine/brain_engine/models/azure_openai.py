"""Azure OpenAI chat model provider.

Same wire protocol as :class:`OpenAIChatModel` — Azure OpenAI exposes
the OpenAI Chat Completions API via the official ``openai`` SDK's
:class:`openai.AsyncAzureOpenAI` client.  The differences sit in the
constructor (endpoint + api version + deployment name) and in how the
``model`` slot is interpreted: for Azure it carries the **deployment
name**, not the underlying model id.

Brain Engine uses this provider for the V1 onboarding sandbox replay
generator so calls land on the tenant's ``botel-llm`` Azure OpenAI
resource instead of the public OpenAI API.
"""

from __future__ import annotations

import logging

import openai

from brain_engine.models.base import BaseChatModel
from brain_engine.models.openai import OpenAIChatModel

logger = logging.getLogger(__name__)


class AzureOpenAIChatModel(OpenAIChatModel):
    """Azure-OpenAI-backed chat model.

    Args:
        deployment: Azure deployment name (e.g. ``gpt-4o-mini``).
            Stored as ``self.model`` and forwarded into the
            ``model=`` field of ``chat.completions.create``.
        azure_endpoint: Resource endpoint, e.g.
            ``https://botel-llm.openai.azure.com/``.
        api_version: Azure OpenAI API version, e.g.
            ``2024-08-01-preview``.
        api_key: Azure OpenAI key.  Falls back to the
            ``AZURE_OPENAI_API_KEY`` env var when ``None``.
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens.
    """

    def __init__(
        self,
        deployment: str,
        *,
        azure_endpoint: str,
        api_version: str,
        api_key: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> None:
        """Initialize AzureOpenAIChatModel."""
        # Bypass OpenAIChatModel.__init__ — it pins provider="openai".
        BaseChatModel.__init__(
            self,
            model=deployment,
            provider="azure_openai",
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self._api_key = api_key
        self._azure_endpoint = azure_endpoint
        self._api_version = api_version
        self._client: openai.AsyncAzureOpenAI | None = None  # type: ignore[assignment]

    @property
    def client(self) -> openai.AsyncAzureOpenAI:  # type: ignore[override]
        """Lazily create the Azure OpenAI client."""
        if self._client is None:
            self._client = openai.AsyncAzureOpenAI(
                api_key=self._api_key,
                azure_endpoint=self._azure_endpoint,
                api_version=self._api_version,
            )
        return self._client
