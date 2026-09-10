"""Notify: CalendarPort / EmailPort hooks (fakes by default; real adapters under `.adapters`)."""

from clinic_ai_booking.notify.fakes import FakeCalendar, FakeEmail
from clinic_ai_booking.notify.ports import CalendarPort, EmailPort
from clinic_ai_booking.notify.service import (
    get_calendar_port,
    get_email_port,
    notify_booking_cancelled,
    notify_booking_created,
    notify_booking_rescheduled,
    notify_calendar_remove_only,
    reset_ports_to_fakes,
    set_ports,
)

__all__ = [
    "CalendarPort",
    "EmailPort",
    "FakeCalendar",
    "FakeEmail",
    "get_calendar_port",
    "get_email_port",
    "notify_booking_cancelled",
    "notify_booking_created",
    "notify_booking_rescheduled",
    "notify_calendar_remove_only",
    "reset_ports_to_fakes",
    "set_ports",
]
