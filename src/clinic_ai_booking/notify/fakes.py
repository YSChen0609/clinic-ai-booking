"""In-memory calendar/email adapters that log and record calls for tests."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from clinic_ai_booking.domain.models import Booking

logger = logging.getLogger(__name__)


@dataclass
class FakeCalendar:
    """CalendarPort that records upsert/remove instead of calling a vendor API."""

    calls: list[tuple[str, int | None, str]] = field(default_factory=list)

    def upsert_booking(self, booking: Booking) -> None:
        """Record an upsert and log booking id + status."""
        self.calls.append(("upsert", booking.id, booking.status))
        logger.info(
            "fake_calendar upsert booking_id=%s status=%s starts_at=%s",
            booking.id,
            booking.status,
            booking.starts_at.isoformat(),
        )

    def remove_booking(self, booking: Booking) -> None:
        """Record a remove and log booking id."""
        self.calls.append(("remove", booking.id, booking.status))
        logger.info("fake_calendar remove booking_id=%s", booking.id)

    def clear(self) -> None:
        """Drop recorded calls (tests)."""
        self.calls.clear()


@dataclass
class FakeEmail:
    """EmailPort that records send actions instead of delivering mail."""

    calls: list[tuple[str, int | None, str]] = field(default_factory=list)

    def send_booking_created(self, booking: Booking) -> None:
        """Record a created notification."""
        self.calls.append(("created", booking.id, booking.patient_email))
        logger.info(
            "fake_email created booking_id=%s to=%s status=%s",
            booking.id,
            booking.patient_email,
            booking.status,
        )

    def send_booking_cancelled(self, booking: Booking) -> None:
        """Record a cancelled notification."""
        self.calls.append(("cancelled", booking.id, booking.patient_email))
        logger.info(
            "fake_email cancelled booking_id=%s to=%s",
            booking.id,
            booking.patient_email,
        )

    def send_booking_rescheduled(self, booking: Booking) -> None:
        """Record a rescheduled notification."""
        self.calls.append(("rescheduled", booking.id, booking.patient_email))
        logger.info(
            "fake_email rescheduled booking_id=%s to=%s status=%s",
            booking.id,
            booking.patient_email,
            booking.status,
        )

    def clear(self) -> None:
        """Drop recorded calls (tests)."""
        self.calls.clear()
