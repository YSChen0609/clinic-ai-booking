"""Shared LangGraph state for the clinic chat turn."""

from __future__ import annotations

from typing import Annotated, NotRequired, TypedDict

from langgraph.graph.message import add_messages

from clinic_ai_booking.chat.facts import TurnFacts


class TurnState(TypedDict):
    """One messenger turn through scope → book subgraph → reply."""

    messages: Annotated[list, add_messages]
    user_text: str
    reply: str
    in_scope: bool
    facts: TurnFacts
    intent: NotRequired[str]
