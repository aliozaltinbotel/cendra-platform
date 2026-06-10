"""Tenant utilities — workspace-scoped Redis key prefixes.

Multi-tenancy in Brain Engine uses workspace_id to scope all Redis
keys. When workspace_id is empty, the original key pattern is used
for backward compatibility.

Pattern:
    Without workspace: brain:proc:{id}
    With workspace:    brain:{workspace_id}:proc:{id}
"""

from __future__ import annotations


def build_prefix(base: str, workspace_id: str = "") -> str:
    """Build a Redis key prefix with optional workspace scoping.

    Args:
        base: Base prefix (e.g., "brain:proc:").
        workspace_id: Workspace ID for multi-tenancy.

    Returns:
        Scoped prefix string. Original if workspace_id is empty.
    """
    if not workspace_id:
        return base
    return base.replace("brain:", f"brain:{workspace_id}:", 1)
