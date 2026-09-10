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


def test_offer_slots_reply_shows_start_windows_and_duration() -> None:
    from clinic_ai_booking.chat.facts import empty_facts
    from clinic_ai_booking.chat.reply import _fallback_reply

    facts = empty_facts()
    facts["status"] = "offer_slots"
    facts["resolved_day"] = "Monday 31 Aug 2026"
    facts["clinic_today"] = "Monday 31 Aug 2026"
    facts["offered_times"] = ["09:00", "09:15", "09:30"]
    facts["availability"] = {
        "weekday": "Monday",
        "service_code": "A",
        "duration_minutes": 60,
        "bands": {
            "morning": {
                "start_windows": [["09:00", "11:00"]],
                "sample_starts": ["09:00"],
            },
            "afternoon": {
                "start_windows": [["13:00", "16:00"]],
                "sample_starts": [],
            },
            "evening": {"start_windows": [], "sample_starts": []},
        },
    }
    text = _fallback_reply(facts)
    assert "Start times on Monday 31 Aug 2026" in text
    assert "• Morning: 09:00–11:00" in text
    assert "• Afternoon: 13:00–16:00" in text
    assert "Service A (60 min)" in text
    assert "What start works?" in text
    assert "clinic today" not in text
    assert "15-minute grid" not in text
    assert "Examples:" not in text


def test_offer_slots_reply_today_first_asks_other_days() -> None:
    from clinic_ai_booking.chat.facts import empty_facts
    from clinic_ai_booking.chat.reply import _fallback_reply

    facts = empty_facts()
    facts["status"] = "offer_slots"
    facts["resolved_day"] = "Monday 31 Aug 2026"
    facts["clinic_today"] = "Monday 31 Aug 2026"
    facts["availability"] = {
        "day": "2026-08-31",
        "service_code": "B",
        "duration_minutes": 30,
        "suggest_other_days": True,
        "bands": {
            "evening": {
                "start_windows": [["18:00", "19:30"]],
                "sample_starts": ["18:00"],
            },
            "morning": {"start_windows": [], "sample_starts": []},
            "afternoon": {"start_windows": [], "sample_starts": []},
        },
    }
    text = _fallback_reply(facts)
    assert text.startswith("Later today (Monday 31 Aug 2026) — start times")
    assert "• Evening: 18:00–19:30" in text
    assert "Service B (30 min)" in text
    assert "tomorrow / another day" in text
    assert "What start works?" not in text


def test_offer_slots_reply_next_open_asks_other_day() -> None:
    from clinic_ai_booking.chat.facts import empty_facts
    from clinic_ai_booking.chat.reply import _fallback_reply

    facts = empty_facts()
    facts["status"] = "offer_slots"
    facts["resolved_day"] = "Tuesday 1 Sep 2026"
    facts["clinic_today"] = "Monday 31 Aug 2026"
    facts["availability"] = {
        "day": "2026-09-01",
        "service_code": "A",
        "duration_minutes": 60,
        "suggest_other_days": True,
        "bands": {
            "morning": {
                "start_windows": [["09:00", "11:00"]],
                "sample_starts": ["09:00"],
            },
            "afternoon": {"start_windows": [], "sample_starts": []},
            "evening": {"start_windows": [], "sample_starts": []},
        },
    }
    text = _fallback_reply(facts)
    assert text.startswith("Next open (Tuesday 1 Sep 2026) — start times")
    assert "or another day" in text
    assert "tomorrow / another day" not in text


def test_booked_reply_uses_clean_summary() -> None:
    from clinic_ai_booking.chat.facts import empty_facts
    from clinic_ai_booking.chat.reply import _fallback_reply

    facts = empty_facts()
    facts["status"] = "booked"
    facts["booking_id"] = 33
    facts["professional_name"] = "Dr. Alex Chen"
    facts["service_code"] = "A"
    facts["resolved_day"] = "Friday 18 Sep 2026"
    facts["offered_times"] = ["11:00"]
    facts["ends_at_clock"] = "12:00"
    text = _fallback_reply(facts)
    assert text.startswith("Your appointment is booked.")
    assert "Professional: Dr. Alex Chen" in text
    assert "Service: A" in text
    assert "Time: Friday 18 Sep 2026, 11:00–12:00 (Taipei, UTC+8)" in text
    assert "Reference: #33" in text
    assert "T11:00" not in text
    assert "+08:00" not in text


def test_need_confirm_book_reply_uses_clean_summary() -> None:
    from clinic_ai_booking.chat.facts import empty_facts
    from clinic_ai_booking.chat.reply import _fallback_reply

    facts = empty_facts()
    facts["status"] = "need_confirm_book"
    facts["professional_name"] = "Dr. Alex Chen"
    facts["service_code"] = "A"
    facts["resolved_day"] = "Friday 18 Sep 2026"
    facts["offered_times"] = ["11:00"]
    facts["ends_at_clock"] = "12:00"
    text = _fallback_reply(facts)
    assert text.startswith("Please confirm your booking:")
    assert "Professional: Dr. Alex Chen" in text
    assert "Reply yes to book" in text


def test_junior_blocked_reply_is_multiline() -> None:
    from clinic_ai_booking.chat.facts import empty_facts
    from clinic_ai_booking.chat.reply import _fallback_reply

    facts = empty_facts()
    facts["status"] = "need_confirm_doctor"
    facts["doctor_candidates"] = [
        "1. Dr. Maya Lin (senior)",
        "2. Dr. Jordan Wu (senior)",
    ]
    facts["error"] = (
        "Dr. Alex Chen is a junior professional and cannot perform service C "
        "(seniors only).\n\n"
        "Seniors who can do this service:\n"
        "1. Dr. Maya Lin (senior)\n"
        "2. Dr. Jordan Wu (senior)\n\n"
        "Reply with a number or name, or choose a different service (A–E)."
    )
    text = _fallback_reply(facts)
    assert "junior professional" in text
    assert "\n\nSeniors who can do this service:\n" in text
    assert "1. Dr. Maya Lin (senior)" in text
    assert "2. Dr. Jordan Wu (senior)" in text
