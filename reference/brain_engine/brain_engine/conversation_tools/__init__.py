"""Conversation tools — ReAct agent tools for guest messaging.

Each tool is a @tool-decorated async function that the conversation
agent can call during message processing. Tools are dynamically
enabled/disabled based on customer settings.
"""

from brain_engine.conversation_tools.rag_search import rag_document_search
from brain_engine.conversation_tools.availability_checker import availability_checker
from brain_engine.conversation_tools.upsell_calculator import upsell_calculator
from brain_engine.conversation_tools.emergency_contact import emergency_contact
from brain_engine.conversation_tools.location_search import location_search
from brain_engine.conversation_tools.reservation_info import reservation_info_retriever
from brain_engine.conversation_tools.alternative_property import alternative_property_finder
from brain_engine.conversation_tools.complaint_checker import check_repeated_complaint
from brain_engine.conversation_tools.thanks_generator import thanks_response_generator

ALL_TOOLS = [
    rag_document_search,
    availability_checker,
    upsell_calculator,
    emergency_contact,
    location_search,
    reservation_info_retriever,
    alternative_property_finder,
    check_repeated_complaint,
    thanks_response_generator,
]

# Map tool function names to customer toggle field names
TOOL_TOGGLE_MAP: dict[str, str] = {
    "rag_document_search": "rag_document_search",
    "availability_checker": "search_availability",
    "upsell_calculator": "upsell_calculator",
    "emergency_contact": "emergency_contact",
    "location_search": "search_internet",
    "alternative_property_finder": "suggest_alternative_listings",
    "check_repeated_complaint": "complaint_checker",
    "thanks_response_generator": "rag_document_search",  # always on
    "reservation_info_retriever": "rag_document_search",  # always on
}

__all__ = [
    "ALL_TOOLS",
    "TOOL_TOGGLE_MAP",
    "rag_document_search",
    "availability_checker",
    "upsell_calculator",
    "emergency_contact",
    "location_search",
    "reservation_info_retriever",
    "alternative_property_finder",
    "check_repeated_complaint",
    "thanks_response_generator",
]
