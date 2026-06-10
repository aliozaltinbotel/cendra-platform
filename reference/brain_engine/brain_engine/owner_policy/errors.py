"""Exception hierarchy for the owner-policy DSL pipeline."""

from __future__ import annotations


__all__ = [
    "OwnerPolicyCompileError",
    "OwnerPolicyError",
    "OwnerPolicyParseError",
]


class OwnerPolicyError(Exception):
    """Base for every owner-policy DSL failure."""


class OwnerPolicyParseError(OwnerPolicyError):
    """Raised when the input does not match the DSL grammar."""


class OwnerPolicyCompileError(OwnerPolicyError):
    """Raised when a syntactically-valid AST is semantically wrong.

    Examples: an unknown style name, an unknown action kind in a
    ``forbid`` clause, or duplicate ``owner`` blocks for the same
    identifier in one document.
    """
