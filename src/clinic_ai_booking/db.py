"""Postgres engine, schema apply, and catalog seed."""

from pathlib import Path
import os
import re

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from clinic_ai_booking.doctors import DOCTORS
from clinic_ai_booking.models import Base, Professional, Service

# code, duration_minutes, seniors_only — A/B all pros; C–E seniors only.
SERVICE_DEFS: tuple[tuple[str, int, bool], ...] = (
    ("A", 60, False),
    ("B", 60, False),
    ("C", 150, True),
    ("D", 120, True),
    ("E", 360, True),
)

_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HOST_ENV_KEYS = frozenset(
    {
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_TEST_DB",
        "TEST_DATABASE_URL",
    }
)

NO_OVERLAP_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'bookings_no_overlap'
    ) THEN
        ALTER TABLE bookings
            ADD CONSTRAINT bookings_no_overlap
            EXCLUDE USING gist (
                professional_id WITH =,
                tstzrange(starts_at, ends_at, '[)') WITH &&
            )
            WHERE (status IN ('confirmed', 'pending_doctor'));
    END IF;
END $$;
"""

# create_all does not add columns to existing tables (stage 1 → stage 2).
BOOKINGS_USER_ID_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bookings' AND column_name = 'user_id'
    ) THEN
        ALTER TABLE bookings
            ADD COLUMN user_id INTEGER REFERENCES users(id);
    END IF;
END $$;
"""


def normalize_database_url(url: str) -> str:
    """Use the psycopg3 driver when the URL is a plain postgresql:// DSN."""
    if url.startswith("postgresql://") and "+psycopg" not in url.split("://", 1)[0]:
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


def database_url_from_env() -> str | None:
    """Return DATABASE_URL from the environment, or None if unset."""
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        return None
    return normalize_database_url(raw)


def load_host_env(path: Path) -> None:
    """Load POSTGRES_* from a Compose .env into os.environ if unset. Skips DATABASE_URL."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in _HOST_ENV_KEYS and key not in os.environ:
            os.environ[key] = value.strip().strip("'\"")


def host_postgres_url(*, database: str | None = None) -> str:
    """Build a host-side Postgres URL. Ignores Compose DATABASE_URL (hostname db)."""
    if database is None and os.environ.get("TEST_DATABASE_URL"):
        return normalize_database_url(os.environ["TEST_DATABASE_URL"])
    user = os.environ.get("POSTGRES_USER", "clinic")
    password = os.environ.get("POSTGRES_PASSWORD", "clinic_dev_change_me")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    name = database or os.environ.get("POSTGRES_TEST_DB", "clinic_test")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"


def make_engine(url: str) -> Engine:
    """Build a SQLAlchemy engine for that Postgres URL."""
    return create_engine(normalize_database_url(url), pool_pre_ping=True)


def ensure_database(url: str) -> None:
    """Create the target database if it does not exist."""
    sa_url = make_url(normalize_database_url(url))
    db_name = sa_url.database
    if not db_name or not _DB_NAME_RE.match(db_name):
        raise ValueError("database name is missing or invalid")
    last_error: Exception | None = None
    for admin_name in ("postgres", "clinic"):
        admin = create_engine(sa_url.set(database=admin_name), isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as conn:
                exists = conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"),
                    {"n": db_name},
                ).scalar()
                if not exists:
                    conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            return
        except OperationalError as exc:
            last_error = exc
        finally:
            admin.dispose()
    if last_error is not None:
        raise last_error



def apply_schema(engine: Engine) -> None:
    """Create tables, btree_gist, overlap exclusion, and stage-2 user_id column."""
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(BOOKINGS_USER_ID_SQL))
        conn.execute(text(NO_OVERLAP_SQL))


def seed_catalog(session: Session) -> None:
    """Insert or update the 3 professionals and services A–E."""
    for doctor in DOCTORS:
        row = session.scalar(select(Professional).where(Professional.slug == doctor.slug))
        if row is None:
            session.add(
                Professional(slug=doctor.slug, name=doctor.name, is_senior=doctor.is_senior)
            )
        else:
            row.name = doctor.name
            row.is_senior = doctor.is_senior
    for code, minutes, seniors_only in SERVICE_DEFS:
        row = session.scalar(select(Service).where(Service.code == code))
        if row is None:
            session.add(
                Service(code=code, duration_minutes=minutes, seniors_only=seniors_only)
            )
        else:
            row.duration_minutes = minutes
            row.seniors_only = seniors_only
    session.commit()


def apply_schema_and_seed(url: str) -> Engine:
    """Apply schema and seed catalog; return an engine bound to url."""
    engine = make_engine(url)
    apply_schema(engine)
    with Session(engine) as session:
        seed_catalog(session)
    return engine

