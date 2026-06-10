"""Tests for the per-property cleanup helper script.

Limits the surface to the pure-Python helpers (report formatting,
shell-command hints) so the suite never touches a live database.
The DB path is covered by an :class:`asyncpg`-mocked path that
verifies the script issues the expected SQL without actually
opening a network connection.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


def _load_module() -> Any:
    """Import the cleanup script as a module from the scripts dir."""
    spec = importlib.util.spec_from_file_location(
        "cleanup_property_data",
        _SCRIPTS_DIR / "cleanup_property_data.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["cleanup_property_data"] = module
    spec.loader.exec_module(module)
    return module


cleanup_module = _load_module()
TableCleanupResult = cleanup_module.TableCleanupResult


def test_report_dry_run_uses_would_delete_header() -> None:
    """Dry-run report headers must say 'WOULD DELETE'."""
    results = (
        TableCleanupResult(
            table="decision_cases", rows_matched=10, rows_deleted=0,
        ),
    )
    output = cleanup_module._format_report(results, apply=False)
    assert "WOULD DELETE" in output
    assert "DELETED" not in output.replace("WOULD DELETE", "")


def test_report_apply_uses_deleted_header() -> None:
    """Apply-mode report headers must say 'DELETED'."""
    results = (
        TableCleanupResult(
            table="pattern_rules", rows_matched=3, rows_deleted=3,
        ),
    )
    output = cleanup_module._format_report(results, apply=True)
    assert "DELETED" in output


def test_report_totals_aggregate_correctly() -> None:
    """The TOTAL row sums matched + deleted across every table."""
    results = (
        TableCleanupResult(
            table="decision_cases", rows_matched=7, rows_deleted=7,
        ),
        TableCleanupResult(
            table="pattern_rules", rows_matched=3, rows_deleted=3,
        ),
        TableCleanupResult(
            table="blockers", rows_matched=0, rows_deleted=0,
        ),
    )
    output = cleanup_module._format_report(results, apply=True)
    lines = output.splitlines()
    total_line = lines[-1]
    assert total_line.startswith("TOTAL")
    assert "10" in total_line  # 7 + 3 + 0
    assert "10" in total_line  # deleted total
    assert "0" in total_line


def test_report_handles_empty_results() -> None:
    """The formatter must not crash on an empty result tuple."""
    output = cleanup_module._format_report((), apply=False)
    # Header rows still render even with no data.
    assert "TOTAL" in output


def test_redis_hint_carries_property_id_and_db_index() -> None:
    """Redis hint pattern targets the right namespace + DB 1."""
    hint = cleanup_module._redis_hint("323133")
    assert "conv:323133:*" in hint
    assert "-n 1" in hint
    assert "bookly.redis.cache.windows.net" in hint
    assert hint.startswith("redis-cli")


def test_qdrant_hint_carries_property_id_filter() -> None:
    """Qdrant delete-points hint filters on the property metadata key."""
    hint = cleanup_module._qdrant_hint("323133")
    assert '"property_id"' in hint
    assert '"value":"323133"' in hint
    assert "memory_facts" in hint


def test_property_tables_registry_is_non_empty() -> None:
    """The registry must list at least one per-property table."""
    assert len(cleanup_module._PROPERTY_TABLES) > 0


def test_property_tables_registry_contains_known_tables() -> None:
    """Sanity-check that the registry covers core tables."""
    names = {table for table, _ in cleanup_module._PROPERTY_TABLES}
    assert "decision_cases" in names
    assert "blockers" in names
    assert "interactions" in names
    assert "property_profiles" in names


def test_property_profiles_uses_channel_id_column() -> None:
    """``property_profiles`` is keyed by ``property_channel_id``.

    Mümin 2026-05-11 dry-run on 323133 revealed that
    ``property_profiles`` and ``property_pm_facts`` carry the
    short Hostaway channel id under ``property_channel_id`` —
    not the generic ``property_id`` the other tables use.  Pin
    that fact so the registry stays accurate against schema
    drift.
    """
    registry = dict(cleanup_module._PROPERTY_TABLES)
    assert (
        registry["property_profiles"] == "property_channel_id"
    )
    assert (
        registry["property_pm_facts"] == "property_channel_id"
    )


def test_pattern_rules_filter_uses_scope_id_column() -> None:
    """``pattern_rules`` is keyed by ``scope_id`` with scope='property'."""
    table, column, scope = cleanup_module._PATTERN_RULES_TABLE
    assert table == "pattern_rules"
    assert column == "scope_id"
    assert scope == "property"


def test_main_requires_property_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI rejects calls without ``--property-id``."""
    with pytest.raises(SystemExit):
        cleanup_module.main([])


def test_main_dry_run_invokes_cleanup_without_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default (no ``--apply``) calls cleanup_property with apply=False."""
    called: dict[str, Any] = {}

    async def fake_cleanup(
        *,
        property_id: str,
        apply: bool,
    ) -> Sequence[Any]:
        called["property_id"] = property_id
        called["apply"] = apply
        return ()

    monkeypatch.setattr(
        cleanup_module, "cleanup_property", fake_cleanup,
    )
    rc = cleanup_module.main(["--property-id", "323133"])
    assert rc == 0
    assert called == {"property_id": "323133", "apply": False}


def test_main_apply_flag_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``--apply`` flag is forwarded to cleanup_property."""
    captured: dict[str, Any] = {}

    async def fake_cleanup(
        *,
        property_id: str,
        apply: bool,
    ) -> Sequence[Any]:
        captured["apply"] = apply
        return ()

    monkeypatch.setattr(
        cleanup_module, "cleanup_property", fake_cleanup,
    )
    cleanup_module.main(["--property-id", "323133", "--apply"])
    assert captured["apply"] is True


@pytest.mark.asyncio
async def test_cleanup_property_dry_run_issues_count_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run path runs SELECT COUNT but never DELETE."""
    monkeypatch.setenv("DATABASE_URL", "postgres://fake/db")

    captured_queries: list[str] = []

    class FakeConn:
        async def fetchval(self, query: str, *_: Any) -> int:
            captured_queries.append(query)
            return 5

        async def execute(self, query: str, *_: Any) -> str:
            captured_queries.append(query)
            return "DELETE 5"

        async def close(self) -> None:
            return None

    async def fake_connect(*_: Any, **__: Any) -> FakeConn:
        return FakeConn()

    monkeypatch.setattr(
        cleanup_module.asyncpg, "connect", fake_connect,
    )
    results = await cleanup_module.cleanup_property(
        property_id="323133", apply=False,
    )
    assert len(results) > 0
    for r in results:
        assert r.rows_deleted == 0
    # No DELETE statements issued in dry-run.
    for query in captured_queries:
        assert "SELECT COUNT" in query or "FROM" in query
        assert not query.lstrip().upper().startswith("DELETE")


@pytest.mark.asyncio
async def test_cleanup_property_apply_issues_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--apply`` path issues DELETE statements."""
    monkeypatch.setenv("DATABASE_URL", "postgres://fake/db")

    captured_queries: list[str] = []

    class FakeConn:
        async def fetchval(self, query: str, *_: Any) -> int:
            captured_queries.append(query)
            return 4

        async def execute(self, query: str, *_: Any) -> str:
            captured_queries.append(query)
            return "DELETE 4"

        async def close(self) -> None:
            return None

    async def fake_connect(*_: Any, **__: Any) -> FakeConn:
        return FakeConn()

    monkeypatch.setattr(
        cleanup_module.asyncpg, "connect", fake_connect,
    )
    results = await cleanup_module.cleanup_property(
        property_id="323133", apply=True,
    )
    assert len(results) > 0
    delete_count = sum(
        1 for q in captured_queries
        if q.lstrip().upper().startswith("DELETE")
    )
    assert delete_count >= len(results)


def test_cleanup_property_raises_when_dsn_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing DATABASE_URL aborts before any connection attempt."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv(
        "DECISION_CASE_STORE_DATABASE_URL", raising=False,
    )
    import asyncio

    with pytest.raises(SystemExit):
        asyncio.run(
            cleanup_module.cleanup_property(
                property_id="323133", apply=False,
            )
        )


def test_pattern_rules_scope_clause_is_present_in_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pattern_rules path must narrow on scope='property'."""
    import asyncio

    monkeypatch.setenv("DATABASE_URL", "postgres://fake/db")
    captured: list[str] = []

    class FakeConn:
        async def fetchval(self, query: str, *_: Any) -> int:
            captured.append(query)
            return 1

        async def execute(self, query: str, *_: Any) -> str:
            captured.append(query)
            return "DELETE 1"

        async def close(self) -> None:
            return None

    async def fake_connect(*_: Any, **__: Any) -> FakeConn:
        return FakeConn()

    monkeypatch.setattr(
        cleanup_module.asyncpg, "connect", fake_connect,
    )
    asyncio.run(
        cleanup_module.cleanup_property(
            property_id="323133", apply=True,
        )
    )
    rules_queries = [
        q for q in captured if "pattern_rules" in q
    ]
    assert rules_queries
    assert all(
        "scope = 'property'" in q for q in rules_queries
    )
