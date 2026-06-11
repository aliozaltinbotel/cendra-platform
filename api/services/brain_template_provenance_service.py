"""Service seam that surfaces template provenance to the Automation Hub.

CEN-43 / Platform Ask P3.  The kernel value object lives in
``core.brain.patterns.template_provenance``; this service is the
platform-facing adapter the Hub wrapper (Pixel's lane) and curation
tooling (Packs' lane) call.  It does three things, all additive and
observe-only — no migration, no upstream-table edit, no touchpoint:

1. **Surface** ``provenance`` on the recommended-app payload the Hub
   wrapper renders (acceptance #1) — :meth:`annotate_recommended_apps`.
2. **Resolve** the provenance an instantiated app inherits from its
   source template (acceptance #2) — :meth:`provenance_for_installed_app`,
   which keys off the existing ``InstalledApp.app_id`` → source-template
   linkage rather than stamping anything at install time.
3. **Guard** curation tooling so no ``promoted`` template can be created
   at G2 (acceptance #3) — :meth:`register_curation_provenance`.

At G2 there is no promotion path (#8 unbuilt), so every template resolves
to ``starter``.  :data:`_PROMOTED_TEMPLATE_IDS` is the seam a future
promotion path writes to; it is empty and cannot be populated through
curation (the guard refuses ``promoted``).
"""

from __future__ import annotations

from typing import Any

from core.brain.patterns.template_provenance import (
    DEFAULT_PROVENANCE,
    PROVENANCE_KEY,
    TemplateProvenance,
    annotate_template,
    guard_curation_provenance,
)

# Source-template app_ids that resolve to ``promoted``.  Empty at G2 — the
# pattern-mining → promotion path (#8) is the only thing allowed to grow
# this set, and it is unbuilt.  Curation tooling cannot add to it (see
# :meth:`TemplateProvenanceService.register_curation_provenance`).
_PROMOTED_TEMPLATE_IDS: frozenset[str] = frozenset()


class TemplateProvenanceService:
    """Resolve and surface Automation Hub template provenance."""

    @classmethod
    def provenance_for_template(cls, template_app_id: str | None) -> TemplateProvenance:
        """Resolve the provenance of a source template by its app_id.

        At G2 every template is ``starter``; only ids in
        :data:`_PROMOTED_TEMPLATE_IDS` (empty) resolve to ``promoted``.
        """
        if template_app_id and template_app_id in _PROMOTED_TEMPLATE_IDS:
            return TemplateProvenance.PROMOTED
        return DEFAULT_PROVENANCE

    @classmethod
    def provenance_for_installed_app(cls, installed_app: Any) -> TemplateProvenance:
        """Provenance an instantiated app inherits from its source template.

        An installed app references its source template structurally via
        ``InstalledApp.app_id`` (the install flow copies no provenance —
        it points at the template's app).  We resolve through that
        linkage, so the reference is retained without a write at
        instantiation time (acceptance #2).  Accepts an object exposing
        ``app_id`` or a raw app_id string.
        """
        app_id = getattr(installed_app, "app_id", installed_app)
        return cls.provenance_for_template(app_id if isinstance(app_id, str) else None)

    @classmethod
    def annotate_recommended_apps(cls, result: dict[str, Any]) -> dict[str, Any]:
        """Stamp ``provenance`` onto each app in a recommended-apps payload.

        ``result`` is the ``{"recommended_apps": [...], "categories": [...]}``
        shape produced by ``RecommendedAppService``.  Returns a shallow
        copy whose every app dict carries :data:`PROVENANCE_KEY`, resolved
        from the template's ``app_id`` (default ``starter``).  The Hub
        wrapper calls this so the §5.2 label is data-driven (acceptance #1).
        Non-mutating; an absent/empty list is returned unchanged.
        """
        apps = result.get("recommended_apps")
        if not apps:
            return result
        annotated = [
            annotate_template(app, cls.provenance_for_template(app.get("app_id")))
            for app in apps
        ]
        return {**result, "recommended_apps": annotated}

    @classmethod
    def annotate_template_detail(cls, detail: dict[str, Any] | None) -> dict[str, Any] | None:
        """Stamp provenance onto a single template-detail payload.

        Mirrors :meth:`annotate_recommended_apps` for the per-template
        preview surface (acceptance #1 — labeling at *preview*).  The
        detail dict is keyed by ``id`` (the template app_id).
        """
        if not detail:
            return detail
        return annotate_template(detail, cls.provenance_for_template(detail.get("id")))

    @classmethod
    def register_curation_provenance(cls, value: Any) -> TemplateProvenance:
        """Validate provenance a curation tool wants to assign.

        Delegates to the kernel guard: returns ``starter`` (the only legal
        value at G2) or raises ``PromotedTemplateNotAllowedError`` for
        ``promoted`` (acceptance #3).
        """
        return guard_curation_provenance(value)


__all__ = ["PROVENANCE_KEY", "TemplateProvenanceService"]
