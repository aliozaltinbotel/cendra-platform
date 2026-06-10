"""Mockup Data Loader — loads property, cleaner, vendor data from config.

Provides in-memory access to pre-configured operational data so that
API endpoints (booking/new, ops, etc.) can auto-resolve contacts and
property details without requiring them in every request.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"

_properties: dict[str, dict[str, Any]] = {}
_cleaners: dict[str, dict[str, Any]] = {}
_vendors: dict[str, dict[str, Any]] = {}


def load_all() -> None:
    """Load all mockup data from config JSON files."""
    _load_properties()
    _load_cleaners()
    _load_vendors()
    logger.info(
        "Mockup loaded: %d properties, %d cleaners, %d vendors",
        len(_properties), len(_cleaners), len(_vendors),
    )


def _load_properties() -> None:
    """Load properties from config/properties.json."""
    path = _CONFIG_DIR / "properties.json"
    if not path.exists():
        logger.warning("properties.json not found at %s", path)
        return
    with open(path) as f:
        data = json.load(f)
    for prop in data:
        pid = prop.get("property_id", "")
        if pid:
            _properties[pid] = prop


def _load_cleaners() -> None:
    """Load cleaners from config/cleaners.json."""
    path = _CONFIG_DIR / "cleaners.json"
    if not path.exists():
        return
    with open(path) as f:
        data = json.load(f)
    for c in data:
        cid = c.get("contact_id", c.get("name", ""))
        if cid:
            _cleaners[cid] = c


def _load_vendors() -> None:
    """Load vendors from config/vendors.json."""
    path = _CONFIG_DIR / "vendors.json"
    if not path.exists():
        return
    with open(path) as f:
        data = json.load(f)
    for v in data:
        vid = v.get("contact_id", v.get("name", ""))
        if vid:
            _vendors[vid] = v


# ── Persist to JSON ───────────────────────────────────────── #


def _persist_cleaners() -> None:
    """Save current cleaner data back to config/cleaners.json."""
    path = _CONFIG_DIR / "cleaners.json"
    try:
        with open(path, "w") as f:
            json.dump(list(_cleaners.values()), f, indent=2, ensure_ascii=False)
        logger.info("Persisted cleaners to %s", path)
    except Exception as exc:
        logger.error("Failed to persist cleaners: %s", exc)


def _persist_vendors() -> None:
    """Save current vendor data back to config/vendors.json."""
    path = _CONFIG_DIR / "vendors.json"
    try:
        with open(path, "w") as f:
            json.dump(list(_vendors.values()), f, indent=2, ensure_ascii=False)
        logger.info("Persisted vendors to %s", path)
    except Exception as exc:
        logger.error("Failed to persist vendors: %s", exc)


def _persist_properties() -> None:
    """Save current property data back to config/properties.json."""
    path = _CONFIG_DIR / "properties.json"
    try:
        with open(path, "w") as f:
            json.dump(list(_properties.values()), f, indent=2, ensure_ascii=False)
        logger.info("Persisted properties to %s", path)
    except Exception as exc:
        logger.error("Failed to persist properties: %s", exc)


def get_property(property_id: str) -> dict[str, Any]:
    """Get property data by ID.

    Args:
        property_id: Property identifier.

    Returns:
        Property dict or empty dict.
    """
    return _properties.get(property_id, {})


def get_property_access(property_id: str) -> dict[str, str]:
    """Get property access codes (wifi, door, lockbox).

    Args:
        property_id: Property identifier.

    Returns:
        Access dict with wifi_name, wifi_password, etc.
    """
    prop = _properties.get(property_id, {})
    return prop.get("property_access", {})


def get_pms_user(property_id: str) -> dict[str, str]:
    """Get PMS user (property manager) for a property.

    Args:
        property_id: Property identifier.

    Returns:
        PMS user dict with name, phone, telegram_chat_id.
    """
    prop = _properties.get(property_id, {})
    return prop.get("pms_user", {})


def get_cleaners_for_property(
    property_id: str,
) -> list[dict[str, Any]]:
    """Get all cleaners assigned to a property.

    Args:
        property_id: Property identifier.

    Returns:
        List of cleaner dicts.
    """
    prop = _properties.get(property_id, {})
    cleaner_ids = prop.get("cleaners", [])
    result = []
    for cid in cleaner_ids:
        cleaner = _cleaners.get(cid)
        if cleaner:
            result.append(cleaner)
    if not result:
        return list(_cleaners.values())
    return result


def get_cleaner(contact_id: str) -> dict[str, Any]:
    """Get a single cleaner by ID.

    Args:
        contact_id: Cleaner contact ID.

    Returns:
        Cleaner dict or empty dict.
    """
    return _cleaners.get(contact_id, {})


def get_vendors_for_property(
    property_id: str,
) -> list[dict[str, Any]]:
    """Get all vendors assigned to a property.

    Args:
        property_id: Property identifier.

    Returns:
        List of vendor dicts.
    """
    prop = _properties.get(property_id, {})
    vendor_ids = prop.get("vendors", [])
    result = []
    for vid in vendor_ids:
        vendor = _vendors.get(vid)
        if vendor:
            result.append(vendor)
    if not result:
        return list(_vendors.values())
    return result


def get_vendor(contact_id: str) -> dict[str, Any]:
    """Get a single vendor by ID.

    Args:
        contact_id: Vendor contact ID.

    Returns:
        Vendor dict or empty dict.
    """
    return _vendors.get(contact_id, {})


def get_all_properties() -> list[dict[str, Any]]:
    """Get all properties.

    Returns:
        List of all property dicts.
    """
    return list(_properties.values())


def get_all_cleaners() -> list[dict[str, Any]]:
    """Get all cleaners.

    Returns:
        List of all cleaner dicts.
    """
    return list(_cleaners.values())


def get_all_vendors() -> list[dict[str, Any]]:
    """Get all vendor dicts.

    Returns:
        List of all vendor dicts.
    """
    return list(_vendors.values())


# ── Chat ID mapping ──────────────────────────────────────── #


def update_chat_id(contact_id: str, chat_id: str) -> bool:
    """Set telegram_chat_id for a cleaner, vendor, or PMS user.

    Persists the change to the config JSON file so it survives
    server restarts and deploys.

    Args:
        contact_id: Contact identifier (e.g. 'cleaner-aybuke').
        chat_id: Telegram chat ID string.

    Returns:
        True if contact was found and updated.
    """
    if contact_id in _cleaners:
        _cleaners[contact_id]["telegram_chat_id"] = chat_id
        logger.info("Chat ID set: %s -> %s", contact_id, chat_id)
        _persist_cleaners()
        return True
    if contact_id in _vendors:
        _vendors[contact_id]["telegram_chat_id"] = chat_id
        logger.info("Chat ID set: %s -> %s", contact_id, chat_id)
        _persist_vendors()
        return True

    # Check PMS users in properties
    for prop in _properties.values():
        pms = prop.get("pms_user", {})
        if pms.get("name", "").lower() == contact_id.lower():
            pms["telegram_chat_id"] = chat_id
            logger.info("PMS chat ID set: %s -> %s", contact_id, chat_id)
            _persist_properties()
            return True

    return False


def find_contact_by_chat_id(chat_id: str) -> dict[str, Any] | None:
    """Find a cleaner, vendor, or PMS user by their Telegram chat_id.

    Args:
        chat_id: Telegram chat ID string.

    Returns:
        Contact dict with added 'role' field, or None.
    """
    for c in _cleaners.values():
        if str(c.get("telegram_chat_id", "")) == str(chat_id):
            return {**c, "role": "cleaner"}

    for v in _vendors.values():
        if str(v.get("telegram_chat_id", "")) == str(chat_id):
            return {**v, "role": "vendor"}

    for prop in _properties.values():
        pms = prop.get("pms_user", {})
        if str(pms.get("telegram_chat_id", "")) == str(chat_id):
            return {**pms, "role": "pms", "property_id": prop["property_id"]}

    return None


def auto_match_by_name(first_name: str, chat_id: str) -> dict[str, Any] | None:
    """Try to match a Telegram first_name to mockup contacts.

    Checks cleaners, vendors, and PMS users by name (case-insensitive).
    Sets telegram_chat_id only if not already set (manual registration
    via API takes priority over auto-match).

    Args:
        first_name: Telegram user's first name.
        chat_id: Telegram chat ID.

    Returns:
        Matched contact dict, or None.
    """
    name_lower = first_name.lower().strip()

    for c in _cleaners.values():
        if c.get("name", "").lower() == name_lower:
            existing = c.get("telegram_chat_id", "")
            if not existing or str(existing) == str(chat_id):
                c["telegram_chat_id"] = chat_id
                _persist_cleaners()
                logger.info("Auto-matched cleaner %s -> chat_id %s", c["name"], chat_id)
            else:
                logger.info(
                    "Skipped auto-match for %s: already set to %s (incoming %s)",
                    c["name"], existing, chat_id,
                )
            return {**c, "role": "cleaner"}

    for v in _vendors.values():
        if v.get("name", "").lower() == name_lower:
            existing = v.get("telegram_chat_id", "")
            if not existing or str(existing) == str(chat_id):
                v["telegram_chat_id"] = chat_id
                _persist_vendors()
                logger.info("Auto-matched vendor %s -> chat_id %s", v["name"], chat_id)
            else:
                logger.info(
                    "Skipped auto-match for %s: already set to %s (incoming %s)",
                    v["name"], existing, chat_id,
                )
            return {**v, "role": "vendor"}

    for prop in _properties.values():
        pms = prop.get("pms_user", {})
        if pms.get("name", "").lower() == name_lower:
            existing = pms.get("telegram_chat_id", "")
            if not existing or str(existing) == str(chat_id):
                pms["telegram_chat_id"] = chat_id
                _persist_properties()
                logger.info("Auto-matched PMS %s -> chat_id %s", pms["name"], chat_id)
            else:
                logger.info(
                    "Skipped auto-match for PMS %s: already set to %s (incoming %s)",
                    pms["name"], existing, chat_id,
                )
            return {**pms, "role": "pms", "property_id": prop["property_id"]}

    return None


def get_pms_chat_id(property_id: str) -> str:
    """Get PMS user's Telegram chat_id for a property.

    Args:
        property_id: Property identifier.

    Returns:
        Chat ID string, or empty string.
    """
    prop = _properties.get(property_id, {})
    return str(prop.get("pms_user", {}).get("telegram_chat_id", ""))
