"""Tenant-scoped observe-posture service and audit surface (CEN-31).

This service makes the legacy gate rollout config operable per tenant
without expanding the runtime touchpoint set:

1. ``BRAIN_GATES_MODE=off`` remains the global kill switch.
2. An explicit tenant override of ``off`` always disables the tenant.
3. ``BRAIN_GATES_MODE=enforce`` stays config-owned; this surface refuses
   writes while enforce is configured, and ``observe`` overrides are
   ignored for effective-mode resolution in that posture.
4. An explicit tenant override of ``observe`` enables observe when the
   configured mode is ``observe``, even if the legacy allowlist excludes
   the tenant.
5. When no explicit tenant override exists, runtime behavior falls back
   to the existing env mode + allowlist semantics.

The write path persists the explicit requested posture and appends an
immutable audit row capturing both the requested transition and the
effective posture before/after applying the resolution rules above.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final, Literal, TypedDict, cast

from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

from core.brain.runtime_gateway import (
    configured_governance_mode,
    configured_tenant_enabled,
    resolve_effective_governance_mode,
)
from extensions.ext_database import db
from models.brain_gate_posture import (
    BrainTenantGatePosture,
    BrainTenantGatePostureAudit,
)

GatePostureValue = Literal["off", "observe"]
EffectiveGatePostureValue = Literal["off", "observe", "enforce"]

_MAX_REASON_LENGTH: Final[int] = 255
_DEFAULT_AUDIT_LIMIT: Final[int] = 50
_MAX_AUDIT_LIMIT: Final[int] = 200


class GatePostureResolution(TypedDict):
    configured_mode: EffectiveGatePostureValue
    effective_mode: EffectiveGatePostureValue
    tenant_enabled: bool
    override_mode: GatePostureValue | None
    source: str
    active: bool


class GatePostureState(TypedDict):
    tenant_id: str
    override_posture: GatePostureValue | None
    changed_at: str | None
    changed_by: str | None
    reason: str | None
    resolution: GatePostureResolution


class GatePostureAuditEntry(TypedDict):
    actor_type: str
    actor_id: str | None
    changed_by: str
    prior_posture: GatePostureValue
    new_posture: GatePostureValue
    prior_effective_posture: EffectiveGatePostureValue
    new_effective_posture: EffectiveGatePostureValue
    changed_at: str
    reason: str


class ObserveOnlyGatePostureWriteError(RuntimeError):
    """Raised when config-owned enforce posture makes writes out of scope."""


def _session_maker() -> sessionmaker:
    return sessionmaker(bind=db.engine, expire_on_commit=False)


def _normalize_reason(reason: str) -> str:
    normalized = reason.strip()
    if not normalized:
        raise ValueError("reason is required")
    if len(normalized) > _MAX_REASON_LENGTH:
        raise ValueError(f"reason must be {_MAX_REASON_LENGTH} characters or fewer")
    return normalized


def _normalize_posture(posture: str) -> GatePostureValue:
    normalized = posture.strip().lower()
    if normalized not in {"off", "observe"}:
        raise ValueError("posture must be 'off' or 'observe'")
    return cast(GatePostureValue, normalized)


def _normalize_effective_posture(posture: str) -> EffectiveGatePostureValue:
    normalized = posture.strip().lower()
    if normalized not in {"off", "observe", "enforce"}:
        raise ValueError("effective posture must be 'off', 'observe', or 'enforce'")
    return cast(EffectiveGatePostureValue, normalized)


def _normalize_actor(actor_kind: str, actor_id: str | None) -> tuple[str, str]:
    normalized_kind = actor_kind.strip().lower()
    if normalized_kind not in {"account", "api_key"}:
        raise ValueError("actor_kind must be 'account' or 'api_key'")
    if actor_id is None or not actor_id.strip():
        raise ValueError("actor_id is required")
    return normalized_kind, actor_id.strip()


def _isoformat(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    else:
        moment = moment.astimezone(UTC)
    return moment.isoformat().replace("+00:00", "Z")


class BrainGatePostureService:
    """Tenant-scoped read/write facade for explicit observe posture control."""

    _tenant_id: str
    _sessions: sessionmaker

    def __init__(self, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._tenant_id = tenant_id
        self._sessions = _session_maker()

    def get_posture(self) -> GatePostureState:
        row = self._current_row()
        return self._serialize_state(row)

    def set_posture(
        self,
        *,
        posture: str,
        reason: str,
        actor_kind: str,
        actor_id: str,
    ) -> GatePostureState:
        normalized_posture = _normalize_posture(posture)
        normalized_reason = _normalize_reason(reason)
        normalized_actor_kind, normalized_actor_id = _normalize_actor(actor_kind, actor_id)
        changed_by = f"{normalized_actor_kind}:{normalized_actor_id}"

        configured_mode = configured_governance_mode()
        if configured_mode == "enforce":
            raise ObserveOnlyGatePostureWriteError(
                "observe-only posture writes are unavailable while BRAIN_GATES_MODE=enforce"
            )

        current = self._current_row()
        if current is not None and current.posture == normalized_posture:
            return self._serialize_state(current)

        tenant_enabled = configured_tenant_enabled(self._tenant_id)
        prior_posture = (
            _normalize_posture(current.posture) if current is not None else self._fallback_posture(tenant_enabled)
        )
        prior_effective_posture, _ = resolve_effective_governance_mode(
            configured_mode=configured_mode,
            tenant_enabled=tenant_enabled,
            override_mode=current.posture if current is not None else None,
        )
        new_effective_posture, _ = resolve_effective_governance_mode(
            configured_mode=configured_mode,
            tenant_enabled=tenant_enabled,
            override_mode=normalized_posture,
        )
        changed_at = datetime.now(UTC)

        with self._sessions() as session:
            row = session.execute(
                select(BrainTenantGatePosture).where(BrainTenantGatePosture.tenant_id == self._tenant_id)
            ).scalar_one_or_none()
            if row is None:
                row = BrainTenantGatePosture(
                    tenant_id=self._tenant_id,
                    posture=normalized_posture,
                    actor_kind=normalized_actor_kind,
                    actor_id=normalized_actor_id,
                    changed_by=changed_by,
                    reason=normalized_reason,
                    changed_at=changed_at,
                )
                session.add(row)
            else:
                row.posture = normalized_posture
                row.actor_kind = normalized_actor_kind
                row.actor_id = normalized_actor_id
                row.changed_by = changed_by
                row.reason = normalized_reason
                row.changed_at = changed_at

            session.add(
                BrainTenantGatePostureAudit(
                    tenant_id=self._tenant_id,
                    prior_posture=prior_posture,
                    new_posture=normalized_posture,
                    prior_effective_posture=prior_effective_posture,
                    new_effective_posture=new_effective_posture,
                    actor_kind=normalized_actor_kind,
                    actor_id=normalized_actor_id,
                    changed_by=changed_by,
                    reason=normalized_reason,
                    occurred_at=changed_at,
                )
            )
            session.commit()

        return self.get_posture()

    def list_audit(self, *, limit: int = _DEFAULT_AUDIT_LIMIT) -> list[GatePostureAuditEntry]:
        bounded_limit = max(1, min(limit, _MAX_AUDIT_LIMIT))
        with self._sessions() as session:
            rows = session.execute(
                select(BrainTenantGatePostureAudit)
                .where(BrainTenantGatePostureAudit.tenant_id == self._tenant_id)
                .order_by(
                    desc(BrainTenantGatePostureAudit.occurred_at),
                    desc(BrainTenantGatePostureAudit.id),
                )
                .limit(bounded_limit)
            ).scalars()
            return [self._serialize_audit(row) for row in rows]

    def _current_row(self) -> BrainTenantGatePosture | None:
        with self._sessions() as session:
            return session.execute(
                select(BrainTenantGatePosture).where(BrainTenantGatePosture.tenant_id == self._tenant_id)
            ).scalar_one_or_none()

    @staticmethod
    def _fallback_posture(tenant_enabled: bool) -> GatePostureValue:
        return "observe" if tenant_enabled and configured_governance_mode() == "observe" else "off"

    def _serialize_state(self, row: BrainTenantGatePosture | None) -> GatePostureState:
        override_mode = _normalize_posture(row.posture) if row is not None else None
        configured_mode = configured_governance_mode()
        tenant_enabled = configured_tenant_enabled(self._tenant_id)
        effective_mode, source = resolve_effective_governance_mode(
            configured_mode=configured_mode,
            tenant_enabled=tenant_enabled,
            override_mode=override_mode,
        )
        return {
            "tenant_id": self._tenant_id,
            "override_posture": override_mode,
            "changed_at": _isoformat(row.changed_at) if row is not None else None,
            "changed_by": row.changed_by if row is not None else None,
            "reason": row.reason if row is not None else None,
            "resolution": {
                "configured_mode": configured_mode,
                "effective_mode": effective_mode,
                "tenant_enabled": tenant_enabled,
                "override_mode": override_mode,
                "source": source,
                "active": effective_mode != "off",
            },
        }

    @staticmethod
    def _serialize_audit(row: BrainTenantGatePostureAudit) -> GatePostureAuditEntry:
        return {
            "actor_type": row.actor_kind,
            "actor_id": row.actor_id,
            "changed_by": row.changed_by,
            "prior_posture": _normalize_posture(row.prior_posture),
            "new_posture": _normalize_posture(row.new_posture),
            "prior_effective_posture": _normalize_effective_posture(row.prior_effective_posture),
            "new_effective_posture": _normalize_effective_posture(row.new_effective_posture),
            "changed_at": _isoformat(row.occurred_at) or "",
            "reason": row.reason,
        }
