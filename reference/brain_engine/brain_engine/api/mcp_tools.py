"""MCP Tool Formatter — Formats Brain Engine decisions as MCP tool calls.

Cendra's infrastructure executes tools via MCP Server (30+ tools).
Brain Engine decides WHAT to do, then formats the decision as
MCP tool calls that Cendra can execute directly.

Supported MCP tools (from Cendra ops case):
    Contact:  createContact, getContacts, assignContactToProperty,
              checkContactAvailability
    Comms:    sendWhatsApp, sendEmail, sendInboxReply, sendSlackMessage
    Calendar: createContactEvent, updateContactEvent, deleteContactEvent
    Property: getProperty, getReservation, getPropertyOpsConfig
    Tasks:    createTask, updateTask, getTasksByProperty
    IoT:      lockDoor, unlockDoor, getDeviceStatus, createAccessCode
    Knowledge: searchKnowledgeBase, getPropertyRules
    Ops:      logOpsActivity, writeOperationalMemory, getOpsConfig
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MCPToolCall:
    """A single MCP tool call for Cendra to execute.

    Attributes:
        tool: Tool name matching Cendra's MCP Server.
        params: Parameters for the tool call.
        priority: Execution priority (1=highest).
        depends_on: Tool call IDs this depends on.
        call_id: Unique identifier for this call.
    """

    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    priority: int = 1
    depends_on: list[str] = field(default_factory=list)
    call_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API response.

        Returns:
            Dict representation.
        """
        result: dict[str, Any] = {
            "tool": self.tool,
            "params": self.params,
        }
        if self.priority != 1:
            result["priority"] = self.priority
        if self.depends_on:
            result["depends_on"] = self.depends_on
        if self.call_id:
            result["call_id"] = self.call_id
        return result


class MCPToolFormatter:
    """Formats Brain Engine decisions as MCP tool calls.

    Brain Engine reasons about what actions to take, then this
    formatter converts those decisions into the exact MCP tool call
    format that Cendra's infrastructure expects.
    """

    # ── Communication Tools ─────────────────────────────────────────── #

    @staticmethod
    def send_whatsapp(
        contact_id: str,
        message: str,
        phone: str = "",
    ) -> MCPToolCall:
        """Format a WhatsApp message send.

        Args:
            contact_id: Contact to message.
            message: Message text.
            phone: Phone number (if contact_id not available).

        Returns:
            MCPToolCall for sendWhatsApp.
        """
        params: dict[str, Any] = {"message": message}
        if contact_id:
            params["contactId"] = contact_id
        if phone:
            params["phone"] = phone
        return MCPToolCall(tool="sendWhatsApp", params=params)

    @staticmethod
    def send_email(
        contact_id: str,
        subject: str,
        body: str,
    ) -> MCPToolCall:
        """Format an email send.

        Args:
            contact_id: Contact to email.
            subject: Email subject.
            body: Email body.

        Returns:
            MCPToolCall for sendEmail.
        """
        return MCPToolCall(
            tool="sendEmail",
            params={
                "contactId": contact_id,
                "subject": subject,
                "body": body,
            },
        )

    @staticmethod
    def send_inbox_reply(
        reservation_id: str,
        message: str,
    ) -> MCPToolCall:
        """Format a PMS inbox reply to guest.

        Args:
            reservation_id: Reservation identifier.
            message: Reply message text.

        Returns:
            MCPToolCall for sendInboxReply.
        """
        return MCPToolCall(
            tool="sendInboxReply",
            params={
                "reservationId": reservation_id,
                "message": message,
            },
        )

    @staticmethod
    def send_slack(channel: str, message: str) -> MCPToolCall:
        """Format a Slack message.

        Args:
            channel: Slack channel name.
            message: Message text.

        Returns:
            MCPToolCall for sendSlackMessage.
        """
        return MCPToolCall(
            tool="sendSlackMessage",
            params={"channel": channel, "message": message},
        )

    # ── Contact Management Tools ────────────────────────────────────── #

    @staticmethod
    def create_contact(
        name: str,
        phone: str,
        role: str = "vendor",
        vendor_category: str = "",
    ) -> MCPToolCall:
        """Format contact creation (Scenario 2: alternative contact).

        Args:
            name: Contact name.
            phone: Phone number.
            role: Contact role (cleaner/vendor).
            vendor_category: Vendor category if applicable.

        Returns:
            MCPToolCall for createContact.
        """
        params: dict[str, Any] = {
            "name": name,
            "phone": phone,
            "role": role,
        }
        if vendor_category:
            params["vendorCategory"] = vendor_category
        return MCPToolCall(tool="createContact", params=params)

    @staticmethod
    def get_contacts(
        property_id: str,
        role: str = "",
        vendor_category: str = "",
    ) -> MCPToolCall:
        """Format contact retrieval (read-before-write guardrail).

        Args:
            property_id: Property to get contacts for.
            role: Filter by role.
            vendor_category: Filter by vendor category.

        Returns:
            MCPToolCall for getContacts.
        """
        params: dict[str, Any] = {"propertyId": property_id}
        if role:
            params["role"] = role
        if vendor_category:
            params["vendorCategory"] = vendor_category
        return MCPToolCall(tool="getContacts", params=params)

    @staticmethod
    def assign_contact(
        contact_id: str,
        property_id: str,
        role: str = "vendor",
    ) -> MCPToolCall:
        """Format contact-to-property assignment.

        Args:
            contact_id: Contact to assign.
            property_id: Property to assign to.
            role: Assignment role.

        Returns:
            MCPToolCall for assignContactToProperty.
        """
        return MCPToolCall(
            tool="assignContactToProperty",
            params={
                "contactId": contact_id,
                "propertyId": property_id,
                "role": role,
            },
        )

    # ── Task Management Tools ───────────────────────────────────────── #

    @staticmethod
    def create_task(
        task_name: str,
        description: str,
        main_category: str,
        sub_category: str = "",
        tags: list[str] | None = None,
        property_id: str = "",
        reservation_id: str = "",
    ) -> MCPToolCall:
        """Format task creation (for PM visibility).

        Args:
            task_name: Short task title.
            description: Task description.
            main_category: Main category.
            sub_category: Sub-category.
            tags: Classification tags.
            property_id: Property identifier.
            reservation_id: Reservation identifier.

        Returns:
            MCPToolCall for createTask.
        """
        params: dict[str, Any] = {
            "task": task_name,
            "description": description,
            "main_category": main_category,
        }
        if sub_category:
            params["sub_category"] = sub_category
        if tags:
            params["tags"] = tags
        if property_id:
            params["propertyId"] = property_id
        if reservation_id:
            params["reservationId"] = reservation_id
        return MCPToolCall(tool="createTask", params=params)

    @staticmethod
    def update_task(
        task_id: str,
        status: str = "",
        notes: str = "",
    ) -> MCPToolCall:
        """Format task update.

        Args:
            task_id: Task to update.
            status: New status.
            notes: Update notes.

        Returns:
            MCPToolCall for updateTask.
        """
        params: dict[str, Any] = {"taskId": task_id}
        if status:
            params["status"] = status
        if notes:
            params["notes"] = notes
        return MCPToolCall(tool="updateTask", params=params)

    # ── Calendar Tools ──────────────────────────────────────────────── #

    @staticmethod
    def create_event(
        contact_id: str,
        property_id: str,
        start_time: str,
        end_time: str,
        event_type: str = "cleaning",
    ) -> MCPToolCall:
        """Format calendar event creation.

        Args:
            contact_id: Contact for the event.
            property_id: Property identifier.
            start_time: ISO format start time.
            end_time: ISO format end time.
            event_type: Type of event.

        Returns:
            MCPToolCall for createContactEvent.
        """
        return MCPToolCall(
            tool="createContactEvent",
            params={
                "contactId": contact_id,
                "propertyId": property_id,
                "startTime": start_time,
                "endTime": end_time,
                "eventType": event_type,
            },
        )

    # ── IoT Tools ───────────────────────────────────────────────────── #

    @staticmethod
    def create_access_code(
        property_id: str,
        code: str,
        name: str = "",
        starts_at: str = "",
        ends_at: str = "",
    ) -> MCPToolCall:
        """Format access code creation.

        Args:
            property_id: Property identifier.
            code: Access code.
            name: Code name/label.
            starts_at: Code activation time.
            ends_at: Code expiration time.

        Returns:
            MCPToolCall for createAccessCode.
        """
        params: dict[str, Any] = {
            "propertyId": property_id,
            "code": code,
        }
        if name:
            params["name"] = name
        if starts_at:
            params["startsAt"] = starts_at
        if ends_at:
            params["endsAt"] = ends_at
        return MCPToolCall(tool="createAccessCode", params=params)

    @staticmethod
    def unlock_door(property_id: str) -> MCPToolCall:
        """Format door unlock command.

        Args:
            property_id: Property identifier.

        Returns:
            MCPToolCall for unlockDoor.
        """
        return MCPToolCall(
            tool="unlockDoor",
            params={"propertyId": property_id},
        )

    # ── Ops Logging Tools ───────────────────────────────────────────── #

    @staticmethod
    def log_activity(
        session_id: str,
        action: str,
        details: str,
        agent_type: str = "brain_engine",
    ) -> MCPToolCall:
        """Format ops activity log entry.

        Args:
            session_id: Ops session identifier.
            action: Action performed.
            details: Action details.
            agent_type: Which agent performed the action.

        Returns:
            MCPToolCall for logOpsActivity.
        """
        return MCPToolCall(
            tool="logOpsActivity",
            params={
                "sessionId": session_id,
                "action": action,
                "details": details,
                "agentType": agent_type,
            },
        )

    @staticmethod
    def write_memory(
        key: str,
        value: str,
        property_id: str = "",
    ) -> MCPToolCall:
        """Format operational memory write.

        Args:
            key: Memory key.
            value: Memory value.
            property_id: Property scope.

        Returns:
            MCPToolCall for writeOperationalMemory.
        """
        params: dict[str, Any] = {"key": key, "value": value}
        if property_id:
            params["propertyId"] = property_id
        return MCPToolCall(
            tool="writeOperationalMemory",
            params=params,
        )

    # ── Knowledge Tools ─────────────────────────────────────────────── #

    @staticmethod
    def search_knowledge_base(
        query: str,
        property_id: str = "",
    ) -> MCPToolCall:
        """Format knowledge base search (RAG).

        Args:
            query: Search query.
            property_id: Property scope for filtering.

        Returns:
            MCPToolCall for searchKnowledgeBase.
        """
        params: dict[str, Any] = {"query": query}
        if property_id:
            params["propertyId"] = property_id
        return MCPToolCall(tool="searchKnowledgeBase", params=params)

    @staticmethod
    def sync_knowledge_base(property_id: str = "") -> MCPToolCall:
        """Format KB sync trigger (Cendra KB → Brain Engine SemanticMemory).

        Args:
            property_id: Property scope (empty = all properties).

        Returns:
            MCPToolCall for syncKnowledgeBase.
        """
        params: dict[str, Any] = {}
        if property_id:
            params["propertyId"] = property_id
        return MCPToolCall(tool="syncKnowledgeBase", params=params)

    @staticmethod
    def approve_knowledge_candidate(
        candidate_id: str,
        approved: bool = True,
        edited_content: str = "",
    ) -> MCPToolCall:
        """Format knowledge candidate approval/rejection.

        Args:
            candidate_id: Knowledge candidate identifier.
            approved: Whether to approve.
            edited_content: Edited Q&A content (if modified).

        Returns:
            MCPToolCall for approveKnowledgeCandidate.
        """
        params: dict[str, Any] = {
            "candidateId": candidate_id,
            "approved": approved,
        }
        if edited_content:
            params["editedContent"] = edited_content
        return MCPToolCall(tool="approveKnowledgeCandidate", params=params)

    @staticmethod
    def add_knowledge_entry(
        title: str,
        content: str,
        property_id: str = "",
        category: str = "",
    ) -> MCPToolCall:
        """Format new knowledge entry creation in Cendra KB.

        Args:
            title: Entry title (question or topic).
            content: Entry content (answer or information).
            property_id: Property scope.
            category: Knowledge category.

        Returns:
            MCPToolCall for addKnowledgeEntry.
        """
        params: dict[str, Any] = {"title": title, "content": content}
        if property_id:
            params["propertyId"] = property_id
        if category:
            params["category"] = category
        return MCPToolCall(tool="addKnowledgeEntry", params=params)
