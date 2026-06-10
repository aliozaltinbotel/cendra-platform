"""Read a property's full detail straight from the Elasticsearch
``unified_properties`` index.

``unified_properties`` is the canonical unified-property index (every
property, ~20k docs) that the onboarding-api GraphQL ``property()``
resolver is itself backed by — but reading it directly drops the
GraphQL resolver's customer/org scoping, so ANY property builds a
profile.  Its ``_source`` shape — ``{channelEntityId, data, pmsId,
transformedAt, …}`` — is exactly what
:func:`brain_engine.integrations.unified_data.readers._parse_property_detail`
already consumes, so the same parser is reused verbatim.

Join key: the brain's ``property_channel_id`` == ``channelEntityId``.
"""

from __future__ import annotations

from typing import Final

import structlog

from brain_engine.integrations.elasticsearch.client import (
    ElasticsearchClient,
)
from brain_engine.integrations.unified_data.readers import (
    PropertyDetail,
    _parse_property_detail,
)

__all__ = ["ElasticsearchPropertyReader"]


logger = structlog.get_logger(__name__)

_DEFAULT_INDEX: Final[str] = "unified_properties"


class ElasticsearchPropertyReader:
    """Fetch a :class:`PropertyDetail` from ``unified_properties``.

    The reader never owns the client; the application lifespan does.
    """

    def __init__(
        self,
        client: ElasticsearchClient,
        *,
        index: str = _DEFAULT_INDEX,
    ) -> None:
        self._client = client
        self._index = index
        self._log = logger.bind(component="es_property_reader")

    async def get_detail(
        self,
        channel_entity_id: str,
    ) -> PropertyDetail | None:
        """Return the property detail for ``channel_entity_id``.

        ``None`` when the id is empty or no document matches.  Raises
        :class:`ElasticsearchReaderError` only on a transport / status
        failure (the harvester fails open on that).
        """
        if not channel_entity_id:
            return None
        body = {
            "size": 1,
            "query": {"term": {"channelEntityId": channel_entity_id}},
        }
        response = await self._client.search(self._index, body)
        hits = (response.get("hits") or {}).get("hits") or []
        if not hits:
            self._log.info(
                "es.property_absent", channel_entity_id=channel_entity_id,
            )
            return None
        source = hits[0].get("_source") or {}
        detail = _parse_property_detail(source)
        self._log.info(
            "es.detail_built", channel_entity_id=channel_entity_id,
        )
        return detail
