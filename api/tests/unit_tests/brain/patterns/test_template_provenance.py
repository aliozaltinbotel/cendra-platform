"""Tests for the template-provenance kernel field shape (CEN-43 / P3)."""

import pytest

from core.brain.patterns.template_provenance import (
    DEFAULT_PROVENANCE,
    PROVENANCE_KEY,
    PromotedTemplateNotAllowedError,
    TemplateProvenance,
    annotate_template,
    coerce_provenance,
    guard_curation_provenance,
    provenance_of,
)


class TestCoerceProvenance:
    def test_starter_and_promoted_strings(self):
        assert coerce_provenance("starter") is TemplateProvenance.STARTER
        assert coerce_provenance("promoted") is TemplateProvenance.PROMOTED

    def test_enum_passthrough(self):
        assert coerce_provenance(TemplateProvenance.PROMOTED) is TemplateProvenance.PROMOTED

    def test_case_and_whitespace_insensitive(self):
        assert coerce_provenance("  STARTER ") is TemplateProvenance.STARTER

    @pytest.mark.parametrize("value", [None, "", "bogus", 7, object()])
    def test_unknown_resolves_to_default_starter(self, value):
        assert coerce_provenance(value) is DEFAULT_PROVENANCE
        assert DEFAULT_PROVENANCE is TemplateProvenance.STARTER


class TestGuardCurationProvenance:
    def test_starter_allowed(self):
        assert guard_curation_provenance("starter") is TemplateProvenance.STARTER

    def test_default_allowed(self):
        # Missing/legacy values resolve to starter and pass the guard.
        assert guard_curation_provenance(None) is TemplateProvenance.STARTER

    def test_promoted_string_rejected(self):
        with pytest.raises(PromotedTemplateNotAllowedError):
            guard_curation_provenance("promoted")

    def test_promoted_enum_rejected(self):
        with pytest.raises(PromotedTemplateNotAllowedError):
            guard_curation_provenance(TemplateProvenance.PROMOTED)

    def test_promoted_mixed_case_rejected(self):
        with pytest.raises(PromotedTemplateNotAllowedError):
            guard_curation_provenance("Promoted")


class TestAnnotateAndRead:
    def test_annotate_default_is_starter(self):
        out = annotate_template({"app_id": "x"})
        assert out[PROVENANCE_KEY] == "starter"
        assert provenance_of(out) is TemplateProvenance.STARTER

    def test_annotate_is_non_mutating(self):
        src = {"app_id": "x"}
        annotate_template(src)
        assert PROVENANCE_KEY not in src

    def test_annotate_preserves_existing_when_default_passed(self):
        out = annotate_template({"app_id": "x", PROVENANCE_KEY: "promoted"})
        assert out[PROVENANCE_KEY] == "promoted"

    def test_annotate_explicit_overrides(self):
        out = annotate_template({"app_id": "x"}, TemplateProvenance.PROMOTED)
        assert out[PROVENANCE_KEY] == "promoted"

    def test_provenance_of_missing_is_starter(self):
        assert provenance_of({"app_id": "x"}) is TemplateProvenance.STARTER
