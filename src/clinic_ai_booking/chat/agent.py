"""ClinicAgent: turn StateGraph (scope → booking engine subgraph → reply)."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.orm import Session

from clinic_ai_booking.chat.context import ChatContext, load_context, save_context
from clinic_ai_booking.chat.facts import empty_facts
from clinic_ai_booking.chat.turn_graph import build_turn_graph
from clinic_ai_booking.config import ChatSettings
from clinic_ai_booking.domain.models import User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnResult:
    """Output of one messenger turn."""

    reply: str
    thread_id: str
    can_book_now: bool
    # Structured booking outcome for tests / debugging (not shown to the patient).
    facts: dict[str, Any]
    in_scope: bool


class ClinicAgent:
    """Clinic chat agent service (one instance per app process)."""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        settings: ChatSettings,
        checkpointer: InMemorySaver | None = None,
    ) -> None:
        self._settings = settings
        self._checkpointer = checkpointer or InMemorySaver()
        self._model = model
        self._graph = build_turn_graph(model, checkpointer=self._checkpointer)

    @property
    def settings(self) -> ChatSettings:
        """Return frozen settings used to build this agent."""
        return self._settings

    @property
    def graph(self) -> CompiledStateGraph:
        """Return the compiled turn graph."""
        return self._graph

    def run_one_turn(
        self,
        *,
        message: str,
        session: MutableMapping[str, Any],
        db: Session,
        thread_id: str,
        user: User | None = None,
    ) -> TurnResult:
        """Run one user message: load_context → turn graph → save_context."""
        text = message.strip()
        if not text:
            raise ValueError("message is empty")

        ctx = load_context(session, db, user=user)
        config = {"configurable": {"thread_id": thread_id}}

        logger.info(
            "chat turn thread_id=%s authenticated=%s", thread_id, ctx.is_authenticated
        )
        result = self._graph.invoke(
            {
                "messages": [HumanMessage(content=text)],
                "user_text": text,
                "reply": "",
                "in_scope": False,
                "facts": empty_facts(),
                "intent": "book",
            },
            config=config,
            context=ctx,
        )

        save_context(session, ctx)

        reply = (result.get("reply") or "").strip() or "Sorry, I could not produce a reply."
        facts = dict(result.get("facts") or empty_facts())
        return TurnResult(
            reply=reply,
            thread_id=thread_id,
            can_book_now=ctx.can_book_now,
            facts=facts,
            in_scope=bool(result.get("in_scope")),
        )
