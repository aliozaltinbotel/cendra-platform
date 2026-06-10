"""MCPClient — consume external Model Context Protocol servers.

Connects to MCP-compliant tool servers (Cendra, external APIs)
and exposes their tools as callable functions for the agent.

Example::

    client = MCPClient()
    client.add_server(MCPServerConfig(
        name="cendra",
        url="https://api.cendra.ai/mcp",
        api_key="...",
    ))
    tools = await client.list_tools()
    result = await client.call_tool("searchKB", {"query": "wifi"})

Based on: LangChain MultiServerMCPClient.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server connection.

    Attributes:
        name: Human-readable server identifier.
        url: Server base URL.
        api_key: Authentication key (sent as Bearer token).
        timeout: Request timeout in seconds.
        headers: Additional HTTP headers.
    """

    name: str
    url: str
    api_key: str = ""
    timeout: int = 30
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class MCPToolDef:
    """Tool definition from an MCP server.

    Attributes:
        name: Tool name.
        description: What the tool does.
        parameters: JSON Schema for tool input.
        server_name: Which server provides this tool.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    server_name: str


@dataclass
class MCPToolResult:
    """Result from calling an MCP tool.

    Attributes:
        tool_name: Tool that was called.
        server_name: Server that handled the call.
        content: Result content (text).
        is_error: Whether the call failed.
        elapsed_ms: Call duration.
    """

    tool_name: str
    server_name: str
    content: str
    is_error: bool = False
    elapsed_ms: int = 0


class MCPClient:
    """Multi-server MCP client for consuming external tools.

    Manages connections to multiple MCP servers and provides
    a unified interface for tool discovery and execution.
    """

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerConfig] = {}
        self._tools: dict[str, MCPToolDef] = {}
        self._call_count: int = 0

    def add_server(self, config: MCPServerConfig) -> None:
        """Register an MCP server.

        Args:
            config: Server configuration.
        """
        self._servers[config.name] = config
        logger.info("MCP server registered: %s (%s)", config.name, config.url)

    def remove_server(self, name: str) -> bool:
        """Remove an MCP server.

        Args:
            name: Server name to remove.

        Returns:
            True if removed.
        """
        removed = self._servers.pop(name, None) is not None
        if removed:
            self._tools = {
                k: v for k, v in self._tools.items()
                if v.server_name != name
            }
        return removed

    async def discover_tools(self) -> list[MCPToolDef]:
        """Discover tools from all registered servers.

        Calls each server's ``/tools/list`` endpoint and
        caches the results.

        Returns:
            Combined list of all available tools.
        """
        all_tools: list[MCPToolDef] = []
        for name, config in self._servers.items():
            tools = await self._fetch_tools(config)
            all_tools.extend(tools)
        self._tools = {t.name: t for t in all_tools}
        return all_tools

    async def list_tools(self) -> list[MCPToolDef]:
        """List all cached tools (call discover_tools first).

        Returns:
            List of MCPToolDef.
        """
        if not self._tools:
            return await self.discover_tools()
        return list(self._tools.values())

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> MCPToolResult:
        """Call a tool on its MCP server.

        Args:
            tool_name: Name of the tool to call.
            arguments: Tool input arguments.

        Returns:
            MCPToolResult with content or error.

        Raises:
            KeyError: If tool not found.
        """
        tool_def = self._tools.get(tool_name)
        if tool_def is None:
            raise KeyError(f"MCP tool '{tool_name}' not found")

        config = self._servers[tool_def.server_name]
        return await self._execute_tool(config, tool_name, arguments)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Get tool definitions in OpenAI function format.

        Returns:
            List of tool schema dicts ready for LLM.
        """
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in self._tools.values()
        ]

    @property
    def server_count(self) -> int:
        """Number of registered servers."""
        return len(self._servers)

    @property
    def tool_count(self) -> int:
        """Number of discovered tools."""
        return len(self._tools)

    @property
    def total_calls(self) -> int:
        """Total tool calls made."""
        return self._call_count

    # ── Internal ─────────────────────────────────────────────── #

    async def _fetch_tools(
        self,
        config: MCPServerConfig,
    ) -> list[MCPToolDef]:
        """Fetch tools from a single server.

        Args:
            config: Server config.

        Returns:
            List of tools from this server.
        """
        url = f"{config.url.rstrip('/')}/tools/list"
        headers = _build_headers(config)

        try:
            async with httpx.AsyncClient(
                timeout=config.timeout,
            ) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error(
                "Failed to fetch tools from %s: %s",
                config.name, exc,
            )
            return []

        return _parse_tool_list(data, config.name)

    async def _execute_tool(
        self,
        config: MCPServerConfig,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> MCPToolResult:
        """Execute a tool call on a server.

        Args:
            config: Server config.
            tool_name: Tool to call.
            arguments: Tool input.

        Returns:
            MCPToolResult.
        """
        url = f"{config.url.rstrip('/')}/tools/call"
        headers = _build_headers(config)
        payload = {"name": tool_name, "arguments": arguments}

        start = time.monotonic()
        self._call_count += 1

        try:
            async with httpx.AsyncClient(
                timeout=config.timeout,
            ) as client:
                resp = await client.post(
                    url, json=payload, headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            return MCPToolResult(
                tool_name=tool_name,
                server_name=config.name,
                content=f"Error: {exc}",
                is_error=True,
                elapsed_ms=elapsed,
            )

        elapsed = int((time.monotonic() - start) * 1000)
        return _parse_tool_result(data, tool_name, config.name, elapsed)


def _build_headers(config: MCPServerConfig) -> dict[str, str]:
    """Build HTTP headers for an MCP server request.

    Args:
        config: Server config with api_key and extra headers.

    Returns:
        Headers dict.
    """
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    headers.update(config.headers)
    return headers


def _parse_tool_list(
    data: Any,
    server_name: str,
) -> list[MCPToolDef]:
    """Parse tool list response from MCP server.

    Args:
        data: JSON response data.
        server_name: Server that provided the tools.

    Returns:
        List of MCPToolDef.
    """
    tools_data = data if isinstance(data, list) else data.get("tools", [])
    return [
        MCPToolDef(
            name=t.get("name", ""),
            description=t.get("description", ""),
            parameters=t.get("inputSchema", t.get("parameters", {})),
            server_name=server_name,
        )
        for t in tools_data
        if t.get("name")
    ]


def _parse_tool_result(
    data: Any,
    tool_name: str,
    server_name: str,
    elapsed_ms: int,
) -> MCPToolResult:
    """Parse tool call response from MCP server.

    Args:
        data: JSON response data.
        tool_name: Called tool name.
        server_name: Server that handled the call.
        elapsed_ms: Call duration.

    Returns:
        MCPToolResult.
    """
    content_list = data.get("content", [])
    text_parts = [
        c.get("text", "") for c in content_list
        if c.get("type") == "text"
    ]
    content = "\n".join(text_parts) if text_parts else str(data)
    is_error = data.get("isError", False)

    return MCPToolResult(
        tool_name=tool_name,
        server_name=server_name,
        content=content,
        is_error=is_error,
        elapsed_ms=elapsed_ms,
    )
