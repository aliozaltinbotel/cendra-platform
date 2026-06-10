"""Direct Elasticsearch read integration for property detail.

Reads the full property document straight from the canonical
``unified_properties`` index (every property, no GraphQL customer/org
scoping) and parses it into a :class:`PropertyDetail` with the same
parser the GraphQL path uses — so any property the Sandbox can select
builds a profile.
"""

from __future__ import annotations

from brain_engine.integrations.elasticsearch.client import (
    DEFAULT_ENDPOINT,
    ElasticsearchClient,
)
from brain_engine.integrations.elasticsearch.errors import (
    ElasticsearchError,
    ElasticsearchReaderError,
)
from brain_engine.integrations.elasticsearch.reader import (
    ElasticsearchPropertyReader,
)

__all__ = [
    "DEFAULT_ENDPOINT",
    "ElasticsearchClient",
    "ElasticsearchError",
    "ElasticsearchPropertyReader",
    "ElasticsearchReaderError",
]
