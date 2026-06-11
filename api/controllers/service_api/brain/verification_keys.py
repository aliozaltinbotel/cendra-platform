"""Verification-key publication routes for signed receipts.

The public ``GET /v1/brain/verification-keys/<key_id>`` route is
intentionally unauthenticated. Exported receipts only carry ``key_id``,
so third-party verification must be able to fetch the historical public
key without a tenant-scoped token. The route publishes only public key
material; private signing bytes never leave the custody layer.
"""

from __future__ import annotations

from flask_restx import Resource
from pydantic import BaseModel, Field

from controllers.common.schema import (
    query_params_from_model,
    query_params_from_request,
    register_response_schema_models,
)
from controllers.service_api import service_api_ns
from controllers.service_api.wraps import validate_app_token
from fields.base import ResponseModel
from libs.helper import dump_response
from services.brain_signing_key_service import BrainSigningKeyService


class BrainVerificationKeyInventoryQuery(BaseModel):
    include_retired: bool = Field(
        default=False,
        description="Include retired historical keys for the caller tenant.",
    )


class BrainVerificationKeyResponse(ResponseModel):
    key_id: str
    algorithm: str
    public_key_base64url: str
    status: str
    activated_at: str
    retired_at: str | None


class BrainVerificationKeyListResponse(ResponseModel):
    keys: list[BrainVerificationKeyResponse]


register_response_schema_models(
    service_api_ns,
    BrainVerificationKeyResponse,
    BrainVerificationKeyListResponse,
)


@service_api_ns.route("/brain/verification-keys/<string:key_id>")
class BrainVerificationKeyApi(Resource):
    """Public verification-key lookup by immutable ``key_id``."""

    @service_api_ns.response(
        200,
        "Verification key retrieved successfully",
        service_api_ns.models[BrainVerificationKeyResponse.__name__],
    )
    def get(self, key_id: str):
        key = BrainSigningKeyService().get_verification_key(key_id)
        if key is None:
            return {"message": "not found"}, 404
        return dump_response(BrainVerificationKeyResponse, key), 200


@service_api_ns.route("/brain/verification-keys")
class BrainVerificationKeysApi(Resource):
    """Tenant-authenticated inventory of published verification keys."""

    @service_api_ns.doc(params=query_params_from_model(BrainVerificationKeyInventoryQuery))
    @service_api_ns.response(
        200,
        "Verification keys retrieved successfully",
        service_api_ns.models[BrainVerificationKeyListResponse.__name__],
    )
    @validate_app_token
    def get(self, app_model, end_user=None):
        query = query_params_from_request(BrainVerificationKeyInventoryQuery)
        keys = BrainSigningKeyService().list_verification_keys(
            app_model.tenant_id,
            include_retired=query.include_retired,
        )
        return dump_response(BrainVerificationKeyListResponse, {"keys": keys}), 200
