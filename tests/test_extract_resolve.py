"""Unit tests for day-intent calculator and extract sanitize (no live LLM)."""

from __future__ import annotations

from datetime import date, time

from clinic_ai_booking.chat.extract import TurnExtract, sanitize_extract
from clinic_ai_booking.chat.resolve import (
    detect_time_band,
    filter_starts_by_band,
    is_concrete_clock,
    is_explicit_cancel_or_reschedule,
    resolve_day_intent,
    wants_other_slot,
)
from clinic_ai_booking.hours import clinic_datetime


def test_next_friday_from_monday_is_eleven_days() -> None:
    today = date(2026, 9, 7)  # Monday
    resolved = resolve_day_intent(
        today,
        day_kind="weekday",
        weekday="friday",
        weekday_when="next",
    )
    assert resolved == date(2026, 9, 18)
    assert (resolved - today).days == 11


def test_upcoming_wednesday_from_monday() -> None:
    today = date(2026, 9, 7)
    assert (
        resolve_day_intent(
            today, day_kind="weekday", weekday="wednesday", weekday_when="upcoming"
        )
        == date(2026, 9, 9)
    )


def test_sanitize_uses_llm_day_intent_not_raw_wed_regex() -> None:
    today = date(2026, 9, 7)
    # Simulates LLM understanding "Wed." → weekday=wednesday
    cleaned = sanitize_extract(
        TurnExtract(
            in_scope=True,
            day_kind="weekday",
            weekday="wednesday",
            weekday_when="upcoming",
        ),
        user_text="No, I said Wed.",
        today=today,
    )
    assert cleaned.day == "2026-09-09"


def test_sanitize_without_day_intent_leaves_day_null() -> None:
    today = date(2026, 9, 7)
    cleaned = sanitize_extract(
        TurnExtract(in_scope=True, day_kind="none"),
        user_text="No, I said Wed.",
        today=today,
    )
    # No calculator input from LLM → no silent regex expansion of Wed.
    assert cleaned.day is None


def test_tmr_via_day_kind_tomorrow() -> None:
    today = date(2026, 9, 7)
    cleaned = sanitize_extract(
        TurnExtract(in_scope=True, day_kind="tomorrow"),
        user_text="book A tmr morning with Chen",
        today=today,
    )
    assert cleaned.day == "2026-09-08"


def test_another_d_service_letter(db_session) -> None:
    from clinic_ai_booking.chat.resolve import detect_service_in_text

    assert (
        detect_service_in_text(
            db_session, "I also want another D on Wednesday with Dr. Wu"
        )
        == "D"
    )


def test_morning_is_band_not_clock() -> None:
    assert detect_time_band("tmr morning") == "morning"
    assert not is_concrete_clock("morning")
    assert is_concrete_clock("11:00")
    assert is_concrete_clock("1400")
    assert not is_concrete_clock("11")


def test_clock_from_user_text_accepts_compact_hhmm() -> None:
    from clinic_ai_booking.chat.resolve import clock_from_user_text, parse_starts_at

    assert clock_from_user_text("1400") == "14:00"
    assert clock_from_user_text("14:00") == "14:00"
    assert clock_from_user_text("Mankey, mankey@gmail.com") is None
    day = date(2026, 9, 8)
    assert parse_starts_at("1400", day=day) is not None
    assert parse_starts_at("1400", day=day).hour == 14


def test_sanitize_keeps_1400_as_starts_at() -> None:
    today = date(2026, 9, 7)
    cleaned = sanitize_extract(
        TurnExtract(in_scope=True, day_kind="none", starts_at=None),
        user_text="1400",
        today=today,
    )
    assert cleaned.starts_at == "14:00"


def test_apply_extract_same_day_keeps_starts_at() -> None:
    from unittest.mock import MagicMock

    from clinic_ai_booking.chat.context import BookingDraft, ChatContext
    from clinic_ai_booking.chat.extract import apply_extract

    ctx = ChatContext(db=MagicMock(), user_id=None)
    ctx.draft = BookingDraft(
        service_code="A",
        professional_slug="junior",
        day="2026-09-08",
        starts_at="14:00",
    )
    # Contact turn: LLM may re-emit the same day from history — must not wipe the slot.
    apply_extract(
        ctx,
        TurnExtract(
            in_scope=True,
            day_kind="absolute",
            day_absolute="2026-09-08",
            day="2026-09-08",
            patient_name="Mankey",
            patient_email="mankey@gmail.com",
        ),
        user_text="Mankey, mankey@gmail.com",
    )
    assert ctx.draft.starts_at == "14:00"
    assert ctx.patient_email == "mankey@gmail.com"
    assert ctx.can_book_now is True


def test_sanitize_strips_invented_morning_clock() -> None:
    today = date(2026, 9, 7)
    cleaned = sanitize_extract(
        TurnExtract(
            in_scope=True,
            intent="book",
            service_code="A",
            professional="Chen",
            day_kind="tomorrow",
            starts_at="11:00",
            time_band=None,
        ),
        user_text="Can I book service A tmr morning with Dr. Chen",
        today=today,
    )
    assert cleaned.day == "2026-09-08"
    assert cleaned.time_band == "morning"
    assert cleaned.starts_at is None


def test_other_time_is_not_reschedule() -> None:
    assert wants_other_slot("Well, other time on the same day?")
    assert not is_explicit_cancel_or_reschedule("Well, other time on the same day?")
    today = date(2026, 9, 7)
    cleaned = sanitize_extract(
        TurnExtract(in_scope=True, intent="reschedule", starts_at="09:00"),
        user_text="Well, other time on the same day?",
        today=today,
    )
    assert cleaned.intent == "book"
    assert cleaned.starts_at is None


def test_sanitize_does_not_keep_llm_email_without_user_typing_it() -> None:
    from unittest.mock import MagicMock

    from clinic_ai_booking.chat.context import ChatContext
    from clinic_ai_booking.chat.extract import apply_extract

    ctx = ChatContext(db=MagicMock(), user_id=None)
    extracted = TurnExtract(
        in_scope=True,
        patient_name="Invented",
        patient_email="invented@example.com",
    )
    apply_extract(ctx, extracted, user_text="Book service A tomorrow morning")
    assert ctx.patient_email is None
    assert ctx.can_book_now is False


def test_apply_extract_parses_name_comma_email() -> None:
    from unittest.mock import MagicMock

    from clinic_ai_booking.chat.context import ChatContext
    from clinic_ai_booking.chat.extract import apply_extract

    ctx = ChatContext(db=MagicMock(), user_id=None)
    apply_extract(
        ctx,
        TurnExtract(in_scope=True),
        user_text="use: Ten, ten@gmail.com",
    )
    assert ctx.patient_email == "ten@gmail.com"
    assert ctx.patient_name == "Ten"
    assert ctx.can_book_now is True


def test_resolve_dr_chen_and_detect_in_text(db_session) -> None:
    from clinic_ai_booking.chat.resolve import (
        detect_professional_in_text,
        resolve_professional_slug,
    )

    assert resolve_professional_slug(db_session, "Dr. Chen") == "junior"
    assert (
        detect_professional_in_text(
            db_session, "Can I have a service A with Dr. Chen tmr morning?"
        )
        == "junior"
    )


def test_compress_chat_history_truncates() -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    from clinic_ai_booking.chat.history import compress_chat_history

    messages = [
        HumanMessage(content="book A tmr"),
        AIMessage(content="ok " + ("x" * 300)),
        HumanMessage(content="Wed please"),
    ]
    text = compress_chat_history(messages)
    assert "User: book A tmr" in text
    assert "Wed please" not in text
    assert "…" in text


def test_filter_morning_band() -> None:
    day = date(2026, 8, 31)
    starts = [
        clinic_datetime(day, time(9, 0)),
        clinic_datetime(day, time(14, 0)),
        clinic_datetime(day, time(18, 0)),
    ]
    morning = filter_starts_by_band(starts, "morning")
    assert len(morning) == 1
    assert morning[0].hour == 9
