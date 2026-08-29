"""Booking engine: clinic rules, appointments, and slot queries for tools/UI.

Both new booking and reschedule are multi-step for the chat agent.

New booking (back-and-forth):
1. list_patient_appointments(email) — remind if they already booked
2. list_available_starts(pro, service, day) — show blank starts for one day
3. list_next_available_starts(...) — if that day is empty, propose later days
4. check_start(...) — optional, when the patient proposes a time
5. book_appointment(...) — only after they confirm (enforces patient caps)

Reschedule (back-and-forth):
1. list_available_starts / list_next_available_starts(..., ignore_booking_id=current_id)
2. check_start(..., ignore_booking_id=current_id) — optional
3. book_appointment(..., ignore_booking_id=current_id)
4. cancel_appointment(current_id)

One-shot when the new start is already agreed:
reschedule_appointment(current_id, new_start) — books first, then cancels.

Patient caps (see booking_rules.md): at most two active bookings, different
services, different times; same service → reschedule, do not rebook.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinic_ai_booking.auth import get_or_create_user
from clinic_ai_booking.hours import (
    CLOSE,
    OVERTIME_END,
    candidate_starts,
    day_range,
    ends_after,
    interval_overlaps_break,
    on_start_grid,
    overlaps,
    same_calendar_day,
    start_during_open_hours,
    to_clinic,
)
from clinic_ai_booking.models import (
    ACTIVE_STATUSES,
    STATUS_CANCELLED,
    STATUS_CONFIRMED,
    STATUS_PENDING_DOCTOR,
    Booking,
    Professional,
    Service,
)
from clinic_ai_booking.notify import (
    notify_booking_cancelled,
    notify_booking_created,
    notify_booking_rescheduled,
)

NotifyFn = Callable[[Booking], None]

# At most two active bookings per patient (different services, different times).
MAX_ACTIVE_BOOKINGS_PER_PATIENT = 2


class BookingError(ValueError):
    """Invalid booking request or a slot that cannot be taken."""


@dataclass(frozen=True)
class BusyBlock:
    """Public busy block: times only, never patient identity."""

    professional_slug: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class SlotPlan:
    """A start that passed clinic rules (not yet written to the DB)."""

    professional_id: int
    service_id: int
    professional_slug: str
    service_code: str
    starts_at: datetime
    ends_at: datetime
    status: str


@dataclass(frozen=True)
class DayAvailability:
    """Blank starts on one calendar day (for proactive multi-day suggestions)."""

    day: date
    starts: tuple[datetime, ...]


@dataclass(frozen=True)
class PatientAppointment:
    """One active booking for agent reminders (no other patients' data)."""

    booking_id: int
    professional_slug: str
    service_code: str
    starts_at: datetime
    ends_at: datetime
    status: str


@dataclass(frozen=True)
class ServiceInfo:
    """Catalog row for tools: fixed duration, no invented length."""

    code: str
    duration_minutes: int
    seniors_only: bool


@dataclass(frozen=True)
class ProfessionalInfo:
    """Catalog row for tools: who can be booked."""

    slug: str
    name: str
    is_senior: bool


def list_services(session: Session) -> list[ServiceInfo]:
    """Return services A–E with fixed durations from the database."""
    rows = session.scalars(select(Service).order_by(Service.code)).all()
    return [
        ServiceInfo(
            code=row.code,
            duration_minutes=row.duration_minutes,
            seniors_only=row.seniors_only,
        )
        for row in rows
    ]


def list_professionals(session: Session) -> list[ProfessionalInfo]:
    """Return bookable professionals from the database."""
    rows = session.scalars(select(Professional).order_by(Professional.slug)).all()
    return [
        ProfessionalInfo(slug=row.slug, name=row.name, is_senior=row.is_senior)
        for row in rows
    ]


def list_available_starts(
    session: Session,
    professional_slug: str,
    service_code: str,
    day: date,
    *,
    ignore_booking_id: int | None = None,
) -> list[datetime]:
    """Return blank start times for that pro, service, and day.

    Use this in chat before book_appointment (new book or reschedule).
    Pass ignore_booking_id while rescheduling so the current appointment does
    not block other starts that only conflict with that row.
    """
    professional = _load_professional(session, professional_slug)
    service = _load_service(session, service_code)
    if not _can_perform(professional, service):
        return []
    busy = _active_intervals(
        session, professional.id, day, ignore_booking_id=ignore_booking_id
    )
    starts: list[datetime] = []
    for start in candidate_starts(day):
        try:
            _check_slot(professional, service, start, busy=busy)
        except BookingError:
            continue
        starts.append(start)
    return starts


def list_next_available_starts(
    session: Session,
    professional_slug: str,
    service_code: str,
    from_day: date,
    *,
    max_days: int = 14,
    limit_days: int = 3,
    limit_starts_per_day: int = 5,
    ignore_booking_id: int | None = None,
) -> list[DayAvailability]:
    """Scan forward from from_day and return the next days that have free starts.

    Use when a requested day is empty (weekend, full, or no legal starts) so the
    agent can propose alternatives without waiting for the patient to ask.
    """
    if max_days < 1:
        raise BookingError("max_days must be at least 1")
    if limit_days < 1:
        raise BookingError("limit_days must be at least 1")
    if limit_starts_per_day < 1:
        raise BookingError("limit_starts_per_day must be at least 1")
    # Fail fast on unknown pro/service (same as list_available_starts).
    _load_professional(session, professional_slug)
    _load_service(session, service_code)
    found: list[DayAvailability] = []
    for offset in range(max_days):
        day = from_day + timedelta(days=offset)
        starts = list_available_starts(
            session,
            professional_slug,
            service_code,
            day,
            ignore_booking_id=ignore_booking_id,
        )
        if not starts:
            continue
        found.append(
            DayAvailability(day=day, starts=tuple(starts[:limit_starts_per_day]))
        )
        if len(found) >= limit_days:
            break
    return found


def list_busy_blocks(
    session: Session, professional_slug: str, day: date
) -> list[BusyBlock]:
    """Return busy blocks for that professional and day (no patient fields)."""
    professional = _load_professional(session, professional_slug)
    intervals = _active_intervals(session, professional.id, day)
    return [
        BusyBlock(professional_slug=professional.slug, starts_at=start, ends_at=end)
        for start, end in intervals
    ]


def list_patient_appointments(
    session: Session, patient_email: str
) -> list[PatientAppointment]:
    """Return this patient's active bookings so the agent can remind them."""
    email = _require_email(patient_email)
    rows = _active_patient_bookings(session, email)
    return [
        PatientAppointment(
            booking_id=row.id,
            professional_slug=row.professional.slug,
            service_code=row.service.code,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            status=row.status,
        )
        for row in rows
    ]


def check_start(
    session: Session,
    *,
    professional_slug: str,
    service_code: str,
    starts_at: datetime,
    ignore_booking_id: int | None = None,
) -> SlotPlan:
    """Validate a proposed start without writing. Raises BookingError if blocked."""
    professional = _load_professional(session, professional_slug)
    service = _load_service(session, service_code)
    start = _require_start(starts_at)
    end, status = _check_slot(
        professional,
        service,
        start,
        busy=_active_intervals(
            session,
            professional.id,
            to_clinic(start).date(),
            ignore_booking_id=ignore_booking_id,
        ),
    )
    return SlotPlan(
        professional_id=professional.id,
        service_id=service.id,
        professional_slug=professional.slug,
        service_code=service.code,
        starts_at=start,
        ends_at=end,
        status=status,
    )


def book_appointment(
    session: Session,
    *,
    professional_slug: str,
    service_code: str,
    starts_at: datetime,
    patient_name: str,
    patient_email: str,
    notify: NotifyFn = notify_booking_created,
    ignore_booking_id: int | None = None,
) -> Booking:
    """Insert after the start is valid. Prefer list_available_starts / check_start first."""
    name = _require_name(patient_name)
    email = _require_email(patient_email)
    service = _load_service(session, service_code)
    start = _require_start(starts_at)
    end = start + timedelta(minutes=service.duration_minutes)
    # Patient caps before pro-busy checks so "already booked" beats generic overlap.
    _enforce_patient_booking_rules(
        session,
        patient_email=email,
        service_id=service.id,
        service_code=service.code,
        starts_at=start,
        ends_at=end,
        ignore_booking_id=ignore_booking_id,
    )
    plan = check_start(
        session,
        professional_slug=professional_slug,
        service_code=service_code,
        starts_at=starts_at,
        ignore_booking_id=ignore_booking_id,
    )
    booking = _insert_appointment(
        session,
        professional_id=plan.professional_id,
        service_id=plan.service_id,
        patient_name=name,
        patient_email=email,
        starts_at=plan.starts_at,
        ends_at=plan.ends_at,
        status=plan.status,
    )
    # First successful book (or later books) links/creates the patient account.
    user = get_or_create_user(session, name, email)
    booking.user_id = user.id
    session.flush()
    notify(booking)
    return booking


def cancel_appointment(
    session: Session,
    booking_id: int,
    notify: NotifyFn = notify_booking_cancelled,
) -> Booking:
    """Mark an active appointment cancelled so its slot is free. Caller commits."""
    booking = _load_active_appointment(session, booking_id)
    booking.status = STATUS_CANCELLED
    session.flush()
    notify(booking)
    return booking


def reschedule_appointment(
    session: Session,
    booking_id: int,
    starts_at: datetime,
    notify: NotifyFn = notify_booking_rescheduled,
) -> Booking:
    """Book a valid new slot first, then cancel the original. Caller commits."""
    original = _load_active_appointment(session, booking_id)
    replacement = book_appointment(
        session,
        professional_slug=original.professional.slug,
        service_code=original.service.code,
        starts_at=starts_at,
        patient_name=original.patient_name,
        patient_email=original.patient_email,
        notify=_noop_notify,
        ignore_booking_id=original.id,
    )
    cancel_appointment(session, original.id, notify=_noop_notify)
    notify(replacement)
    return replacement


def _noop_notify(_booking: Booking) -> None:
    """Skip side-effect notify when composing book + cancel."""
    return None


def _enforce_patient_booking_rules(
    session: Session,
    *,
    patient_email: str,
    service_id: int,
    service_code: str,
    starts_at: datetime,
    ends_at: datetime,
    ignore_booking_id: int | None = None,
) -> None:
    """Reject rebooks / same-service doubles / a third active booking."""
    existing = _active_patient_bookings(
        session, patient_email, ignore_booking_id=ignore_booking_id
    )
    for row in existing:
        if row.starts_at == starts_at:
            raise BookingError(
                "you already have a booking at that time "
                f"(booking_id={row.id}, service={row.service.code}, "
                f"{row.starts_at.isoformat()}); "
                "remind the patient — do not rebook the same slot"
            )
        if overlaps(starts_at, ends_at, row.starts_at, row.ends_at):
            raise BookingError(
                "that time overlaps your existing booking "
                f"(booking_id={row.id}, service={row.service.code}, "
                f"{row.starts_at.isoformat()}–{row.ends_at.isoformat()}); "
                "choose a different time or reschedule"
            )
        if row.service_id == service_id:
            raise BookingError(
                f"you already booked service {service_code} "
                f"(booking_id={row.id}, {row.starts_at.isoformat()}); "
                "reschedule that appointment instead of booking it again"
            )
    if len(existing) >= MAX_ACTIVE_BOOKINGS_PER_PATIENT:
        summary = ", ".join(
            f"{row.service.code} at {row.starts_at.isoformat()} (id={row.id})"
            for row in existing
        )
        raise BookingError(
            "at most two different services at two different times; "
            f"you already have {len(existing)} active bookings: {summary}. "
            "Reschedule or cancel one before booking another"
        )


def _active_patient_bookings(
    session: Session,
    patient_email: str,
    *,
    ignore_booking_id: int | None = None,
) -> list[Booking]:
    """Load this patient's active bookings, optionally skipping one id."""
    stmt = (
        select(Booking)
        .where(
            Booking.patient_email == patient_email,
            Booking.status.in_(ACTIVE_STATUSES),
        )
        .order_by(Booking.starts_at)
    )
    rows = list(session.scalars(stmt).all())
    if ignore_booking_id is None:
        return rows
    return [row for row in rows if row.id != ignore_booking_id]


def _check_slot(
    professional: Professional,
    service: Service,
    start: datetime,
    *,
    busy: list[tuple[datetime, datetime]],
) -> tuple[datetime, str]:
    """Return (end, status) if start is allowed, else raise BookingError."""
    if not _can_perform(professional, service):
        raise BookingError(
            f"{professional.slug} cannot perform service {service.code}"
        )
    start = _require_start(start)
    if not start_during_open_hours(start):
        raise BookingError(
            "start must be Monday–Friday 09:00–20:00 on a 15-minute grid "
            "and not during a break"
        )
    end = start + timedelta(minutes=service.duration_minutes)
    status = _status_for(service.code, start, end)
    if any(overlaps(start, end, b_start, b_end) for b_start, b_end in busy):
        raise BookingError("that time overlaps an existing booking")
    return end, status


def _insert_appointment(
    session: Session,
    *,
    professional_id: int,
    service_id: int,
    patient_name: str,
    patient_email: str,
    starts_at: datetime,
    ends_at: datetime,
    status: str,
) -> Booking:
    """Insert a booking row; map Postgres overlap errors to BookingError."""
    booking = Booking(
        professional_id=professional_id,
        service_id=service_id,
        patient_name=patient_name,
        patient_email=patient_email,
        starts_at=starts_at,
        ends_at=ends_at,
        status=status,
    )
    try:
        with session.begin_nested():
            session.add(booking)
            session.flush()
    except IntegrityError as exc:
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if sqlstate == "23P01":
            raise BookingError("that time overlaps an existing booking") from exc
        raise BookingError("could not save booking") from exc
    return booking


def _status_for(service_code: str, start: datetime, end: datetime) -> str:
    """Choose confirmed or pending_doctor, or reject break/overtime violations."""
    if not same_calendar_day(start, end):
        raise BookingError("booking must end on the same calendar day")
    if service_code == "E":
        if ends_after(end, OVERTIME_END):
            raise BookingError("service E must end by 22:00")
        if ends_after(end, CLOSE) or interval_overlaps_break(start, end):
            return STATUS_PENDING_DOCTOR
        return STATUS_CONFIRMED
    if ends_after(end, CLOSE):
        raise BookingError("booking must end by 20:00")
    if interval_overlaps_break(start, end):
        raise BookingError("this service cannot overlap a break")
    return STATUS_CONFIRMED


def _require_start(starts_at: datetime) -> datetime:
    """Normalize to Asia/Taipei and require a 15-minute start grid."""
    try:
        local = to_clinic(starts_at)
    except ValueError as exc:
        raise BookingError(str(exc)) from exc
    if not on_start_grid(local):
        raise BookingError("start must be on a 15-minute grid")
    return local


def _require_name(name: str) -> str:
    """Return a non-empty trimmed patient name, or raise BookingError."""
    cleaned = name.strip()
    if not cleaned:
        raise BookingError("patient name is required")
    return cleaned


def _require_email(email: str) -> str:
    """Return a normalized email, or raise BookingError if it looks invalid."""
    cleaned = email.strip().lower()
    if "@" not in cleaned:
        raise BookingError("patient email is invalid")
    local, _, domain = cleaned.partition("@")
    if not local or not domain or " " in cleaned or "." not in domain:
        raise BookingError("patient email is invalid")
    return cleaned


def _load_professional(session: Session, slug: str) -> Professional:
    """Load a professional by slug, or raise BookingError if missing."""
    key = slug.strip()
    row = session.scalar(select(Professional).where(Professional.slug == key))
    if row is None:
        raise BookingError(f"unknown professional: {slug}")
    return row


def _load_service(session: Session, code: str) -> Service:
    """Load a service by code, or raise BookingError if missing."""
    key = code.strip().upper()
    row = session.scalar(select(Service).where(Service.code == key))
    if row is None:
        raise BookingError(f"unknown service: {code}")
    return row


def _load_active_appointment(session: Session, booking_id: int) -> Booking:
    """Load a confirmed or pending_doctor booking by id, or raise BookingError."""
    booking = session.get(Booking, booking_id)
    if booking is None:
        raise BookingError(f"unknown booking: {booking_id}")
    if booking.status == STATUS_CANCELLED:
        raise BookingError("booking is already cancelled")
    if booking.status not in ACTIVE_STATUSES:
        raise BookingError(f"booking cannot be changed (status={booking.status})")
    return booking


def _can_perform(professional: Professional, service: Service) -> bool:
    """True if this professional is allowed to perform the service."""
    if service.seniors_only and not professional.is_senior:
        return False
    return True


def _active_intervals(
    session: Session,
    professional_id: int,
    day: date,
    ignore_booking_id: int | None = None,
) -> list[tuple[datetime, datetime]]:
    """Return active booking intervals for that professional on that day."""
    day_start, day_end = day_range(day)
    stmt = select(Booking.id, Booking.starts_at, Booking.ends_at).where(
        Booking.professional_id == professional_id,
        Booking.status.in_(ACTIVE_STATUSES),
        Booking.starts_at < day_end,
        Booking.ends_at > day_start,
    )
    rows = session.execute(stmt).all()
    return [
        (row.starts_at, row.ends_at)
        for row in rows
        if ignore_booking_id is None or row.id != ignore_booking_id
    ]
