"""Doctor profiles for public pages."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Doctor:
    """A clinic professional shown on the public site."""

    slug: str
    name: str
    role: str
    summary: str
    is_senior: bool


DOCTORS: tuple[Doctor, ...] = (
    Doctor(
        slug="junior",
        name="Dr. Alex Chen",
        role="Junior professional",
        summary="Services A and B. Placeholder intro — replace with final clinic copy later.",
        is_senior=False,
    ),
    Doctor(
        slug="senior-1",
        name="Dr. Maya Lin",
        role="Senior 1",
        summary="Services A–E. Placeholder intro — replace with final clinic copy later.",
        is_senior=True,
    ),
    Doctor(
        slug="senior-2",
        name="Dr. Jordan Wu",
        role="Senior 2",
        summary="Services A–E. Placeholder intro — replace with final clinic copy later.",
        is_senior=True,
    ),
)

DOCTORS_BY_SLUG: dict[str, Doctor] = {d.slug: d for d in DOCTORS}
