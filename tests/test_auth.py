"""Auth: session login, account-on-book, visitor blocked from cancel/reschedule."""

from datetime import date, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from clinic_ai_booking.auth import (
    LOGIN_REQUIRED_MESSAGE,
    AuthError,
    cancel_for_user,
    login_with_name_email,
    reschedule_for_user,
)
from clinic_ai_booking.booking import book_appointment
from clinic_ai_booking.hours import clinic_datetime
from clinic_ai_booking.main import app, get_db, set_engine
from clinic_ai_booking.models import User

MONDAY = date(2026, 8, 31)
NAME = "Pat Lee"
EMAIL = "pat@example.com"


@pytest.fixture
def api_client(db_engine: Engine, db_session: Session) -> TestClient:
    """HTTP client with the test DB engine and per-request sessions."""
    set_engine(db_engine)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as client:
        set_engine(db_engine)
        from clinic_ai_booking.notify import reset_ports_to_fakes

        reset_ports_to_fakes()
        yield client
    app.dependency_overrides.clear()
    set_engine(None)


def test_login_with_name_email_creates_session(api_client: TestClient) -> None:
    me = api_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json() == {"authenticated": False, "user": None}

    login = api_client.post(
        "/auth/login.json",
        json={"name": NAME, "email": EMAIL},
    )
    assert login.status_code == 200
    body = login.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == EMAIL
    assert body["user"]["name"] == NAME

    again = api_client.get("/auth/me")
    assert again.status_code == 200
    assert again.json()["authenticated"] is True
    assert again.json()["user"]["email"] == EMAIL

    logout = api_client.post("/auth/logout.json")
    assert logout.status_code == 200
    assert api_client.get("/auth/me").json()["authenticated"] is False


def test_first_successful_book_creates_user_account(db_session: Session) -> None:
    assert db_session.scalar(select(func.count()).select_from(User)) == 0
    booking = book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=clinic_datetime(MONDAY, time(9, 0)),
        patient_name=NAME,
        patient_email=EMAIL,
    )
    db_session.flush()
    user = db_session.scalar(select(User).where(User.email == EMAIL))
    assert user is not None
    assert user.name == NAME
    assert booking.user_id == user.id


def test_visitor_cannot_cancel_or_reschedule(db_session: Session) -> None:
    booking = book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=clinic_datetime(MONDAY, time(9, 0)),
        patient_name=NAME,
        patient_email=EMAIL,
    )
    db_session.flush()
    with pytest.raises(AuthError, match="log in"):
        cancel_for_user(db_session, booking.id, None)
    with pytest.raises(AuthError, match="log in"):
        reschedule_for_user(
            db_session,
            booking.id,
            clinic_datetime(MONDAY, time(11, 0)),
            None,
        )


def test_visitor_api_cancel_and_reschedule_tell_them_to_log_in(
    api_client: TestClient,
    db_session: Session,
) -> None:
    booking = book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=clinic_datetime(MONDAY, time(9, 0)),
        patient_name=NAME,
        patient_email=EMAIL,
    )
    db_session.flush()

    cancel = api_client.post(f"/api/bookings/{booking.id}/cancel")
    assert cancel.status_code == 401
    assert cancel.json()["detail"] == LOGIN_REQUIRED_MESSAGE

    reschedule = api_client.post(
        f"/api/bookings/{booking.id}/reschedule",
        json={"starts_at": clinic_datetime(MONDAY, time(11, 0)).isoformat()},
    )
    assert reschedule.status_code == 401
    assert reschedule.json()["detail"] == LOGIN_REQUIRED_MESSAGE


def test_logged_in_user_can_cancel_and_reschedule(
    api_client: TestClient,
    db_session: Session,
) -> None:
    booking = book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=clinic_datetime(MONDAY, time(9, 0)),
        patient_name=NAME,
        patient_email=EMAIL,
    )
    db_session.flush()
    original_id = booking.id

    login = api_client.post(
        "/auth/login.json",
        json={"name": NAME, "email": EMAIL},
    )
    assert login.status_code == 200

    moved = api_client.post(
        f"/api/bookings/{original_id}/reschedule",
        json={"starts_at": clinic_datetime(MONDAY, time(11, 0)).isoformat()},
    )
    assert moved.status_code == 200
    new_id = moved.json()["id"]
    assert new_id != original_id

    cancelled = api_client.post(f"/api/bookings/{new_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_login_with_name_email_domain_creates_account(db_session: Session) -> None:
    user = login_with_name_email(db_session, NAME, EMAIL)
    db_session.flush()
    assert user.id is not None
    assert db_session.get(User, user.id) is not None
