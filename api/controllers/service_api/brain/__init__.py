"""Cendra brain service_api surface (Batch 5, additive package).

Most routes stay service-token authenticated like the rest of
``service_api``. The deliberate exception is the public receipt
verification-key lookup in ``verification_keys.py``: exported receipts
carry only ``key_id``, so third-party verification must be able to
resolve the historical public key without a tenant-scoped token.

- ``POST /v1/brain/retrieval`` — T6 external-knowledge loopback in
  Dify's External Knowledge Base API shape.
- ``GET /v1/brain/trust-meter/<property_id>`` — TrustMeter view.
- ``GET/POST /v1/brain/policies/<owner_id>`` — owner-policy documents.
- ``GET /v1/brain/cases`` — captured DecisionCases (audit).
- ``GET /v1/brain/verification-keys/<key_id>`` — public verification-key lookup.
- ``GET /v1/brain/verification-keys`` — tenant-authenticated verification-key inventory.
"""

from flask import request
from flask_restx import Resource

from controllers.service_api import service_api_ns
from controllers.service_api.wraps import validate_app_token
from core.brain.policy.errors import OwnerPolicyCompileError
from services.brain_governance_service import BrainGovernanceService


@service_api_ns.route("/brain/retrieval")
class BrainRetrievalApi(Resource):
    @validate_app_token
    def post(self, app_model, end_user=None):
        payload = request.get_json(force=True, silent=True) or {}
        setting = payload.get("retrieval_setting") or {}
        service = BrainGovernanceService(app_model.tenant_id)
        records = service.retrieve_memory(
            str(payload.get("query", "")),
            top_k=int(setting.get("top_k", 5)),
            score_threshold=float(setting.get("score_threshold", 0.0)),
        )
        return {"records": records}


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


from . import verification_keys  # noqa: F401
