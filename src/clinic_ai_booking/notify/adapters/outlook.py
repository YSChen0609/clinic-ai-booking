"""Outlook / Microsoft Graph calendar + mail adapters (clinic mailbox)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from clinic_ai_booking.notify.adapters.copy import (
    booking_marker,
    email_body_cancelled,
    email_body_created,
    email_body_rescheduled,
    email_subject_cancelled,
    email_subject_created,
    email_subject_rescheduled,
    event_description,
    event_summary,
)
from clinic_ai_booking.notify.adapters.tokens import MicrosoftTokenSource
from clinic_ai_booking.domain.hours import TIMEZONE_NAME, to_clinic
from clinic_ai_booking.domain.models import STATUS_CONFIRMED, Booking

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"


class OutlookAdapterError(Exception):
    """Microsoft Graph calendar or mail call failed."""


@dataclass
class OutlookCalendar:
    """CalendarPort on one shared clinic Outlook calendar via Graph."""

    tokens: MicrosoftTokenSource
    user_upn: str
    http: httpx.Client
    calendar_id: str | None = None

    def upsert_booking(self, booking: Booking) -> None:
        """Create or patch the clinic event; patient is a required attendee."""
        if booking.status != STATUS_CONFIRMED:
            logger.info(
                "outlook_calendar skip upsert booking_id=%s status=%s",
                booking.id,
                booking.status,
            )
            return
        if booking.id is None:
            raise OutlookAdapterError("booking id is required for calendar upsert")
        body = _event_body(booking)
        existing = self._find_event_id(booking.id)
        if existing:
            response = self.http.patch(
                f"{self._events_root()}/{quote(existing, safe='')}",
                headers=self._auth_headers(),
                json=body,
            )
        else:
            response = self.http.post(
                self._events_root(),
                headers=self._auth_headers(),
                json=body,
            )
        if response.status_code >= 400:
            logger.error(
                "outlook_calendar upsert failed status=%s body=%s",
                response.status_code,
                response.text[:400],
            )
            raise OutlookAdapterError("Outlook calendar upsert failed")
        logger.info("outlook_calendar upsert booking_id=%s", booking.id)

    def remove_booking(self, booking: Booking) -> None:
        """Delete the clinic event if present; ignore missing events."""
        if booking.id is None:
            return
        existing = self._find_event_id(booking.id)
        if not existing:
            logger.info("outlook_calendar remove miss booking_id=%s", booking.id)
            return
        response = self.http.delete(
            f"{self._events_root()}/{quote(existing, safe='')}",
            headers=self._auth_headers(),
        )
        if response.status_code in (204, 404):
            logger.info(
                "outlook_calendar remove booking_id=%s status=%s",
                booking.id,
                response.status_code,
            )
            return
        if response.status_code >= 400:
            logger.error(
                "outlook_calendar remove failed status=%s body=%s",
                response.status_code,
                response.text[:400],
            )
            raise OutlookAdapterError("Outlook calendar remove failed")

    def _events_root(self) -> str:
        user = quote(self.user_upn, safe="@")
        if self.calendar_id:
            cal = quote(self.calendar_id, safe="")
            return f"{GRAPH}/users/{user}/calendars/{cal}/events"
        return f"{GRAPH}/users/{user}/events"

    def _find_event_id(self, booking_id: int) -> str | None:
        marker = booking_marker(booking_id)
        # Escape single quotes for OData string literal.
        safe = marker.replace("'", "''")
        user = quote(self.user_upn, safe="@")
        url = f"{GRAPH}/users/{user}/events"
        response = self.http.get(
            url,
            headers=self._auth_headers(),
            params={
                "$filter": f"contains(subject,'{safe}')",
                "$select": "id,subject",
                "$top": "5",
            },
        )
        if response.status_code >= 400:
            logger.error(
                "outlook_calendar find failed status=%s body=%s",
                response.status_code,
                response.text[:400],
            )
            raise OutlookAdapterError("Outlook calendar lookup failed")
        payload = response.json()
        rows = payload.get("value") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return None
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                return row["id"]
        return None

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.tokens.access_token()}",
            "Content-Type": "application/json",
        }


@dataclass
class OutlookEmail:
    """EmailPort via Graph sendMail from the clinic mailbox."""

    tokens: MicrosoftTokenSource
    user_upn: str
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
        user = quote(self.user_upn, safe="@")
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": to_addr}}],
            },
            "saveToSentItems": True,
        }
        response = self.http.post(
            f"{GRAPH}/users/{user}/sendMail",
            headers={
                "Authorization": f"Bearer {self.tokens.access_token()}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if response.status_code >= 400:
            logger.error(
                "outlook sendMail failed status=%s body=%s",
                response.status_code,
                response.text[:400],
            )
            raise OutlookAdapterError("Outlook sendMail failed")
        logger.info("outlook sendMail to=%s subject=%s", to_addr, subject)


def _event_body(booking: Booking) -> dict[str, Any]:
    start = to_clinic(booking.starts_at)
    end = to_clinic(booking.ends_at)
    # Graph expects local wall time + timeZone name (not offset in dateTime).
    fmt = "%Y-%m-%dT%H:%M:%S"
    return {
        "subject": event_summary(booking),
        "body": {"contentType": "Text", "content": event_description(booking)},
        "start": {"dateTime": start.strftime(fmt), "timeZone": TIMEZONE_NAME},
        "end": {"dateTime": end.strftime(fmt), "timeZone": TIMEZONE_NAME},
        "attendees": [
            {
                "emailAddress": {
                    "address": booking.patient_email,
                    "name": booking.patient_name,
                },
                "type": "required",
            }
        ],
        "singleValueExtendedProperties": [
            {
                "id": "String {66f5a359-4659-4830-9070-00040ec6ac6e} Name clinicBookingId",
                "value": str(booking.id),
            },
            {
                "id": "String {66f5a359-4659-4830-9070-00040ec6ac6e} Name professionalId",
                "value": str(booking.professional_id),
            },
        ],
    }
