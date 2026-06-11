"""Tests for the TemplateProvenanceService Hub seam (CEN-43 / P3)."""

from dataclasses import dataclass

import pytest

from core.brain.patterns.template_provenance import (
    PROVENANCE_KEY,
    PromotedTemplateNotAllowedError,
    TemplateProvenance,
)
from services.brain_template_provenance_service import TemplateProvenanceService


@dataclass
class _FakeInstalledApp:
    app_id: str


class TestResolution:
    def test_template_defaults_to_starter_at_g2(self):
        assert (
            TemplateProvenanceService.provenance_for_template("any-app-id")
            is TemplateProvenance.STARTER
        )

    def test_none_app_id_is_starter(self):
        assert TemplateProvenanceService.provenance_for_template(None) is TemplateProvenance.STARTER

    def test_installed_app_inherits_source_template_provenance(self):
        # Acceptance #2: resolved via the InstalledApp.app_id → source linkage.
        app = _FakeInstalledApp(app_id="tmpl-123")
        assert (
            TemplateProvenanceService.provenance_for_installed_app(app)
            is TemplateProvenance.STARTER
        )

    def test_installed_app_accepts_raw_app_id(self):
        assert (
            TemplateProvenanceService.provenance_for_installed_app("tmpl-123")
            is TemplateProvenance.STARTER
        )


class TestAnnotation:
    def test_annotates_each_recommended_app(self):
        payload = {
            "recommended_apps": [{"app_id": "a"}, {"app_id": "b"}],
            "categories": ["Agent"],
        }
        out = TemplateProvenanceService.annotate_recommended_apps(payload)
        assert [app[PROVENANCE_KEY] for app in out["recommended_apps"]] == ["starter", "starter"]
        assert out["categories"] == ["Agent"]

    def test_annotation_is_non_mutating(self):
        payload = {"recommended_apps": [{"app_id": "a"}], "categories": []}
        TemplateProvenanceService.annotate_recommended_apps(payload)
        assert PROVENANCE_KEY not in payload["recommended_apps"][0]

    def test_empty_payload_passthrough(self):
        payload = {"recommended_apps": [], "categories": []}
        assert TemplateProvenanceService.annotate_recommended_apps(payload) == payload

    def test_annotate_template_detail(self):
        out = TemplateProvenanceService.annotate_template_detail({"id": "x", "name": "n"})
        assert out[PROVENANCE_KEY] == "starter"

    def test_annotate_template_detail_none(self):
        assert TemplateProvenanceService.annotate_template_detail(None) is None


class TestCurationGuard:
    def test_starter_registration_allowed(self):
        assert (
            TemplateProvenanceService.register_curation_provenance("starter")
            is TemplateProvenance.STARTER
        )

    def test_promoted_registration_rejected(self):
        # Acceptance #3: curation cannot create a promoted template at G2.
        with pytest.raises(PromotedTemplateNotAllowedError):
            TemplateProvenanceService.register_curation_provenance("promoted")
