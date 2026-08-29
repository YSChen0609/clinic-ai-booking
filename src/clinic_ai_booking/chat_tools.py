"""Clinic booking tools for create_agent (allowlist only)."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from langchain.tools import ToolRuntime, tool

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
    list_available_starts as engine_list_starts,
    list_next_available_starts as engine_list_next,
    list_patient_appointments as engine_list_patient,
    list_professionals as engine_list_pros,
    list_services as engine_list_services,
)
from clinic_ai_booking.chat_context import ChatContext
from clinic_ai_booking.hours import explain_clinic_day, explain_clinic_start
from clinic_ai_booking.models import STATUS_PENDING_DOCTOR, Booking, User
from clinic_ai_booking.notify import notify_booking_created


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


@tool
def list_services(runtime: ToolRuntime[ChatContext]) -> str:
    """List clinic services A–E with fixed durations (do not invent length)."""
    rows = engine_list_services(runtime.context.db)
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
def list_professionals(runtime: ToolRuntime[ChatContext]) -> str:
    """List bookable professionals (slug, name, senior flag)."""
    rows = engine_list_pros(runtime.context.db)
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
def check_clinic_time(when: str, runtime: ToolRuntime[ChatContext]) -> str:
    """Validate a day (YYYY-MM-DD) or start (ISO datetime) against clinic hours.

    Call this before listing slots. Weekends, off-grid times, outside 09:00–20:00,
    and break starts are invalid — stop and explain; do not invent availability.
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
    runtime: ToolRuntime[ChatContext],
) -> str:
    """Save visitor name+email for this browser session. Call once; do not re-ask."""
    ctx = runtime.context
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


@tool
def list_available_starts(
    professional_slug: str,
    service_code: str,
    day: str,
    runtime: ToolRuntime[ChatContext],
    ignore_booking_id: int | None = None,
) -> str:
    """List free start times for one professional, service, and day (YYYY-MM-DD)."""
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
                    "hint": "Call check_clinic_time / list_next_available_starts for a weekday.",
                    "starts": [],
                }
            )
        starts = engine_list_starts(
            runtime.context.db,
            professional_slug,
            service_code,
            parsed_day,
            ignore_booking_id=ignore_booking_id,
        )
    except BookingError as exc:
        return _err(exc)
    payload: dict[str, Any] = {
        "ok": True,
        "valid_day": True,
        "professional_slug": professional_slug,
        "service_code": service_code.upper(),
        "day": day,
        "starts": [s.isoformat() for s in starts],
    }
    if not starts:
        payload["hint"] = (
            "No free starts that day. Call list_next_available_starts from this day."
        )
    return _ok(payload)


@tool
def list_next_available_starts(
    professional_slug: str,
    service_code: str,
    from_day: str,
    runtime: ToolRuntime[ChatContext],
    ignore_booking_id: int | None = None,
) -> str:
    """Scan forward from from_day and return the next days with free starts."""
    try:
        found = engine_list_next(
            runtime.context.db,
            professional_slug,
            service_code,
            _parse_day(from_day),
            ignore_booking_id=ignore_booking_id,
        )
    except BookingError as exc:
        return _err(exc)
    return _ok(
        {
            "ok": True,
            "days": [
                {
                    "day": row.day.isoformat(),
                    "starts": [s.isoformat() for s in row.starts],
                }
                for row in found
            ],
        }
    )


@tool
def list_patient_appointments(
    runtime: ToolRuntime[ChatContext],
    patient_email: str = "",
) -> str:
    """List this patient's active bookings. Uses session/sticky email when set."""
    ctx = runtime.context
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
    runtime: ToolRuntime[ChatContext],
    ignore_booking_id: int | None = None,
) -> str:
    """Validate a proposed start without writing. Duration comes from the service."""
    try:
        plan = engine_check_start(
            runtime.context.db,
            professional_slug=professional_slug,
            service_code=service_code,
            starts_at=_parse_start(starts_at),
            ignore_booking_id=ignore_booking_id,
        )
    except BookingError as exc:
        return _err(exc)
    return _ok(
        {
            "ok": True,
            "professional_slug": plan.professional_slug,
            "service_code": plan.service_code,
            "starts_at": plan.starts_at.isoformat(),
            "ends_at": plan.ends_at.isoformat(),
            "status": plan.status,
        }
    )


@tool
def book_appointment(
    professional_slug: str,
    service_code: str,
    starts_at: str,
    runtime: ToolRuntime[ChatContext],
    patient_name: str = "",
    patient_email: str = "",
) -> str:
    """Book after the patient confirms. Duration is fixed by service A–E."""
    ctx = runtime.context
    name, email = _resolve_book_contact(ctx, patient_name, patient_email)
    try:
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
    return _ok(_booking_payload(booking))


@tool
def cancel_appointment(booking_id: int, runtime: ToolRuntime[ChatContext]) -> str:
    """Cancel an active booking. Requires a logged-in patient who owns it."""
    ctx = runtime.context
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
    runtime: ToolRuntime[ChatContext],
) -> str:
    """Reschedule to a new start. Requires a logged-in patient who owns it."""
    ctx = runtime.context
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
    list_services,
    list_professionals,
    check_clinic_time,
    remember_visitor,
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
    "list_services",
    "remember_visitor",
    "reschedule_appointment",
]
