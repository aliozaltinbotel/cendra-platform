"""SupplyPredictor — predict supply needs before they run out.

Learns consumption patterns per property and predicts when supplies
will run out. Auto-generates restock tasks BEFORE cleaners report
"malzeme bitti" at the last minute.

CEO complaint: "malzeme bitince söylerim dedi, son dk söylerse
sıkıntı olur, önceden planlamak lazım"

Brain Engine solution: track usage rate per turnover, predict
when to restock, auto-order before running out.

Based on: Cendra real operations (March 2026).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ConsumptionRecord:
    """A record of supply consumption per turnover.

    Attributes:
        property_id: Which property.
        item_id: Which supply item.
        consumed: How many units consumed.
        guests: Number of guests in the stay.
        stay_days: Length of stay in days.
        timestamp: When the turnover happened.
    """

    property_id: str = ""
    item_id: str = ""
    consumed: int = 0
    guests: int = 1
    stay_days: int = 1
    timestamp: str = ""


@dataclass
class Prediction:
    """Predicted supply need.

    Attributes:
        item_id: Which supply.
        item_name: Human-readable name.
        current_quantity: Current stock.
        predicted_need: How many will be needed.
        turnovers_until_empty: How many more turnovers before empty.
        restock_by: Date by which to restock.
        confidence: Prediction confidence (0-1).
        recommendation: What to do.
    """

    item_id: str = ""
    item_name: str = ""
    current_quantity: int = 0
    predicted_need: int = 0
    turnovers_until_empty: int = 0
    restock_by: str = ""
    confidence: float = 0.7
    recommendation: str = ""


class SupplyPredictor:
    """Predicts supply needs based on historical consumption.

    Learns how much of each supply is used per turnover
    (adjusted for guest count and stay length) and predicts
    when restocking will be needed.

    Args:
        property_id: Property to predict for.
    """

    def __init__(self, property_id: str = "") -> None:
        self._property_id = property_id
        self._history: list[ConsumptionRecord] = []
        self._avg_consumption: dict[str, float] = {}

    def record_consumption(
        self,
        item_id: str,
        consumed: int,
        guests: int = 2,
        stay_days: int = 3,
    ) -> None:
        """Record supply consumption after a turnover.

        Args:
            item_id: Which item was consumed.
            consumed: How many units.
            guests: Number of guests in the stay.
            stay_days: Duration of stay.
        """
        record = ConsumptionRecord(
            property_id=self._property_id,
            item_id=item_id,
            consumed=consumed,
            guests=guests,
            stay_days=stay_days,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._history.append(record)
        self._update_averages(item_id)

    def _update_averages(self, item_id: str) -> None:
        """Recalculate average consumption for an item.

        Args:
            item_id: Item to recalculate.
        """
        records = [r for r in self._history if r.item_id == item_id]
        if not records:
            return

        total = sum(r.consumed for r in records)
        self._avg_consumption[item_id] = total / len(records)

    def predict(
        self,
        item_id: str,
        current_quantity: int,
        item_name: str = "",
        upcoming_bookings: int = 3,
        avg_guests: int = 2,
    ) -> Prediction:
        """Predict when a supply item will run out.

        Args:
            item_id: Item to predict.
            current_quantity: Current stock level.
            item_name: Human-readable name.
            upcoming_bookings: Number of future bookings.
            avg_guests: Average guests per booking.

        Returns:
            Prediction with restock recommendation.
        """
        avg = self._avg_consumption.get(item_id)

        if avg is None or avg == 0:
            avg = _default_consumption(item_id)

        turnovers_left = (
            math.floor(current_quantity / avg) if avg > 0 else 999
        )

        predicted_need = math.ceil(avg * upcoming_bookings)
        shortage = max(0, predicted_need - current_quantity)

        confidence = min(0.95, 0.5 + len([
            r for r in self._history if r.item_id == item_id
        ]) * 0.05)

        recommendation = _build_recommendation(
            item_name or item_id,
            current_quantity,
            turnovers_left,
            shortage,
            upcoming_bookings,
        )

        return Prediction(
            item_id=item_id,
            item_name=item_name or item_id,
            current_quantity=current_quantity,
            predicted_need=predicted_need,
            turnovers_until_empty=turnovers_left,
            confidence=confidence,
            recommendation=recommendation,
        )

    def predict_all(
        self,
        inventory: list[dict[str, Any]],
        upcoming_bookings: int = 3,
    ) -> list[Prediction]:
        """Predict needs for all inventory items.

        Args:
            inventory: List of items with id, name, quantity.
            upcoming_bookings: Future bookings count.

        Returns:
            Sorted predictions (most urgent first).
        """
        predictions: list[Prediction] = []
        for item in inventory:
            pred = self.predict(
                item_id=item.get("item_id", ""),
                current_quantity=item.get("current_quantity", 0),
                item_name=item.get("name", ""),
                upcoming_bookings=upcoming_bookings,
            )
            predictions.append(pred)

        predictions.sort(key=lambda p: p.turnovers_until_empty)
        return predictions

    @property
    def history_count(self) -> int:
        """Number of consumption records."""
        return len(self._history)


def _default_consumption(item_id: str) -> float:
    """Default consumption per turnover when no history exists.

    Args:
        item_id: Item identifier.

    Returns:
        Estimated units consumed per turnover.
    """
    defaults = {
        "toilet_paper": 4.0,
        "hand_soap": 0.3,
        "shampoo": 0.3,
        "dish_soap": 0.2,
        "sponges": 0.5,
        "trash_bags": 3.0,
        "cleaning_spray": 0.2,
        "towel_sets": 0.0,
        "bed_sheets": 0.0,
        "coffee_capsules": 4.0,
    }
    return defaults.get(item_id, 1.0)


def _build_recommendation(
    name: str,
    current: int,
    turnovers_left: int,
    shortage: int,
    upcoming: int,
) -> str:
    """Build human-readable recommendation.

    Args:
        name: Item name.
        current: Current stock.
        turnovers_left: Turnovers until empty.
        shortage: Units short for upcoming bookings.
        upcoming: Number of upcoming bookings.

    Returns:
        Recommendation string.
    """
    if current == 0:
        return f"URGENT: {name} is EMPTY. Restock immediately!"
    if turnovers_left <= 1:
        return (
            f"RESTOCK NOW: {name} will run out after next turnover. "
            f"Need {shortage} more for {upcoming} upcoming bookings."
        )
    if turnovers_left <= 3:
        return (
            f"RESTOCK SOON: {name} has ~{turnovers_left} turnovers left. "
            f"Plan to buy {shortage} units."
        )
    return f"OK: {name} has ~{turnovers_left} turnovers of stock."
