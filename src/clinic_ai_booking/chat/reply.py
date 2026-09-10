"""Client-facing reply: deterministic for booking facts; LLM only as soft fallback."""

from __future__ import annotations

import json
import logging

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from clinic_ai_booking.chat.facts import TurnFacts, facts_as_dict
from clinic_ai_booking.chat.messages import message_text

logger = logging.getLogger(__name__)

# Statuses where inventing times/ids is unacceptable — never free-form LLM.
_DETERMINISTIC = frozenset(
    {
        "offer_slots",
        "need_info",
        "need_confirm_doctor",
        "need_clarify_day",
        "need_confirm_slot",
        "need_confirm_book",
        "book_failed",
        "booked",
        "slot_ok",
        "identity_ok",
    }
)

REPLY_SYSTEM = """\
You are this clinic's booking assistant. Write one short, clear patient-facing reply.
Use ONLY the facts JSON. Never invent doctors, service codes, times, weekdays, \
booking ids, or success. If offered_times / offered_starts are present, list only those.
If resolved_day or clinic_today is set, use those exact labels.
Prefer short paragraphs and bullet or numbered lists when listing options.
Do not mention JSON, tools, or internal field names.
Do not use ISO timestamps or UTC offsets; use friendly day labels and HH:MM clocks.
"""


def render_reply(
    model: BaseChatModel,
    *,
    user_text: str,
    facts: TurnFacts,
) -> str:
    """Turn facts into one patient-facing message."""
    status = facts.get("status") or ""
    if status in _DETERMINISTIC:
        return _fallback_reply(facts)

    payload = json.dumps(facts_as_dict(facts), ensure_ascii=True)
    try:
        result = model.invoke(
            [
                SystemMessage(content=REPLY_SYSTEM),
                HumanMessage(
                    content=f"User said:\n{user_text}\n\nFacts JSON:\n{payload}"
                ),
            ]
        )
        text = message_text(result).strip()
        if text:
            return text
    except Exception:
        logger.exception("reply LLM failed; using deterministic fallback")
    return _fallback_reply(facts)


def _appointment_summary_lines(facts: TurnFacts) -> list[str]:
    """Professional / service / time lines for confirm and booked replies."""
    pro = (facts.get("professional_name") or "").strip() or "your doctor"
    code = (facts.get("service_code") or "").strip() or "?"
    day = (facts.get("resolved_day") or "").strip()
    times = facts.get("offered_times") or []
    start_clock = times[0] if times else ""
    end_clock = (facts.get("ends_at_clock") or "").strip()
    if day and start_clock and end_clock:
        when = f"{day}, {start_clock}–{end_clock}"
    elif day and start_clock:
        when = f"{day}, {start_clock}"
    else:
        when = day or start_clock or "the chosen time"
    return [
        f"Professional: {pro}",
        f"Service: {code}",
        f"Time: {when} (Taipei, UTC+8)",
    ]


def _duration_note(facts: TurnFacts, avail: dict) -> str:
    """Service + duration label for offer lines."""
    code = (
        (facts.get("service_code") or "").strip()
        or str(avail.get("service_code") or "").strip()
    )
    minutes = facts.get("duration_minutes")
    if minutes is None:
        raw = avail.get("duration_minutes")
        minutes = int(raw) if isinstance(raw, int) else None
    if code and minutes is not None:
        return f"Service {code} ({minutes} min)"
    if minutes is not None:
        return f"{minutes} min"
    if code:
        return f"Service {code}"
    return ""


def _band_lines(window_parts: list[str]) -> list[str]:
    """Turn 'morning 09:00–11:00' parts into bullet lines."""
    lines: list[str] = []
    for part in window_parts:
        if " " in part:
            band, rest = part.split(" ", 1)
            lines.append(f"• {band.capitalize()}: {rest}")
        else:
            lines.append(f"• {part}")
    return lines


def _fallback_reply(facts: TurnFacts) -> str:
    """Deterministic reply when inventing times must not happen."""
    status = facts.get("status") or ""
    day = (facts.get("resolved_day") or "").strip()

    if status == "booked":
        lines = ["Your appointment is booked."]
        lines.extend(_appointment_summary_lines(facts))
        booking_id = facts.get("booking_id")
        if booking_id is not None:
            lines.append(f"Reference: #{booking_id}")
        return "\n".join(lines)
    if status == "book_failed":
        err = (facts.get("error") or "unknown error").strip()
        return f"I could not complete the booking:\n\n{err}"
    if status == "need_confirm_doctor":
        cands = facts.get("doctor_candidates") or []
        err = (facts.get("error") or "").strip()
        if err:
            return err
        listed = "\n".join(cands) if cands else "(see catalog)"
        return (
            "Please confirm which doctor you want:\n"
            f"{listed}\n\n"
            "Reply yes for the first option, or name the doctor."
        )
    if status == "need_clarify_day":
        err = (facts.get("error") or "").strip()
        if err:
            return err
        opts = facts.get("day_options") or []
        if len(opts) >= 2:
            return (
                "Which day did you mean?\n"
                f"1. {opts[0]}\n"
                f"2. {opts[1]}\n\n"
                "Reply 1 or 2, or the date."
            )
        return "Which day did you mean? Please give the date."
    if status == "need_confirm_slot":
        err = (facts.get("error") or "").strip()
        if err:
            return err
        times = facts.get("offered_times") or []
        clock = times[0] if times else "that time"
        return (
            f"We only start on 15-minute times.\n\n"
            f"Shall I use {clock}? Reply yes or name another time."
        )
    if status == "need_confirm_book":
        lines = ["Please confirm your booking:"]
        lines.extend(_appointment_summary_lines(facts))
        lines.append("")
        lines.append("Reply yes to book, or tell me what to change.")
        return "\n".join(lines)
    if status == "offer_slots":
        avail = facts.get("availability") or {}
        day = (facts.get("resolved_day") or "").strip()
        err = (facts.get("error") or "").strip()
        lead = f"{err}\n\n" if err else ""
        duration = _duration_note(facts, avail)
        duration_line = f" for {duration}" if duration else ""

        window_parts: list[str] = []
        bands = avail.get("bands") or {}
        for band_name in ("morning", "afternoon", "evening"):
            band_info = bands.get(band_name) or {}
            wins = band_info.get("start_windows") or []
            if not wins:
                continue
            parts = [f"{a}–{b}" for a, b in wins]
            window_parts.append(f"{band_name} {', '.join(parts)}")
        flat_wins = avail.get("start_windows") or []
        if not window_parts and flat_wins:
            window_parts.append(", ".join(f"{a}–{b}" for a, b in flat_wins))

        next_days = avail.get("next_days") or []
        if len(next_days) > 1:
            day_bits: list[str] = []
            for block in next_days[:3]:
                label = f"{block.get('weekday', '')} {block.get('day', '')}".strip()
                wins = block.get("start_windows") or []
                if wins:
                    ranges = ", ".join(f"{a}–{b}" for a, b in wins)
                    day_bits.append(f"• {label}: {ranges}")
                else:
                    samples = block.get("sample_starts") or []
                    if samples:
                        day_bits.append(
                            f"• {label}: {samples[0]}–{samples[-1]}"
                        )
            body = "\n".join(day_bits) if day_bits else "• no free starts soon"
            return (
                f"{lead}Start times available{duration_line}:\n"
                f"{body}\n\n"
                "What start works?"
            )

        band_block = "\n".join(_band_lines(window_parts)) if window_parts else ""
        if avail.get("suggest_other_days") and day and band_block:
            clinic_today = (facts.get("clinic_today") or "").strip()
            if clinic_today and clinic_today == day:
                return (
                    f"{lead}Later today ({day}) — start times{duration_line}:\n"
                    f"{band_block}\n\n"
                    "Want one of those, or tomorrow / another day?"
                )
            return (
                f"{lead}Next open ({day}) — start times{duration_line}:\n"
                f"{band_block}\n\n"
                "Want one of those, or another day?"
            )
        if day and band_block:
            return (
                f"{lead}Start times on {day}{duration_line}:\n"
                f"{band_block}\n\n"
                "What start works?"
            )
        if band_block:
            return (
                f"{lead}Start times{duration_line}:\n"
                f"{band_block}\n\n"
                "What start works?"
            )
        times = facts.get("offered_times") or []
        listed = ", ".join(times[:3]) if times else "none right now"
        if avail.get("suggest_other_days") and day:
            clinic_today = (facts.get("clinic_today") or "").strip()
            if clinic_today and clinic_today == day:
                return (
                    f"{lead}Later today ({day}) — start times{duration_line}:\n"
                    f"• {listed}\n\n"
                    "Want one of those, or tomorrow / another day?"
                )
            return (
                f"{lead}Next open ({day}) — start times{duration_line}:\n"
                f"• {listed}\n\n"
                "Want one of those, or another day?"
            )
        prefix = f"Start times on {day}" if day else "Start times"
        return (
            f"{lead}{prefix}{duration_line}:\n"
            f"• {listed}\n\n"
            "What start works?"
        )
    if status in {"slot_ok", "identity_ok"}:
        hint = (facts.get("hint") or "").strip()
        return hint or "I can hold that slot once I have your name and email."
    missing = facts.get("missing") or []
    if status == "need_info" or missing:
        err = (facts.get("error") or "").strip()
        if err:
            return err
        hint = (facts.get("hint") or "").strip()
        if hint and "Ask" not in hint:
            return hint
        labels = {
            "service_code": "Which service (A–E)",
            "professional_slug": "Which doctor",
            "day": "Which day",
            "starts_at": "Which listed start time",
            "patient_name": "Your full name",
            "patient_email": "Your email",
        }
        need = [labels.get(m, m) for m in missing] or ["A bit more detail"]
        day_bit = f" for {day}" if day else ""
        bullets = "\n".join(f"• {item}" for item in need)
        extra = ""
        if "professional_slug" in missing:
            pros = facts.get("catalog_professionals") or []
            if pros:
                extra = "\n\nOur doctors:\n" + "\n".join(
                    f"• {p}" for p in pros[:5]
                )
        return f"To continue{day_bit}, please tell me:\n{bullets}{extra}"
    hint = (facts.get("hint") or "").strip()
    if hint:
        return hint
    return "How can I help you book an appointment?"
