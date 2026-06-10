"""BrainAgent — main orchestrator wiring all Brain Engine modules.

The BrainAgent is the single entry point for running the AI agent.
It assembles the middleware stack, execution engine, skills, memory,
observability, and structured output into a unified interface.

Example::

    agent = BrainAgent.create(
        model="openai:gpt-4o-mini",
        skills_dir="./skills",
    )
    result = await agent.run("What time is checkout?", thread_id="t1")
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from brain_engine.execution.engine import ExecutionEngine
from brain_engine.execution.models import (
    AgentAction,
    AgentFinish,
    ExecutionConfig,
    ExecutionResult,
)
from brain_engine.execution.runtime import Runtime
from brain_engine.middleware.stack import MiddlewareStack
from brain_engine.observability.manager import CallbackManager
from brain_engine.observability.models import RunContext

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Configuration for the BrainAgent.

    Attributes:
        model: LLM model identifier (e.g. 'openai:gpt-4o-mini').
        temperature: LLM temperature.
        max_iterations: Max execution loop iterations.
        max_execution_time: Timeout in seconds.
        skills_dir: Path to skills directory.
        system_prompt: Base system prompt.
        return_intermediate_steps: Whether to include steps in result.
    """

    model: str = "openai:gpt-4o-mini"
    temperature: float = 0.3
    max_iterations: int = 15
    max_execution_time: int = 300
    skills_dir: str = ""
    system_prompt: str = "You are a helpful AI assistant."
    return_intermediate_steps: bool = True


class BrainAgent:
    """Main agent orchestrator — wires all modules together.

    Manages the lifecycle of a single agent instance: receives input,
    runs through the middleware + execution pipeline, and returns
    structured results.

    Args:
        config: Agent configuration.
        middleware_stack: Pre-configured middleware stack.
        callback_manager: Observability callback manager.
        tools: List of available tool definitions.
        checkpointer: Optional checkpoint backend.
    """

    def __init__(
        self,
        config: AgentConfig,
        middleware_stack: MiddlewareStack | None = None,
        callback_manager: CallbackManager | None = None,
        tools: list[dict[str, Any]] | None = None,
        checkpointer: Any = None,
    ) -> None:
        self._config = config
        self._stack = middleware_stack or MiddlewareStack()
        self._callbacks = callback_manager or CallbackManager()
        self._tools = tools or []
        self._checkpointer = checkpointer
        self._llm = _init_llm(config.model, config.temperature)

    @classmethod
    def create(
        cls,
        model: str = "openai:gpt-4o-mini",
        skills_dir: str = "",
        system_prompt: str = "You are a helpful AI assistant.",
        **kwargs: Any,
    ) -> BrainAgent:
        """Factory method for quick agent creation.

        Args:
            model: LLM model identifier.
            skills_dir: Path to SKILL.md files directory.
            system_prompt: Base system prompt.
            **kwargs: Additional AgentConfig fields.

        Returns:
            Configured BrainAgent instance.
        """
        config = AgentConfig(
            model=model,
            skills_dir=skills_dir,
            system_prompt=system_prompt,
            **kwargs,
        )
        stack = _build_default_stack(config)
        callbacks = CallbackManager(tags=["brain_agent"])
        return cls(
            config=config,
            middleware_stack=stack,
            callback_manager=callbacks,
        )

    # ── Public API ────────────────────────────────────────────────────

    async def run(
        self,
        input_text: str,
        thread_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Run the agent on an input.

        Args:
            input_text: User message or task.
            thread_id: Thread for checkpoint persistence.
            context: Additional context dict.

        Returns:
            ExecutionResult with output and metadata.
        """
        run_id = str(uuid.uuid4())
        thread_id = thread_id or run_id
        run_ctx = self._create_run_context(run_id)

        messages = self._build_messages(input_text, context)
        messages = await self._run_pre_pipeline(messages)

        result = await self._execute(messages, thread_id, run_ctx)
        result.run_id = run_id

        await self._run_post_pipeline(run_ctx, result)
        return result

    async def invoke(
        self,
        messages: list[dict[str, str]],
        thread_id: str | None = None,
    ) -> ExecutionResult:
        """Run the agent on pre-built messages.

        Args:
            messages: Pre-built message list.
            thread_id: Thread for checkpointing.

        Returns:
            ExecutionResult.
        """
        run_id = str(uuid.uuid4())
        thread_id = thread_id or run_id
        run_ctx = self._create_run_context(run_id)

        processed = await self._run_pre_pipeline(messages)
        result = await self._execute(processed, thread_id, run_ctx)
        result.run_id = run_id

        await self._run_post_pipeline(run_ctx, result)
        return result

    # ── Middleware & Pipeline ─────────────────────────────────────────

    def add_middleware(self, middleware: Any) -> None:
        """Add a middleware to the stack.

        Args:
            middleware: Middleware implementing MiddlewareProtocol.
        """
        self._stack.add(middleware)

    def add_tool(self, tool: dict[str, Any]) -> None:
        """Register a tool for the agent.

        Args:
            tool: Tool definition dict with name, description, handler.
        """
        self._tools.append(tool)

    @property
    def middleware_names(self) -> list[str]:
        """Names of all registered middleware."""
        return self._stack.names

    @property
    def tool_count(self) -> int:
        """Number of registered tools."""
        return len(self._tools) + len(self._stack.collect_tools())

    # ── Internal ──────────────────────────────────────────────────────

    def _create_run_context(self, run_id: str) -> RunContext:
        """Create a root RunContext for this execution.

        Args:
            run_id: Unique run identifier.

        Returns:
            Root RunContext.
        """
        return self._callbacks.create_run_context(
            name="brain_agent", run_type="agent",
        )

    def _build_messages(
        self,
        input_text: str,
        context: dict[str, Any] | None,
    ) -> list[dict[str, str]]:
        """Build the initial message list from input.

        Args:
            input_text: User message.
            context: Optional context dict.

        Returns:
            Message list with system + user messages.
        """
        system = self._config.system_prompt
        if context:
            ctx_str = _format_context(context)
            system = f"{system}\n\n{ctx_str}"

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": input_text},
        ]

    async def _run_pre_pipeline(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Run pre-model middleware hooks.

        Args:
            messages: Input messages.

        Returns:
            Processed messages.
        """
        prompt_additions = self._stack.build_prompt_additions()
        if prompt_additions:
            messages = _inject_additions(messages, prompt_additions)
        return await self._stack.run_pre_model(messages)

    async def _execute(
        self,
        messages: list[dict[str, Any]],
        thread_id: str,
        run_ctx: RunContext,
    ) -> ExecutionResult:
        """Execute the agent loop via ExecutionEngine.

        Args:
            messages: Processed messages.
            thread_id: Thread identifier.
            run_ctx: Run context for observability.

        Returns:
            ExecutionResult.
        """
        planner = _LLMPlanner(self._llm, self._stack)
        tool_executor = _ToolExecutor(self._tools, self._stack)
        exec_config = ExecutionConfig(
            max_iterations=self._config.max_iterations,
            max_execution_time_seconds=self._config.max_execution_time,
            return_intermediate_steps=self._config.return_intermediate_steps,
        )
        runtime = Runtime()
        engine = ExecutionEngine(
            planner=planner,
            tool_executor=tool_executor,
            config=exec_config,
            runtime=runtime,
        )
        return await engine.run({"messages": messages})

    async def _run_post_pipeline(
        self,
        run_ctx: RunContext,
        result: ExecutionResult,
    ) -> None:
        """Run post-execution hooks and checkpointing.

        Args:
            run_ctx: Run context.
            result: Execution result.
        """
        if result.output:
            await self._stack.run_post_model(result.output)

        await self._callbacks.on_agent_finish(
            run_ctx, result.output,
        )

        if self._checkpointer and result.succeeded:
            await self._save_checkpoint(result)

    async def _save_checkpoint(self, result: ExecutionResult) -> None:
        """Save execution state to checkpointer.

        Args:
            result: Completed execution result.
        """
        try:
            config = {"thread_id": result.run_id}
            await self._checkpointer.put(
                config,
                {"output": result.output, "status": result.status},
                {"step": result.iterations},
            )
        except Exception:
            logger.warning("Failed to save checkpoint", exc_info=True)


# ══════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════


class _LLMPlanner:
    """Adapts LLM to the Planner protocol for ExecutionEngine.

    Args:
        llm: LLM instance with invoke() method.
        stack: Middleware stack for pre/post hooks.
    """

    def __init__(self, llm: Any, stack: MiddlewareStack) -> None:
        self._llm = llm
        self._stack = stack

    async def plan(
        self,
        intermediate_steps: list[tuple[dict[str, Any], str]],
        **kwargs: Any,
    ) -> AgentAction | AgentFinish:
        """Plan the next action based on conversation history.

        Args:
            intermediate_steps: Previous (action, observation) pairs.
            **kwargs: Must include 'messages'.

        Returns:
            AgentAction or AgentFinish.
        """
        messages = kwargs.get("messages", [])
        messages = _append_steps(messages, intermediate_steps)

        response = await self._call_llm(messages)
        return _parse_llm_response(response)

    async def _call_llm(
        self,
        messages: list[dict[str, Any]],
    ) -> Any:
        """Call the LLM through middleware.

        Args:
            messages: Input messages.

        Returns:
            LLM response.
        """
        processed = await self._stack.run_pre_model(messages)

        if hasattr(self._llm, "invoke"):
            response = await self._llm.invoke(processed)
        elif hasattr(self._llm, "acompletion"):
            response = await self._llm.acompletion(processed)
        else:
            import litellm
            response = await litellm.acompletion(
                model=getattr(self._llm, "model", "gpt-4o-mini"),
                messages=processed,
            )

        return await self._stack.run_post_model(response)


class _ToolExecutor:
    """Adapts tools to the ToolExecutor protocol for ExecutionEngine.

    Args:
        tools: List of tool definitions.
        stack: Middleware stack for tool wrapping.
    """

    def __init__(
        self, tools: list[dict[str, Any]], stack: MiddlewareStack,
    ) -> None:
        self._tools = {t.get("name", ""): t for t in tools}
        mw_tools = stack.collect_tools()
        for t in mw_tools:
            self._tools[t.name] = {"name": t.name, "handler": t.handler}

    async def execute(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> str:
        """Execute a tool by name.

        Args:
            tool_name: Tool to execute.
            tool_input: Tool arguments.

        Returns:
            Tool output as string.
        """
        tool = self._tools.get(tool_name)
        if not tool:
            return f"Error: unknown tool '{tool_name}'"

        handler = tool.get("handler")
        if handler is None:
            return f"Error: tool '{tool_name}' has no handler"

        try:
            result = await handler(**tool_input)
            return str(result)
        except Exception as exc:
            return f"Error executing {tool_name}: {exc}"


def _init_llm(model: str, temperature: float) -> Any:
    """Initialize the LLM model.

    Args:
        model: Model identifier.
        temperature: LLM temperature.

    Returns:
        LLM instance or config dict.
    """
    try:
        from brain_engine.models import init_chat_model
        return init_chat_model(model, temperature=temperature)
    except ImportError:
        return {"model": model, "temperature": temperature}


def _build_default_stack(config: AgentConfig) -> MiddlewareStack:
    """Build the default middleware stack.

    Args:
        config: Agent configuration.

    Returns:
        Configured MiddlewareStack.
    """
    from brain_engine.middleware.builtin.logging_mw import LoggingMiddleware

    stack = MiddlewareStack()
    stack.add(LoggingMiddleware())

    if config.skills_dir:
        _add_skill_middleware(stack, config.skills_dir)

    return stack


def _add_skill_middleware(stack: MiddlewareStack, skills_dir: str) -> None:
    """Load skills and add SkillMiddleware to the stack.

    Args:
        stack: Middleware stack to add to.
        skills_dir: Path to skills directory.
    """
    try:
        from brain_engine.skills import SkillLoader, SkillRegistry
        from brain_engine.middleware.builtin.skill_mw import SkillMiddleware

        loader = SkillLoader(skills_dir)
        registry = SkillRegistry()
        registry.register_many(loader.load_all())
        stack.add(SkillMiddleware(registry=registry))
    except Exception:
        logger.warning("Failed to load skills from %s", skills_dir)


def _format_context(context: dict[str, Any]) -> str:
    """Format a context dict as a text section.

    Args:
        context: Context key-value pairs.

    Returns:
        Formatted context string.
    """
    lines = [f"- {k}: {v}" for k, v in context.items()]
    return "## Context\n" + "\n".join(lines)


def _inject_additions(
    messages: list[dict[str, Any]],
    additions: str,
) -> list[dict[str, Any]]:
    """Inject middleware prompt additions into system message.

    Args:
        messages: Original messages.
        additions: Additional prompt text.

    Returns:
        Modified messages.
    """
    result = list(messages)
    for i, msg in enumerate(result):
        if msg.get("role") == "system":
            result[i] = {
                **msg,
                "content": str(msg.get("content", "")) + f"\n\n{additions}",
            }
            return result
    return result


def _append_steps(
    messages: list[dict[str, Any]],
    steps: list[tuple[dict[str, Any], str]],
) -> list[dict[str, Any]]:
    """Append intermediate steps as assistant/tool messages.

    Args:
        messages: Base messages.
        steps: (action_dict, observation) pairs.

    Returns:
        Messages with step history appended.
    """
    result = list(messages)
    for action, observation in steps:
        tool_name = action.get("tool", "unknown")
        result.append({
            "role": "assistant",
            "content": f"[Called {tool_name}]",
        })
        result.append({
            "role": "tool",
            "content": observation,
            "name": tool_name,
        })
    return result


def _parse_llm_response(response: Any) -> AgentAction | AgentFinish:
    """Parse LLM response into AgentAction or AgentFinish.

    Args:
        response: LLM response (AIMessage, dict, or str).

    Returns:
        AgentAction if tool call detected, else AgentFinish.
    """
    tool_calls = _extract_tool_calls(response)
    if tool_calls:
        call = tool_calls[0]
        return AgentAction(
            tool=call.get("name", ""),
            tool_input=call.get("args", {}),
            log=_extract_text(response),
        )

    text = _extract_text(response)
    return AgentFinish(
        return_values={"output": text},
        log=text,
    )


def _extract_tool_calls(response: Any) -> list[dict[str, Any]]:
    """Extract tool calls from an LLM response.

    Args:
        response: LLM response.

    Returns:
        List of tool call dicts with 'name' and 'args'.
    """
    direct = _extract_direct_tool_calls(response)
    if direct:
        return direct
    return _extract_choices_tool_calls(response)


def _extract_direct_tool_calls(response: Any) -> list[dict[str, Any]]:
    """Extract tool calls from response.tool_calls attribute.

    Args:
        response: LLM response.

    Returns:
        Tool call dicts or empty list.
    """
    if not hasattr(response, "tool_calls") or not response.tool_calls:
        return []
    return [
        {"name": tc.name, "args": tc.args}
        for tc in response.tool_calls
        if hasattr(tc, "name")
    ]


def _extract_choices_tool_calls(response: Any) -> list[dict[str, Any]]:
    """Extract tool calls from response.choices[].message.tool_calls.

    Args:
        response: LLM response with choices.

    Returns:
        Tool call dicts or empty list.
    """
    if not hasattr(response, "choices"):
        return []
    for choice in response.choices:
        msg = getattr(choice, "message", None)
        if msg and hasattr(msg, "tool_calls") and msg.tool_calls:
            return [
                {
                    "name": tc.function.name,
                    "args": _safe_parse_args(tc.function.arguments),
                }
                for tc in msg.tool_calls
            ]
    return []


def _safe_parse_args(args: Any) -> dict[str, Any]:
    """Safely parse tool call arguments.

    Args:
        args: JSON string or dict.

    Returns:
        Parsed dict.
    """
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        import json
        try:
            return json.loads(args)
        except (json.JSONDecodeError, ValueError):
            return {"raw": args}
    return {}


def _extract_text(response: Any) -> str:
    """Extract text content from an LLM response.

    Args:
        response: Model response.

    Returns:
        Text content string.
    """
    if isinstance(response, str):
        return response
    if hasattr(response, "content"):
        return str(response.content or "")
    if hasattr(response, "choices"):
        choices = response.choices
        if choices:
            return str(choices[0].message.content or "")
    return str(response)
