"""Cendra brain service_api surface (Batch 5, additive package).

Most routes stay service-token authenticated like the rest of
``service_api``. The deliberate exception is the public receipt
verification-key lookup in ``verification_keys.py``: exported receipts
carry only ``key_id``, so third-party verification must be able to
resolve the historical public key without a tenant-scoped token.

- ``POST /v1/brain/retrieval`` — T6 external-knowledge loopback in
  Dify's External Knowledge Base API shape.
- ``GET/PUT /v1/brain/gate-posture`` — explicit per-tenant observe
  posture state + write surface (CEN-31).
- ``GET /v1/brain/gate-posture/audit`` — append-only posture audit log.
- ``GET /v1/brain/trust-meter/<property_id>`` — TrustMeter view.
- ``GET/POST /v1/brain/policies/<owner_id>`` — owner-policy documents.
- ``GET /v1/brain/cases`` — captured DecisionCases (audit).
- ``GET /v1/brain/cases/metrics`` — bounded per-tenant accrual metrics.
- ``GET /v1/brain/verification-keys/<key_id>`` — public verification-key lookup.
- ``GET /v1/brain/verification-keys`` — tenant-authenticated verification-key inventory.
"""

from datetime import date, datetime

from flask import request
from flask_restx import Resource
from pydantic import BaseModel, ConfigDict, Field, model_validator
from werkzeug.exceptions import BadRequest, Conflict

from controllers.common.schema import (
    query_params_from_model,
    query_params_from_request,
    register_response_schema_models,
)
from controllers.service_api import service_api_ns
from controllers.service_api.wraps import validate_and_get_api_token, validate_app_token
from core.brain.policy.errors import OwnerPolicyCompileError
from fields.base import ResponseModel
from libs.helper import dump_response
from services.brain_gate_posture_service import (
    BrainGatePostureService,
    ObserveOnlyGatePostureWriteError,
)
from services.brain_governance_service import BrainGovernanceService

_MAX_METRICS_WINDOW_DAYS = 90


class BrainCaseMetricsQuery(BaseModel):
    date_from: date = Field(description="Inclusive UTC day at the start of the aggregation window.")
    date_to: date = Field(description="Inclusive UTC day at the end of the aggregation window.")
    workflow: str | None = Field(
        default=None,
        description="Optional workflow kind or raw tool/event alias to scope metrics to one automation.",
    )

    @model_validator(mode="after")
    def validate_window(self) -> "BrainCaseMetricsQuery":
        if self.date_to < self.date_from:
            raise ValueError("date_to must be on or after date_from")
        if (self.date_to - self.date_from).days + 1 > _MAX_METRICS_WINDOW_DAYS:
            raise ValueError(f"date range must be {_MAX_METRICS_WINDOW_DAYS} days or fewer")
        return self


class BrainCaseMetricsVerdictCountsResponse(ResponseModel):
    would_act: int
    would_abstain: int
    unknown: int


class BrainCaseMetricsCaptureIntegrityResponse(ResponseModel):
    captured_count: int
    dispatched_count: int
    capture_rate: float | None


class BrainCaseMetricsWorkflowCalibrationResponse(ResponseModel):
    sample_size: int
    covered: bool


class BrainCaseMetricsCalibrationWindowResponse(ResponseModel):
    window_size: int
    min_samples: int
    active_workflow_count: int
    covered_workflow_count: int
    coverage_rate: float | None


class BrainCaseMetricsDayResponse(ResponseModel):
    date: date
    captured_count: int
    dispatched_count: int
    verdict_counts: BrainCaseMetricsVerdictCountsResponse


class BrainCaseMetricsWorkflowResponse(ResponseModel):
    workflow: str
    label: str
    captured_count: int
    dispatched_count: int
    verdict_counts: BrainCaseMetricsVerdictCountsResponse
    calibration_window: BrainCaseMetricsWorkflowCalibrationResponse
    latest_case_at: datetime | None
    latest_dispatch_at: datetime | None


class BrainCaseMetricsResponse(ResponseModel):
    date_from: date
    date_to: date
    generated_at: datetime
    capture_integrity: BrainCaseMetricsCaptureIntegrityResponse
    calibration_window: BrainCaseMetricsCalibrationWindowResponse
    by_day: list[BrainCaseMetricsDayResponse]
    by_workflow: list[BrainCaseMetricsWorkflowResponse]
    by_verdict: BrainCaseMetricsVerdictCountsResponse


class BrainGatePostureResolutionResponse(ResponseModel):
    configured_mode: str
    effective_mode: str
    tenant_enabled: bool
    override_mode: str | None
    source: str
    active: bool


class BrainGatePostureResponse(ResponseModel):
    tenant_id: str
    override_posture: str | None
    changed_at: datetime | None
    changed_by: str | None
    reason: str | None
    resolution: BrainGatePostureResolutionResponse


class BrainGatePostureAuditEntryResponse(ResponseModel):
    actor_type: str
    actor_id: str | None
    changed_by: str
    prior_posture: str
    new_posture: str
    prior_effective_posture: str
    new_effective_posture: str
    changed_at: datetime
    reason: str


class BrainGatePostureAuditResponse(ResponseModel):
    records: list[BrainGatePostureAuditEntryResponse]


class BrainGatePostureMutationPayload(BaseModel):
    posture: str = Field(description="Requested explicit tenant posture: off or observe.")
    reason: str = Field(min_length=1, max_length=255, description="Free-text operator reason for the posture change.")

    model_config = ConfigDict(extra="forbid")


class BrainGatePostureAuditQuery(BaseModel):
    limit: int = Field(default=50, ge=1, le=200, description="Maximum number of most-recent audit rows to return.")


register_response_schema_models(
    service_api_ns,
    BrainCaseMetricsResponse,
    BrainGatePostureResponse,
    BrainGatePostureAuditResponse,
)


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


@service_api_ns.route("/brain/gate-posture")
class BrainGatePostureApi(Resource):
    @service_api_ns.response(
        200,
        "Brain gate posture retrieved successfully",
        service_api_ns.models[BrainGatePostureResponse.__name__],
    )
    @validate_app_token
    def get(self, app_model, end_user=None):
        posture = BrainGatePostureService(app_model.tenant_id).get_posture()
        return dump_response(BrainGatePostureResponse, posture), 200

    @service_api_ns.response(
        200,
        "Brain gate posture updated successfully",
        service_api_ns.models[BrainGatePostureResponse.__name__],
    )
    @validate_app_token
    def put(self, app_model, end_user=None):
        try:
            payload = BrainGatePostureMutationPayload.model_validate(request.get_json(force=True, silent=True) or {})
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc

        api_token = validate_and_get_api_token("app")
        try:
            posture = BrainGatePostureService(app_model.tenant_id).set_posture(
                posture=payload.posture,
                reason=payload.reason,
                actor_kind="api_key",
                actor_id=str(api_token.id),
            )
        except ObserveOnlyGatePostureWriteError as exc:
            raise Conflict(str(exc)) from exc
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        return dump_response(BrainGatePostureResponse, posture), 200


@service_api_ns.route("/brain/gate-posture/audit")
class BrainGatePostureAuditApi(Resource):
    @service_api_ns.doc(params=query_params_from_model(BrainGatePostureAuditQuery))
    @service_api_ns.response(
        200,
        "Brain gate posture audit retrieved successfully",
        service_api_ns.models[BrainGatePostureAuditResponse.__name__],
    )
    @validate_app_token
    def get(self, app_model, end_user=None):
        query = query_params_from_request(BrainGatePostureAuditQuery)
        records = BrainGatePostureService(app_model.tenant_id).list_audit(limit=query.limit)
        return dump_response(BrainGatePostureAuditResponse, {"records": records}), 200


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


@service_api_ns.route("/brain/cases/metrics")
class BrainCasesMetricsApi(Resource):
    """Bounded per-tenant ledger accrual aggregates for product surfaces."""

    @service_api_ns.doc(params=query_params_from_model(BrainCaseMetricsQuery))
    @service_api_ns.response(
        200,
        "Brain case metrics retrieved successfully",
        service_api_ns.models[BrainCaseMetricsResponse.__name__],
    )
    @validate_app_token
    def get(self, app_model, end_user=None):
        query = query_params_from_request(BrainCaseMetricsQuery)
        metrics = BrainGovernanceService(app_model.tenant_id).case_metrics(
            date_from=query.date_from,
            date_to=query.date_to,
            workflow=query.workflow,
        )
        return dump_response(BrainCaseMetricsResponse, metrics), 200


from . import verification_keys  # noqa: F401
