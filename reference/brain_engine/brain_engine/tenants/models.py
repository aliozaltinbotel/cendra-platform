"""Value objects for the Phase 3 tenant-resolution layer.

A :class:`TenantContext` captures *who owns this request*: which
customer, which Cendra workspace, and which property-management
system feeds the data.  The middleware populates one
:class:`TenantContext` per HTTP request and stores it in a
:mod:`contextvars` ContextVar so every downstream service (loaders,
readers, harvester) can read the active tenant without threading it
through call signatures.

The object is intentionally frozen + slotted: any service that needs
*different* tenant scope must build a new context rather than mutate
a shared instance.  This rules out a whole class of cross-request
contamination bugs at the type level.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "TENANT_SOURCE_BOOTSTRAP",
    "TENANT_SOURCE_ENV_DEFAULT",
    "TENANT_SOURCE_LAZY",
    "TENANT_SOURCE_MANUAL",
    "TENANT_SOURCE_REGISTRY",
    "TENANT_SOURCE_REQUEST_BODY",
    "TENANT_SOURCE_SYNC",
    "TenantContext",
]


# The ``source`` field on :class:`TenantContext` is unified across
# two concerns: the **origin** of a registry row (what kind of code
# path put the mapping into Postgres) and the **resolution path**
# the live request took.  Both produce a single string label that
# downstream observability filters can pivot on:
#
#   * ``request_body`` — Phase 1 operator override carried inline.
#   * ``registry``     — middleware found the row in Postgres.
#   * ``lazy``         — live GraphQL probe discovered the property
#                        and persisted it on the spot.
#   * ``env_default``  — middleware fell through to the pod-level
#                        env vars (registry gap signal).
#   * ``bootstrap``    — Postgres row written by a successful
#                        ``/bootstrap/property/{id}`` run.
#   * ``sync``         — Postgres row written by the nightly sync.
#   * ``manual``       — Postgres row written by operator backfill.
#
# Note that ``registry`` is exclusively a resolution-time label —
# it is never written into the Postgres ``source`` column because
# the SQL constraint only allows ``{bootstrap, sync, lazy,
# manual}``.  The resolver normalises every registry hit to
# ``TENANT_SOURCE_REGISTRY`` regardless of the row's stored origin
# so observability has a single label for the hot path.


#: Tenant came from an explicit ``customer_id`` field in the
#: incoming request body (Phase 1 behaviour — operator override).
TENANT_SOURCE_REQUEST_BODY: Final[str] = "request_body"

#: Tenant was resolved via Postgres ``property_tenant_registry``
#: lookup (the hot path once a property is known).
TENANT_SOURCE_REGISTRY: Final[str] = "registry"

#: Tenant was discovered at request time via a live GraphQL probe
#: against the unified-data gateway and persisted back to the
#: registry for subsequent hits.
TENANT_SOURCE_LAZY: Final[str] = "lazy"

#: No tenant could be resolved — middleware fell back to the
#: pod-level environment defaults.  Operators should treat this as
#: a misconfiguration signal (a request hit the brain for a
#: property that nobody has ever bootstrapped).
TENANT_SOURCE_ENV_DEFAULT: Final[str] = "env_default"

#: Postgres row written by a successful bootstrap run (operator
#: kicked off ``/bootstrap/property/{id}`` with explicit tenant
#: fields).  Matches the SQL CHECK enum.
TENANT_SOURCE_BOOTSTRAP: Final[str] = "bootstrap"

#: Postgres row written by the nightly sync cron.  Matches the SQL
#: CHECK enum.
TENANT_SOURCE_SYNC: Final[str] = "sync"

#: Postgres row written by operator backfill (one-shot migration
#: script seeding from existing ``decision_cases``).  Matches the
#: SQL CHECK enum.
TENANT_SOURCE_MANUAL: Final[str] = "manual"


_ALLOWED_SOURCES: Final[frozenset[str]] = frozenset(
    {
        TENANT_SOURCE_REQUEST_BODY,
        TENANT_SOURCE_REGISTRY,
        TENANT_SOURCE_LAZY,
        TENANT_SOURCE_ENV_DEFAULT,
        TENANT_SOURCE_BOOTSTRAP,
        TENANT_SOURCE_SYNC,
        TENANT_SOURCE_MANUAL,
    }
)


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Per-request tenant identity.

    Attributes:
        customer_id: Cendra customer UUID (e.g. ``"ec9013b9-..."``).
            Empty string is reserved for the legacy "drop the
            filter" semantics inherited from Phase 1 — never
            ``None``.
        org_id: Optional Cendra workspace UUID.  ``None`` means
            "this property is not yet plumbed to a workspace, do
            not include ``orgId`` in GraphQL filters".
        provider_type: Upper-case PMS identifier
            (``"HOSTAWAY"``, ``"LODGIFY"``, ``"GUESTY"`` …).
        property_channel_id: Short channel id used as the
            registry primary key.
        source: One of :data:`TENANT_SOURCE_*` — records how the
            middleware reached this context so observability can
            audit registry quality.
    """

    customer_id: str
    org_id: str | None
    provider_type: str
    property_channel_id: str
    source: str

    def __post_init__(self) -> None:
        if self.source not in _ALLOWED_SOURCES:
            allowed = ", ".join(sorted(_ALLOWED_SOURCES))
            raise ValueError(
                f"TenantContext.source={self.source!r} not in "
                f"{{{allowed}}}",
            )
