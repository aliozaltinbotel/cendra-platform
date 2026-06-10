"""Cendra onboarding-api unified-data integration.

Read-side façade over the GraphQL endpoint exposed by the
``onboarding-api`` service in the ``dev`` namespace.  See
``reference_onboarding_api_graphql.md`` (memory) for schema details.

Public surface intentionally stays small: a single client, a single
exception family, and the canonical query strings.  Domain mapping
into Brain Engine objects (timeline events, etc.) is deliberately
kept in the consuming subsystem to avoid coupling this layer to any
particular use case.
"""

from __future__ import annotations

from brain_engine.integrations.unified_data.client import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    UnifiedDataGraphQLClient,
)
from brain_engine.integrations.unified_data.errors import (
    UnifiedDataError,
    UnifiedDataGraphQLError,
    UnifiedDataTransportError,
)
from brain_engine.integrations.unified_data.queries import (
    CONVERSATIONS_WITH_MESSAGES_QUERY,
    PROPERTIES_LIST_QUERY,
    PROPERTY_DETAIL_QUERY,
    RATE_PLANS_LIST_QUERY,
    RATE_PLANS_WITH_CALENDAR_QUERY,
    RESERVATIONS_LIST_QUERY,
    REVIEWS_LIST_QUERY,
)
from brain_engine.integrations.unified_data.pms_fetcher import (
    GraphqlPmsFetcher,
    fetch_calendar_window,
    fetch_reservation_context,
    to_feature_dict,
)
from brain_engine.integrations.unified_data.readers import (
    UnifiedPropertyReader,
    UnifiedRatePlanReader,
    UnifiedReviewReader,
)

__all__ = [
    "CONVERSATIONS_WITH_MESSAGES_QUERY",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "PROPERTIES_LIST_QUERY",
    "PROPERTY_DETAIL_QUERY",
    "RATE_PLANS_LIST_QUERY",
    "RATE_PLANS_WITH_CALENDAR_QUERY",
    "RESERVATIONS_LIST_QUERY",
    "REVIEWS_LIST_QUERY",
    "GraphqlPmsFetcher",
    "fetch_calendar_window",
    "UnifiedDataError",
    "UnifiedDataGraphQLClient",
    "UnifiedDataGraphQLError",
    "UnifiedDataTransportError",
    "UnifiedPropertyReader",
    "UnifiedRatePlanReader",
    "UnifiedReviewReader",
    "fetch_reservation_context",
    "to_feature_dict",
]
