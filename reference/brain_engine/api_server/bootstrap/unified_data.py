"""Lifespan wiring for the Cendra Unified Data GraphQL client.

The :class:`UnifiedDataGraphQLClient` reaches the Cendra
``onboarding-api`` GraphQL gateway and powers three downstream
sections in lifespan that share the same client + workspace
identifiers:

* the narrative ``UnifiedReservationsTimelineSource``,
* the conversation archive loader (``GraphQLConversationArchiveLoader``),
* the profile harvester invocation.

Originally the client was built inline inside the narrative
section, which meant the same env-var trio
(``UNIFIED_DATA_CUSTOMER_ID``, ``UNIFIED_DATA_ORG_ID``,
``UNIFIED_DATA_PROVIDER_TYPE``) was read once but referenced from
three different places by local-variable name.  Hoisting the
construction into its own bootstrap section lets every consumer
read a single, attribute-named result and keeps the narrative
section focused on assembling its source list.

The wire entry point is synchronous because
:class:`UnifiedDataGraphQLClient` only allocates an
:class:`httpx.AsyncClient` at construction (no network I/O until
the first request).  The shutdown contract still lives in
``server.lifespan``: ``await client.aclose()`` releases the
underlying connection pool there.

Activation contract:

* When ``UNIFIED_DATA_CUSTOMER_ID`` is empty, the client stays
  ``None`` — the unified gateway is opt-in per-environment.
* Any construction error is logged and swallowed so the narrative,
  archive loader, and profile harvester continue with their
  in-house sources rather than aborting startup.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from brain_engine.integrations.unified_data import UnifiedDataGraphQLClient

logger = logging.getLogger(__name__)


def wire(
    application: FastAPI,
) -> tuple[
    UnifiedDataGraphQLClient | None,
    str,
    str | None,
    str | None,
]:
    """Build the Unified Data client and resolve workspace identifiers.

    On success ``application.state.unified_data_client`` is
    populated so that future readers migrated off the module
    global can resolve it through the FastAPI request lifecycle.

    Args:
        application: The FastAPI app whose ``state`` is the
            canonical home for the constructed client.

    Returns:
        A 4-tuple ``(client, customer_id, org_id, provider_type)``:

        * ``client`` — the GraphQL client, or ``None`` when
          ``UNIFIED_DATA_CUSTOMER_ID`` is empty or construction
          raised.
        * ``customer_id`` — the configured Cendra customer id, or
          empty string when the gateway is disabled.  Downstream
          consumers gate on truthiness, so an empty string is the
          stable "off" sentinel.
        * ``org_id`` / ``provider_type`` — narrowing scopes; both
          are ``None`` when the corresponding env var is unset.

        ``client.aclose()`` must be awaited on shutdown to release
        the underlying httpx pool — that teardown stays in
        ``server.lifespan`` for now.
    """
    customer_id = os.getenv("UNIFIED_DATA_CUSTOMER_ID", "").strip()
    org_id = os.getenv("UNIFIED_DATA_ORG_ID", "").strip() or None
    provider_type = (
        os.getenv("UNIFIED_DATA_PROVIDER_TYPE", "").strip() or None
    )

    if not customer_id:
        return None, "", org_id, provider_type

    try:
        base_url = os.getenv("UNIFIED_DATA_BASE_URL", "").strip()
        client_kwargs: dict[str, str] = {}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = UnifiedDataGraphQLClient(**client_kwargs)
    except Exception as exc:  # noqa: BLE001 — optional adapter
        logger.warning(
            "UnifiedDataGraphQLClient init skipped: %s (%s)",
            exc,
            type(exc).__name__,
        )
        return None, customer_id, org_id, provider_type

    application.state.unified_data_client = client
    logger.info(
        "UnifiedDataGraphQLClient initialized "
        "(customer=%s, org=%s, provider=%s)",
        customer_id,
        org_id or "—",
        provider_type or "—",
    )
    return client, customer_id, org_id, provider_type
