"""Facts produced by the booking graph for the client-facing reply."""

from __future__ import annotations

from typing import Any, TypedDict


class TurnFacts(TypedDict):
    """Structured outcome of one turn's booking work (no invented slots)."""

    status: str
    missing: list[str]
    hint: str
    offered_starts: list[str]
    offered_times: list[str]
    booking_id: int | None
    error: str | None
    catalog_services: list[str]
    catalog_professionals: list[str]
    clinic_today: str
    resolved_day: str
    time_band: str
    # Plan B compact availability (day/weekday/bands); empty dict when unused.
    availability: dict[str, Any]
    doctor_candidates: list[str]
    # ISO dates when clarifying "next Friday" (this week vs next week).
    day_options: list[str]
    # Confirm / booked summary fields (patient-facing).
    professional_name: str
    service_code: str
    duration_minutes: int | None
    ends_at_clock: str


def empty_facts() -> TurnFacts:
    """Return a blank facts bag for a new turn."""
    return TurnFacts(
        status="empty",
        missing=[],
        hint="",
        offered_starts=[],
        offered_times=[],
        booking_id=None,
        error=None,
        catalog_services=[],
        catalog_professionals=[],
        clinic_today="",
        resolved_day="",
        time_band="",
        availability={},
        doctor_candidates=[],
        day_options=[],
        professional_name="",
        service_code="",
        duration_minutes=None,
        ends_at_clock="",
    )


def facts_as_dict(facts: TurnFacts) -> dict[str, Any]:
    """JSON-serializable copy for the reply prompt."""
    return dict(facts)
