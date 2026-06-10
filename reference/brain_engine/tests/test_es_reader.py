"""Tests for the Elasticsearch client + property-detail reader.

The reader fetches a ``unified_properties`` document by
``channelEntityId`` and parses its ``_source`` into a
:class:`PropertyDetail` using the same parser as the GraphQL path
(``_parse_property_detail``); the client turns transport / status
failures into a single :class:`ElasticsearchReaderError`.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from brain_engine.integrations.elasticsearch.client import (
    ElasticsearchClient,
)
from brain_engine.integrations.elasticsearch.errors import (
    ElasticsearchReaderError,
)
from brain_engine.integrations.elasticsearch.reader import (
    ElasticsearchPropertyReader,
)

# -- reader ------------------------------------------------------------


class _FakeClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((index, body))
        return self._response


def _hit(source: dict[str, Any]) -> dict[str, Any]:
    return {"hits": {"hits": [{"_source": source}]}}


@pytest.mark.asyncio
async def test_reader_parses_unified_properties_doc() -> None:
    # Shape mirrors a real unified_properties _source.
    source = {
        "channelEntityId": "598829",
        "pmsId": "pms-1",
        "data": {
            "title": "T2 - La French Casa",
            "baseCurrency": "EUR",
            "basePrice": 137,
            "cleaningFee": 40,
            "wifiNetwork": "Available",
        },
    }
    client = _FakeClient(_hit(source))
    reader = ElasticsearchPropertyReader(client)  # type: ignore[arg-type]
    detail = await reader.get_detail("598829")
    assert detail is not None
    assert detail.channel_entity_id == "598829"
    assert detail.cleaning_fee == 40.0
    assert detail.base_currency == "EUR"
    assert detail.title == "T2 - La French Casa"
    # Scoped by a term query on channelEntityId against unified_properties.
    index, body = client.calls[0]
    assert index == "unified_properties"
    assert body["query"] == {"term": {"channelEntityId": "598829"}}


@pytest.mark.asyncio
async def test_reader_returns_none_when_absent() -> None:
    reader = ElasticsearchPropertyReader(
        _FakeClient({"hits": {"hits": []}}),  # type: ignore[arg-type]
    )
    assert await reader.get_detail("nope") is None


@pytest.mark.asyncio
async def test_reader_empty_id_is_none() -> None:
    reader = ElasticsearchPropertyReader(
        _FakeClient({"hits": {"hits": []}}),  # type: ignore[arg-type]
    )
    assert await reader.get_detail("") is None


# -- client transport / status handling --------------------------------


class _FakeHttp:
    """Minimal stand-in for httpx.AsyncClient.post."""

    def __init__(self, *, status: int = 200, body: Any = None,
                 raise_exc: Exception | None = None) -> None:
        self._status = status
        self._body = body if body is not None else {"hits": {"hits": []}}
        self._raise = raise_exc

    async def post(self, url: str, json: Any = None) -> Any:
        if self._raise is not None:
            raise self._raise
        return httpx.Response(self._status, json=self._body)


def _client(http: _FakeHttp) -> ElasticsearchClient:
    return ElasticsearchClient(api_key="k", client=http)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_client_returns_json_on_200() -> None:
    es = _client(_FakeHttp(status=200, body={"hits": {"hits": [1]}}))
    assert await es.search("unified_properties", {"query": {}}) == {
        "hits": {"hits": [1]},
    }


@pytest.mark.asyncio
async def test_client_raises_on_non_2xx() -> None:
    es = _client(_FakeHttp(status=503))
    with pytest.raises(ElasticsearchReaderError):
        await es.search("unified_properties", {"query": {}})


@pytest.mark.asyncio
async def test_client_raises_on_transport_error() -> None:
    es = _client(_FakeHttp(raise_exc=httpx.ConnectError("down")))
    with pytest.raises(ElasticsearchReaderError):
        await es.search("unified_properties", {"query": {}})


def test_client_requires_api_key() -> None:
    with pytest.raises(ValueError):
        ElasticsearchClient(api_key="")
