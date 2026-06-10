"""SupplyTracker — cleaner expense and inventory management.

Tracks consumable supplies per property (toilet paper, soap, towels,
cleaning products). Cleaners report expenses, system tracks levels,
alerts when restocking needed, and generates expense reports for owners.

Real scenario from Cendra CEO:
    CEO: "harcama yaptın mı hic toilet kağıdı fln"
    Aynur: "malzeme bitince söylerim"
    CEO: "son dk söylerse sıkıntı olur, önceden planlamak lazım"

Brain Engine solution: proactive supply monitoring + auto-restock alerts.

Based on: Cendra real operations (March 2026 CEO screenshots).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class SupplyCategory(StrEnum):
    """Categories of property supplies."""

    BATHROOM = "bathroom"
    KITCHEN = "kitchen"
    CLEANING = "cleaning"
    BEDROOM = "bedroom"
    GENERAL = "general"


class SupplyStatus(StrEnum):
    """Supply stock levels."""

    FULL = "full"
    ADEQUATE = "adequate"
    LOW = "low"
    EMPTY = "empty"


@dataclass
class SupplyItem:
    """A trackable supply item for a property.

    Attributes:
        item_id: Unique identifier.
        name: Item name (e.g. "toilet paper").
        category: Supply category.
        property_id: Which property this is for.
        current_quantity: Current stock level.
        min_quantity: Restock threshold.
        unit: Unit of measurement (rolls, bottles, sets).
        cost_per_unit: Average cost per unit (EUR).
        auto_restock: Whether to auto-generate restock tasks.
        last_restocked: When last restocked.
        supplier_note: Where to buy / preferred brand.
    """

    item_id: str = ""
    name: str = ""
    category: SupplyCategory = SupplyCategory.GENERAL
    property_id: str = ""
    current_quantity: int = 0
    min_quantity: int = 2
    unit: str = "units"
    cost_per_unit: float = 0.0
    auto_restock: bool = True
    last_restocked: str = ""
    supplier_note: str = ""

    @property
    def status(self) -> SupplyStatus:
        """Calculate current stock status.

        Returns:
            Stock level status.
        """
        if self.current_quantity <= 0:
            return SupplyStatus.EMPTY
        if self.current_quantity <= self.min_quantity:
            return SupplyStatus.LOW
        if self.current_quantity <= self.min_quantity * 2:
            return SupplyStatus.ADEQUATE
        return SupplyStatus.FULL

    @property
    def needs_restock(self) -> bool:
        """Whether this item needs restocking."""
        return self.current_quantity <= self.min_quantity


@dataclass
class ExpenseRecord:
    """A cleaner expense for supplies.

    Attributes:
        expense_id: Unique identifier.
        cleaner_id: Who spent the money.
        cleaner_name: Human-readable name.
        property_id: For which property.
        items: List of purchased items with quantities.
        total_amount: Total cost.
        currency: Currency code.
        receipt_photo: URL/path to receipt photo.
        reported_at: When the expense was reported.
        approved: Whether owner approved reimbursement.
        reimbursed: Whether cleaner was reimbursed.
    """

    expense_id: str = ""
    cleaner_id: str = ""
    cleaner_name: str = ""
    property_id: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    total_amount: float = 0.0
    currency: str = "EUR"
    receipt_photo: str = ""
    reported_at: str = ""
    approved: bool = False
    reimbursed: bool = False


@dataclass
class RestockAlert:
    """Alert when supplies need restocking.

    Attributes:
        property_id: Which property.
        items: List of items needing restock.
        urgency: How urgent (low, medium, high).
        next_checkin: When the next guest arrives.
        message: Human-readable alert message.
    """

    property_id: str = ""
    items: list[SupplyItem] = field(default_factory=list)
    urgency: str = "medium"
    next_checkin: str = ""
    message: str = ""


class SupplyTracker:
    """Tracks property supplies and cleaner expenses.

    Maintains inventory per property, processes expense reports
    from cleaners, generates restock alerts, and produces
    expense summaries for owners.

    Args:
        property_id: Property to track.
    """

    def __init__(self, property_id: str = "") -> None:
        self._property_id = property_id
        self._inventory: dict[str, SupplyItem] = {}
        self._expenses: list[ExpenseRecord] = []
        self._default_items = _get_default_items(property_id)

    def initialize_inventory(
        self,
        items: list[SupplyItem] | None = None,
    ) -> list[SupplyItem]:
        """Set up initial inventory with default or custom items.

        Args:
            items: Custom items, or None for defaults.

        Returns:
            List of initialized items.
        """
        for item in (items or self._default_items):
            item.property_id = self._property_id
            self._inventory[item.item_id] = item
        return list(self._inventory.values())

    def update_quantity(
        self,
        item_id: str,
        quantity: int,
    ) -> SupplyItem | None:
        """Update the current quantity of a supply item.

        Args:
            item_id: Item to update.
            quantity: New quantity.

        Returns:
            Updated item or None if not found.
        """
        item = self._inventory.get(item_id)
        if item is None:
            return None
        item.current_quantity = quantity
        logger.info(
            "Supply updated: %s = %d %s (status: %s)",
            item.name, quantity, item.unit, item.status,
        )
        return item

    def consume(
        self,
        item_id: str,
        quantity: int = 1,
    ) -> SupplyItem | None:
        """Record consumption of a supply item.

        Args:
            item_id: Item consumed.
            quantity: How many consumed.

        Returns:
            Updated item or None.
        """
        item = self._inventory.get(item_id)
        if item is None:
            return None
        item.current_quantity = max(0, item.current_quantity - quantity)
        return item

    def restock(
        self,
        item_id: str,
        quantity: int,
    ) -> SupplyItem | None:
        """Record restocking of a supply item.

        Args:
            item_id: Item restocked.
            quantity: How many added.

        Returns:
            Updated item or None.
        """
        item = self._inventory.get(item_id)
        if item is None:
            return None
        item.current_quantity += quantity
        item.last_restocked = datetime.now(timezone.utc).isoformat()
        return item

    def record_expense(
        self,
        cleaner_id: str,
        cleaner_name: str,
        items: list[dict[str, Any]],
        total_amount: float,
        currency: str = "EUR",
        receipt_photo: str = "",
    ) -> ExpenseRecord:
        """Record a cleaner's expense for supplies.

        Also updates inventory for purchased items.

        Args:
            cleaner_id: Who spent.
            cleaner_name: Name.
            items: What was bought.
            total_amount: Total cost.
            currency: Currency.
            receipt_photo: Receipt URL.

        Returns:
            Created ExpenseRecord.
        """
        record = ExpenseRecord(
            expense_id=f"exp_{len(self._expenses) + 1}",
            cleaner_id=cleaner_id,
            cleaner_name=cleaner_name,
            property_id=self._property_id,
            items=items,
            total_amount=total_amount,
            currency=currency,
            receipt_photo=receipt_photo,
            reported_at=datetime.now(timezone.utc).isoformat(),
        )
        self._expenses.append(record)

        for purchased in items:
            item_id = purchased.get("item_id", "")
            qty = purchased.get("quantity", 0)
            if item_id and qty:
                self.restock(item_id, qty)

        logger.info(
            "Expense recorded: %s spent %.2f %s on %d items",
            cleaner_name, total_amount, currency, len(items),
        )
        return record

    def check_levels(self) -> RestockAlert | None:
        """Check all supply levels and generate alert if needed.

        Returns:
            RestockAlert if any items are low, None otherwise.
        """
        low_items = [
            item for item in self._inventory.values()
            if item.needs_restock
        ]
        if not low_items:
            return None

        urgency = "high" if any(
            i.status == SupplyStatus.EMPTY for i in low_items
        ) else "medium"

        message = _build_alert_message(low_items)

        return RestockAlert(
            property_id=self._property_id,
            items=low_items,
            urgency=urgency,
            message=message,
        )

    def get_expense_summary(
        self,
        period_days: int = 30,
    ) -> dict[str, Any]:
        """Generate expense summary for owner reporting.

        Args:
            period_days: How many days to include.

        Returns:
            Summary dict with totals, by-cleaner, by-category.
        """
        total = sum(e.total_amount for e in self._expenses)
        by_cleaner: dict[str, float] = {}
        for exp in self._expenses:
            by_cleaner[exp.cleaner_name] = (
                by_cleaner.get(exp.cleaner_name, 0) + exp.total_amount
            )

        return {
            "property_id": self._property_id,
            "period_days": period_days,
            "total_expenses": total,
            "expense_count": len(self._expenses),
            "by_cleaner": by_cleaner,
            "pending_reimbursement": sum(
                e.total_amount for e in self._expenses
                if not e.reimbursed
            ),
        }

    def parse_expense_from_message(
        self,
        message: str,
        cleaner_id: str = "",
        cleaner_name: str = "",
    ) -> ExpenseRecord | None:
        """Parse a natural language expense message from cleaner.

        Handles messages like:
        - "Toilet kağıdı aldım 25 TL"
        - "Bought soap and shampoo, 15 euro"
        - "harcama: deterjan 10 TL, çöp poşeti 5 TL"

        Args:
            message: Cleaner's message about expenses.
            cleaner_id: Cleaner identifier.
            cleaner_name: Cleaner name.

        Returns:
            Parsed ExpenseRecord or None if no expense detected.
        """
        parsed = _parse_expense_text(message)
        if not parsed:
            return None

        return self.record_expense(
            cleaner_id=cleaner_id,
            cleaner_name=cleaner_name,
            items=parsed["items"],
            total_amount=parsed["total"],
            currency=parsed["currency"],
        )

    @property
    def inventory(self) -> list[SupplyItem]:
        """All tracked items."""
        return list(self._inventory.values())

    @property
    def low_items(self) -> list[SupplyItem]:
        """Items that need restocking."""
        return [i for i in self._inventory.values() if i.needs_restock]

    @property
    def total_expenses(self) -> float:
        """Total expenses recorded."""
        return sum(e.total_amount for e in self._expenses)


# ── Default inventory ────────────────────────────────────────────────── #


def _get_default_items(property_id: str) -> list[SupplyItem]:
    """Standard supply items for a vacation rental.

    Args:
        property_id: Property identifier.

    Returns:
        Default supply items.
    """
    return [
        SupplyItem(
            item_id="toilet_paper", name="Toilet Paper",
            category=SupplyCategory.BATHROOM,
            current_quantity=8, min_quantity=4,
            unit="rolls", cost_per_unit=0.50,
        ),
        SupplyItem(
            item_id="hand_soap", name="Hand Soap",
            category=SupplyCategory.BATHROOM,
            current_quantity=3, min_quantity=1,
            unit="bottles", cost_per_unit=2.00,
        ),
        SupplyItem(
            item_id="shampoo", name="Shampoo",
            category=SupplyCategory.BATHROOM,
            current_quantity=2, min_quantity=1,
            unit="bottles", cost_per_unit=3.00,
        ),
        SupplyItem(
            item_id="dish_soap", name="Dish Soap",
            category=SupplyCategory.KITCHEN,
            current_quantity=2, min_quantity=1,
            unit="bottles", cost_per_unit=2.50,
        ),
        SupplyItem(
            item_id="sponges", name="Kitchen Sponges",
            category=SupplyCategory.KITCHEN,
            current_quantity=4, min_quantity=2,
            unit="pieces", cost_per_unit=0.80,
        ),
        SupplyItem(
            item_id="trash_bags", name="Trash Bags",
            category=SupplyCategory.GENERAL,
            current_quantity=10, min_quantity=5,
            unit="bags", cost_per_unit=0.30,
        ),
        SupplyItem(
            item_id="cleaning_spray", name="All-Purpose Cleaner",
            category=SupplyCategory.CLEANING,
            current_quantity=2, min_quantity=1,
            unit="bottles", cost_per_unit=4.00,
        ),
        SupplyItem(
            item_id="towel_sets", name="Towel Sets",
            category=SupplyCategory.BEDROOM,
            current_quantity=4, min_quantity=2,
            unit="sets", cost_per_unit=8.00,
        ),
        SupplyItem(
            item_id="bed_sheets", name="Bed Sheet Sets",
            category=SupplyCategory.BEDROOM,
            current_quantity=3, min_quantity=2,
            unit="sets", cost_per_unit=15.00,
        ),
        SupplyItem(
            item_id="coffee_capsules", name="Coffee Capsules",
            category=SupplyCategory.KITCHEN,
            current_quantity=10, min_quantity=5,
            unit="capsules", cost_per_unit=0.40,
        ),
    ]


def _build_alert_message(items: list[SupplyItem]) -> str:
    """Build human-readable restock alert message.

    Args:
        items: Low-stock items.

    Returns:
        Alert message for manager/cleaner.
    """
    lines = ["Supply restock needed:"]
    for item in items:
        status = "EMPTY" if item.status == SupplyStatus.EMPTY else "LOW"
        lines.append(
            f"  - {item.name}: {item.current_quantity} {item.unit} "
            f"({status}, min: {item.min_quantity})"
        )
    return "\n".join(lines)


import re


def _parse_expense_text(
    message: str,
) -> dict[str, Any] | None:
    """Parse natural language expense message.

    Handles Turkish, English, Spanish expense messages.

    Args:
        message: Raw message text.

    Returns:
        Dict with items, total, currency or None.
    """
    lower = message.lower()

    amounts = re.findall(
        r"(\d+(?:[.,]\d+)?)\s*(?:tl|lira|eur|euro|€|\$|usd)",
        lower,
    )
    if not amounts:
        return None

    total = sum(float(a.replace(",", ".")) for a in amounts)

    currency = "TL"
    if "eur" in lower or "€" in lower or "euro" in lower:
        currency = "EUR"
    elif "$" in lower or "usd" in lower:
        currency = "USD"

    items = _detect_supply_items(lower)

    return {
        "items": items,
        "total": total,
        "currency": currency,
    }


def _detect_supply_items(text: str) -> list[dict[str, Any]]:
    """Detect supply items mentioned in text.

    Args:
        text: Lowercased message.

    Returns:
        List of item dicts.
    """
    item_keywords = {
        "toilet_paper": ["toilet", "tuvalet", "kağıd", "papel"],
        "hand_soap": ["soap", "sabun", "jabón"],
        "shampoo": ["shampoo", "şampuan", "champú"],
        "dish_soap": ["dish", "bulaşık", "lavavajillas"],
        "trash_bags": ["trash", "çöp", "basura", "poşet"],
        "cleaning_spray": ["clean", "deterjan", "spray", "limpia"],
        "sponges": ["sponge", "sünger", "esponja"],
    }

    detected: list[dict[str, Any]] = []
    for item_id, keywords in item_keywords.items():
        if any(kw in text for kw in keywords):
            detected.append({
                "item_id": item_id,
                "name": item_id.replace("_", " ").title(),
                "quantity": 1,
            })

    if not detected:
        detected.append({
            "item_id": "other",
            "name": "Miscellaneous supplies",
            "quantity": 1,
        })

    return detected
