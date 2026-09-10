"""Catalog resolve helpers, day/clock parsing, and soft-context detectors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from difflib import SequenceMatcher

from dateparser import parse as dateparser_parse
from sqlalchemy.orm import Session

from clinic_ai_booking.chat.aliases import normalize_professional_alias
from clinic_ai_booking.chat.catalog import reject_unknown_catalog
from clinic_ai_booking.domain.booking import (
    explain_can_perform,
    list_professionals,
    list_services,
)
from clinic_ai_booking.domain.hours import TIMEZONE, clinic_datetime, to_clinic

_TIME_BANDS = frozenset({"morning", "afternoon", "evening"})
# Concrete clock only — not "morning" / "11" alone without am/pm or :
_CLOCK_RE = re.compile(
    r"^(?P<h>\d{1,2})(:(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?$",
    re.IGNORECASE,
)
# Compact 1400 / 0930 (not bare "11", not years from ISO dates in longer text).
_HHMM_RE = re.compile(r"^(?P<h>[01]\d|2[0-3])(?P<m>[0-5]\d)$")
_ISO_START_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")

# Closest-name matching for doctor confirm flow.
_MIN_DOCTOR_SCORE = 0.55
_AMBIGUOUS_SCORE_GAP = 0.08
# Lock without "did you mean?" only at this score or via alias / numbered pick.
_EXACT_DOCTOR_SCORE = 0.9

_WEEKDAY_NAMES = frozenset(
    {
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    }
)


@dataclass(frozen=True)
class ProfessionalMatch:
    """One ranked catalog doctor for confirm-before-lock."""

    slug: str
    name: str
    score: float
    is_senior: bool


def resolve_service_code(session: Session, raw: str | None) -> str | None:
    """Map a service letter to a catalog code, or None if unknown/empty."""
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip().upper()
    err = reject_unknown_catalog(session, service_code=cleaned)
    if err:
        return None
    return cleaned


def resolve_professional_slug(session: Session, raw: str | None) -> str | None:
    """Map a slug or doctor name to a unique catalog slug, or None if ambiguous."""
    matches = rank_professionals(session, raw)
    if len(matches) == 1:
        return matches[0].slug
    if not matches:
        return None
    if professional_match_is_ambiguous(matches):
        return None
    return matches[0].slug


def rank_professionals(session: Session, raw: str | None) -> list[ProfessionalMatch]:
    """Return catalog doctors ranked by closeness to the user phrase."""
    if not raw or not raw.strip():
        return []
    alias_slug = normalize_professional_alias(raw)
    if alias_slug:
        for row in list_professionals(session):
            if row.slug == alias_slug:
                return [
                    ProfessionalMatch(
                        slug=row.slug,
                        name=row.name,
                        score=1.0,
                        is_senior=row.is_senior,
                    )
                ]
    key = _normalize_doctor_key(raw)
    if not key:
        return []
    scored: list[ProfessionalMatch] = []
    for row in list_professionals(session):
        score = _doctor_match_score(key, row.slug, row.name)
        if score < _MIN_DOCTOR_SCORE:
            continue
        scored.append(
            ProfessionalMatch(
                slug=row.slug,
                name=row.name,
                score=score,
                is_senior=row.is_senior,
            )
        )
    scored.sort(key=lambda m: (-m.score, m.slug))
    return scored


def professional_match_is_ambiguous(matches: list[ProfessionalMatch]) -> bool:
    """True when top matches are too close to auto-pick one."""
    if len(matches) < 2:
        return False
    return matches[0].score - matches[1].score < _AMBIGUOUS_SCORE_GAP


def professional_match_is_exact(match: ProfessionalMatch) -> bool:
    """True when score is high enough to lock without a yes-confirm."""
    return match.score >= _EXACT_DOCTOR_SCORE


def is_doctor_confirm_phrase(user_text: str) -> bool:
    """True when the user affirms the proposed doctor."""
    lowered = user_text.lower().strip()
    if re.fullmatch(r"(yes|yeah|yep|y|ok|okay|sure|confirm|correct|right)\.?", lowered):
        return True
    return bool(
        re.search(
            r"\b(yes|yeah|yep|ok|okay|sure|confirm|correct|that one|this one|"
            r"the first|number\s*1|#\s*1)\b",
            lowered,
        )
    )


def is_doctor_reject_phrase(user_text: str) -> bool:
    """True when the user rejects the proposed doctor."""
    return bool(
        re.search(
            r"\b(no|nope|wrong|other doctor|different doctor|not (him|her|that))\b",
            user_text.lower(),
        )
    )


def _doctor_match_score(key: str, slug: str, name: str) -> float:
    """Score how well key matches a catalog doctor (1.0 = exact)."""
    slug_l = slug.lower()
    name_l = _normalize_doctor_key(name)
    if key == slug_l or key == name_l:
        return 1.0
    if key in {"junior", "senior"}:
        # Role words must match the slug token — avoid "senior"≈"junior" fuzzy hits.
        return 0.85 if key in slug_l else 0.0
    if key in slug_l or key in name_l:
        return 0.92
    tokens = [t for t in re.split(r"\W+", key) if t]
    name_tokens = [
        t for t in re.split(r"\W+", name_l) if t and t not in {"dr", "doctor"}
    ]
    if tokens and all(t in name_l or t in slug_l for t in tokens):
        return 0.88
    best = SequenceMatcher(None, key, name_l).ratio()
    best = max(best, SequenceMatcher(None, key, slug_l).ratio())
    for part in name_tokens:
        if len(part) < 3:
            continue
        best = max(best, SequenceMatcher(None, key, part).ratio())
        for tok in tokens:
            if len(tok) >= 3:
                best = max(best, SequenceMatcher(None, tok, part).ratio())
    return best


def detect_service_in_text(session: Session, user_text: str) -> str | None:
    """Find service A–E mentioned in free text (does not invent)."""
    text = user_text.lower()
    match = re.search(r"\bservice\s*([a-e])\b", text)
    if match:
        return resolve_service_code(session, match.group(1))
    match = re.search(
        r"\b(?:another|a|an)\s+([a-e])\b"
        r"|\b([a-e])\s+on\b"
        r"|\b([a-e])\b(?=\s+with\b)"
        r"|\b(?:book|have|want|need)\s+(?:a\s+|an\s+)?([a-e])\b",
        text,
    )
    if match:
        letter = next(g for g in match.groups() if g)
        return resolve_service_code(session, letter)
    return None


def detect_unknown_service_letter(user_text: str) -> str | None:
    """Return a letter outside A–E when the user clearly named that service."""
    from clinic_ai_booking.chat.aliases import SERVICE_CODES

    text = user_text.lower()
    # Explicit "service F" / "services F"
    for match in re.finditer(r"\bservices?\s*([a-z])\b", text):
        letter = match.group(1).upper()
        if letter not in SERVICE_CODES:
            return letter
    # "I want F" / "book F" / "need a F" — only letters outside A–E
    match = re.search(
        r"\b(?:want|book|have|need|like)\s+(?:a\s+|an\s+|the\s+)?"
        r"(?:service\s+)?([f-z])\b",
        text,
    )
    if match:
        return match.group(1).upper()
    # Bare "F" as the whole message (or with punctuation)
    bare = re.fullmatch(r"\s*([f-z])\s*[.!]?\s*", text)
    if bare:
        return bare.group(1).upper()
    return None


def unknown_service_message(session: Session, letter: str) -> str:
    """Patient-facing reject when the named service is not in the catalog."""
    lines = catalog_service_lines(session)
    listed = "\n".join(f"• {line}" for line in lines) if lines else "• A–E"
    return (
        f"We do not offer service {letter.upper()}.\n\n"
        f"Available services:\n{listed}\n\n"
        "Which service would you like (A–E)?"
    )


def doctor_suggest_followup(session: Session, *, slug: str | None) -> str:
    """Extra line when a fuzzy doctor is pending confirmation."""
    if not slug:
        return ""
    for row in list_professionals(session):
        if row.slug == slug:
            return (
                f"\n\nAlso, did you mean {row.name}? "
                "After you pick a service, reply yes or that name to confirm the doctor."
            )
    return ""



def detect_professional_in_text(session: Session, user_text: str) -> str | None:
    """Find a unique catalog doctor in free text, or None if missing/ambiguous."""
    text = user_text.lower()
    pros = list_professionals(session)
    hits: list[str] = []
    for row in sorted(pros, key=lambda r: len(r.name), reverse=True):
        name_l = row.name.lower()
        if name_l in text:
            hits.append(row.slug)
            continue
        if re.search(rf"\b{re.escape(row.slug.lower())}\b", text):
            hits.append(row.slug)
            continue
        parts = [
            p
            for p in re.findall(r"[a-z]+", row.name.lower())
            if p not in {"dr", "doctor"} and len(p) >= 3
        ]
        for part in parts:
            if re.search(rf"\b{re.escape(part)}\b", text):
                hits.append(row.slug)
                break
    if re.search(r"\bjunior\b", text) and "junior" not in hits:
        hits.append("junior")
    if re.search(r"\bsenior\b", text):
        for row in pros:
            if row.is_senior and row.slug not in hits:
                hits.append(row.slug)
    uniq = list(dict.fromkeys(hits))
    if len(uniq) == 1:
        return uniq[0]
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


def catalog_professional_lines(
    session: Session, service_code: str | None = None
) -> list[str]:
    """Human lines for bookable professionals; optional service filters by can_perform."""
    rows = list_professionals(session)
    if service_code:
        rows = [
            row
            for row in rows
            if explain_can_perform(session, row.slug, service_code)[0]
        ]
    return [
        f"{row.name} ({'senior' if row.is_senior else 'junior'})" for row in rows
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
    minute = int(match.group("m") or 0)
    ampm = (match.group("ampm") or "").lower()
    # Bare hour (no :mm, no am/pm): allow 0 and 3–23 as HH:00.
    # Reject 1 and 2 alone — those are doctor/day menu picks ("1" / "2").
    if match.group("m") is None and not ampm:
        if hour in (1, 2) or hour > 23:
            return None
        return f"{hour:02d}:00"
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
    """Pull HH:MM from the user message when they named a clock (incl. 19 / 1400)."""
    if not user_text or not user_text.strip():
        return None
    text = user_text.strip()
    whole = normalize_clock_token(text)
    if whole is not None:
        return whole

    # "let's do 19" / "at 9" / "do 14:30" / "book 19"
    spoken = re.search(
        r"(?:let'?s\s+)?(?:do|at|for|from|around|book|take|pick)\s+"
        r"(\d{1,2})(?::(\d{2}))?\b(?:\s*([ap]m))?",
        text,
        re.I,
    )
    if spoken:
        hour = int(spoken.group(1))
        minute = int(spoken.group(2) or 0)
        ampm = (spoken.group(3) or "").lower()
        token = f"{hour}:{minute:02d}{ampm}" if spoken.group(2) else (
            f"{hour}{ampm}" if ampm else str(hour)
        )
        normalized = normalize_clock_token(token)
        if normalized is not None:
            return normalized
        # "do 1" / "do 2" with no am/pm — still allow as 01:00 / 02:00 when spoken.
        if not spoken.group(2) and not ampm and 0 <= hour <= 23 and minute == 0:
            return f"{hour:02d}:00"

    for token in re.findall(
        r"\b\d{1,2}:\d{2}\b|\b\d{3,4}\b|\b\d{1,2}\s*[ap]m\b", text, re.I
    ):
        normalized = normalize_clock_token(token)
        if normalized is not None:
            return normalized
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


def parse_clinic_day(text: str, today: date) -> date | None:
    """Resolve a natural-language or ISO day phrase with dateparser (Asia/Taipei)."""
    if not text or not text.strip():
        return None
    cleaned = _normalize_day_phrase(text)
    if not cleaned:
        return None
    absolute = parse_day(cleaned)
    if absolute is not None:
        return absolute
    base = datetime.combine(today, time(12, 0), tzinfo=TIMEZONE)
    parsed = dateparser_parse(
        cleaned,
        languages=["en"],
        settings={
            "RELATIVE_BASE": base,
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": False,
            "TIMEZONE": "Asia/Taipei",
        },
    )
    if parsed is None:
        return None
    return parsed.date()


def date_cheat_sheet(today: date, *, days: int = 14) -> str:
    """Build a Current→future weekday→ISO map for the extract system prompt."""
    if days < 1:
        days = 1
    lines = [
        f"Date cheat sheet (clinic Asia/Taipei; today={today.isoformat()} "
        f"{today.strftime('%A')}). Use these ISO values only as hints — "
        "still set day_kind/weekday fields or day_phrase; never invent other dates:"
    ]
    for offset in range(days):
        day = today + timedelta(days=offset)
        label = "today" if offset == 0 else ("tomorrow" if offset == 1 else "")
        suffix = f" ({label})" if label else ""
        lines.append(f"  - {day.strftime('%A')} {day.isoformat()}{suffix}")
    return "\n".join(lines)


def resolve_day_intent(
    today: date,
    *,
    day_kind: str | None = None,
    day_absolute: str | None = None,
    weekday: str | None = None,
    weekday_when: str | None = None,
) -> date | None:
    """Compute a calendar date from LLM day intent via dateparser."""
    kind = (day_kind or "none").strip().lower()
    if kind in {"", "none"}:
        return None
    if kind == "absolute":
        return parse_day(day_absolute)
    if kind == "today":
        return today
    if kind == "tomorrow":
        return parse_clinic_day("tomorrow", today)
    if kind == "day_after_tomorrow":
        return parse_clinic_day("day after tomorrow", today)
    if kind == "weekday":
        return _weekday_from_intent(today, weekday, weekday_when)
    if day_absolute:
        return parse_day(day_absolute)
    return None


def _normalize_day_phrase(text: str) -> str:
    """Normalize chat shorthand before dateparser (tmr → tomorrow)."""
    cleaned = " ".join(text.strip().lower().split())
    cleaned = re.sub(r"\b(tmr|tmrw|tomorow)\b", "tomorrow", cleaned)
    cleaned = re.sub(r"\bnext\s+next\s+", "in two weeks on ", cleaned)
    return cleaned


def _weekday_from_intent(
    today: date, weekday: str | None, weekday_when: str | None
) -> date | None:
    """Map weekday + this/next/upcoming/week_after_next using dateparser anchors."""
    if not weekday:
        return None
    key = weekday.strip().lower()
    if key not in _WEEKDAY_NAMES:
        return None
    when = (weekday_when or "upcoming").strip().lower()
    if when == "this" and today.strftime("%A").lower() == key:
        return today
    if when in {"this", "upcoming", ""}:
        return _weekday_on_or_after(today, key, today)
    if when == "next":
        return _weekday_on_or_after(today, key, today + timedelta(days=7))
    if when == "week_after_next":
        return _weekday_on_or_after(today, key, today + timedelta(days=14))
    return _weekday_on_or_after(today, key, today)


def _weekday_on_or_after(today: date, weekday: str, start: date) -> date | None:
    """First weekday on or after start, resolved with dateparser."""
    del today  # anchor is start; today kept for call-site clarity
    if start.strftime("%A").lower() == weekday:
        return start
    base = datetime.combine(start, time(12, 0), tzinfo=TIMEZONE)
    parsed = dateparser_parse(
        weekday,
        languages=["en"],
        settings={
            "RELATIVE_BASE": base,
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": False,
            "TIMEZONE": "Asia/Taipei",
        },
    )
    if parsed is None:
        return None
    return parsed.date()


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
            r"\b(him|her|them|same (doctor|one|person)|"
            r"with him|with her|same (doc|physician))\b",
            user_text.lower(),
        )
    )


def wants_same_day_context(user_text: str) -> bool:
    """True when follow-up keeps the previous day without renaming it."""
    return bool(
        re.search(
            r"\b(same day|that day|also|"
            r"same (monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
            r"that (monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
            r"the same (monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
            user_text.lower(),
        )
    )


def is_ambiguous_next_weekday(user_text: str) -> bool:
    """True when the user said 'next <weekday>' (needs this-week vs next-week ask)."""
    return bool(
        re.search(
            r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            user_text.lower(),
        )
    )


def next_weekday_choices(today: date, weekday: str) -> tuple[date, date] | None:
    """Return (upcoming weekday, that weekday next week) for a clarify prompt."""
    key = weekday.strip().lower()
    if key not in _WEEKDAY_NAMES:
        return None
    upcoming = _weekday_on_or_after(today, key, today)
    following = _weekday_on_or_after(today, key, today + timedelta(days=7))
    if upcoming is None or following is None:
        return None
    return upcoming, following


def pick_day_clarify_option(
    user_text: str, options: list[str]
) -> str | None:
    """Map '1'/'2', ISO date, or M/D to a pending day-clarify ISO option."""
    if not options:
        return None
    lowered = user_text.lower().strip()
    index_match = re.fullmatch(r"#?\s*([12])\.?", lowered)
    if index_match:
        idx = int(index_match.group(1)) - 1
        if 0 <= idx < len(options):
            return options[idx]
    for opt in options:
        if opt in lowered:
            return opt
        try:
            day = date.fromisoformat(opt)
        except ValueError:
            continue
        # 9/18 or 09-18 style
        if re.search(
            rf"\b0?{day.month}[/-]0?{day.day}\b",
            lowered,
        ):
            return opt
        label = day.strftime("%A").lower()
        if label in lowered and ("this" in lowered or "upcoming" in lowered):
            if opt == options[0]:
                return opt
        if label in lowered and ("next week" in lowered or "following" in lowered):
            if len(options) > 1 and opt == options[1]:
                return opt
    if re.search(r"\b(this|upcoming)\b", lowered) and options:
        return options[0]
    if re.search(r"\b(next week|following week|the week after)\b", lowered) and len(
        options
    ) > 1:
        return options[1]
    return None


def is_explicit_cancel_or_reschedule(user_text: str) -> bool:
    """True when the user clearly asks to cancel or reschedule."""
    return bool(
        re.search(
            r"\b(cancel|reschedule|change (my |the )?appointment)\b",
            user_text.lower(),
        )
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
    """Weekday + day month year for patient-facing facts."""
    from clinic_ai_booking.domain.hours import format_patient_day

    return format_patient_day(day)


def detect_all_services_in_text(session: Session, user_text: str) -> list[str]:
    """Return unique service codes A–E mentioned in text, in order of appearance."""
    text = user_text.lower()
    found: list[str] = []

    def add(letter: str) -> None:
        code = resolve_service_code(session, letter)
        if code and code not in found:
            found.append(code)

    for match in re.finditer(r"\bservice\s*([a-e])\b", text):
        add(match.group(1))
    for match in re.finditer(r"\b([a-e])\s*(?:,|&|and)\s*([a-e])\b", text):
        add(match.group(1))
        add(match.group(2))
    # "A, B, and C" — collect further letters after the first pair pattern
    for match in re.finditer(
        r"\b([a-e])\s*,\s*([a-e])(?:\s*,\s*(?:and\s+)?([a-e]))?",
        text,
    ):
        for group in match.groups():
            if group:
                add(group)
    if len(found) >= 2:
        return found
    one = detect_service_in_text(session, user_text)
    return [one] if one else []
