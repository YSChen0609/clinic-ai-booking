"""Per-invoke agent context and cookie session load/save (rehydrate / dehydrate)."""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from clinic_ai_booking.domain.hours import TIMEZONE
from clinic_ai_booking.domain.models import User

# Legacy sticky keys — purged on load/save/reset so old cookies cannot haunt chat.
SESSION_VISITOR_NAME_KEY = "visitor_name"
SESSION_VISITOR_EMAIL_KEY = "visitor_email"
SESSION_CONTACT_CONFIRMED_KEY = "visitor_contact_confirmed"

SESSION_BOOKING_DRAFT_KEY = "booking_draft"
# Contact for this chat thread (kept across bookings; cleared on Reset chat).
SESSION_CHAT_CONTACT_KEY = "chat_contact"
# Last successful book cues for "him" / "also … afternoon".
SESSION_CHAT_MEMORY_KEY = "chat_memory"


def clinic_today(now: datetime | None = None) -> date:
    """Return today's calendar date in Asia/Taipei."""
    clock = now.astimezone(TIMEZONE) if now is not None else datetime.now(TIMEZONE)
    return clock.date()


@dataclass
class BookingDraft:
    """In-progress booking choices; persisted via save_context."""

    service_code: str | None = None
    professional_slug: str | None = None
    day: str | None = None
    starts_at: str | None = None
    # morning | afternoon | evening — prefer over inventing a clock time
    time_band: str | None = None

    def to_session(self) -> dict[str, Any]:
        """Serialize for Starlette session storage."""
        return asdict(self)

    @classmethod
    def from_session(cls, raw: Any) -> BookingDraft:
        """Load draft from session dict, or empty draft."""
        if not isinstance(raw, dict):
            return cls()
        band = raw.get("time_band")
        if band not in {"morning", "afternoon", "evening"}:
            band = None
        return cls(
            service_code=raw.get("service_code"),
            professional_slug=raw.get("professional_slug"),
            day=raw.get("day"),
            starts_at=raw.get("starts_at"),
            time_band=band,
        )


@dataclass
class ChatMemory:
    """Short chat-thread memory after a successful book (not legacy sticky identity)."""

    last_professional_slug: str | None = None
    last_day: str | None = None

    def to_session(self) -> dict[str, Any]:
        """Serialize for the session cookie."""
        return asdict(self)

    @classmethod
    def from_session(cls, raw: Any) -> ChatMemory:
        """Load memory from session dict."""
        if not isinstance(raw, dict):
            return cls()
        return cls(
            last_professional_slug=raw.get("last_professional_slug"),
            last_day=raw.get("last_day"),
        )


@dataclass
class ChatContext:
    """Runtime context for the turn graph (context_schema=ChatContext)."""

    db: Session
    user_id: int | None
    patient_name: str | None = None
    patient_email: str | None = None
    today: date | None = field(default=None)
    draft: BookingDraft = field(default_factory=BookingDraft)
    memory: ChatMemory = field(default_factory=ChatMemory)
    # Visitor contact usable after they typed it once in this chat thread.
    contact_confirmed: bool = False

    @property
    def is_authenticated(self) -> bool:
        """True when a logged-in patient is on the session."""
        return self.user_id is not None

    @property
    def has_contact(self) -> bool:
        """True when name and email are present."""
        return bool(self.patient_name and self.patient_email)

    @property
    def can_book_now(self) -> bool:
        """True when identity may be used to write a booking."""
        if not self.has_contact:
            return False
        if self.is_authenticated:
            return True
        return self.contact_confirmed

    def clinic_day(self) -> date:
        """Clinic today for this turn (Asia/Taipei unless tests override)."""
        return self.today if self.today is not None else clinic_today()

    def clear_draft(self) -> None:
        """Drop in-progress booking choices after a successful book."""
        self.draft = BookingDraft()

    def remember_book(
        self, *, professional_slug: str, day: str
    ) -> None:
        """Keep doctor/day for follow-ups; clear draft; keep typed contact."""
        self.memory.last_professional_slug = professional_slug
        self.memory.last_day = day
        self.clear_draft()

    def clear_visitor_booking_state(self) -> None:
        """Drop draft + visitor contact + memory (chat reset only)."""
        self.clear_draft()
        self.memory = ChatMemory()
        if not self.is_authenticated:
            self.patient_name = None
            self.patient_email = None
            self.contact_confirmed = False


def load_context(
    session: MutableMapping[str, Any],
    db: Session,
    *,
    user: User | None = None,
) -> ChatContext:
    """Rehydrate ChatContext from the signed cookie session before agent.invoke."""
    purge_legacy_visitor_contact(session)
    memory = ChatMemory.from_session(session.get(SESSION_CHAT_MEMORY_KEY))
    draft = BookingDraft.from_session(session.get(SESSION_BOOKING_DRAFT_KEY))
    if user is not None:
        return ChatContext(
            db=db,
            user_id=user.id,
            patient_name=user.name,
            patient_email=user.email,
            contact_confirmed=True,
            draft=draft,
            memory=memory,
        )
    name, email = _load_chat_contact(session)
    return ChatContext(
        db=db,
        user_id=None,
        patient_name=name,
        patient_email=email,
        contact_confirmed=bool(name and email),
        draft=draft,
        memory=memory,
    )


def save_context(session: MutableMapping[str, Any], ctx: ChatContext) -> None:
    """Persist draft, thread memory, and chat contact; purge legacy sticky keys."""
    purge_legacy_visitor_contact(session)
    session[SESSION_BOOKING_DRAFT_KEY] = ctx.draft.to_session()
    session[SESSION_CHAT_MEMORY_KEY] = ctx.memory.to_session()
    if ctx.is_authenticated:
        # Login identity is the source of truth; still keep thread memory.
        return
    if ctx.contact_confirmed and ctx.has_contact:
        session[SESSION_CHAT_CONTACT_KEY] = {
            "name": ctx.patient_name,
            "email": ctx.patient_email,
        }
    else:
        session.pop(SESSION_CHAT_CONTACT_KEY, None)


def clear_chat_booking_state(session: MutableMapping[str, Any]) -> None:
    """Clear draft, contact, and memory (chat reset / new thread)."""
    session.pop(SESSION_BOOKING_DRAFT_KEY, None)
    session.pop(SESSION_CHAT_CONTACT_KEY, None)
    session.pop(SESSION_CHAT_MEMORY_KEY, None)
    purge_legacy_visitor_contact(session)


def purge_legacy_visitor_contact(session: MutableMapping[str, Any]) -> None:
    """Remove old sticky visitor keys that caused silent email reuse."""
    session.pop(SESSION_VISITOR_NAME_KEY, None)
    session.pop(SESSION_VISITOR_EMAIL_KEY, None)
    session.pop(SESSION_CONTACT_CONFIRMED_KEY, None)


def has_patient_contact_in_session(session: MutableMapping[str, Any]) -> bool:
    """Return whether chat-thread contact is on the session cookie."""
    name, email = _load_chat_contact(session)
    return bool(name and email)


def has_visitor_contact_in_session(session: MutableMapping[str, Any]) -> bool:
    """Alias for cookie-only contact check (API compat)."""
    return has_patient_contact_in_session(session)


def _load_chat_contact(
    session: MutableMapping[str, Any],
) -> tuple[str | None, str | None]:
    raw = session.get(SESSION_CHAT_CONTACT_KEY)
    if not isinstance(raw, dict):
        return None, None
    name = raw.get("name")
    email = raw.get("email")
    if isinstance(name, str) and name.strip() and isinstance(email, str) and email.strip():
        return name.strip(), email.strip()
    return None, None
