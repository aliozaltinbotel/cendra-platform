"""
RemoteLock API integration - alternative smart lock provider.

Docs: https://developer.remotelock.com/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.remotelock.com"


@dataclass(frozen=True)
class LockStatus:
    """Current state of a RemoteLock device."""

    lock_id: str
    name: str
    state: str  # "locked", "unlocked"
    battery_level: int | None = None
    connectivity: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class AccessCode:
    """A temporary PIN / access code on a RemoteLock device."""

    code_id: str
    code: str
    name: str
    start: datetime | None = None
    end: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class RemoteLockError(Exception):
    """Raised when a RemoteLock API request fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RemoteLock:
    """
    Async client for the RemoteLock API.

    Drop-in alternative to :class:`~brain_engine.integrations.smart_lock.nuki.NukiLock`.

    Usage::

        async with RemoteLock(access_token="tok_...") as rl:
            status = await rl.get_status("device-uuid")
            await rl.unlock("device-uuid")
            code = await rl.create_access_code(
                "device-uuid", "Guest", start, end
            )
    """

    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self._access_token = access_token
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/vnd.lockstate.v1+json",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout),
        )

    async def __aenter__(self) -> RemoteLock:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def unlock(self, lock_id: str) -> LockStatus:
        """Remotely unlock a device.

        Args:
            lock_id: RemoteLock device UUID.
        """
        await self._put(f"/devices/{lock_id}/unlock", {})
        return await self.get_status(lock_id)

    async def lock(self, lock_id: str) -> LockStatus:
        """Remotely lock a device.

        Args:
            lock_id: RemoteLock device UUID.
        """
        await self._put(f"/devices/{lock_id}/lock", {})
        return await self.get_status(lock_id)

    async def get_status(self, lock_id: str) -> LockStatus:
        """Get the current status of a lock.

        Args:
            lock_id: RemoteLock device UUID.
        """
        data = await self._get(f"/devices/{lock_id}")
        attributes = data.get("data", {}).get("attributes", {})
        return LockStatus(
            lock_id=lock_id,
            name=attributes.get("name", ""),
            state=attributes.get("lock_state", "unknown"),
            battery_level=attributes.get("battery_level"),
            connectivity=attributes.get("connectivity", "unknown"),
            raw=data,
        )

    async def create_access_code(
        self,
        lock_id: str,
        name: str,
        start: datetime,
        end: datetime,
        *,
        code: str | None = None,
    ) -> AccessCode:
        """Create a temporary access guest on a lock.

        Args:
            lock_id: RemoteLock device UUID.
            name: Guest name / label.
            start: Start of access window.
            end: End of access window.
            code: Optional specific PIN; auto-generated if omitted.
        """
        payload: dict[str, Any] = {
            "type": "access_guest",
            "attributes": {
                "name": name,
                "starts_at": start.isoformat(),
                "ends_at": end.isoformat(),
            },
        }
        if code:
            payload["attributes"]["pin"] = code

        data = await self._post(f"/devices/{lock_id}/access_persons", payload)
        attrs = data.get("data", {}).get("attributes", {})
        return AccessCode(
            code_id=data.get("data", {}).get("id", ""),
            code=str(attrs.get("pin", "")),
            name=name,
            start=start,
            end=end,
            raw=data,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            response = await self._client.get(path)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            logger.error("RemoteLock GET %s failed: %s", path, exc.response.text)
            raise RemoteLockError(
                f"API error on GET {path}: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("RemoteLock GET %s network error: %s", path, exc)
            raise RemoteLockError(f"Network error on GET {path}: {exc}") from exc

    async def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=json)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            logger.error("RemoteLock POST %s failed: %s", path, exc.response.text)
            raise RemoteLockError(
                f"API error on POST {path}: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("RemoteLock POST %s network error: %s", path, exc)
            raise RemoteLockError(f"Network error on POST {path}: {exc}") from exc

    async def _put(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.put(path, json=json)
            response.raise_for_status()
            if response.status_code == 204:
                return {}
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            logger.error("RemoteLock PUT %s failed: %s", path, exc.response.text)
            raise RemoteLockError(
                f"API error on PUT {path}: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("RemoteLock PUT %s network error: %s", path, exc)
            raise RemoteLockError(f"Network error on PUT {path}: {exc}") from exc
