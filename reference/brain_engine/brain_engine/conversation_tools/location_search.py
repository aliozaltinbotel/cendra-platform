"""Location search tool — nearby places via web search.

Uses SerpAPI (Google Maps) to find restaurants, pharmacies,
supermarkets, and other points of interest near the property.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from brain_engine.tools.decorator import tool
from brain_engine.tools.runtime import ToolRuntime

logger = logging.getLogger(__name__)


@tool(description=(
    "Search for nearby locations: restaurants, cafes, pharmacies, "
    "supermarkets, ATMs, hospitals, tourist attractions, etc. "
    "Use when guest asks about nearby places or recommendations. "
    "Do NOT use for property amenities or on-site facilities — use rag_document_search. "
    "Do NOT use for availability or booking questions. "
    "Do NOT use for emergency situations — use emergency_contact."
))
async def location_search(
    query: str,
    runtime: ToolRuntime | None = None,
) -> str:
    """Search for nearby places.

    Args:
        query: What to search for (e.g. 'best restaurant nearby').
        runtime: Injected runtime context.

    Returns:
        List of nearby places with names, ratings, and distances.
    """
    property_id = runtime.config.get("property_id", "") if runtime else ""
    address = _get_property_address(property_id)

    try:
        results = await _serpapi_search(query, address)
        return _format_places(results)
    except Exception as exc:
        logger.error("Location search failed: %s", exc)
        return "I couldn't search for nearby places right now."


def _get_property_address(property_id: str) -> str:
    """Get property address for location context.

    Args:
        property_id: Property identifier.

    Returns:
        Property address string.
    """
    try:
        from brain_engine.api import mockup_loader
        prop = mockup_loader.get_property(property_id)
        return prop.get("address", "")
    except Exception:
        return ""


async def _serpapi_search(
    query: str,
    location: str,
) -> list[dict[str, Any]]:
    """Execute Google Maps search via SerpAPI.

    Args:
        query: Search query.
        location: Location context (address).

    Returns:
        List of place dicts.
    """
    api_key = os.getenv("SERPAPI_API_KEY", "")
    if not api_key:
        logger.warning("SERPAPI_API_KEY not configured")
        return []

    try:
        from serpapi import GoogleSearch
    except ImportError:
        logger.warning("serpapi package not installed")
        return []

    search_query = f"{query} near {location}" if location else query

    search = GoogleSearch({
        "engine": "google_maps",
        "q": search_query,
        "type": "search",
        "api_key": api_key,
    })

    raw = search.get_dict()
    return raw.get("local_results", [])[:5]


def _format_places(results: list[dict[str, Any]]) -> str:
    """Format search results for the agent.

    Args:
        results: SerpAPI results.

    Returns:
        Formatted list of places.
    """
    if not results:
        return "No nearby places found for this search."

    lines: list[str] = []
    for i, place in enumerate(results[:5], 1):
        name = place.get("title", "Unknown")
        rating = place.get("rating", "N/A")
        address = place.get("address", "")
        line = f"{i}. {name} (rating: {rating})"
        if address:
            line += f" — {address}"
        lines.append(line)

    return "\n".join(lines)
