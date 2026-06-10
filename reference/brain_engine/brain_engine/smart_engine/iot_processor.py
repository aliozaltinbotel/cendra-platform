"""IoT Event Processor — connects Seam device events to Brain Engine.

Processes IoT events from smart locks, thermostats, and sensors.
Routes events to AutomationEngine for rule-based actions and
stores events for pattern detection.

Supported device types (via Seam API):
    - Smart Locks: lock/unlock events → security monitoring
    - Thermostats: temperature readings → climate control
    - Sensors: motion, door open/close → occupancy detection

Event flow:
    Seam webhook → POST /api/v1/iot/event → IoTProcessor → AutomationEngine
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from brain_engine.smart_engine.automation_rules import (
    AutomationEngine,
    AutomationEvent,
    AutomationResult,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IoTEvent:
    """An event from an IoT device.

    Attributes:
        device_id: Device identifier (Seam device ID).
        device_type: Device type (smart_lock, thermostat, sensor).
        event_type: Specific event (lock.locked, lock.unlocked, etc.).
        property_id: Property where device is installed.
        timestamp: ISO timestamp of event.
        data: Event-specific data.
    """

    device_id: str
    device_type: str
    event_type: str
    property_id: str = ""
    timestamp: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IoTProcessingResult:
    """Result of processing an IoT event.

    Attributes:
        event_type: Original event type.
        property_id: Property context.
        device_type: Device type.
        is_anomaly: Whether this event is unusual.
        anomaly_reason: Why it's an anomaly (if applicable).
        automation_result: Actions from AutomationEngine.
        alerts: Alert messages generated.
    """

    event_type: str = ""
    property_id: str = ""
    device_type: str = ""
    is_anomaly: bool = False
    anomaly_reason: str = ""
    automation_result: AutomationResult | None = None
    alerts: list[str] = field(default_factory=list)


class IoTProcessor:
    """Processes IoT events and routes to automations.

    Analyzes events for anomalies (unexpected unlock, temp spike)
    and forwards to AutomationEngine for rule-based actions.

    Args:
        automation_engine: Automation rules engine.
        vacancy_checker: Callable that checks if property is vacant.
    """

    def __init__(
        self,
        automation_engine: AutomationEngine | None = None,
    ) -> None:
        self._automation = automation_engine or AutomationEngine()
        self._vacancy_cache: dict[str, bool] = {}

    def set_vacancy(self, property_id: str, is_vacant: bool) -> None:
        """Update vacancy status for a property.

        Args:
            property_id: Property identifier.
            is_vacant: Whether property is currently vacant.
        """
        self._vacancy_cache[property_id] = is_vacant

    def process(self, event: IoTEvent) -> IoTProcessingResult:
        """Process an IoT event through anomaly detection and automation.

        Args:
            event: IoT device event.

        Returns:
            Processing result with anomalies and actions.
        """
        result = IoTProcessingResult(
            event_type=event.event_type,
            property_id=event.property_id,
            device_type=event.device_type,
        )

        self._check_anomaly(event, result)
        self._route_to_automation(event, result)

        logger.info(
            "IoT event %s from %s at %s: anomaly=%s, actions=%d",
            event.event_type,
            event.device_type,
            event.property_id,
            result.is_anomaly,
            len(result.automation_result.actions) if result.automation_result else 0,
        )
        return result

    def _check_anomaly(
        self,
        event: IoTEvent,
        result: IoTProcessingResult,
    ) -> None:
        """Check if event is anomalous.

        Args:
            event: IoT event to check.
            result: Result to update with anomaly info.
        """
        is_vacant = self._vacancy_cache.get(event.property_id, False)

        if event.event_type == "lock.unlocked" and is_vacant:
            result.is_anomaly = True
            result.anomaly_reason = "Door unlocked during vacant period"
            result.alerts.append(
                f"ALERT: Unexpected unlock at {event.property_id}",
            )

        if event.event_type == "temperature.spike":
            temp = event.data.get("temperature", 0)
            if isinstance(temp, (int, float)) and temp > 35:
                result.is_anomaly = True
                result.anomaly_reason = f"Temperature spike: {temp}°C"
                result.alerts.append(
                    f"ALERT: High temperature {temp}°C at {event.property_id}",
                )

    def _route_to_automation(
        self,
        event: IoTEvent,
        result: IoTProcessingResult,
    ) -> None:
        """Route IoT event to AutomationEngine.

        Args:
            event: IoT event.
            result: Result to update with automation actions.
        """
        is_vacant = self._vacancy_cache.get(event.property_id, False)

        auto_event = AutomationEvent(
            event_type=event.event_type,
            property_id=event.property_id,
            event_data={
                **event.data,
                "device_id": event.device_id,
                "device_type": event.device_type,
                "is_vacant": is_vacant,
            },
        )

        result.automation_result = self._automation.process(auto_event)
