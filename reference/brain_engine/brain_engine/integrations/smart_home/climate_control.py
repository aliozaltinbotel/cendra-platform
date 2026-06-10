"""Smart home climate control via Sensibo API.

Provides AC/heating control for guest comfort automation.
Supports pre-cooling/heating based on guest ETA.

Docs: https://sensibo.github.io/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SENSIBO_BASE_URL = "https://home.sensibo.com/api/v2"


@dataclass(frozen=True)
class ClimateState:
    """Current state of a climate control device."""
    device_id: str
    temperature_current: float = 0.0
    temperature_target: float = 22.0
    humidity: float = 0.0
    mode: str = "cool"  # cool, heat, fan, dry, auto
    fan_speed: str = "auto"
    is_on: bool = False
    last_updated: str = ""


@dataclass
class ClimateCommand:
    """Command to set climate state."""
    temperature: float | None = None
    mode: str | None = None
    fan_speed: str | None = None
    on: bool | None = None


class ClimateControlError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ClimateControlClient:
    """Sensibo smart AC client.

    Usage::

        async with ClimateControlClient(api_key="...") as climate:
            state = await climate.get_state("device123")
            await climate.prepare_for_guest("device123", target_temp=22.0)
    """

    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=SENSIBO_BASE_URL,
            timeout=timeout,
        )

    async def get_state(self, device_id: str) -> ClimateState:
        """Get current state of a climate device."""
        try:
            response = await self._client.get(
                f"/pods/{device_id}",
                params={
                    "apiKey": self.api_key,
                    "fields": "acState,measurements",
                },
            )
            response.raise_for_status()
            data = response.json().get("result", {})

            ac = data.get("acState", {})
            meas = data.get("measurements", {})

            return ClimateState(
                device_id=device_id,
                temperature_current=meas.get("temperature", 0),
                temperature_target=ac.get("targetTemperature", 22),
                humidity=meas.get("humidity", 0),
                mode=ac.get("mode", "cool"),
                fan_speed=ac.get("fanLevel", "auto"),
                is_on=ac.get("on", False),
                last_updated=datetime.now(timezone.utc).isoformat(),
            )
        except httpx.HTTPStatusError as exc:
            raise ClimateControlError(
                f"Get state failed: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc

    async def set_climate(self, device_id: str, command: ClimateCommand) -> ClimateState:
        """Set climate control parameters."""
        ac_state: dict[str, Any] = {}
        if command.on is not None:
            ac_state["on"] = command.on
        if command.temperature is not None:
            ac_state["targetTemperature"] = command.temperature
        if command.mode is not None:
            ac_state["mode"] = command.mode
        if command.fan_speed is not None:
            ac_state["fanLevel"] = command.fan_speed

        try:
            response = await self._client.post(
                f"/pods/{device_id}/acStates",
                params={"apiKey": self.api_key},
                json={"acState": ac_state},
            )
            response.raise_for_status()

            logger.info("Climate set for %s: %s", device_id, ac_state)
            return await self.get_state(device_id)

        except httpx.HTTPStatusError as exc:
            raise ClimateControlError(
                f"Set climate failed: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc

    async def prepare_for_guest(
        self,
        device_id: str,
        target_temp: float = 22.0,
        minutes_before_arrival: int = 30,
    ) -> dict[str, Any]:
        """Smart pre-cooling/heating for guest arrival.

        Turns on the AC and sets it to the target temperature.
        Should be called `minutes_before_arrival` minutes before the guest arrives.
        """
        current = await self.get_state(device_id)
        temp_diff = abs(current.temperature_current - target_temp)

        # Decide mode based on current vs target
        if current.temperature_current > target_temp + 1:
            mode = "cool"
        elif current.temperature_current < target_temp - 1:
            mode = "heat"
        else:
            mode = "auto"

        # Set aggressive fan for large temp differences
        fan = "high" if temp_diff > 5 else "auto"

        await self.set_climate(device_id, ClimateCommand(
            on=True,
            temperature=target_temp,
            mode=mode,
            fan_speed=fan,
        ))

        logger.info(
            "Preparing for guest: %s → %s°C (%s mode, fan=%s, diff=%.1f°C)",
            device_id, target_temp, mode, fan, temp_diff,
        )

        return {
            "device_id": device_id,
            "action": "prepare_for_guest",
            "target_temp": target_temp,
            "current_temp": current.temperature_current,
            "mode": mode,
            "fan_speed": fan,
            "pre_cool_minutes": minutes_before_arrival,
        }

    async def turn_off(self, device_id: str) -> ClimateState:
        """Turn off the climate control device."""
        return await self.set_climate(device_id, ClimateCommand(on=False))

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> ClimateControlClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
