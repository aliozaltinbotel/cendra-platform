"""CheckinGuideGenerator — auto-generate check-in guides for guests.

Creates personalized check-in guides with property access codes,
WiFi credentials, video instructions, transport directions, and
house rules. Sent automatically after passport verification.

Real scenario from Cendra CEO:
    "Please send me the passports for all guests, and once I have
    them, I will send you the check-in guide."
    Then manually sends: WiFi, building code, lockbox code, video.

Brain Engine: auto-generates and sends the full guide.

Based on: Cendra real operations (March 2026 CEO screenshots).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PropertyAccess:
    """Access credentials for a property.

    Attributes:
        building_code: Building entrance code.
        lockbox_code: Key lockbox code.
        door_code: Apartment door code (smart lock).
        wifi_name: WiFi network name.
        wifi_password: WiFi password.
        parking_info: Parking instructions.
        video_url: Check-in video instruction URL.
        address: Property address.
        maps_url: Google Maps link.
        floor: Floor number.
        apartment_number: Apartment/unit number.
    """

    building_code: str = ""
    lockbox_code: str = ""
    door_code: str = ""
    wifi_name: str = ""
    wifi_password: str = ""
    parking_info: str = ""
    video_url: str = ""
    address: str = ""
    maps_url: str = ""
    floor: str = ""
    apartment_number: str = ""


@dataclass
class CheckinGuide:
    """Complete check-in guide for a guest.

    Attributes:
        guest_name: Guest's name.
        property_name: Property display name.
        checkin_date: Check-in date.
        checkin_time: Expected check-in time.
        checkout_date: Check-out date.
        checkout_time: Check-out time.
        access: Property access credentials.
        house_rules: List of house rules.
        emergency_contacts: Emergency contact info.
        language: Guide language.
        sections: Generated guide sections.
    """

    guest_name: str = ""
    property_name: str = ""
    checkin_date: str = ""
    checkin_time: str = "15:00"
    checkout_date: str = ""
    checkout_time: str = "11:00"
    access: PropertyAccess = field(default_factory=PropertyAccess)
    house_rules: list[str] = field(default_factory=list)
    emergency_contacts: dict[str, str] = field(default_factory=dict)
    language: str = "en"
    sections: list[dict[str, str]] = field(default_factory=list)


class CheckinGuideGenerator:
    """Generates personalized check-in guides.

    Creates multi-language check-in guides based on property
    data, guest preferences, and booking details. Guides are
    structured for WhatsApp delivery (short messages).

    Args:
        knowledge_base: KB for property-specific info.
        memory: Memory system for guest preferences.
    """

    def __init__(
        self,
        knowledge_base: Any = None,
        memory: Any = None,
    ) -> None:
        self._kb = knowledge_base
        self._memory = memory

    async def generate(
        self,
        guest_name: str,
        property_id: str,
        access: PropertyAccess,
        checkin_date: str = "",
        checkout_date: str = "",
        language: str = "en",
        guest_count: int = 1,
    ) -> CheckinGuide:
        """Generate a complete check-in guide.

        Args:
            guest_name: Guest's name.
            property_id: Property identifier.
            access: Access credentials.
            checkin_date: Check-in date.
            checkout_date: Check-out date.
            language: Guide language.
            guest_count: Number of guests.

        Returns:
            Complete CheckinGuide ready to send.
        """
        guide = CheckinGuide(
            guest_name=guest_name,
            property_name=property_id,
            checkin_date=checkin_date,
            checkout_date=checkout_date,
            access=access,
            language=language,
        )

        guide.sections = self._build_sections(guide, guest_count)
        guide.house_rules = _default_house_rules(language)

        logger.info(
            "Check-in guide generated for %s at %s (%s)",
            guest_name, property_id, language,
        )
        return guide

    def _build_sections(
        self,
        guide: CheckinGuide,
        guest_count: int,
    ) -> list[dict[str, str]]:
        """Build all guide sections.

        Args:
            guide: Base guide data.
            guest_count: Number of guests.

        Returns:
            List of section dicts with title and content.
        """
        sections: list[dict[str, str]] = []
        lang = guide.language

        sections.append(self._welcome_section(guide, lang))
        sections.append(self._access_section(guide, lang))
        sections.append(self._wifi_section(guide, lang))

        if guide.access.video_url:
            sections.append(self._video_section(guide, lang))

        if guide.access.maps_url or guide.access.address:
            sections.append(self._location_section(guide, lang))

        sections.append(self._rules_section(guide, lang))
        sections.append(self._checkout_section(guide, lang))
        sections.append(self._emergency_section(guide, lang))

        return sections

    def _welcome_section(
        self,
        guide: CheckinGuide,
        lang: str,
    ) -> dict[str, str]:
        """Build welcome section."""
        if lang == "tr":
            return {
                "title": "Hoş Geldiniz",
                "content": (
                    f"Merhaba {guide.guest_name}! "
                    f"{guide.property_name} konaklamanız için "
                    f"check-in bilgileriniz hazır."
                ),
            }
        return {
            "title": "Welcome",
            "content": (
                f"Hi {guide.guest_name}! "
                f"Here's your check-in guide for {guide.property_name}. "
                f"Check-in: {guide.checkin_time}."
            ),
        }

    def _access_section(
        self,
        guide: CheckinGuide,
        lang: str,
    ) -> dict[str, str]:
        """Build access codes section."""
        a = guide.access
        lines: list[str] = []

        if a.building_code:
            label = "Bina kodu" if lang == "tr" else "Building code"
            lines.append(f"{label}: {a.building_code}")
        if a.lockbox_code:
            label = "Anahtar kutusu" if lang == "tr" else "Lockbox code"
            lines.append(f"{label}: {a.lockbox_code}")
        if a.door_code:
            label = "Kapı kodu" if lang == "tr" else "Door code"
            lines.append(f"{label}: {a.door_code}")
        if a.floor:
            label = "Kat" if lang == "tr" else "Floor"
            lines.append(f"{label}: {a.floor}")
        if a.apartment_number:
            label = "Daire" if lang == "tr" else "Apartment"
            lines.append(f"{label}: {a.apartment_number}")

        title = "Erişim Kodları" if lang == "tr" else "Access Codes"
        return {"title": title, "content": "\n".join(lines)}

    def _wifi_section(
        self,
        guide: CheckinGuide,
        lang: str,
    ) -> dict[str, str]:
        """Build WiFi section."""
        a = guide.access
        title = "WiFi" if lang != "tr" else "WiFi Bilgileri"
        content = f"Name: {a.wifi_name}\nPassword: {a.wifi_password}"
        return {"title": title, "content": content}

    def _video_section(
        self,
        guide: CheckinGuide,
        lang: str,
    ) -> dict[str, str]:
        """Build video instructions section."""
        title = "Video Talimatları" if lang == "tr" else "Video Instructions"
        content = guide.access.video_url
        return {"title": title, "content": content}

    def _location_section(
        self,
        guide: CheckinGuide,
        lang: str,
    ) -> dict[str, str]:
        """Build location section."""
        a = guide.access
        title = "Konum" if lang == "tr" else "Location"
        lines: list[str] = []
        if a.address:
            lines.append(a.address)
        if a.maps_url:
            lines.append(a.maps_url)
        return {"title": title, "content": "\n".join(lines)}

    def _rules_section(
        self,
        guide: CheckinGuide,
        lang: str,
    ) -> dict[str, str]:
        """Build house rules section."""
        title = "Ev Kuralları" if lang == "tr" else "House Rules"
        rules = guide.house_rules or _default_house_rules(lang)
        content = "\n".join(f"• {r}" for r in rules)
        return {"title": title, "content": content}

    def _checkout_section(
        self,
        guide: CheckinGuide,
        lang: str,
    ) -> dict[str, str]:
        """Build checkout info section."""
        title = "Check-out" if lang != "tr" else "Çıkış Bilgileri"
        if lang == "tr":
            content = (
                f"Çıkış: {guide.checkout_time}\n"
                f"Anahtarları anahtar kutusuna bırakın.\n"
                f"Lütfen çöpleri ayırarak atın."
            )
        else:
            content = (
                f"Check-out: {guide.checkout_time}\n"
                f"Please leave keys in the lockbox.\n"
                f"Separate garbage into bins."
            )
        return {"title": title, "content": content}

    def _emergency_section(
        self,
        guide: CheckinGuide,
        lang: str,
    ) -> dict[str, str]:
        """Build emergency contacts section."""
        title = "Acil Durum" if lang == "tr" else "Emergency"
        contacts = guide.emergency_contacts or {
            "Emergency": "112",
            "Property Manager": "+44 7761 286000",
        }
        lines = [f"{k}: {v}" for k, v in contacts.items()]
        return {"title": title, "content": "\n".join(lines)}

    def format_for_whatsapp(self, guide: CheckinGuide) -> list[str]:
        """Format guide as WhatsApp messages (one per section).

        WhatsApp works best with short individual messages
        rather than one long text.

        Args:
            guide: Complete check-in guide.

        Returns:
            List of WhatsApp message strings.
        """
        messages: list[str] = []
        for section in guide.sections:
            msg = f"*{section['title']}*\n{section['content']}"
            messages.append(msg)
        return messages


def _default_house_rules(language: str) -> list[str]:
    """Default house rules by language.

    Args:
        language: Language code.

    Returns:
        List of rule strings.
    """
    if language == "tr":
        return [
            "İçeride sigara içilmez",
            "Evcil hayvan yasaktır",
            "Sessiz saatler: 22:00 - 08:00",
            "Parti düzenlemek yasaktır",
            "Çöpleri ayrıştırarak atın",
        ]
    return [
        "No smoking inside",
        "No pets allowed",
        "Quiet hours: 10 PM - 8 AM",
        "No parties",
        "Separate garbage into bins",
    ]
