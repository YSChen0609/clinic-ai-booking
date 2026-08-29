"""LangChain create_agent harness (LangGraph runtime) for clinic chat."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from clinic_ai_booking.chat_context import ChatContext
from clinic_ai_booking.chat_middleware import CHAT_MIDDLEWARE
from clinic_ai_booking.chat_tools import BOOKING_TOOLS
from clinic_ai_booking.llm import make_chat_model

logger = logging.getLogger(__name__)

SESSION_THREAD_KEY = "chat_thread_id"
SESSION_VISITOR_NAME_KEY = "visitor_name"
SESSION_VISITOR_EMAIL_KEY = "visitor_email"

_checkpointer = InMemorySaver()
_agent: CompiledStateGraph[Any, ChatContext, Any, Any] | None = None


def build_agent(model: BaseChatModel) -> CompiledStateGraph[Any, ChatContext, Any, Any]:
    """Compile create_agent with booking tools, middleware, and checkpointer."""
    return create_agent(
        model,
        tools=BOOKING_TOOLS,
        middleware=CHAT_MIDDLEWARE,
        context_schema=ChatContext,
        checkpointer=_checkpointer,
    )


def get_agent() -> CompiledStateGraph[Any, ChatContext, Any, Any]:
    """Return the process agent, building it on first use."""
    global _agent
    if _agent is None:
        _agent = build_agent(make_chat_model())
    return _agent


def set_agent(agent: CompiledStateGraph[Any, ChatContext, Any, Any] | None) -> None:
    """Replace or clear the process agent (tests)."""
    global _agent
    _agent = agent


def reset_checkpointer() -> None:
    """Drop in-memory thread history (tests)."""
    global _checkpointer, _agent
    _checkpointer = InMemorySaver()
    _agent = None


def new_thread_id() -> str:
    """Allocate a conversation thread id for session storage."""
    return str(uuid.uuid4())


def message_text(message: BaseMessage) -> str:
    """Return plain text from an AI/human message content."""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def run_chat_turn(
    *,
    message: str,
    thread_id: str,
    context: ChatContext,
    agent: CompiledStateGraph[Any, ChatContext, Any, Any] | None = None,
) -> str:
    """Run one user message through create_agent; reuse thread history via thread_id."""
    graph = agent or get_agent()
    text = message.strip()
    if not text:
        raise ValueError("message is required")
    logger.info("chat_turn thread_id=%s authenticated=%s", thread_id, context.is_authenticated)
    result = graph.invoke(
        {"messages": [HumanMessage(content=text)]},
        config={"configurable": {"thread_id": thread_id}},
        context=context,
    )
    messages: list[BaseMessage] = result["messages"]
    for item in reversed(messages):
        if isinstance(item, AIMessage) and not item.tool_calls:
            reply = message_text(item).strip()
            if reply:
                return reply
    return "Sorry — I could not produce a reply. Please try again."
