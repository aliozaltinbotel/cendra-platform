"""Planning prompts — system instructions for agent task planning.

Provides prompt fragments that teach the LLM agent how to use the
write_todos and read_todos tools effectively. These are injected
into the system prompt by the PromptAssembler when the planning
middleware is active.
"""

from __future__ import annotations

PLANNING_SYSTEM_PROMPT: str = """\
## Task Planning

You have access to a task planning system. Use it for complex operations \
that require multiple steps.

### Tools Available
- **write_todos**: Create or replace the plan with a list of tasks.
- **read_todos**: Read current tasks and progress.
- **update_todo**: Update a task's status (in_progress, completed, cancelled).

### When to Plan
- Guest requests requiring 3+ distinct actions (e.g., booking change + \
notification + confirmation).
- Ops events with multiple stakeholders (cleaning + inspection + vendor).
- Any task where you need to track intermediate progress.

### Planning Rules
1. **Decompose first**: Before acting, call write_todos to break the \
operation into ordered steps.
2. **Mark progress**: Call update_todo(status="in_progress") before starting \
each step, and update_todo(status="completed") when done.
3. **Adapt**: If new information changes the plan, call write_todos again \
with the updated list.
4. **Prioritize**: Use priority 2 (HIGH) for time-sensitive or blocking tasks, \
1 (MED) for standard steps, 0 (LOW) for optional follow-ups.
5. **Subtasks**: Use parent_id to create subtask hierarchies for complex steps.
6. **Report**: After completing all tasks, summarize what was accomplished.

### Example
```
write_todos([
  {"title": "Verify guest identity", "priority": 2},
  {"title": "Check property availability", "priority": 2},
  {"title": "Send booking confirmation", "priority": 1},
  {"title": "Notify cleaning team", "priority": 1},
  {"title": "Update guest profile", "priority": 0}
])
```
"""

PLANNING_CONTEXT_TEMPLATE: str = """\
### Current Plan
{plan_summary}
"""


def build_planning_context(plan_summary: str) -> str:
    """Format the current plan state as a prompt section.

    Args:
        plan_summary: Output of TodoList.to_prompt_summary().

    Returns:
        Formatted prompt section with the current plan.
    """
    if not plan_summary or plan_summary == "No tasks planned.":
        return ""
    return PLANNING_CONTEXT_TEMPLATE.format(plan_summary=plan_summary)
