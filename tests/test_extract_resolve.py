"""Unit tests for day-intent calculator and extract sanitize (no live LLM)."""

from __future__ import annotations

from datetime import date, time

from clinic_ai_booking.chat.extract import TurnExtract, sanitize_extract
from clinic_ai_booking.chat.resolve import (
    detect_all_services_in_text,
    detect_time_band,
    filter_starts_by_band,
    format_day_label,
    is_concrete_clock,
    is_explicit_cancel_or_reschedule,
    resolve_day_intent,
    wants_other_slot,
)
from clinic_ai_booking.domain.hours import (
    clinic_datetime,
    format_patient_day,
    format_patient_span,
)


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


def test_week_after_next_friday_from_monday() -> None:
    today = date(2026, 9, 7)  # Monday
    resolved = resolve_day_intent(
        today,
        day_kind="weekday",
        weekday="friday",
        weekday_when="week_after_next",
    )
    assert resolved == date(2026, 9, 25)
    assert (resolved - today).days == 18


def test_parse_clinic_day_tomorrow_and_iso() -> None:
    from clinic_ai_booking.chat.resolve import parse_clinic_day

    today = date(2026, 9, 7)
    assert parse_clinic_day("tmr", today) == date(2026, 9, 8)
    assert parse_clinic_day("tomorrow", today) == date(2026, 9, 8)
    assert parse_clinic_day("2026-09-18", today) == date(2026, 9, 18)
    assert parse_clinic_day("friday", today) == date(2026, 9, 11)


def test_date_cheat_sheet_lists_today_and_tomorrow() -> None:
    from clinic_ai_booking.chat.resolve import date_cheat_sheet

    today = date(2026, 9, 7)
    sheet = date_cheat_sheet(today, days=3)
    assert "2026-09-07" in sheet
    assert "Monday" in sheet
    assert "tomorrow" in sheet
    assert "2026-09-09" in sheet


def test_sanitize_day_phrase_via_dateparser() -> None:
    today = date(2026, 9, 7)  # Monday
    cleaned = sanitize_extract(
        TurnExtract(
            in_scope=True,
            day_kind="none",
            day_phrase="day after tomorrow",
        ),
        user_text="can we do the day after tomorrow?",
        today=today,
    )
    assert cleaned.day == "2026-09-09"


def test_aliases_map_chen_and_service() -> None:
    from clinic_ai_booking.chat.aliases import (
        normalize_professional_alias,
        normalize_service_alias,
    )

    assert normalize_professional_alias("Dr. Chen") == "junior"
    assert normalize_professional_alias("Maya Lin") == "senior-1"
    assert normalize_service_alias("service c") == "C"
    assert normalize_service_alias("Z") is None


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
    assert is_concrete_clock("19")
    assert is_concrete_clock("11")
    # Menu picks — must not be clocks when alone.
    assert not is_concrete_clock("1")
    assert not is_concrete_clock("2")


def test_clock_from_user_text_accepts_compact_hhmm() -> None:
    from clinic_ai_booking.chat.resolve import clock_from_user_text, parse_starts_at

    assert clock_from_user_text("1400") == "14:00"
    assert clock_from_user_text("14:00") == "14:00"
    assert clock_from_user_text("19") == "19:00"
    assert clock_from_user_text("let's do 19") == "19:00"
    assert clock_from_user_text("Let's do 19") == "19:00"
    assert clock_from_user_text("at 9") == "09:00"
    assert clock_from_user_text("1") is None
    assert clock_from_user_text("2") is None
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
        professional_confirmed=True,
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
        rank_professionals,
        resolve_professional_slug,
    )

    assert resolve_professional_slug(db_session, "Dr. Chen") == "junior"
    assert (
        detect_professional_in_text(
            db_session, "Can I have a service A with Dr. Chen tmr morning?"
        )
        == "junior"
    )
    senior_hits = rank_professionals(db_session, "senior")
    assert {m.slug for m in senior_hits} >= {"senior-1", "senior-2"}


def test_apply_extract_locks_chen_when_unique(db_session) -> None:
    from clinic_ai_booking.chat.context import ChatContext
    from clinic_ai_booking.chat.extract import TurnExtract, apply_extract

    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 8, 31))
    apply_extract(
        ctx,
        TurnExtract(in_scope=True, professional="Chen", service_code="A"),
        user_text="Book A with Dr. Chen",
    )
    assert ctx.draft.professional_slug == "junior"
    assert ctx.draft.professional_confirmed is True


def test_apply_extract_doctor_only_asks_service_next(db_session) -> None:
    """Clear 'Dr. Chen' with no service → locked doctor; book graph asks service."""
    from clinic_ai_booking.chat.book_graph import doctor_service
    from clinic_ai_booking.chat.context import ChatContext
    from clinic_ai_booking.chat.extract import TurnExtract, apply_extract

    class _Runtime:
        def __init__(self, ctx: ChatContext) -> None:
            self.context = ctx

    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 8, 31))
    apply_extract(
        ctx,
        TurnExtract(in_scope=True, professional="Chen"),
        user_text="Dr. Chen",
    )
    assert ctx.draft.professional_confirmed is True
    assert ctx.draft.service_code is None
    out = doctor_service(
        {
            "messages": [],
            "user_text": "Dr. Chen",
            "reply": "",
            "in_scope": True,
            "facts": {},
            "intent": "book",
        },
        _Runtime(ctx),
    )
    assert out["facts"]["status"] == "need_info"
    assert "service_code" in out["facts"]["missing"]
    assert out["facts"]["status"] != "need_confirm_doctor"

def test_apply_extract_senior_is_ambiguous(db_session) -> None:
    from clinic_ai_booking.chat.context import ChatContext
    from clinic_ai_booking.chat.extract import TurnExtract, apply_extract

    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 8, 31))
    apply_extract(
        ctx,
        TurnExtract(in_scope=True, professional="senior"),
        user_text="book with the senior",
    )
    assert ctx.draft.professional_slug is None
    assert set(ctx.draft.professional_candidates) == {"senior-1", "senior-2"}
    apply_extract(ctx, TurnExtract(in_scope=True), user_text="2")
    assert ctx.draft.professional_slug == "senior-2"
    assert ctx.draft.professional_confirmed is True


def test_apply_extract_typo_asks_confirm_not_lock(db_session) -> None:
    from clinic_ai_booking.chat.context import ChatContext
    from clinic_ai_booking.chat.extract import TurnExtract, apply_extract

    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 8, 31))
    ctx.draft.service_code = "C"
    ctx.draft.professional_candidates = ["senior-1", "senior-2"]
    apply_extract(ctx, TurnExtract(in_scope=True), user_text="Lon then")
    assert ctx.draft.professional_slug == "senior-1"
    assert ctx.draft.professional_confirmed is False
    assert ctx.draft.professional_candidates == ["senior-1", "senior-2"]


def test_apply_extract_contact_does_not_overwrite_locked_doctor(db_session) -> None:
    from clinic_ai_booking.chat.context import ChatContext
    from clinic_ai_booking.chat.extract import TurnExtract, apply_extract

    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 8, 31))
    ctx.draft.service_code = "C"
    ctx.draft.professional_slug = "senior-1"
    ctx.draft.professional_confirmed = True
    ctx.draft.professional_candidates = ["senior-1"]
    apply_extract(
        ctx,
        TurnExtract(
            in_scope=True,
            professional="Dr. Chen",
            patient_name="Tester",
            patient_email="tester@gmail.com",
        ),
        user_text="Tester, tester@gmail.com",
    )
    assert ctx.draft.professional_slug == "senior-1"
    assert ctx.draft.professional_confirmed is True


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


def test_sanitize_next_friday_leaves_day_null() -> None:
    today = date(2026, 9, 9)  # Wednesday
    cleaned = sanitize_extract(
        TurnExtract(
            in_scope=True,
            day_kind="weekday",
            weekday="friday",
            weekday_when="next",
        ),
        user_text="service B with Dr. Chen next Friday",
        today=today,
    )
    assert cleaned.day is None


def test_apply_extract_next_friday_sets_clarify_options() -> None:
    from unittest.mock import MagicMock

    from clinic_ai_booking.chat.context import ChatContext
    from clinic_ai_booking.chat.extract import apply_extract

    ctx = ChatContext(db=MagicMock(), user_id=None, today=date(2026, 9, 9))
    apply_extract(
        ctx,
        TurnExtract(
            in_scope=True,
            day_kind="weekday",
            weekday="friday",
            weekday_when="next",
            day=None,
        ),
        user_text="I'd like service B next Friday",
    )
    assert ctx.draft.day is None
    assert ctx.draft.day_clarify_options == ["2026-09-11", "2026-09-18"]


def test_apply_extract_picks_day_clarify_option() -> None:
    from unittest.mock import MagicMock

    from clinic_ai_booking.chat.context import BookingDraft, ChatContext
    from clinic_ai_booking.chat.extract import apply_extract

    ctx = ChatContext(db=MagicMock(), user_id=None, today=date(2026, 9, 9))
    ctx.draft = BookingDraft(day_clarify_options=["2026-09-11", "2026-09-18"])
    apply_extract(ctx, TurnExtract(in_scope=True), user_text="1")
    assert ctx.draft.day == "2026-09-11"
    assert ctx.draft.day_clarify_options == []


def test_apply_extract_same_day_overrides_llm_friday(db_session) -> None:
    from clinic_ai_booking.chat.context import ChatContext, ChatMemory
    from clinic_ai_booking.chat.extract import apply_extract

    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 9, 9))
    ctx.memory = ChatMemory(last_professional_slug="junior", last_day="2026-09-18")
    apply_extract(
        ctx,
        TurnExtract(
            in_scope=True,
            service_code="C",
            day_kind="weekday",
            weekday="friday",
            weekday_when="upcoming",
            day="2026-09-11",
        ),
        user_text="Okay, I also want a service C with him on the same day",
    )
    assert ctx.draft.day == "2026-09-18"
    assert ctx.draft.professional_slug == "junior"
    assert ctx.draft.professional_candidates == ["junior"]


def test_apply_extract_him_uses_memory_doctor(db_session) -> None:
    from clinic_ai_booking.chat.context import ChatContext, ChatMemory
    from clinic_ai_booking.chat.extract import apply_extract

    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 9, 9))
    ctx.memory = ChatMemory(last_professional_slug="junior", last_day="2026-09-18")
    apply_extract(
        ctx,
        TurnExtract(in_scope=True, service_code="B"),
        user_text="also book B with him",
    )
    assert ctx.draft.professional_slug == "junior"
    assert ctx.draft.professional_candidates == ["junior"]


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


def test_format_patient_day_is_friendly() -> None:
    assert format_patient_day(date(2026, 9, 18)) == "Friday 18 Sep 2026"
    assert format_day_label(date(2026, 9, 18)) == "Friday 18 Sep 2026"


def test_format_patient_span_has_start_and_end() -> None:
    start = clinic_datetime(date(2026, 9, 18), time(11, 0))
    end = clinic_datetime(date(2026, 9, 18), time(12, 0))
    assert format_patient_span(start, end) == "Friday 18 Sep 2026, 11:00–12:00"


def test_detect_all_services_in_text_finds_pair(db_session) -> None:
    assert detect_all_services_in_text(db_session, "I want service A and B") == ["A", "B"]
    assert detect_all_services_in_text(db_session, "book A & C please") == ["A", "C"]
    assert detect_all_services_in_text(db_session, "service A only") == ["A"]


def test_detect_unknown_service_letter() -> None:
    from clinic_ai_booking.chat.resolve import detect_unknown_service_letter

    assert detect_unknown_service_letter("I want a service F with Dr. Lon") == "F"
    assert detect_unknown_service_letter("I want F") == "F"
    assert detect_unknown_service_letter("I want service F") == "F"
    assert detect_unknown_service_letter("service A with Chen") is None
    assert detect_unknown_service_letter("book B tomorrow") is None


def test_apply_extract_unknown_service_does_not_invent(db_session) -> None:
    from clinic_ai_booking.chat.context import ChatContext
    from clinic_ai_booking.chat.extract import TurnExtract, apply_extract

    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 8, 31))
    apply_extract(
        ctx,
        TurnExtract(in_scope=True, service_code="A", professional="Lin"),
        user_text="I'd like to book a service F with Dr. Lon",
    )
    assert ctx.draft.service_code is None
    assert ctx.draft.professional_slug == "senior-1"
    assert ctx.draft.professional_confirmed is False


def test_apply_extract_keeps_prior_service_when_unknown_named(db_session) -> None:
    from clinic_ai_booking.chat.context import ChatContext
    from clinic_ai_booking.chat.extract import TurnExtract, apply_extract

    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 8, 31))
    ctx.draft.service_code = "B"
    apply_extract(
        ctx,
        TurnExtract(in_scope=True, service_code="A"),
        user_text="actually service F",
    )
    assert ctx.draft.service_code == "B"


def test_apply_extract_yes_does_not_invent_next_friday(db_session) -> None:
    from clinic_ai_booking.chat.context import ChatContext
    from clinic_ai_booking.chat.extract import TurnExtract, apply_extract

    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 9, 10))
    ctx.draft.service_code = "E"
    ctx.draft.professional_slug = "senior-1"
    ctx.draft.professional_confirmed = True
    ctx.draft.day = "2026-09-11"
    ctx.draft.starts_at = "13:00"
    ctx.draft.starts_at_confirmed = True
    ctx.patient_name = "Chris"
    ctx.patient_email = "chris@example.com"
    ctx.contact_confirmed = True
    apply_extract(
        ctx,
        TurnExtract(
            in_scope=True,
            day_kind="weekday",
            weekday="friday",
            weekday_when="next",
        ),
        user_text="yes",
    )
    assert ctx.draft.day == "2026-09-11"
    assert ctx.draft.day_clarify_options == []
    assert ctx.draft.book_confirmed is True


def test_apply_extract_clock_keeps_locked_doctor_and_last_day(db_session) -> None:
    from clinic_ai_booking.chat.context import ChatContext, ChatMemory
    from clinic_ai_booking.chat.extract import TurnExtract, apply_extract

    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 9, 10))
    ctx.memory = ChatMemory(
        last_professional_slug="senior-1", last_day="2026-09-11"
    )
    ctx.draft.service_code = "A"
    ctx.draft.professional_slug = "senior-1"
    ctx.draft.professional_confirmed = True
    ctx.draft.professional_candidates = ["senior-1"]
    ctx.draft.day = "2026-09-11"
    apply_extract(
        ctx,
        TurnExtract(in_scope=True, professional="Chen"),
        user_text="Well, 13:00",
    )
    assert ctx.draft.professional_slug == "senior-1"
    assert ctx.draft.professional_confirmed is True
    assert ctx.draft.day == "2026-09-11"
    assert ctx.draft.starts_at == "13:00"


def test_apply_extract_same_doctor_restores_last_day(db_session) -> None:
    from clinic_ai_booking.chat.context import ChatContext, ChatMemory
    from clinic_ai_booking.chat.extract import TurnExtract, apply_extract

    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 9, 10))
    ctx.memory = ChatMemory(
        last_professional_slug="senior-1", last_day="2026-09-11"
    )
    ctx.draft.service_code = "A"
    apply_extract(
        ctx,
        TurnExtract(in_scope=True),
        user_text="Same doctor",
    )
    assert ctx.draft.professional_slug == "senior-1"
    assert ctx.draft.professional_confirmed is True
    assert ctx.draft.day == "2026-09-11"


def test_unknown_service_message_lists_catalog(db_session) -> None:
    from clinic_ai_booking.chat.resolve import unknown_service_message

    text = unknown_service_message(db_session, "F")
    assert "do not offer service F" in text
    assert "• A (" in text
    assert "Which service" in text


def test_clamp_draft_drops_invalid_service(db_session) -> None:
    from clinic_ai_booking.chat.context import ChatContext, clamp_draft_to_catalog

    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 8, 31))
    ctx.draft.service_code = "F"
    ctx.draft.professional_slug = "not-a-doctor"
    clamp_draft_to_catalog(ctx)
    assert ctx.draft.service_code is None
    assert ctx.draft.professional_slug is None
