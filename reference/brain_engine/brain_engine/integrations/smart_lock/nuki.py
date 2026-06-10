"""
Nuki Smart Lock API integration.

Docs: https://developer.nuki.io/page/nuki-web-api-1-4/3
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.nuki.io"


@dataclass(frozen=True)
class LockStatus:
    """Current state of a Nuki smart lock."""

    lock_id: str
    name: str
    state: str  # "locked", "unlocked", "unlatched", etc.
    battery_critical: bool = False
    door_state: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class AccessCode:
    """A temporary access code / keypad code."""

    code_id: str
    code: str
    name: str
    start: datetime | None = None
    end: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class NukiError(Exception):
    """Raised when a Nuki API request fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class NukiLock:
    """
    Async client for the Nuki Web API.

    Usage::

        async with NukiLock(api_token="abc123") as nuki:
            status = await nuki.get_status("12345")
            await nuki.unlock("12345")
            code = await nuki.create_access_code(
                "12345", "Guest", start, end
            )
    """

    def __init__(
        self,
        api_token: str,
        *,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self._api_token = api_token
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {self._api_token}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout),
        )

    async def __aenter__(self) -> NukiLock:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def unlock(self, lock_id: str) -> LockStatus:
        """Unlock a smart lock.

        Args:
            lock_id: The Nuki smart lock identifier.

        Returns:
            Updated :class:`LockStatus`.
        """
        await self._post(f"/smartlock/{lock_id}/action/unlock", {})
        return await self.get_status(lock_id)

    async def lock(self, lock_id: str) -> LockStatus:
        """Lock a smart lock.

        Args:
            lock_id: The Nuki smart lock identifier.
        """
        await self._post(f"/smartlock/{lock_id}/action/lock", {})
        return await self.get_status(lock_id)

    async def get_status(self, lock_id: str) -> LockStatus:
        """Get the current status of a smart lock.

        Args:
            lock_id: The Nuki smart lock identifier.
        """
        data = await self._get(f"/smartlock/{lock_id}")
        state_map = {
            1: "locked",
            2: "unlocking",
            3: "unlocked",
            4: "locking",
            5: "unlatched",
            254: "motor_blocked",
            255: "undefined",
        }
        state_data = data.get("state", {})
        state_num = state_data.get("state", 255)
        return LockStatus(
            lock_id=lock_id,
            name=data.get("name", ""),
            state=state_map.get(state_num, "unknown"),
            battery_critical=state_data.get("batteryCritical", False),
            door_state=str(state_data.get("doorState", "")),
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
        """Create a temporary keypad access code.

        Args:
            lock_id: The Nuki smart lock identifier.
            name: Human-readable label for the code.
            start: When the code becomes valid.
            end: When the code expires.
            code: Optional specific code; auto-generated if omitted.
        """
        payload: dict[str, Any] = {
            "name": name,
            "type": 0,  # Access code
            "allowedFromDate": start.isoformat(),
            "allowedUntilDate": end.isoformat(),
        }
        if code:
            payload["code"] = int(code)

        data = await self._put(f"/smartlock/{lock_id}/auth", payload)
        return AccessCode(
            code_id=str(data.get("id", "")),
            code=str(data.get("code", "")),
            name=name,
            start=start,
            end=end,
            raw=data,
        )

    async def get_activity_log(
        self,
        lock_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch the recent activity log for a smart lock.

        Args:
            lock_id: The Nuki smart lock identifier.
            limit: Maximum number of log entries to return.

        Returns:
            List of activity log entries (most recent first).
        """
        data = await self._get(f"/smartlock/{lock_id}/log?limit={limit}")
        if isinstance(data, list):
            return data
        return data.get("logs", data.get("items", []))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            response = await self._client.get(path)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            logger.error("Nuki GET %s failed: %s", path, exc.response.text)
            raise NukiError(
                f"API error on GET {path}: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("Nuki GET %s network error: %s", path, exc)
            raise NukiError(f"Network error on GET {path}: {exc}") from exc

    async def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=json)
            response.raise_for_status()
            if response.status_code == 204:
                return {}
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            logger.error("Nuki POST %s failed: %s", path, exc.response.text)
            raise NukiError(
                f"API error on POST {path}: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("Nuki POST %s network error: %s", path, exc)
            raise NukiError(f"Network error on POST {path}: {exc}") from exc

    async def _put(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.put(path, json=json)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            logger.error("Nuki PUT %s failed: %s", path, exc.response.text)
            raise NukiError(
                f"API error on PUT {path}: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("Nuki PUT %s network error: %s", path, exc)
            raise NukiError(f"Network error on PUT {path}: {exc}") from exc
