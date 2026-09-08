"""Tests for FAQ tokens and standard responses."""

from __future__ import annotations

from unittest.mock import MagicMock

from clinic_ai_booking.chat.responses import (
    FAQ_DOCTOR,
    FAQ_HOURS,
    FAQ_SERVICES,
    OUT_OF_SCOPE,
    faq_label,
    faq_reply,
    is_faq_token,
)


def test_faq_tokens() -> None:
    assert is_faq_token(FAQ_SERVICES) is True
    assert is_faq_token("hello") is False
    assert faq_label(FAQ_HOURS) == "Hours"


def test_faq_hours_reply() -> None:
    reply = faq_reply(FAQ_HOURS, MagicMock())
    assert "Monday" in reply
    assert "Friday" in reply


def test_out_of_scope_constant() -> None:
    assert "clinic" in OUT_OF_SCOPE.lower()
