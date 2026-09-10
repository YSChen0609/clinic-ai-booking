"""Helpers for reading LangChain message content."""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage


def message_text(message: BaseMessage) -> str:
    """Extract plain text from a chat message."""
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


def last_ai_reply(messages: list[BaseMessage]) -> str:
    """Return the latest non-tool-call assistant text, or a fallback."""
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            text = message_text(message).strip()
            if text:
                return text
    return "Sorry, I could not produce a reply."
