"""Shared calendar/email wording from a Booking (professional in metadata)."""

from __future__ import annotations

from clinic_ai_booking.hours import TIMEZONE_NAME, to_clinic
from clinic_ai_booking.models import STATUS_PENDING_DOCTOR, Booking


def booking_marker(booking_id: int) -> str:
    """Stable marker embedded in Outlook subjects for lookup."""
    return f"(booking:{booking_id})"


def google_event_id(booking_id: int) -> str:
    """Deterministic Google Calendar event id (base32hex-safe)."""
    return f"cab{booking_id:012d}"


def professional_label(booking: Booking) -> str:
    """Doctor display name for event metadata."""
    pro = booking.professional
    return pro.name if pro is not None else f"professional:{booking.professional_id}"


def service_code(booking: Booking) -> str:
    """Service code letter, or unknown."""
    svc = booking.service
    return svc.code if svc is not None else "?"


def event_summary(booking: Booking) -> str:
    """Calendar event title including professional and booking id."""
    return (
        f"Clinic — {professional_label(booking)} — "
        f"service {service_code(booking)} {booking_marker(booking.id or 0)}"
    )


def event_description(booking: Booking) -> str:
    """Clinic-facing event body (patient + professional + ids)."""
    start = to_clinic(booking.starts_at).strftime("%Y-%m-%d %H:%M")
    end = to_clinic(booking.ends_at).strftime("%H:%M")
    return (
        f"Professional: {professional_label(booking)}\n"
        f"Service: {service_code(booking)}\n"
        f"Patient: {booking.patient_name} <{booking.patient_email}>\n"
        f"When: {start}–{end} ({TIMEZONE_NAME})\n"
        f"Booking id: {booking.id}\n"
        f"Status: {booking.status}\n"
    )


def email_subject_created(booking: Booking) -> str:
    """Patient email subject for create / pending."""
    when = to_clinic(booking.starts_at).strftime("%Y-%m-%d %H:%M")
    if booking.status == STATUS_PENDING_DOCTOR:
        return f"Pending doctor approval — clinic booking {when}"
    return f"Booking confirmed — clinic {when}"


def email_body_created(booking: Booking) -> str:
    """Patient email body for create / pending."""
    when = to_clinic(booking.starts_at).strftime("%Y-%m-%d %H:%M %Z")
    pro = professional_label(booking)
    code = service_code(booking)
    if booking.status == STATUS_PENDING_DOCTOR:
        return (
            f"Hello {booking.patient_name},\n\n"
            f"Your request for service {code} with {pro} on {when} "
            f"is pending doctor approval. We will confirm once approved.\n\n"
            f"Booking id: {booking.id}\n"
        )
    return (
        f"Hello {booking.patient_name},\n\n"
        f"Your booking for service {code} with {pro} on {when} is confirmed.\n"
        f"You should also receive a calendar invitation from the clinic.\n\n"
        f"Booking id: {booking.id}\n"
    )


def email_subject_cancelled(booking: Booking) -> str:
    """Patient email subject for cancel."""
    when = to_clinic(booking.starts_at).strftime("%Y-%m-%d %H:%M")
    return f"Booking cancelled — clinic {when}"


def email_body_cancelled(booking: Booking) -> str:
    """Patient email body for cancel."""
    when = to_clinic(booking.starts_at).strftime("%Y-%m-%d %H:%M %Z")
    return (
        f"Hello {booking.patient_name},\n\n"
        f"Your booking with {professional_label(booking)} on {when} "
        f"has been cancelled.\n\nBooking id: {booking.id}\n"
    )


def email_subject_rescheduled(booking: Booking) -> str:
    """Patient email subject for reschedule."""
    when = to_clinic(booking.starts_at).strftime("%Y-%m-%d %H:%M")
    if booking.status == STATUS_PENDING_DOCTOR:
        return f"Rescheduled (pending approval) — clinic {when}"
    return f"Booking rescheduled — clinic {when}"


def email_body_rescheduled(booking: Booking) -> str:
    """Patient email body for reschedule."""
    when = to_clinic(booking.starts_at).strftime("%Y-%m-%d %H:%M %Z")
    pending = ""
    if booking.status == STATUS_PENDING_DOCTOR:
        pending = " This time is pending doctor approval."
    return (
        f"Hello {booking.patient_name},\n\n"
        f"Your booking with {professional_label(booking)} "
        f"(service {service_code(booking)}) is now at {when}.{pending}\n\n"
        f"Booking id: {booking.id}\n"
    )
