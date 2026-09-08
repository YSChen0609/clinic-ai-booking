"""Standard chat replies and FAQ chip tokens (UI sends tokens to /api/chat)."""

from __future__ import annotations

import logging

from clinic_ai_booking.booking import list_professionals, list_services
from clinic_ai_booking.chat.context import ChatContext
from clinic_ai_booking.doctors import DOCTORS
from clinic_ai_booking.hours import BREAKS, CLOSE, OPEN, TIMEZONE_NAME

logger = logging.getLogger(__name__)

# Chip payloads — exact message text from the messenger quick-reply buttons.
FAQ_SERVICES = "FAQ_SERVICES"
FAQ_HOURS = "FAQ_HOURS"
FAQ_DOCTOR = "FAQ_DOCTOR"

FAQ_TOKENS = frozenset({FAQ_SERVICES, FAQ_HOURS, FAQ_DOCTOR})

FAQ_CHIP_LABELS: dict[str, str] = {
    FAQ_SERVICES: "Services",
    FAQ_HOURS: "Hours",
    FAQ_DOCTOR: "Doctors",
}

OUT_OF_SCOPE = (
    "I can only help with clinic services, hours, availability, and booking. "
    "What would you like to know about the clinic?"
)

CONTACT_REQUIRED_MESSAGE = (
    "Visitor contact is required before booking. Ask for the patient's full name "
    "and email before confirming the appointment. Do not invent a name or email."
)

NO_FIELDS_TO_UPDATE = "no fields to update"

PATIENT_EMAIL_UNKNOWN = (
    "patient email unknown — call get_session_info or update_context first"
)


def draft_missing_message(fields: list[str]) -> str:
    """Error when book_appointment is missing draft fields."""
    return f"draft missing: {', '.join(fields)}"


def is_faq_token(message: str) -> bool:
    """True when message is a FAQ chip token."""
    return message.strip() in FAQ_TOKENS


def faq_label(message: str) -> str | None:
    """Return the UI label for a FAQ token, if any."""
    return FAQ_CHIP_LABELS.get(message.strip())


def build_faq_services(ctx: ChatContext) -> str:
    """Standard services FAQ."""
    rows = list_services(ctx.db)
    lines = ["We offer these services (fixed durations):"]
    for row in rows:
        senior = " — seniors only" if row.seniors_only else ""
        lines.append(f"- Service {row.code}: {row.duration_minutes} minutes{senior}")
    lines.append("Which service are you interested in?")
    return "\n".join(lines)


def build_faq_hours() -> str:
    """Standard hours FAQ."""
    lunch = BREAKS[0]
    dinner = BREAKS[1]
    return (
        f"We are open Monday–Friday, {OPEN.strftime('%H:%M')}–{CLOSE.strftime('%H:%M')} "
        f"({TIMEZONE_NAME}). "
        f"Lunch break {lunch[0].strftime('%H:%M')}–{lunch[1].strftime('%H:%M')}; "
        f"dinner break {dinner[0].strftime('%H:%M')}–{dinner[1].strftime('%H:%M')}. "
        "Weekends are closed."
    )


def build_faq_doctor(ctx: ChatContext) -> str:
    """Standard doctors FAQ."""
    try:
        rows = list_professionals(ctx.db)
        lines = ["Our bookable professionals:"]
        for row in rows:
            senior = "senior" if row.is_senior else "junior"
            lines.append(f"- {row.name} ({row.slug}, {senior})")
    except Exception:
        logger.exception("faq doctors: db lookup failed; using static catalog")
        lines = ["Our bookable professionals:"]
        for doc in DOCTORS:
            senior = "senior" if doc.is_senior else "junior"
            lines.append(f"- {doc.name} ({doc.slug}, {senior})")
    lines.append("Who would you like to book with?")
    return "\n".join(lines)


def faq_reply(token: str, ctx: ChatContext) -> str:
    """Return the standard FAQ body for a chip token."""
    key = token.strip()
    if key == FAQ_SERVICES:
        return build_faq_services(ctx)
    if key == FAQ_HOURS:
        return build_faq_hours()
    if key == FAQ_DOCTOR:
        return build_faq_doctor(ctx)
    return OUT_OF_SCOPE
