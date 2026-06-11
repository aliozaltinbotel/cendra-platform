"""Console brain reads — tenant-scoped, console-session-authenticated.

CEN-47 (B3): the operator console must reach the brain trust-meter and
case audit over a *console session*, never a service-API app token.  The
security-relevant contract these tests pin down is twofold:

1. the controllers carry the console auth stack (``setup_required`` /
   ``login_required`` / ``account_initialization_required``); and
2. the service facade is scoped to ``current_user.current_tenant_id`` —
   the logged-in workspace — so one tenant can never read another's reads.

The decorators are exercised centrally in ``test_wraps.py``; here we
unwrap them (matching ``test_feature.py``) and assert the delegation +
tenant scoping that the gate exists to guarantee.
"""

import pytest
from flask import Flask
from pytest_mock import MockerFixture


@pytest.fixture
def app():
    app = Flask(__name__)
    app.testing = True
    return app


def unwrap(func):
    """Recursively unwrap decorated functions (see test_feature.py)."""
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    return func


def _patch_user(mocker: MockerFixture, tenant_id: str = "tenant_xyz"):
    fake_user = mocker.Mock()
    fake_user.current_tenant_id = tenant_id
    mocker.patch("controllers.console.brain.current_user", fake_user)
    return fake_user


class TestConsoleBrainTrustMeterApi:
    def test_get_is_tenant_scoped_and_delegates(self, mocker: MockerFixture):
        from controllers.console.brain import ConsoleBrainTrustMeterApi

        _patch_user(mocker, "tenant_xyz")
        service_cls = mocker.patch("controllers.console.brain.BrainGovernanceService")
        service_cls.return_value.trust_meter.return_value = {"property_id": "prop-123", "bands": []}

        raw_get = unwrap(ConsoleBrainTrustMeterApi.get)
        result = raw_get(ConsoleBrainTrustMeterApi(), "prop-123")

        assert result == {"property_id": "prop-123", "bands": []}
        # tenant comes from the console session, not a request param
        service_cls.assert_called_once_with("tenant_xyz")
        service_cls.return_value.trust_meter.assert_called_once_with("prop-123")


class TestConsoleBrainCasesApi:
    def test_get_clamps_limit_and_passes_filters(self, app, mocker: MockerFixture):
        from controllers.console.brain import ConsoleBrainCasesApi

        _patch_user(mocker, "tenant_xyz")
        service_cls = mocker.patch("controllers.console.brain.BrainGovernanceService")
        service_cls.return_value.list_cases.return_value = [{"case_id": "c1"}]

        raw_get = unwrap(ConsoleBrainCasesApi.get)
        # over-large limit must be clamped to 200; filters/offset pass through
        with app.test_request_context("/?property_id=prop-9&limit=500&offset=10"):
            result = raw_get(ConsoleBrainCasesApi())

        assert result == {"cases": [{"case_id": "c1"}]}
        service_cls.assert_called_once_with("tenant_xyz")
        service_cls.return_value.list_cases.assert_called_once_with(
            property_id="prop-9", limit=200, offset=10
        )

    def test_get_defaults_when_no_query_params(self, app, mocker: MockerFixture):
        from controllers.console.brain import ConsoleBrainCasesApi

        _patch_user(mocker, "tenant_xyz")
        service_cls = mocker.patch("controllers.console.brain.BrainGovernanceService")
        service_cls.return_value.list_cases.return_value = []

        raw_get = unwrap(ConsoleBrainCasesApi.get)
        with app.test_request_context("/"):
            result = raw_get(ConsoleBrainCasesApi())

        assert result == {"cases": []}
        service_cls.return_value.list_cases.assert_called_once_with(
            property_id=None, limit=50, offset=0
        )
