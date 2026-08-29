"""Deterministic guardrails for the clinic create_agent chat path."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import (
    AgentState,
    ModelRequest,
    before_model,
    dynamic_prompt,
    wrap_tool_call,
)
from langchain.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from clinic_ai_booking.auth import LOGIN_REQUIRED_MESSAGE
from clinic_ai_booking.chat_context import ChatContext
from clinic_ai_booking.chat_tools import TOOL_ALLOWLIST

logger = logging.getLogger(__name__)

OUT_OF_SCOPE_REPLY = (
    "I can only help with this clinic's dental appointments — services, "
    "availability, booking, cancel, and reschedule. "
    "Please ask about those, or log in if you need to change an existing booking."
)

AUTH_REQUIRED_TOOLS = frozenset({"cancel_appointment", "reschedule_appointment"})

_OUT_OF_SCOPE = re.compile(
    r"\b("
    r"recipe|cook(?:ing)?|weather|forecast|bitcoin|crypto|stock\s*tips?|"
    r"write\s+(?:me\s+)?(?:code|python|javascript)|homework|essay|"
    r"tell\s+me\s+a\s+joke|capital\s+of|who\s+won\s+the|"
    r"translate\s+this|movie\s+recommend"
    r")\b",
    re.IGNORECASE,
)

_CLINIC_HINT = re.compile(
    r"\b("
    r"book|booking|appoint(?:ment)?s?|cancel|reschedul\w*|doctor|dentist|"
    r"clinic|service\s*[a-e]|availab\w*|slot|hours|junior|senior|"
    r"teeth|dental|hygien"
    r")\b",
    re.IGNORECASE,
)

SYSTEM_PROMPT_BASE = """You are the English-only booking assistant for a dental clinic (timezone Asia/Taipei).

Scope: clinic services A–E, professional availability, booking, cancel, and reschedule only.
Refuse anything else politely and briefly.

Hours: Monday–Friday 09:00–20:00; breaks 12:00–13:00 and 17:00–18:00. Closed weekends.
Starts are on a 15-minute grid. Durations are fixed by service code from list_services — never invent length.

Workflow:
1. Clarify service (A–E) and professional (use list_professionals) before asking for contact.
2. Call check_clinic_time on any proposed day or start. If invalid (weekend, outside hours, break, off-grid), tell the patient and stop — do not call list_available_starts for that day.
3. If a weekday has no free starts, call list_next_available_starts and offer alternatives.
4. Ask for name+email only when ready to book AND contact is not already known (see identity below). Never re-ask once known — call remember_visitor once when they first give contact.
5. Call book_appointment only after the patient confirms a specific start. Prefer sticky/session contact over asking again.
6. Service E overtime/break may return pending_doctor — explain doctor confirmation is still required.
7. Cancel/reschedule require site login; if a tool says to log in, tell them to use the header login.
8. Do not invent booking ids, free slots, or policy beyond tool results.
"""


def is_out_of_scope(text: str) -> bool:
    """True when the latest user text is clearly outside clinic booking."""
    cleaned = text.strip()
    if not cleaned:
        return False
    if _OUT_OF_SCOPE.search(cleaned) and not _CLINIC_HINT.search(cleaned):
        return True
    return False


def _latest_user_text(state: AgentState) -> str:
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            content = message.content
            if isinstance(content, str):
                return content
            return str(content)
    return ""


@before_model(can_jump_to=["end"])
def scope_gate(state: AgentState, runtime: Runtime[ChatContext]) -> dict[str, Any] | None:
    """Refuse out-of-scope turns before the model (no booking tools run)."""
    del runtime
    text = _latest_user_text(state)
    if not is_out_of_scope(text):
        return None
    logger.info("scope_gate refused turn text=%r", text[:120])
    return {
        "messages": [AIMessage(content=OUT_OF_SCOPE_REPLY)],
        "jump_to": "end",
    }


@dynamic_prompt
def clinic_system_prompt(request: ModelRequest[ChatContext]) -> str:
    """System prompt plus known identity so name/email are not re-asked."""
    ctx = request.runtime.context
    if ctx.is_authenticated:
        identity = (
            f"Logged-in patient: {ctx.user_name} <{ctx.user_email}>. "
            "Do not ask for name or email; use these when booking. "
            "Cancel/reschedule tools are available for their own bookings."
        )
    elif ctx.has_visitor_contact:
        identity = (
            f"Visitor contact already known: {ctx.visitor_name} <{ctx.visitor_email}>. "
            "Do NOT ask for name or email again. Use remember_visitor only if they "
            "explicitly change contact. Use these values when booking."
        )
    else:
        identity = (
            "Visitor (not logged in; contact not yet saved). "
            "Ask service/doctor/day first. Collect name+email only when about to book, "
            "then call remember_visitor once. Cancel/reschedule require site login."
        )
    return f"{SYSTEM_PROMPT_BASE}\n{identity}"


@wrap_tool_call
def tool_guardrails(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
) -> ToolMessage | Command[Any]:
    """Allowlist + auth policy + light tool logging."""
    call = request.tool_call
    name = call.get("name", "")
    call_id = call.get("id", "")
    args = call.get("args", {})

    if name not in TOOL_ALLOWLIST:
        logger.warning("blocked unknown tool_call name=%s", name)
        return ToolMessage(
            content=f"Tool {name!r} is not available.",
            tool_call_id=call_id,
        )

    ctx = request.runtime.context
    if name in AUTH_REQUIRED_TOOLS and not ctx.is_authenticated:
        logger.info("auth blocked tool=%s (visitor)", name)
        return ToolMessage(
            content=LOGIN_REQUIRED_MESSAGE,
            tool_call_id=call_id,
        )

    logger.info("tool_call name=%s args=%s", name, args)
    result = handler(request)
    if isinstance(result, ToolMessage):
        preview = result.content
        if isinstance(preview, str) and len(preview) > 500:
            preview = preview[:500] + "…"
        logger.info("tool_result name=%s content=%s", name, preview)
    else:
        logger.info("tool_result name=%s type=%s", name, type(result).__name__)
    return result


CHAT_MIDDLEWARE = [scope_gate, clinic_system_prompt, tool_guardrails]
