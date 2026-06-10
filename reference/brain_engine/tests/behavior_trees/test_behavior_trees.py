"""Behaviour of the BT composition layer."""

from __future__ import annotations

import pytest

from brain_engine.behavior_trees import (
    ActionBehaviour,
    ConditionBehaviour,
    Status,
    TickRecord,
    TreeContext,
    TreeRunner,
    parallel,
    selector,
    sequence,
)


def _ctx(**data: object) -> TreeContext:
    """Build a :class:`TreeContext` with the supplied data."""
    return TreeContext(data=dict(data))


def test_condition_success_returns_success() -> None:
    """Truthy predicate ⇒ :attr:`Status.SUCCESS`."""
    ctx = _ctx()
    leaf = ConditionBehaviour(
        name="always-true",
        context=ctx,
        predicate=lambda _c: True,
    )
    leaf.tick_once()
    assert leaf.status is Status.SUCCESS


def test_condition_failure_returns_failure() -> None:
    """Falsey predicate ⇒ :attr:`Status.FAILURE`."""
    ctx = _ctx()
    leaf = ConditionBehaviour(
        name="always-false",
        context=ctx,
        predicate=lambda _c: False,
    )
    leaf.tick_once()
    assert leaf.status is Status.FAILURE


def test_condition_exception_returns_failure() -> None:
    """Raised exception ⇒ :attr:`Status.FAILURE` + rationale captured."""
    ctx = _ctx()

    def boom(_c: TreeContext) -> bool:
        raise RuntimeError("nope")

    leaf = ConditionBehaviour(
        name="explodes", context=ctx, predicate=boom,
    )
    leaf.tick_once()
    assert leaf.status is Status.FAILURE
    audit = ctx.metadata["audit"]
    assert isinstance(audit, list)
    assert audit[-1].rationale.startswith(
        "explodes: exception RuntimeError"
    )


def test_action_status_passes_through() -> None:
    """Action returning :class:`Status` passes it through."""
    ctx = _ctx()
    leaf = ActionBehaviour(
        name="raw-success",
        context=ctx,
        action=lambda _c: Status.SUCCESS,
    )
    leaf.tick_once()
    assert leaf.status is Status.SUCCESS


def test_action_bool_translates() -> None:
    """Action returning ``bool`` translates to status."""
    ctx = _ctx()
    ok = ActionBehaviour(
        name="bool-true",
        context=ctx,
        action=lambda _c: True,
    )
    ok.tick_once()
    assert ok.status is Status.SUCCESS
    bad = ActionBehaviour(
        name="bool-false",
        context=ctx,
        action=lambda _c: False,
    )
    bad.tick_once()
    assert bad.status is Status.FAILURE


def test_action_none_defaults_to_success() -> None:
    """Action returning ``None`` defaults to SUCCESS."""
    ctx = _ctx()
    leaf = ActionBehaviour(
        name="void",
        context=ctx,
        action=lambda _c: None,
    )
    leaf.tick_once()
    assert leaf.status is Status.SUCCESS


def test_action_invalid_return_raises() -> None:
    """Returning anything else raises ``TypeError`` at tick time."""
    ctx = _ctx()

    def bad(_c: TreeContext) -> object:
        return 42

    leaf = ActionBehaviour(
        name="weird",
        context=ctx,
        action=bad,  # type: ignore[arg-type]
    )
    leaf.tick_once()
    # The translator raises; the BT then catches and surfaces
    # FAILURE through the standard py_trees exception path.
    assert leaf.status is Status.FAILURE


def test_action_side_effect_runs() -> None:
    """Action with side effect mutates the context."""
    ctx = _ctx(counter=0)

    def increment(c: TreeContext) -> Status:
        c.set("counter", int(c.get("counter", 0)) + 1)
        return Status.SUCCESS

    leaf = ActionBehaviour(
        name="incr", context=ctx, action=increment,
    )
    leaf.tick_once()
    assert ctx.get("counter") == 1


def test_sequence_short_circuits_on_failure() -> None:
    """Sequence stops at the first FAILURE child."""
    ctx = _ctx()
    side_effect: list[str] = []

    def make_action(tag: str, status: Status) -> ActionBehaviour:
        def run(_c: TreeContext) -> Status:
            side_effect.append(tag)
            return status

        return ActionBehaviour(
            name=tag, context=ctx, action=run,
        )

    root = sequence(
        name="seq",
        children=[
            make_action("a", Status.SUCCESS),
            make_action("b", Status.FAILURE),
            make_action("c", Status.SUCCESS),
        ],
    )
    root.tick_once()
    assert root.status is Status.FAILURE
    assert side_effect == ["a", "b"]


def test_sequence_success_runs_all_children() -> None:
    """All-SUCCESS Sequence ticks every child + reports SUCCESS."""
    ctx = _ctx()
    seen: list[str] = []
    children = [
        ActionBehaviour(
            name=f"leaf-{i}",
            context=ctx,
            action=lambda _c, idx=i: (
                seen.append(f"leaf-{idx}") or Status.SUCCESS
            ),
        )
        for i in range(3)
    ]
    root = sequence(name="seq", children=children)
    root.tick_once()
    assert root.status is Status.SUCCESS
    assert seen == ["leaf-0", "leaf-1", "leaf-2"]


def test_selector_picks_first_success() -> None:
    """Selector returns the first SUCCESS child's status."""
    ctx = _ctx()
    root = selector(
        name="sel",
        children=[
            ConditionBehaviour(
                name="no",
                context=ctx,
                predicate=lambda _c: False,
            ),
            ConditionBehaviour(
                name="yes",
                context=ctx,
                predicate=lambda _c: True,
            ),
            ConditionBehaviour(
                name="never",
                context=ctx,
                predicate=lambda _c: False,
            ),
        ],
    )
    root.tick_once()
    assert root.status is Status.SUCCESS


def test_selector_all_failure_reports_failure() -> None:
    """All-FAILURE Selector reports FAILURE."""
    ctx = _ctx()
    root = selector(
        name="sel",
        children=[
            ConditionBehaviour(
                name=f"f-{i}",
                context=ctx,
                predicate=lambda _c: False,
            )
            for i in range(3)
        ],
    )
    root.tick_once()
    assert root.status is Status.FAILURE


def test_parallel_success_on_all() -> None:
    """Default parallel policy requires every child to succeed."""
    ctx = _ctx()
    root = parallel(
        name="par",
        children=[
            ConditionBehaviour(
                name="a",
                context=ctx,
                predicate=lambda _c: True,
            ),
            ConditionBehaviour(
                name="b",
                context=ctx,
                predicate=lambda _c: True,
            ),
        ],
    )
    root.tick_once()
    assert root.status is Status.SUCCESS


def test_parallel_one_failure_yields_failure() -> None:
    """One child failing fails the default-policy Parallel."""
    ctx = _ctx()
    root = parallel(
        name="par",
        children=[
            ConditionBehaviour(
                name="a",
                context=ctx,
                predicate=lambda _c: True,
            ),
            ConditionBehaviour(
                name="b",
                context=ctx,
                predicate=lambda _c: False,
            ),
        ],
    )
    root.tick_once()
    assert root.status is Status.FAILURE


def test_runner_records_audit_log() -> None:
    """The runner emits per-leaf :class:`TickRecord` entries."""
    ctx = _ctx()
    root = sequence(
        name="seq",
        children=[
            ConditionBehaviour(
                name="cond",
                context=ctx,
                predicate=lambda _c: True,
                rationale_true="cond: ok",
            ),
            ActionBehaviour(
                name="act",
                context=ctx,
                action=lambda _c: Status.SUCCESS,
                rationale="act: ran",
            ),
        ],
    )
    runner = TreeRunner()
    result = runner.run(root=root, context=ctx)
    assert result.status is Status.SUCCESS
    assert result.ticks == 1
    assert not result.timed_out
    names = [r.leaf_name for r in result.records]
    rationales = [r.rationale for r in result.records]
    assert names == ["cond", "act"]
    assert rationales == ["cond: ok", "act: ran"]


def test_runner_bounded_by_max_ticks() -> None:
    """RUNNING-forever leaves bounce off the tick cap."""
    ctx = _ctx()

    class ForeverRunning(ConditionBehaviour):  # type: ignore[misc]
        def update(self) -> Status:
            return Status.RUNNING

    root = ForeverRunning(
        name="forever", context=ctx, predicate=lambda _c: True,
    )
    runner = TreeRunner(max_ticks=3)
    result = runner.run(root=root, context=ctx)
    assert result.timed_out is True
    assert result.ticks == 3
    assert result.status is Status.RUNNING


def test_runner_resets_audit_log_between_runs() -> None:
    """A second :meth:`run` does not accumulate prior records."""
    ctx = _ctx()
    root = ConditionBehaviour(
        name="cond",
        context=ctx,
        predicate=lambda _c: True,
    )
    runner = TreeRunner()
    first = runner.run(root=root, context=ctx)
    second = runner.run(root=root, context=ctx)
    assert len(first.records) == 1
    assert len(second.records) == 1


def test_tick_record_requires_tz_aware() -> None:
    """``TickRecord`` rejects naive datetimes."""
    from datetime import datetime

    with pytest.raises(ValueError, match="tz-aware"):
        TickRecord(
            leaf_name="x",
            status=Status.SUCCESS,
            rationale="",
            ticked_at=datetime(2026, 5, 11),
        )


def test_tick_record_requires_name() -> None:
    """``TickRecord`` rejects an empty leaf name."""
    from datetime import datetime, timezone

    with pytest.raises(ValueError, match="leaf_name"):
        TickRecord(
            leaf_name="",
            status=Status.SUCCESS,
            rationale="",
            ticked_at=datetime.now(timezone.utc),
        )


def test_composer_rejects_empty_name() -> None:
    """Composite builders require a non-empty name."""
    with pytest.raises(ValueError, match="name"):
        sequence(name="", children=[])
    with pytest.raises(ValueError, match="name"):
        selector(name="", children=[])
    with pytest.raises(ValueError, match="name"):
        parallel(name="", children=[])


def test_runner_rejects_invalid_max_ticks() -> None:
    """``max_ticks`` must be a positive integer."""
    with pytest.raises(ValueError, match="max_ticks"):
        TreeRunner(max_ticks=0)


def test_realistic_abstention_chain() -> None:
    """End-to-end: abstention + policy + emit composed in a Sequence."""
    ctx = _ctx(
        confidence=0.92,
        policy_allowed=True,
        emitted=False,
    )

    def abstention_ok(c: TreeContext) -> bool:
        confidence = c.get("confidence")
        if not isinstance(confidence, float):
            return False
        return confidence >= 0.7

    def policy_ok(c: TreeContext) -> bool:
        return bool(c.get("policy_allowed"))

    def emit(c: TreeContext) -> Status:
        c.set("emitted", True)
        return Status.SUCCESS

    root = sequence(
        name="reply-pipeline",
        children=[
            ConditionBehaviour(
                name="abstention",
                context=ctx,
                predicate=abstention_ok,
            ),
            ConditionBehaviour(
                name="policy",
                context=ctx,
                predicate=policy_ok,
            ),
            ActionBehaviour(
                name="emit",
                context=ctx,
                action=emit,
            ),
        ],
    )
    result = TreeRunner().run(root=root, context=ctx)
    assert result.status is Status.SUCCESS
    assert ctx.get("emitted") is True
    assert [r.leaf_name for r in result.records] == [
        "abstention",
        "policy",
        "emit",
    ]
