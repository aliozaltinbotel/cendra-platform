"""SubAgentRegistry — discovers and manages available subagent types.

Maintains a registry of SubAgentSpec objects. The parent agent
queries the registry to discover what subagents are available,
and the runner uses it to look up specs by name.
"""

from __future__ import annotations

import logging
from typing import Any

from brain_engine.subagents.models import GENERAL_PURPOSE_SPEC, SubAgentSpec

logger = logging.getLogger(__name__)


class SubAgentRegistry:
    """Registry of available subagent types.

    Provides registration, discovery, and lookup of SubAgentSpec
    objects. Automatically includes the general-purpose subagent.

    Args:
        include_default: Whether to register the default general-purpose agent.
    """

    def __init__(self, include_default: bool = True) -> None:
        self._specs: dict[str, SubAgentSpec] = {}
        if include_default:
            self.register(GENERAL_PURPOSE_SPEC)

    @property
    def count(self) -> int:
        """Return the number of registered subagents."""
        return len(self._specs)

    @property
    def names(self) -> list[str]:
        """Return sorted list of registered subagent names."""
        return sorted(self._specs.keys())

    def register(self, spec: SubAgentSpec) -> None:
        """Register a subagent specification.

        Args:
            spec: SubAgentSpec to register.

        Raises:
            ValueError: If a spec with the same name is already registered.
        """
        if spec.name in self._specs:
            msg = f"Subagent '{spec.name}' already registered"
            raise ValueError(msg)
        self._specs[spec.name] = spec
        logger.debug("Registered subagent: %s", spec.name)

    def unregister(self, name: str) -> bool:
        """Remove a subagent by name.

        Args:
            name: Subagent name to remove.

        Returns:
            True if removed, False if not found.
        """
        if name not in self._specs:
            return False
        del self._specs[name]
        logger.debug("Unregistered subagent: %s", name)
        return True

    def get(self, name: str) -> SubAgentSpec | None:
        """Look up a subagent spec by name.

        Args:
            name: Subagent name.

        Returns:
            SubAgentSpec or None if not found.
        """
        return self._specs.get(name)

    def get_or_raise(self, name: str) -> SubAgentSpec:
        """Look up a subagent spec, raising if not found.

        Args:
            name: Subagent name.

        Returns:
            SubAgentSpec.

        Raises:
            KeyError: If the subagent is not registered.
        """
        spec = self._specs.get(name)
        if spec is None:
            available = ", ".join(self.names)
            msg = (
                f"Subagent '{name}' not found. "
                f"Available: {available}"
            )
            raise KeyError(msg)
        return spec

    def list_specs(self) -> list[SubAgentSpec]:
        """Return all registered specs sorted by name.

        Returns:
            List of SubAgentSpec objects.
        """
        return [self._specs[n] for n in self.names]

    def build_tool_description(self) -> str:
        """Build a description of all subagents for the task tool.

        Returns:
            Multi-line string describing available subagent types.
        """
        lines: list[str] = []
        for spec in self.list_specs():
            lines.append(spec.to_tool_description())
        return "\n".join(lines)

    def has(self, name: str) -> bool:
        """Check if a subagent is registered."""
        return name in self._specs
