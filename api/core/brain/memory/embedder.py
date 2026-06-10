"""Embedding seam — dense vectors come from a separate embedding pod.

The reference embedded in-process via ``sentence_transformers`` (~700 MB
of weights for the Sprint A target model).  Per the Batch 3 deployment
decision, cendra-platform keeps model weights OUT of the api image:
dense embeddings are served by a dedicated embedding pod (TEI /
infinity / any OpenAI-compatible ``/v1/embeddings`` server) and the
kernel talks to it through the :class:`Embedder` Protocol.

Configuration (documented for the T8 env surface):

- ``BRAIN_EMBEDDING_ENDPOINT`` — base URL of the embedding pod
  (e.g. ``http://brain-embedder:8080/v1``).  Required for
  :class:`RemoteEmbedder`.
- ``BRAIN_EMBEDDING_API_KEY`` — optional bearer token.
- ``BRAIN_EMBEDDING_MODEL`` / ``BRAIN_EMBEDDING_DIM`` — model name and
  dimensionality (see :mod:`core.brain.memory.embedding_config`; the
  re-embedding migration warning there applies unchanged).

Vectors are L2-normalised client-side so similarity behaviour matches
the reference's ``normalize_embeddings=True``.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Sequence
from typing import Final, Protocol, runtime_checkable

import httpx

from core.brain.memory.embedding_config import resolve_embedding_model

__all__ = [
    "EMBEDDING_API_KEY_ENV",
    "EMBEDDING_ENDPOINT_ENV",
    "Embedder",
    "RemoteEmbedder",
]

logger = logging.getLogger(__name__)

EMBEDDING_ENDPOINT_ENV: Final[str] = "BRAIN_EMBEDDING_ENDPOINT"
EMBEDDING_API_KEY_ENV: Final[str] = "BRAIN_EMBEDDING_API_KEY"

_DEFAULT_TIMEOUT_SECONDS: Final[float] = 30.0


@runtime_checkable
class Embedder(Protocol):
    """Dense text encoder seam (implementations live outside the kernel)."""

    def encode(self, text: str) -> list[float]:
        """Return the L2-normalised embedding for ``text``."""
        ...

    def encode_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Return L2-normalised embeddings for ``texts`` (input order)."""
        ...


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


class RemoteEmbedder:
    """OpenAI-compatible ``/embeddings`` client for the embedding pod."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        endpoint = endpoint or os.environ.get(EMBEDDING_ENDPOINT_ENV, "").strip()
        if not endpoint:
            raise ValueError(f"RemoteEmbedder needs an endpoint — pass one or set {EMBEDDING_ENDPOINT_ENV}")
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key if api_key is not None else os.environ.get(EMBEDDING_API_KEY_ENV) or None
        self._model = model or resolve_embedding_model()
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def encode(self, text: str) -> list[float]:
        return self.encode_batch([text])[0]

    def encode_batch(self, texts: Sequence[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        response = self._client.post(
            f"{self._endpoint}/embeddings",
            json={"model": self._model, "input": list(texts)},
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        rows = sorted(payload["data"], key=lambda item: item.get("index", 0))
        if len(rows) != len(texts):
            raise ValueError(f"embedding pod returned {len(rows)} vectors for {len(texts)} inputs")
        return [_normalize([float(v) for v in row["embedding"]]) for row in rows]

    def __repr__(self) -> str:
        return f"RemoteEmbedder(endpoint={self._endpoint!r}, model={self._model!r})"
