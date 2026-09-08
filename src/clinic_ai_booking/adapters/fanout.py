"""Fan-out wrappers so one clinic booking can dual-write Google + Outlook."""

from __future__ import annotations

from dataclasses import dataclass

from clinic_ai_booking.models import Booking
from clinic_ai_booking.ports import CalendarPort, EmailPort


@dataclass
class FanoutCalendar:
    """CalendarPort that upserts/removes on every child adapter."""

    children: list[CalendarPort]

    def upsert_booking(self, booking: Booking) -> None:
        """Create or update the event on each configured calendar."""
        errors: list[str] = []
        for child in self.children:
            try:
                child.upsert_booking(booking)
            except Exception as exc:  # noqa: BLE001 — collect then raise
                errors.append(f"{type(child).__name__}: {exc}")
        if errors:
            raise RuntimeError("calendar fan-out failed: " + "; ".join(errors))

    def remove_booking(self, booking: Booking) -> None:
        """Remove the event on each configured calendar."""
        errors: list[str] = []
        for child in self.children:
            try:
                child.remove_booking(booking)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(child).__name__}: {exc}")
        if errors:
            raise RuntimeError("calendar fan-out remove failed: " + "; ".join(errors))


@dataclass
class FanoutEmail:
    """EmailPort that sends via every child (usually one provider)."""

    children: list[EmailPort]

    def send_booking_created(self, booking: Booking) -> None:
        """Notify the patient via each configured mail adapter."""
        self._all("send_booking_created", booking)

    def send_booking_cancelled(self, booking: Booking) -> None:
        """Notify cancel via each configured mail adapter."""
        self._all("send_booking_cancelled", booking)

    def send_booking_rescheduled(self, booking: Booking) -> None:
        """Notify reschedule via each configured mail adapter."""
        self._all("send_booking_rescheduled", booking)

    def _all(self, method: str, booking: Booking) -> None:
        errors: list[str] = []
        for child in self.children:
            try:
                getattr(child, method)(booking)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(child).__name__}: {exc}")
        if errors:
            raise RuntimeError("email fan-out failed: " + "; ".join(errors))
