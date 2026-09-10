"""LLM tools over the booking engine."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any

from langchain.tools import BaseTool, ToolRuntime, tool

from clinic_ai_booking.auth import (
    AuthError,
    cancel_for_user,
    get_user_by_id,
    normalize_email,
    normalize_name,
    reschedule_for_user,
)
from clinic_ai_booking.domain.booking import (
    BookingError,
    DayAvailability,
    PatientAppointment,
    SlotPlan,
    booking_cancelled_dict,
    booking_created_dict,
    booking_rescheduled_dict,
    book_appointment as engine_book,
    check_start as engine_check_start,
    list_available_starts as engine_list_available_starts,
    list_next_available_starts as engine_list_next_available_starts,
    list_patient_appointments as engine_list_patient_appointments,
    list_professionals as engine_list_professionals,
    list_services as engine_list_services,
)
from clinic_ai_booking.chat.catalog import reject_unknown_catalog
from clinic_ai_booking.chat.context import ChatContext
from clinic_ai_booking.chat.responses import (
    CONTACT_REQUIRED_MESSAGE,
    NO_FIELDS_TO_UPDATE,
    PATIENT_EMAIL_UNKNOWN,
    draft_missing_message,
)
from clinic_ai_booking.domain.hours import (
    explain_clinic_day as hours_explain_clinic_day,
    explain_clinic_start as hours_explain_clinic_start,
    to_clinic,
)


@tool
def get_session_info(runtime: ToolRuntime[ChatContext]) -> str:
    """Return clinic today, patient identity, and booking draft for this session."""
    ctx = runtime.context
    today = ctx.clinic_day()
    return _ok(
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        authenticated=ctx.is_authenticated,
        patient_name=ctx.patient_name,
        patient_email=ctx.patient_email,
        can_book_now=ctx.can_book_now,
        draft=ctx.draft.to_session(),
        hint="Call this before asking for name, email, or today's date again.",
    )


@tool
def update_context(
    runtime: ToolRuntime[ChatContext],
    patient_name: str | None = None,
    patient_email: str | None = None,
    service_code: str | None = None,
    professional_slug: str | None = None,
    day: str | None = None,
    starts_at: str | None = None,
) -> str:
    """Save contact or booking choices the user just provided; omit unknown fields."""
    ctx = runtime.context
    updated: list[str] = []

    try:
        if patient_name is not None and patient_name.strip():
            ctx.patient_name = normalize_name(patient_name)
            updated.append("patient_name")
        if patient_email is not None and patient_email.strip():
            ctx.patient_email = normalize_email(patient_email)
            updated.append("patient_email")
    except AuthError as exc:
        return _err(str(exc))

    if not ctx.is_authenticated and (
        "patient_name" in updated or "patient_email" in updated
    ):
        ctx.contact_confirmed = ctx.has_contact

    catalog_err = reject_unknown_catalog(
        ctx.db,
        professional_slug=professional_slug,
        service_code=service_code,
    )
    if catalog_err:
        return _err(catalog_err)

    draft_fields = {
        "service_code": service_code.upper().strip() if service_code else None,
        "professional_slug": professional_slug,
        "day": day,
        "starts_at": starts_at,
    }
    for field, raw in draft_fields.items():
        if raw is None:
            continue
        cleaned = raw.strip()
        if not cleaned:
            continue
        setattr(ctx.draft, field, cleaned)
        updated.append(field)

    if not updated:
        return _err(NO_FIELDS_TO_UPDATE)

    return _ok(
        updated=updated,
        can_book_now=ctx.can_book_now,
        patient_name=ctx.patient_name,
        patient_email=ctx.patient_email,
        draft=ctx.draft.to_session(),
    )


@tool
def list_services(runtime: ToolRuntime[ChatContext]) -> str:
    """Return services A–E with fixed durations from the database."""
    return _run(runtime.context, engine_list_services)


@tool
def list_professionals(runtime: ToolRuntime[ChatContext]) -> str:
    """Return bookable professionals from the database."""
    return _run(runtime.context, engine_list_professionals)


@tool
def explain_clinic_day(day: str) -> str:
    """Check whether a calendar day (YYYY-MM-DD) is open for bookings."""
    try:
        day_date = _parse_day(day)
        valid, reason = hours_explain_clinic_day(day_date)
    except ValueError as exc:
        return _err(str(exc))
    return _ok(valid=valid, day=day_date.isoformat(), reason=reason)


@tool
def explain_clinic_start(starts_at: str) -> str:
    """Check whether a proposed start (ISO datetime) is within clinic hours."""
    try:
        start = _parse_starts_at(starts_at)
        valid, reason = hours_explain_clinic_start(start)
    except ValueError as exc:
        return _err(str(exc))
    return _ok(valid=valid, starts_at=start.isoformat(), reason=reason)


@tool
def list_available_starts(
    runtime: ToolRuntime[ChatContext],
    professional_slug: str,
    service_code: str,
    day: str,
    ignore_booking_id: int | None = None,
) -> str:
    """Return free start times for one day (YYYY-MM-DD). Pick starts_at from the result."""
    ctx = runtime.context
    catalog_err = reject_unknown_catalog(
        ctx.db,
        professional_slug=professional_slug,
        service_code=service_code,
    )
    if catalog_err:
        return _err(catalog_err)
    try:
        day_date = _parse_day(day)
        starts = engine_list_available_starts(
            ctx.db,
            professional_slug.strip(),
            service_code.strip().upper(),
            day_date,
            ignore_booking_id=ignore_booking_id,
        )
    except (BookingError, ValueError) as exc:
        return _err(str(exc))
    return _ok(**_day_slots(day_date, starts))


@tool
def list_next_available_starts(
    runtime: ToolRuntime[ChatContext],
    professional_slug: str,
    service_code: str,
    from_day: str,
    ignore_booking_id: int | None = None,
) -> str:
    """Scan forward from from_day (YYYY-MM-DD) and return the next days with free starts."""
    ctx = runtime.context
    catalog_err = reject_unknown_catalog(
        ctx.db,
        professional_slug=professional_slug,
        service_code=service_code,
    )
    if catalog_err:
        return _err(catalog_err)
    try:
        rows = engine_list_next_available_starts(
            ctx.db,
            professional_slug.strip(),
            service_code.strip().upper(),
            _parse_day(from_day),
            ignore_booking_id=ignore_booking_id,
        )
    except (BookingError, ValueError) as exc:
        return _err(str(exc))
    return _ok(days=[_day_slots(row.day, list(row.starts)) for row in rows])


@tool
def list_patient_appointments(runtime: ToolRuntime[ChatContext]) -> str:
    """Return this patient's active bookings (uses session email)."""
    ctx = runtime.context
    if not ctx.patient_email:
        return _err(PATIENT_EMAIL_UNKNOWN)
    return _run(ctx, engine_list_patient_appointments, patient_email=ctx.patient_email)


@tool
def check_start(
    runtime: ToolRuntime[ChatContext],
    professional_slug: str,
    service_code: str,
    starts_at: str,
    ignore_booking_id: int | None = None,
) -> str:
    """Validate a proposed start (ISO datetime from list_available_starts)."""
    ctx = runtime.context
    catalog_err = reject_unknown_catalog(
        ctx.db,
        professional_slug=professional_slug,
        service_code=service_code,
    )
    if catalog_err:
        return _err(catalog_err)
    try:
        plan = engine_check_start(
            ctx.db,
            professional_slug=professional_slug.strip(),
            service_code=service_code.strip().upper(),
            starts_at=_parse_starts_at(starts_at),
            ignore_booking_id=ignore_booking_id,
        )
    except (BookingError, ValueError) as exc:
        return _err(str(exc))
    return _ok(plan=_encode_slot_plan(plan))


@tool
def book_appointment(runtime: ToolRuntime[ChatContext]) -> str:
    """Book using session identity and draft (service, professional, starts_at)."""
    ctx = runtime.context
    if not ctx.can_book_now:
        return _err(CONTACT_REQUIRED_MESSAGE)

    missing = [
        name
        for name, value in (
            ("service_code", ctx.draft.service_code),
            ("professional_slug", ctx.draft.professional_slug),
            ("starts_at", ctx.draft.starts_at),
        )
        if not value
    ]
    if missing:
        return _err(draft_missing_message(missing), draft=ctx.draft.to_session())

    catalog_err = reject_unknown_catalog(
        ctx.db,
        professional_slug=ctx.draft.professional_slug,
        service_code=ctx.draft.service_code,
    )
    if catalog_err:
        return _err(catalog_err, draft=ctx.draft.to_session())

    try:
        booking = engine_book(
            ctx.db,
            professional_slug=ctx.draft.professional_slug or "",
            service_code=ctx.draft.service_code or "",
            starts_at=_parse_starts_at(ctx.draft.starts_at or ""),
            patient_name=ctx.patient_name or "",
            patient_email=ctx.patient_email or "",
        )
    except (BookingError, AuthError, ValueError) as exc:
        return _err(str(exc))

    ctx.clear_draft()
    return _ok(**booking_created_dict(booking))


@tool
def cancel_appointment(booking_id: int, runtime: ToolRuntime[ChatContext]) -> str:
    """Cancel an active booking owned by the logged-in patient."""
    ctx = runtime.context
    user = get_user_by_id(ctx.db, ctx.user_id) if ctx.user_id is not None else None
    try:
        booking = cancel_for_user(ctx.db, booking_id, user)
    except (AuthError, BookingError) as exc:
        return _err(str(exc))
    return _ok(**booking_cancelled_dict(booking))


@tool
def reschedule_appointment(
    booking_id: int,
    starts_at: str,
    runtime: ToolRuntime[ChatContext],
) -> str:
    """Reschedule an owned booking to a new start (ISO datetime from availability tools)."""
    ctx = runtime.context
    user = get_user_by_id(ctx.db, ctx.user_id) if ctx.user_id is not None else None
    try:
        booking = reschedule_for_user(
            ctx.db, booking_id, _parse_starts_at(starts_at), user
        )
    except (AuthError, BookingError, ValueError) as exc:
        return _err(str(exc))
    return _ok(**booking_rescheduled_dict(booking))


def _ok(**fields: object) -> str:
    return json.dumps({"ok": True, **fields})


def _err(error: str, **fields: object) -> str:
    return json.dumps({"ok": False, "error": error, **fields})


def _run(ctx: ChatContext, fn: Callable[..., Any], /, **kwargs: Any) -> str:
    """Call a booking-engine function and return JSON for the agent."""
    try:
        return _ok(result=_encode(fn(ctx.db, **kwargs)))
    except (BookingError, AuthError, ValueError) as exc:
        return _err(str(exc))


def _encode(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        if isinstance(value, PatientAppointment):
            return _encode_patient_appointment(value)
        if isinstance(value, SlotPlan):
            return _encode_slot_plan(value)
        if isinstance(value, DayAvailability):
            return _day_slots(value.day, list(value.starts))
        return {key: _encode(item) for key, item in asdict(value).items()}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    return value


def _encode_patient_appointment(row: PatientAppointment) -> dict[str, object]:
    return {
        "booking_id": row.booking_id,
        "professional_slug": row.professional_slug,
        "service_code": row.service_code,
        "starts_at": row.starts_at.isoformat(),
        "ends_at": row.ends_at.isoformat(),
        "status": row.status,
    }


def _encode_slot_plan(plan: SlotPlan) -> dict[str, object]:
    return {
        "professional_slug": plan.professional_slug,
        "service_code": plan.service_code,
        "starts_at": plan.starts_at.isoformat(),
        "ends_at": plan.ends_at.isoformat(),
        "status": plan.status,
    }


def _day_slots(day: date, starts: list[datetime]) -> dict[str, object]:
    """Compact slot list: clock times plus full ISO values for update_context."""
    return {
        "day": day.isoformat(),
        "times": [_clinic_time(start) for start in starts],
        "starts_at": [start.isoformat() for start in starts],
    }


def _clinic_time(dt: datetime) -> str:
    return to_clinic(dt).strftime("%H:%M")


def _parse_day(raw: str) -> date:
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("day is required (YYYY-MM-DD)")
    return date.fromisoformat(cleaned)


def _parse_starts_at(raw: str) -> datetime:
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("starts_at is required")
    return datetime.fromisoformat(cleaned)


CONTACT_GATED_TOOLS: list[BaseTool] = [book_appointment]
AUTH_GATED_TOOLS: list[BaseTool] = [cancel_appointment, reschedule_appointment]

BOOKING_TOOLS: list[BaseTool] = [
    get_session_info,
    update_context,
    list_services,
    list_professionals,
    explain_clinic_day,
    explain_clinic_start,
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
    "AUTH_GATED_TOOLS",
    "BOOKING_TOOLS",
    "CONTACT_GATED_TOOLS",
    "ChatContext",
    "TOOL_ALLOWLIST",
    "book_appointment",
    "cancel_appointment",
    "check_start",
    "explain_clinic_day",
    "explain_clinic_start",
    "get_session_info",
    "list_available_starts",
    "list_next_available_starts",
    "list_patient_appointments",
    "list_professionals",
    "list_services",
    "reschedule_appointment",
    "update_context",
]
