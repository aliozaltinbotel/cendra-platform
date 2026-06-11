"""Cendra brain console surface (Batch 6, additive package).

Workspace-authenticated console endpoints for the TrustMeter, owner
policies, the decision audit trail, and the tenant gate-posture
read/write surface.  The web UI (web/**/brain/) consumes these; see
PORTING_MAP.
"""

from flask import request
from flask_login import current_user
from flask_restx import Resource
from pydantic import BaseModel, ConfigDict, Field
from werkzeug.exceptions import BadRequest, Conflict

from controllers.common.schema import query_params_from_model, query_params_from_request
from controllers.console import console_ns
from controllers.console.wraps import (
    account_initialization_required,
    edit_permission_required,
    setup_required,
)
from core.brain.policy.errors import OwnerPolicyCompileError
from libs.login import login_required
from services.brain_gate_posture_service import (
    BrainGatePostureService,
    ObserveOnlyGatePostureWriteError,
)
from services.brain_gate_wiring_service import BrainGateWiringService
from services.brain_governance_service import BrainGovernanceService


def _service() -> BrainGovernanceService:
    return BrainGovernanceService(current_user.current_tenant_id)


def _gate_wiring_service() -> BrainGateWiringService:
    return BrainGateWiringService(current_user.current_tenant_id)


def _gate_posture_service() -> BrainGatePostureService:
    return BrainGatePostureService(current_user.current_tenant_id)


class GatePostureMutationPayload(BaseModel):
    posture: str = Field(description="Requested explicit tenant posture: off or observe.")
    reason: str = Field(min_length=1, max_length=255)

    model_config = ConfigDict(extra="forbid")


class GatePostureAuditQuery(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)


@console_ns.route("/brain/trust-meter/<string:property_id>")
class ConsoleBrainTrustMeterApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    def get(self, property_id: str):
        return _service().trust_meter(property_id)


@console_ns.route("/brain/policies/<string:owner_id>")
class ConsoleBrainPolicyApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    def get(self, owner_id: str):
        policy = _service().get_policy(owner_id)
        if policy is None:
            return {"message": "not found"}, 404
        return policy

    @setup_required
    @login_required
    @account_initialization_required
    def post(self, owner_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return _service().save_policy(owner_id, str(payload.get("document_text", "")))
        except OwnerPolicyCompileError as exc:
            return {"message": str(exc)}, 400


@console_ns.route("/brain/cases")
class ConsoleBrainCasesApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    def get(self):
        return {
            "cases": _service().list_cases(
                property_id=request.args.get("property_id"),
                limit=min(int(request.args.get("limit", 50)), 200),
                offset=int(request.args.get("offset", 0)),
            )
        }


@console_ns.route("/brain/gate-posture")
class ConsoleBrainGatePostureApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    def get(self):
        return _gate_posture_service().get_posture()

    @setup_required
    @login_required
    @account_initialization_required
    @edit_permission_required
    def put(self):
        try:
            payload = GatePostureMutationPayload.model_validate(request.get_json(force=True, silent=True) or {})
            return _gate_posture_service().set_posture(
                posture=payload.posture,
                reason=payload.reason,
                actor_kind="account",
                actor_id=current_user.id,
            )
        except ObserveOnlyGatePostureWriteError as exc:
            raise Conflict(str(exc)) from exc
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc


@console_ns.route("/brain/gate-posture/audit")
class ConsoleBrainGatePostureAuditApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    @console_ns.doc(params=query_params_from_model(GatePostureAuditQuery))
    def get(self):
        query = query_params_from_request(GatePostureAuditQuery)
        return {"records": _gate_posture_service().list_audit(limit=query.limit)}


@console_ns.route("/brain/gate-wiring/node-types")
class ConsoleBrainGateWiringNodeTypesApi(Resource):
    """Authoritative gate-wired node-type → touchpoint enumeration (CEN-41)."""

    @setup_required
    @login_required
    @account_initialization_required
    def get(self):
        return {"node_types": _gate_wiring_service().node_type_enumeration()}


@console_ns.route("/brain/gate-wiring/workflow/<string:workflow_id>")
class ConsoleBrainGateWiringWorkflowApi(Resource):
    """Per-workflow / per-node governed-status read for the builder surfaces.

    Lets the console render the per-flow governed indicator and per-node
    markers from this read alone — no canvas-shape heuristics (CEN-41).
    """

    @setup_required
    @login_required
    @account_initialization_required
    def get(self, workflow_id: str):
        report = _gate_wiring_service().inspect_workflow(workflow_id)
        if report is None:
            return {"message": "workflow not found"}, 404
        return report
