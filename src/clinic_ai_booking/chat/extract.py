"""LLM extract: scope + structured draft fields for one user turn."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from clinic_ai_booking.auth import normalize_email, normalize_name
from clinic_ai_booking.chat.context import ChatContext
from clinic_ai_booking.chat.resolve import (
    clock_from_user_text,
    detect_professional_in_text,
    detect_service_in_text,
    detect_time_band,
    is_concrete_clock,
    resolve_day_intent,
    resolve_professional_slug,
    resolve_service_code,
    wants_other_slot,
    wants_same_day_context,
    wants_same_professional,
)

logger = logging.getLogger(__name__)

Intent = Literal["book", "cancel", "reschedule", "info", "other"]
TimeBand = Literal["morning", "afternoon", "evening"]
DayKind = Literal[
    "none",
    "absolute",
    "today",
    "tomorrow",
    "day_after_tomorrow",
    "weekday",
]
WeekdayName = Literal[
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]
WeekdayWhen = Literal["this", "next", "upcoming"]


class TurnExtract(BaseModel):
    """Structured fields taken from the latest user message (+ recent chat)."""

    in_scope: bool = Field(description="True if about clinic services, hours, doctors, booking")
    intent: Intent = Field(default="book")
    service_code: str | None = Field(
        default=None, description="Service letter A–E if mentioned"
    )
    professional: str | None = Field(
        default=None, description="Doctor name or slug if mentioned"
    )
    # LLM understands language; server calculator turns this into YYYY-MM-DD.
    day_kind: DayKind = Field(
        default="none",
        description=(
            "How the user named the day: none | absolute | today | tomorrow | "
            "day_after_tomorrow | weekday. Use tomorrow for tmr/tmrw. "
            "Use weekday for Mon/Wed/Friday/etc. (normalize abbreviations to full names)."
        ),
    )
    day_absolute: str | None = Field(
        default=None,
        description="YYYY-MM-DD only when day_kind=absolute and the user gave that date",
    )
    weekday: WeekdayName | None = Field(
        default=None,
        description="Full weekday name when day_kind=weekday (Wed→wednesday, Fri→friday)",
    )
    weekday_when: WeekdayWhen | None = Field(
        default=None,
        description="this | next | upcoming — for day_kind=weekday; next Friday = following week",
    )
    # Filled by server after resolve_day_intent (not for the LLM to invent).
    day: str | None = Field(
        default=None,
        description="YYYY-MM-DD computed by server; leave null in LLM output",
    )
    time_band: TimeBand | None = Field(
        default=None,
        description="morning/afternoon/evening when user said a part of day, not a clock",
    )
    starts_at: str | None = Field(
        default=None,
        description="Only an explicit clock (e.g. 09:00 or 9am) or ISO — never invent",
    )
    patient_name: str | None = None
    patient_email: str | None = None


EXTRACT_SYSTEM = """\
You extract booking fields for a dental clinic chatbot.
Clinic timezone Asia/Taipei. Today is {today} ({weekday}).

In scope: services A–E, hours, doctors, availability, booking, cancel, reschedule, contact.
Out of scope: anything else.

Day handling (important):
- Do NOT invent a YYYY-MM-DD yourself unless the user typed an explicit ISO date \
(then day_kind=absolute and day_absolute=that date).
- Otherwise set day_kind + weekday fields and leave day null. The server calculator \
will turn your intent into a calendar date.
- Examples (Today is Monday):
  - "tmr" / "tomorrow" → day_kind=tomorrow
  - "next Friday" → day_kind=weekday, weekday=friday, weekday_when=next
  - "Wed" / "Wednesday" / "I said Wed" → day_kind=weekday, weekday=wednesday, \
weekday_when=upcoming (use Recent chat if the latest line is only a correction)
  - "this Monday" → day_kind=weekday, weekday=monday, weekday_when=this

Other rules:
- Use Recent chat when the latest message is vague ("I said Wed", "him", "that day").
- service_code is a single letter A–E when clear; else null.
- professional is a name or slug fragment when clear; else null.
- "morning" / "afternoon" / "evening" → set time_band only. Do NOT set starts_at for those words.
- starts_at ONLY when the user named an explicit clock (09:00, 9am, 14:30). Never invent times.
- intent=cancel or reschedule ONLY if they clearly ask to cancel or reschedule.
- Do not invent doctors, services, or times absent from the latest message and Recent chat.
"""


def extract_turn(
    model: BaseChatModel,
    user_text: str,
    ctx: ChatContext,
    *,
    recent_history: str = "",
) -> TurnExtract:
    """Run structured extract; then resolve day intent with the server calculator."""
    today = ctx.clinic_day()
    system = EXTRACT_SYSTEM.format(today=today.isoformat(), weekday=today.strftime("%A"))
    draft = ctx.draft.to_session()
    history_block = recent_history.strip() or "(none)"
    human = (
        f"Current draft: {json.dumps(draft)}\n"
        f"Thread memory: {json.dumps(ctx.memory.to_session())}\n"
        f"Known patient_name: {ctx.patient_name!r}\n"
        f"Known patient_email: {ctx.patient_email!r}\n"
        f"Recent chat (compressed):\n{history_block}\n\n"
        f"Latest user message:\n{user_text}"
    )
    try:
        structured = model.with_structured_output(TurnExtract)
        result = structured.invoke(
            [SystemMessage(content=system), HumanMessage(content=human)]
        )
        if isinstance(result, TurnExtract):
            extracted = result
        elif isinstance(result, dict):
            extracted = TurnExtract.model_validate(result)
        else:
            extracted = _extract_json_fallback(model, system, human)
    except Exception:
        logger.exception("structured extract failed; falling back to JSON parse")
        extracted = _extract_json_fallback(model, system, human)
    return sanitize_extract(extracted, user_text=user_text, today=today)


def sanitize_extract(
    extracted: TurnExtract,
    *,
    user_text: str,
    today: date,
    recent_history: str = "",
) -> TurnExtract:
    """Apply server day calculator + strip invented clocks. No weekday-abbreviation regex."""
    del recent_history  # history is for the LLM only
    data = extracted.model_dump()

    resolved = resolve_day_intent(
        today,
        day_kind=data.get("day_kind"),
        day_absolute=data.get("day_absolute"),
        weekday=data.get("weekday"),
        weekday_when=data.get("weekday_when"),
    )
    data["day"] = resolved.isoformat() if resolved is not None else None

    band = data.get("time_band") or detect_time_band(user_text)
    data["time_band"] = band if band in {"morning", "afternoon", "evening"} else None

    if wants_other_slot(user_text):
        data["starts_at"] = None
        data["intent"] = "book"

    # Prefer a clock the user typed (14:00 / 1400 / 2pm) over LLM invention.
    named = clock_from_user_text(user_text)
    if named:
        data["starts_at"] = named
        data["time_band"] = None
    elif not is_concrete_clock(data.get("starts_at")):
        data["starts_at"] = None
    else:
        # LLM clock without a user-typed clock → drop
        data["starts_at"] = None

    return TurnExtract.model_validate(data)


def apply_extract(ctx: ChatContext, extracted: TurnExtract, *, user_text: str = "") -> None:
    """Merge non-null extract fields into ChatContext draft and contact."""
    _apply_contact_from_user_text(ctx, extracted, user_text=user_text)

    code = None
    if extracted.service_code:
        code = resolve_service_code(ctx.db, extracted.service_code)
    if not code and user_text:
        code = detect_service_in_text(ctx.db, user_text)
    if code:
        ctx.draft.service_code = code

    slug = None
    if extracted.professional:
        slug = resolve_professional_slug(ctx.db, extracted.professional)
    if not slug and user_text:
        slug = detect_professional_in_text(ctx.db, user_text)
    if not slug and wants_same_professional(user_text):
        slug = ctx.memory.last_professional_slug
    if slug:
        ctx.draft.professional_slug = slug

    if extracted.day:
        day = extracted.day.strip()[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            # Only clear the chosen slot when the calendar day actually changes.
            if ctx.draft.day and ctx.draft.day != day:
                ctx.draft.starts_at = None
            ctx.draft.day = day
    elif ctx.memory.last_day and (
        wants_same_day_context(user_text)
        or (wants_same_professional(user_text) and detect_time_band(user_text))
    ):
        # No new day intent from LLM — reuse last booked day for "also … with him"
        ctx.draft.day = ctx.memory.last_day

    if extracted.time_band in {"morning", "afternoon", "evening"}:
        ctx.draft.time_band = extracted.time_band

    if wants_other_slot(user_text):
        ctx.draft.starts_at = None

    named = clock_from_user_text(user_text)
    if named:
        ctx.draft.starts_at = named
        ctx.draft.time_band = None
    elif detect_time_band(user_text):
        ctx.draft.starts_at = None

    if not is_concrete_clock(ctx.draft.starts_at):
        ctx.draft.starts_at = None


def _apply_contact_from_user_text(
    ctx: ChatContext, extracted: TurnExtract, *, user_text: str
) -> None:
    """Set visitor contact only from an email the user typed in this message."""
    if ctx.is_authenticated:
        return

    email_in_text = _email_in_text(user_text)
    if not email_in_text:
        return

    try:
        ctx.patient_email = normalize_email(email_in_text)
    except ValueError:
        return

    name = extracted.patient_name
    if name:
        try:
            ctx.patient_name = normalize_name(name)
        except ValueError:
            pass
    if not ctx.patient_name:
        inferred = _name_before_email(user_text, email_in_text)
        if inferred:
            try:
                ctx.patient_name = normalize_name(inferred)
            except ValueError:
                pass
    ctx.contact_confirmed = bool(ctx.patient_name and ctx.patient_email)


def _email_in_text(user_text: str) -> str | None:
    """Return the first email-looking token in the user message."""
    match = re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", user_text, re.I)
    return match.group(0) if match else None


def _name_before_email(user_text: str, email: str) -> str | None:
    """Parse 'Ten, ten@x.com' / 'use: Ten, ten@x.com' style contact lines."""
    match = re.search(
        rf"(?:use:?\s*)?([^,\n@]+?)\s*,\s*{re.escape(email)}",
        user_text,
        flags=re.I,
    )
    if match:
        return match.group(1).strip()
    return None


def _extract_json_fallback(
    model: BaseChatModel, system: str, human: str
) -> TurnExtract:
    """Ask for JSON when structured output is unavailable."""
    result = model.invoke(
        [
            SystemMessage(
                content=system
                + "\nReply with ONLY a JSON object matching the extract schema."
            ),
            HumanMessage(content=human),
        ]
    )
    text = result.content if isinstance(result.content, str) else str(result.content)
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return TurnExtract(in_scope=False, intent="other")
    try:
        return TurnExtract.model_validate(json.loads(match.group(0)))
    except Exception:
        logger.exception("extract JSON parse failed")
        return TurnExtract(in_scope=False, intent="other")
