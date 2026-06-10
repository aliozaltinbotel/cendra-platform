"""Error hierarchy for the causal-navigation subsystem (Gap #3).

Errors are structured so FastAPI handlers can map them to HTTP codes
without inspecting error messages:

- :class:`CausalError` is the common root.
- :class:`CausalNavigationError` covers user-facing problems such as
  an unknown anchor or an invalid direction; the API translates it to
  4xx.
- :class:`CausalInferenceError` captures unexpected failures inside
  an inference rule; the API translates it to 503 with a stable body.
"""

from __future__ import annotations

__all__ = [
    "CausalError",
    "CausalInferenceError",
    "CausalNavigationError",
]


class CausalError(Exception):
    """Base class for every exception raised by the causal package."""


class CausalNavigationError(CausalError):
    """Raised when a navigation request cannot be satisfied.

    Typical causes: anchor event is not present in the graph, the
    requested direction is unsupported, or the depth parameter is
    outside the supported range.
    """


class CausalInferenceError(CausalError):
    """Raised when an inference rule fails unexpectedly.

    The builder catches per-rule failures and logs them, so this error
    only escapes when every rule crashes or when the builder itself is
    misconfigured.
    """
