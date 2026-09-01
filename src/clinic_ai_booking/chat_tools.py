"""Clinic booking tools for the custom chat tool loop (allowlist only)."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from typing import Any

from langchain.tools import tool

from clinic_ai_booking.auth import (
    LOGIN_REQUIRED_MESSAGE,
    AuthError,
    cancel_for_user,
    get_user_by_id,
    normalize_email,
    normalize_name,
    reschedule_for_user,
)
from clinic_ai_booking.booking import (
    BookingError,
    book_appointment as engine_book,
    check_start as engine_check_start,
    explain_can_perform,
    get_service_duration,
    list_available_starts as engine_list_starts,
    list_next_available_starts as engine_list_next,
    list_patient_appointments as engine_list_patient,
    list_professionals as engine_list_pros,
    list_services as engine_list_services,
)
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field

from sqlalchemy.orm import Session

from clinic_ai_booking.hours import (
    BREAKS,
    CLOSE,
    OPEN,
    TIMEZONE,
    explain_clinic_day,
    explain_clinic_start,
    to_clinic,
)

# --- Tool runtime (inside chat_tools; agent layer is intentionally absent) ---

_WEEKDAY = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "mon": 0,
    "tue": 1,
    "tues": 1,
    "wed": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
_WEEKDAY_ALT = (
    "monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    "mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun"
)

_ctx: ContextVar["ChatContext | None"] = ContextVar("clinic_chat_ctx", default=None)


def clinic_today(now: datetime | None = None) -> date:
    """Return today's calendar date in Asia/Taipei."""
    clock = now.astimezone(TIMEZONE) if now is not None else datetime.now(TIMEZONE)
    return clock.date()


def resolve_day_phrase(phrase: str, *, today: date | None = None) -> date:
    """Parse today/tomorrow/weekday/YYYY-MM-DD into a clinic calendar date."""
    raw = phrase.strip().lower()
    if not raw:
        raise ValueError("day phrase is empty")
    base = today or clinic_today()
    raw = re.sub(r"[.,;:]+", " ", raw)
    raw = re.sub(r"\b(morning|afternoon|evening|am|pm)\b", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return date.fromisoformat(raw)
    if raw in {"today", "tonight"}:
        return base
    if raw in {"tomorrow", "tmr", "tmrw"}:
        return base + timedelta(days=1)
    if raw in {"day after tomorrow", "the day after tomorrow"}:
        return base + timedelta(days=2)
    m = re.fullmatch(rf"next\s+({_WEEKDAY_ALT})", raw)
    if m:
        target = _WEEKDAY[m.group(1)]
        start = base + timedelta(days=1)
        delta = (target - start.weekday()) % 7
        return start + timedelta(days=delta)
    m = re.fullmatch(rf"(this\s+)?({_WEEKDAY_ALT})", raw)
    if m:
        target = _WEEKDAY[m.group(2)]
        delta = (target - base.weekday()) % 7
        return base + timedelta(days=delta)
    raise ValueError(
        "could not resolve day; use YYYY-MM-DD, today, tomorrow, or a weekday name"
    )


@dataclass
class BookingDraft:
    """In-progress booking choices for tools (optional session draft)."""

    service_code: str | None = None
    professional_slug: str | None = None
    day: str | None = None
    window: str | None = None
    starts_at: str | None = None
    last_summary: dict[str, Any] | None = None

    def to_session(self) -> dict[str, Any]:
        """Serialize for Starlette session storage."""
        return asdict(self)

    @classmethod
    def from_session(cls, raw: Any) -> "BookingDraft":
        """Load draft from session dict, or empty draft."""
        if not isinstance(raw, dict):
            return cls()
        return cls(
            service_code=raw.get("service_code"),
            professional_slug=raw.get("professional_slug"),
            day=raw.get("day"),
            window=raw.get("window"),
            starts_at=raw.get("starts_at"),
            last_summary=raw.get("last_summary")
            if isinstance(raw.get("last_summary"), dict)
            else None,
        )


@dataclass
class ChatContext:
    """Request-scoped identity + DB for booking tools."""

    db: Session
    user_id: int | None
    user_name: str | None
    user_email: str | None
    visitor_name: str | None = None
    visitor_email: str | None = None
    today: date | None = field(default=None)
    draft: BookingDraft = field(default_factory=BookingDraft)

    @property
    def is_authenticated(self) -> bool:
        """True when a logged-in patient is on the session."""
        return self.user_id is not None

    @property
    def has_visitor_contact(self) -> bool:
        """True when sticky visitor name+email are already known."""
        return bool(self.visitor_name and self.visitor_email)

    @property
    def can_book_now(self) -> bool:
        """True when identity is enough to write a booking."""
        return self.is_authenticated or self.has_visitor_contact

    def clinic_day(self) -> date:
        """Clinic today for this turn (Asia/Taipei unless tests override)."""
        return self.today if self.today is not None else clinic_today()

    def patient_name(self) -> str | None:
        """Logged-in name, else sticky visitor name."""
        return self.user_name if self.is_authenticated else self.visitor_name

    def patient_email(self) -> str | None:
        """Logged-in email, else sticky visitor email."""
        return self.user_email if self.is_authenticated else self.visitor_email

    def set_visitor(self, name: str, email: str) -> None:
        """Remember visitor contact for this turn."""
        self.visitor_name = name
        self.visitor_email = email

    def clear_draft(self) -> None:
        """Drop in-progress booking choices after a successful book."""
        self.draft = BookingDraft()


def set_chat_context(context: ChatContext) -> Token:
    """Bind ChatContext for the current tool-dispatch turn."""
    return _ctx.set(context)


def reset_chat_context(token: Token) -> None:
    """Restore the previous chat context binding."""
    _ctx.reset(token)


def get_chat_context() -> ChatContext:
    """Return the active ChatContext, or raise if missing."""
    context = _ctx.get()
    if context is None:
        raise RuntimeError("chat context is not set for this tool call")
    return context


def professional_display_name(db: Session, slug: str) -> str:
    """Return the public doctor name for a slug, or a safe fallback."""
    for row in engine_list_pros(db):
        if row.slug == slug:
            return row.name
    return "the doctor"

from clinic_ai_booking.models import STATUS_PENDING_DOCTOR, Booking, User
from clinic_ai_booking.notify import notify_booking_created

CONTACT_REQUIRED_MESSAGE = (
    "Visitor contact is required before booking. Ask for the patient's full name "
    "and email, call remember_visitor once they provide both, then book. "
    "Do not invent a name or email."
)

# Cap what the model sees so it summarizes instead of dumping every 15-min start.
_EXAMPLE_STARTS = 3


def _parse_day(value: str) -> date:
    """Parse YYYY-MM-DD into a date."""
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise BookingError("day must be YYYY-MM-DD") from exc


def _parse_start(value: str) -> datetime:
    """Parse an ISO datetime (prefer timezone-aware Asia/Taipei)."""
    raw = value.strip()
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise BookingError("starts_at must be an ISO datetime") from exc


def _ok(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=str)


def _err(exc: Exception) -> str:
    return _ok({"ok": False, "error": str(exc)})


def _booking_payload(booking: Booking) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "booking_id": booking.id,
        "professional_slug": booking.professional.slug,
        "service_code": booking.service.code,
        "starts_at": booking.starts_at.isoformat(),
        "ends_at": booking.ends_at.isoformat(),
        "status": booking.status,
        "duration_minutes": booking.service.duration_minutes,
    }
    if booking.status == STATUS_PENDING_DOCTOR:
        payload["patient_message"] = (
            "This service E booking runs into overtime or a break and is "
            "pending_doctor — it needs doctor confirmation before it is final."
        )
    return payload


def _session_user(ctx: ChatContext) -> User | None:
    if ctx.user_id is None:
        return None
    return get_user_by_id(ctx.db, ctx.user_id)


def _resolve_book_contact(
    ctx: ChatContext, patient_name: str, patient_email: str
) -> tuple[str, str]:
    """Prefer logged-in or sticky visitor contact; else tool args (and remember them)."""
    if ctx.is_authenticated:
        return ctx.user_name or "", ctx.user_email or ""
    name = (ctx.visitor_name or patient_name or "").strip()
    email = (ctx.visitor_email or patient_email or "").strip()
    if name and email and not ctx.has_visitor_contact:
        try:
            ctx.set_visitor(normalize_name(name), normalize_email(email))
        except AuthError:
            pass
    return name, email


def _session_label(clock: time) -> str:
    lunch_start, _ = BREAKS[0]
    dinner_start, _ = BREAKS[1]
    if clock < lunch_start:
        return "morning"
    if lunch_start <= clock < BREAKS[0][1]:
        return "lunch_break"
    if clock < dinner_start:
        return "afternoon"
    if dinner_start <= clock < BREAKS[1][1]:
        return "dinner_break"
    return "evening"


def normalize_window(window: str | None) -> str | None:
    """Map free text to morning/afternoon/evening, or None."""
    if not window:
        return None
    raw = window.strip().lower()
    if raw in {"morning", "am", "before lunch"}:
        return "morning"
    if raw in {"afternoon", "pm", "after lunch"}:
        return "afternoon"
    if raw in {"evening", "night", "after dinner"}:
        return "evening"
    if re.search(r"\b(morning|before\s+lunch)\b", raw):
        return "morning"
    if re.search(r"\b(afternoon|after\s+lunch)\b", raw):
        return "afternoon"
    if re.search(r"\b(evening|after\s+dinner)\b", raw):
        return "evening"
    return None


def filter_starts_by_window(
    starts: list[datetime], window: str | None
) -> list[datetime]:
    """Keep starts that fall in morning/afternoon/evening when window is set."""
    label = normalize_window(window)
    if label is None:
        return starts
    return [s for s in starts if _session_label(to_clinic(s).time()) == label]


def format_summary_reply(
    *,
    professional_name: str,
    service_code: str,
    day: str,
    summary: dict[str, Any],
    window: str | None = None,
) -> str:
    """Deterministic short availability reply from a starts_summary payload."""
    blocks = summary.get("blocks") or []
    if window:
        blocks = [b for b in blocks if b.get("label") == window]
    if not blocks and not summary.get("total_starts"):
        return (
            f"No free starts for service {service_code} with {professional_name} "
            f"on {day}. I can check nearby weekdays if you want."
        )
    if not blocks:
        blocks = summary.get("blocks") or []
    duration = int(summary.get("duration_minutes") or 0)
    examples = summary.get("example_starts") or []
    example_bits: list[str] = []
    for iso in examples[:3]:
        try:
            example_bits.append(to_clinic(datetime.fromisoformat(iso)).strftime("%H:%M"))
        except ValueError:
            continue
    if not example_bits and blocks:
        # Fall back to the block range endpoints when examples are missing.
        first = blocks[0]
        example_bits = [str(first["from"])]
        if first.get("to") and first["to"] != first["from"]:
            example_bits.append(str(first["to"]))

    latest = summary.get("latest_start")
    if window and blocks:
        latest = blocks[-1]["to"]
    elif blocks and not latest:
        latest = blocks[-1]["to"]

    window_bit = f" ({window})" if window else ""
    line = (
        f"{professional_name} is available for service {service_code} on {day}"
        f"{window_bit}."
    )
    if duration and latest and window:
        line += (
            f" Service {service_code} takes {duration} minutes, so the latest "
            f"{window} start is {latest}."
        )
    elif duration and latest:
        line += (
            f" Service {service_code} takes {duration} minutes; "
            f"the latest start in this view is {latest}."
        )
    elif latest:
        line += f" The latest start in this view is {latest}."
    if example_bits:
        line += f" Available starts include {', '.join(example_bits)}."
    line += " Which exact start time works for you?"
    return line


def summarize_starts(starts: list[datetime], duration_minutes: int) -> dict[str, Any]:
    """Build a patient-friendly availability summary (no full dump)."""
    if not starts:
        return {
            "blocks": [],
            "latest_start": None,
            "example_starts": [],
            "total_starts": 0,
            "duration_minutes": duration_minutes,
            "hint": "No free starts. Call list_next_available_starts for nearby days.",
        }

    blocks: list[dict[str, Any]] = []
    current_label: str | None = None
    block_first: datetime | None = None
    block_last: datetime | None = None
    block_count = 0

    def flush() -> None:
        nonlocal current_label, block_first, block_last, block_count
        if current_label is None or block_first is None or block_last is None:
            return
        blocks.append(
            {
                "label": current_label,
                "from": block_first.strftime("%H:%M"),
                "to": block_last.strftime("%H:%M"),
                "count": block_count,
            }
        )
        current_label = None
        block_first = None
        block_last = None
        block_count = 0

    for start in starts:
        local = to_clinic(start)
        label = _session_label(local.time())
        if label in {"lunch_break", "dinner_break"}:
            continue
        if label != current_label:
            flush()
            current_label = label
            block_first = local
            block_last = local
            block_count = 1
        else:
            block_last = local
            block_count += 1
    flush()

    latest = to_clinic(starts[-1])
    return {
        "blocks": blocks,
        "latest_start": latest.strftime("%H:%M"),
        "latest_start_iso": latest.isoformat(),
        "example_starts": [s.isoformat() for s in starts[:_EXAMPLE_STARTS]],
        "total_starts": len(starts),
        "duration_minutes": duration_minutes,
        "open_hours": f"{OPEN.strftime('%H:%M')}–{CLOSE.strftime('%H:%M')}",
        "hint": (
            "Prefer patient_reply_hint when present. Explain duration and the latest "
            "start for the named window (e.g. morning). "
            f"List at most {_EXAMPLE_STARTS} example times. Do not say 'N options'. "
            "Do not dump every start. Never say confirmed yet."
        ),
    }


@tool
def resolve_day(phrase: str) -> str:
    """Resolve today/tomorrow/weekday/YYYY-MM-DD to a clinic calendar day (Asia/Taipei).

    Always call this before check_clinic_time or list_available_starts when the
    patient uses relative words like tomorrow or next Monday.
    """
    try:
        day = resolve_day_phrase(phrase, today=get_chat_context().clinic_day())
    except ValueError as exc:
        return _err(BookingError(str(exc)))
    ok, reason = explain_clinic_day(day)
    get_chat_context().draft.day = day.isoformat()
    return _ok(
        {
            "ok": True,
            "phrase": phrase,
            "day": day.isoformat(),
            "weekday": day.strftime("%A"),
            "valid_clinic_day": ok,
            "reason": reason,
        }
    )


@tool
def list_services() -> str:
    """List clinic services A–E with fixed durations (do not invent length)."""
    rows = engine_list_services(get_chat_context().db)
    return _ok(
        {
            "ok": True,
            "services": [
                {
                    "code": row.code,
                    "duration_minutes": row.duration_minutes,
                    "seniors_only": row.seniors_only,
                }
                for row in rows
            ],
        }
    )


@tool
def list_professionals() -> str:
    """List bookable professionals (slug, name, senior flag)."""
    rows = engine_list_pros(get_chat_context().db)
    return _ok(
        {
            "ok": True,
            "professionals": [
                {"slug": row.slug, "name": row.name, "is_senior": row.is_senior}
                for row in rows
            ],
        }
    )


@tool
def check_clinic_time(when: str) -> str:
    """Validate a day (YYYY-MM-DD) or start (ISO datetime) against clinic hours.

    Call resolve_day first for relative phrases. Weekends, off-grid times, outside
    09:00–20:00, and break starts are invalid — stop and explain; do not invent slots.
    """
    del runtime
    raw = when.strip()
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            day = _parse_day(raw)
            ok, reason = explain_clinic_day(day)
            return _ok(
                {
                    "ok": ok,
                    "kind": "day",
                    "when": day.isoformat(),
                    "valid": ok,
                    "reason": reason,
                }
            )
        start = _parse_start(raw)
        ok, reason = explain_clinic_start(start)
        return _ok(
            {
                "ok": ok,
                "kind": "start",
                "when": start.isoformat(),
                "valid": ok,
                "reason": reason,
            }
        )
    except BookingError as exc:
        return _err(exc)


@tool
def remember_visitor(
    patient_name: str,
    patient_email: str,
) -> str:
    """Save visitor name+email for this browser session. Call once; do not re-ask."""
    ctx = get_chat_context()
    if ctx.is_authenticated:
        return _ok(
            {
                "ok": True,
                "saved": False,
                "message": "Patient is logged in; session name/email are used instead.",
                "patient_name": ctx.user_name,
                "patient_email": ctx.user_email,
            }
        )
    try:
        name = normalize_name(patient_name)
        email = normalize_email(patient_email)
    except AuthError as exc:
        return _err(exc)
    ctx.set_visitor(name, email)
    return _ok(
        {
            "ok": True,
            "saved": True,
            "patient_name": name,
            "patient_email": email,
            "message": "Contact saved for this chat session. Do not ask again.",
        }
    )


def format_multi_pro_reply(
    *,
    service_code: str,
    day: str,
    window: str | None,
    rows: list[dict[str, Any]],
) -> str:
    """Patient-facing summary when several doctors have free starts."""
    if not rows:
        win = f" in the {window}" if window else ""
        return (
            f"No free starts for service {service_code} on {day}{win}. "
            "I can check nearby weekdays if you want."
        )
    win = f" ({window})" if window else ""
    lines = [f"For service {service_code} on {day}{win}:"]
    for row in rows:
        name = str(row.get("professional_name") or "a doctor")
        summary = row.get("starts_summary") or {}
        duration = int(summary.get("duration_minutes") or 0)
        latest = summary.get("latest_start")
        examples = summary.get("example_starts") or []
        clocks: list[str] = []
        for iso in examples[:3]:
            try:
                clocks.append(to_clinic(datetime.fromisoformat(iso)).strftime("%H:%M"))
            except ValueError:
                continue
        bit = f"- {name}"
        if duration and latest:
            bit += f": service takes {duration} minutes; latest start {latest}"
        elif latest:
            bit += f": latest start {latest}"
        if clocks:
            bit += f"; available starts include {', '.join(clocks)}"
        lines.append(bit + ".")
    lines.append("Which doctor and exact start time work for you?")
    return "\n".join(lines)


@tool
def list_service_availability(
    service_code: str,
    day: str,
    window: str = "",
) -> str:
    """List every eligible doctor with free starts for one service/day/window.

    Use this when the patient asks who is available (not a named doctor).
    Seniors-only services must include both senior doctors when they have starts.
    """
    try:
        parsed_day = _parse_day(day)
        day_ok, day_reason = explain_clinic_day(parsed_day)
        if not day_ok:
            return _ok(
                {
                    "ok": False,
                    "valid_day": False,
                    "day": parsed_day.isoformat(),
                    "error": day_reason,
                    "professionals": [],
                    "patient_reply_hint": day_reason,
                }
            )
        code = service_code.upper().strip()
        duration = get_service_duration(get_chat_context().db, code)
        win = normalize_window(window) or normalize_window(get_chat_context().draft.window)
        rows_out: list[dict[str, Any]] = []
        for pro in engine_list_pros(get_chat_context().db):
            eligible, _eligibility = explain_can_perform(
                get_chat_context().db, pro.slug, code
            )
            if not eligible:
                continue
            starts = engine_list_starts(
                get_chat_context().db, pro.slug, code, parsed_day
            )
            if win:
                starts = filter_starts_by_window(starts, win)
            if not starts:
                continue
            summary = summarize_starts(starts, duration)
            rows_out.append(
                {
                    "professional_slug": pro.slug,
                    "professional_name": pro.name,
                    "is_senior": pro.is_senior,
                    "starts_summary": summary,
                    "example_starts": [s.isoformat() for s in starts[:3]],
                }
            )
    except BookingError as exc:
        return _err(exc)

    draft = get_chat_context().draft
    draft.service_code = code
    draft.day = parsed_day.isoformat()
    if win:
        draft.window = win
    # Only pin a doctor when exactly one is free; otherwise the patient must choose.
    if len(rows_out) == 1:
        draft.professional_slug = str(rows_out[0]["professional_slug"])
        draft.last_summary = rows_out[0]["starts_summary"]
    else:
        draft.professional_slug = None
        draft.last_summary = None

    hint = format_multi_pro_reply(
        service_code=code,
        day=parsed_day.isoformat(),
        window=win,
        rows=rows_out,
    )
    return _ok(
        {
            "ok": True,
            "valid_day": True,
            "service_code": code,
            "day": parsed_day.isoformat(),
            "window": win,
            "professionals": rows_out,
            "patient_reply_hint": hint,
            "hint": (
                "Name EVERY doctor in professionals with free starts "
                "(e.g. both Dr. Maya Lin and Dr. Jordan Wu for service C). "
                "Do not invent later days unless this tool returned empty."
            ),
        }
    )


@tool
def list_available_starts(
    professional_slug: str,
    service_code: str,
    day: str,
    window: str = "",
    ignore_booking_id: int | None = None,
) -> str:
    """Summarize free starts for one professional, service, and day (YYYY-MM-DD).

    Optional window: morning / afternoon / evening. Returns blocks + a few examples —
    not every 15-minute start. Use resolve_day first for relative dates.
    On seniors-only mismatch, returns a clear error. Do not re-list all professionals
    when the patient already named a doctor.
    """
    try:
        parsed_day = _parse_day(day)
        eligible, eligibility = explain_can_perform(
            get_chat_context().db, professional_slug, service_code
        )
        if not eligible:
            return _ok(
                {
                    "ok": False,
                    "eligible": False,
                    "professional_slug": professional_slug,
                    "service_code": service_code.upper(),
                    "day": parsed_day.isoformat(),
                    "error": eligibility,
                    "hint": (
                        "Reply in one or two short sentences. Do not list all dentists. "
                        "Ask whether to pick a senior doctor or a different service."
                    ),
                    "starts_summary": summarize_starts([], 0),
                }
            )
        day_ok, day_reason = explain_clinic_day(parsed_day)
        if not day_ok:
            return _ok(
                {
                    "ok": False,
                    "eligible": True,
                    "valid_day": False,
                    "day": parsed_day.isoformat(),
                    "error": day_reason,
                    "hint": "Call check_clinic_time / list_next_available_starts for a weekday.",
                    "starts_summary": summarize_starts([], 0),
                }
            )
        starts = engine_list_starts(
            get_chat_context().db,
            professional_slug,
            service_code,
            parsed_day,
            ignore_booking_id=ignore_booking_id,
        )
        win = normalize_window(window) or normalize_window(get_chat_context().draft.window)
        if win:
            starts = filter_starts_by_window(starts, win)
        duration = get_service_duration(get_chat_context().db, service_code)
    except BookingError as exc:
        return _err(exc)

    summary = summarize_starts(starts, duration)
    draft = get_chat_context().draft
    draft.professional_slug = professional_slug
    draft.service_code = service_code.upper()
    draft.day = parsed_day.isoformat()
    if win:
        draft.window = win
    draft.last_summary = summary
    payload: dict[str, Any] = {
        "ok": True,
        "eligible": True,
        "valid_day": True,
        "professional_slug": professional_slug,
        "service_code": service_code.upper(),
        "day": parsed_day.isoformat(),
        "window": win,
        "eligibility": eligibility,
        "starts_summary": summary,
        "patient_reply_hint": format_summary_reply(
            professional_name=professional_display_name(
                get_chat_context().db, professional_slug
            ),
            service_code=service_code.upper(),
            day=parsed_day.isoformat(),
            summary=summary,
            window=win,
        ),
    }
    if not starts:
        payload["hint"] = (
            "No free starts that day"
            + (f" in the {win} window" if win else "")
            + ". Call list_next_available_starts from this day."
        )
    return _ok(payload)


@tool
def list_next_available_starts(
    professional_slug: str,
    service_code: str,
    from_day: str,
    ignore_booking_id: int | None = None,
) -> str:
    """Scan forward from from_day and return the next days with free starts."""
    try:
        eligible, eligibility = explain_can_perform(
            get_chat_context().db, professional_slug, service_code
        )
        if not eligible:
            return _ok(
                {
                    "ok": False,
                    "eligible": False,
                    "error": eligibility,
                    "days": [],
                    "hint": (
                        "Reply briefly. Offer a senior doctor or another service; "
                        "do not dump the full staff list."
                    ),
                }
            )
        from_parsed = _parse_day(from_day)
        duration = get_service_duration(get_chat_context().db, service_code)
        found = engine_list_next(
            get_chat_context().db,
            professional_slug,
            service_code,
            from_parsed,
            ignore_booking_id=ignore_booking_id,
        )
    except BookingError as exc:
        return _err(exc)
    return _ok(
        {
            "ok": True,
            "eligible": True,
            "eligibility": eligibility,
            "days": [
                {
                    "day": row.day.isoformat(),
                    "starts_summary": summarize_starts(list(row.starts), duration),
                    "example_starts": [s.isoformat() for s in row.starts],
                }
                for row in found
            ],
            "hint": "Offer the nearest days briefly; do not dump every start time.",
        }
    )


@tool
def list_patient_appointments(
    patient_email: str = "",
) -> str:
    """List this patient's active bookings. Uses session/sticky email when set."""
    ctx = get_chat_context()
    email = (ctx.patient_email() or patient_email or "").strip()
    if not email:
        return _err(BookingError("patient email is required (or log in / remember_visitor)"))
    try:
        rows = engine_list_patient(ctx.db, email)
    except BookingError as exc:
        return _err(exc)
    return _ok(
        {
            "ok": True,
            "appointments": [
                {
                    "booking_id": row.booking_id,
                    "professional_slug": row.professional_slug,
                    "service_code": row.service_code,
                    "starts_at": row.starts_at.isoformat(),
                    "ends_at": row.ends_at.isoformat(),
                    "status": row.status,
                }
                for row in rows
            ],
        }
    )


@tool
def check_start(
    professional_slug: str,
    service_code: str,
    starts_at: str,
    ignore_booking_id: int | None = None,
) -> str:
    """Validate a proposed start without writing. Duration comes from the service."""
    try:
        eligible, eligibility = explain_can_perform(
            get_chat_context().db, professional_slug, service_code
        )
        if not eligible:
            return _ok({"ok": False, "eligible": False, "error": eligibility})
        plan = engine_check_start(
            get_chat_context().db,
            professional_slug=professional_slug,
            service_code=service_code,
            starts_at=_parse_start(starts_at),
            ignore_booking_id=ignore_booking_id,
        )
    except BookingError as exc:
        return _err(exc)
    draft = get_chat_context().draft
    draft.professional_slug = plan.professional_slug
    draft.service_code = plan.service_code
    draft.day = to_clinic(plan.starts_at).date().isoformat()
    draft.starts_at = plan.starts_at.isoformat()
    needs_contact = not get_chat_context().can_book_now
    return _ok(
        {
            "ok": True,
            "eligible": True,
            "eligibility": eligibility,
            "professional_slug": plan.professional_slug,
            "service_code": plan.service_code,
            "starts_at": plan.starts_at.isoformat(),
            "ends_at": plan.ends_at.isoformat(),
            "status": plan.status,
            "needs_contact": needs_contact,
            "hint": (
                "Start is valid but NOT booked yet. "
                + (
                    "Ask for name+email, call remember_visitor, then book_appointment. "
                    "Never say confirmed until book_appointment returns ok."
                    if needs_contact
                    else "Ask the patient to confirm, then call book_appointment. "
                    "Never say confirmed until book_appointment returns ok."
                )
            ),
        }
    )


@tool
def book_appointment(
    professional_slug: str,
    service_code: str,
    starts_at: str,
    patient_name: str = "",
    patient_email: str = "",
) -> str:
    """Book after the patient confirms a start; contact from login, sticky, or this turn."""
    ctx = get_chat_context()
    name, email = _resolve_book_contact(ctx, patient_name, patient_email)
    if not name.strip() or not email.strip():
        return _ok(
            {
                "ok": False,
                "error": CONTACT_REQUIRED_MESSAGE,
                "needs_contact": True,
            }
        )
    try:
        eligible, eligibility = explain_can_perform(ctx.db, professional_slug, service_code)
        if not eligible:
            return _ok({"ok": False, "eligible": False, "error": eligibility})
        booking = engine_book(
            ctx.db,
            professional_slug=professional_slug,
            service_code=service_code,
            starts_at=_parse_start(starts_at),
            patient_name=name,
            patient_email=email,
            notify=notify_booking_created,
        )
        ctx.db.flush()
    except BookingError as exc:
        return _err(exc)
    ctx.clear_draft()
    return _ok(_booking_payload(booking))


@tool
def cancel_appointment(booking_id: int) -> str:
    """Cancel an active booking. Requires a logged-in patient who owns it."""
    ctx = get_chat_context()
    user = _session_user(ctx)
    try:
        booking = cancel_for_user(ctx.db, booking_id, user)
        ctx.db.flush()
    except (AuthError, BookingError) as exc:
        return _err(exc)
    return _ok({"ok": True, "booking_id": booking.id, "status": booking.status})


@tool
def reschedule_appointment(
    booking_id: int,
    starts_at: str,
) -> str:
    """Reschedule to a new start. Requires a logged-in patient who owns it."""
    ctx = get_chat_context()
    user = _session_user(ctx)
    try:
        booking = reschedule_for_user(
            ctx.db, booking_id, _parse_start(starts_at), user
        )
        ctx.db.flush()
    except (AuthError, BookingError) as exc:
        return _err(exc)
    return _ok(_booking_payload(booking))


BOOKING_TOOLS = [
    resolve_day,
    list_services,
    list_professionals,
    check_clinic_time,
    remember_visitor,
    list_service_availability,
    list_available_starts,
    list_next_available_starts,
    list_patient_appointments,
    check_start,
    book_appointment,
    cancel_appointment,
    reschedule_appointment,
]

TOOL_ALLOWLIST = frozenset(t.name for t in BOOKING_TOOLS)

__all__ = [
    "BOOKING_TOOLS",
    "BookingDraft",
    "ChatContext",
    "get_chat_context",
    "set_chat_context",
    "reset_chat_context",
    "resolve_day_phrase",

    "CONTACT_REQUIRED_MESSAGE",
    "LOGIN_REQUIRED_MESSAGE",
    "TOOL_ALLOWLIST",
    "book_appointment",
    "cancel_appointment",
    "check_clinic_time",
    "check_start",
    "list_available_starts",
    "list_next_available_starts",
    "list_patient_appointments",
    "list_professionals",
    "list_service_availability",
    "list_services",
    "remember_visitor",
    "resolve_day",
    "reschedule_appointment",
    "summarize_starts",
    "filter_starts_by_window",
    "format_summary_reply",
    "format_multi_pro_reply",
    "normalize_window",
]
