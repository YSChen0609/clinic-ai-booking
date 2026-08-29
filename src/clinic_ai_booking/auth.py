"""Visitor vs login: name+email accounts, session identity, cancel/reschedule gate.

No-password login is MVP-only. Password auth and OAuth are deferred (see TODOS.md).
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from clinic_ai_booking.models import Booking, User

LOGIN_REQUIRED_MESSAGE = "Please log in to cancel or reschedule."
SESSION_USER_ID_KEY = "user_id"


class AuthError(ValueError):
    """Missing login or an action the current identity may not perform."""


def normalize_name(name: str) -> str:
    """Return a non-empty trimmed name, or raise AuthError."""
    cleaned = name.strip()
    if not cleaned:
        raise AuthError("name is required")
    return cleaned


def normalize_email(email: str) -> str:
    """Return a normalized email, or raise AuthError if it looks invalid."""
    cleaned = email.strip().lower()
    if "@" not in cleaned:
        raise AuthError("email is invalid")
    local, _, domain = cleaned.partition("@")
    if not local or not domain or " " in cleaned or "." not in domain:
        raise AuthError("email is invalid")
    return cleaned


def get_user_by_email(session: Session, email: str) -> User | None:
    """Return the user for that email, or None."""
    return session.scalar(select(User).where(User.email == normalize_email(email)))


def get_user_by_id(session: Session, user_id: int) -> User | None:
    """Return the user for that id, or None."""
    return session.get(User, user_id)


def get_or_create_user(session: Session, name: str, email: str) -> User:
    """Find by email or insert a patient account; refresh name when found."""
    cleaned_name = normalize_name(name)
    cleaned_email = normalize_email(email)
    user = session.scalar(select(User).where(User.email == cleaned_email))
    if user is None:
        user = User(
            email=cleaned_email,
            name=cleaned_name,
            created_at=datetime.now(UTC),
        )
        session.add(user)
        session.flush()
        return user
    if user.name != cleaned_name:
        user.name = cleaned_name
        session.flush()
    return user


def login_with_name_email(session: Session, name: str, email: str) -> User:
    """Create or load the patient account used for a logged-in session."""
    return get_or_create_user(session, name, email)


def require_login(user: User | None) -> User:
    """Return user, or raise AuthError asking the visitor to log in."""
    if user is None:
        raise AuthError(LOGIN_REQUIRED_MESSAGE)
    return user


def user_owns_booking(user: User, booking: Booking) -> bool:
    """True if this booking belongs to the logged-in patient."""
    if booking.user_id is not None and booking.user_id == user.id:
        return True
    return booking.patient_email == user.email


def cancel_for_user(session: Session, booking_id: int, user: User | None) -> Booking:
    """Cancel when logged in and the booking is theirs; visitors get AuthError."""
    from clinic_ai_booking.booking import cancel_appointment

    actor = require_login(user)
    booking = session.get(Booking, booking_id)
    if booking is None:
        raise AuthError(f"unknown booking: {booking_id}")
    if not user_owns_booking(actor, booking):
        raise AuthError("that booking belongs to another patient")
    return cancel_appointment(session, booking_id)


def reschedule_for_user(
    session: Session,
    booking_id: int,
    starts_at: datetime,
    user: User | None,
) -> Booking:
    """Reschedule when logged in and the booking is theirs; visitors get AuthError."""
    from clinic_ai_booking.booking import reschedule_appointment

    actor = require_login(user)
    booking = session.get(Booking, booking_id)
    if booking is None:
        raise AuthError(f"unknown booking: {booking_id}")
    if not user_owns_booking(actor, booking):
        raise AuthError("that booking belongs to another patient")
    return reschedule_appointment(session, booking_id, starts_at)
