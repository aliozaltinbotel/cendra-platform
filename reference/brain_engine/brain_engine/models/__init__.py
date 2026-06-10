"""Unified LLM provider system for Brain Engine.

Provides a consistent interface across Azure OpenAI, Anthropic,
Google, and Ollama backends.  Use ``init_chat_model`` factory to
instantiate any supported provider via ``"provider:model"``
syntax.  Public ``api.openai.com`` is intentionally not registered
— Azure OpenAI is the sole OpenAI surface.

Example::

    from brain_engine.models import init_chat_model

    llm = init_chat_model(
        "azure_openai:gpt-4o-mini",
        api_key="…",
        azure_endpoint="https://botel-llm.openai.azure.com/",
        api_version="2024-08-01-preview",
        temperature=0.3,
    )
    response = await llm.invoke([{"role": "user", "content": "Hello"}])
"""

from brain_engine.models.base import BaseChatModel
from brain_engine.models.factory import init_chat_model
from brain_engine.models.messages import AIMessage, ToolCall, TokenUsage
from brain_engine.models.profiles import ModelProfile

__all__ = [
    "BaseChatModel",
    "init_chat_model",
    "AIMessage",
    "ToolCall",
    "TokenUsage",
    "ModelProfile",
]
