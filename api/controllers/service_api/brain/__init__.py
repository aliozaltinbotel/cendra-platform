"""Cendra brain service_api surface (Batch 5, additive package).

Routes (service-token authenticated like every service_api route):

- ``POST /v1/brain/retrieval`` — T6 external-knowledge loopback in
  Dify's External Knowledge Base API shape.
- ``GET /v1/brain/trust-meter/<property_id>`` — TrustMeter view.
- ``GET/POST /v1/brain/policies/<owner_id>`` — owner-policy documents.
- ``GET /v1/brain/cases`` — captured DecisionCases (audit).
"""

from flask import request
from flask_restx import Resource

from controllers.service_api import service_api_ns
from controllers.service_api.wraps import validate_app_token
from core.brain.epistemic.as_of import parse_as_of
from core.brain.policy.errors import OwnerPolicyCompileError
from services.brain_governance_service import BrainGovernanceService
from services.brain_knowledge_gap_service import BrainKnowledgeGapService


@service_api_ns.route("/brain/retrieval")
class BrainRetrievalApi(Resource):
    @validate_app_token
    def post(self, app_model, end_user=None):
        payload = request.get_json(force=True, silent=True) or {}
        setting = payload.get("retrieval_setting") or {}
        # CEN-15 Part A: optional decision-time anchor.  Omitted →
        # current belief (standard contract); unparseable → 400.
        as_of = None
        if payload.get("as_of") is not None:
            try:
                as_of = parse_as_of(payload["as_of"])
            except ValueError as exc:
                return {"message": str(exc)}, 400
        service = BrainGovernanceService(app_model.tenant_id)
        records = service.retrieve_memory(
            str(payload.get("query", "")),
            top_k=int(setting.get("top_k", 5)),
            score_threshold=float(setting.get("score_threshold", 0.0)),
            as_of=as_of,
        )
        return {"records": records}


@service_api_ns.route("/brain/knowledge-gaps/<string:property_id>")
class BrainKnowledgeGapsApi(Resource):
    @validate_app_token
    def get(self, app_model, property_id: str, end_user=None):
        """Knowledge Gap registry read (CEN-15 Part B contract).

        The kernel registry is keyed on a vertical-neutral
        ``subject_ref``; this surface applies the hospitality pack's
        mapping (``property_id`` ⇄ ``subject_ref``) at the wire edge so
        the published contract holds without leaking pack semantics
        into the kernel.
        """
        dedup = request.args.get("dedup", "true").lower() != "false"
        try:
            view = BrainKnowledgeGapService(app_model.tenant_id).list_gaps(
                property_id,
                status=request.args.get("status", "open"),
                dedup=dedup,
            )
        except ValueError as exc:
            return {"message": str(exc)}, 400
        gaps = []
        for gap in view["gaps"]:
            card = dict(gap)
            card["property_id"] = card.pop("subject_ref")
            gaps.append(card)
        return {
            "property_id": view["subject_ref"],
            "as_of_now": view["as_of_now"],
            "gaps": gaps,
        }


@service_api_ns.route("/brain/trust-meter/<string:property_id>")
class BrainTrustMeterApi(Resource):
    @validate_app_token
    def get(self, app_model, property_id: str, end_user=None):
        return BrainGovernanceService(app_model.tenant_id).trust_meter(property_id)


@service_api_ns.route("/brain/policies/<string:owner_id>")
class BrainPolicyApi(Resource):
    @validate_app_token
    def get(self, app_model, owner_id: str, end_user=None):
        policy = BrainGovernanceService(app_model.tenant_id).get_policy(owner_id)
        if policy is None:
            return {"message": "not found"}, 404
        return policy

    @validate_app_token
    def post(self, app_model, owner_id: str, end_user=None):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return BrainGovernanceService(app_model.tenant_id).save_policy(
                owner_id, str(payload.get("document_text", ""))
            )
        except OwnerPolicyCompileError as exc:
            return {"message": str(exc)}, 400


@service_api_ns.route("/brain/cases")
class BrainCasesApi(Resource):
    @validate_app_token
    def get(self, app_model, end_user=None):
        return {
            "cases": BrainGovernanceService(app_model.tenant_id).list_cases(
                property_id=request.args.get("property_id"),
                limit=min(int(request.args.get("limit", 50)), 200),
                offset=int(request.args.get("offset", 0)),
            )
        }
