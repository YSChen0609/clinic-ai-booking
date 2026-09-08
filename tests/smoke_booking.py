"""Stage 1 smoke: seed, overlap reject, service E pending_doctor.

Run from clinic-ai-booking/: docker compose up -d db
then: uv run python tests/smoke_booking.py
"""

from dataclasses import asdict
from datetime import date, datetime, time
from pathlib import Path
import os
import sys

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from clinic_ai_booking.domain.booking import BookingError, book_appointment, list_busy_blocks
from clinic_ai_booking.db import apply_schema_and_seed, host_postgres_url, load_host_env
from clinic_ai_booking.domain.hours import clinic_datetime
from clinic_ai_booking.domain.models import Booking, Professional, Service, User

ROOT = Path(__file__).resolve().parents[1]
SMOKE_EMAIL = "smoke@example.com"
SMOKE_NAME = "Smoke Test"
DAY = date(2026, 8, 31)  # Monday


def _patient() -> dict[str, str]:
    return {"patient_name": SMOKE_NAME, "patient_email": SMOKE_EMAIL}


def _clock(hour: int, minute: int = 0) -> datetime:
    return clinic_datetime(DAY, time(hour, minute))


def _wipe_smoke(session: Session) -> None:
    session.execute(delete(Booking).where(Booking.patient_email == SMOKE_EMAIL))
    session.execute(delete(User).where(User.email == SMOKE_EMAIL))
    session.commit()


def _redact_url(url: str) -> str:
    if "@" not in url:
        return url
    head, _, tail = url.partition("@")
    scheme, _, rest = head.partition("://")
    user, _, _pw = rest.partition(":")
    return f"{scheme}://{user}:***@{tail}"


def main() -> int:
    """Run booking smoke against Compose Postgres and print expected results."""
    load_host_env(ROOT / ".env")
    url = host_postgres_url(database=os.environ.get("POSTGRES_DB", "clinic"))
    print("== clinic-ai-booking stage 1 smoke ==")
    print(f"using {_redact_url(url)}")
    print("expect: catalog seeded, overlap rejected, E overtime pending_doctor")
    print()

    try:
        engine = apply_schema_and_seed(url)
    except Exception as exc:
        print(f"FAIL: cannot reach Postgres ({exc})")
        print("start it with: docker compose up -d db")
        return 1

    failed = False
    with Session(engine) as session:
        _wipe_smoke(session)
        slugs = session.scalars(select(Professional.slug).order_by(Professional.slug)).all()
        codes = session.scalars(select(Service.code).order_by(Service.code)).all()
        print(f"catalog: {', '.join(slugs)} | {', '.join(codes)}")
        if slugs != ["junior", "senior-1", "senior-2"] or codes != ["A", "B", "C", "D", "E"]:
            print("FAIL: seed did not create 3 professionals and services A-E")
            failed = True
        else:
            print("  -> ok")

        print()
        print("1) happy path: junior service A 09:00")
        first = book_appointment(
            session,
            professional_slug="junior",
            service_code="A",
            starts_at=_clock(9),
            **_patient(),
        )
        session.commit()
        print(f"   status={first.status} {first.starts_at.strftime('%H:%M')}-{first.ends_at.strftime('%H:%M')}")
        if first.status != "confirmed":
            print("FAIL: expected confirmed")
            failed = True
        else:
            print("   expect confirmed 09:00-10:00  -> ok")

        print()
        print("2) overlap: junior service A 09:30 (same morning)")
        try:
            book_appointment(
                session,
                professional_slug="junior",
                service_code="A",
                starts_at=_clock(9, 30),
                **_patient(),
            )
            session.commit()
            print("FAIL: second booking should have been rejected")
            failed = True
        except BookingError as exc:
            session.rollback()
            print(f"   rejected: {exc}")
            print("   expect an overlap error  -> ok")

        print()
        print("3) service E overtime: senior-1 16:00 (ends 22:00, crosses dinner)")
        overtime = book_appointment(
            session,
            professional_slug="senior-1",
            service_code="E",
            starts_at=_clock(16),
            **_patient(),
        )
        session.commit()
        print(
            f"   status={overtime.status} "
            f"{overtime.starts_at.strftime('%H:%M')}-{overtime.ends_at.strftime('%H:%M')}"
        )
        if overtime.status != "pending_doctor":
            print("FAIL: expected pending_doctor")
            failed = True
        else:
            print("   expect pending_doctor 16:00-22:00  -> ok")

        print()
        print("4) public busy slots for junior (times only, no patient name/email)")
        busy = list_busy_blocks(session, "junior", DAY)
        keys = list(asdict(busy[0])) if busy else []
        for slot in busy:
            print(
                f"   {slot.professional_slug} "
                f"{slot.starts_at.strftime('%H:%M')}-{slot.ends_at.strftime('%H:%M')} "
                f"keys={keys}"
            )
        if any("patient" in key for key in keys):
            print("FAIL: public busy payload leaked a patient field")
            failed = True
        elif not busy:
            print("FAIL: expected junior to look busy")
            failed = True
        else:
            print("   expect one block 09:00-10:00, keys professional_slug/starts_at/ends_at  -> ok")

        _wipe_smoke(session)

    print()
    if failed:
        print("FAIL")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
