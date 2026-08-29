from datetime import date, time, timedelta

from clinic_ai_booking.hours import (
    CLOSE,
    OPEN,
    OVERTIME_END,
    SLOT_MINUTES,
    candidate_starts,
    clinic_datetime,
    explain_clinic_day,
    explain_clinic_start,
    interval_overlaps_break,
    on_start_grid,
    overlaps,
    same_calendar_day,
    start_during_open_hours,
)

MONDAY = date(2026, 8, 31)
SATURDAY = date(2026, 8, 29)


def test_on_start_grid_accepts_quarter_hours() -> None:
    start = clinic_datetime(MONDAY, time(9, 15))
    assert on_start_grid(start)
    assert not on_start_grid(clinic_datetime(MONDAY, time(9, 5)))


def test_start_during_open_hours_rejects_weekend_and_break() -> None:
    assert start_during_open_hours(clinic_datetime(MONDAY, OPEN))
    assert not start_during_open_hours(clinic_datetime(SATURDAY, OPEN))
    assert not start_during_open_hours(clinic_datetime(MONDAY, time(12, 0)))
    assert not start_during_open_hours(clinic_datetime(MONDAY, CLOSE))


def test_interval_overlaps_break_for_lunch() -> None:
    start = clinic_datetime(MONDAY, time(11, 30))
    end = start + timedelta(hours=1)
    assert interval_overlaps_break(start, end)
    ends_at_lunch = clinic_datetime(MONDAY, time(11, 0))
    assert not interval_overlaps_break(ends_at_lunch, clinic_datetime(MONDAY, time(12, 0)))


def test_candidate_starts_use_15_minute_grid() -> None:
    starts = candidate_starts(MONDAY)
    assert starts[0] == clinic_datetime(MONDAY, OPEN)
    assert starts[-1] == clinic_datetime(MONDAY, time(19, 45))
    assert all((s.minute % SLOT_MINUTES) == 0 for s in starts)
    assert candidate_starts(SATURDAY) == []


def test_same_calendar_day_and_overtime_limit() -> None:
    start = clinic_datetime(MONDAY, time(16, 0))
    end_ok = clinic_datetime(MONDAY, OVERTIME_END)
    end_midnight = start + timedelta(hours=8)
    assert same_calendar_day(start, end_ok)
    assert not same_calendar_day(start, end_midnight)
    assert overlaps(start, end_ok, clinic_datetime(MONDAY, time(17, 0)), clinic_datetime(MONDAY, time(18, 0)))


def test_explain_clinic_day_and_start() -> None:
    ok_day, reason_day = explain_clinic_day(SATURDAY)
    assert ok_day is False
    assert "Saturday" in reason_day or "Monday–Friday" in reason_day
    ok_start, _ = explain_clinic_start(clinic_datetime(MONDAY, time(9, 0)))
    assert ok_start is True
    bad_start, reason_start = explain_clinic_start(clinic_datetime(MONDAY, time(12, 30)))
    assert bad_start is False
    assert "break" in reason_start.lower()
