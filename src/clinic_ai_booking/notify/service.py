"""Notify hooks: CalendarPort / EmailPort (fakes by default; real via adapters)."""

from __future__ import annotations

from clinic_ai_booking.notify.fakes import FakeCalendar, FakeEmail
from clinic_ai_booking.domain.models import STATUS_CONFIRMED, Booking
from clinic_ai_booking.notify.ports import CalendarPort, EmailPort

_calendar: CalendarPort = FakeCalendar()
_email: EmailPort = FakeEmail()


def get_calendar_port() -> CalendarPort:
    """Return the process calendar adapter."""
    return _calendar


def get_email_port() -> EmailPort:
    """Return the process email adapter."""
    return _email


def set_ports(calendar: CalendarPort, email: EmailPort) -> None:
    """Replace process adapters (tests or real wiring)."""
    global _calendar, _email
    _calendar = calendar
    _email = email


def reset_ports_to_fakes() -> tuple[FakeCalendar, FakeEmail]:
    """Install fresh fakes and return them (tests)."""
    calendar = FakeCalendar()
    email = FakeEmail()
    set_ports(calendar, email)
    return calendar, email


def notify_booking_created(booking: Booking) -> None:
    """Email always; calendar upsert only when status is confirmed."""
    if booking.status == STATUS_CONFIRMED:
        _calendar.upsert_booking(booking)
    _email.send_booking_created(booking)


def notify_booking_cancelled(booking: Booking) -> None:
    """Remove calendar event + email after cancel."""
    _calendar.remove_booking(booking)
    _email.send_booking_cancelled(booking)


def notify_calendar_remove_only(booking: Booking) -> None:
    """Remove calendar event without email (reschedule drops the original)."""
    _calendar.remove_booking(booking)


def notify_booking_rescheduled(booking: Booking) -> None:
    """Upsert calendar when confirmed + email for the replacement booking."""
    if booking.status == STATUS_CONFIRMED:
        _calendar.upsert_booking(booking)
    _email.send_booking_rescheduled(booking)
