"""Model capability profiles and pricing information.

Each supported model has a ``ModelProfile`` that describes its limits,
capabilities, and per-token pricing. The registry is used by the
router and cost-tracker to make informed decisions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """Static capability profile for an LLM model.

    Attributes:
        max_input_tokens: Maximum context-window tokens.
        max_output_tokens: Maximum completion tokens.
        supports_tools: Whether native tool-calling is available.
        supports_vision: Whether image inputs are accepted.
        supports_structured_output: Whether JSON-mode / schema is supported.
        cost_per_1k_input: USD cost per 1 000 input tokens.
        cost_per_1k_output: USD cost per 1 000 output tokens.
    """

    max_input_tokens: int
    max_output_tokens: int
    supports_tools: bool = True
    supports_vision: bool = False
    supports_structured_output: bool = False
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0


# ── OpenAI models ──────────────────────────────────────────────────────
_OPENAI_PROFILES: dict[str, ModelProfile] = {
    "gpt-4o": ModelProfile(
        max_input_tokens=128_000,
        max_output_tokens=16_384,
        supports_tools=True,
        supports_vision=True,
        supports_structured_output=True,
        cost_per_1k_input=0.0025,
        cost_per_1k_output=0.01,
    ),
    "gpt-4o-mini": ModelProfile(
        max_input_tokens=128_000,
        max_output_tokens=16_384,
        supports_tools=True,
        supports_vision=True,
        supports_structured_output=True,
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
    ),
    "gpt-4-turbo": ModelProfile(
        max_input_tokens=128_000,
        max_output_tokens=4_096,
        supports_tools=True,
        supports_vision=True,
        supports_structured_output=False,
        cost_per_1k_input=0.01,
        cost_per_1k_output=0.03,
    ),
}

# ── Anthropic models ───────────────────────────────────────────────────
_ANTHROPIC_PROFILES: dict[str, ModelProfile] = {
    "claude-opus-4-6": ModelProfile(
        max_input_tokens=200_000,
        max_output_tokens=32_000,
        supports_tools=True,
        supports_vision=True,
        supports_structured_output=True,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
    ),
    "claude-sonnet-4-6": ModelProfile(
        max_input_tokens=200_000,
        max_output_tokens=16_000,
        supports_tools=True,
        supports_vision=True,
        supports_structured_output=True,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
    ),
    "claude-haiku-4-5-20251001": ModelProfile(
        max_input_tokens=200_000,
        max_output_tokens=8_192,
        supports_tools=True,
        supports_vision=True,
        supports_structured_output=True,
        cost_per_1k_input=0.0008,
        cost_per_1k_output=0.004,
    ),
}

# ── Google models ──────────────────────────────────────────────────────
_GOOGLE_PROFILES: dict[str, ModelProfile] = {
    "gemini-2.5-flash": ModelProfile(
        max_input_tokens=1_000_000,
        max_output_tokens=65_536,
        supports_tools=True,
        supports_vision=True,
        supports_structured_output=True,
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
    ),
    "gemini-2.5-pro": ModelProfile(
        max_input_tokens=1_000_000,
        max_output_tokens=65_536,
        supports_tools=True,
        supports_vision=True,
        supports_structured_output=True,
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.005,
    ),
}

# ── Ollama (local) ─────────────────────────────────────────────────────
_OLLAMA_DEFAULT = ModelProfile(
    max_input_tokens=32_768,
    max_output_tokens=4_096,
    supports_tools=False,
    supports_vision=False,
    supports_structured_output=False,
    cost_per_1k_input=0.0,
    cost_per_1k_output=0.0,
)

# ── Unified registry ──────────────────────────────────────────────────
MODEL_PROFILES: dict[str, ModelProfile] = {
    **{f"openai:{k}": v for k, v in _OPENAI_PROFILES.items()},
    **{f"anthropic:{k}": v for k, v in _ANTHROPIC_PROFILES.items()},
    **{f"google_genai:{k}": v for k, v in _GOOGLE_PROFILES.items()},
}


def get_profile(provider: str, model: str) -> ModelProfile:
    """Look up the profile for a provider:model pair.

    Args:
        provider: Provider name (openai, anthropic, google_genai, ollama).
        model: Model identifier within the provider.

    Returns:
        Matching ``ModelProfile``, or a conservative default for Ollama /
        unknown models.
    """
    key = f"{provider}:{model}"
    if key in MODEL_PROFILES:
        return MODEL_PROFILES[key]
    if provider == "ollama":
        return _OLLAMA_DEFAULT
    return ModelProfile(
        max_input_tokens=128_000,
        max_output_tokens=4_096,
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.002,
    )
