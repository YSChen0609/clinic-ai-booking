"""LLM extract: scope + structured draft fields for one user turn.

Design (assignment MVP): treat the local chat model as a noisy classifier.
The LLM proposes TurnExtract fields; this module sanitizes and merges them into
BookingDraft. Clinic rules and free slots stay in domain/booking.py — extract
must not invent availability. Closed selects: service A–E only; doctors map to
catalog slugs; unknown letters (e.g. F) stay in scope with a catalog reject.
See internal.md (Chat extract design) and TODOS.md for constrained-decoding
upgrades.
"""

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
from clinic_ai_booking.chat.aliases import (
    SERVICE_CODES,
    catalog_prompt_block,
    normalize_professional_alias,
    normalize_service_alias,
)
from clinic_ai_booking.chat.context import ChatContext
from clinic_ai_booking.chat.resolve import (
    clock_from_user_text,
    date_cheat_sheet,
    detect_professional_in_text,
    detect_service_in_text,
    detect_time_band,
    detect_unknown_service_letter,
    is_ambiguous_next_weekday,
    is_concrete_clock,
    is_doctor_confirm_phrase,
    is_doctor_reject_phrase,
    next_weekday_choices,
    parse_clinic_day,
    pick_day_clarify_option,
    professional_match_is_ambiguous,
    professional_match_is_exact,
    rank_professionals,
    resolve_day_intent,
    resolve_service_code,
    wants_other_slot,
    wants_same_day_context,
    wants_same_professional,
)
from clinic_ai_booking.domain.booking import list_professionals

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
WeekdayWhen = Literal["this", "next", "upcoming", "week_after_next"]


class TurnExtract(BaseModel):
    """Structured fields taken from the latest user message (+ recent chat)."""

    in_scope: bool = Field(description="True if about clinic services, hours, doctors, booking")
    intent: Intent = Field(default="book")
    service_code: str | None = Field(
        default=None,
        description="Service letter A–E only (never invent other codes)",
    )
    professional: str | None = Field(
        default=None,
        description="Doctor from the allowed catalog / aliases, or null",
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
        description=(
            "this | next | upcoming | week_after_next — for day_kind=weekday; "
            "use next when the user said 'next Friday' (server will ask which week); "
            "week_after_next for 'next next Friday' / Friday in two weeks"
        ),
    )
    day_phrase: str | None = Field(
        default=None,
        description=(
            "Raw relative day phrase when structured day_kind is awkward "
            "(e.g. 'the Wednesday after next'). Server dateparser resolves it. "
            "Leave null if day_kind/weekday fields are enough."
        ),
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

{cheat_sheet}

{catalog}

In scope: services A–E, hours, doctors, availability, booking, cancel, reschedule, contact.
Out of scope: anything else (not clinic). Naming an unknown service letter (e.g. F) \
is still in scope — set service_code null; the server will reject it.

Day handling (important):
- Do NOT invent a YYYY-MM-DD yourself unless the user typed an explicit ISO date \
(then day_kind=absolute and day_absolute=that date).
- Prefer day_kind + weekday fields. For awkward phrases ("Wednesday after next", \
"in two weeks on Friday") set day_phrase to the user's words and leave day null; \
the server dateparser resolves it.
- Examples (Today is Monday):
  - "tmr" / "tomorrow" → day_kind=tomorrow
  - "next Friday" → day_kind=weekday, weekday=friday, weekday_when=next \
(server will ask which Friday)
  - "next next Friday" / "Friday in two weeks" → day_kind=weekday, \
weekday=friday, weekday_when=week_after_next
  - "same day" / "that Friday" → day_kind=none (server reuses last booked day)
  - "Wed" / "Wednesday" / "I said Wed" → day_kind=weekday, weekday=wednesday, \
weekday_when=upcoming (use Recent chat if the latest line is only a correction)
  - "this Monday" → day_kind=weekday, weekday=monday, weekday_when=this

Other rules:
- Use Recent chat when the latest message is vague ("I said Wed", "him", "that day").
- service_code must be one of A–E when clear; else null.
- professional must match the allowed doctor list / aliases; else null. \
For "him"/"her" alone leave professional null (server uses memory).
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
    system = EXTRACT_SYSTEM.format(
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        cheat_sheet=date_cheat_sheet(today),
        catalog=catalog_prompt_block(),
    )
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

    # Clamp service to A–E via alias table. Invalid letters → null (never invent).
    raw_service = data.get("service_code")
    aliased = normalize_service_alias(raw_service) if raw_service else None
    if detect_unknown_service_letter(user_text):
        data["service_code"] = None
        data["in_scope"] = True
        if (data.get("intent") or "other") == "other":
            data["intent"] = "book"
    elif aliased:
        data["service_code"] = aliased
    elif raw_service and str(raw_service).strip().upper() in SERVICE_CODES:
        data["service_code"] = str(raw_service).strip().upper()
    elif raw_service:
        data["service_code"] = None

    # "next Friday" is ambiguous — leave day null; apply_extract sets clarify options.
    if (
        is_ambiguous_next_weekday(user_text)
        or (
            (data.get("day_kind") or "").strip().lower() == "weekday"
            and (data.get("weekday_when") or "").strip().lower() == "next"
        )
    ) and (data.get("day_kind") or "").strip().lower() != "absolute":
        data["day"] = None
    else:
        resolved = None
        phrase = (data.get("day_phrase") or "").strip()
        if phrase and (data.get("day_kind") or "none").strip().lower() in {
            "none",
            "",
        }:
            resolved = parse_clinic_day(phrase, today)
        if resolved is None:
            resolved = resolve_day_intent(
                today,
                day_kind=data.get("day_kind"),
                day_absolute=data.get("day_absolute"),
                weekday=data.get("weekday"),
                weekday_when=data.get("weekday_when"),
            )
        if resolved is None and phrase:
            resolved = parse_clinic_day(phrase, today)
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
    before = _draft_booking_fingerprint(ctx.draft)
    _apply_contact_from_user_text(ctx, extracted, user_text=user_text)

    unknown = detect_unknown_service_letter(user_text) if user_text else None
    if unknown:
        # Named letter outside A–E: do not invent/substitute a catalog code.
        # Keep any previously locked valid service until they pick a real letter.
        pass
    else:
        code = None
        if extracted.service_code:
            code = resolve_service_code(ctx.db, extracted.service_code)
            if not code:
                code = normalize_service_alias(extracted.service_code)
                if code:
                    code = resolve_service_code(ctx.db, code)
        if not code and user_text:
            code = detect_service_in_text(ctx.db, user_text)
        if code:
            ctx.draft.service_code = code

    _apply_professional(ctx, extracted, user_text=user_text)
    _apply_day(ctx, extracted, user_text=user_text)

    if extracted.time_band in {"morning", "afternoon", "evening"}:
        ctx.draft.time_band = extracted.time_band

    if wants_other_slot(user_text):
        ctx.draft.starts_at = None
        ctx.draft.starts_at_confirmed = False

    named = clock_from_user_text(user_text)
    if named:
        ctx.draft.starts_at = named
        ctx.draft.time_band = None
        ctx.draft.starts_at_confirmed = False
        # Follow-up clock after "same day" book: keep last_day, not clinic today.
        if not ctx.draft.day and ctx.memory.last_day:
            ctx.draft.day = ctx.memory.last_day
            ctx.draft.day_clarify_options = []
    elif detect_time_band(user_text):
        ctx.draft.starts_at = None
        ctx.draft.starts_at_confirmed = False

    if not is_concrete_clock(ctx.draft.starts_at):
        ctx.draft.starts_at = None

    if _draft_booking_fingerprint(ctx.draft) != before:
        ctx.draft.book_confirmed = False

    # Affirm a pending snapped slot ("yes" → 09:15).
    if (
        ctx.draft.starts_at
        and not ctx.draft.starts_at_confirmed
        and is_doctor_confirm_phrase(user_text)
        and not clock_from_user_text(user_text)
    ):
        ctx.draft.starts_at_confirmed = True
        return

    # Affirm checkout before DB write.
    if (
        not ctx.draft.book_confirmed
        and _draft_ready_for_checkout(ctx)
        and is_doctor_confirm_phrase(user_text)
        and not clock_from_user_text(user_text)
        and not is_doctor_reject_phrase(user_text)
    ):
        ctx.draft.book_confirmed = True


def _draft_booking_fingerprint(draft: object) -> tuple[object, ...]:
    """Fields that invalidate a prior checkout confirmation."""
    return (
        getattr(draft, "service_code", None),
        getattr(draft, "professional_slug", None),
        getattr(draft, "day", None),
        getattr(draft, "starts_at", None),
    )


def _draft_ready_for_checkout(ctx: ChatContext) -> bool:
    """True when draft + contact are enough to show a book confirmation."""
    d = ctx.draft
    return bool(
        d.service_code
        and d.professional_slug
        and d.professional_confirmed
        and d.day
        and d.starts_at
        and ctx.can_book_now
    )


def _apply_day(ctx: ChatContext, extracted: TurnExtract, *, user_text: str) -> None:
    """Merge day / next-weekday clarify into the draft."""
    draft = ctx.draft

    if draft.day_clarify_options:
        picked = pick_day_clarify_option(user_text, draft.day_clarify_options)
        if picked:
            if draft.day and draft.day != picked:
                draft.starts_at = None
                draft.starts_at_confirmed = False
            draft.day = picked
            draft.day_clarify_options = []
            return

    # Bare "yes" / confirm must not invent "next Friday" from chat history.
    if _is_confirm_only_turn(user_text):
        return

    # Clock-only follow-up must not overwrite a locked day via LLM day invent.
    if clock_from_user_text(user_text) and draft.day and not wants_same_day_context(
        user_text
    ):
        if not is_ambiguous_next_weekday(user_text) and not re.search(
            r"\b(today|tomorrow|tmr|monday|tuesday|wednesday|thursday|"
            r"friday|saturday|sunday)\b",
            user_text.lower(),
        ):
            return

    weekday = (extracted.weekday or "").strip().lower() or None
    needs_next_clarify = is_ambiguous_next_weekday(user_text) or (
        (extracted.day_kind or "").strip().lower() == "weekday"
        and (extracted.weekday_when or "").strip().lower() == "next"
        and weekday is not None
    )
    if needs_next_clarify:
        if not weekday:
            match = re.search(
                r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
                user_text.lower(),
            )
            if match:
                weekday = match.group(1)
        if weekday:
            choices = next_weekday_choices(ctx.clinic_day(), weekday)
            if choices is not None:
                upcoming, following = choices
                draft.day = None
                draft.day_clarify_options = [upcoming.isoformat(), following.isoformat()]
                draft.starts_at = None
                draft.starts_at_confirmed = False
                return

    # Soft same-day beats an LLM-invented weekday when memory has a day.
    if ctx.memory.last_day and wants_same_day_context(user_text):
        if not re.search(r"\b\d{4}-\d{2}-\d{2}\b", user_text) and not re.search(
            r"\b\d{1,2}[/-]\d{1,2}\b", user_text
        ):
            draft.day = ctx.memory.last_day
            draft.day_clarify_options = []
            return

    if extracted.day:
        day = extracted.day.strip()[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            if draft.day and draft.day != day:
                draft.starts_at = None
                draft.starts_at_confirmed = False
            draft.day = day
            draft.day_clarify_options = []
    elif ctx.memory.last_day and (
        wants_same_day_context(user_text)
        or (wants_same_professional(user_text) and detect_time_band(user_text))
    ):
        draft.day = ctx.memory.last_day
        draft.day_clarify_options = []


def _is_confirm_only_turn(user_text: str) -> bool:
    """True for bare yes/ok with no new day/time/service/doctor words."""
    if not is_doctor_confirm_phrase(user_text):
        return False
    if clock_from_user_text(user_text):
        return False
    if detect_unknown_service_letter(user_text):
        return False
    lowered = user_text.lower().strip()
    if re.search(
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"today|tomorrow|tmr|next|service|[a-e]|dr\.?|doctor)\b",
        lowered,
    ):
        return False
    return True


def _apply_professional(
    ctx: ChatContext, extracted: TurnExtract, *, user_text: str
) -> None:
    """Resolve doctor: lock unique matches; confirm only when ambiguous."""
    draft = ctx.draft

    # Affirm / reject a pending proposal before looking for a new name.
    # Only while unconfirmed — leftover candidates after a lock must not re-open
    # the picker (e.g. "Well, 13:00" must not fuzzy-unlock the doctor).
    pending = bool(
        draft.professional_slug and not draft.professional_confirmed
    ) or bool(
        draft.professional_candidates and not draft.professional_confirmed
    )
    if pending:
        if is_doctor_reject_phrase(user_text):
            draft.professional_slug = None
            draft.professional_confirmed = False
            draft.professional_candidates = []
            return
        picked = _pick_doctor_from_confirm_text(ctx, user_text, exact_only=True)
        if picked:
            draft.professional_slug = picked
            draft.professional_confirmed = True
            draft.professional_candidates = [picked]
            return
        if (
            draft.professional_slug
            and not professional_match_is_ambiguous(
                rank_professionals(ctx.db, draft.professional_slug)
            )
            and is_doctor_confirm_phrase(user_text)
        ):
            draft.professional_confirmed = True
            draft.professional_candidates = [draft.professional_slug]
            return
        fuzzy = _pick_doctor_from_confirm_text(ctx, user_text, exact_only=False)
        if fuzzy:
            # Typo / soft match — keep the full candidate list and ask yes.
            draft.professional_slug = fuzzy
            draft.professional_confirmed = False
            if not draft.professional_candidates:
                draft.professional_candidates = [fuzzy]
            return
        # Still waiting on a doctor choice; do not invent from LLM history.
        return

    named = detect_professional_in_text(ctx.db, user_text)

    # Soft "him"/"with him" / "same doctor" wins unless the user named another.
    if wants_same_professional(user_text) and ctx.memory.last_professional_slug:
        if not named or named == ctx.memory.last_professional_slug:
            draft.professional_slug = ctx.memory.last_professional_slug
            draft.professional_confirmed = True
            draft.professional_candidates = [ctx.memory.last_professional_slug]
            if not draft.day and ctx.memory.last_day:
                draft.day = ctx.memory.last_day
                draft.day_clarify_options = []
            return

    # Locked doctor stays unless this message clearly names someone.
    if draft.professional_confirmed and draft.professional_slug and not named:
        query = (extracted.professional or "").strip()
        if not query or not _professional_named_in_text(query, user_text):
            return

    query = (extracted.professional or "").strip() or None
    if query and not _professional_named_in_text(query, user_text) and not named:
        # LLM often re-emits a prior doctor on contact-only turns — ignore.
        query = None
    matches = rank_professionals(ctx.db, query) if query else []
    if not matches and user_text:
        # Fall back to unique in-text detect, then rank that slug for confirm.
        detected = named or detect_professional_in_text(ctx.db, user_text)
        if detected:
            matches = rank_professionals(ctx.db, detected)
        else:
            soft_name = _soft_doctor_name_from_text(user_text)
            if soft_name:
                matches = rank_professionals(ctx.db, soft_name)
            else:
                # "senior" alone → both seniors as candidates
                soft = rank_professionals(ctx.db, _soft_doctor_query(user_text) or "")
                if soft:
                    matches = soft

    if not matches and wants_same_professional(user_text):
        remembered = ctx.memory.last_professional_slug
        if remembered:
            draft.professional_slug = remembered
            draft.professional_confirmed = True
            draft.professional_candidates = [remembered]
        return

    if not matches:
        return

    if professional_match_is_ambiguous(matches):
        draft.professional_slug = None
        draft.professional_confirmed = False
        draft.professional_candidates = [m.slug for m in matches]
        return

    top = matches[0]
    if draft.professional_slug == top.slug and draft.professional_confirmed:
        return
    draft.professional_slug = top.slug
    draft.professional_candidates = [top.slug]
    # Clear unique name/slug → lock; soft fuzzy still needs a yes.
    draft.professional_confirmed = professional_match_is_exact(top)


def _professional_named_in_text(professional: str, user_text: str) -> bool:
    """True when the extract doctor phrase (or a token) appears in the user text."""
    key = re.sub(r"^(dr\.?|doctor)\s+", "", professional.strip().lower())
    key = re.sub(r"\s+", " ", key).strip()
    lowered = user_text.lower()
    if key and key in lowered:
        return True
    tokens = [t for t in re.split(r"\W+", key) if len(t) >= 3]
    return any(re.search(rf"\b{re.escape(t)}\b", lowered) for t in tokens)


def _soft_doctor_query(user_text: str) -> str | None:
    """Pull junior/senior role words when no proper name was extracted."""
    lowered = user_text.lower()
    if re.search(r"\bjunior\b", lowered):
        return "junior"
    if re.search(r"\bsenior\b", lowered):
        return "senior"
    return None


def _soft_doctor_name_from_text(user_text: str) -> str | None:
    """Pull a likely doctor token (e.g. Lon from 'Dr. Lon') for fuzzy rank."""
    text = user_text.lower()
    match = re.search(r"\b(?:dr\.?|doctor)\s+([a-z]{2,})\b", text)
    if match:
        return match.group(1)
    match = re.search(r"\bwith\s+(?:dr\.?\s+)?([a-z]{2,})\b", text)
    if match and match.group(1) not in {"a", "an", "the", "him", "her", "me"}:
        return match.group(1)
    return None


def _pick_doctor_from_confirm_text(
    ctx: ChatContext, user_text: str, *, exact_only: bool
) -> str | None:
    """Map '1' / name / slug to a pending candidate slug."""
    cands = ctx.draft.professional_candidates
    if not cands and ctx.draft.professional_slug:
        cands = [ctx.draft.professional_slug]
    if not cands:
        return None
    lowered = user_text.lower().strip()
    index_match = re.fullmatch(r"#?\s*([1-9])\.?", lowered)
    if index_match:
        idx = int(index_match.group(1)) - 1
        if 0 <= idx < len(cands):
            return cands[idx]

    alias = normalize_professional_alias(user_text)
    if alias and alias in cands:
        return alias

    # Exact last-name / slug token among candidates (e.g. "Lin", "Dr. Wu").
    for slug in cands:
        if slug.lower() in lowered:
            return slug
    by_slug = {row.slug: row for row in list_professionals(ctx.db)}
    for slug in cands:
        row = by_slug.get(slug)
        if row is None:
            continue
        name_l = row.name.lower()
        parts = [
            p
            for p in re.findall(r"[a-z]+", name_l)
            if p not in {"dr", "doctor"} and len(p) >= 3
        ]
        for part in parts:
            if re.search(rf"\b{re.escape(part)}\b", lowered):
                return slug

    if exact_only:
        return None

    matches = rank_professionals(ctx.db, user_text)
    for match in matches:
        if match.slug in cands and not professional_match_is_exact(match):
            return match.slug
        if match.slug in cands and professional_match_is_exact(match):
            return match.slug
    return None


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
