"""Phase 5 — GraphQL lazy probe for unknown properties.

When the Sandbox UI picks a property the brain has never seen
(``property_tenant_registry`` MISS) the Phase 3 resolver would
otherwise fall through to the pod env_default — which is almost
certainly the wrong customer.  The Phase 5 :class:`GraphQLLazyProbe`
closes that gap: it asks the unified-data GraphQL gateway "who owns
property X?" by iterating every known customer and looking for the
property in their ``conversations`` feed.

The probe is wired into :class:`TenantResolver` so the resolve
chain becomes:

  1. cache hit  → return
  2. registry hit → return + cache
  3. **lazy probe → upsert + cache + return (Phase 5)**
  4. env_default → fall through (logged at WARN)

Why ``conversations`` rather than ``properties``:
  * the ``properties`` GraphQL query does not return
    ``providerType`` in the row payload — a hit confirms ownership
    but leaves the provider unknown;
  * the ``conversations`` query *does* return ``providerType`` per
    row, so a single hit yields the full
    ``(customer_id, provider_type)`` we need.

A negative cache (TTL-bounded LRU) prevents the same unknown
property from triggering N x customers worth of GraphQL traffic on
every request.  Hits are upserted into the registry by
:class:`TenantResolver` so subsequent requests resolve via the
cheap path.

Strategies considered + rejected:
  * **Customer-only ownership probe** (``properties`` query) —
    rejected because we cannot recover ``provider_type`` from
    the response, leaving downstream readers without enough
    scope to call ``get_detail`` / harvester paths.
  * **Customer x provider matrix** (~20 providers x N customers)
    — rejected because the conversations-query path returns
    ``provider_type`` for free and costs at most 1 query per
    candidate customer.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

import structlog

from brain_engine.tenants.models import (
    TENANT_SOURCE_LAZY,
    TenantContext,
)

if TYPE_CHECKING:
    from brain_engine.integrations.unified_data.client import (
        UnifiedDataGraphQLClient,
    )

__all__ = ["GraphQLLazyProbe"]


logger = structlog.get_logger(__name__)


_DEFAULT_NEGATIVE_CACHE_CAPACITY: Final[int] = 5000
_DEFAULT_NEGATIVE_CACHE_TTL: Final[timedelta] = timedelta(minutes=10)


#: Async callable that produces the list of customer ids the
#: probe should iterate.  Kept as a callable so the wiring layer
#: can inject ``registry.distinct_customers`` directly without
#: forcing the probe to import the registry symbol.
CustomersProvider = Callable[[], Awaitable[list[str]]]


# ---------------------------------------------------------------------------
# GraphQL probe — conversations(customerId, propertyChannelId, limit=1)
# ---------------------------------------------------------------------------


_CONVERSATIONS_PROBE_QUERY: Final[str] = """\
query LazyProbeProperty(
  $customerId: String!
  $propertyChannelId: String!
) {
  conversations(
    customerId: $customerId
    propertyChannelId: $propertyChannelId
    limit: 1
  ) {
    channelEntityId
    providerType
    data {
      propertyChannelId
    }
  }
}
"""


class GraphQLLazyProbe:
    """Discover ``(customer_id, provider_type)`` for an unknown property."""

    def __init__(
        self,
        client: UnifiedDataGraphQLClient,
        customers_provider: CustomersProvider,
        *,
        extra_customers: tuple[str, ...] = (),
        negative_cache_ttl: timedelta = _DEFAULT_NEGATIVE_CACHE_TTL,
        negative_cache_capacity: int = _DEFAULT_NEGATIVE_CACHE_CAPACITY,
    ) -> None:
        self._client = client
        self._customers_provider = customers_provider
        self._extra_customers = tuple(c for c in extra_customers if c)
        self._negative_ttl = max(timedelta(0), negative_cache_ttl)
        self._negative_capacity = max(1, negative_cache_capacity)
        self._negative: OrderedDict[str, datetime] = OrderedDict()

    async def __call__(
        self,
        property_channel_id: str,
    ) -> TenantContext | None:
        return await self.probe(property_channel_id)

    async def probe(
        self,
        property_channel_id: str,
    ) -> TenantContext | None:
        if not property_channel_id:
            return None
        if self._inside_negative_cache(property_channel_id):
            return None

        for customer_id in await self._candidate_customers():
            hit = await self._try_customer(
                customer_id=customer_id,
                property_channel_id=property_channel_id,
            )
            if hit is not None:
                return hit

        self._mark_negative(property_channel_id)
        logger.info(
            "lazy_probe.miss",
            property_channel_id=property_channel_id,
        )
        return None

    async def _candidate_customers(self) -> list[str]:
        try:
            registry_customers = await self._customers_provider()
        except Exception as exc:
            logger.warning(
                "lazy_probe.customers_lookup_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            registry_customers = []
        seen: set[str] = set()
        ordered: list[str] = []
        for customer in (*self._extra_customers, *registry_customers):
            if not customer or customer in seen:
                continue
            seen.add(customer)
            ordered.append(customer)
        return ordered

    async def _try_customer(
        self,
        *,
        customer_id: str,
        property_channel_id: str,
    ) -> TenantContext | None:
        try:
            payload = await self._client.execute(
                _CONVERSATIONS_PROBE_QUERY,
                {
                    "customerId": customer_id,
                    "propertyChannelId": property_channel_id,
                },
                operation_name="LazyProbeProperty",
            )
        except Exception as exc:
            logger.warning(
                "lazy_probe.graphql_failed",
                customer_id=customer_id,
                property_channel_id=property_channel_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        conversations = _coerce_list(payload.get("conversations"))
        if not conversations:
            return None
        first = conversations[0]
        provider_type = (first.get("providerType") or "").strip()
        if not provider_type:
            return None
        logger.info(
            "lazy_probe.hit",
            customer_id=customer_id,
            property_channel_id=property_channel_id,
            provider_type=provider_type,
        )
        return TenantContext(
            customer_id=customer_id,
            org_id=None,
            provider_type=provider_type,
            property_channel_id=property_channel_id,
            source=TENANT_SOURCE_LAZY,
        )

    def _inside_negative_cache(self, property_channel_id: str) -> bool:
        stamped = self._negative.get(property_channel_id)
        if stamped is None:
            return False
        if datetime.now(UTC) - stamped >= self._negative_ttl:
            self._negative.pop(property_channel_id, None)
            return False
        # Refresh LRU position so hot misses stay cached.
        self._negative.move_to_end(property_channel_id)
        return True

    def _mark_negative(self, property_channel_id: str) -> None:
        self._negative[property_channel_id] = datetime.now(UTC)
        self._negative.move_to_end(property_channel_id)
        while len(self._negative) > self._negative_capacity:
            self._negative.popitem(last=False)


def _coerce_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]
