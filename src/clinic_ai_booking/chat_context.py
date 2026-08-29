"""Per-turn runtime context for booking tools and middleware."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session


@dataclass
class ChatContext:
    """Request-scoped identity + DB for tools/middleware (not checkpointed)."""

    db: Session
    user_id: int | None
    user_name: str | None
    user_email: str | None
    visitor_name: str | None = None
    visitor_email: str | None = None

    @property
    def is_authenticated(self) -> bool:
        """True when a logged-in patient is on the session."""
        return self.user_id is not None

    @property
    def has_visitor_contact(self) -> bool:
        """True when sticky visitor name+email are already known."""
        return bool(self.visitor_name and self.visitor_email)

    def patient_name(self) -> str | None:
        """Logged-in name, else sticky visitor name."""
        if self.is_authenticated:
            return self.user_name
        return self.visitor_name

    def patient_email(self) -> str | None:
        """Logged-in email, else sticky visitor email."""
        if self.is_authenticated:
            return self.user_email
        return self.visitor_email

    def set_visitor(self, name: str, email: str) -> None:
        """Remember visitor contact for this turn (caller may persist to session)."""
        self.visitor_name = name
        self.visitor_email = email
