"""Async SQLAlchemy engine factory for the Botel/Bookly.Pms MySQL DB.

The Botel PMS database lives on the same Azure MySQL server as the
CORA inbox stack and hosts the canonical ``MessageItem``,
``Conversation``, and related tables that drive past-conversation
analysis.  This module owns the connection layer only — schema
definitions, query DSL, and reader adapters belong in sibling
modules so the engine stays decoupled from any particular use case.

The engine is intentionally **read-only by convention**: no ORM
metadata is ever pushed against this database (no ``create_all``)
because the upstream schema is owned by the Bookly.Pms .NET
service.

Secret resolution mirrors the CORA ``KeyVaultService`` pattern but
uses the in-house :class:`~brain_engine.security.SecretProvider`
abstraction so every credential reader in the engine goes through
one rotation-friendly seam.  The default chain is:

1. :class:`EnvSecretProvider` — reads ``os.environ`` directly.  In
   AKS the Secret Provider Class CRD projects Key Vault values into
   the pod environment, so this layer is the cheap hot path in
   production as well.
2. :class:`KeyVaultSecretProvider` — direct Key Vault lookup,
   always wired into the chain.  The vault URL comes from the
   ``AZURE_KEYVAULT_URL`` env var when set (production override)
   and otherwise falls back to :data:`_DEFAULT_KEY_VAULT_URL` —
   the same URL the deploy manifests pin, hard-coded so a fresh
   clone plus ``az login`` and a Key Vault Secrets User role
   resolves credentials without extra setup.  An explicit mapping
   (mirroring CORA's ``KeyVaultService.ENV_MAPPING`` in
   ``CendraRuleCreator/src/services/key_vault_service.py``)
   translates the engine's UPPER_SNAKE_CASE call-site names to
   the CamelCase secret identifiers the team already provisions —
   most importantly ``BOTEL_MYSQL_URL`` →
   :data:`_KV_DATABASE_URL_SECRET`, which reuses CORA's existing
   MySQL connection secret since both stacks share the same Azure
   MySQL server.

Nothing in this module reads or writes ``.env`` files directly;
operators inject values either through the pod environment or via
Key Vault.

Configuration priority (each name is resolved through the provider
chain above):

1. ``BOTEL_MYSQL_URL`` — fully-qualified DSN, used verbatim after
   the driver prefix is normalized to ``mysql+asyncmy://``.
2. ``BOTEL_MYSQL_HOST`` + ``BOTEL_MYSQL_PORT`` +
   ``BOTEL_MYSQL_DB`` + ``BOTEL_MYSQL_USER`` +
   ``BOTEL_MYSQL_PASSWORD`` (+ optional ``BOTEL_MYSQL_SSL``)
   composed into a DSN.

Either path raises :class:`BotelPmsConfigError` on missing or
malformed input; live failures at first use surface as
:class:`BotelPmsConnectionError`.
"""

from __future__ import annotations

import os
import re
import ssl
from collections.abc import Mapping
from typing import Final
from urllib.parse import quote

import structlog
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql import text

from brain_engine.integrations.botel_pms.errors import (
    BotelPmsConfigError,
    BotelPmsConnectionError,
)
from brain_engine.security import (
    CompositeSecretProvider,
    EnvSecretProvider,
    KeyVaultSecretProvider,
    SecretProvider,
)

__all__ = [
    "build_botel_pms_url",
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_session_factory",
    "ping",
]


logger = structlog.get_logger(__name__)


_ASYNC_DRIVER_PREFIX: Final[str] = "mysql+asyncmy://"
_SSL_REQUIRED_TOKENS: Final[frozenset[str]] = frozenset(
    {"required", "true", "1", "yes"}
)
_DEFAULT_POOL_SIZE: Final[int] = 5
_DEFAULT_MAX_OVERFLOW: Final[int] = 10
# 30 minutes — Azure MySQL idle-connection timeout defaults to 8 h
# but the gateway in front of botelmysql recycles sockets sooner;
# 1800 s keeps the pool warm without surfacing stale-conn errors.
_DEFAULT_POOL_RECYCLE_SECONDS: Final[int] = 1800
_KEY_VAULT_URL_ENV: Final[str] = "AZURE_KEYVAULT_URL"

# Production Key Vault that hosts the shared MySQL secret.  Hard
# coded so a fresh ``git clone`` + ``az login`` works without any
# env-var setup: the AKS deploy manifests
# (``deploy/brain-engine-{prod,dev}.yaml``) already pin this same
# URL via the ``AZURE_KEYVAULT_URL`` env var, so production keeps
# overriding through env (the existing source of truth) while
# local devs fall back to this constant.  Not a secret — visible
# in the deploy manifests already committed to this repo.
_DEFAULT_KEY_VAULT_URL: Final[str] = (
    "https://prod-botel-keyvault.vault.azure.net/"
)

# Canonical KV secret name for the MySQL connection string.  Reuses
# CORA's secret since both stacks talk to the same Azure MySQL
# server (see CendraRuleCreator/src/services/key_vault_service.py:
# REQUIRED_KEYS / ENV_MAPPING — entry "AIOptionsDatabaseUrl" →
# "DATABASE_URL").  Override at the call-site by passing a custom
# secrets provider with a different mapping when Bookly.Pms gets a
# dedicated KV secret.
_KV_DATABASE_URL_SECRET: Final[str] = "AIOptionsDatabaseUrl"

# Engine-name → KV-name mapping, mirroring CORA's ENV_MAPPING shape.
# Names absent from this dict are passed through unchanged when
# resolving against KV; in practice that means only the URL has a
# canonical KV home — composed-parameter secrets (HOST / USER /
# PASSWORD / etc.) are env-only on purpose, since production reads
# the full URL.
_DEFAULT_KV_NAMES: Final[Mapping[str, str]] = {
    "BOTEL_MYSQL_URL": _KV_DATABASE_URL_SECRET,
}

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_default_secret_provider: SecretProvider | None = None


# ── Secret-provider plumbing ───────────────────────────────────────


class _KvNameAdapter:
    """Map UPPER_SNAKE_CASE secret names to CORA-style KV names.

    Mirrors the bidirectional mapping in
    :class:`CendraRuleCreator.services.key_vault_service.\
KeyVaultService` (``ENV_MAPPING``): the engine speaks in the
    UPPER_SNAKE_CASE convention everywhere — env vars, function
    arguments, log keys — while the team's existing KV identifiers
    use CamelCase with no separator (``AIOptionsDatabaseUrl``,
    ``AIOptionsAzureOpenaiApiKey``, …).  Names absent from the
    mapping pass through unchanged, so a future caller that adds a
    secret to KV directly does not need a code change.

    Example::

        secrets.get("BOTEL_MYSQL_URL")
        # → KV.get_secret("AIOptionsDatabaseUrl")
    """

    def __init__(
        self,
        inner: SecretProvider,
        mapping: Mapping[str, str] | None = None,
    ) -> None:
        self._inner = inner
        self._mapping: Mapping[str, str] = (
            mapping if mapping is not None else _DEFAULT_KV_NAMES
        )

    def _translate(self, name: str) -> str:
        return self._mapping.get(name, name)

    def get(self, name: str) -> str:
        return self._inner.get(self._translate(name))

    def invalidate(self, name: str) -> None:
        self._inner.invalidate(self._translate(name))


def _default_provider() -> SecretProvider:
    """Build (and cache) the default env-then-KV provider chain.

    Mirrors the CORA ``KeyVaultService`` resolution pattern: env
    short-circuits cheaply (in production the CSI driver projects
    Key Vault into the pod env under UPPER_SNAKE_CASE names), Key
    Vault is only consulted when the env path misses — and when
    consulted, names are auto-translated to KV's hyphen format via
    :class:`_KvNameAdapter`.

    The Key Vault URL resolution order is:

    1. ``AZURE_KEYVAULT_URL`` env var (production override —
       projected into every brainengine pod by deploy/*.yaml).
    2. :data:`_DEFAULT_KEY_VAULT_URL` — the same vault URL,
       hard-coded so a fresh clone with ``az login`` and the
       right RBAC role on the vault can read secrets without any
       extra setup.

    The Azure SDK is imported lazily by
    :class:`KeyVaultSecretProvider`, so callers that bypass the
    KV path entirely (e.g. by passing their own provider into
    :func:`get_engine`) never pay the import cost.
    """
    global _default_secret_provider
    if _default_secret_provider is not None:
        return _default_secret_provider

    providers: list[SecretProvider] = [EnvSecretProvider()]
    vault_url = (
        os.environ.get(_KEY_VAULT_URL_ENV, "").strip()
        or _DEFAULT_KEY_VAULT_URL
    )
    if vault_url:
        providers.append(
            _KvNameAdapter(
                KeyVaultSecretProvider(vault_url=vault_url)
            )
        )

    _default_secret_provider = (
        providers[0]
        if len(providers) == 1
        else CompositeSecretProvider(*providers)
    )
    return _default_secret_provider


def _read(
    secrets: SecretProvider,
    name: str,
    *,
    default: str = "",
) -> str:
    """Look up ``name`` through ``secrets``, returning ``default``.

    The :class:`SecretProvider` Protocol raises ``KeyError`` on
    miss; this helper translates that into a tolerant default so
    URL-composition code stays linear.
    """
    try:
        return secrets.get(name)
    except KeyError:
        return default


# ── URL resolution ──────────────────────────────────────────────────


def build_botel_pms_url(
    *, secrets: SecretProvider | None = None
) -> str:
    """Resolve the async DSN for the Botel/Bookly.Pms database.

    See the module docstring for the env-var priority order.

    Args:
        secrets: Override the default env-then-KV provider chain
            (primarily for tests).  When ``None``, the singleton
            built by :func:`_default_provider` is used.

    Returns:
        DSN normalized to the ``mysql+asyncmy://`` driver prefix.

    Raises:
        BotelPmsConfigError: If neither ``BOTEL_MYSQL_URL`` nor the
            host/user/db trio is fully populated, or if the explicit
            URL has no parseable scheme.
    """
    secrets = secrets or _default_provider()

    explicit = _read(secrets, "BOTEL_MYSQL_URL").strip()
    if explicit:
        return _normalize_driver_prefix(explicit)

    host = _read(secrets, "BOTEL_MYSQL_HOST").strip()
    user = _read(secrets, "BOTEL_MYSQL_USER").strip()
    db = _read(secrets, "BOTEL_MYSQL_DB").strip()
    missing = [
        name
        for name, value in (
            ("BOTEL_MYSQL_HOST", host),
            ("BOTEL_MYSQL_USER", user),
            ("BOTEL_MYSQL_DB", db),
        )
        if not value
    ]
    if missing:
        raise BotelPmsConfigError(
            "Botel PMS MySQL configuration incomplete; missing "
            + ", ".join(missing),
            field=missing[0],
        )

    port = _read(secrets, "BOTEL_MYSQL_PORT", default="3306").strip()
    port = port or "3306"
    password = _read(secrets, "BOTEL_MYSQL_PASSWORD")
    # urllib.parse.quote (not quote_plus) — userinfo encodes
    # spaces as %20; quote_plus's "+" form is form-encoded, not
    # RFC 3986 userinfo, and asyncmy/PyMySQL parses literally.
    user_q = quote(user, safe="")
    password_q = quote(password, safe="")
    base = (
        f"{_ASYNC_DRIVER_PREFIX}{user_q}:{password_q}"
        f"@{host}:{port}/{db}"
    )
    ssl_token = _read(secrets, "BOTEL_MYSQL_SSL").strip().lower()
    if ssl_token in _SSL_REQUIRED_TOKENS:
        base = f"{base}?ssl=required"
    return base


def _normalize_driver_prefix(url: str) -> str:
    """Force the URL onto the ``mysql+asyncmy://`` driver prefix."""
    if url.startswith(_ASYNC_DRIVER_PREFIX):
        return url
    if url.startswith("mysql://"):
        return url.replace("mysql://", _ASYNC_DRIVER_PREFIX, 1)
    if url.startswith("mysql+pymysql://"):
        return url.replace(
            "mysql+pymysql://", _ASYNC_DRIVER_PREFIX, 1
        )
    if re.match(r"^[a-z][a-z0-9+]*://", url):
        logger.warning(
            "botel_pms.url.scheme.coerced",
            url=_mask_password(url),
        )
        return re.sub(
            r"^[^:]+://", _ASYNC_DRIVER_PREFIX, url, count=1
        )
    raise BotelPmsConfigError(
        "BOTEL_MYSQL_URL has no scheme; cannot derive driver",
        field="BOTEL_MYSQL_URL",
    )


# ── Engine + session ────────────────────────────────────────────────


def get_engine(
    *, secrets: SecretProvider | None = None
) -> AsyncEngine:
    """Return the singleton :class:`AsyncEngine`, building lazily.

    Args:
        secrets: Override the default env-then-KV provider chain
            (primarily for tests).  Only consulted on the first
            call; subsequent calls reuse the cached engine.

    Returns:
        Process-wide async engine bound to the Botel PMS server.

    Raises:
        BotelPmsConfigError: If the DSN cannot be assembled.
    """
    global _engine
    if _engine is not None:
        return _engine

    secrets = secrets or _default_provider()
    url = build_botel_pms_url(secrets=secrets)
    connect_args, url_for_create = _ssl_connect_args(url)

    _engine = create_async_engine(
        url_for_create,
        pool_size=_DEFAULT_POOL_SIZE,
        max_overflow=_DEFAULT_MAX_OVERFLOW,
        pool_recycle=_DEFAULT_POOL_RECYCLE_SECONDS,
        pool_pre_ping=True,
        echo=False,
        connect_args=connect_args,
    )
    logger.info(
        "botel_pms.engine.created",
        url=_mask_password(url_for_create),
        pool_size=_DEFAULT_POOL_SIZE,
        max_overflow=_DEFAULT_MAX_OVERFLOW,
        ssl=bool(connect_args.get("ssl")),
    )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the singleton session factory, lazily binding the engine."""
    global _session_factory
    if _session_factory is not None:
        return _session_factory
    _session_factory = async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return _session_factory


def get_session() -> AsyncSession:
    """Return a fresh :class:`AsyncSession` from the shared factory.

    The caller owns the session lifecycle — use as an async context
    manager (``async with get_session() as s: ...``) or close
    explicitly when done.
    """
    return get_session_factory()()


async def dispose_engine() -> None:
    """Close all pooled connections and reset the singletons.

    Required by hot-reload paths and integration tests; production
    code paths should let the process exit handle teardown.  The
    cached secret provider is also cleared so a subsequent
    :func:`get_engine` call re-reads the environment.
    """
    global _engine, _session_factory, _default_secret_provider
    if _engine is not None:
        await _engine.dispose()
        logger.info("botel_pms.engine.disposed")
    _engine = None
    _session_factory = None
    _default_secret_provider = None


# ── Health check ────────────────────────────────────────────────────


async def ping() -> bool:
    """Issue ``SELECT 1`` to confirm the engine can reach the server.

    Returns:
        ``True`` on a successful round-trip.

    Raises:
        BotelPmsConnectionError: If the driver raises any
            :class:`SQLAlchemyError`.  The original error is kept as
            ``__cause__`` for stack-trace forensics.
    """
    engine = get_engine()
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            return result.scalar_one() == 1
    except SQLAlchemyError as exc:
        host = _extract_host(
            engine.url.render_as_string(hide_password=True)
        )
        raise BotelPmsConnectionError(
            f"Botel PMS MySQL ping failed: {exc}",
            host=host,
        ) from exc


# ── SSL helpers ─────────────────────────────────────────────────────


def _ssl_connect_args(url: str) -> tuple[dict, str]:
    """Translate ``ssl=required`` into ``connect_args`` for asyncmy.

    Azure Database for MySQL terminates client connections at a TLS
    edge that presents a public-CA certificate; we keep the channel
    encrypted but skip CA/hostname verification so deployment images
    do not have to bundle the DigiCert chain.

    The SSL flag is deployment configuration, not a secret, so it
    is read directly from the URL and the env var — never from the
    secrets provider.  That keeps the Key Vault path scoped to the
    fields that actually live in KV (just the URL today) and avoids
    hitting Azure with names like ``BOTEL-MYSQL-SSL`` that the team
    has no reason to provision.

    Returns:
        ``(connect_args, url_without_ssl_param)`` — ``ssl`` is
        dropped from the URL because asyncmy only honours it via
        ``connect_args``.
    """
    url_requests_ssl = _has_ssl_required(url)
    cleaned_url = _strip_ssl_query_param(url)

    env_ssl = os.environ.get("BOTEL_MYSQL_SSL", "").strip().lower()
    needs_ssl = (
        url_requests_ssl or env_ssl in _SSL_REQUIRED_TOKENS
    )
    if not needs_ssl:
        return {}, cleaned_url

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return {"ssl": ctx}, cleaned_url


def _has_ssl_required(url: str) -> bool:
    """Return True if the DSN's query string asks for SSL."""
    lowered = url.lower()
    return any(
        f"ssl={token}" in lowered for token in _SSL_REQUIRED_TOKENS
    )


def _strip_ssl_query_param(url: str) -> str:
    """Remove ``?ssl=…`` / ``&ssl=…`` from a DSN query string."""
    cleaned = re.sub(
        r"[?&]ssl=[^&]+",
        "",
        url,
        count=1,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.replace("?&", "?")
    if cleaned.endswith("?"):
        cleaned = cleaned[:-1]
    return cleaned


# ── Logging helpers ─────────────────────────────────────────────────


def _mask_password(url: str) -> str:
    """Redact the password component for safe logging."""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)


def _extract_host(url: str) -> str | None:
    """Pull the host token out of a DSN, ``None`` if not present."""
    match = re.search(r"@([^:/?]+)", url)
    return match.group(1) if match else None
