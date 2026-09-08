"""In-scope booking StateGraph: nodes call booking.py only (no inventing slots)."""

from __future__ import annotations

import logging
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from clinic_ai_booking.domain.booking import (
    BookingError,
    book_appointment as engine_book,
    booking_created_dict,
    check_start as engine_check_start,
    explain_can_perform,
    list_available_starts as engine_list_available_starts,
    list_next_available_starts as engine_list_next_available_starts,
    list_patient_appointments as engine_list_patient_appointments,
)
from clinic_ai_booking.chat.context import ChatContext
from clinic_ai_booking.chat.facts import TurnFacts, empty_facts
from clinic_ai_booking.chat.resolve import (
    catalog_professional_lines,
    catalog_service_lines,
    filter_starts_by_band,
    format_day_label,
    parse_day,
    parse_starts_at,
)
from clinic_ai_booking.chat.state import TurnState
from clinic_ai_booking.domain.hours import explain_clinic_day, to_clinic

logger = logging.getLogger(__name__)

Route = Literal["reply", "available_time", "client_id", "book", "post_booking"]


def doctor_service(state: TurnState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    """Validate or request service + professional via the booking engine."""
    ctx = runtime.context
    facts = empty_facts()
    facts["clinic_today"] = format_day_label(ctx.clinic_day())
    facts["catalog_services"] = catalog_service_lines(ctx.db)
    facts["catalog_professionals"] = catalog_professional_lines(ctx.db)
    if ctx.draft.day:
        facts["resolved_day"] = format_day_label(parse_day(ctx.draft.day) or ctx.clinic_day())
    if ctx.draft.time_band:
        facts["time_band"] = ctx.draft.time_band

    missing: list[str] = []
    if not ctx.draft.service_code:
        missing.append("service_code")
    if not ctx.draft.professional_slug:
        missing.append("professional_slug")
    if missing:
        facts["status"] = "need_info"
        facts["missing"] = missing
        facts["hint"] = (
            "Ask which service (A–E) and which doctor they want. "
            "Use only the catalog lists in facts."
        )
        return {"facts": facts}

    ok, reason = explain_can_perform(
        ctx.db, ctx.draft.professional_slug, ctx.draft.service_code
    )
    if not ok:
        facts["status"] = "need_info"
        facts["missing"] = ["professional_slug"]
        facts["error"] = reason
        facts["hint"] = reason
        return {"facts": facts}

    blocked = _same_service_block(ctx)
    if blocked is not None:
        return {"facts": blocked}

    facts["status"] = "doctor_ok"
    facts["hint"] = reason
    return {"facts": facts}


def available_time(state: TurnState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    """List or check starts via the booking engine; stop when the user must choose."""
    ctx = runtime.context
    facts = empty_facts()
    facts["clinic_today"] = format_day_label(ctx.clinic_day())
    facts["catalog_services"] = catalog_service_lines(ctx.db)
    facts["catalog_professionals"] = catalog_professional_lines(ctx.db)
    if ctx.draft.time_band:
        facts["time_band"] = ctx.draft.time_band
    slug = ctx.draft.professional_slug
    code = ctx.draft.service_code
    assert slug and code

    day = parse_day(ctx.draft.day)
    start = parse_starts_at(ctx.draft.starts_at, day=day)

    if start is not None:
        try:
            plan = engine_check_start(
                ctx.db,
                professional_slug=slug,
                service_code=code,
                starts_at=start,
            )
            ctx.draft.day = to_clinic(plan.starts_at).date().isoformat()
            ctx.draft.starts_at = plan.starts_at.isoformat()
            facts["status"] = "slot_ok"
            facts["resolved_day"] = format_day_label(to_clinic(plan.starts_at).date())
            facts["hint"] = (
                f"Slot is free: {plan.starts_at.isoformat()} "
                f"(service {plan.service_code}, {plan.professional_slug})."
            )
            facts["offered_starts"] = [plan.starts_at.isoformat()]
            facts["offered_times"] = [to_clinic(plan.starts_at).strftime("%H:%M")]
            return {"facts": facts}
        except BookingError as exc:
            facts["error"] = str(exc)
            day = to_clinic(start).date()
            ctx.draft.starts_at = None

    if day is None:
        try:
            nxt = engine_list_next_available_starts(
                ctx.db, slug, code, ctx.clinic_day(), limit_days=3, limit_starts_per_day=5
            )
        except BookingError as exc:
            facts["status"] = "need_info"
            facts["error"] = str(exc)
            facts["hint"] = str(exc)
            facts["missing"] = ["day"]
            return {"facts": facts}
        return _offer_days(
            facts, nxt, hint="Ask which day/time they want from these options.", band=ctx.draft.time_band
        )

    facts["resolved_day"] = format_day_label(day)
    valid, reason = explain_clinic_day(day)
    if not valid:
        nxt = engine_list_next_available_starts(
            ctx.db, slug, code, ctx.clinic_day(), limit_days=3, limit_starts_per_day=5
        )
        facts["error"] = reason
        return _offer_days(
            facts, nxt, hint=reason + " Offer the next open starts.", band=ctx.draft.time_band
        )

    starts = filter_starts_by_band(
        engine_list_available_starts(ctx.db, slug, code, day), ctx.draft.time_band
    )
    if not starts:
        all_day = engine_list_available_starts(ctx.db, slug, code, day)
        if all_day and ctx.draft.time_band:
            wanted = ctx.draft.time_band
            facts["status"] = "offer_slots"
            facts["offered_starts"] = [s.isoformat() for s in all_day[:12]]
            facts["offered_times"] = [to_clinic(s).strftime("%H:%M") for s in all_day[:12]]
            facts["missing"] = ["starts_at"]
            # Do not label these times as the empty band (e.g. "(morning)").
            facts["time_band"] = ""
            facts["error"] = f"No {wanted} starts on {format_day_label(day)}"
            facts["hint"] = (
                f"{facts['error']}. "
                "Show the other times from offered_times only, or ask another day."
            )
            ctx.draft.day = day.isoformat()
            return {"facts": facts}
        nxt = engine_list_next_available_starts(
            ctx.db, slug, code, day, limit_days=3, limit_starts_per_day=5
        )
        return _offer_days(
            facts,
            nxt,
            hint=f"No free starts on {format_day_label(day)}. Offer later days from the engine list.",
            band=ctx.draft.time_band,
        )

    ctx.draft.day = day.isoformat()
    facts["status"] = "offer_slots"
    facts["offered_starts"] = [s.isoformat() for s in starts[:12]]
    facts["offered_times"] = [to_clinic(s).strftime("%H:%M") for s in starts[:12]]
    band_note = f" ({ctx.draft.time_band})" if ctx.draft.time_band else ""
    facts["hint"] = (
        f"Show only these starts for {format_day_label(day)}{band_note}. "
        "Do not invent times. Ask the patient to pick one."
    )
    facts["missing"] = ["starts_at"]
    return {"facts": facts}


def client_id(state: TurnState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    """Require name+email typed in this chat before writing a booking."""
    ctx = runtime.context
    facts = dict(state.get("facts") or empty_facts())

    if ctx.can_book_now:
        blocked = _same_service_block(ctx)
        if blocked is not None:
            return {"facts": blocked}
        facts["status"] = "identity_ok"
        facts["hint"] = "Identity is present; proceed to book."
        facts["missing"] = []
        return {"facts": facts}

    missing: list[str] = []
    if not ctx.patient_name:
        missing.append("patient_name")
    if not ctx.patient_email:
        missing.append("patient_email")
    facts["status"] = "need_info"
    facts["missing"] = missing
    facts["hint"] = "Ask for the patient's full name and email before booking."
    return {"facts": facts}


def book_node(state: TurnState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    """Write the appointment through booking.book_appointment."""
    ctx = runtime.context
    facts = empty_facts()
    slug = ctx.draft.professional_slug
    code = ctx.draft.service_code
    day = parse_day(ctx.draft.day)
    start = parse_starts_at(ctx.draft.starts_at, day=day)
    if not slug or not code or start is None or not ctx.can_book_now:
        facts["status"] = "book_failed"
        facts["error"] = "Missing service, doctor, start, or contact."
        facts["hint"] = facts["error"]
        return {"facts": facts}

    try:
        booking = engine_book(
            ctx.db,
            professional_slug=slug,
            service_code=code,
            starts_at=start,
            patient_name=ctx.patient_name or "",
            patient_email=ctx.patient_email or "",
        )
        created = booking_created_dict(booking)
        day_iso = to_clinic(start).date().isoformat()
        ctx.remember_book(professional_slug=slug, day=day_iso)
        facts["status"] = "booked"
        facts["booking_id"] = int(created["id"])  # type: ignore[arg-type]
        facts["hint"] = (
            f"Booking confirmed id={created['id']} "
            f"starts_at={created['starts_at']} status={created['status']}."
        )
        facts["offered_starts"] = [str(created["starts_at"])]
        return {"facts": facts}
    except BookingError as exc:
        ctx.db.rollback()
        facts["status"] = "book_failed"
        facts["error"] = str(exc)
        facts["hint"] = str(exc)
        return {"facts": facts}
    except Exception as exc:
        ctx.db.rollback()
        logger.exception("book_appointment failed")
        facts["status"] = "book_failed"
        facts["error"] = str(exc)
        facts["hint"] = "Booking failed; apologize and ask to try another listed time."
        return {"facts": facts}


def post_booking(state: TurnState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    """Post-book step (notify already runs inside the engine); finalize hint."""
    del runtime  # notify stub already invoked by engine_book
    facts = dict(state.get("facts") or empty_facts())
    if facts.get("status") == "booked":
        facts["hint"] = (
            (facts.get("hint") or "")
            + " Confirm success to the patient; calendar/email sync is stubbed."
        )
    return {"facts": facts}


def route_after_doctor(state: TurnState) -> Route:
    """Continue only when service+doctor are valid."""
    if (state.get("facts") or {}).get("status") == "doctor_ok":
        return "available_time"
    return "reply"


def route_after_time(state: TurnState) -> Route:
    """Continue only when a concrete start passed check_start."""
    if (state.get("facts") or {}).get("status") == "slot_ok":
        return "client_id"
    return "reply"


def route_after_identity(state: TurnState) -> Route:
    """Book only when contact is present."""
    if (state.get("facts") or {}).get("status") == "identity_ok":
        return "book"
    return "reply"


def route_after_book(state: TurnState) -> Route:
    """Notify path only after a successful write."""
    if (state.get("facts") or {}).get("status") == "booked":
        return "post_booking"
    return "reply"


def build_book_graph() -> StateGraph:
    """Return an uncompiled booking subgraph builder (nodes + edges)."""
    builder = StateGraph(TurnState, context_schema=ChatContext)
    builder.add_node("doctor_service", doctor_service)
    builder.add_node("available_time", available_time)
    builder.add_node("client_id", client_id)
    builder.add_node("book", book_node)
    builder.add_node("post_booking", post_booking)

    builder.add_edge(START, "doctor_service")
    builder.add_conditional_edges(
        "doctor_service",
        route_after_doctor,
        {"available_time": "available_time", "reply": END},
    )
    builder.add_conditional_edges(
        "available_time",
        route_after_time,
        {"client_id": "client_id", "reply": END},
    )
    builder.add_conditional_edges(
        "client_id",
        route_after_identity,
        {"book": "book", "reply": END},
    )
    builder.add_conditional_edges(
        "book",
        route_after_book,
        {"post_booking": "post_booking", "reply": END},
    )
    builder.add_edge("post_booking", END)
    return builder


def _offer_days(
    facts: TurnFacts, days: list[Any], *, hint: str, band: str | None = None
) -> dict[str, Any]:
    starts: list[str] = []
    times: list[str] = []
    for block in days:
        filtered = filter_starts_by_band(list(block.starts), band)
        for start in filtered or list(block.starts)[:3]:
            starts.append(start.isoformat())
            times.append(f"{block.day.isoformat()} {to_clinic(start).strftime('%H:%M')}")
    facts["status"] = "offer_slots"
    facts["offered_starts"] = starts[:15]
    facts["offered_times"] = times[:15]
    facts["missing"] = ["day", "starts_at"]
    facts["hint"] = hint
    if days:
        facts["resolved_day"] = format_day_label(days[0].day)
    return {"facts": facts}


def _same_service_block(ctx: ChatContext) -> TurnFacts | None:
    """If confirmed email already has the draft service active, stop before booking."""
    if not ctx.can_book_now:
        return None
    email = ctx.patient_email
    code = ctx.draft.service_code
    if not email or not code:
        return None
    try:
        existing = engine_list_patient_appointments(ctx.db, email)
    except BookingError as exc:
        facts = empty_facts()
        facts["status"] = "book_failed"
        facts["error"] = str(exc)
        facts["hint"] = str(exc)
        return facts

    match = next((row for row in existing if row.service_code == code), None)
    if match is None:
        return None

    ctx.draft.starts_at = None
    facts = empty_facts()
    facts["clinic_today"] = format_day_label(ctx.clinic_day())
    requested = parse_day(ctx.draft.day)
    if requested is not None:
        facts["resolved_day"] = format_day_label(requested)
    facts["status"] = "book_failed"
    when = match.starts_at.isoformat()
    requested_note = (
        f" That existing appointment is on {when} — not the day you asked for"
        + (f" ({facts['resolved_day']})" if facts.get("resolved_day") else "")
        + "."
    )
    facts["error"] = (
        f"Contact {email} already has an active service {code} booking "
        f"(#{match.booking_id} starting {when})."
        f"{requested_note} "
        "Log in to cancel/reschedule that booking, or confirm a different email. "
        "I did not create a new booking."
    )
    facts["hint"] = facts["error"]
    facts["booking_id"] = match.booking_id
    return facts
