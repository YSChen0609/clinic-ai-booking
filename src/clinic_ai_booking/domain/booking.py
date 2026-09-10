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

Patient caps (see docs/booking_rules.md): at most two upcoming bookings
(ends_at still in the future), different services, different times; same
service → reschedule, do not rebook. Finished appointments do not count.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinic_ai_booking.auth import get_or_create_user
from clinic_ai_booking.domain.hours import (
    CLOSE,
    OVERTIME_END,
    SLOT_MINUTES,
    TIMEZONE,
    candidate_starts,
    day_range,
    ends_after,
    format_patient_clock,
    format_patient_day,
    format_patient_span,
    interval_overlaps_break,
    on_start_grid,
    overlaps,
    same_calendar_day,
    start_during_open_hours,
    to_clinic,
)
from clinic_ai_booking.domain.models import (
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
    notify_calendar_remove_only,
)

NotifyFn = Callable[[Booking], None]

# At most two not-yet-finished bookings per patient (ends_at still in the future).
MAX_ACTIVE_BOOKINGS_PER_PATIENT = 2


def _clinic_now() -> datetime:
    """Return clinic 'now' for caps and availability (tests may monkeypatch)."""
    return datetime.now(TIMEZONE)


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
class BandAvailability:
    """Duration-aware free starts for one part of day (Plan B offer shape)."""

    start_windows: tuple[tuple[str, str], ...]
    sample_starts: tuple[str, ...]


@dataclass(frozen=True)
class DayAvailabilitySummary:
    """Compact day offer for chat: weekday + bands of start windows/samples."""

    day: date
    weekday: str
    professional_slug: str
    service_code: str
    duration_minutes: int
    grid_minutes: int
    bands: dict[str, BandAvailability]


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


def explain_can_perform(
    session: Session, professional_slug: str, service_code: str
) -> tuple[bool, str]:
    """Return (ok, patient-facing reason) for whether this pro may do the service."""
    professional = _load_professional(session, professional_slug)
    service = _load_service(session, service_code)
    if _can_perform(professional, service):
        level = "senior" if professional.is_senior else "junior"
        return (
            True,
            f"{professional.name} ({level}) can perform service {service.code}.",
        )
    level = "senior professional" if professional.is_senior else "junior professional"
    return (
        False,
        f"{professional.name} is a {level} and cannot perform service "
        f"{service.code} (seniors only).",
    )


def seniors_for_service(session: Session, service_code: str) -> list[ProfessionalInfo]:
    """Return seniors who may perform that service (empty if none)."""
    service = _load_service(session, service_code)
    return [
        row
        for row in list_professionals(session)
        if row.is_senior and _can_perform(_load_professional(session, row.slug), service)
    ]


def snap_to_nearest_start(
    session: Session,
    professional_slug: str,
    service_code: str,
    day: date,
    requested: datetime,
    *,
    ignore_booking_id: int | None = None,
) -> datetime | None:
    """Return the free 15-minute start on that day closest to requested, or None."""
    free = list_available_starts(
        session,
        professional_slug,
        service_code,
        day,
        ignore_booking_id=ignore_booking_id,
    )
    if not free:
        return None
    req = to_clinic(requested)
    return min(free, key=lambda s: abs((to_clinic(s) - req).total_seconds()))


def get_service_duration(session: Session, service_code: str) -> int:
    """Return fixed duration minutes for a service code."""
    return _load_service(session, service_code).duration_minutes


def list_available_starts(
    session: Session,
    professional_slug: str,
    service_code: str,
    day: date,
    *,
    ignore_booking_id: int | None = None,
) -> list[datetime]:
    """Return blank start times for that pro, service, and day.

    Starts already in the past (clinic now) are omitted. Use this in chat
    before book_appointment (new book or reschedule). Pass ignore_booking_id
    while rescheduling so the current appointment does not block other starts
    that only conflict with that row.
    """
    professional = _load_professional(session, professional_slug)
    service = _load_service(session, service_code)
    if not _can_perform(professional, service):
        return []
    busy = _active_intervals(
        session, professional.id, day, ignore_booking_id=ignore_booking_id
    )
    now = _clinic_now()
    starts: list[datetime] = []
    for start in candidate_starts(day):
        if to_clinic(start) <= now:
            continue
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


def summarize_day_availability(
    session: Session,
    professional_slug: str,
    service_code: str,
    day: date,
    *,
    time_band: str | None = None,
    sample_per_band: int = 4,
    ignore_booking_id: int | None = None,
) -> DayAvailabilitySummary:
    """Return Plan B availability: weekday + per-band start windows and samples.

    Windows are inclusive HH:MM ranges of legal 15-minute starts for this
    service duration (not busy exceptions). Empty band means nothing to offer.
    """
    if sample_per_band < 1:
        raise BookingError("sample_per_band must be at least 1")
    service = _load_service(session, service_code)
    _load_professional(session, professional_slug)
    starts = list_available_starts(
        session,
        professional_slug,
        service_code,
        day,
        ignore_booking_id=ignore_booking_id,
    )
    bands = _band_availability_map(starts, sample_per_band=sample_per_band)
    if time_band in {"morning", "afternoon", "evening"}:
        bands = {
            name: (
                bands[name]
                if name == time_band
                else BandAvailability(start_windows=(), sample_starts=())
            )
            for name in ("morning", "afternoon", "evening")
        }
    return DayAvailabilitySummary(
        day=day,
        weekday=day.strftime("%A"),
        professional_slug=professional_slug,
        service_code=service_code,
        duration_minutes=service.duration_minutes,
        grid_minutes=SLOT_MINUTES,
        bands=bands,
    )


def day_availability_dict(summary: DayAvailabilitySummary) -> dict[str, object]:
    """JSON-friendly Plan B shape for chat facts / replies."""
    return {
        "day": summary.day.isoformat(),
        "weekday": summary.weekday,
        "professional_slug": summary.professional_slug,
        "service_code": summary.service_code,
        "duration_minutes": summary.duration_minutes,
        "grid_minutes": summary.grid_minutes,
        "bands": {
            name: {
                "start_windows": [list(w) for w in band.start_windows],
                "sample_starts": list(band.sample_starts),
            }
            for name, band in summary.bands.items()
        },
    }


def list_busy_blocks(
    session: Session, professional_slug: str, day: date
) -> list[BusyBlock]:
    """Return busy blocks for that professional and day (no patient fields)."""
    return list_busy_blocks_range(session, professional_slug, day, day)


def list_busy_blocks_range(
    session: Session,
    professional_slug: str,
    start_day: date,
    end_day: date,
) -> list[BusyBlock]:
    """Return busy blocks for that professional from start_day through end_day inclusive."""
    if end_day < start_day:
        raise BookingError("end day must be on or after start day")
    professional = _load_professional(session, professional_slug)
    range_start, _ = day_range(start_day)
    _, range_end = day_range(end_day)
    stmt = (
        select(Booking.starts_at, Booking.ends_at)
        .where(
            Booking.professional_id == professional.id,
            Booking.status.in_(ACTIVE_STATUSES),
            Booking.starts_at < range_end,
            Booking.ends_at > range_start,
        )
        .order_by(Booking.starts_at)
    )
    return [
        BusyBlock(
            professional_slug=professional.slug,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
        )
        for row in session.execute(stmt).all()
    ]


def busy_block_public_dict(block: BusyBlock) -> dict[str, str]:
    """Serialize a busy block for public calendar APIs (no patient fields)."""
    return {
        "professional_slug": block.professional_slug,
        "starts_at": block.starts_at.isoformat(),
        "ends_at": block.ends_at.isoformat(),
    }


def list_patient_appointments(
    session: Session, patient_email: str
) -> list[PatientAppointment]:
    """Return this patient's upcoming (not-yet-finished) bookings for reminders."""
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
    # Drop the original calendar event without a cancel email; then notify replacement.
    cancel_appointment(session, original.id, notify=notify_calendar_remove_only)
    notify(replacement)
    return replacement


def booking_created_dict(booking: Booking) -> dict[str, object]:
    """Serialize a new booking for HTTP and chat tools."""
    return {
        "id": booking.id,
        "status": booking.status,
        "patient_email": booking.patient_email,
        "user_id": booking.user_id,
        "starts_at": booking.starts_at.isoformat(),
        "ends_at": booking.ends_at.isoformat(),
    }


def booking_cancelled_dict(booking: Booking) -> dict[str, object]:
    """Serialize a cancelled booking for HTTP and chat tools."""
    return {"id": booking.id, "status": booking.status}


def booking_rescheduled_dict(booking: Booking) -> dict[str, object]:
    """Serialize a rescheduled booking for HTTP and chat tools."""
    return {
        "id": booking.id,
        "status": booking.status,
        "starts_at": booking.starts_at.isoformat(),
        "ends_at": booking.ends_at.isoformat(),
    }


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
    """Reject rebooks / same-service doubles / a third upcoming booking."""
    existing = _active_patient_bookings(
        session, patient_email, ignore_booking_id=ignore_booking_id
    )
    for row in existing:
        if row.starts_at == starts_at:
            raise BookingError(
                f"You already have booking #{row.id} at that same time "
                f"(service {row.service.code}, "
                f"{format_patient_span(row.starts_at, row.ends_at)}). "
                "Manage it on the website, or pick a different time."
            )
        if overlaps(starts_at, ends_at, row.starts_at, row.ends_at):
            raise BookingError(
                f"That time overlaps your booking #{row.id} "
                f"(service {row.service.code}, "
                f"{format_patient_span(row.starts_at, row.ends_at)}). "
                "Pick another time, or reschedule on the website."
            )
        if row.service_id == service_id:
            raise BookingError(
                f"This email already has an upcoming service {service_code} booking "
                f"(#{row.id} on {format_patient_span(row.starts_at, row.ends_at)}). "
                "Cancel or reschedule it on the website, or use a different email. "
                "I did not create a new booking."
            )
    if len(existing) >= MAX_ACTIVE_BOOKINGS_PER_PATIENT:
        summary = ", ".join(
            f"{row.service.code} on "
            f"{format_patient_day(to_clinic(row.starts_at).date())} "
            f"at {format_patient_clock(row.starts_at)} (#{row.id})"
            for row in existing
        )
        raise BookingError(
            f"This email already has {len(existing)} upcoming bookings ({summary}). "
            "The limit is two. Cancel or reschedule one on the website, "
            "or use a different email. I did not create a new booking."
        )


def _active_patient_bookings(
    session: Session,
    patient_email: str,
    *,
    ignore_booking_id: int | None = None,
) -> list[Booking]:
    """Load this patient's not-yet-finished confirmed/pending bookings."""
    now = _clinic_now()
    stmt = (
        select(Booking)
        .where(
            Booking.patient_email == patient_email,
            Booking.status.in_(ACTIVE_STATUSES),
            Booking.ends_at > now,
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
    if to_clinic(start) <= _clinic_now():
        raise BookingError("that start is already in the past; pick a later time")
    end = start + timedelta(minutes=service.duration_minutes)
    status = _status_for(service.code, start, end)
    if any(overlaps(start, end, b_start, b_end) for b_start, b_end in busy):
        raise BookingError(
            "that time is already taken on this doctor's calendar; "
            "offer another start from list_available_starts "
            "(this is not the patient's own booking)"
        )
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
            raise BookingError(
                "that time is already taken on this doctor's calendar; "
                "offer another start from list_available_starts "
                "(this is not the patient's own booking)"
            ) from exc
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


def _band_availability_map(
    starts: list[datetime], *, sample_per_band: int
) -> dict[str, BandAvailability]:
    """Split legal starts into morning/afternoon/evening windows + samples."""
    buckets: dict[str, list[datetime]] = {
        "morning": [],
        "afternoon": [],
        "evening": [],
    }
    for start in starts:
        local = to_clinic(start)
        hour = local.hour
        if hour < 12:
            buckets["morning"].append(start)
        elif hour < 17:
            buckets["afternoon"].append(start)
        else:
            buckets["evening"].append(start)
    return {
        name: BandAvailability(
            start_windows=tuple(_compress_start_windows(group)),
            sample_starts=tuple(
                to_clinic(s).strftime("%H:%M") for s in group[:sample_per_band]
            ),
        )
        for name, group in buckets.items()
    }


def _compress_start_windows(starts: list[datetime]) -> list[tuple[str, str]]:
    """Run-length encode consecutive 15-minute starts as inclusive HH:MM windows."""
    if not starts:
        return []
    ordered = sorted(to_clinic(s) for s in starts)
    windows: list[tuple[str, str]] = []
    run_start = ordered[0]
    prev = ordered[0]
    step = timedelta(minutes=SLOT_MINUTES)
    for current in ordered[1:]:
        if current - prev == step:
            prev = current
            continue
        windows.append((run_start.strftime("%H:%M"), prev.strftime("%H:%M")))
        run_start = current
        prev = current
    windows.append((run_start.strftime("%H:%M"), prev.strftime("%H:%M")))
    return windows


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
