"""Public doctor busy calendar: filter by professional, no patient fields."""

from datetime import date, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from clinic_ai_booking.booking import (
    BookingError,
    book_appointment,
    list_busy_blocks_range,
)
from clinic_ai_booking.hours import clinic_datetime
from clinic_ai_booking.main import app, get_db, set_engine

MONDAY = date(2026, 8, 31)
WEEK_END = date(2026, 9, 6)


@pytest.fixture
def api_client(db_engine: Engine, db_session: Session):
    """HTTP client bound to the test DB engine."""
    set_engine(db_engine)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as client:
        # Lifespan may rebind the engine / ports; restore test DB + fakes.
        set_engine(db_engine)
        from clinic_ai_booking.notify import reset_ports_to_fakes

        reset_ports_to_fakes()
        yield client
    app.dependency_overrides.clear()
    set_engine(None)


def test_busy_api_filters_by_doctor_and_hides_patient_fields(
    api_client: TestClient, db_session: Session
) -> None:
    book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=clinic_datetime(MONDAY, time(9, 0)),
        patient_name="Secret Patient",
        patient_email="secret@example.com",
    )
    db_session.flush()

    junior = api_client.get(
        "/api/doctors/junior/busy",
        params={"from": MONDAY.isoformat(), "to": WEEK_END.isoformat()},
    )
    senior = api_client.get(
        "/api/doctors/senior-1/busy",
        params={"from": MONDAY.isoformat(), "to": WEEK_END.isoformat()},
    )

    assert junior.status_code == 200
    body = junior.json()
    assert body["professional_slug"] == "junior"
    assert set(body) == {"professional_slug", "timezone", "from", "to", "busy"}
    assert len(body["busy"]) == 1
    slot = body["busy"][0]
    assert set(slot) == {"professional_slug", "starts_at", "ends_at"}
    assert "patient" not in str(body).lower()
    assert "secret" not in str(body).lower()
    assert "@" not in str(body)

    assert senior.status_code == 200
    assert senior.json()["busy"] == []


def test_doctor_page_shows_busy_only_for_that_professional(
    api_client: TestClient, db_session: Session
) -> None:
    book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=clinic_datetime(MONDAY, time(9, 0)),
        patient_name="Hidden Name",
        patient_email="hidden@example.com",
    )
    db_session.flush()
    db_session.commit()

    junior_page = api_client.get(f"/doctors/junior?from={MONDAY.isoformat()}")
    senior_page = api_client.get(f"/doctors/senior-1?from={MONDAY.isoformat()}")
    intro = api_client.get("/")

    assert junior_page.status_code == 200
    html = junior_page.text
    assert "Busy times" in html
    assert "Busy 09:00–10:00" in html
    assert "Clinic closed (weekend)" in html
    assert "Hidden Name" not in html
    assert "hidden@example.com" not in html
    assert 'id="chat-shell"' in html

    assert senior_page.status_code == 200
    assert "Busy 09:00–10:00" not in senior_page.text
    assert "No busy blocks" in senior_page.text
    assert 'id="chat-shell"' in senior_page.text

    assert intro.status_code == 200
    assert "/doctors/junior" in intro.text
    assert "/doctors/senior-1" in intro.text
    assert "/doctors/senior-2" in intro.text
    assert 'id="chat-shell"' in intro.text


def test_list_busy_blocks_range_rejects_inverted_window(db_session: Session) -> None:
    with pytest.raises(BookingError, match="end day"):
        list_busy_blocks_range(db_session, "junior", WEEK_END, MONDAY)


def test_busy_api_rejects_bad_dates(api_client: TestClient) -> None:
    bad = api_client.get("/api/doctors/junior/busy", params={"from": "not-a-day"})
    unknown = api_client.get("/api/doctors/nobody/busy")
    assert bad.status_code == 400
    assert unknown.status_code == 404
