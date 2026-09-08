"""Adapter unit tests with mocked Google / Microsoft Graph HTTP."""

from __future__ import annotations

from datetime import date, time
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from clinic_ai_booking.adapters.copy import google_event_id
from clinic_ai_booking.adapters.google import GoogleAdapterError, GoogleCalendar, GoogleEmail
from clinic_ai_booking.adapters.outlook import (
    OutlookAdapterError,
    OutlookCalendar,
    OutlookEmail,
)
from clinic_ai_booking.adapters.tokens import GoogleTokenSource, MicrosoftTokenSource
from clinic_ai_booking.booking import book_appointment
from clinic_ai_booking.fakes import FakeCalendar, FakeEmail
from clinic_ai_booking.hours import clinic_datetime
from clinic_ai_booking.models import STATUS_PENDING_DOCTOR
from clinic_ai_booking.notify import (
    notify_booking_created,
    reset_ports_to_fakes,
    set_ports,
)

MONDAY = date(2026, 8, 31)

def test_google_calendar_upsert_puts_event_with_attendee() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "g-token", "expires_in": 3600})
        calls.append((request.method, str(request.url)))
        if request.method == "PUT":
            body = request.read()
            assert b"patient@example.com" in body
            assert b"Dr." in body or b"professional" in body.lower() or b"Clinic" in body
            return httpx.Response(200, json={"id": "cab000000000001"})
        return httpx.Response(500, text="no")

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    tokens = GoogleTokenSource("id", "secret", "refresh", http)
    cal = GoogleCalendar(tokens=tokens, calendar_id="primary", http=http)
    booking = _stub_booking(1, status="confirmed")
    cal.upsert_booking(booking)
    assert any(m == "PUT" for m, _ in calls)


def test_google_calendar_upsert_posts_when_put_404() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "g-token", "expires_in": 3600})
        methods.append(request.method)
        if request.method == "PUT":
            return httpx.Response(404, json={"error": "notFound"})
        if request.method == "POST":
            return httpx.Response(200, json={"id": google_event_id(7)})
        return httpx.Response(500)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cal = GoogleCalendar(
        tokens=GoogleTokenSource("id", "secret", "refresh", http),
        calendar_id="primary",
        http=http,
    )
    cal.upsert_booking(_stub_booking(7, status="confirmed"))
    assert methods == ["PUT", "POST"]


def test_google_calendar_skips_pending_doctor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "g-token", "expires_in": 3600})
        raise AssertionError(f"unexpected calendar call {request.method} {request.url}")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cal = GoogleCalendar(
        tokens=GoogleTokenSource("id", "secret", "refresh", http),
        calendar_id="primary",
        http=http,
    )
    cal.upsert_booking(_stub_booking(3, status=STATUS_PENDING_DOCTOR))


def test_google_calendar_upsert_raises_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "g-token", "expires_in": 3600})
        return httpx.Response(403, text="forbidden")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cal = GoogleCalendar(
        tokens=GoogleTokenSource("id", "secret", "refresh", http),
        calendar_id="primary",
        http=http,
    )
    with pytest.raises(GoogleAdapterError):
        cal.upsert_booking(_stub_booking(1, status="confirmed"))


def test_google_email_send_created() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "g-token", "expires_in": 3600})
        seen["url"] = str(request.url)
        seen["json"] = request.read()
        return httpx.Response(200, json={"id": "msg-1"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    mail = GoogleEmail(
        tokens=GoogleTokenSource("id", "secret", "refresh", http),
        sender="clinic@example.com",
        http=http,
    )
    mail.send_booking_created(_stub_booking(1, status="confirmed"))
    assert "gmail.googleapis.com" in seen["url"]
    assert b"raw" in seen["json"]


def test_outlook_calendar_upsert_posts_when_missing() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2/v2.0/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "ms-token", "expires_in": 3600})
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"value": []})
        if request.method == "POST":
            body = request.read()
            assert b"patient@example.com" in body
            assert b"booking:9" in body
            return httpx.Response(201, json={"id": "evt-9"})
        return httpx.Response(500)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cal = OutlookCalendar(
        tokens=MicrosoftTokenSource("tenant", "id", "secret", http),
        user_upn="clinic@contoso.com",
        http=http,
    )
    cal.upsert_booking(_stub_booking(9, status="confirmed"))
    assert methods == ["GET", "POST"]


def test_outlook_calendar_remove_deletes_when_found() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2/v2.0/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "ms-token", "expires_in": 3600})
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"value": [{"id": "evt-2", "subject": "x (booking:2)"}]})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(500)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cal = OutlookCalendar(
        tokens=MicrosoftTokenSource("tenant", "id", "secret", http),
        user_upn="clinic@contoso.com",
        http=http,
    )
    cal.remove_booking(_stub_booking(2, status="cancelled"))
    assert methods == ["GET", "DELETE"]


def test_outlook_email_send_mail() -> None:
    seen: list[str] = []
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2/v2.0/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "ms-token", "expires_in": 3600})
        seen.append(str(request.url))
        assert request.method == "POST"
        bodies.append(request.read())
        return httpx.Response(202)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    mail = OutlookEmail(
        tokens=MicrosoftTokenSource("tenant", "id", "secret", http),
        user_upn="clinic@contoso.com",
        http=http,
    )
    mail.send_booking_created(_stub_booking(1, status=STATUS_PENDING_DOCTOR))
    assert any("sendMail" in u for u in seen)
    assert bodies and b"Pending doctor approval" in bodies[0]


def test_outlook_calendar_raises_on_graph_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2/v2.0/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "ms-token", "expires_in": 3600})
        if request.method == "GET":
            return httpx.Response(200, json={"value": []})
        return httpx.Response(500, text="boom")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cal = OutlookCalendar(
        tokens=MicrosoftTokenSource("tenant", "id", "secret", http),
        user_upn="clinic@contoso.com",
        http=http,
    )
    with pytest.raises(OutlookAdapterError):
        cal.upsert_booking(_stub_booking(1, status="confirmed"))


def test_pending_doctor_skips_calendar_but_sends_email(db_session: Session) -> None:
    calendar, email = reset_ports_to_fakes()
    booking = book_appointment(
        db_session,
        professional_slug="senior-1",
        service_code="E",
        starts_at=clinic_datetime(MONDAY, time(16, 0)),
        patient_name="Pat Lee",
        patient_email="pat@example.com",
    )
    assert booking.status == STATUS_PENDING_DOCTOR
    assert calendar.calls == []
    assert email.calls == [("created", booking.id, "pat@example.com")]


def test_confirmed_book_calls_calendar_and_email(db_session: Session) -> None:
    calendar, email = reset_ports_to_fakes()
    booking = book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=clinic_datetime(MONDAY, time(9, 0)),
        patient_name="Pat Lee",
        patient_email="pat@example.com",
    )
    assert booking.status == "confirmed"
    assert calendar.calls == [("upsert", booking.id, "confirmed")]
    assert email.calls == [("created", booking.id, "pat@example.com")]


def test_notify_created_uses_ports_only() -> None:
    calendar = FakeCalendar()
    email = FakeEmail()
    set_ports(calendar, email)
    booking = _stub_booking(55, status="confirmed")
    notify_booking_created(booking)
    assert calendar.calls == [("upsert", 55, "confirmed")]
    assert email.calls == [("created", 55, "patient@example.com")]


def _stub_booking(booking_id: int, *, status: str):
    from clinic_ai_booking.models import Booking, Professional, Service

    pro = Professional(id=1, slug="junior", name="Dr. Alex Chen", is_senior=False)
    svc = Service(id=1, code="A", duration_minutes=60, seniors_only=False)
    booking = Booking(
        id=booking_id,
        professional_id=1,
        service_id=1,
        patient_name="Pat",
        patient_email="patient@example.com",
        starts_at=clinic_datetime(MONDAY, time(9, 0)),
        ends_at=clinic_datetime(MONDAY, time(10, 0)),
        status=status,
    )
    booking.professional = pro
    booking.service = svc
    return booking
