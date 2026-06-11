"""Vertical pack loader (Batch 6).

Loads a vertical pack's YAML data files (e.g. ``packs/hospitality/``)
into the kernel's injectable constructs — closing the loop the earlier
batches left open: every genericised vocabulary/policy that moved to
pack data gets a typed loader here.  The kernel still ships no
vocabulary; callers (services, seeds, tests) choose the pack directory.

Loaded surfaces:

- ``tier_defaults.yaml``      → ``TierPolicy`` mapping
- ``approval.yaml``           → ``ApprovalPolicy`` + never-auto set
- ``blockers.yaml``           → blocker default severity / actions
- ``workflow_kinds.yaml``     → ``InMemoryWorkflowKindRegistry`` seed +
                                 incident event types
- ``scenario_features.yaml``  → per-scenario ``FeatureWhitelist`` map
- ``scenarios.yaml``          → scenario vocabulary (list of kinds)

``seed_workflow_kinds`` writes the pack's kinds into a tenant's
``brain_workflow_kinds`` registry rows (idempotent upsert).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.brain.autonomy.approval import ApprovalPolicy
from core.brain.autonomy.workflow_kinds import InMemoryWorkflowKindRegistry
from core.brain.certificates.policy import TierPolicy
from core.brain.certificates.tier import AutonomyTier
from core.brain.patterns.blockers import BlockerSeverity
from core.brain.patterns.scenario_features import FeatureWhitelist

__all__ = ["PackData", "load_pack", "seed_workflow_kinds"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PackData:
    """Parsed vertical-pack content ready for kernel injection."""

    name: str
    tier_policy: TierPolicy = field(default_factory=TierPolicy)
    approval_policy: ApprovalPolicy = field(default_factory=ApprovalPolicy)
    never_auto_approve: frozenset[str] = frozenset()
    blocker_severity: dict[str, BlockerSeverity] = field(default_factory=dict)
    blocker_actions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    workflow_kind_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    workflow_kind_labels: dict[str, str] = field(default_factory=dict)
    incident_event_types: frozenset[str] = frozenset()
    scenario_features: dict[str, FeatureWhitelist] = field(default_factory=dict)
    scenarios: tuple[str, ...] = ()

    def workflow_kind_registry(self) -> InMemoryWorkflowKindRegistry:
        return InMemoryWorkflowKindRegistry(self.workflow_kind_aliases, self.workflow_kind_labels)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_pack(pack_dir: str | Path) -> PackData:
    """Parse every known data file in ``pack_dir`` (missing files skip)."""
    root = Path(pack_dir)
    if not root.is_dir():
        raise ValueError(f"pack directory not found: {root}")

    tiers = _read_yaml(root / "tier_defaults.yaml").get("tier_ceilings", {})
    tier_policy = TierPolicy({kind: AutonomyTier(value) for kind, value in tiers.items()})

    approval_raw = _read_yaml(root / "approval.yaml")
    approval_policy = ApprovalPolicy(
        auto_approve_actions=frozenset(approval_raw.get("auto_approve_actions") or ()),
        conditional_approve_actions=frozenset(approval_raw.get("conditional_approve_actions") or ()),
        always_require_approval=frozenset(approval_raw.get("always_require_approval") or ()),
    )

    blockers_raw = _read_yaml(root / "blockers.yaml")
    blocker_severity = {
        kind: BlockerSeverity(value) for kind, value in (blockers_raw.get("blocker_severity") or {}).items()
    }
    blocker_actions = {kind: tuple(actions) for kind, actions in (blockers_raw.get("blocker_actions") or {}).items()}

    kinds_raw = _read_yaml(root / "workflow_kinds.yaml")
    kind_aliases = {
        kind: tuple((spec or {}).get("event_aliases") or ())
        for kind, spec in (kinds_raw.get("workflow_kinds") or {}).items()
    }
    kind_labels = {
        kind: str((spec or {}).get("label"))
        for kind, spec in (kinds_raw.get("workflow_kinds") or {}).items()
        if (spec or {}).get("label")
    }

    features_raw = _read_yaml(root / "scenario_features.yaml")
    scenario_features = {
        scenario: FeatureWhitelist(
            pms_keys=tuple(spec["pms_keys"]) if (spec or {}).get("pms_keys") else None,
            calendar_keys=tuple(spec["calendar_keys"]) if (spec or {}).get("calendar_keys") else None,
            guest_keys=tuple(spec["guest_keys"]) if (spec or {}).get("guest_keys") else None,
        )
        for scenario, spec in (features_raw.get("scenario_features") or {}).items()
    }

    scenarios = tuple(_read_yaml(root / "scenarios.yaml").get("scenarios") or ())

    pack = PackData(
        name=root.name,
        tier_policy=tier_policy,
        approval_policy=approval_policy,
        never_auto_approve=frozenset(approval_raw.get("never_auto_approve") or ()),
        blocker_severity=blocker_severity,
        blocker_actions=blocker_actions,
        workflow_kind_aliases=kind_aliases,
        workflow_kind_labels=kind_labels,
        incident_event_types=frozenset(kinds_raw.get("incident_event_types") or ()),
        scenario_features=scenario_features,
        scenarios=scenarios,
    )
    logger.info(
        "pack loaded name=%s kinds=%s scenarios=%s tiers=%s",
        pack.name,
        len(kind_aliases),
        len(scenarios),
        len(tiers),
    )
    return pack


def seed_workflow_kinds(pack: PackData, *, session_maker, tenant_id: str) -> int:
    """Idempotently upsert the pack's workflow kinds into a tenant registry."""
    from sqlalchemy import select

    from models.brain_autonomy import BrainWorkflowKind

    written = 0
    with session_maker() as session:
        for kind, aliases in pack.workflow_kind_aliases.items():
            label = pack.workflow_kind_labels.get(kind)
            row = session.execute(
                select(BrainWorkflowKind).where(
                    BrainWorkflowKind.tenant_id == tenant_id,
                    BrainWorkflowKind.kind == kind,
                )
            ).scalar_one_or_none()
            if row is None:
                session.add(
                    BrainWorkflowKind(
                        tenant_id=tenant_id, kind=kind, event_aliases=list(aliases), label=label
                    )
                )
                written += 1
            else:
                row.event_aliases = list(aliases)
                row.label = label
                row.enabled = True
        session.commit()
    return written
