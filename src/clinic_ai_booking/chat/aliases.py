"""Closed catalogs and alias tables for extract (7B as classifier, not free text)."""

from __future__ import annotations

from clinic_ai_booking.domain.doctors import DOCTORS

# Canonical service codes the model may emit.
SERVICE_CODES: tuple[str, ...] = ("A", "B", "C", "D", "E")

# Alias → canonical service letter (lowercase keys).
SERVICE_ALIASES: dict[str, str] = {
    "a": "A",
    "b": "B",
    "c": "C",
    "d": "D",
    "e": "E",
    "service a": "A",
    "service b": "B",
    "service c": "C",
    "service d": "D",
    "service e": "E",
}

# Alias → professional slug (lowercase keys). Names from domain/doctors.py.
PROFESSIONAL_ALIASES: dict[str, str] = {
    "junior": "junior",
    "chen": "junior",
    "alex": "junior",
    "alex chen": "junior",
    "dr alex chen": "junior",
    "dr. alex chen": "junior",
    "dr chen": "junior",
    "dr. chen": "junior",
    "senior-1": "senior-1",
    "senior 1": "senior-1",
    "maya": "senior-1",
    "lin": "senior-1",
    "maya lin": "senior-1",
    "dr maya lin": "senior-1",
    "dr. maya lin": "senior-1",
    "dr lin": "senior-1",
    "dr. lin": "senior-1",
    "senior-2": "senior-2",
    "senior 2": "senior-2",
    "jordan": "senior-2",
    "wu": "senior-2",
    "jordan wu": "senior-2",
    "dr jordan wu": "senior-2",
    "dr. jordan wu": "senior-2",
    "dr wu": "senior-2",
    "dr. wu": "senior-2",
}


def catalog_prompt_block() -> str:
    """Multiple-choice catalog lines for the extract system prompt."""
    doctors = []
    for doc in DOCTORS:
        level = "senior" if doc.is_senior else "junior"
        doctors.append(f"  - {doc.name} ({level}, slug={doc.slug})")
    return (
        "Allowed services (pick one letter only): A, B, C, D, E.\n"
        "Allowed doctors (match to one of these; do not invent names):\n"
        + "\n".join(doctors)
        + "\nAliases OK in professional field: Chen/Alex→junior; Lin/Maya→senior-1; "
        "Wu/Jordan→senior-2; junior; senior (ambiguous — leave for server)."
    )


def normalize_service_alias(raw: str | None) -> str | None:
    """Map a free-text service mention to A–E, or None."""
    if not raw or not raw.strip():
        return None
    key = " ".join(raw.strip().lower().split())
    if key in SERVICE_ALIASES:
        return SERVICE_ALIASES[key]
    if len(key) == 1 and key.upper() in SERVICE_CODES:
        return key.upper()
    return None


def normalize_professional_alias(raw: str | None) -> str | None:
    """Map a free-text doctor mention to a slug when unique, or None."""
    if not raw or not raw.strip():
        return None
    key = " ".join(raw.strip().lower().split())
    key = key.removeprefix("doctor ").removeprefix("dr. ").removeprefix("dr ")
    key = " ".join(key.split())
    return PROFESSIONAL_ALIASES.get(key)
