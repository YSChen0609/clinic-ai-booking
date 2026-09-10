"""Live Ollama dialogue regression (skipped unless ``pytest -m llm``)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from clinic_ai_booking.chat.context import (
    SESSION_BOOKING_DRAFT_KEY,
    BookingDraft,
)
from clinic_ai_booking.chat.factory import create_chat_agent
from clinic_ai_booking.llm import make_chat_model
from dialogue_harness import (
    load_dialogue_scripts,
    ollama_reachable,
    run_dialogue,
)

pytestmark = pytest.mark.llm


@pytest.fixture(scope="module")
def llm_agent():
    """ClinicAgent against live Ollama (temperature 0 for less drift)."""
    if not ollama_reachable():
        pytest.skip(
            "Ollama not reachable — start compose ollama and pull the model "
            "(or set OLLAMA_BASE_URL)"
        )
    return create_chat_agent(model=make_chat_model(temperature=0))


@pytest.fixture(scope="module", autouse=True)
def _skip_llm_without_ollama():
    """Fail fast before session Postgres fixture when Ollama is down."""
    if not ollama_reachable():
        pytest.skip(
            "Ollama not reachable — start compose ollama and pull the model "
            "(or set OLLAMA_BASE_URL)"
        )


@pytest.mark.parametrize(
    "script",
    load_dialogue_scripts(),
    ids=lambda s: s["id"],
)
def test_dialogue_script(script: dict, llm_agent, db_session: Session) -> None:
    """Run a frozen multi-turn script; assert facts/draft, not reply wording."""
    run_dialogue(script, agent=llm_agent, db=db_session)


def test_confirm_before_book_adaptive(llm_agent, db_session: Session) -> None:
    """Drive offer → slot → contact → checkout; must hit need_confirm_book before booked."""
    from clinic_ai_booking.chat.agent import TurnResult

    session: dict = {}
    thread = "llm-confirm-adaptive"

    def turn(message: str) -> TurnResult:
        return llm_agent.run_one_turn(
            message=message,
            session=session,
            db=db_session,
            thread_id=thread,
            user=None,
        )

    first = turn("Book service A with Dr. Chen tomorrow morning")
    assert first.facts.get("status") == "offer_slots", first.facts
    clocks = first.facts.get("offered_times") or []
    assert clocks, first.facts

    saw_confirm_book = False
    booked = False
    result = turn(f"I'll take {clocks[0]}")
    for _ in range(8):
        status = result.facts.get("status") or ""
        if status == "need_confirm_book":
            saw_confirm_book = True
            result = turn("yes")
            continue
        if status == "need_confirm_slot":
            result = turn("yes")
            continue
        if status == "need_info":
            missing = result.facts.get("missing") or []
            draft = BookingDraft.from_session(session.get(SESSION_BOOKING_DRAFT_KEY))
            if (
                "patient_name" in missing
                or "patient_email" in missing
                or not draft.service_code
            ):
                result = turn(
                    "My name is Dialogue Patient, "
                    "email dialogue-patient@example.com"
                )
            else:
                result = turn("yes")
            continue
        if status == "booked":
            booked = True
            break
        if status == "offer_slots":
            more = result.facts.get("offered_times") or clocks
            result = turn(f"Please book {more[0]}")
            continue
        pytest.fail(f"unexpected status={status!r} facts={result.facts!r} reply={result.reply!r}")

    assert saw_confirm_book, "expected need_confirm_book before write"
    assert booked or (result.facts.get("status") == "booked"), result.facts
    assert result.facts.get("booking_id") is not None or booked
