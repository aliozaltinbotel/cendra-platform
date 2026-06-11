"""Controller contract for the brain verification-key publication endpoints."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask

import controllers.service_api.brain.verification_keys as verification_keys_api

TENANT = "11111111-1111-1111-1111-111111111111"


def unwrap(func):
    return inspect.unwrap(func)


def test_public_verification_key_lookup_returns_public_shape_without_auth(app: Flask, monkeypatch) -> None:
    service = Mock()
    service.get_verification_key.return_value = {
        "key_id": "brk_ed25519_01976f88-b5d3-7c8a-b4ec-8d7db2d8e9a5",
        "algorithm": "Ed25519",
        "public_key_base64url": "ZHVtbXkta2V5",
        "status": "active",
        "activated_at": "2026-06-11T10:00:00Z",
        "retired_at": None,
    }
    service_cls = Mock(return_value=service)
    monkeypatch.setattr(verification_keys_api, "BrainSigningKeyService", service_cls)

    with app.test_request_context("/brain/verification-keys/brk_ed25519_01976f88-b5d3-7c8a-b4ec-8d7db2d8e9a5"):
        api = verification_keys_api.BrainVerificationKeyApi()
        response, status = api.get("brk_ed25519_01976f88-b5d3-7c8a-b4ec-8d7db2d8e9a5")

    assert status == 200
    assert response["algorithm"] == "Ed25519"
    service.get_verification_key.assert_called_once_with("brk_ed25519_01976f88-b5d3-7c8a-b4ec-8d7db2d8e9a5")


def test_public_verification_key_lookup_returns_not_found(app: Flask, monkeypatch) -> None:
    service = Mock()
    service.get_verification_key.return_value = None
    monkeypatch.setattr(verification_keys_api, "BrainSigningKeyService", Mock(return_value=service))

    with app.test_request_context("/brain/verification-keys/missing-key"):
        api = verification_keys_api.BrainVerificationKeyApi()
        response, status = api.get("missing-key")

    assert status == 404
    assert response == {"message": "not found"}


def test_inventory_route_uses_app_tenant_and_include_retired_flag(app: Flask, monkeypatch) -> None:
    service = Mock()
    service.list_verification_keys.return_value = [
        {
            "key_id": "brk_ed25519_01976f88-b5d3-7c8a-b4ec-8d7db2d8e9a5",
            "algorithm": "Ed25519",
            "public_key_base64url": "ZHVtbXkta2V5",
            "status": "active",
            "activated_at": "2026-06-11T10:00:00Z",
            "retired_at": None,
        }
    ]
    service_cls = Mock(return_value=service)
    monkeypatch.setattr(verification_keys_api, "BrainSigningKeyService", service_cls)

    with app.test_request_context("/brain/verification-keys?include_retired=true", method="GET"):
        api = verification_keys_api.BrainVerificationKeysApi()
        response, status = unwrap(api.get)(api, app_model=SimpleNamespace(tenant_id=TENANT))

    assert status == 200
    assert response["keys"][0]["status"] == "active"
    service_cls.assert_called_once_with()
    service.list_verification_keys.assert_called_once_with(TENANT, include_retired=True)
