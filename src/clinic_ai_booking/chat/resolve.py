"""Resolve catalog ids and start times from draft / extract text."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from clinic_ai_booking.domain.booking import list_professionals, list_services
from clinic_ai_booking.chat.catalog import reject_unknown_catalog
from clinic_ai_booking.domain.hours import TIMEZONE, clinic_datetime, to_clinic

_TIME_BANDS = frozenset({"morning", "afternoon", "evening"})
# Concrete clock only — not "morning" / "11" alone without am/pm or :
_CLOCK_RE = re.compile(
    r"^(?P<h>\d{1,2})(:(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?$",
    re.IGNORECASE,
)
# Compact 1400 / 0930 (not bare "11", not years from ISO dates in longer text).
_HHMM_RE = re.compile(r"^(?P<h>[01]\d|2[0-3])(?P<m>[0-5]\d)$")
_ISO_START_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"
)


def resolve_service_code(session: Session, raw: str | None) -> str | None:
    """Map a service letter to a catalog code, or None if unknown/empty."""
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip().upper()
    if len(cleaned) == 1 and cleaned.isalpha():
        cleaned = cleaned
    err = reject_unknown_catalog(session, service_code=cleaned)
    if err:
        return None
    return cleaned


def resolve_professional_slug(session: Session, raw: str | None) -> str | None:
    """Map a slug or doctor name fragment to a catalog slug."""
    if not raw or not raw.strip():
        return None
    key = _normalize_doctor_key(raw)
    if not key:
        return None
    pros = list_professionals(session)
    for row in pros:
        if row.slug.lower() == key:
            return row.slug
    for row in pros:
        if _normalize_doctor_key(row.name) == key:
            return row.slug
    tokens = [t for t in re.split(r"\W+", key) if t]
    if not tokens:
        return None
    for row in pros:
        name_l = _normalize_doctor_key(row.name)
        slug_l = row.slug.lower()
        if all(t in name_l or t in slug_l for t in tokens):
            return row.slug
    return None


def detect_service_in_text(session: Session, user_text: str) -> str | None:
    """Find service A–E mentioned in free text (does not invent)."""
    text = user_text.lower()
    match = re.search(r"\bservice\s*([a-e])\b", text)
    if match:
        return resolve_service_code(session, match.group(1))
    # "another D on Wednesday" / "book A with"
    match = re.search(
        r"\b(?:another|a|an)\s+([a-e])\b"
        r"|\b([a-e])\s+on\b"
        r"|\b([a-e])\b(?=\s+with\b)"
        r"|\b(?:book|have)\s+(?:a\s+)?([a-e])\b",
        text,
    )
    if match:
        letter = next(g for g in match.groups() if g)
        return resolve_service_code(session, letter)
    return None


def detect_professional_in_text(session: Session, user_text: str) -> str | None:
    """Find a catalog doctor mentioned in free text (name, last name, or slug)."""
    text = user_text.lower()
    pros = list_professionals(session)
    # Prefer longer full-name hits first
    for row in sorted(pros, key=lambda r: len(r.name), reverse=True):
        name_l = row.name.lower()
        if name_l in text:
            return row.slug
        if row.slug.lower() in text.split():  # whole-token slug
            return row.slug
        if re.search(rf"\b{re.escape(row.slug.lower())}\b", text):
            return row.slug
    # Last/first name tokens (skip Dr.)
    for row in pros:
        parts = [
            p
            for p in re.findall(r"[a-z]+", row.name.lower())
            if p not in {"dr", "doctor"} and len(p) >= 3
        ]
        for part in parts:
            if re.search(rf"\b{re.escape(part)}\b", text):
                return row.slug
    return None


def _normalize_doctor_key(raw: str) -> str:
    """Lowercase and strip leading Dr./Doctor for matching."""
    key = raw.strip().lower()
    key = re.sub(r"^(dr\.?|doctor)\s+", "", key).strip()
    key = re.sub(r"\s+", " ", key)
    return key


def catalog_service_lines(session: Session) -> list[str]:
    """Human lines for services A–E."""
    return [
        f"{row.code} ({row.duration_minutes} min"
        + (", seniors only)" if row.seniors_only else ")")
        for row in list_services(session)
    ]


def catalog_professional_lines(session: Session) -> list[str]:
    """Human lines for bookable professionals."""
    return [
        f"{row.name} (slug={row.slug}, {'senior' if row.is_senior else 'junior'})"
        for row in list_professionals(session)
    ]


def parse_day(raw: str | None) -> date | None:
    """Parse YYYY-MM-DD, or None."""
    if not raw or not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def parse_starts_at(raw: str | None, *, day: date | None = None) -> datetime | None:
    """Parse ISO datetime or HH:MM / 1400 (needs day) into clinic-aware datetime."""
    if not is_concrete_clock(raw):
        return None
    text = raw.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TIMEZONE)
        return to_clinic(dt)
    except ValueError:
        pass
    if day is None:
        return None
    normalized = normalize_clock_token(text)
    if normalized is None:
        return None
    hour_s, minute_s = normalized.split(":")
    return clinic_datetime(day, time(int(hour_s), int(minute_s)))


def normalize_clock_token(raw: str) -> str | None:
    """Normalize a single clock token to HH:MM, or None if not a concrete clock."""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if text.lower() in _TIME_BANDS:
        return None
    if _ISO_START_RE.match(text):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return f"{dt.hour:02d}:{dt.minute:02d}"
        except ValueError:
            return None
    compact = text.lower().replace(" ", "")
    hhmm = _HHMM_RE.fullmatch(compact)
    if hhmm:
        return f"{int(hhmm.group('h')):02d}:{hhmm.group('m')}"
    match = _CLOCK_RE.fullmatch(compact)
    if not match:
        return None
    hour = int(match.group("h"))
    minute = int(match.group("m") or "0")
    ampm = match.group("ampm")
    if match.group("m") is None and ampm is None:
        # Bare "11" is too easy for the LLM to invent from "morning"
        return None
    if hour > 23 or minute > 59:
        return None
    if ampm and hour > 12:
        return None
    if ampm == "am" and hour == 12:
        hour = 0
    elif ampm == "pm" and hour < 12:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def is_concrete_clock(raw: str | None) -> bool:
    """True when text is an ISO start or an explicit clock (not 'morning')."""
    if not raw or not raw.strip():
        return False
    text = raw.strip()
    if text.lower() in _TIME_BANDS:
        return False
    if _ISO_START_RE.match(text):
        return True
    return normalize_clock_token(text) is not None


def clock_from_user_text(user_text: str) -> str | None:
    """Pull HH:MM from the user message when they named a clock (incl. 1400)."""
    if not user_text or not user_text.strip():
        return None
    text = user_text.strip()
    # Whole message is a time pick: "1400", "14:00", "2pm"
    whole = normalize_clock_token(text)
    if whole is not None:
        return whole
    lowered = text.lower()
    match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", lowered)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    match = re.search(r"\b([1-9]|1[0-2])\s*(am|pm)\b", lowered)
    if match:
        return normalize_clock_token(f"{match.group(1)}{match.group(2)}")
    # Compact HHMM only as a whole token (avoid matching years in ISO dates).
    match = re.search(r"(?<![\d-])([01]\d|2[0-3])([0-5]\d)(?![\d-])", lowered)
    if match:
        return f"{match.group(1)}:{match.group(2)}"
    return None


def detect_time_band(user_text: str) -> str | None:
    """Return morning/afternoon/evening if the user named a part of day."""
    lowered = user_text.lower()
    if re.search(r"\b(morning)\b", lowered):
        return "morning"
    if re.search(r"\b(afternoon)\b", lowered):
        return "afternoon"
    if re.search(r"\b(evening|night)\b", lowered):
        return "evening"
    return None


# Full weekday names only — abbreviations are the LLM's job to normalize.
_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def resolve_day_intent(
    today: date,
    *,
    day_kind: str | None = None,
    day_absolute: str | None = None,
    weekday: str | None = None,
    weekday_when: str | None = None,
) -> date | None:
    """Compute a calendar date from LLM day intent (not from raw chat regex).

    Examples (today = Monday 2026-09-07):
    - today → 2026-09-07
    - tomorrow → 2026-09-08
    - weekday=friday, weekday_when=next → 2026-09-18 (11 days later)
    - weekday=wednesday, weekday_when=upcoming → 2026-09-09
    """
    kind = (day_kind or "none").strip().lower()
    if kind in {"", "none"}:
        return None
    if kind == "absolute":
        return parse_day(day_absolute)
    if kind == "today":
        return today
    if kind == "tomorrow":
        return today + timedelta(days=1)
    if kind == "day_after_tomorrow":
        return today + timedelta(days=2)
    if kind == "weekday":
        return _date_for_weekday(today, weekday, weekday_when)
    # Legacy: bare ISO in day_absolute without kind
    if day_absolute:
        return parse_day(day_absolute)
    return None


def _date_for_weekday(
    today: date, weekday: str | None, weekday_when: str | None
) -> date | None:
    """Map full weekday name + this/next/upcoming to a date."""
    if not weekday:
        return None
    key = weekday.strip().lower()
    if key not in _WEEKDAY_INDEX:
        return None
    target = _WEEKDAY_INDEX[key]
    when = (weekday_when or "upcoming").strip().lower()
    delta = (target - today.weekday()) % 7
    if when == "next":
        delta = 7 if delta == 0 else delta + 7
    elif when == "this":
        pass  # today if same weekday, else upcoming this week
    # upcoming: same as this
    return today + timedelta(days=delta)


def wants_other_slot(user_text: str) -> bool:
    """True when the user asks for another time (keep day, clear starts_at)."""
    return bool(
        re.search(
            r"\b(other|another|different|else)\b.*\b(time|slot|hour)\b"
            r"|\b(same day)\b",
            user_text.lower(),
        )
    )


def wants_same_professional(user_text: str) -> bool:
    """True when the user refers to the previous doctor (him/her/same)."""
    return bool(
        re.search(
            r"\b(him|her|them|same (doctor|one|person)|with him|with her)\b",
            user_text.lower(),
        )
    )


def wants_same_day_context(user_text: str) -> bool:
    """True when follow-up should keep the last booked day (also / that day)."""
    return bool(
        re.search(r"\b(also|same day|that day|as well)\b", user_text.lower())
    )


def is_explicit_cancel_or_reschedule(user_text: str) -> bool:
    """True only for clear cancel/reschedule wording (not 'other time')."""
    return bool(
        re.search(r"\b(cancel|reschedule|rescheduling)\b", user_text.lower())
    )


def filter_starts_by_band(
    starts: list[datetime], band: str | None
) -> list[datetime]:
    """Keep starts in morning (<12) / afternoon (12–16:59) / evening (17+)."""
    if band not in _TIME_BANDS:
        return starts
    kept: list[datetime] = []
    for start in starts:
        hour = to_clinic(start).hour
        if band == "morning" and hour < 12:
            kept.append(start)
        elif band == "afternoon" and 12 <= hour < 17:
            kept.append(start)
        elif band == "evening" and hour >= 17:
            kept.append(start)
    return kept


def format_day_label(day: date) -> str:
    """Weekday + ISO for patient-facing facts."""
    return f"{day.strftime('%A')} {day.isoformat()}"
