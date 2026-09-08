"""Client-facing reply: deterministic for booking facts; LLM only as soft fallback."""

from __future__ import annotations

import json
import logging

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from clinic_ai_booking.chat.facts import TurnFacts, facts_as_dict
from clinic_ai_booking.chat.messages import message_text

logger = logging.getLogger(__name__)

# Statuses where inventing times/ids is unacceptable — never free-form LLM.
_DETERMINISTIC = frozenset(
    {"offer_slots", "need_info", "book_failed", "booked", "slot_ok", "identity_ok"}
)

REPLY_SYSTEM = """\
You are this clinic's booking assistant. Write one short, clear patient-facing reply.
Use ONLY the facts JSON. Never invent doctors, service codes, times, weekdays, \
booking ids, or success. If offered_times / offered_starts are present, list only those.
If resolved_day or clinic_today is set, use those exact labels.
Do not mention JSON, tools, or internal field names.
"""


def render_reply(
    model: BaseChatModel,
    *,
    user_text: str,
    facts: TurnFacts,
) -> str:
    """Turn facts into one patient-facing message."""
    status = facts.get("status") or ""
    if status in _DETERMINISTIC:
        return _fallback_reply(facts)

    payload = json.dumps(facts_as_dict(facts), ensure_ascii=True)
    try:
        result = model.invoke(
            [
                SystemMessage(content=REPLY_SYSTEM),
                HumanMessage(
                    content=f"User said:\n{user_text}\n\nFacts JSON:\n{payload}"
                ),
            ]
        )
        text = message_text(result).strip()
        if text:
            return text
    except Exception:
        logger.exception("reply LLM failed; using deterministic fallback")
    return _fallback_reply(facts)


def _fallback_reply(facts: TurnFacts) -> str:
    """Deterministic reply when inventing times must not happen."""
    status = facts.get("status") or ""
    day = (facts.get("resolved_day") or "").strip()
    today = (facts.get("clinic_today") or "").strip()
    band = (facts.get("time_band") or "").strip()

    if status == "booked":
        starts = facts.get("offered_starts") or []
        when = starts[0] if starts else ""
        return (
            f"Your appointment is booked (id {facts.get('booking_id')}"
            + (f", {when}" if when else "")
            + ")."
        )
    if status == "book_failed":
        err = (facts.get("error") or "unknown error").strip()
        return f"I could not complete the booking: {err}"
    if status == "offer_slots":
        times = facts.get("offered_times") or []
        listed = ", ".join(times[:10]) if times else "(none from the calendar)"
        prefix = f"For {day}" if day else "Available times"
        if band:
            prefix += f" ({band})"
        today_note = f" (clinic today is {today})" if today and day else ""
        err = (facts.get("error") or "").strip()
        lead = f"{err}. " if err else ""
        return (
            f"{lead}{prefix}{today_note}, the calendar has: {listed}. "
            "Which of these times works for you?"
        )
    if status in {"slot_ok", "identity_ok"}:
        hint = (facts.get("hint") or "").strip()
        return hint or "I can hold that slot once I have your name and email."
    missing = facts.get("missing") or []
    if status == "need_info" or missing:
        err = (facts.get("error") or "").strip()
        if err:
            return err
        hint = (facts.get("hint") or "").strip()
        if hint and "Ask" not in hint:
            return hint
        labels = {
            "service_code": "which service (A–E)",
            "professional_slug": "which doctor",
            "day": "which day",
            "starts_at": "which listed time",
            "patient_name": "your full name",
            "patient_email": "your email",
        }
        need = [labels.get(m, m) for m in missing] or ["a bit more detail"]
        day_bit = f" (for {day})" if day else ""
        extra = ""
        if "professional_slug" in missing:
            pros = facts.get("catalog_professionals") or []
            if pros:
                extra = " Our doctors: " + "; ".join(pros[:5]) + "."
        return f"To continue{day_bit}, please tell me {', '.join(need)}.{extra}"
    hint = (facts.get("hint") or "").strip()
    if hint:
        return hint
    return "How can I help you book an appointment?"
