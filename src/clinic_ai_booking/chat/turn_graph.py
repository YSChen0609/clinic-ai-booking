"""Outer turn StateGraph: FAQ/scope/extract → booking subgraph → reply."""

from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from clinic_ai_booking.auth import LOGIN_REQUIRED_MESSAGE
from clinic_ai_booking.chat.book_graph import build_book_graph
from clinic_ai_booking.chat.context import ChatContext
from clinic_ai_booking.chat.extract import TurnExtract, apply_extract, extract_turn
from clinic_ai_booking.chat.facts import empty_facts
from clinic_ai_booking.chat.history import compress_chat_history
from clinic_ai_booking.chat.reply import render_reply
from clinic_ai_booking.chat.resolve import (
    detect_all_services_in_text,
    detect_unknown_service_letter,
    doctor_suggest_followup,
    is_explicit_cancel_or_reschedule,
    unknown_service_message,
)
from clinic_ai_booking.chat.responses import OUT_OF_SCOPE, faq_reply, is_faq_token
from clinic_ai_booking.chat.state import TurnState

logger = logging.getLogger(__name__)

AfterExtract = Literal["reply", "book"]


def build_turn_graph(
    model: BaseChatModel,
    *,
    checkpointer: InMemorySaver | None = None,
) -> CompiledStateGraph:
    """Compile the full chat turn graph (option A)."""
    book = build_book_graph().compile()

    def extract_scope(state: TurnState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
        """FAQ chip, cancel/reschedule note, or LLM extract + draft merge."""
        ctx = runtime.context
        text = (state.get("user_text") or "").strip()
        facts = empty_facts()

        if is_faq_token(text):
            faq = faq_reply(text, ctx)
            return {
                "in_scope": True,
                "intent": "info",
                "facts": facts,
                "reply": faq,
                "messages": [AIMessage(content=faq)],
            }

        recent = compress_chat_history(state.get("messages"))
        extracted = extract_turn(model, text, ctx, recent_history=recent)

        unknown = detect_unknown_service_letter(text)
        if unknown:
            # Stay in clinic scope; closed select rejects letters outside A–E.
            apply_extract(ctx, extracted, user_text=text)
            msg = unknown_service_message(ctx.db, unknown)
            if (
                ctx.draft.professional_slug
                and not ctx.draft.professional_confirmed
            ):
                msg += doctor_suggest_followup(
                    ctx.db, slug=ctx.draft.professional_slug
                )
            facts["status"] = "need_info"
            facts["missing"] = ["service_code"]
            if ctx.draft.professional_slug and not ctx.draft.professional_confirmed:
                facts["missing"].append("professional_confirm")
            facts["error"] = msg
            facts["hint"] = msg
            return {
                "in_scope": True,
                "intent": "book",
                "facts": facts,
                "reply": msg,
                "messages": [AIMessage(content=msg)],
            }

        if not extracted.in_scope:
            return {
                "in_scope": False,
                "intent": extracted.intent,
                "facts": facts,
                "reply": OUT_OF_SCOPE,
                "messages": [AIMessage(content=OUT_OF_SCOPE)],
            }

        if extracted.intent in {"cancel", "reschedule"} and is_explicit_cancel_or_reschedule(
            text
        ):
            msg = (
                LOGIN_REQUIRED_MESSAGE
                if not ctx.is_authenticated
                else (
                    "Cancel and reschedule are not in the chat booking flow yet. "
                    "Please use the website to manage your appointment."
                )
            )
            facts["status"] = "need_info"
            facts["hint"] = msg
            return {
                "in_scope": True,
                "intent": extracted.intent,
                "facts": facts,
                "reply": msg,
                "messages": [AIMessage(content=msg)],
            }

        apply_extract(ctx, extracted, user_text=text)
        multi = detect_all_services_in_text(ctx.db, text)
        if len(multi) >= 2:
            ctx.draft.service_code = None
            ctx.draft.book_confirmed = False
            listed = ", ".join(multi[:-1]) + f" and {multi[-1]}"
            msg = (
                "I can book one service at a time "
                "(you may hold up to two upcoming appointments). "
                f"You mentioned {listed}. Which should we book first?"
            )
            facts["status"] = "need_info"
            facts["missing"] = ["service_code"]
            facts["error"] = msg
            facts["hint"] = msg
            return {
                "in_scope": True,
                "intent": "book",
                "facts": facts,
                "reply": msg,
                "messages": [AIMessage(content=msg)],
            }
        return {
            "in_scope": True,
            "intent": "book" if extracted.intent in {"cancel", "reschedule"} else extracted.intent,
            "facts": empty_facts(),
            "reply": "",
        }

    def run_book(state: TurnState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
        """Advance the deterministic booking subgraph as far as facts allow."""
        result = book.invoke(
            {
                "messages": state.get("messages") or [],
                "user_text": state.get("user_text") or "",
                "reply": state.get("reply") or "",
                "in_scope": True,
                "facts": state.get("facts") or empty_facts(),
                "intent": state.get("intent") or "book",
            },
            context=runtime.context,
        )
        return {"facts": result.get("facts") or empty_facts()}

    def reply_node(state: TurnState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
        """Render the patient-facing reply (skip LLM when already set)."""
        del runtime
        existing = (state.get("reply") or "").strip()
        if existing:
            return {"reply": existing, "messages": [AIMessage(content=existing)]}

        facts = state.get("facts") or empty_facts()
        text = render_reply(
            model, user_text=state.get("user_text") or "", facts=facts
        )
        return {"reply": text, "messages": [AIMessage(content=text)]}

    def after_extract(state: TurnState) -> AfterExtract:
        if (state.get("reply") or "").strip():
            return "reply"
        if not state.get("in_scope"):
            return "reply"
        return "book"

    builder = StateGraph(TurnState, context_schema=ChatContext)
    builder.add_node("extract_scope", extract_scope)
    builder.add_node("book", run_book)
    builder.add_node("reply", reply_node)
    builder.add_edge(START, "extract_scope")
    builder.add_conditional_edges(
        "extract_scope",
        after_extract,
        {"reply": "reply", "book": "book"},
    )
    builder.add_edge("book", "reply")
    builder.add_edge("reply", END)
    return builder.compile(checkpointer=checkpointer or InMemorySaver())


# Re-export for tests
__all__ = ["build_turn_graph", "TurnExtract"]
