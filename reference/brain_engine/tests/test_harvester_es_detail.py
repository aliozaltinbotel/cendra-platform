"""The harvester reads property detail from ES ``unified_properties``
first (full coverage, any property) and falls back to the GraphQL
property reader only when ES is absent / empty / errors.
"""

from __future__ import annotations

from typing import Any

import pytest

from brain_engine.integrations.unified_data.readers import (
    _parse_property_detail,
)
from brain_engine.profiles.harvester import PropertyProfileHarvester
from brain_engine.profiles.models import PropertyProfile


def _detail(cleaning_fee: float) -> Any:
    return _parse_property_detail({
        "channelEntityId": "598829",
        "data": {"title": "Test", "cleaningFee": cleaning_fee},
    })


class _GraphqlReader:
    def __init__(self, detail: Any) -> None:
        self._detail = detail
        self.called = False

    async def get_detail(self, *, channel_entity_id: str) -> Any:
        self.called = True
        return self._detail


class _ESReader:
    def __init__(self, detail: Any = None, *, boom: bool = False) -> None:
        self._detail = detail
        self._boom = boom

    async def get_detail(self, channel_entity_id: str) -> Any:
        if self._boom:
            raise RuntimeError("es down")
        return self._detail


class _ListReader:
    async def list_for_property(
        self, *, property_channel_id: str, limit: int, skip: int,
    ) -> list[Any]:
        return []


class _Store:
    def __init__(self) -> None:
        self.saved: PropertyProfile | None = None

    async def get(self, property_channel_id: str) -> PropertyProfile | None:
        return None

    async def put(self, profile: PropertyProfile) -> None:
        self.saved = profile

    async def list_all(self) -> list[PropertyProfile]:
        return []


async def _run(es: Any, graphql: _GraphqlReader) -> _Store:
    store = _Store()
    harvester = PropertyProfileHarvester(
        property_reader=graphql,                # type: ignore[arg-type]
        rate_plan_reader=_ListReader(),         # type: ignore[arg-type]
        review_reader=_ListReader(),            # type: ignore[arg-type]
        profile_store=store,                    # type: ignore[arg-type]
        es_reader=es,
    )
    await harvester.harvest(
        property_channel_id="598829", customer_id="c",
        org_id="o", provider_type="LODGIFY",
    )
    return store


@pytest.mark.asyncio
async def test_es_detail_is_primary_graphql_not_called() -> None:
    graphql = _GraphqlReader(_detail(35.0))
    store = await _run(_ESReader(_detail(40.0)), graphql)
    sp = dict(store.saved.static_payload)  # type: ignore[union-attr]
    assert sp["cleaning_fee"] == 40.0   # ES value used
    assert graphql.called is False      # GraphQL never consulted


@pytest.mark.asyncio
async def test_falls_back_to_graphql_when_es_absent() -> None:
    graphql = _GraphqlReader(_detail(35.0))
    store = await _run(_ESReader(None), graphql)  # ES has no doc
    sp = dict(store.saved.static_payload)  # type: ignore[union-attr]
    assert sp["cleaning_fee"] == 35.0   # GraphQL fallback
    assert graphql.called is True


@pytest.mark.asyncio
async def test_falls_back_to_graphql_when_es_errors() -> None:
    graphql = _GraphqlReader(_detail(35.0))
    store = await _run(_ESReader(boom=True), graphql)  # ES raises
    sp = dict(store.saved.static_payload)  # type: ignore[union-attr]
    assert sp["cleaning_fee"] == 35.0   # fail-open → GraphQL
    assert graphql.called is True


@pytest.mark.asyncio
async def test_no_es_reader_uses_graphql() -> None:
    graphql = _GraphqlReader(_detail(35.0))
    store = await _run(None, graphql)
    sp = dict(store.saved.static_payload)  # type: ignore[union-attr]
    assert sp["cleaning_fee"] == 35.0
    assert graphql.called is True


@pytest.mark.asyncio
async def test_detail_missing_when_both_empty() -> None:
    graphql = _GraphqlReader(None)
    store = await _run(_ESReader(None), graphql)
    assert store.saved is None  # no profile built
