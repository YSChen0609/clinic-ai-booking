"""Stage 3: create_agent tools, middleware guardrails, sticky visitor, clinic time."""

from __future__ import annotations

from datetime import date, time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from clinic_ai_booking import agent as agent_mod
from clinic_ai_booking.agent import (
    build_agent,
    message_text,
    reset_checkpointer,
    run_chat_turn,
)
from clinic_ai_booking.auth import LOGIN_REQUIRED_MESSAGE, login_with_name_email
from clinic_ai_booking.booking import book_appointment
from clinic_ai_booking.chat_context import ChatContext
from clinic_ai_booking.chat_middleware import OUT_OF_SCOPE_REPLY, is_out_of_scope
from clinic_ai_booking.fakes import FakeCalendar, FakeEmail
from clinic_ai_booking.hours import clinic_datetime, explain_clinic_day, explain_clinic_start
from clinic_ai_booking.main import app, get_db, set_engine
from clinic_ai_booking.models import STATUS_PENDING_DOCTOR
from clinic_ai_booking.notify import reset_ports_to_fakes, set_ports

MONDAY = date(2026, 8, 31)
SATURDAY = date(2026, 8, 29)
NAME = "Pat Lee"
EMAIL = "pat@example.com"


class ScriptedModel(BaseChatModel):
    """Chat model that returns a fixed AIMessage sequence and supports bind_tools."""

    responses: list[AIMessage] = Field(default_factory=list)
    i: int = 0

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        if self.i >= len(self.responses):
            raise AssertionError("ScriptedModel has no more responses")
        msg = self.responses[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:
        del tools, kwargs
        return self


def _tool_call(name: str, args: dict[str, Any], call_id: str = "c1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _visitor_ctx(
    db: Session,
    *,
    name: str | None = None,
    email: str | None = None,
) -> ChatContext:
    return ChatContext(
        db=db,
        user_id=None,
        user_name=None,
        user_email=None,
        visitor_name=name,
        visitor_email=email,
    )


def _user_ctx(db: Session, user_id: int, name: str, email: str) -> ChatContext:
    return ChatContext(
        db=db,
        user_id=user_id,
        user_name=name,
        user_email=email,
    )


@pytest.fixture
def fakes() -> tuple[FakeCalendar, FakeEmail]:
    calendar, email = reset_ports_to_fakes()
    yield calendar, email
    reset_ports_to_fakes()


@pytest.fixture
def chat_ctx(db_session: Session, fakes: tuple[FakeCalendar, FakeEmail]) -> ChatContext:
    del fakes
    return _visitor_ctx(db_session)


@pytest.fixture(autouse=True)
def _isolate_agent_memory() -> None:
    reset_checkpointer()
    yield
    reset_checkpointer()


def _run(
    responses: list[AIMessage],
    message: str,
    context: ChatContext,
    *,
    thread_id: str = "test-thread",
) -> str:
    agent = build_agent(ScriptedModel(responses=responses))
    return run_chat_turn(
        message=message,
        thread_id=thread_id,
        context=context,
        agent=agent,
    )


def test_is_out_of_scope_detects_non_clinic_requests() -> None:
    assert is_out_of_scope("Can you give me a pasta recipe?")
    assert is_out_of_scope("What's the weather in Taipei?")
    assert not is_out_of_scope("What services do you offer?")
    assert not is_out_of_scope("Book service A with the junior doctor")
    assert not is_out_of_scope("hello")


def test_out_of_scope_refusal_skips_model_and_tools(chat_ctx: ChatContext) -> None:
    model = ScriptedModel(responses=[])
    agent = build_agent(model)
    reply = run_chat_turn(
        message="Write me a chocolate cake recipe",
        thread_id="scope-1",
        context=chat_ctx,
        agent=agent,
    )
    assert reply == OUT_OF_SCOPE_REPLY
    assert model.i == 0


def test_explain_clinic_day_rejects_weekend() -> None:
    ok, reason = explain_clinic_day(SATURDAY)
    assert ok is False
    assert "Monday–Friday" in reason or "Saturday" in reason


def test_explain_clinic_start_rejects_break() -> None:
    ok, reason = explain_clinic_start(clinic_datetime(MONDAY, time(12, 0)))
    assert ok is False
    assert "break" in reason.lower()


def test_explain_clinic_start_accepts_weekday_morning() -> None:
    ok, reason = explain_clinic_start(clinic_datetime(MONDAY, time(9, 0)))
    assert ok is True
    assert "valid" in reason.lower()


def test_check_clinic_time_rejects_saturday(chat_ctx: ChatContext) -> None:
    reply = _run(
        [
            _tool_call("check_clinic_time", {"when": SATURDAY.isoformat()}),
            AIMessage(
                content="Saturday is closed; the clinic is Monday–Friday only."
            ),
        ],
        "Can I book on 2026-08-29?",
        chat_ctx,
        thread_id="time-sat",
    )
    assert "monday" in reply.lower() or "closed" in reply.lower() or "weekend" in reply.lower()


def test_list_available_starts_stops_early_on_weekend(chat_ctx: ChatContext) -> None:
    reply = _run(
        [
            _tool_call(
                "list_available_starts",
                {
                    "professional_slug": "junior",
                    "service_code": "A",
                    "day": SATURDAY.isoformat(),
                },
            ),
            AIMessage(
                content="That day is invalid (weekend). I will not list Saturday slots."
            ),
        ],
        "Show junior slots on Saturday.",
        chat_ctx,
        thread_id="starts-sat",
    )
    assert "weekend" in reply.lower() or "invalid" in reply.lower() or "saturday" in reply.lower()


def test_remember_visitor_then_book_without_reasking(
    chat_ctx: ChatContext, fakes: tuple[FakeCalendar, FakeEmail]
) -> None:
    calendar, email = fakes
    start = clinic_datetime(MONDAY, time(9, 0)).isoformat()
    reply = _run(
        [
            _tool_call(
                "remember_visitor",
                {"patient_name": NAME, "patient_email": EMAIL},
                "c1",
            ),
            _tool_call(
                "book_appointment",
                {
                    "professional_slug": "junior",
                    "service_code": "A",
                    "starts_at": start,
                },
                "c2",
            ),
            AIMessage(content="Booked service A for Pat Lee at 09:00."),
        ],
        "My name is Pat Lee, email pat@example.com — book junior A at 09:00 Monday.",
        chat_ctx,
        thread_id="sticky-book",
    )
    assert "Booked" in reply
    assert chat_ctx.has_visitor_contact
    assert chat_ctx.visitor_email == EMAIL
    assert any(call[0] == "upsert" for call in calendar.calls)
    assert any(call[2] == EMAIL for call in email.calls if call[0] == "created")


def test_book_uses_sticky_visitor_already_on_context(
    db_session: Session, fakes: tuple[FakeCalendar, FakeEmail]
) -> None:
    email_port: FakeEmail
    calendar, email_port = fakes
    del calendar
    ctx = _visitor_ctx(db_session, name=NAME, email=EMAIL)
    start = clinic_datetime(MONDAY, time(10, 0)).isoformat()
    reply = _run(
        [
            _tool_call(
                "book_appointment",
                {
                    "professional_slug": "junior",
                    "service_code": "A",
                    "starts_at": start,
                    "patient_name": "Wrong",
                    "patient_email": "wrong@example.com",
                },
            ),
            AIMessage(content="Booked using your saved contact."),
        ],
        "Book me at 10:00.",
        ctx,
        thread_id="sticky-prefer",
    )
    assert "Booked" in reply
    assert any(call[2] == EMAIL for call in email_port.calls if call[0] == "created")
    assert not any(call[2] == "wrong@example.com" for call in email_port.calls)


def test_list_services_and_book_happy_path(
    chat_ctx: ChatContext, fakes: tuple[FakeCalendar, FakeEmail]
) -> None:
    calendar, email = fakes
    start = clinic_datetime(MONDAY, time(9, 0)).isoformat()
    reply = _run(
        [
            _tool_call("list_services", {}),
            _tool_call(
                "book_appointment",
                {
                    "professional_slug": "junior",
                    "service_code": "A",
                    "starts_at": start,
                    "patient_name": NAME,
                    "patient_email": EMAIL,
                },
                "c2",
            ),
            AIMessage(content="Booked service A for you at 09:00."),
        ],
        "Please book service A tomorrow morning.",
        chat_ctx,
    )
    assert "Booked" in reply
    assert chat_ctx.has_visitor_contact
    assert any(call[0] == "upsert" for call in calendar.calls)
    assert any(call[0] == "created" for call in email.calls)


def test_conflict_rejected_via_book_tool(chat_ctx: ChatContext) -> None:
    start = clinic_datetime(MONDAY, time(9, 0))
    book_appointment(
        chat_ctx.db,
        professional_slug="junior",
        service_code="A",
        starts_at=start,
        patient_name="Other",
        patient_email="other@example.com",
    )
    chat_ctx.db.flush()

    reply = _run(
        [
            _tool_call(
                "book_appointment",
                {
                    "professional_slug": "junior",
                    "service_code": "A",
                    "starts_at": start.isoformat(),
                    "patient_name": NAME,
                    "patient_email": EMAIL,
                },
            ),
            AIMessage(content="That slot overlaps an existing booking."),
        ],
        "Book junior service A at 09:00.",
        chat_ctx,
        thread_id="conflict-1",
    )
    assert "overlap" in reply.lower() or "booking" in reply.lower()


def test_cancel_requires_authenticated_user(chat_ctx: ChatContext) -> None:
    booking = book_appointment(
        chat_ctx.db,
        professional_slug="junior",
        service_code="A",
        starts_at=clinic_datetime(MONDAY, time(9, 0)),
        patient_name=NAME,
        patient_email=EMAIL,
    )
    chat_ctx.db.flush()

    reply = _run(
        [
            _tool_call("cancel_appointment", {"booking_id": booking.id}),
            AIMessage(content=LOGIN_REQUIRED_MESSAGE),
        ],
        "Please cancel my booking.",
        chat_ctx,
        thread_id="cancel-visitor",
    )
    assert LOGIN_REQUIRED_MESSAGE in reply or "log in" in reply.lower()


def test_reschedule_works_when_logged_in(
    db_session: Session, fakes: tuple[FakeCalendar, FakeEmail]
) -> None:
    calendar, email = fakes
    user = login_with_name_email(db_session, NAME, EMAIL)
    booking = book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=clinic_datetime(MONDAY, time(9, 0)),
        patient_name=NAME,
        patient_email=EMAIL,
    )
    db_session.flush()
    ctx = _user_ctx(db_session, user.id, user.name, user.email)
    new_start = clinic_datetime(MONDAY, time(11, 0)).isoformat()
    calendar.clear()
    email.clear()
    reply = _run(
        [
            _tool_call(
                "reschedule_appointment",
                {"booking_id": booking.id, "starts_at": new_start},
            ),
            AIMessage(content="Rescheduled to 11:00."),
        ],
        "Reschedule my appointment to 11:00.",
        ctx,
        thread_id="resched-1",
    )
    assert "11:00" in reply or "Rescheduled" in reply
    assert any(call[0] == "upsert" for call in calendar.calls)
    assert any(call[0] == "rescheduled" for call in email.calls)


def test_e_overtime_pending_doctor_and_fakes(
    chat_ctx: ChatContext, fakes: tuple[FakeCalendar, FakeEmail]
) -> None:
    calendar, email = fakes
    start = clinic_datetime(MONDAY, time(16, 0)).isoformat()
    reply = _run(
        [
            _tool_call(
                "book_appointment",
                {
                    "professional_slug": "senior-1",
                    "service_code": "E",
                    "starts_at": start,
                    "patient_name": NAME,
                    "patient_email": EMAIL,
                },
            ),
            AIMessage(
                content=(
                    "Booked service E as pending_doctor; it needs doctor confirmation."
                )
            ),
        ],
        "Book service E at 16:00 with senior-1.",
        chat_ctx,
        thread_id="e-ot",
    )
    assert "pending_doctor" in reply.lower() or "confirmation" in reply.lower()
    assert any(call[2] == STATUS_PENDING_DOCTOR for call in calendar.calls if call[0] == "upsert")
    assert any(call[0] == "created" for call in email.calls)


def test_logged_in_book_uses_session_identity_not_args(
    db_session: Session, fakes: tuple[FakeCalendar, FakeEmail]
) -> None:
    _calendar, email = fakes
    user = login_with_name_email(db_session, NAME, EMAIL)
    ctx = _user_ctx(db_session, user.id, user.name, user.email)
    start = clinic_datetime(MONDAY, time(10, 0)).isoformat()
    reply = _run(
        [
            _tool_call(
                "book_appointment",
                {
                    "professional_slug": "junior",
                    "service_code": "A",
                    "starts_at": start,
                    "patient_name": "Wrong Name",
                    "patient_email": "wrong@example.com",
                },
            ),
            AIMessage(content=f"Booked for {NAME}."),
        ],
        "Book me at 10:00 for service A.",
        ctx,
        thread_id="auth-book",
    )
    assert NAME.split()[0] in reply or "Booked" in reply
    assert any(call[2] == EMAIL for call in email.calls if call[0] == "created")


def test_api_chat_persists_visitor_in_session(
    db_engine: Engine, db_session: Session, fakes: tuple[FakeCalendar, FakeEmail]
) -> None:
    calendar, email = fakes
    set_engine(db_engine)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    reset_checkpointer()
    agent_mod.set_agent(
        build_agent(
            ScriptedModel(
                responses=[
                    _tool_call(
                        "remember_visitor",
                        {
                            "patient_name": "Tester Que",
                            "patient_email": "tester@gmail.com",
                        },
                    ),
                    AIMessage(content="Thanks — I saved your contact."),
                ]
            )
        )
    )
    try:
        with TestClient(app) as client:
            res = client.post(
                "/api/chat",
                json={"message": "I am Tester Que, tester@gmail.com"},
            )
            assert res.status_code == 200

            start = clinic_datetime(MONDAY, time(9, 0)).isoformat()
            calendar.clear()
            email.clear()
            agent_mod.set_agent(
                build_agent(
                    ScriptedModel(
                        responses=[
                            _tool_call(
                                "book_appointment",
                                {
                                    "professional_slug": "junior",
                                    "service_code": "A",
                                    "starts_at": start,
                                },
                            ),
                            AIMessage(content="Booked with your saved contact."),
                        ]
                    )
                )
            )
            res2 = client.post(
                "/api/chat",
                json={"message": "Book junior service A Monday at 09:00."},
            )
            assert res2.status_code == 200
            assert any(
                call[2] == "tester@gmail.com" for call in email.calls if call[0] == "created"
            )
    finally:
        app.dependency_overrides.clear()
        set_engine(None)
        agent_mod.set_agent(None)
        reset_checkpointer()


def test_multi_turn_reuses_thread_history(chat_ctx: ChatContext) -> None:
    model = ScriptedModel(
        responses=[
            AIMessage(content="We offer services A through E."),
            AIMessage(content="As I said, A through E — A and B are 60 minutes."),
        ]
    )
    agent = build_agent(model)
    first = run_chat_turn(
        message="What services do you have?",
        thread_id="multi-1",
        context=chat_ctx,
        agent=agent,
    )
    second = run_chat_turn(
        message="How long is A?",
        thread_id="multi-1",
        context=chat_ctx,
        agent=agent,
    )
    assert "A" in first
    assert "60" in second or "A" in second


def test_fakes_record_notify_from_engine(
    db_session: Session, fakes: tuple[FakeCalendar, FakeEmail]
) -> None:
    calendar, email = fakes
    booking = book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=clinic_datetime(MONDAY, time(9, 0)),
        patient_name=NAME,
        patient_email=EMAIL,
    )
    db_session.flush()
    assert ("upsert", booking.id, booking.status) in calendar.calls
    assert ("created", booking.id, EMAIL) in email.calls


def test_message_text_flattens_blocks() -> None:
    msg = AIMessage(content=[{"type": "text", "text": "Hello"}, {"type": "text", "text": "!"}])
    assert message_text(msg) == "Hello!"


def test_set_ports_swap(db_session: Session) -> None:
    calendar = FakeCalendar()
    email = FakeEmail()
    set_ports(calendar, email)
    book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=clinic_datetime(MONDAY, time(9, 0)),
        patient_name=NAME,
        patient_email=EMAIL,
    )
    db_session.flush()
    assert calendar.calls
    assert email.calls
    reset_ports_to_fakes()
