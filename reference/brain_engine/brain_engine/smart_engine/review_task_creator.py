"""Review Task Creator — generates tasks from guest reviews.

Analyzes guest reviews, extracts actionable issues, and creates
maintenance/follow-up tasks. Positive-only reviews get zero tasks.
"""

from __future__ import annotations

import json
import logging

import litellm
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_TEMPERATURE = 0.2

# Valid task categories for operational review triage
TASK_CATEGORIES = [
    "Technical Issues",
    "Emergency Situations",
    "Cleaning and Hygiene",
    "Reservation Issues",
    "Maintenance Needs",
    "Property Information",
    "Financial Matters",
    "Guest Services",
    "Guest Complaints",
    "Communication and Follow-Up",
]


class ReviewTask(BaseModel):
    """A task extracted from a guest review."""

    task_level: str = Field(default="Medium", description="Low/Medium/High/Urgent")
    description: str = ""
    main_category: str = ""
    sub_category: str = ""


class ReviewTaskRequest(BaseModel):
    """Input for review task creation."""

    customer_id: str
    property_id: str
    review_text: str
    review_rating: int = Field(default=0, ge=0, le=5)
    guest_name: str = ""
    reservation_id: str = ""


class ReviewTaskResponse(BaseModel):
    """Output of review task creation."""

    status: bool = True
    number_of_tasks: int = 0
    tasks: list[ReviewTask] = Field(default_factory=list)
    review_response: str = ""
    error: str | None = None


async def create_tasks_from_review(
    request: ReviewTaskRequest,
) -> ReviewTaskResponse:
    """Analyze a guest review and create actionable tasks.

    Focuses ONLY on issues and complaints. Positive reviews
    generate zero tasks. Also generates a brief review response.

    Args:
        request: Review analysis request.

    Returns:
        Created tasks and suggested review response.
    """
    prompt = (
        f"Guest review (rating {request.review_rating}/5):\n"
        f"{request.review_text}\n\n"
        f"Guest: {request.guest_name or 'Anonymous'}\n"
        f"Property: {request.property_id}"
    )

    try:
        response = await litellm.acompletion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=_TEMPERATURE,
            max_tokens=800,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        tasks = _parse_tasks(data.get("tasks", []))

        return ReviewTaskResponse(
            number_of_tasks=len(tasks),
            tasks=tasks,
            review_response=data.get("response", ""),
        )
    except Exception as exc:
        logger.error("Review task creation failed: %s", exc)
        return ReviewTaskResponse(status=False, error=str(exc))


def _parse_tasks(raw_tasks: list[dict]) -> list[ReviewTask]:
    """Parse and validate tasks from LLM output.

    Args:
        raw_tasks: Raw task dicts from LLM.

    Returns:
        List of validated ReviewTask objects.
    """
    tasks: list[ReviewTask] = []
    for item in raw_tasks[:5]:
        category = item.get("main_category", "")
        if category not in TASK_CATEGORIES:
            category = "Guest Complaints"

        level = item.get("task_level", "Medium")
        if level not in ("Low", "Medium", "High", "Urgent"):
            level = "Medium"

        tasks.append(ReviewTask(
            task_level=level,
            description=item.get("description", ""),
            main_category=category,
            sub_category=item.get("sub_category", ""),
        ))

    return tasks


_SYSTEM_PROMPT = """Analyze a guest review and extract actionable tasks.

Rules:
- Focus ONLY on issues, problems, and complaints
- If the review is purely positive, return 0 tasks
- Each task should be specific and actionable
- Include task priority (Low/Medium/High/Urgent)
- Also generate a brief response to the review (max 100 words)

Valid main categories:
- Technical Issues
- Emergency Situations
- Cleaning and Hygiene
- Reservation Issues
- Maintenance Needs
- Property Information
- Financial Matters
- Guest Services
- Guest Complaints
- Communication and Follow-Up

Return JSON:
{
    "number_of_tasks": 2,
    "tasks": [
        {"task_level": "High", "description": "Fix broken shower head in bathroom",
         "main_category": "Maintenance Needs", "sub_category": "Plumbing"}
    ],
    "response": "Thank you for your feedback..."
}

For purely positive reviews:
{"number_of_tasks": 0, "tasks": [], "response": "Thank you for your kind words..."}
"""
