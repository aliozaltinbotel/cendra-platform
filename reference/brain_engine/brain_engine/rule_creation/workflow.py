"""Rule Creation Workflow — Brain Engine-delegated implementation.

Replaces the original seven-file specialist-agent layout with a
single self-contained workflow module that:

* Keeps every public API the UI depends on — :class:`RuleCreationRequest`,
  :class:`RuleCreationResponse`, :class:`WorkflowPhase`,
  :class:`RuleBundle` and the three functions :func:`start_workflow`,
  :func:`send_message`, :func:`get_workflow_status` — so the
  ``/rule-creation/*`` HTTP endpoints continue to behave identically
  from the caller's point of view.
* Routes the intent-discovery prompt through the Foundation Layer
  catalog (FL-01) so the rule classifier has access to the curated
  hospitality sector knowledge instead of re-deriving it from
  scratch.  This is the first real integration with the rest of
  Brain Engine; the other phases still call the LLM directly
  (``litellm`` — which is Brain Engine's standard LLM gateway and
  auto-routes between OpenAI and Azure per the deployment config).
* Owns the in-memory workflow store
  (``_active_workflows: dict[str, ConversationState]``) until a
  Postgres-backed store lands as a follow-up — this matches the
  pre-existing behaviour and avoids changing pod-restart semantics
  in this commit.

The seven specialist agents that previously lived in
``rule_creation/agents/`` (``greeting``, ``discovery``, ``label``,
``tag``, ``ai_rule``, ``escalation``, ``confirmation``) survive as
private phase handlers inside this module.  Their LLM prompts are
preserved verbatim so the observed behaviour from the UI does not
change.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

import litellm

from brain_engine.analysis.iterative_questioning import (
    build_clarifying_questions,
    render_question_prompt,
)
from brain_engine.patterns.foundation_customer_catalog import (
    FoundationCustomerCatalogStore,
    FoundationCustomerScenario,
)
from brain_engine.patterns.foundation_registry import (
    FoundationScenario,
    load_foundation_scenarios,
)
from brain_engine.rule_creation.models import (
    LABEL_FIELDS,
    AgentMessage,
    AIRuleComponent,
    ConversationState,
    EscalationComponent,
    LabelComponent,
    LabelCondition,
    LabelOperator,
    RuleBundle,
    RuleCreationRequest,
    RuleCreationResponse,
    RuleType,
    TagComponent,
    WorkflowPhase,
)

logger = logging.getLogger(__name__)


__all__ = [
    "get_workflow_status",
    "send_message",
    "set_customer_foundation_store",
    "start_workflow",
]


# ── module constants ──────────────────────────────────────── #


_PRIMARY_MODEL = "gpt-4o"
_LIGHT_MODEL = "gpt-4o-mini"

# Greeting tolerates a touch more variety; everything else needs
# tight, structured extraction so we keep temperature low.
_TEMP_CREATIVE = 0.3
_TEMP_STRICT = 0.2

# Foundation Layer integration — the discovery phase consults the
# parsed hospitality catalog (FL-01 `foundation_scenarios_reactive`
# table) to ground the rule classifier in sector knowledge.  The
# catalog is loaded lazily from the shipped markdown the first time
# discovery runs; the result is cached at module level so subsequent
# calls do not re-parse 469 scenarios.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FOUNDATION_DOC = (
    _REPO_ROOT
    / "Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_Foundation.md"
)
_FOUNDATION_CACHE: tuple[FoundationScenario, ...] | None = None
# Limit how many scenario titles are surfaced in the discovery prompt
# so the LLM context stays small.  Twelve is enough to anchor the
# classifier without overwhelming gpt-4o's input window.
_FOUNDATION_HINT_LIMIT = 12


# In-memory workflow store.  Production deployments should migrate
# to Postgres-backed state (mirroring ``onboarding/job_store.py``)
# so wip workflows survive pod restarts — that lift is intentionally
# deferred to a follow-up so this commit stays a behaviour-preserving
# refactor.
_active_workflows: dict[str, ConversationState] = {}


# Sprint 6 W9 — when wired via :func:`set_customer_foundation_store`,
# every workflow that reaches :attr:`WorkflowPhase.FINALIZED` is
# copied into the FL-14 ``foundation_scenarios_customer`` catalog so
# the orchestrator's matcher (FL-14b) can blend the PM-authored
# rule into the second-tier foundation lookup.  Module-level
# injection keeps the public ``send_message`` /
# ``start_workflow`` API unchanged — the app factory calls
# :func:`set_customer_foundation_store` at lifespan once.  ``None``
# (the default) keeps the pre-W9 behaviour bit-for-bit.
_customer_foundation_store: FoundationCustomerCatalogStore | None = None


def set_customer_foundation_store(
    store: FoundationCustomerCatalogStore | None,
) -> None:
    """Inject the FL-14 customer-foundation store (Sprint 6 W9).

    Call once at app lifespan with the wired
    :class:`FoundationCustomerCatalogStore` instance; subsequent
    workflows that reach the FINALIZED phase will have their bundle
    upserted into the store as a customer-scoped scenario.  Pass
    ``None`` to disable — useful for tests that want to assert the
    pre-W9 behaviour.
    """
    global _customer_foundation_store
    _customer_foundation_store = store


# ── public entry points ───────────────────────────────────── #


async def start_workflow(
    request: RuleCreationRequest,
) -> RuleCreationResponse:
    """Open a fresh workflow, run the greeting handler, and return.

    Args:
        request: Initial request with ``customer_id`` and optional
            opening message.

    Returns:
        Response containing the generated ``workflow_id``, the
        greeting agent's message, and the next conversation phase
        (always :attr:`WorkflowPhase.INTENT_DISCOVERY` on success).
    """
    workflow_id = f"rc-{uuid.uuid4().hex[:12]}"
    state = ConversationState(
        workflow_id=workflow_id,
        customer_id=request.customer_id,
        phase=WorkflowPhase.GREETING,
    )
    _active_workflows[workflow_id] = state

    result = await _run_greeting(state, request.message or "Hello")
    _apply_agent_result(state, result)

    return RuleCreationResponse(
        workflow_id=workflow_id,
        agent_message=result.message,
        phase=state.phase.value,
    )


async def send_message(
    request: RuleCreationRequest,
) -> RuleCreationResponse:
    """Advance an existing workflow with a new PM message.

    Routes the message to the phase handler matching the workflow's
    current :class:`WorkflowPhase`.  Returns an error response when
    the workflow id is unknown so the caller can distinguish a stale
    UI session from a real failure.

    Args:
        request: Message addressed to a specific ``workflow_id``.

    Returns:
        Updated phase plus the assistant message produced by the
        handler.  When the workflow finalises, ``rule_bundle`` is
        populated and ``is_complete`` is ``True``.
    """
    state = _active_workflows.get(request.workflow_id)
    if state is None:
        return RuleCreationResponse(
            status=False,
            error=f"Workflow {request.workflow_id} not found",
        )

    if state.phase in {
        WorkflowPhase.FINALIZED,
        WorkflowPhase.CANCELLED,
    }:
        return RuleCreationResponse(
            workflow_id=state.workflow_id,
            agent_message="This workflow is already completed.",
            phase=state.phase.value,
            is_complete=True,
            rule_bundle=(
                state.partial_bundle
                if state.phase == WorkflowPhase.FINALIZED
                else None
            ),
        )

    result = await _route_to_handler(state, request.message)
    _apply_agent_result(state, result)
    state.turn_count += 1

    is_complete = state.phase in {
        WorkflowPhase.FINALIZED,
        WorkflowPhase.CANCELLED,
    }
    # Sprint 6 W9 — when the workflow finalises, copy the bundle
    # into the FL-14 customer foundation catalog so the
    # orchestrator (FL-14b) can blend the PM-authored rule into
    # the second-tier foundation lookup.  Cancellation does NOT
    # trigger the copy — only confirmed rules survive into the
    # customer foundation.
    if state.phase == WorkflowPhase.FINALIZED:
        await _persist_finalized_rule_to_customer_foundation(state)
    return RuleCreationResponse(
        workflow_id=state.workflow_id,
        agent_message=result.message,
        phase=state.phase.value,
        is_complete=is_complete,
        rule_bundle=state.partial_bundle if is_complete else None,
    )


def get_workflow_status(workflow_id: str) -> RuleCreationResponse:
    """Return the current phase and partial bundle for a workflow.

    Args:
        workflow_id: Identifier returned by :func:`start_workflow`.

    Returns:
        Snapshot of the phase + partial bundle, or an error
        response when the workflow id is unknown.
    """
    state = _active_workflows.get(workflow_id)
    if state is None:
        return RuleCreationResponse(
            status=False,
            error=f"Workflow {workflow_id} not found",
        )

    return RuleCreationResponse(
        workflow_id=workflow_id,
        phase=state.phase.value,
        is_complete=state.phase in {
            WorkflowPhase.FINALIZED,
            WorkflowPhase.CANCELLED,
        },
        rule_bundle=state.partial_bundle,
    )


# ── phase routing ─────────────────────────────────────────── #


async def _route_to_handler(
    state: ConversationState,
    message: str,
) -> AgentMessage:
    """Dispatch ``message`` to the handler for ``state.phase``."""
    phase = state.phase

    if phase == WorkflowPhase.GREETING:
        return await _run_greeting(state, message)

    if phase == WorkflowPhase.INTENT_DISCOVERY:
        return await _run_discovery(state, message)

    if phase == WorkflowPhase.DETAIL_COLLECTION:
        return await _route_detail_collection(state, message)

    if phase == WorkflowPhase.CONFIRMATION:
        return await _run_confirmation(state, message)

    return AgentMessage(
        agent_name="system",
        message="Unexpected state. Please start a new workflow.",
        phase=phase,
    )


async def _route_detail_collection(
    state: ConversationState,
    message: str,
) -> AgentMessage:
    """Sub-route the DETAIL_COLLECTION phase by ``state.rule_type``."""
    rule_type = state.rule_type

    if rule_type == RuleType.LABEL:
        return await _run_label(state, message)

    if rule_type == RuleType.TAG:
        result = await _run_tag(state, message)
        # The original behaviour: as soon as the tag is complete,
        # immediately gather escalation config in the same turn so
        # the UI does not need to round-trip an empty phase.
        if result.next_phase == WorkflowPhase.CONFIRMATION:
            return await _run_escalation(state, message)
        return result

    if rule_type == RuleType.AI_RULE:
        return await _run_ai_rule(state, message)

    if rule_type in {
        RuleType.LABEL_THEN_AI_RULE,
        RuleType.LABEL_TAG_AI_RULE,
    }:
        if not state.partial_bundle.label_component:
            return await _run_label(state, message)
        if (
            rule_type == RuleType.LABEL_TAG_AI_RULE
            and not state.partial_bundle.tag_component
        ):
            return await _run_tag(state, message)
        return await _run_ai_rule(state, message)

    if rule_type == RuleType.TAG_THEN_AI_RULE:
        if not state.partial_bundle.tag_component:
            return await _run_tag(state, message)
        return await _run_ai_rule(state, message)

    return await _run_ai_rule(state, message)


# ── state application ─────────────────────────────────────── #


def _apply_agent_result(
    state: ConversationState,
    result: AgentMessage,
) -> None:
    """Fold an :class:`AgentMessage` back into the conversation state.

    Updates the phase, language, rule type, component flags, and
    overlays any extracted components onto ``state.partial_bundle``.
    Mutations are guarded so a malformed agent payload (e.g. an
    unknown ``rule_type`` string) cannot corrupt the state — the
    field is simply ignored.
    """
    data = result.extracted_data

    if result.next_phase is not None:
        state.phase = result.next_phase

    if "detected_language" in data:
        state.detected_language = data["detected_language"]

    if "rule_type" in data:
        try:
            state.rule_type = RuleType(data["rule_type"])
        except ValueError:
            logger.warning(
                "rule_creation.unknown_rule_type value=%s",
                data["rule_type"],
            )

    if "is_composite" in data:
        state.is_composite = bool(data["is_composite"])

    if "components" in data:
        state.components = list(data["components"])

    if "confidence" in data:
        try:
            state.confidence = float(data["confidence"])
        except (TypeError, ValueError):
            state.confidence = 0.0

    if "label_component" in data:
        state.partial_bundle.label_component = LabelComponent(
            **data["label_component"],
        )

    if "tag_component" in data:
        state.partial_bundle.tag_component = TagComponent(
            **data["tag_component"],
        )

    if "ai_rule_component" in data:
        state.partial_bundle.ai_rule_component = AIRuleComponent(
            **data["ai_rule_component"],
        )

    if "escalation_component" in data:
        state.partial_bundle.escalation_component = EscalationComponent(
            **data["escalation_component"],
        )

    if state.rule_type is not None:
        state.partial_bundle.rule_type = state.rule_type

    state.context_summary = (
        f"Phase: {state.phase.value}, "
        f"Rule type: {state.rule_type}, "
        f"Turn: {state.turn_count}"
    )


# ── customer-foundation persistence (Sprint 6 W9) ─────────── #


async def _persist_finalized_rule_to_customer_foundation(
    state: ConversationState,
) -> None:
    """Copy a finalised rule into the FL-14 customer foundation store.

    The function is a *best-effort* hook — when the store is not
    wired (``None``) the call is a no-op; when the upsert fails the
    error is logged but the workflow's response is never affected.
    This keeps rule-creation latency stable even when the
    second-tier foundation infrastructure is degraded.
    """
    store = _customer_foundation_store
    if store is None:
        return
    if not state.customer_id:
        logger.warning(
            "rule_creation.customer_foundation.missing_customer_id "
            "workflow_id=%s",
            state.workflow_id,
        )
        return
    try:
        scenario = _bundle_to_customer_scenario(state)
    except ValueError as exc:
        logger.warning(
            "rule_creation.customer_foundation.invalid_bundle "
            "workflow_id=%s error=%s",
            state.workflow_id,
            exc,
        )
        return
    try:
        await store.upsert(scenario)
        logger.info(
            "rule_creation.customer_foundation.persisted "
            "customer_id=%s scenario_id=%s",
            scenario.customer_id,
            scenario.scenario_id,
        )
    except Exception as exc:
        logger.error(
            "rule_creation.customer_foundation.upsert_failed "
            "customer_id=%s scenario_id=%s error=%s",
            scenario.customer_id,
            scenario.scenario_id,
            exc,
        )


def _bundle_to_customer_scenario(
    state: ConversationState,
) -> FoundationCustomerScenario:
    """Project a finalised :class:`ConversationState` into FL-14 row.

    Picks the most descriptive title available from the partial
    bundle (AI rule name > tag name > label name > workflow id) and
    builds a free-form trigger from the agent's collected
    description fields so the FL-15 matcher has enough text to
    embed.  Other foundation fields stay at their conservative
    FL-14 defaults — a customer-authored rule never claims
    ``Critical`` risk or learning authority over the core
    foundation.

    Raises:
        ValueError: When the bundle has no usable title.  The
            caller logs + skips in that case.
    """
    bundle = state.partial_bundle
    title = _bundle_title(bundle, state.workflow_id)
    trigger_parts = _bundle_trigger_parts(bundle)
    return FoundationCustomerScenario(
        customer_id=state.customer_id,
        scenario_id=f"c_{state.customer_id}_{state.workflow_id}",
        title=title,
        trigger="\n".join(part for part in trigger_parts if part).strip(),
        ai_default_behavior=(
            bundle.ai_rule_component.expected_behavior
            if bundle.ai_rule_component is not None
            else ""
        ),
        source_rule_id=state.workflow_id,
    )


def _bundle_title(bundle: RuleBundle, fallback: str) -> str:
    """Pick the most descriptive title from the bundle's components."""
    if bundle.ai_rule_component is not None and bundle.ai_rule_component.name:
        return bundle.ai_rule_component.name
    if bundle.tag_component is not None and bundle.tag_component.name:
        return bundle.tag_component.name
    if bundle.label_component is not None and bundle.label_component.name:
        return bundle.label_component.name
    if bundle.bundle_name:
        return bundle.bundle_name
    if not fallback:
        raise ValueError(
            "bundle has no usable title and no fallback id",
        )
    return f"Rule {fallback}"


def _bundle_trigger_parts(bundle: RuleBundle) -> list[str]:
    """Render the bundle into trigger text the matcher can embed."""
    parts: list[str] = []
    if bundle.ai_rule_component is not None:
        if bundle.ai_rule_component.description:
            parts.append(bundle.ai_rule_component.description)
        if bundle.ai_rule_component.expected_behavior:
            parts.append(bundle.ai_rule_component.expected_behavior)
    if bundle.tag_component is not None:
        if bundle.tag_component.description:
            parts.append(bundle.tag_component.description)
        if bundle.tag_component.keywords:
            parts.append(
                "Keywords: " + ", ".join(bundle.tag_component.keywords),
            )
    if bundle.label_component is not None and bundle.label_component.conditions:
        parts.append(
            "Conditions: "
            + "; ".join(
                f"{cond.field} {cond.operator.value} {cond.value}"
                for cond in bundle.label_component.conditions
            ),
        )
    return parts


# ── foundation catalog access ─────────────────────────────── #


def _load_foundation_cached() -> tuple[FoundationScenario, ...]:
    """Lazy-load the parsed Hospitality foundation catalog.

    The catalog is loaded once per process and cached at module
    level — 469 scenarios are ~150 KB in memory.  When the shipped
    markdown is absent (e.g. minimal CI checkout) the loader logs a
    warning and returns an empty tuple; discovery falls back to its
    plain LLM prompt.
    """
    global _FOUNDATION_CACHE
    if _FOUNDATION_CACHE is None:
        _FOUNDATION_CACHE = load_foundation_scenarios(_FOUNDATION_DOC)
    return _FOUNDATION_CACHE


def _foundation_hint_scenarios() -> list[FoundationScenario]:
    """Return the stage-coverage representative scenarios.

    Picks the first scenario seen per stage so the resulting list
    surfaces one example per booking-journey phase.  Cached
    foundation rows preserve their original order — the
    deterministic selection lets every downstream prompt builder
    (``_foundation_hint_lines``, ``_clarifying_question_block``)
    share the same scenario set without re-running the catalog
    scan.
    """
    scenarios = _load_foundation_cached()
    if not scenarios:
        return []
    per_stage: dict[int, FoundationScenario] = {}
    for scenario in scenarios:
        per_stage.setdefault(scenario.stage_number, scenario)
    picked: list[FoundationScenario] = []
    for stage_number in sorted(per_stage.keys()):
        picked.append(per_stage[stage_number])
        if len(picked) >= _FOUNDATION_HINT_LIMIT:
            break
    return picked


def _foundation_hint_lines() -> list[str]:
    """Return up to ``_FOUNDATION_HINT_LIMIT`` scenario titles as hints.

    Selected for *coverage* rather than relevance — one title per
    stage where possible — so the LLM sees a representative slice of
    the sector taxonomy without ballooning the prompt.  The selection
    is deterministic so the same prompt produces the same output
    across runs.
    """
    return [
        f"- Stage {scenario.stage_number} "
        f"({scenario.stage_label}): {scenario.title}"
        for scenario in _foundation_hint_scenarios()
    ]


def _clarifying_question_block(pm_description: str) -> str:
    """Build the FL-15 clarifying-question prompt block (Sprint 6 W10).

    Walks the stage-coverage scenarios through
    :func:`build_clarifying_questions` to surface ``Required Data
    Checks`` the PM has not yet mentioned in ``pm_description``.
    Returns an LLM-friendly multi-line prompt block, or an empty
    string when:

    * the foundation catalog is missing on disk (same fall-through
      as :func:`_foundation_hint_lines`),
    * the PM description already covers every check the dominant
      stage-coverage scenarios surface, or
    * the catalog rows have no ``required_data_checks`` populated
      (legacy parser output pre FL-01).

    Caller (``_run_discovery``) concatenates the result into the
    system prompt so the LLM classifier can paraphrase the missed
    checks back at the PM in iterative-questioning style.
    """
    scenarios = _foundation_hint_scenarios()
    if not scenarios:
        return ""
    questions = build_clarifying_questions(pm_description, scenarios)
    return render_question_prompt(questions)


# ── phase handlers ────────────────────────────────────────── #


async def _run_greeting(
    state: ConversationState,
    pm_message: str,
) -> AgentMessage:
    """Greet the PM and detect language; advance to discovery."""
    user_prompt = (
        f"PM says: {pm_message}\n\n"
        "This is the start of a rule creation conversation. "
        "Greet the PM and ask what kind of rule they'd like to create."
    )
    try:
        response = await litellm.acompletion(
            model=_PRIMARY_MODEL,
            messages=[
                {"role": "system", "content": _GREETING_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=_TEMP_CREATIVE,
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        data = _parse_json(response)
        return AgentMessage(
            agent_name="greeting",
            message=data.get("agent_message", ""),
            phase=WorkflowPhase.GREETING,
            next_phase=WorkflowPhase.INTENT_DISCOVERY,
            extracted_data={
                "detected_language": data.get("detected_language", "en"),
            },
            needs_user_input=True,
        )
    except Exception as exc:
        logger.error("rule_creation.greeting_failed: %s", exc)
        return AgentMessage(
            agent_name="greeting",
            message="Welcome! What kind of rule would you like to create?",
            phase=WorkflowPhase.GREETING,
            next_phase=WorkflowPhase.INTENT_DISCOVERY,
        )


async def _run_discovery(
    state: ConversationState,
    pm_message: str,
) -> AgentMessage:
    """Classify rule type with Foundation Layer sector context.

    The Foundation catalog (FL-01) is consulted to surface a small
    representative slice of the hospitality taxonomy.  Those titles
    are inlined into the system prompt so the rule-type classifier
    has the curated sector knowledge to anchor its decision.  When
    the catalog cannot be loaded (markdown missing) the handler
    falls back to the plain prompt and logs the gap.
    """
    foundation_lines = _foundation_hint_lines()
    if foundation_lines:
        sector_block = (
            "Hospitality sector context (Brain Engine foundation, "
            "representative scenarios — use as background only, do "
            "not reference them in the reply):\n"
            + "\n".join(foundation_lines)
            + "\n\n"
        )
    else:
        sector_block = ""

    # Sprint 6 W10 — append FL-15 clarifying questions about
    # Required Data Checks the PM has not yet mentioned.  Empty
    # string when no checks are missed or the catalog is absent,
    # so the rest of the discovery prompt stays bit-for-bit
    # identical to pre-W10 when there is nothing to ask.
    clarifying_block = _clarifying_question_block(pm_message)
    if clarifying_block:
        clarifying_block = clarifying_block + "\n\n"

    system_prompt = sector_block + clarifying_block + _DISCOVERY_PROMPT
    user_prompt = (
        f"PM's rule description: {pm_message}\n\n"
        f"Previous context: {state.context_summary}\n"
        f"Language: {state.detected_language}"
    )
    try:
        response = await litellm.acompletion(
            model=_PRIMARY_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=_TEMP_STRICT,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        data = _parse_json(response)
        rule_type = _parse_rule_type(data.get("rule_type", "ai_rule"))
        confidence = _safe_float(data.get("confidence"), 0.7)
        next_phase = (
            WorkflowPhase.DETAIL_COLLECTION
            if confidence >= 0.5
            else WorkflowPhase.INTENT_DISCOVERY
        )
        return AgentMessage(
            agent_name="discovery",
            message=data.get("agent_message", ""),
            phase=WorkflowPhase.INTENT_DISCOVERY,
            next_phase=next_phase,
            extracted_data={
                "rule_type": rule_type.value,
                "is_composite": bool(data.get("is_composite", False)),
                "components": list(data.get("components", [])),
                "confidence": confidence,
            },
            needs_user_input=confidence < 0.7,
        )
    except Exception as exc:
        logger.error("rule_creation.discovery_failed: %s", exc)
        return AgentMessage(
            agent_name="discovery",
            message=(
                "Could you describe the rule you want to create "
                "in more detail?"
            ),
            phase=WorkflowPhase.INTENT_DISCOVERY,
            needs_user_input=True,
        )


async def _run_label(
    state: ConversationState,
    pm_message: str,
) -> AgentMessage:
    """Extract a :class:`LabelComponent` from the PM's reply."""
    existing = ""
    if state.partial_bundle.label_component is not None:
        existing = state.partial_bundle.label_component.model_dump_json()

    user_prompt = (
        f"PM says: {pm_message}\n\n"
        f"Existing label: {existing or 'None'}\n"
        f"Available fields: {', '.join(LABEL_FIELDS)}\n"
        f"Context: {state.context_summary}"
    )
    try:
        response = await litellm.acompletion(
            model=_PRIMARY_MODEL,
            messages=[
                {"role": "system", "content": _LABEL_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=_TEMP_STRICT,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        data = _parse_json(response)
        label = _parse_label(data)
        is_complete = bool(label.name and label.conditions)
        return AgentMessage(
            agent_name="label",
            message=data.get("agent_message", ""),
            phase=WorkflowPhase.DETAIL_COLLECTION,
            next_phase=(
                WorkflowPhase.CONFIRMATION
                if is_complete
                else WorkflowPhase.DETAIL_COLLECTION
            ),
            extracted_data={"label_component": label.model_dump()},
            needs_user_input=not is_complete,
        )
    except Exception as exc:
        logger.error("rule_creation.label_failed: %s", exc)
        return AgentMessage(
            agent_name="label",
            message="Which reservation field should I use for the condition?",
            phase=WorkflowPhase.DETAIL_COLLECTION,
            needs_user_input=True,
        )


async def _run_tag(
    state: ConversationState,
    pm_message: str,
) -> AgentMessage:
    """Extract a :class:`TagComponent` from the PM's reply."""
    user_prompt = (
        f"PM says: {pm_message}\n\n"
        f"Context: {state.context_summary}"
    )
    try:
        response = await litellm.acompletion(
            model=_PRIMARY_MODEL,
            messages=[
                {"role": "system", "content": _TAG_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=_TEMP_STRICT,
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        data = _parse_json(response)
        tag = TagComponent(
            name=data.get("name", ""),
            description=data.get("description", ""),
            priority=data.get("priority", "medium"),
            keywords=list(data.get("keywords", [])),
        )
        is_complete = bool(tag.name and tag.description)
        return AgentMessage(
            agent_name="tag",
            message=data.get("agent_message", ""),
            phase=WorkflowPhase.DETAIL_COLLECTION,
            next_phase=(
                WorkflowPhase.CONFIRMATION
                if is_complete
                else WorkflowPhase.DETAIL_COLLECTION
            ),
            extracted_data={"tag_component": tag.model_dump()},
            needs_user_input=not is_complete,
        )
    except Exception as exc:
        logger.error("rule_creation.tag_failed: %s", exc)
        return AgentMessage(
            agent_name="tag",
            message="What message pattern should this tag detect?",
            phase=WorkflowPhase.DETAIL_COLLECTION,
            needs_user_input=True,
        )


async def _run_ai_rule(
    state: ConversationState,
    pm_message: str,
) -> AgentMessage:
    """Extract an :class:`AIRuleComponent`, handling composite delegations."""
    user_prompt = (
        f"PM says: {pm_message}\n\n"
        f"Rule type: {state.rule_type}\n"
        f"Is composite: {state.is_composite}\n"
        f"Components needed: {state.components}\n"
        f"Context: {state.context_summary}"
    )
    try:
        response = await litellm.acompletion(
            model=_PRIMARY_MODEL,
            messages=[
                {"role": "system", "content": _AI_RULE_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=_TEMP_STRICT,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        data = _parse_json(response)
        ai_rule = AIRuleComponent(
            name=data.get("name", ""),
            description=data.get("description", ""),
            expected_behavior=data.get("expected_behavior", ""),
        )
        delegate_to = data.get("delegate_to")
        is_complete = bool(
            ai_rule.name
            and ai_rule.expected_behavior
            and not delegate_to
        )
        extracted: dict[str, Any] = {
            "ai_rule_component": ai_rule.model_dump(),
        }
        if delegate_to:
            extracted["delegate_to"] = delegate_to
        return AgentMessage(
            agent_name="ai_rule",
            message=data.get("agent_message", ""),
            phase=WorkflowPhase.DETAIL_COLLECTION,
            next_phase=(
                WorkflowPhase.CONFIRMATION
                if is_complete
                else WorkflowPhase.DETAIL_COLLECTION
            ),
            extracted_data=extracted,
            needs_user_input=not is_complete and not delegate_to,
        )
    except Exception as exc:
        logger.error("rule_creation.ai_rule_failed: %s", exc)
        return AgentMessage(
            agent_name="ai_rule",
            message="What behavior should the AI follow in this scenario?",
            phase=WorkflowPhase.DETAIL_COLLECTION,
            needs_user_input=True,
        )


async def _run_escalation(
    state: ConversationState,
    pm_message: str,
) -> AgentMessage:
    """Extract escalation configuration for the current tag bundle."""
    tag = state.partial_bundle.tag_component
    tag_name = tag.name if tag is not None else "unknown"
    user_prompt = (
        f"PM says: {pm_message}\n\n"
        f"Tag being configured: {tag_name}\n"
        f"Context: {state.context_summary}"
    )
    try:
        response = await litellm.acompletion(
            model=_LIGHT_MODEL,
            messages=[
                {"role": "system", "content": _ESCALATION_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=_TEMP_STRICT,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        data = _parse_json(response)
        escalation = EscalationComponent(
            escalate_to=data.get("escalate_to", "pm"),
            auto_create_task=bool(data.get("auto_create_task", False)),
            task_priority=data.get("task_priority", "Medium"),
            notification_channel=data.get("notification_channel", "default"),
        )
        return AgentMessage(
            agent_name="escalation",
            message=data.get("agent_message", ""),
            phase=WorkflowPhase.DETAIL_COLLECTION,
            next_phase=WorkflowPhase.CONFIRMATION,
            extracted_data={
                "escalation_component": escalation.model_dump(),
            },
            needs_user_input=False,
        )
    except Exception as exc:
        logger.error("rule_creation.escalation_failed: %s", exc)
        return AgentMessage(
            agent_name="escalation",
            message="Should this tag create a task or just notify you?",
            phase=WorkflowPhase.DETAIL_COLLECTION,
            needs_user_input=True,
        )


async def _run_confirmation(
    state: ConversationState,
    pm_message: str,
) -> AgentMessage:
    """Confirm, edit, or cancel the assembled :class:`RuleBundle`."""
    bundle_json = state.partial_bundle.model_dump_json(indent=2)
    user_prompt = (
        f"PM says: {pm_message}\n\n"
        f"Rule bundle to confirm:\n{bundle_json}\n\n"
        "Does the PM want to confirm, edit, or cancel?"
    )
    try:
        response = await litellm.acompletion(
            model=_LIGHT_MODEL,
            messages=[
                {"role": "system", "content": _CONFIRMATION_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=_TEMP_STRICT,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        data = _parse_json(response)
        decision = data.get("decision", "confirm")
        if decision == "confirm":
            return AgentMessage(
                agent_name="confirmation",
                message=data.get(
                    "agent_message",
                    "Rule created successfully!",
                ),
                phase=WorkflowPhase.CONFIRMATION,
                next_phase=WorkflowPhase.FINALIZED,
                extracted_data={"decision": "confirm"},
                needs_user_input=False,
            )
        if decision == "cancel":
            return AgentMessage(
                agent_name="confirmation",
                message=data.get(
                    "agent_message",
                    "Rule creation cancelled.",
                ),
                phase=WorkflowPhase.CONFIRMATION,
                next_phase=WorkflowPhase.CANCELLED,
                extracted_data={"decision": "cancel"},
                needs_user_input=False,
            )
        return AgentMessage(
            agent_name="confirmation",
            message=data.get(
                "agent_message",
                "What would you like to change?",
            ),
            phase=WorkflowPhase.CONFIRMATION,
            next_phase=WorkflowPhase.DETAIL_COLLECTION,
            extracted_data={
                "decision": "edit",
                "edit_component": data.get("edit_component", ""),
            },
            needs_user_input=True,
        )
    except Exception as exc:
        logger.error("rule_creation.confirmation_failed: %s", exc)
        return AgentMessage(
            agent_name="confirmation",
            message=(
                "Would you like to confirm this rule, edit it, or cancel?"
            ),
            phase=WorkflowPhase.CONFIRMATION,
            needs_user_input=True,
        )


# ── parsing helpers ───────────────────────────────────────── #


def _parse_json(response: Any) -> dict[str, Any]:
    """Extract the JSON object from a ``litellm`` completion response."""
    content = response.choices[0].message.content or "{}"
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _parse_rule_type(raw: str) -> RuleType:
    """Coerce a raw rule-type string into the enum with a safe fallback."""
    try:
        return RuleType(raw)
    except ValueError:
        return RuleType.AI_RULE


def _parse_label(data: dict[str, Any]) -> LabelComponent:
    """Build a :class:`LabelComponent` from the discovery LLM payload."""
    conditions: list[LabelCondition] = []
    for cond in data.get("conditions", []):
        field_name = cond.get("field", "")
        if field_name not in LABEL_FIELDS:
            continue
        try:
            operator = LabelOperator(cond.get("operator", "Equals"))
        except ValueError:
            operator = LabelOperator.EQUALS
        conditions.append(
            LabelCondition(
                field=field_name,
                operator=operator,
                value=str(cond.get("value", "")),
            ),
        )
    return LabelComponent(
        name=data.get("name", ""),
        icon=data.get("icon", ""),
        conditions=conditions,
    )


def _safe_float(raw: Any, default: float) -> float:
    """Coerce ``raw`` to ``float``; fall back to ``default`` on failure."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# ── system prompts (preserved verbatim from original agents) ── #


_GREETING_PROMPT = """You are the greeting agent for a rule creation system.

Your job:
1. Welcome the property manager
2. Detect their language
3. Ask what kind of rule they want to create

Rule types available:
- Label: Data-driven condition (e.g. "when booking is over $500")
- Tag: Message pattern detection (e.g. "detect cleanliness complaints")
- AI Rule: Behavior override (e.g. "never confirm availability")

Return JSON:
{
    "agent_message": "Welcome! What kind of rule would you like to create?",
    "detected_language": "en"
}
"""


_DISCOVERY_PROMPT = """You are the discovery agent for a rule creation system.

Analyze the PM's description to determine:
1. Rule type needed
2. Whether it's a simple or composite rule
3. Confidence in classification

Rule types:
- label: Data-driven condition on reservation fields (numberOfGuest, totalPrice, status, etc.)
- tag: Detect message patterns from guests (complaints, requests)
- ai_rule: Override AI behavior (custom instructions)
- label_then_ai_rule: IF data condition THEN change AI behavior
- tag_then_ai_rule: IF message pattern detected THEN change AI behavior
- label_tag_ai_rule: Combined data + message + behavior

Return JSON:
{
    "rule_type": "label_then_ai_rule",
    "is_composite": true,
    "components": ["label", "ai_rule"],
    "confidence": 0.85,
    "agent_message": "I understand you want a rule that..."
}
"""


_LABEL_PROMPT = """You are the label specialist agent for rule creation.

Build data-driven conditions on reservation fields.

Available fields (use EXACTLY these names):
reservationId, status, numberOfGuest, numberOfNights, totalPrice,
bookingChannel, channelCode, source, listingId, propertyId,
numberOfChildren, isPaid, isReturning, checkInDate

Available operators:
Equals, NotEquals, GreaterThan, LessThan, Contains, NotContains, In, NotIn

Return JSON:
{
    "name": "VIP Guest Label",
    "icon": "star",
    "conditions": [
        {"field": "totalPrice", "operator": "GreaterThan", "value": "500"},
        {"field": "isReturning", "operator": "Equals", "value": "true"}
    ],
    "agent_message": "I've set up the VIP label with these conditions..."
}

If PM's description is vague, ask specific clarifying questions.
"""


_TAG_PROMPT = """You are the tag specialist agent for rule creation.

Build semantic message detection tags.

A tag detects MESSAGE PATTERNS from guests (not data conditions).
Examples: "cleanliness complaint", "discount request", "parking question"

Return JSON:
{
    "name": "Cleanliness Complaint",
    "description": "Guest mentions dirty, stained, or unclean areas",
    "priority": "high",
    "keywords": ["dirty", "stain", "unclean", "dust", "hygiene"],
    "agent_message": "I've created a tag that will detect..."
}

Priority: low, medium, high
Keywords are optional hints — the main detection uses the description semantically.
"""


_AI_RULE_PROMPT = """You are the AI rule specialist agent for rule creation.

Build behavioral policies for the AI assistant.

An AI rule defines HOW the AI should behave in specific situations.
Examples:
- "Never confirm availability without checking the calendar"
- "Always offer early check-in for VIP guests"
- "Respond to parking questions with: We will answer you ASAP"

For COMPOSITE rules (label_then_ai_rule, tag_then_ai_rule):
- Build the AI behavior part
- If the label or tag condition needs building, set delegate_to

Return JSON:
{
    "name": "No Availability Confirmation",
    "description": "Prevent confirming availability without verification",
    "expected_behavior": "When guest asks about availability, always say you will check and get back",
    "delegate_to": null,
    "agent_message": "I've created the behavior rule..."
}

delegate_to options: "label_agent", "tag_agent", null (if complete)
"""


_ESCALATION_PROMPT = """You are the escalation specialist for rule creation.

Configure what happens when a tag matches a guest message.

Options:
- escalate_to: "pm" (property manager), "ops" (operations team), "owner"
- auto_create_task: true/false
- task_priority: "Low", "Medium", "High", "Urgent"
- notification_channel: "default", "email", "whatsapp", "sms"

Return JSON:
{
    "escalate_to": "pm",
    "auto_create_task": true,
    "task_priority": "High",
    "notification_channel": "default",
    "agent_message": "When this tag matches, I'll create a High priority task..."
}
"""


_CONFIRMATION_PROMPT = """You are the confirmation agent for rule creation.

Present the rule summary and handle the PM's decision.

PM can:
1. CONFIRM — finalize and save the rule
2. EDIT — modify a specific component
3. CANCEL — discard the rule

When presenting the summary, format it clearly:
- Rule name and type
- Conditions (for labels)
- Detection description (for tags)
- AI behavior (for ai_rules)
- Escalation (if configured)

Return JSON:
{
    "decision": "confirm",
    "agent_message": "Here's your rule summary:...\n\nRule saved!",
    "edit_component": ""
}

edit_component: "label", "tag", "ai_rule", "escalation" (only when decision=edit)
"""
