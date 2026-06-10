"""Episodic Memory - Session and event history with temporal retrieval.

Stores episodes (events, interactions, decisions) with timestamps and
supports retrieval by recency, time range, and session context.

Inspired by Zep's temporal knowledge layer:
- Automatic timestamping of all events
- Session-scoped history
- Time-range queries for temporal reasoning

Supports two backends:
- JSON file (default, for development/testing)
- Redis (for production, with automatic TTL-based expiration) — inject
  Dify's shared client from ``extensions.ext_redis`` in deployments
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis

import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, override

from core.brain.memory.observe import emit_memory_retrieved

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Episode:
    """A single episode in the agent's experience history.

    Attributes:
        id: Unique episode identifier.
        event: The event type or action name.
        content: Detailed content or description of what happened.
        metadata: Arbitrary key-value metadata.
        session_id: The session this episode belongs to.
        timestamp: UTC timestamp of when the episode occurred.
    """

    event: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Serialize the episode to a JSON-compatible dictionary."""
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Episode:
        """Deserialize an episode from a dictionary."""
        data = dict(data)
        ts = data.get("timestamp")
        if isinstance(ts, str):
            data["timestamp"] = datetime.fromisoformat(ts)
        return cls(**data)


class EpisodicBackend(ABC):
    """Abstract backend interface for episodic memory storage."""

    @abstractmethod
    def append(self, episode: Episode) -> None: ...

    @abstractmethod
    def get_recent(self, n: int, session_id: str | None = None) -> list[Episode]: ...

    @abstractmethod
    def get_by_time_range(self, start: datetime, end: datetime, session_id: str | None = None) -> list[Episode]: ...

    @abstractmethod
    def get_session_history(self, session_id: str) -> list[Episode]: ...

    @abstractmethod
    def clear(self, session_id: str | None = None) -> None: ...


class JsonFileBackend(EpisodicBackend):
    """JSON file-based backend for development and testing.

    Args:
        file_path: Path to the JSON file for persistence.
    """

    def __init__(self, file_path: str | Path = "episodic_memory.json") -> None:
        self._path = Path(file_path)
        self._episodes: list[Episode] = []
        self._loaded = False

    def _load(self) -> None:
        """Load episodes from disk if not already loaded."""
        if self._loaded:
            return
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._episodes = [Episode.from_dict(d) for d in raw]
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Failed to load episodic memory: %s", exc)
                self._episodes = []
        self._loaded = True

    def _save(self) -> None:
        """Persist episodes to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = [ep.to_dict() for ep in self._episodes]
        self._path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    @override
    def append(self, episode: Episode) -> None:
        self._load()
        self._episodes.append(episode)
        self._save()

    @override
    def get_recent(self, n: int, session_id: str | None = None) -> list[Episode]:
        self._load()
        episodes = self._episodes
        if session_id:
            episodes = [e for e in episodes if e.session_id == session_id]
        return episodes[-n:]

    @override
    def get_by_time_range(self, start: datetime, end: datetime, session_id: str | None = None) -> list[Episode]:
        self._load()
        results = []
        for ep in self._episodes:
            if start <= ep.timestamp <= end:
                if session_id is None or ep.session_id == session_id:
                    results.append(ep)
        return results

    @override
    def get_session_history(self, session_id: str) -> list[Episode]:
        self._load()
        return [e for e in self._episodes if e.session_id == session_id]

    @override
    def clear(self, session_id: str | None = None) -> None:
        self._load()
        if session_id:
            self._episodes = [e for e in self._episodes if e.session_id != session_id]
        else:
            self._episodes.clear()
        self._save()


class RedisBackend(EpisodicBackend):
    """Redis-based backend for production episodic memory.

    Stores episodes as JSON strings in Redis sorted sets, keyed by
    timestamp for efficient time-range queries.

    Args:
        redis_url: Redis connection URL.
        key_prefix: Prefix for all Redis keys used by this backend.
        ttl_seconds: Time-to-live for episode entries. None for no expiry.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        key_prefix: str = "brain:episodic:",
        redis_client: redis.Redis | None = None,
        ttl_seconds: int | None = 86400 * 30,  # 30 days default
    ) -> None:
        import redis

        # Dify deployments inject the shared client from
        # extensions.ext_redis (redis_client); the URL path remains for
        # standalone use and tests.
        self._redis = redis_client if redis_client is not None else redis.from_url(redis_url, decode_responses=True)
        self._prefix = key_prefix
        self._ttl = ttl_seconds

    def _key(self, session_id: str | None = None) -> str:
        if session_id:
            return f"{self._prefix}session:{session_id}"
        return f"{self._prefix}global"

    @override
    def append(self, episode: Episode) -> None:
        key = self._key(episode.session_id)
        global_key = self._key(None)
        score = episode.timestamp.timestamp()
        data = json.dumps(episode.to_dict())

        pipeline = self._redis.pipeline()
        pipeline.zadd(key, {data: score})
        pipeline.zadd(global_key, {data: score})
        if self._ttl:
            pipeline.expire(key, self._ttl)
        pipeline.execute()

    @override
    def get_recent(self, n: int, session_id: str | None = None) -> list[Episode]:
        key = self._key(session_id)
        raw_entries = self._redis.zrevrange(key, 0, n - 1)
        return [Episode.from_dict(json.loads(r)) for r in reversed(raw_entries)]

    @override
    def get_by_time_range(self, start: datetime, end: datetime, session_id: str | None = None) -> list[Episode]:
        key = self._key(session_id)
        raw_entries = self._redis.zrangebyscore(key, start.timestamp(), end.timestamp())
        return [Episode.from_dict(json.loads(r)) for r in raw_entries]

    @override
    def get_session_history(self, session_id: str) -> list[Episode]:
        key = self._key(session_id)
        raw_entries = self._redis.zrange(key, 0, -1)
        return [Episode.from_dict(json.loads(r)) for r in raw_entries]

    @override
    def clear(self, session_id: str | None = None) -> None:
        key = self._key(session_id)
        self._redis.delete(key)

    def close(self) -> None:
        """Close the Redis connection."""
        self._redis.close()


class EpisodicMemory:
    """Session and event history with temporal retrieval.

    Provides a high-level API for recording episodes and querying them
    by recency, time range, or session. The actual storage is delegated
    to a pluggable backend (JSON file or Redis).

    Args:
        backend: The storage backend to use. Defaults to JsonFileBackend.
        session_id: Default session ID for new episodes.
        max_episodes: Maximum episodes to retain per session (0 for unlimited).
    """

    def __init__(
        self,
        backend: EpisodicBackend | None = None,
        session_id: str = "",
        max_episodes: int = 0,
    ) -> None:
        self.session_id = session_id or str(uuid.uuid4())
        self.max_episodes = max_episodes
        self._backend = backend or JsonFileBackend()

    def add_episode(
        self,
        event: str,
        content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Episode:
        """Record a new episode in memory.

        Args:
            event: The event type or action name.
            content: Detailed description of what happened.
            metadata: Optional key-value metadata.

        Returns:
            The created Episode object.
        """
        episode = Episode(
            event=event,
            content=content,
            metadata=metadata or {},
            session_id=self.session_id,
        )
        self._backend.append(episode)

        logger.info(
            "Recorded episode: event=%s session=%s",
            event,
            self.session_id,
        )
        return episode

    def get_recent(self, n: int = 10) -> list[Episode]:
        """Retrieve the N most recent episodes for the current session.

        Args:
            n: Number of recent episodes to retrieve.

        Returns:
            List of episodes, oldest first.
        """
        t0 = time.perf_counter()
        episodes = self._backend.get_recent(n, session_id=self.session_id)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        emit_memory_retrieved(
            tier="episodic",
            query=f"recent:{n}",
            hits=[
                {
                    "id": getattr(ep, "id", ""),
                    "score": 1.0,
                    "excerpt": getattr(ep, "content", "") or getattr(ep, "event", ""),
                }
                for ep in episodes
            ],
            latency_ms=latency_ms,
        )
        return episodes

    def search_by_time_range(
        self,
        start: datetime,
        end: datetime,
    ) -> list[Episode]:
        """Retrieve episodes within a time range for the current session.

        Args:
            start: Start of the time range (inclusive).
            end: End of the time range (inclusive).

        Returns:
            List of episodes within the range, chronologically ordered.
        """
        return self._backend.get_by_time_range(start, end, session_id=self.session_id)

    def get_session_history(self, session_id: str | None = None) -> list[Episode]:
        """Retrieve the full history for a session.

        Args:
            session_id: The session to query. Defaults to the current session.

        Returns:
            Complete list of episodes for the session.
        """
        sid = session_id or self.session_id
        return self._backend.get_session_history(sid)

    def clear(self) -> None:
        """Clear all episodes for the current session."""
        self._backend.clear(session_id=self.session_id)

    @override
    def __repr__(self) -> str:
        return f"EpisodicMemory(session_id={self.session_id!r}, backend={type(self._backend).__name__})"
