"""Notify hooks: fake CalendarPort / EmailPort today; real adapters in stage 6."""

from __future__ import annotations

from clinic_ai_booking.fakes import FakeCalendar, FakeEmail
from clinic_ai_booking.models import Booking
from clinic_ai_booking.ports import CalendarPort, EmailPort

_calendar: CalendarPort = FakeCalendar()
_email: EmailPort = FakeEmail()


def get_calendar_port() -> CalendarPort:
    """Return the process calendar adapter."""
    return _calendar


def get_email_port() -> EmailPort:
    """Return the process email adapter."""
    return _email


def set_ports(calendar: CalendarPort, email: EmailPort) -> None:
    """Replace process adapters (tests or stage-6 wiring)."""
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
    """Sync calendar + email after a new booking is saved."""
    _calendar.upsert_booking(booking)
    _email.send_booking_created(booking)


def notify_booking_cancelled(booking: Booking) -> None:
    """Remove calendar event + email after cancel."""
    _calendar.remove_booking(booking)
    _email.send_booking_cancelled(booking)


def notify_booking_rescheduled(booking: Booking) -> None:
    """Update calendar + email after reschedule (replacement booking)."""
    _calendar.upsert_booking(booking)
    _email.send_booking_rescheduled(booking)
