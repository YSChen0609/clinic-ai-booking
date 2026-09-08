"""Deterministic booking subgraph tests (no LLM)."""

from __future__ import annotations

from datetime import date, time

from sqlalchemy.orm import Session

from clinic_ai_booking.domain.booking import (
    book_appointment as engine_book,
    list_available_starts as engine_list_available_starts,
)
from clinic_ai_booking.chat.book_graph import (
    available_time,
    book_node,
    build_book_graph,
    client_id,
    doctor_service,
)
from clinic_ai_booking.chat.context import ChatContext
from clinic_ai_booking.chat.facts import empty_facts
from clinic_ai_booking.chat.resolve import resolve_professional_slug
from clinic_ai_booking.domain.hours import clinic_datetime, to_clinic

MONDAY = date(2026, 8, 31)


class _Runtime:
    def __init__(self, ctx: ChatContext) -> None:
        self.context = ctx


def _state(**kwargs):
    base = {
        "messages": [],
        "user_text": "",
        "reply": "",
        "in_scope": True,
        "facts": empty_facts(),
        "intent": "book",
    }
    base.update(kwargs)
    return base


def test_resolve_professional_by_name(db_session: Session) -> None:
    assert resolve_professional_slug(db_session, "Chen") == "junior"
    assert resolve_professional_slug(db_session, "Dr. Chen") == "junior"
    assert resolve_professional_slug(db_session, "senior-1") == "senior-1"


def test_doctor_service_asks_for_missing(db_session: Session) -> None:
    ctx = ChatContext(db=db_session, user_id=None, today=MONDAY)
    out = doctor_service(_state(), _Runtime(ctx))
    assert out["facts"]["status"] == "need_info"
    assert "service_code" in out["facts"]["missing"]


def test_doctor_service_rejects_junior_for_senior_service(db_session: Session) -> None:
    ctx = ChatContext(db=db_session, user_id=None, today=MONDAY)
    ctx.draft.service_code = "E"
    ctx.draft.professional_slug = "junior"
    out = doctor_service(_state(), _Runtime(ctx))
    assert out["facts"]["status"] == "need_info"
    assert out["facts"]["error"]


def test_doctor_service_blocks_same_service_early(db_session: Session) -> None:
    start = clinic_datetime(MONDAY, time(11, 0))
    booking = engine_book(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=start,
        patient_name="Same Pat",
        patient_email="same-pat@example.com",
    )
    db_session.commit()

    ctx = ChatContext(
        db=db_session,
        user_id=None,
        patient_name="Same Pat",
        patient_email="same-pat@example.com",
        contact_confirmed=True,
        today=MONDAY,
    )
    ctx.draft.service_code = "A"
    ctx.draft.professional_slug = "junior"
    out = doctor_service(_state(), _Runtime(ctx))
    assert out["facts"]["status"] == "book_failed"
    assert "same-pat@example.com" in out["facts"]["error"]
    assert f"#{booking.id}" in out["facts"]["error"]
    assert "active service A" in out["facts"]["error"]


def test_client_id_asks_for_name_and_email(db_session: Session) -> None:
    ctx = ChatContext(db=db_session, user_id=None, today=MONDAY)
    out = client_id(_state(), _Runtime(ctx))
    assert out["facts"]["status"] == "need_info"
    assert "patient_name" in out["facts"]["missing"]
    assert "patient_email" in out["facts"]["missing"]


def test_apply_extract_detects_chen_in_sentence(db_session: Session) -> None:
    from clinic_ai_booking.chat.extract import TurnExtract, apply_extract, sanitize_extract

    text = "Can I have a service A with Dr. Chen tmr morning?"
    extracted = sanitize_extract(
        TurnExtract(in_scope=True, day_kind="tomorrow", time_band="morning"),
        user_text=text,
        today=MONDAY,
    )
    ctx = ChatContext(db=db_session, user_id=None, today=MONDAY)
    apply_extract(ctx, extracted, user_text=text)
    assert ctx.draft.service_code == "A"
    assert ctx.draft.professional_slug == "junior"
    assert ctx.draft.day == "2026-09-01"
    assert ctx.draft.time_band == "morning"


def test_apply_extract_him_and_also_reuses_memory(db_session: Session) -> None:
    from clinic_ai_booking.chat.context import ChatMemory
    from clinic_ai_booking.chat.extract import TurnExtract, apply_extract, sanitize_extract

    tomorrow = "2026-09-01"
    text = "ok, can I also have a service C with him in the afternoon?"
    extracted = sanitize_extract(
        TurnExtract(in_scope=True, day_kind="none", time_band="afternoon"),
        user_text=text,
        today=MONDAY,
    )
    assert extracted.day is None
    ctx = ChatContext(
        db=db_session,
        user_id=None,
        today=MONDAY,
        memory=ChatMemory(last_professional_slug="junior", last_day=tomorrow),
    )
    apply_extract(ctx, extracted, user_text=text)
    assert ctx.draft.service_code == "C"
    assert ctx.draft.professional_slug == "junior"
    assert ctx.draft.day == tomorrow
    assert ctx.draft.time_band == "afternoon"


def test_available_time_offers_starts(db_session: Session) -> None:
    ctx = ChatContext(db=db_session, user_id=None, today=MONDAY)
    ctx.draft.service_code = "A"
    ctx.draft.professional_slug = "junior"
    ctx.draft.day = MONDAY.isoformat()
    out = available_time(_state(), _Runtime(ctx))
    assert out["facts"]["status"] == "offer_slots"
    assert "09:00" in out["facts"]["offered_times"]


def test_available_time_filters_morning_band(db_session: Session) -> None:
    ctx = ChatContext(db=db_session, user_id=None, today=MONDAY)
    ctx.draft.service_code = "A"
    ctx.draft.professional_slug = "junior"
    ctx.draft.day = MONDAY.isoformat()
    ctx.draft.time_band = "morning"
    out = available_time(_state(), _Runtime(ctx))
    assert out["facts"]["status"] == "offer_slots"
    assert out["facts"]["time_band"] == "morning"
    assert all(int(t.split(":")[0]) < 12 for t in out["facts"]["offered_times"])
    assert "Monday 2026-08-31" in out["facts"]["resolved_day"]


def test_available_time_morning_fallback_does_not_label_band(
    db_session: Session,
) -> None:
    """When morning is empty, offer other times without calling them morning."""
    # Fill morning capacity (service A spans multiple grid slots).
    for i in range(20):
        morning = [
            s
            for s in engine_list_available_starts(db_session, "junior", "A", MONDAY)
            if to_clinic(s).hour < 12
        ]
        if not morning:
            break
        engine_book(
            db_session,
            professional_slug="junior",
            service_code="A",
            starts_at=morning[0],
            patient_name=f"Fill {i}",
            patient_email=f"fill{i}@example.com",
        )

    ctx = ChatContext(db=db_session, user_id=None, today=MONDAY)
    ctx.draft.service_code = "A"
    ctx.draft.professional_slug = "junior"
    ctx.draft.day = MONDAY.isoformat()
    ctx.draft.time_band = "morning"
    out = available_time(_state(), _Runtime(ctx))
    assert out["facts"]["status"] == "offer_slots"
    assert out["facts"]["time_band"] == ""
    assert "No morning starts" in (out["facts"]["error"] or "")
    assert out["facts"]["offered_times"]
    assert all(int(t.split(":")[0]) >= 12 for t in out["facts"]["offered_times"])


def test_client_id_blocks_without_contact(db_session: Session) -> None:
    ctx = ChatContext(db=db_session, user_id=None, today=MONDAY)
    facts = empty_facts()
    facts["status"] = "slot_ok"
    out = client_id(_state(facts=facts), _Runtime(ctx))
    assert out["facts"]["status"] == "need_info"
    assert "patient_name" in out["facts"]["missing"]


def test_book_graph_end_to_end_writes(db_session: Session) -> None:
    start = clinic_datetime(MONDAY, time(9, 0))
    ctx = ChatContext(
        db=db_session,
        user_id=None,
        patient_name="Graph Pat",
        patient_email="graph-pat@example.com",
        contact_confirmed=True,
        today=MONDAY,
    )
    ctx.draft.service_code = "A"
    ctx.draft.professional_slug = "junior"
    ctx.draft.day = MONDAY.isoformat()
    ctx.draft.starts_at = start.isoformat()

    graph = build_book_graph().compile()
    result = graph.invoke(
        _state(),
        context=ctx,
    )
    assert result["facts"]["status"] == "booked"
    assert result["facts"]["booking_id"] is not None
    assert ctx.draft.service_code is None
    assert ctx.patient_email == "graph-pat@example.com"
    assert ctx.memory.last_professional_slug == "junior"
    assert ctx.memory.last_day == MONDAY.isoformat()


def test_book_node_fails_on_conflict(db_session: Session) -> None:
    start = clinic_datetime(MONDAY, time(9, 0))
    first = ChatContext(
        db=db_session,
        user_id=None,
        patient_name="First",
        patient_email="first-graph@example.com",
        contact_confirmed=True,
        today=MONDAY,
    )
    first.draft.service_code = "A"
    first.draft.professional_slug = "junior"
    first.draft.day = MONDAY.isoformat()
    first.draft.starts_at = start.isoformat()
    booked = book_node(_state(), _Runtime(first))
    assert booked["facts"]["status"] == "booked"
    db_session.commit()

    second = ChatContext(
        db=db_session,
        user_id=None,
        patient_name="Second",
        patient_email="second-graph@example.com",
        contact_confirmed=True,
        today=MONDAY,
    )
    second.draft.service_code = "A"
    second.draft.professional_slug = "junior"
    second.draft.day = MONDAY.isoformat()
    second.draft.starts_at = start.isoformat()
    conflict = book_node(_state(), _Runtime(second))
    assert conflict["facts"]["status"] == "book_failed"
