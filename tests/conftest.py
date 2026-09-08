"""Shared booking-engine fixtures. Needs Compose Postgres (or TEST_DATABASE_URL)."""

from pathlib import Path

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from clinic_ai_booking.db import apply_schema_and_seed, ensure_database, host_postgres_url, load_host_env
from clinic_ai_booking.models import Booking, User
from clinic_ai_booking.notify import reset_ports_to_fakes

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _fake_notify_ports():
    """Keep unit tests on FakeCalendar/FakeEmail (ignore host NOTIFY_MODE)."""
    reset_ports_to_fakes()
    yield
    reset_ports_to_fakes()


@pytest.fixture(scope="session")
def db_engine() -> Engine:
    """Apply schema on a dedicated test database."""
    load_host_env(_ROOT / ".env")
    url = host_postgres_url()
    try:
        ensure_database(url)
        return apply_schema_and_seed(url)
    except OperationalError as exc:
        pytest.fail(
            f"Postgres is not reachable ({url}). "
            f"Start it with: docker compose up -d db ({exc})"
        )


@pytest.fixture
def db_session(db_engine: Engine) -> Session:
    """Yield a session and delete bookings/users afterward."""
    session = Session(db_engine)
    try:
        yield session
    finally:
        session.rollback()
        session.execute(delete(Booking))
        session.execute(delete(User))
        session.commit()
        session.close()
