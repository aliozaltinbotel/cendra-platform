"""Cendra brain console surface (Batch 6, additive package).

Workspace-authenticated console endpoints for the TrustMeter, owner
policies and the decision audit trail.  The web UI (web/**/brain/)
consumes these; see PORTING_MAP.
"""

from flask import request
from flask_login import current_user
from flask_restx import Resource

from controllers.console import console_ns
from controllers.console.wraps import account_initialization_required, setup_required
from core.brain.policy.errors import OwnerPolicyCompileError
from libs.login import login_required
from services.brain_gate_wiring_service import BrainGateWiringService
from services.brain_governance_service import BrainGovernanceService


def _service() -> BrainGovernanceService:
    return BrainGovernanceService(current_user.current_tenant_id)


def _gate_wiring_service() -> BrainGateWiringService:
    return BrainGateWiringService(current_user.current_tenant_id)


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
