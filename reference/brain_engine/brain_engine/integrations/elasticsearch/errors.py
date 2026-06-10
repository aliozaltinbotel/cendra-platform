"""Error types for the direct Elasticsearch read integration.

A single base (:class:`ElasticsearchError`) plus one reader-level wrapper
(:class:`ElasticsearchReaderError`) so callers can catch one type
regardless of whether the failure was transport, auth, or a malformed
response — mirroring the ``UnifiedDataReaderError`` convention in
:mod:`brain_engine.integrations.unified_data.errors`.
"""

from __future__ import annotations

__all__ = [
    "ElasticsearchError",
    "ElasticsearchReaderError",
]


class ElasticsearchError(Exception):
    """Base for every failure raised by the Elasticsearch integration."""


class ElasticsearchReaderError(ElasticsearchError):
    """A reader could not produce a result (transport / status / body).

    Carries the reader name so logs can attribute the failure without
    the caller having to know which query ran.
    """

    def __init__(self, reader: str, message: str) -> None:
        super().__init__(f"{reader}: {message}")
        self.reader = reader
