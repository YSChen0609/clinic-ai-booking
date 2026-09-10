"""Live-LLM dialogue harness: load JSON scripts, run ClinicAgent, assert end state."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from clinic_ai_booking.chat.agent import ClinicAgent, TurnResult
from clinic_ai_booking.chat.context import (
    SESSION_BOOKING_DRAFT_KEY,
    SESSION_CHAT_MEMORY_KEY,
    BookingDraft,
    ChatMemory,
)

DIALOGUES_DIR = Path(__file__).resolve().parent / "dialogues"

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def ollama_base_url() -> str:
    """Return Ollama HTTP base used by the app (host pytest default)."""
    return (
        os.environ.get("OLLAMA_BASE_URL", "").strip()
        or os.environ.get("OLLAMA_HOST", "").strip()
        or "http://127.0.0.1:11434"
    )


def ollama_reachable(*, timeout_s: float = 2.0) -> bool:
    """True when Ollama /api/tags responds."""
    try:
        response = httpx.get(f"{ollama_base_url().rstrip('/')}/api/tags", timeout=timeout_s)
        return response.status_code == 200
    except Exception:
        return False


def load_dialogue_scripts(directory: Path | None = None) -> list[dict[str, Any]]:
    """Load every *.json dialogue script (sorted by filename)."""
    root = directory or DIALOGUES_DIR
    scripts: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "id" not in data or "turns" not in data:
            raise ValueError(f"invalid dialogue script: {path}")
        data["_path"] = str(path)
        scripts.append(data)
    return scripts


def _fill_user_text(template: str, last: TurnResult | None) -> str:
    """Replace {first_offered_clock} / {first_offered_start} from the prior turn."""

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if last is None:
            raise AssertionError(f"placeholder {{{key}}} needs a prior turn")
        facts = last.facts
        if key == "first_offered_clock":
            clocks = facts.get("offered_times") or []
            if not clocks:
                raise AssertionError("no offered_times to pick from")
            return str(clocks[0])
        if key == "first_offered_start":
            starts = facts.get("offered_starts") or []
            if not starts:
                raise AssertionError("no offered_starts to pick from")
            return str(starts[0])
        if key == "first_day_option":
            opts = facts.get("day_options") or []
            if not opts:
                raise AssertionError("no day_options to pick from")
            return str(opts[0])
        raise AssertionError(f"unknown placeholder {{{key}}}")

    return _PLACEHOLDER.sub(repl, template)


def _assert_expect(
    expect: dict[str, Any],
    *,
    result: TurnResult,
    session: dict[str, Any],
) -> None:
    """Assert end-state expectations (not reply prose quality)."""
    if "in_scope" in expect:
        assert result.in_scope is bool(expect["in_scope"]), (
            f"in_scope={result.in_scope!r} want {expect['in_scope']!r}; "
            f"reply={result.reply!r}"
        )
    if "status_in" in expect:
        status = result.facts.get("status") or ""
        allowed = list(expect["status_in"])
        assert status in allowed, (
            f"status={status!r} not in {allowed}; facts={result.facts!r}; "
            f"reply={result.reply!r}"
        )
    if "reply_equals" in expect:
        assert result.reply == expect["reply_equals"], (
            f"reply mismatch: {result.reply!r} != {expect['reply_equals']!r}"
        )
    if "reply_contains" in expect:
        for needle in expect["reply_contains"]:
            assert needle.lower() in result.reply.lower(), (
                f"reply missing {needle!r}: {result.reply!r}"
            )
    if expect.get("has_offered_starts"):
        starts = result.facts.get("offered_starts") or []
        assert starts, f"expected offered_starts; facts={result.facts!r}"
    if expect.get("has_availability"):
        avail = result.facts.get("availability") or {}
        assert avail, f"expected availability; facts={result.facts!r}"
    if "day_options_len" in expect:
        opts = result.facts.get("day_options") or []
        assert len(opts) == int(expect["day_options_len"]), (
            f"day_options={opts!r} len want {expect['day_options_len']}"
        )
    if "draft" in expect or "draft_not" in expect:
        draft = BookingDraft.from_session(session.get(SESSION_BOOKING_DRAFT_KEY))
        for key, want in (expect.get("draft") or {}).items():
            got = getattr(draft, key, None)
            assert got == want, f"draft.{key}={got!r} want {want!r}"
        for key, bad in (expect.get("draft_not") or {}).items():
            got = getattr(draft, key, None)
            assert got != bad, f"draft.{key} should not be {bad!r}"
    if "memory" in expect:
        memory = ChatMemory.from_session(session.get(SESSION_CHAT_MEMORY_KEY))
        for key, want in expect["memory"].items():
            got = getattr(memory, key, None)
            assert got == want, f"memory.{key}={got!r} want {want!r}"
    if expect.get("booking_id_set"):
        assert result.facts.get("booking_id") is not None, (
            f"expected booking_id; facts={result.facts!r}"
        )


def run_dialogue(
    script: dict[str, Any],
    *,
    agent: ClinicAgent,
    db: Session,
    session: dict[str, Any] | None = None,
    user: Any = None,
) -> list[TurnResult]:
    """Run all turns in a dialogue script; raise on first failed expect."""
    bag: dict[str, Any] = {} if session is None else session
    thread_id = f"llm-dialogue-{script['id']}-{uuid4().hex[:8]}"
    results: list[TurnResult] = []
    last: TurnResult | None = None
    for index, turn in enumerate(script["turns"]):
        user_text = _fill_user_text(str(turn["user"]), last)
        result = agent.run_one_turn(
            message=user_text,
            session=bag,
            db=db,
            thread_id=thread_id,
            user=user,
        )
        results.append(result)
        expect = turn.get("expect") or {}
        try:
            _assert_expect(expect, result=result, session=bag)
        except AssertionError as exc:
            raise AssertionError(
                f"{script['id']} turn {index} user={user_text!r}: {exc}"
            ) from exc
        last = result
    return results
