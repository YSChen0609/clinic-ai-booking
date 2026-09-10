"""In-scope booking StateGraph: nodes call booking.py only (no inventing slots)."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from clinic_ai_booking.domain.booking import (
    BookingError,
    book_appointment as engine_book,
    booking_created_dict,
    check_start as engine_check_start,
    day_availability_dict,
    explain_can_perform,
    get_service_duration,
    list_available_starts as engine_list_available_starts,
    list_next_available_starts as engine_list_next_available_starts,
    list_patient_appointments as engine_list_patient_appointments,
    list_professionals,
    seniors_for_service,
    snap_to_nearest_start,
    summarize_day_availability,
)
from clinic_ai_booking.chat.context import ChatContext
from clinic_ai_booking.chat.facts import TurnFacts, empty_facts
from clinic_ai_booking.chat.resolve import (
    catalog_professional_lines,
    catalog_service_lines,
    filter_starts_by_band,
    format_day_label,
    is_concrete_clock,
    parse_day,
    parse_starts_at,
)
from clinic_ai_booking.chat.state import TurnState
from clinic_ai_booking.domain.hours import (
    explain_clinic_day,
    format_patient_clock,
    format_patient_span,
    on_start_grid,
    to_clinic,
)

logger = logging.getLogger(__name__)

Route = Literal["reply", "available_time", "client_id", "book", "post_booking"]
_MAX_OFFER_SAMPLES = 3


def doctor_service(state: TurnState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    """Validate or request service + professional via the booking engine."""
    ctx = runtime.context
    facts = empty_facts()
    facts["clinic_today"] = format_day_label(ctx.clinic_day())
    facts["catalog_services"] = catalog_service_lines(ctx.db)
    facts["catalog_professionals"] = catalog_professional_lines(
        ctx.db, service_code=ctx.draft.service_code
    )
    if ctx.draft.day:
        facts["resolved_day"] = format_day_label(parse_day(ctx.draft.day) or ctx.clinic_day())
    if ctx.draft.time_band:
        facts["time_band"] = ctx.draft.time_band

    if ctx.draft.day_clarify_options and not ctx.draft.day:
        opts = ctx.draft.day_clarify_options
        facts["status"] = "need_clarify_day"
        facts["day_options"] = list(opts)
        facts["missing"] = ["day"]
        labels = [format_day_label(parse_day(o) or ctx.clinic_day()) for o in opts]
        facts["hint"] = (
            "Ask which Friday (or weekday) they mean using day_options only."
        )
        facts["error"] = (
            f"Did you mean {labels[0]} (this upcoming week) or "
            f"{labels[1] if len(labels) > 1 else 'the following week'}? "
            "Reply 1 or 2, or the date."
        )
        return {"facts": facts}

    missing: list[str] = []
    if not ctx.draft.service_code:
        missing.append("service_code")

    # Ambiguous / unconfirmed candidates must be confirmed before times.
    # Exact unique names are already locked in extract (confirmed=True).
    if ctx.draft.professional_candidates and (
        not ctx.draft.professional_confirmed or not ctx.draft.professional_slug
    ):
        facts["status"] = "need_confirm_doctor"
        facts["doctor_candidates"] = _candidate_lines(ctx)
        facts["missing"] = ["professional_confirm"]
        suggested = None
        if ctx.draft.professional_slug:
            suggested = _professional_name(ctx, ctx.draft.professional_slug)
        if suggested and not ctx.draft.professional_confirmed:
            facts["hint"] = f"Confirm doctor {ctx.draft.professional_slug}."
            facts["error"] = (
                f"Did you mean {suggested}?\n\n"
                "Or pick one of these:\n"
                + "\n".join(facts["doctor_candidates"])
                + "\n\nReply yes, a number, or a name — "
                "or choose a different service (A–E)."
            )
        elif ctx.draft.professional_slug:
            facts["hint"] = (
                f"Confirm doctor {ctx.draft.professional_slug} "
                "(or pick another from doctor_candidates)."
            )
        else:
            facts["hint"] = (
                "Doctor name is ambiguous. Ask which candidate from "
                "doctor_candidates they want."
            )
            facts["error"] = (
                "Please choose a doctor:\n"
                + "\n".join(facts["doctor_candidates"])
                + "\n\nReply with a number or name, "
                "or choose a different service (A–E)."
            )
        return {"facts": facts}

    if not ctx.draft.professional_slug:
        missing.append("professional_slug")
    elif not ctx.draft.professional_confirmed:
        facts["status"] = "need_confirm_doctor"
        facts["doctor_candidates"] = _candidate_lines(ctx) or [
            ctx.draft.professional_slug
        ]
        facts["missing"] = ["professional_confirm"]
        facts["hint"] = (
            f"Confirm doctor {ctx.draft.professional_slug} before offering times."
        )
        return {"facts": facts}

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
        seniors = seniors_for_service(ctx.db, ctx.draft.service_code or "")
        ctx.draft.professional_slug = None
        ctx.draft.professional_confirmed = False
        ctx.draft.professional_candidates = [row.slug for row in seniors]
        if seniors:
            facts["status"] = "need_confirm_doctor"
            facts["doctor_candidates"] = _candidate_lines(ctx)
            facts["missing"] = ["professional_confirm"]
            facts["error"] = (
                f"{reason}\n\n"
                "Seniors who can do this service:\n"
                + "\n".join(facts["doctor_candidates"])
                + "\n\nReply with a number or name, "
                "or choose a different service (A–E)."
            )
            facts["hint"] = facts["error"]
        else:
            facts["status"] = "need_info"
            facts["missing"] = ["professional_slug", "service_code"]
            facts["error"] = (
                f"{reason}\n\n"
                "No senior is available for that service — "
                "please pick a different service."
            )
            facts["hint"] = facts["error"]
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
    facts["catalog_professionals"] = catalog_professional_lines(
        ctx.db, service_code=ctx.draft.service_code
    )
    if ctx.draft.time_band:
        facts["time_band"] = ctx.draft.time_band
    slug = ctx.draft.professional_slug
    code = ctx.draft.service_code
    assert slug and code

    day = parse_day(ctx.draft.day)
    start = parse_starts_at(ctx.draft.starts_at, day=day)
    # Bare clock without a day → prefer last booked day, else clinic today.
    if (
        start is None
        and day is None
        and is_concrete_clock(ctx.draft.starts_at)
    ):
        day = parse_day(ctx.memory.last_day) or ctx.clinic_day()
        ctx.draft.day = day.isoformat()
        start = parse_starts_at(ctx.draft.starts_at, day=day)

    if start is not None:
        snapped = snap_to_nearest_start(ctx.db, slug, code, day or to_clinic(start).date(), start)
        if snapped is None:
            facts["error"] = "That time is not free; pick another start in the windows below."
            day = day or to_clinic(start).date()
            ctx.draft.starts_at = None
            ctx.draft.starts_at_confirmed = False
        else:
            local_req = to_clinic(start)
            local_snap = to_clinic(snapped)
            exact_grid = on_start_grid(start) and local_req.strftime("%H:%M") == local_snap.strftime(
                "%H:%M"
            )
            ctx.draft.day = local_snap.date().isoformat()
            ctx.draft.starts_at = snapped.isoformat()
            if exact_grid or ctx.draft.starts_at_confirmed:
                ctx.draft.starts_at_confirmed = True
                try:
                    plan = engine_check_start(
                        ctx.db,
                        professional_slug=slug,
                        service_code=code,
                        starts_at=snapped,
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
                    day = to_clinic(snapped).date()
                    ctx.draft.starts_at = None
                    ctx.draft.starts_at_confirmed = False
            else:
                clock = local_snap.strftime("%H:%M")
                facts["status"] = "need_confirm_slot"
                facts["resolved_day"] = format_day_label(local_snap.date())
                facts["offered_starts"] = [snapped.isoformat()]
                facts["offered_times"] = [clock]
                facts["missing"] = ["starts_at"]
                facts["hint"] = f"Confirm snapped start {clock}."
                facts["error"] = (
                    f"We only start on 15-minute times. Closest free start is {clock} "
                    f"(you said {local_req.strftime('%H:%M')}). Reply yes to use {clock}, "
                    "or name another time in the free windows."
                )
                return {"facts": facts}

    if day is None:
        return _offer_today_first(facts, ctx, slug, code, band=ctx.draft.time_band)

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
            summary = summarize_day_availability(ctx.db, slug, code, day, sample_per_band=2)
            avail = day_availability_dict(summary)
            samples = _samples_from_availability(avail, band=None)
            facts["status"] = "offer_slots"
            facts["availability"] = avail
            facts["offered_starts"] = samples["isos"]
            facts["offered_times"] = samples["clocks"]
            facts["missing"] = ["starts_at"]
            facts["time_band"] = ""
            facts["error"] = f"No {wanted} starts on {format_day_label(day)}"
            facts["hint"] = (
                f"{facts['error']}. "
                "Show windows/samples from availability, or ask another day."
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
    summary = summarize_day_availability(
        ctx.db,
        slug,
        code,
        day,
        time_band=ctx.draft.time_band,
        sample_per_band=2,
    )
    avail = day_availability_dict(summary)
    samples = _samples_from_availability(avail, band=ctx.draft.time_band)
    facts["status"] = "offer_slots"
    facts["availability"] = avail
    facts["offered_starts"] = samples["isos"]
    facts["offered_times"] = samples["clocks"]
    band_note = f" ({ctx.draft.time_band})" if ctx.draft.time_band else ""
    facts["hint"] = (
        f"Offer start windows plus at most {_MAX_OFFER_SAMPLES} sample clocks for "
        f"{format_day_label(day)}{band_note} ({summary.weekday}). "
        "Say starts are on a 15-minute grid. Do not invent times."
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
    """Confirm with the patient, then write through booking.book_appointment."""
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

    if not ctx.draft.book_confirmed:
        _fill_appointment_summary(facts, ctx, slug=slug, code=code, start=start)
        facts["status"] = "need_confirm_book"
        facts["hint"] = (
            f"Ask the patient to confirm booking service {code} with {slug} "
            f"on {facts['resolved_day']} at {(facts.get('offered_times') or [''])[0]} "
            f"for {ctx.patient_name} <{ctx.patient_email}>. "
            "Reply yes to book, or change a detail."
        )
        facts["missing"] = ["book_confirm"]
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
        _fill_appointment_summary(
            facts,
            ctx,
            slug=slug,
            code=code,
            start=booking.starts_at,
            end=booking.ends_at,
        )
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


def _offer_today_first(
    facts: TurnFacts,
    ctx: ChatContext,
    slug: str,
    code: str,
    *,
    band: str | None,
) -> dict[str, Any]:
    """When day is missing: offer today if free, else next open day; invite other days."""
    today = ctx.clinic_day()
    try:
        today_starts = engine_list_available_starts(ctx.db, slug, code, today)
    except BookingError as exc:
        facts["status"] = "need_info"
        facts["error"] = str(exc)
        facts["hint"] = str(exc)
        facts["missing"] = ["day"]
        return {"facts": facts}

    if today_starts:
        offer_day = today
    else:
        try:
            nxt = engine_list_next_available_starts(
                ctx.db, slug, code, today, limit_days=1, limit_starts_per_day=5
            )
        except BookingError as exc:
            facts["status"] = "need_info"
            facts["error"] = str(exc)
            facts["hint"] = str(exc)
            facts["missing"] = ["day"]
            return {"facts": facts}
        if not nxt:
            facts["status"] = "need_info"
            facts["error"] = "No free starts in the next two weeks."
            facts["hint"] = facts["error"]
            facts["missing"] = ["day"]
            return {"facts": facts}
        offer_day = nxt[0].day

    ctx.draft.day = offer_day.isoformat()
    facts["resolved_day"] = format_day_label(offer_day)
    starts = filter_starts_by_band(
        engine_list_available_starts(ctx.db, slug, code, offer_day), band
    )
    if not starts and band:
        # Same as named-day path: show the day's windows and drop the band filter.
        wanted = band
        summary = summarize_day_availability(
            ctx.db, slug, code, offer_day, sample_per_band=2
        )
        avail = day_availability_dict(summary)
        avail["suggest_other_days"] = True
        samples = _samples_from_availability(avail, band=None)
        facts["status"] = "offer_slots"
        facts["availability"] = avail
        facts["offered_starts"] = samples["isos"]
        facts["offered_times"] = samples["clocks"]
        facts["missing"] = ["starts_at"]
        facts["time_band"] = ""
        facts["error"] = f"No {wanted} starts on {format_day_label(offer_day)}"
        facts["hint"] = (
            f"{facts['error']}. Show windows and ask if they want tomorrow or another day."
        )
        return {"facts": facts}

    summary = summarize_day_availability(
        ctx.db,
        slug,
        code,
        offer_day,
        time_band=band,
        sample_per_band=2,
    )
    avail = day_availability_dict(summary)
    avail["suggest_other_days"] = True
    samples = _samples_from_availability(avail, band=band)
    facts["status"] = "offer_slots"
    facts["availability"] = avail
    facts["offered_starts"] = samples["isos"]
    facts["offered_times"] = samples["clocks"]
    facts["missing"] = ["starts_at"]
    label = "today" if offer_day == today else "the next open day"
    facts["hint"] = (
        f"Offer start windows for {label} ({format_day_label(offer_day)}). "
        "Ask if they want one of those times, or tomorrow / another day. "
        "Do not invent times."
    )
    return {"facts": facts}


def _offer_days(
    facts: TurnFacts, days: list[Any], *, hint: str, band: str | None = None
) -> dict[str, Any]:
    starts: list[str] = []
    times: list[str] = []
    next_days: list[dict[str, Any]] = []
    for block in days:
        filtered = filter_starts_by_band(list(block.starts), band) or list(block.starts)
        windows = _clock_windows(filtered)
        for start in filtered[:_MAX_OFFER_SAMPLES]:
            starts.append(start.isoformat())
            times.append(to_clinic(start).strftime("%H:%M"))
        next_days.append(
            {
                "day": block.day.isoformat(),
                "weekday": block.day.strftime("%A"),
                "start_windows": windows,
                "sample_starts": [
                    to_clinic(s).strftime("%H:%M") for s in filtered[:_MAX_OFFER_SAMPLES]
                ],
            }
        )
    facts["status"] = "offer_slots"
    facts["offered_starts"] = starts[:_MAX_OFFER_SAMPLES]
    facts["offered_times"] = times[:_MAX_OFFER_SAMPLES]
    facts["missing"] = ["day", "starts_at"]
    facts["hint"] = hint
    if days:
        facts["resolved_day"] = format_day_label(days[0].day)
        first_windows = next_days[0]["start_windows"] if next_days else []
        facts["availability"] = {
            "day": days[0].day.isoformat(),
            "weekday": days[0].day.strftime("%A"),
            "bands": {
                "morning": {"start_windows": [], "sample_starts": []},
                "afternoon": {"start_windows": [], "sample_starts": []},
                "evening": {"start_windows": [], "sample_starts": []},
            },
            "next_days": next_days,
            "start_windows": first_windows,
        }
    return {"facts": facts}


def _candidate_lines(ctx: ChatContext) -> list[str]:
    """Human lines for pending doctor candidates."""
    by_slug = {row.slug: row for row in list_professionals(ctx.db)}
    lines: list[str] = []
    slugs = ctx.draft.professional_candidates or (
        [ctx.draft.professional_slug] if ctx.draft.professional_slug else []
    )
    for i, slug in enumerate(slugs, start=1):
        row = by_slug.get(slug)
        if row is None:
            lines.append(f"{i}. {slug}")
        else:
            level = "senior" if row.is_senior else "junior"
            lines.append(f"{i}. {row.name} ({level})")
    return lines


def _clock_windows(starts: list[Any]) -> list[list[str]]:
    """Collapse sorted starts into inclusive HH:MM windows on the 15-minute grid."""
    if not starts:
        return []
    local = sorted(to_clinic(s) for s in starts)
    windows: list[list[str]] = []
    run_start = local[0]
    prev = local[0]
    step = 15 * 60
    for cur in local[1:]:
        if (cur - prev).total_seconds() > step:
            windows.append(
                [run_start.strftime("%H:%M"), prev.strftime("%H:%M")]
            )
            run_start = cur
        prev = cur
    windows.append([run_start.strftime("%H:%M"), prev.strftime("%H:%M")])
    return windows


def _samples_from_availability(
    avail: dict[str, Any], *, band: str | None
) -> dict[str, list[str]]:
    """Flatten Plan B samples into offered_times / ISO starts for replies."""
    day = str(avail.get("day") or "")
    bands = avail.get("bands") or {}
    clocks: list[str] = []
    if band in bands:
        clocks.extend(list((bands[band] or {}).get("sample_starts") or []))
    else:
        for name in ("morning", "afternoon", "evening"):
            clocks.extend(list((bands.get(name) or {}).get("sample_starts") or []))
    clocks = clocks[:_MAX_OFFER_SAMPLES]
    isos = [f"{day}T{c}:00+08:00" for c in clocks if day and c]
    return {"clocks": clocks, "isos": isos}


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
    when = format_patient_span(match.starts_at, match.ends_at)
    requested_note = (
        f" That existing appointment is on {when} — not the day you asked for"
        + (f" ({facts['resolved_day']})" if facts.get("resolved_day") else "")
        + "."
    )
    manage = (
        "Cancel or reschedule that booking on the website"
        if ctx.is_authenticated
        else "Log in on the website to cancel or reschedule that booking, "
        "or use a different email"
    )
    facts["error"] = (
        f"You already have an upcoming service {code} booking "
        f"(#{match.booking_id}, {when})."
        f"{requested_note} "
        f"{manage}. I did not create a new booking."
    )
    facts["hint"] = facts["error"]
    facts["booking_id"] = match.booking_id
    return facts


def _professional_name(ctx: ChatContext, slug: str) -> str:
    """Resolve catalog display name for a professional slug."""
    for row in list_professionals(ctx.db):
        if row.slug == slug:
            return row.name
    return slug


def _fill_appointment_summary(
    facts: TurnFacts,
    ctx: ChatContext,
    *,
    slug: str,
    code: str,
    start: object,
    end: object | None = None,
) -> None:
    """Attach patient-facing confirm/booked fields to facts."""
    local = to_clinic(start)  # type: ignore[arg-type]
    duration = get_service_duration(ctx.db, code)
    if end is None:
        end = start + timedelta(minutes=duration)  # type: ignore[operator]
    facts["resolved_day"] = format_day_label(local.date())
    facts["offered_starts"] = [start.isoformat()]  # type: ignore[union-attr]
    facts["offered_times"] = [format_patient_clock(start)]  # type: ignore[arg-type]
    facts["ends_at_clock"] = format_patient_clock(end)  # type: ignore[arg-type]
    facts["professional_name"] = _professional_name(ctx, slug)
    facts["service_code"] = code
    facts["duration_minutes"] = duration
