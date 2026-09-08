"""Catalog gates: only real professional slugs and service codes reach the engine."""

from __future__ import annotations

from sqlalchemy.orm import Session

from clinic_ai_booking.booking import list_professionals, list_services


def reject_unknown_catalog(
    session: Session,
    *,
    professional_slug: str | None = None,
    service_code: str | None = None,
) -> str | None:
    """Return an error if slug/code is unknown; include valid values for the model."""
    if professional_slug is not None:
        cleaned = professional_slug.strip()
        if cleaned:
            pros = list_professionals(session)
            slugs = {row.slug for row in pros}
            if cleaned not in slugs:
                listing = ", ".join(
                    f"{row.slug} ({row.name})" for row in pros
                )
                return (
                    f"unknown professional_slug={cleaned!r}. "
                    f"Call list_professionals and use a returned slug. "
                    f"Valid: {listing}."
                )

    if service_code is not None:
        cleaned = service_code.strip().upper()
        if cleaned:
            codes = {row.code for row in list_services(session)}
            if cleaned not in codes:
                listing = ", ".join(sorted(codes))
                return (
                    f"unknown service_code={service_code!r}. "
                    f"Call list_services and use a returned code. "
                    f"Valid: {listing}."
                )

    return None
