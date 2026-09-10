"""Clinic opening hours, breaks, and the 15-minute start grid.

All scheduling uses Asia/Taipei.

Mon–Fri 09:00–20:00. Breaks 12:00–13:00 and 17:00–18:00 (half-open).
Service E may overlap those breaks and end after 20:00, but must stay on
the same calendar day and end by 22:00; those bookings are pending_doctor.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TIMEZONE_NAME = "Asia/Taipei"
TIMEZONE = ZoneInfo(TIMEZONE_NAME)

OPEN = time(9, 0)
CLOSE = time(20, 0)
OVERTIME_END = time(22, 0)
# Half-open [start, end) so a booking that ends at 12:00 does not hit lunch.
BREAKS: tuple[tuple[time, time], ...] = (
    (time(12, 0), time(13, 0)),
    (time(17, 0), time(18, 0)),
)
SLOT_MINUTES = 15
WEEKDAYS = frozenset({0, 1, 2, 3, 4})  # Monday–Friday


def to_clinic(dt: datetime) -> datetime:
    """Return dt in clinic time. Raises ValueError if naive."""
    if dt.tzinfo is None:
        raise ValueError("start time must be timezone-aware")
    return dt.astimezone(TIMEZONE)


def format_patient_day(day: date) -> str:
    """Weekday + day month year for patient-facing copy (Asia/Taipei calendar)."""
    return f"{day.strftime('%A')} {day.day} {day.strftime('%b')} {day.year}"


def format_patient_clock(dt: datetime) -> str:
    """HH:MM in clinic time."""
    return to_clinic(dt).strftime("%H:%M")


def format_patient_span(starts_at: datetime, ends_at: datetime) -> str:
    """Day plus start–end clocks for one appointment."""
    local_start = to_clinic(starts_at)
    local_end = to_clinic(ends_at)
    return (
        f"{format_patient_day(local_start.date())}, "
        f"{local_start.strftime('%H:%M')}–{local_end.strftime('%H:%M')}"
    )


def clinic_datetime(day: date, clock: time) -> datetime:
    """Build an aware datetime on that calendar day in clinic time."""
    return datetime.combine(day, clock, tzinfo=TIMEZONE)


def clinic_today(now: datetime | None = None) -> date:
    """Return today's calendar date in Asia/Taipei."""
    clock = now.astimezone(TIMEZONE) if now is not None else datetime.now(TIMEZONE)
    return clock.date()


def day_range(day: date) -> tuple[datetime, datetime]:
    """Return [day 00:00, next day 00:00) in clinic time."""
    start = clinic_datetime(day, time.min)
    return start, start + timedelta(days=1)


def is_weekday(day: date) -> bool:
    """True if the clinic is open that calendar day."""
    return day.weekday() in WEEKDAYS


def on_start_grid(dt: datetime) -> bool:
    """True if dt is a 15-minute start with zero seconds."""
    local = to_clinic(dt)
    return (
        local.second == 0
        and local.microsecond == 0
        and local.minute % SLOT_MINUTES == 0
    )


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """True if half-open [a_start, a_end) overlaps [b_start, b_end)."""
    return a_start < b_end and b_start < a_end


def break_windows(day: date) -> tuple[tuple[datetime, datetime], ...]:
    """Return that day's lunch and dinner breaks as half-open windows."""
    return tuple(
        (clinic_datetime(day, start), clinic_datetime(day, end)) for start, end in BREAKS
    )


def start_during_open_hours(start: datetime) -> bool:
    """True if start is Mon–Fri, on the grid, 09:00–20:00, and not in a break."""
    local = to_clinic(start)
    if not is_weekday(local.date()):
        return False
    if not on_start_grid(local):
        return False
    clock = local.time()
    if clock < OPEN or clock >= CLOSE:
        return False
    for b_start, b_end in break_windows(local.date()):
        if b_start <= local < b_end:
            return False
    return True


def interval_overlaps_break(start: datetime, end: datetime) -> bool:
    """True if [start, end) overlaps lunch or dinner that day."""
    local_start = to_clinic(start)
    local_end = to_clinic(end)
    return any(
        overlaps(local_start, local_end, b_start, b_end)
        for b_start, b_end in break_windows(local_start.date())
    )


def same_calendar_day(start: datetime, end: datetime) -> bool:
    """True if start and end fall on the same Asia/Taipei date."""
    return to_clinic(start).date() == to_clinic(end).date()


def ends_after(end: datetime, limit: time) -> bool:
    """True if local clock time of end is after limit (same-day assumed)."""
    return to_clinic(end).time() > limit


def candidate_starts(day: date) -> list[datetime]:
    """Return 15-minute starts from 09:00 up to but not including 20:00."""
    if not is_weekday(day):
        return []
    start = clinic_datetime(day, OPEN)
    last = clinic_datetime(day, CLOSE)
    out: list[datetime] = []
    t = start
    while t < last:
        out.append(t)
        t += timedelta(minutes=SLOT_MINUTES)
    return out


def explain_clinic_day(day: date) -> tuple[bool, str]:
    """Return (ok, reason) for whether that calendar day can have bookings."""
    weekday = day.strftime("%A")
    if not is_weekday(day):
        return (
            False,
            f"{day.isoformat()} is {weekday}; the clinic is open Monday–Friday only. "
            "Do not list slots for this day — use the next weekday instead.",
        )
    return (
        True,
        f"{day.isoformat()} is {weekday}; the clinic is open 09:00–20:00 Asia/Taipei "
        "(breaks 12:00–13:00 and 17:00–18:00).",
    )


def explain_clinic_start(start: datetime) -> tuple[bool, str]:
    """Return (ok, reason) for whether a proposed start is within clinic hours."""
    try:
        local = to_clinic(start)
    except ValueError as exc:
        return False, str(exc)
    day_ok, day_reason = explain_clinic_day(local.date())
    if not day_ok:
        return False, day_reason
    if not on_start_grid(local):
        return (
            False,
            f"{local.isoformat()} is not on the 15-minute start grid.",
        )
    clock = local.time()
    if clock < OPEN or clock >= CLOSE:
        return (
            False,
            f"{local.isoformat()} is outside open hours "
            f"({OPEN.strftime('%H:%M')}–{CLOSE.strftime('%H:%M')} Asia/Taipei).",
        )
    for b_start, b_end in break_windows(local.date()):
        if b_start <= local < b_end:
            return (
                False,
                f"{local.isoformat()} falls during a break "
                f"({b_start.strftime('%H:%M')}–{b_end.strftime('%H:%M')}).",
            )
    return True, f"{local.isoformat()} is a valid clinic start time."
