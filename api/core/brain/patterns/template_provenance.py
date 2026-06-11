"""Template-provenance field shape for Automation Hub templates (CEN-43 / P3).

Additive kernel module: it carries no touchpoint marker (those tag edited
upstream lines inside registered touchpoints only).  Nothing here imports
from ``core.workflow``, ``core.app``, ``core.agent`` or ``controllers`` —
it is a pure value object + a handful of dict helpers, so the Automation
Hub wrapper (a Cendra-owned controller, Pixel's lane) and the curation
tooling (Packs' lane) can both consume it without reaching into the kernel.

**Why this exists (PRD §5.2 / §5.3, copy-rule C3).**  The Automation Hub
wraps Dify Explore (Ruling Q4) and must label every hand-crafted template
**"Starter template — not Cendra intelligence"** at browse, preview, and
instantiation.  P3 makes that distinction *structural* rather than
copy-only: a template carries a first-class ``provenance`` of
:data:`TemplateProvenance.STARTER` or :data:`TemplateProvenance.PROMOTED`,
so the labeling rule keys off data and the future Suggested-Automations
surface (§5.3, roadmap #8) keys off the same field rather than a curation
convention.

**Observe posture only.**  This module defines and reads a marker; it
enables nothing.  At G2 *every* template is ``starter`` — ``promoted`` is
a valid enum value but the pattern-mining → DSL → ``difyctl`` promotion
path (#8) that would produce it is unbuilt.  :func:`guard_curation_provenance`
makes that guarantee enforceable: curation tooling may only assign
``starter`` until #8 ships, so no ``promoted`` template can be created at
G2 (acceptance #3).

**No migration.**  Provenance is resolved/annotated onto the template
payload dicts the Hub wrapper already passes around (the buildin
``recommended_apps`` shape), and onto instantiated apps via their existing
source-template ``app_id`` linkage.  Missing/legacy values read as
``starter`` (:func:`coerce_provenance`), so pre-change data stays valid.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

# Key under which provenance is surfaced on a template / recommended-app dict.
PROVENANCE_KEY = "provenance"


class TemplateProvenance(StrEnum):
    """Where an Automation Hub template came from.

    ``STARTER`` — hand-crafted by the hospitality team (Packs' catalog).
    Cloneable starting point, *not* Cendra intelligence; carries the
    binding §5.2 label.  Every template at launch is ``STARTER``.

    ``PROMOTED`` — mined from a tenant's own ledger and promoted via the
    #8 path (§5.3).  Roadmap-only: the schema admits it, but the
    promotion path is unbuilt, so nothing produces it at G2.
    """

    STARTER = "starter"
    PROMOTED = "promoted"


#: The provenance every template carries at launch and the value any
#: missing/legacy marker resolves to.
DEFAULT_PROVENANCE = TemplateProvenance.STARTER


class PromotedTemplateNotAllowedError(ValueError):
    """Raised when curation tooling tries to create a ``promoted`` template.

    The promotion path (#8) is unbuilt at G2, so ``promoted`` may not be
    assigned by curation.  This guards acceptance criterion #3.
    """


def coerce_provenance(value: Any) -> TemplateProvenance:
    """Parse an arbitrary value into a :class:`TemplateProvenance`.

    ``None``, unknown strings, and missing markers resolve to
    :data:`DEFAULT_PROVENANCE` (``starter``) so legacy/un-annotated
    templates read as starter rather than raising.
    """
    if isinstance(value, TemplateProvenance):
        return value
    if isinstance(value, str):
        try:
            return TemplateProvenance(value.strip().lower())
        except ValueError:
            return DEFAULT_PROVENANCE
    return DEFAULT_PROVENANCE


def guard_curation_provenance(value: Any) -> TemplateProvenance:
    """Validate provenance assigned by curation tooling at G2.

    Returns the coerced :class:`TemplateProvenance` when it is ``starter``;
    raises :class:`PromotedTemplateNotAllowedError` for any value that
    resolves to ``promoted``.  Use this at every seam where a tool sets a
    template's provenance — it keeps the unbuilt promotion path from being
    reached by convention (acceptance #3).
    """
    # Resolve explicitly (do not silently downgrade a literal "promoted").
    if isinstance(value, TemplateProvenance):
        resolved = value
    elif isinstance(value, str) and value.strip().lower() == TemplateProvenance.PROMOTED.value:
        resolved = TemplateProvenance.PROMOTED
    else:
        resolved = coerce_provenance(value)

    if resolved is TemplateProvenance.PROMOTED:
        raise PromotedTemplateNotAllowedError(
            "promoted templates require the #8 promotion path, which is unbuilt at G2; "
            "curation tooling may only assign 'starter'."
        )
    return resolved


def provenance_of(template: dict[str, Any]) -> TemplateProvenance:
    """Read the provenance of a template/recommended-app dict.

    Returns :data:`DEFAULT_PROVENANCE` when the marker is absent.
    """
    return coerce_provenance(template.get(PROVENANCE_KEY))


def annotate_template(
    template: dict[str, Any],
    provenance: TemplateProvenance | str = DEFAULT_PROVENANCE,
) -> dict[str, Any]:
    """Return ``template`` with its :data:`PROVENANCE_KEY` set.

    Idempotent and non-mutating: a shallow copy is returned with
    ``provenance`` stamped as a plain string (JSON-friendly for the Hub
    wrapper payload).  An already-present marker is preserved unless an
    explicit ``provenance`` other than the default is passed.
    """
    existing = template.get(PROVENANCE_KEY)
    if existing is not None and provenance == DEFAULT_PROVENANCE:
        resolved = coerce_provenance(existing)
    else:
        resolved = coerce_provenance(provenance)
    return {**template, PROVENANCE_KEY: resolved.value}
