from dataclasses import asdict
from datetime import date, datetime, time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from clinic_ai_booking.booking import (
    BookingError,
    BusyBlock,
    book_appointment,
    cancel_appointment,
    check_start,
    list_available_starts,
    list_busy_blocks,
    list_next_available_starts,
    list_patient_appointments,
    reschedule_appointment,
)
from clinic_ai_booking.hours import clinic_datetime
from clinic_ai_booking.models import (
    STATUS_CANCELLED,
    STATUS_CONFIRMED,
    STATUS_PENDING_DOCTOR,
    Booking,
    Professional,
    Service,
)

MONDAY = date(2026, 8, 31)
SATURDAY = date(2026, 8, 29)
PATIENT = {"patient_name": "Pat Lee", "patient_email": "pat@example.com"}


def _book(
    session: Session,
    *,
    slug: str = "junior",
    service: str = "A",
    clock: time = time(9, 0),
    day: date = MONDAY,
) -> Booking:
    return book_appointment(
        session,
        professional_slug=slug,
        service_code=service,
        starts_at=clinic_datetime(day, clock),
        **PATIENT,
    )


def test_seed_creates_three_professionals_and_services_a_to_e(db_session: Session) -> None:
    pros = db_session.scalars(select(Professional).order_by(Professional.slug)).all()
    codes = db_session.scalars(select(Service.code).order_by(Service.code)).all()
    assert [p.slug for p in pros] == ["junior", "senior-1", "senior-2"]
    assert [p.is_senior for p in pros] == [False, True, True]
    assert codes == ["A", "B", "C", "D", "E"]
    durations = {
        row.code: row.duration_minutes
        for row in db_session.scalars(select(Service)).all()
    }
    assert durations == {"A": 60, "B": 60, "C": 150, "D": 120, "E": 360}


def test_books_within_hours_when_slot_is_free(db_session: Session) -> None:
    booking = _book(db_session)
    assert booking.status == STATUS_CONFIRMED
    assert booking.ends_at == clinic_datetime(MONDAY, time(10, 0))
    starts = list_available_starts(db_session, "junior", "A", MONDAY)
    assert clinic_datetime(MONDAY, time(9, 0)) not in starts
    assert clinic_datetime(MONDAY, time(10, 0)) in starts
    assert clinic_datetime(MONDAY, time(11, 0)) in starts


def test_rejects_conflict_when_slots_overlap(db_session: Session) -> None:
    _book(db_session)
    with pytest.raises(BookingError, match="overlaps"):
        _book(db_session, clock=time(9, 30))


def test_rejects_normal_service_when_overlapping_a_break(db_session: Session) -> None:
    with pytest.raises(BookingError, match="break"):
        _book(db_session, clock=time(11, 30))


def test_service_e_overtime_is_pending_doctor(db_session: Session) -> None:
    booking = _book(db_session, slug="senior-1", service="E", clock=time(16, 0))
    assert booking.status == STATUS_PENDING_DOCTOR
    assert booking.ends_at == clinic_datetime(MONDAY, time(22, 0))
    busy = list_busy_blocks(db_session, "senior-1", MONDAY)
    assert len(busy) == 1
    assert busy[0].starts_at == clinic_datetime(MONDAY, time(16, 0))


def test_service_e_may_cross_breaks_as_pending_doctor(db_session: Session) -> None:
    booking = _book(db_session, slug="senior-2", service="E", clock=time(9, 0))
    assert booking.status == STATUS_PENDING_DOCTOR
    assert booking.ends_at == clinic_datetime(MONDAY, time(15, 0))


def test_rejects_e_when_it_would_end_after_22(db_session: Session) -> None:
    with pytest.raises(BookingError, match="22:00"):
        _book(db_session, slug="senior-1", service="E", clock=time(16, 15))


def test_rejects_e_when_it_would_cross_midnight(db_session: Session) -> None:
    with pytest.raises(BookingError, match="same calendar day"):
        _book(db_session, slug="senior-1", service="E", clock=time(18, 0))


def test_rejects_invalid_input_with_clear_error(db_session: Session) -> None:
    with pytest.raises(BookingError, match="timezone-aware"):
        book_appointment(
            db_session,
            professional_slug="junior",
            service_code="A",
            starts_at=datetime(2026, 8, 31, 9, 0),
            **PATIENT,
        )
    with pytest.raises(BookingError, match="15-minute grid"):
        book_appointment(
            db_session,
            professional_slug="junior",
            service_code="A",
            starts_at=clinic_datetime(MONDAY, time(9, 5)),
            **PATIENT,
        )
    with pytest.raises(BookingError, match="patient name"):
        book_appointment(
            db_session,
            professional_slug="junior",
            service_code="A",
            starts_at=clinic_datetime(MONDAY, time(9, 0)),
            patient_name="  ",
            patient_email="pat@example.com",
        )
    with pytest.raises(BookingError, match="email"):
        book_appointment(
            db_session,
            professional_slug="junior",
            service_code="A",
            starts_at=clinic_datetime(MONDAY, time(9, 0)),
            patient_name="Pat",
            patient_email="not-an-email",
        )
    with pytest.raises(BookingError, match="unknown professional"):
        _book(db_session, slug="nobody")
    with pytest.raises(BookingError, match="unknown service"):
        _book(db_session, service="Z")
    with pytest.raises(BookingError, match="Monday"):
        _book(db_session, day=SATURDAY)


def test_junior_cannot_book_c_d_or_e(db_session: Session) -> None:
    for code in ("C", "D", "E"):
        with pytest.raises(BookingError, match="cannot perform"):
            _book(db_session, slug="junior", service=code)
    assert list_available_starts(db_session, "junior", "C", MONDAY) == []


def test_public_busy_blocks_expose_no_patient_fields(db_session: Session) -> None:
    book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=clinic_datetime(MONDAY, time(9, 0)),
        patient_name="Secret Patient",
        patient_email="secret@example.com",
    )
    slots = list_busy_blocks(db_session, "junior", MONDAY)
    assert len(slots) == 1
    payload = asdict(slots[0])
    assert set(payload) == {"professional_slug", "starts_at", "ends_at"}
    assert "patient_name" not in payload
    assert "patient_email" not in payload
    assert "Secret" not in str(payload)
    assert isinstance(slots[0], BusyBlock)
    other = list_busy_blocks(db_session, "senior-1", MONDAY)
    assert other == []


def test_adjacent_bookings_do_not_conflict(db_session: Session) -> None:
    first = _book(db_session, clock=time(9, 0))
    second = book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=clinic_datetime(MONDAY, time(10, 0)),
        patient_name="Other Patient",
        patient_email="other@example.com",
    )
    assert first.status == STATUS_CONFIRMED
    assert second.status == STATUS_CONFIRMED
    assert second.starts_at == first.ends_at


def test_cancels_appointment_and_frees_the_slot(db_session: Session) -> None:
    booking = _book(db_session, clock=time(9, 0))
    cancelled = cancel_appointment(db_session, booking.id)
    assert cancelled.status == STATUS_CANCELLED
    assert list_busy_blocks(db_session, "junior", MONDAY) == []
    again = _book(db_session, clock=time(9, 0))
    assert again.status == STATUS_CONFIRMED
    assert again.id != booking.id


def test_rejects_cancel_when_booking_missing_or_already_cancelled(
    db_session: Session,
) -> None:
    with pytest.raises(BookingError, match="unknown booking"):
        cancel_appointment(db_session, 999_999)
    booking = _book(db_session)
    cancel_appointment(db_session, booking.id)
    with pytest.raises(BookingError, match="already cancelled"):
        cancel_appointment(db_session, booking.id)


def test_agent_style_new_booking_lists_checks_then_books(db_session: Session) -> None:
    choices = list_available_starts(db_session, "junior", "A", MONDAY)
    chosen = clinic_datetime(MONDAY, time(10, 0))
    assert chosen in choices
    plan = check_start(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=chosen,
    )
    assert plan.starts_at == chosen
    assert plan.ends_at == clinic_datetime(MONDAY, time(11, 0))
    assert plan.status == STATUS_CONFIRMED
    booking = book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=chosen,
        **PATIENT,
    )
    assert booking.status == STATUS_CONFIRMED
    assert booking.starts_at == chosen
    assert chosen not in list_available_starts(db_session, "junior", "A", MONDAY)


def test_check_start_rejects_occupied_or_invalid_times(db_session: Session) -> None:
    _book(db_session, clock=time(9, 0))
    with pytest.raises(BookingError, match="overlaps"):
        check_start(
            db_session,
            professional_slug="junior",
            service_code="A",
            starts_at=clinic_datetime(MONDAY, time(9, 30)),
        )
    with pytest.raises(BookingError, match="break"):
        check_start(
            db_session,
            professional_slug="junior",
            service_code="A",
            starts_at=clinic_datetime(MONDAY, time(11, 30)),
        )


def test_list_next_available_starts_skips_weekend_to_weekday(
    db_session: Session,
) -> None:
    days = list_next_available_starts(
        db_session, "junior", "A", SATURDAY, max_days=7, limit_days=1
    )
    assert len(days) == 1
    assert days[0].day == MONDAY
    assert clinic_datetime(MONDAY, time(9, 0)) in days[0].starts


def test_list_next_available_starts_skips_full_day(db_session: Session) -> None:
    n = 0
    while True:
        remaining = list_available_starts(db_session, "junior", "A", MONDAY)
        if not remaining:
            break
        book_appointment(
            db_session,
            professional_slug="junior",
            service_code="A",
            starts_at=remaining[0],
            patient_name=f"Pat {n}",
            patient_email=f"pat{n}@example.com",
        )
        n += 1
    assert list_available_starts(db_session, "junior", "A", MONDAY) == []
    days = list_next_available_starts(
        db_session, "junior", "A", MONDAY, max_days=5, limit_days=1
    )
    assert len(days) == 1
    assert days[0].day == date(2026, 9, 1)
    assert len(days[0].starts) > 0


def test_list_next_available_starts_respects_limits_and_seniors_only(
    db_session: Session,
) -> None:
    days = list_next_available_starts(
        db_session,
        "junior",
        "A",
        MONDAY,
        max_days=7,
        limit_days=2,
        limit_starts_per_day=2,
    )
    assert len(days) == 2
    assert days[0].day == MONDAY
    assert len(days[0].starts) == 2
    assert list_next_available_starts(
        db_session, "junior", "C", MONDAY, max_days=7
    ) == []
    with pytest.raises(BookingError, match="max_days"):
        list_next_available_starts(
            db_session, "junior", "A", MONDAY, max_days=0
        )


def test_list_available_starts_can_ignore_current_booking_for_reschedule(
    db_session: Session,
) -> None:
    booking = _book(db_session, clock=time(9, 0))
    without = list_available_starts(db_session, "junior", "A", MONDAY)
    with_ignore = list_available_starts(
        db_session, "junior", "A", MONDAY, ignore_booking_id=booking.id
    )
    assert clinic_datetime(MONDAY, time(9, 0)) not in without
    assert clinic_datetime(MONDAY, time(9, 0)) in with_ignore


def test_agent_style_reschedule_lists_then_books_then_cancels(
    db_session: Session,
) -> None:
    current = _book(db_session, clock=time(9, 0))
    choices = list_available_starts(
        db_session, "junior", "A", MONDAY, ignore_booking_id=current.id
    )
    new_start = clinic_datetime(MONDAY, time(11, 0))
    assert new_start in choices
    replacement = book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=new_start,
        ignore_booking_id=current.id,
        **PATIENT,
    )
    cancel_appointment(db_session, current.id)
    assert replacement.status == STATUS_CONFIRMED
    assert db_session.get(Booking, current.id).status == STATUS_CANCELLED
    busy = list_busy_blocks(db_session, "junior", MONDAY)
    assert len(busy) == 1
    assert busy[0].starts_at == new_start


def test_reschedules_appointment_to_a_free_slot(db_session: Session) -> None:
    booking = _book(db_session, clock=time(9, 0))
    original_id = booking.id
    moved = reschedule_appointment(
        db_session, booking.id, clinic_datetime(MONDAY, time(11, 0))
    )
    assert moved.id != original_id
    assert moved.starts_at == clinic_datetime(MONDAY, time(11, 0))
    assert moved.ends_at == clinic_datetime(MONDAY, time(12, 0))
    assert moved.status == STATUS_CONFIRMED
    original = db_session.get(Booking, original_id)
    assert original is not None
    assert original.status == STATUS_CANCELLED
    busy = list_busy_blocks(db_session, "junior", MONDAY)
    assert len(busy) == 1
    assert busy[0].starts_at == clinic_datetime(MONDAY, time(11, 0))
    starts = list_available_starts(db_session, "junior", "A", MONDAY)
    assert clinic_datetime(MONDAY, time(9, 0)) in starts
    assert clinic_datetime(MONDAY, time(11, 0)) not in starts


def test_rejects_reschedule_when_new_slot_overlaps(db_session: Session) -> None:
    first = book_appointment(
        db_session,
        professional_slug="junior",
        service_code="A",
        starts_at=clinic_datetime(MONDAY, time(9, 0)),
        patient_name="Other",
        patient_email="other@example.com",
    )
    second = _book(db_session, clock=time(11, 0))
    with pytest.raises(BookingError, match="overlaps"):
        reschedule_appointment(
            db_session, second.id, clinic_datetime(MONDAY, time(9, 30))
        )
    assert first.starts_at == clinic_datetime(MONDAY, time(9, 0))
    assert second.status == STATUS_CONFIRMED
    assert second.starts_at == clinic_datetime(MONDAY, time(11, 0))


def test_rejects_reschedule_when_booking_cancelled(db_session: Session) -> None:
    booking = _book(db_session)
    cancel_appointment(db_session, booking.id)
    with pytest.raises(BookingError, match="already cancelled"):
        reschedule_appointment(
            db_session, booking.id, clinic_datetime(MONDAY, time(11, 0))
        )


def test_reschedule_recomputes_service_e_pending_doctor(db_session: Session) -> None:
    booking = _book(db_session, slug="senior-1", service="E", clock=time(9, 0))
    assert booking.status == STATUS_PENDING_DOCTOR
    original_id = booking.id
    moved = reschedule_appointment(
        db_session, booking.id, clinic_datetime(MONDAY, time(16, 0))
    )
    assert moved.id != original_id
    assert moved.status == STATUS_PENDING_DOCTOR
    assert moved.ends_at == clinic_datetime(MONDAY, time(22, 0))
    assert db_session.get(Booking, original_id).status == STATUS_CANCELLED


def test_failed_reschedule_leaves_original_booking(db_session: Session) -> None:
    booking = _book(db_session, clock=time(9, 0))
    with pytest.raises(BookingError, match="break"):
        reschedule_appointment(
            db_session, booking.id, clinic_datetime(MONDAY, time(11, 30))
        )
    refreshed = db_session.get(Booking, booking.id)
    assert refreshed is not None
    assert refreshed.status == STATUS_CONFIRMED
    assert refreshed.starts_at == clinic_datetime(MONDAY, time(9, 0))
    assert len(list_busy_blocks(db_session, "junior", MONDAY)) == 1


def test_list_patient_appointments_for_reminders(db_session: Session) -> None:
    first = _book(db_session, service="A", clock=time(9, 0))
    second = _book(db_session, service="B", clock=time(14, 0))
    rows = list_patient_appointments(db_session, PATIENT["patient_email"])
    assert [(r.booking_id, r.service_code, r.starts_at) for r in rows] == [
        (first.id, "A", clinic_datetime(MONDAY, time(9, 0))),
        (second.id, "B", clinic_datetime(MONDAY, time(14, 0))),
    ]
    assert list_patient_appointments(db_session, "nobody@example.com") == []


def test_rejects_rebooking_same_slot_and_reminds(db_session: Session) -> None:
    existing = _book(db_session, clock=time(9, 0))
    with pytest.raises(BookingError, match="already have a booking at that time"):
        _book(db_session, clock=time(9, 0))
    rows = list_patient_appointments(db_session, PATIENT["patient_email"])
    assert len(rows) == 1
    assert rows[0].booking_id == existing.id


def test_rejects_second_booking_of_same_service_suggests_reschedule(
    db_session: Session,
) -> None:
    existing = _book(db_session, service="A", clock=time(9, 0))
    with pytest.raises(BookingError, match="reschedule that appointment"):
        _book(db_session, service="A", clock=time(14, 0))
    assert existing.status == STATUS_CONFIRMED
    assert len(list_patient_appointments(db_session, PATIENT["patient_email"])) == 1


def test_allows_second_different_service_at_different_time(
    db_session: Session,
) -> None:
    first = _book(db_session, service="A", clock=time(9, 0))
    second = _book(db_session, service="B", clock=time(14, 0))
    assert first.service.code == "A"
    assert second.service.code == "B"
    assert second.starts_at != first.starts_at
    assert len(list_patient_appointments(db_session, PATIENT["patient_email"])) == 2


def test_rejects_third_active_booking(db_session: Session) -> None:
    _book(db_session, service="A", clock=time(9, 0))
    _book(db_session, service="B", clock=time(14, 0))
    with pytest.raises(BookingError, match="at most two different services"):
        book_appointment(
            db_session,
            professional_slug="senior-1",
            service_code="C",
            starts_at=clinic_datetime(MONDAY, time(10, 0)),
            **PATIENT,
        )


def test_rejects_second_booking_that_overlaps_patient_time(
    db_session: Session,
) -> None:
    _book(db_session, slug="junior", service="A", clock=time(9, 0))
    with pytest.raises(BookingError, match="overlaps your existing booking"):
        book_appointment(
            db_session,
            professional_slug="senior-1",
            service_code="B",
            starts_at=clinic_datetime(MONDAY, time(9, 30)),
            **PATIENT,
        )


def test_reschedule_same_service_still_allowed(db_session: Session) -> None:
    booking = _book(db_session, service="A", clock=time(9, 0))
    moved = reschedule_appointment(
        db_session, booking.id, clinic_datetime(MONDAY, time(14, 0))
    )
    assert moved.service.code == "A"
    assert moved.starts_at == clinic_datetime(MONDAY, time(14, 0))
    assert db_session.get(Booking, booking.id).status == STATUS_CANCELLED
    rows = list_patient_appointments(db_session, PATIENT["patient_email"])
    assert len(rows) == 1
    assert rows[0].booking_id == moved.id
