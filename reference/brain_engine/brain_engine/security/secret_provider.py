"""Secret resolution with rotation-friendly invalidation.

Reference: ``brain_engine_advisory.md`` §9.1 — secret management.

The engine reads secrets — Azure OpenAI keys, Postgres passwords,
Service Bus SAS — through a single :class:`SecretProvider` Protocol
so the rotation policy stays one decision: change the provider, not
every call-site.

Two production-shaped implementations:

* :class:`EnvSecretProvider` reads ``os.environ`` directly.  Used in
  AKS where the Secret Provider Class CRD projects Key Vault values
  into the pod environment.
* :class:`KeyVaultSecretProvider` lazy-imports ``azure-identity`` +
  ``azure-keyvault-secrets`` and caches results with TTL so a tight
  loop does not hammer Key Vault.  Absent dependencies degrade
  gracefully — instantiation fails with a clear message rather than
  silently shadowing into env reads.

A :class:`CompositeSecretProvider` chains providers (env first, then
Key Vault).  The chain is deterministic and short-circuits on the
first hit, mirroring the way 12-factor apps stack overrides.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Protocol


class SecretProvider(Protocol):
    """Resolve a logical secret name to its current value."""

    def get(self, name: str) -> str:
        """Return the secret or raise ``KeyError`` if absent."""

    def invalidate(self, name: str) -> None:
        """Drop any cached value so the next ``get`` re-fetches."""


class EnvSecretProvider:
    """Reads secrets straight from ``os.environ``.

    No caching — env is already in-process memory.  The ``invalidate``
    method is a noop kept for Protocol compliance.
    """

    def __init__(self, *, prefix: str = "") -> None:
        self._prefix = prefix

    def get(self, name: str) -> str:
        key = f"{self._prefix}{name}" if self._prefix else name
        value = os.environ.get(key)
        if value is None:
            raise KeyError(f"secret {key!r} not in environment")
        return value

    def invalidate(self, name: str) -> None:  # noqa: D401 - noop
        """No cache to clear."""


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    value: str
    expires_at: float


class KeyVaultSecretProvider:
    """Azure Key Vault provider with TTL-bounded read-through cache.

    The constructor lazy-imports ``azure-identity`` + ``azure-keyvault-
    secrets``; missing the dependency raises ``RuntimeError`` so the
    failure is loud, not silent.  Concurrency safety is provided by a
    single ``threading.Lock`` around the cache; the underlying SDK
    client is thread-safe per Azure SDK guidelines.
    """

    def __init__(
        self,
        *,
        vault_url: str,
        ttl_seconds: float = 300.0,
        credential: object | None = None,
    ) -> None:
        if not vault_url:
            raise ValueError("vault_url required")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._vault_url = vault_url
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._cache: dict[str, _CacheEntry] = {}
        self._client = self._build_client(credential)

    def _build_client(self, credential: object | None) -> object:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ImportError as exc:  # pragma: no cover - covered in CI
            raise RuntimeError(
                "azure-identity + azure-keyvault-secrets are required "
                "for KeyVaultSecretProvider; install via requirements.txt",
            ) from exc
        return SecretClient(
            vault_url=self._vault_url,
            credential=credential or DefaultAzureCredential(),
        )

    def get(self, name: str) -> str:
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(name)
            if cached is not None and cached.expires_at > now:
                return cached.value
        # Fetch outside the lock — Key Vault calls take 10–100 ms.
        secret = self._client.get_secret(name)  # type: ignore[attr-defined]
        value = secret.value or ""
        if not value:
            raise KeyError(f"Key Vault returned empty secret {name!r}")
        with self._lock:
            self._cache[name] = _CacheEntry(
                value=value, expires_at=now + self._ttl,
            )
        return value

    def invalidate(self, name: str) -> None:
        with self._lock:
            self._cache.pop(name, None)


class CompositeSecretProvider:
    """Chains providers; first hit wins.

    Typical wiring: ``CompositeSecretProvider(EnvSecretProvider(),
    KeyVaultSecretProvider(...))`` — env overrides for local dev,
    Key Vault for production-only secrets.  Misses fall through; the
    final ``KeyError`` carries the chain length so the message is
    actionable in pod logs.
    """

    def __init__(self, *providers: SecretProvider) -> None:
        if not providers:
            raise ValueError("at least one provider required")
        self._providers = providers

    def get(self, name: str) -> str:
        for provider in self._providers:
            try:
                return provider.get(name)
            except KeyError:
                continue
        raise KeyError(
            f"secret {name!r} not found across "
            f"{len(self._providers)} providers",
        )

    def invalidate(self, name: str) -> None:
        for provider in self._providers:
            provider.invalidate(name)
