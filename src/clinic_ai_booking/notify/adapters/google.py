"""Google Calendar + Gmail adapters (clinic account; patient as attendee)."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from clinic_ai_booking.notify.adapters.copy import (
    email_body_cancelled,
    email_body_created,
    email_body_rescheduled,
    email_subject_cancelled,
    email_subject_created,
    email_subject_rescheduled,
    event_description,
    event_summary,
    google_event_id,
)
from clinic_ai_booking.notify.adapters.tokens import GoogleTokenSource
from clinic_ai_booking.domain.hours import TIMEZONE_NAME, to_clinic
from clinic_ai_booking.domain.models import STATUS_CONFIRMED, Booking

logger = logging.getLogger(__name__)

CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class GoogleAdapterError(Exception):
    """Google Calendar or Gmail HTTP call failed."""


@dataclass
class GoogleCalendar:
    """CalendarPort backed by one shared clinic Google Calendar."""

    tokens: GoogleTokenSource
    calendar_id: str
    http: httpx.Client

    def upsert_booking(self, booking: Booking) -> None:
        """Create or replace the clinic event; invite patient as attendee."""
        if booking.status != STATUS_CONFIRMED:
            logger.info(
                "google_calendar skip upsert booking_id=%s status=%s",
                booking.id,
                booking.status,
            )
            return
        if booking.id is None:
            raise GoogleAdapterError("booking id is required for calendar upsert")
        event_id = google_event_id(booking.id)
        body = _event_body(booking)
        path = (
            f"{CALENDAR_BASE}/calendars/{quote(self.calendar_id, safe='')}"
            f"/events/{event_id}"
        )
        response = self.http.put(
            path,
            params={"sendUpdates": "all"},
            headers=self._auth_headers(),
            json=body,
        )
        if response.status_code == 404:
            response = self.http.post(
                f"{CALENDAR_BASE}/calendars/{quote(self.calendar_id, safe='')}/events",
                params={"sendUpdates": "all"},
                headers=self._auth_headers(),
                json={**body, "id": event_id},
            )
        if response.status_code >= 400:
            logger.error(
                "google_calendar upsert failed status=%s body=%s",
                response.status_code,
                response.text[:400],
            )
            raise GoogleAdapterError("Google Calendar upsert failed")
        logger.info("google_calendar upsert booking_id=%s event_id=%s", booking.id, event_id)

    def remove_booking(self, booking: Booking) -> None:
        """Delete the clinic event if present; ignore missing events."""
        if booking.id is None:
            return
        event_id = google_event_id(booking.id)
        path = (
            f"{CALENDAR_BASE}/calendars/{quote(self.calendar_id, safe='')}"
            f"/events/{event_id}"
        )
        response = self.http.delete(
            path,
            params={"sendUpdates": "all"},
            headers=self._auth_headers(),
        )
        if response.status_code in (204, 404, 410):
            logger.info(
                "google_calendar remove booking_id=%s status=%s",
                booking.id,
                response.status_code,
            )
            return
        if response.status_code >= 400:
            logger.error(
                "google_calendar remove failed status=%s body=%s",
                response.status_code,
                response.text[:400],
            )
            raise GoogleAdapterError("Google Calendar remove failed")

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens.access_token()}"}


@dataclass
class GoogleEmail:
    """EmailPort via Gmail API send (clinic mailbox; no patient OAuth)."""

    tokens: GoogleTokenSource
    sender: str
    http: httpx.Client

    def send_booking_created(self, booking: Booking) -> None:
        """Email the patient that a booking was created or is pending."""
        self._send(
            booking.patient_email,
            email_subject_created(booking),
            email_body_created(booking),
        )

    def send_booking_cancelled(self, booking: Booking) -> None:
        """Email the patient that a booking was cancelled."""
        self._send(
            booking.patient_email,
            email_subject_cancelled(booking),
            email_body_cancelled(booking),
        )

    def send_booking_rescheduled(self, booking: Booking) -> None:
        """Email the patient that a booking was rescheduled."""
        self._send(
            booking.patient_email,
            email_subject_rescheduled(booking),
            email_body_rescheduled(booking),
        )

    def _send(self, to_addr: str, subject: str, body: str) -> None:
        raw = _rfc822(self.sender, to_addr, subject, body)
        # Gmail expects URL-safe base64 of the raw message.
        encoded = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
        response = self.http.post(
            GMAIL_SEND,
            headers={"Authorization": f"Bearer {self.tokens.access_token()}"},
            json={"raw": encoded},
        )
        if response.status_code >= 400:
            logger.error(
                "gmail send failed status=%s body=%s",
                response.status_code,
                response.text[:400],
            )
            raise GoogleAdapterError("Gmail send failed")
        logger.info("gmail send to=%s subject=%s", to_addr, subject)


def _event_body(booking: Booking) -> dict[str, Any]:
    start = to_clinic(booking.starts_at)
    end = to_clinic(booking.ends_at)
    return {
        "summary": event_summary(booking),
        "description": event_description(booking),
        "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE_NAME},
        "end": {"dateTime": end.isoformat(), "timeZone": TIMEZONE_NAME},
        "attendees": [
            {
                "email": booking.patient_email,
                "displayName": booking.patient_name,
            }
        ],
        "extendedProperties": {
            "private": {
                "clinicBookingId": str(booking.id),
                "professionalId": str(booking.professional_id),
            }
        },
    }


def _rfc822(from_addr: str, to_addr: str, subject: str, body: str) -> str:
    lines = []
    if from_addr and from_addr != "me":
        lines.append(f"From: {from_addr}")
    lines.append(f"To: {to_addr}")
    lines.append(f"Subject: {subject}")
    lines.append("Content-Type: text/plain; charset=utf-8")
    lines.append("")
    lines.append(body)
    return "\r\n".join(lines)
