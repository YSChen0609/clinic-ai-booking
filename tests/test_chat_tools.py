"""Tests for chat tools."""

from __future__ import annotations

import json
from datetime import date, time
from unittest.mock import MagicMock

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from sqlalchemy.orm import Session

from clinic_ai_booking.auth import get_or_create_user
from clinic_ai_booking.domain.booking import book_appointment as engine_book
from clinic_ai_booking.chat.context import ChatContext, load_context
from clinic_ai_booking.chat.tools import (
    book_appointment,
    cancel_appointment,
    explain_clinic_day,
    explain_clinic_start,
    get_session_info,
    reschedule_appointment,
    update_context,
)
from clinic_ai_booking.domain.hours import clinic_datetime
from clinic_ai_booking.domain.models import STATUS_CANCELLED, STATUS_CONFIRMED

MONDAY = date(2026, 8, 31)
SATURDAY = date(2026, 8, 29)


def _runtime(ctx: ChatContext) -> ToolRuntime[ChatContext]:
    return ToolRuntime(
        state={},
        context=ctx,
        config={},
        stream_writer=lambda _: None,
        tool_call_id="test-call",
        store=None,
    )


def _as_tool_message(content: str) -> ToolMessage:
    """Same wrap create_agent applies to tool return strings."""
    return ToolMessage(content=content, tool_call_id="test-call")


def test_load_context_sets_patient_from_logged_in_user() -> None:
    user = MagicMock()
    user.id = 1
    user.name = "Logged In"
    user.email = "logged@example.com"
    ctx = load_context({}, MagicMock(), user=user)
    assert ctx.patient_name == "Logged In"
    assert ctx.patient_email == "logged@example.com"
    assert ctx.can_book_now is True


def test_load_context_purges_legacy_sticky_visitor_keys() -> None:
    session = {
        "visitor_name": "Sticky",
        "visitor_email": "sticky@example.com",
        "visitor_contact_confirmed": True,
    }
    ctx = load_context(session, MagicMock(), user=None)
    assert ctx.patient_name is None
    assert ctx.patient_email is None
    assert ctx.can_book_now is False
    assert "visitor_name" not in session
    assert "visitor_email" not in session


def test_update_context_saves_patient_and_draft(db_session: Session) -> None:
    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 9, 2))
    result = json.loads(
        update_context.invoke(
            {
                "runtime": _runtime(ctx),
                "patient_name": " Jane Doe ",
                "patient_email": "jane@example.com",
                "service_code": "A",
                "day": "2026-09-05",
            }
        )
    )
    assert result["ok"] is True
    assert "patient_name" in result["updated"]
    assert "service_code" in result["updated"]
    assert result["can_book_now"] is True
    assert ctx.patient_name == "Jane Doe"
    assert ctx.draft.service_code == "A"


def test_update_context_rejects_unknown_professional_slug(db_session: Session) -> None:
    ctx = ChatContext(db=db_session, user_id=None, today=date(2026, 9, 2))
    result = json.loads(
        update_context.invoke(
            {
                "runtime": _runtime(ctx),
                "professional_slug": "senior",
                "service_code": "A",
            }
        )
    )
    assert result["ok"] is False
    assert "unknown professional_slug" in result["error"]
    assert "junior" in result["error"]
    assert ctx.draft.professional_slug is None


def test_update_context_logged_in_updates_draft_only() -> None:
    db = MagicMock()
    ctx = ChatContext(
        db=db,
        user_id=1,
        patient_name="Logged In",
        patient_email="logged@example.com",
        today=date(2026, 9, 2),
    )
    result = json.loads(
        update_context.invoke(
            {
                "runtime": _runtime(ctx),
                "day": "2026-09-05",
            }
        )
    )
    assert result["ok"] is True
    assert result["updated"] == ["day"]
    assert result["patient_name"] == "Logged In"
    assert result["can_book_now"] is True


def test_list_available_starts_returns_compact_slots(db_session: Session) -> None:
    from clinic_ai_booking.chat.tools import list_available_starts

    ctx = ChatContext(db=db_session, user_id=None)
    payload = json.loads(
        list_available_starts.invoke(
            {
                "runtime": _runtime(ctx),
                "professional_slug": "junior",
                "service_code": "A",
                "day": MONDAY.isoformat(),
            }
        )
    )
    assert payload["ok"] is True
    assert payload["day"] == MONDAY.isoformat()
    assert "09:00" in payload["times"]
    assert payload["starts_at"][0].endswith("+08:00")


def test_list_available_starts_rejects_invented_slug(db_session: Session) -> None:
    from clinic_ai_booking.chat.tools import list_available_starts

    ctx = ChatContext(db=db_session, user_id=None)
    payload = json.loads(
        list_available_starts.invoke(
            {
                "runtime": _runtime(ctx),
                "professional_slug": "senior",
                "service_code": "A",
                "day": MONDAY.isoformat(),
            }
        )
    )
    assert payload["ok"] is False
    assert "unknown professional_slug" in payload["error"]
    assert "list_professionals" in payload["error"]


def test_check_start_rejects_unknown_service(db_session: Session) -> None:
    from clinic_ai_booking.chat.tools import check_start

    ctx = ChatContext(db=db_session, user_id=None)
    start = clinic_datetime(MONDAY, time(9, 0))
    payload = json.loads(
        check_start.invoke(
            {
                "runtime": _runtime(ctx),
                "professional_slug": "junior",
                "service_code": "Z",
                "starts_at": start.isoformat(),
            }
        )
    )
    assert payload["ok"] is False
    assert "unknown service_code" in payload["error"]


def test_reject_unknown_catalog_blocks_invented_slug(db_session: Session) -> None:
    from clinic_ai_booking.chat.catalog import reject_unknown_catalog

    err = reject_unknown_catalog(
        db_session, professional_slug="senior", service_code="A"
    )
    assert err is not None
    assert "unknown professional_slug" in err


def test_get_session_info_returns_draft() -> None:
    db = MagicMock()
    ctx = ChatContext(
        db=db,
        user_id=None,
        patient_name="Jane",
        patient_email="jane@example.com",
        contact_confirmed=True,
        today=date(2026, 9, 2),
    )
    ctx.draft.service_code = "B"
    result = json.loads(get_session_info.invoke({"runtime": _runtime(ctx)}))
    assert result["today"] == "2026-09-02"
    assert result["can_book_now"] is True
    assert result["draft"]["service_code"] == "B"


def test_explain_clinic_day_rejects_weekend() -> None:
    payload = json.loads(explain_clinic_day.invoke({"day": SATURDAY.isoformat()}))
    assert payload["ok"] is True
    assert payload["valid"] is False
    assert payload["day"] == SATURDAY.isoformat()
    assert "Monday–Friday" in payload["reason"] or "Saturday" in payload["reason"]


def test_explain_clinic_day_accepts_weekday() -> None:
    payload = json.loads(explain_clinic_day.invoke({"day": MONDAY.isoformat()}))
    assert payload["ok"] is True
    assert payload["valid"] is True
    assert "09:00" in payload["reason"]


def test_explain_clinic_start_rejects_break() -> None:
    start = clinic_datetime(MONDAY, time(12, 30))
    payload = json.loads(explain_clinic_start.invoke({"starts_at": start.isoformat()}))
    assert payload["ok"] is True
    assert payload["valid"] is False
    assert "break" in payload["reason"].lower()


def test_explain_clinic_start_accepts_open_slot() -> None:
    start = clinic_datetime(MONDAY, time(9, 0))
    payload = json.loads(explain_clinic_start.invoke({"starts_at": start.isoformat()}))
    assert payload["ok"] is True
    assert payload["valid"] is True


def test_book_appointment_writes_via_engine(db_session: Session) -> None:
    start = clinic_datetime(MONDAY, time(9, 0))
    ctx = ChatContext(
        db=db_session,
        user_id=None,
        patient_name="Pat Lee",
        patient_email="pat-chat@example.com",
        contact_confirmed=True,
    )
    ctx.draft.service_code = "A"
    ctx.draft.professional_slug = "junior"
    ctx.draft.starts_at = start.isoformat()

    raw = book_appointment.invoke({"runtime": _runtime(ctx)})
    payload = json.loads(_as_tool_message(raw).content)

    assert payload["ok"] is True
    assert payload["status"] == STATUS_CONFIRMED
    assert payload["patient_email"] == "pat-chat@example.com"
    assert payload["starts_at"] == start.isoformat()
    assert payload["ends_at"] == clinic_datetime(MONDAY, time(10, 0)).isoformat()
    assert ctx.draft.starts_at is None


def test_book_appointment_returns_engine_error(db_session: Session) -> None:
    start = clinic_datetime(MONDAY, time(9, 0))
    engine_book(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=start,
        patient_name="First",
        patient_email="first@example.com",
    )
    ctx = ChatContext(
        db=db_session,
        user_id=None,
        patient_name="Other",
        patient_email="other@example.com",
        contact_confirmed=True,
    )
    ctx.draft.service_code = "A"
    ctx.draft.professional_slug = "junior"
    ctx.draft.starts_at = start.isoformat()

    payload = json.loads(book_appointment.invoke({"runtime": _runtime(ctx)}))
    assert payload["ok"] is False
    assert "error" in payload


def test_cancel_and_reschedule_via_engine(db_session: Session) -> None:
    user = get_or_create_user(db_session, "Pat Lee", "pat-resched@example.com")
    start = clinic_datetime(MONDAY, time(9, 0))
    booking = engine_book(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=start,
        patient_name=user.name,
        patient_email=user.email,
    )
    ctx = ChatContext(
        db=db_session,
        user_id=user.id,
        patient_name=user.name,
        patient_email=user.email,
    )

    new_start = clinic_datetime(MONDAY, time(10, 0))
    resched = json.loads(
        reschedule_appointment.invoke(
            {
                "runtime": _runtime(ctx),
                "booking_id": booking.id,
                "starts_at": new_start.isoformat(),
            }
        )
    )
    assert resched["ok"] is True
    assert resched["starts_at"] == new_start.isoformat()
    assert resched["status"] == STATUS_CONFIRMED

    cancelled = json.loads(
        cancel_appointment.invoke(
            {"runtime": _runtime(ctx), "booking_id": resched["id"]}
        )
    )
    assert cancelled["ok"] is True
    assert cancelled["status"] == STATUS_CANCELLED
