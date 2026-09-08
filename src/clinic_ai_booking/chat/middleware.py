"""create_agent middleware hooks."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.agents.middleware import AgentState, before_agent, wrap_tool_call
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from clinic_ai_booking.auth import LOGIN_REQUIRED_MESSAGE
from clinic_ai_booking.chat.catalog import reject_unknown_catalog
from clinic_ai_booking.chat.context import ChatContext
from clinic_ai_booking.chat.messages import message_text
from clinic_ai_booking.chat.prompts import SCOPE_CLASSIFIER_PROMPT
from clinic_ai_booking.chat.responses import (
    CONTACT_REQUIRED_MESSAGE,
    OUT_OF_SCOPE,
    faq_reply,
    is_faq_token,
)
from clinic_ai_booking.chat.tools import AUTH_GATED_TOOLS, CONTACT_GATED_TOOLS
from clinic_ai_booking.llm import make_chat_model

logger = logging.getLogger(__name__)

_AUTH_GATED_NAMES = frozenset(t.name for t in AUTH_GATED_TOOLS)
_CONTACT_GATED_NAMES = frozenset(t.name for t in CONTACT_GATED_TOOLS)

_CATALOG_ARG_TOOLS = frozenset(
    {
        "update_context",
        "list_available_starts",
        "list_next_available_starts",
        "check_start",
        "book_appointment",
    }
)


@wrap_tool_call
def log_tool_calls(request: Any, handler: Any) -> ToolMessage | Any:
    """Log tool name and outcome."""
    name = request.tool_call.get("name", "?")
    logger.info("tool call start name=%s", name)
    try:
        result = handler(request)
    except Exception:
        logger.exception("tool call failed name=%s", name)
        raise
    preview = result.content if isinstance(result, ToolMessage) else str(result)
    logger.info("tool call done name=%s preview=%s", name, preview[:240])
    return result


@before_agent(can_jump_to=["end"])
def agent_gate(state: AgentState, runtime: Runtime[ChatContext]) -> dict[str, Any] | None:
    """FAQ chip tokens and LLM scope check before the agent loop."""
    text = ""
    for item in reversed(state.get("messages") or []):
        if isinstance(item, HumanMessage):
            text = message_text(item).strip()
            break
    if not text:
        return None

    if is_faq_token(text):
        return {"messages": [AIMessage(content=faq_reply(text, runtime.context))], "jump_to": "end"}

    result = make_chat_model().invoke(
        [
            HumanMessage(content=SCOPE_CLASSIFIER_PROMPT),
            HumanMessage(content=f"User message:\n{text}"),
        ]
    )
    label = message_text(result).strip().upper()
    if "OUT_OF_SCOPE" in label and not label.startswith("IN_SCOPE"):
        return {"messages": [AIMessage(content=OUT_OF_SCOPE)], "jump_to": "end"}

    return None


@wrap_tool_call
def catalog_gate(request: Any, handler: Any) -> ToolMessage | Any:
    """Block invented professional_slug / service_code before the engine runs."""
    name = request.tool_call.get("name", "")
    if name not in _CATALOG_ARG_TOOLS:
        return handler(request)

    ctx: ChatContext = request.runtime.context
    args = request.tool_call.get("args") or {}
    if name == "book_appointment":
        err = reject_unknown_catalog(
            ctx.db,
            professional_slug=ctx.draft.professional_slug,
            service_code=ctx.draft.service_code,
        )
    else:
        err = reject_unknown_catalog(
            ctx.db,
            professional_slug=args.get("professional_slug"),
            service_code=args.get("service_code"),
        )
    if err:
        return ToolMessage(
            content=json.dumps({"ok": False, "error": err}),
            tool_call_id=request.tool_call.get("id", ""),
        )
    return handler(request)


@wrap_tool_call
def auth_tool_gate(request: Any, handler: Any) -> ToolMessage | Any:
    """Block cancel/reschedule for visitors."""
    name = request.tool_call.get("name", "")
    if name not in _AUTH_GATED_NAMES:
        return handler(request)
    if request.runtime.context.is_authenticated:
        return handler(request)
    return ToolMessage(
        content=LOGIN_REQUIRED_MESSAGE,
        tool_call_id=request.tool_call.get("id", ""),
    )


@wrap_tool_call
def contact_gate(request: Any, handler: Any) -> ToolMessage | Any:
    """Block book until context has enough identity."""
    name = request.tool_call.get("name", "")
    if name not in _CONTACT_GATED_NAMES:
        return handler(request)
    if request.runtime.context.can_book_now:
        return handler(request)
    return ToolMessage(
        content=CONTACT_REQUIRED_MESSAGE,
        tool_call_id=request.tool_call.get("id", ""),
    )
