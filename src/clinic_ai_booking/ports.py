"""Swap points for calendar and email adapters (fakes or real Google/Outlook)."""

from typing import Protocol

from clinic_ai_booking.models import Booking


class CalendarPort(Protocol):
    """Clinic calendar sync for booking create/update/cancel."""

    def upsert_booking(self, booking: Booking) -> None:
        """Create or update the calendar event for this booking."""

    def remove_booking(self, booking: Booking) -> None:
        """Remove the calendar event for a cancelled booking."""


class EmailPort(Protocol):
    """Patient notification email for booking lifecycle events."""

    def send_booking_created(self, booking: Booking) -> None:
        """Email the patient that a booking was created."""

    def send_booking_cancelled(self, booking: Booking) -> None:
        """Email the patient that a booking was cancelled."""

    def send_booking_rescheduled(self, booking: Booking) -> None:
        """Email the patient that a booking was rescheduled."""
