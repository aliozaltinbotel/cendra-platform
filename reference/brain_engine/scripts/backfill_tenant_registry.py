"""One-shot backfill for ``property_tenant_registry`` (Phase 3).

Seeds the registry table with every property known to the
unified-data GraphQL gateway for a single
``(customer_id, org_id, provider_type)`` tenant tuple.  After the
backfill the brain pod can auto-resolve any of those properties on
the very next request — no lazy GraphQL probe required.

Operator workflow:

  1. Apply migration ``032_property_tenant_registry.sql`` against
     the target Postgres cluster.
  2. Run this script once per known customer.  Use ``--dry-run``
     first to confirm the GraphQL query returns the expected
     property count; flip to a live write only after sampling
     the proposed inserts.
  3. Verify the row count: ``SELECT COUNT(*) FROM
     property_tenant_registry WHERE customer_id = $1``.

Selection criteria are intentionally narrow:

* ``--customer-id`` is **required** so the operator opts in per
  tenant.  No "seed every customer" mode — that would silently
  pull in tenants the operator never reviewed.
* ``--org-id`` / ``--provider-type`` are optional but recommended:
  Cendra's ``properties`` query returns sharper paging when both
  are pinned.
* Rows that already exist for the same ``property_channel_id`` are
  refreshed via ``ON CONFLICT`` — the script is idempotent.

Usage::

    .venv/bin/python scripts/backfill_tenant_registry.py \\
        --customer-id ec9013b9-… \\
        --org-id 626ee566-… \\
        --provider-type LODGIFY \\
        --database-url postgres://… \\
        --dry-run

The script intentionally lives outside ``api_server`` so it can be
shelled in from a one-off operator container without booting the
full FastAPI app.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

logger = logging.getLogger("tenant_backfill")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill property_tenant_registry from unified-data",
    )
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("--org-id", default=None)
    parser.add_argument("--provider-type", default=None)
    parser.add_argument(
        "--database-url",
        default=None,
        help="Postgres URL; falls back to DATABASE_URL env var.",
    )
    parser.add_argument(
        "--unified-data-url",
        default=None,
        help="Unified-data GraphQL endpoint; "
        "falls back to UNIFIED_DATA_GRAPHQL_URL env var.",
    )
    parser.add_argument(
        "--unified-data-token",
        default=None,
        help="Bearer token; falls back to UNIFIED_DATA_GRAPHQL_TOKEN env var.",
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


async def _list_properties(
    *,
    graphql_url: str,
    token: str | None,
    customer_id: str,
    org_id: str | None,
    provider_type: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    from brain_engine.integrations.unified_data.client import (
        UnifiedDataGraphQLClient,
    )
    from brain_engine.integrations.unified_data.readers import (
        UnifiedPropertyReader,
    )

    client = UnifiedDataGraphQLClient(base_url=graphql_url, token=token)
    try:
        reader = UnifiedPropertyReader(
            client,
            cendra_customer_id=customer_id,
            cendra_org_id=org_id,
            provider_type=provider_type,
        )
        summaries = await reader.list_summaries(limit=limit)
    finally:
        await client.aclose()
    return [
        {
            "property_channel_id": (
                getattr(summary, "channel_entity_id", None)
                or getattr(summary, "channelEntityId", None)
                or getattr(summary, "id", None)
            ),
        }
        for summary in summaries
    ]


async def _upsert_rows(
    *,
    database_url: str,
    customer_id: str,
    org_id: str | None,
    provider_type: str,
    properties: list[dict[str, Any]],
) -> int:
    import asyncpg

    from brain_engine.tenants import (
        TENANT_SOURCE_MANUAL,
        PostgresPropertyTenantRegistry,
        TenantContext,
    )

    pool = await asyncpg.create_pool(
        dsn=database_url,
        min_size=1,
        max_size=2,
        command_timeout=10,
    )
    try:
        registry = PostgresPropertyTenantRegistry(pool)
        written = 0
        for row in properties:
            channel_id = row.get("property_channel_id")
            if not channel_id:
                continue
            await registry.upsert(
                TenantContext(
                    customer_id=customer_id,
                    org_id=org_id,
                    provider_type=provider_type,
                    property_channel_id=str(channel_id),
                    source=TENANT_SOURCE_MANUAL,
                ),
            )
            written += 1
        return written
    finally:
        await pool.close()


async def _async_main(args: argparse.Namespace) -> int:
    graphql_url = (
        args.unified_data_url
        or os.environ.get("UNIFIED_DATA_GRAPHQL_URL")
    )
    token = (
        args.unified_data_token
        or os.environ.get("UNIFIED_DATA_GRAPHQL_TOKEN")
    )
    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not graphql_url:
        logger.error("UNIFIED_DATA_GRAPHQL_URL is required")
        return 2
    if not database_url and not args.dry_run:
        logger.error("DATABASE_URL is required for writes (or pass --dry-run)")
        return 2
    if not args.provider_type:
        logger.error("--provider-type is required for the registry row")
        return 2

    properties = await _list_properties(
        graphql_url=graphql_url,
        token=token,
        customer_id=args.customer_id,
        org_id=args.org_id,
        provider_type=args.provider_type,
        limit=args.limit,
    )
    logger.info(
        "Discovered %d property(ies) for customer_id=%s",
        len(properties),
        args.customer_id,
    )

    if args.dry_run:
        for row in properties[:10]:
            logger.info("  would upsert property_channel_id=%s",
                        row.get("property_channel_id"))
        if len(properties) > 10:
            logger.info("  ... and %d more", len(properties) - 10)
        logger.info("Dry-run complete; no writes issued.")
        return 0

    written = await _upsert_rows(
        database_url=database_url or "",
        customer_id=args.customer_id,
        org_id=args.org_id,
        provider_type=args.provider_type,
        properties=properties,
    )
    logger.info(
        "Upserted %d row(s) into property_tenant_registry "
        "(customer_id=%s, provider_type=%s)",
        written,
        args.customer_id,
        args.provider_type,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
