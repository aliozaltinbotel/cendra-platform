"""Tool decorator — create tools from typed Python functions.

Inspired by LangChain's ``@tool`` decorator. Automatically extracts
JSON Schema from type hints and docstrings. Supports both sync and
async functions.

Example::

    @tool(description="Search the knowledge base")
    async def search_kb(query: str, limit: int = 5) -> str:
        \"\"\"Search for relevant knowledge base entries.

        Args:
            query: Search query text.
            limit: Maximum results to return.
        \"\"\"
        return await kb.search(query, limit=limit)

    # search_kb.schema -> full JSON Schema dict
    # search_kb.tool_def -> ready-to-use tool definition dict
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Callable, get_type_hints

logger = logging.getLogger(__name__)

# Python type → JSON Schema type mapping
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Any:
    """Decorate a function as a callable tool with auto-schema.

    Can be used with or without arguments::

        @tool
        def my_tool(x: str) -> str: ...

        @tool(name="custom_name", description="Custom desc")
        def my_tool(x: str) -> str: ...

    Args:
        func: The function to decorate (when used without parens).
        name: Override tool name (defaults to function name).
        description: Override description (defaults to docstring).

    Returns:
        Decorated function with ``.schema`` and ``.tool_def`` attrs.
    """
    if func is not None:
        return _wrap_tool(func, name=name, description=description)
    return lambda f: _wrap_tool(f, name=name, description=description)


def _wrap_tool(
    func: Callable[..., Any],
    *,
    name: str | None,
    description: str | None,
) -> Callable[..., Any]:
    """Attach tool metadata to a function.

    Args:
        func: Original function.
        name: Tool name override.
        description: Description override.

    Returns:
        Wrapped function with schema attributes.
    """
    tool_name = name or func.__name__
    tool_desc = description or _extract_description(func)
    schema = _build_parameters_schema(func)

    @functools.wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        return await func(*args, **kwargs)

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    wrapper = async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    wrapper.tool_name = tool_name  # type: ignore[attr-defined]
    wrapper.tool_description = tool_desc  # type: ignore[attr-defined]
    wrapper.schema = schema  # type: ignore[attr-defined]
    wrapper.tool_def = _build_tool_def(tool_name, tool_desc, schema)  # type: ignore[attr-defined]
    wrapper.is_tool = True  # type: ignore[attr-defined]

    return wrapper


def _extract_description(func: Callable[..., Any]) -> str:
    """Extract description from function docstring.

    Takes the first paragraph (up to blank line) as the description.

    Args:
        func: Function with optional docstring.

    Returns:
        Description string.
    """
    doc = inspect.getdoc(func) or ""
    lines: list[str] = []
    for line in doc.split("\n"):
        stripped = line.strip()
        if not stripped and lines:
            break
        if stripped.startswith("Args:"):
            break
        if stripped:
            lines.append(stripped)
    return " ".join(lines) or f"Tool: {func.__name__}"


def _build_parameters_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Build JSON Schema for function parameters from type hints.

    Inspects the function signature and type annotations to produce
    a complete JSON Schema ``object`` with properties and required.

    Args:
        func: Annotated function.

    Returns:
        JSON Schema dict with ``type``, ``properties``, ``required``.
    """
    sig = inspect.signature(func)
    hints = _safe_get_hints(func)
    arg_docs = _parse_arg_docs(func)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        prop = _param_to_schema(param_name, param, hints, arg_docs)
        properties[param_name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _param_to_schema(
    name: str,
    param: inspect.Parameter,
    hints: dict[str, Any],
    arg_docs: dict[str, str],
) -> dict[str, Any]:
    """Convert a single parameter to JSON Schema property.

    Args:
        name: Parameter name.
        param: Parameter object.
        hints: Type hint dict.
        arg_docs: Parsed argument documentation.

    Returns:
        JSON Schema property dict.
    """
    prop: dict[str, Any] = {}
    hint = hints.get(name)

    if hint is not None:
        prop["type"] = _type_to_json_schema(hint)
    else:
        prop["type"] = "string"

    if name in arg_docs:
        prop["description"] = arg_docs[name]

    if param.default is not inspect.Parameter.empty:
        prop["default"] = param.default

    return prop


def _type_to_json_schema(hint: Any) -> str:
    """Convert a Python type hint to JSON Schema type string.

    Args:
        hint: Python type annotation.

    Returns:
        JSON Schema type string.
    """
    if hint in _TYPE_MAP:
        return _TYPE_MAP[hint]

    origin = getattr(hint, "__origin__", None)
    if origin is list:
        return "array"
    if origin is dict:
        return "object"

    return "string"


def _parse_arg_docs(func: Callable[..., Any]) -> dict[str, str]:
    """Parse Google-style docstring Args section.

    Args:
        func: Function with docstring.

    Returns:
        Dict of param_name -> description.
    """
    doc = inspect.getdoc(func) or ""
    args_section = _extract_args_section(doc)
    return _parse_args_lines(args_section)


def _extract_args_section(doc: str) -> list[str]:
    """Extract lines from the Args: section of a docstring.

    Args:
        doc: Full docstring text.

    Returns:
        Lines within the Args section.
    """
    lines = doc.split("\n")
    in_args = False
    section_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == "Args:":
            in_args = True
            continue
        if in_args:
            if stripped and not stripped.startswith(("Returns:", "Raises:", "Yields:", "Example")):
                section_lines.append(stripped)
            elif stripped.startswith(("Returns:", "Raises:", "Yields:")):
                break
    return section_lines


def _parse_args_lines(lines: list[str]) -> dict[str, str]:
    """Parse individual argument lines from Args section.

    Args:
        lines: Lines like ``query: Search text.``

    Returns:
        Dict of param_name -> description.
    """
    result: dict[str, str] = {}
    for line in lines:
        if ":" in line:
            key, _, desc = line.partition(":")
            key = key.strip()
            desc = desc.strip()
            if key and not key.startswith(" "):
                result[key] = desc
    return result


def _safe_get_hints(func: Callable[..., Any]) -> dict[str, Any]:
    """Get type hints safely, returning empty dict on failure.

    Args:
        func: Function to inspect.

    Returns:
        Type hint dict or empty dict.
    """
    try:
        return get_type_hints(func)
    except Exception:
        return {}


def _build_tool_def(
    name: str,
    description: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Build a complete tool definition dict.

    Args:
        name: Tool name.
        description: Tool description.
        schema: Parameters JSON Schema.

    Returns:
        Tool definition ready for LLM consumption.
    """
    return {
        "name": name,
        "description": description,
        "parameters": schema,
    }
