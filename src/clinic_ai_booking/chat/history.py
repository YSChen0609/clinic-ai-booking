"""Compact recent chat lines for the extract LLM (not a full transcript dump)."""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from clinic_ai_booking.chat.messages import message_text

# Cap cost on local 7B: few short lines, truncated.
_MAX_LINES = 8
_MAX_CHARS_PER_LINE = 160


def compress_chat_history(
    messages: list[BaseMessage] | None,
    *,
    exclude_last_human: bool = True,
) -> str:
    """Return a short User/Assistant transcript for the extract prompt.

    Keeps the last few turns only (compression = truncate + drop older lines).
    Does not call an LLM.
    """
    if not messages:
        return ""

    lines: list[str] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            role = "User"
        elif isinstance(message, AIMessage) and not getattr(message, "tool_calls", None):
            role = "Assistant"
        else:
            continue
        text = " ".join(message_text(message).split())
        if not text:
            continue
        if len(text) > _MAX_CHARS_PER_LINE:
            text = text[: _MAX_CHARS_PER_LINE - 1] + "…"
        lines.append(f"{role}: {text}")

    if exclude_last_human and lines and lines[-1].startswith("User:"):
        # Current turn is passed separately as "User message"
        lines = lines[:-1]

    lines = lines[-_MAX_LINES:]
    return "\n".join(lines)
