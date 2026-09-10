"""Ordered middleware list for create_agent."""

from clinic_ai_booking.chat.middleware import (
    agent_gate,
    auth_tool_gate,
    catalog_gate,
    contact_gate,
    log_tool_calls,
)

CHAT_MIDDLEWARE = [
    log_tool_calls,
    agent_gate,
    catalog_gate,
    auth_tool_gate,
    contact_gate,
]
