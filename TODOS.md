# Deferred work

## Still open

- [ ] Chat cancel/reschedule in the turn graph (book-only today)
- [ ] Remove unused `chat/tools.py` + leftover `create_agent` middleware (`middleware.py` / `middleware_stack.py`) once no longer referenced by tests
- [ ] Doctor approve UI for service E `pending_doctor` (engine stores them; notify sends pending email only — **no** confirmed calendar write until status becomes `confirmed`)
- [ ] Password auth (replace no-password name+email MVP login)
- [ ] OAuth for patients/staff (after password / stronger auth)
- [ ] CI (GitHub Actions) — skip until asked
- [ ] MCP — skip for v1
- [ ] Replace placeholder clinic/doctor intro copy on public pages
- [ ] Calendar invite decline: invite+email are **notifications only** — Postgres stays confirmed if the patient declines. Decide later: ignore, sync attendee response, or email-only notify

## Done (MVP stages 0–7)

- [x] Compose skeleton, `/health`, intro + doctor pages, messenger shell
- [x] Postgres booking engine + `pending_doctor` rules
- [x] Visitor vs login; account on first book; cancel/reschedule gated
- [x] Chat: LangGraph turn graph + Ollama (`ClinicAgent` / `turn_graph` + `book_graph`)
- [x] Busy-only doctor calendars from Postgres
- [x] Voice: Voicebox faster-whisper STT + Piper TTS in messenger
- [x] Real Google/Outlook notify adapters; `NOTIFY_MODE=fake|real`
- [x] Docs (`docs/internal.md`, `docs/external.md`) + Compose pins / non-root / healthchecks
- [x] Package layout: `domain/`, `notify/` (+ adapters), `chat/`, `voice/`

## Stage 6 behavior notes

- Confirmed book → upsert clinic Google and/or Outlook events (patient as attendee) + patient email.
- `pending_doctor` → pending patient email only.
- After future doctor approve: set `confirmed` and call the same notify path.
- Public doctor UI remains Postgres busy-only (no patient PII).
- **v1:** calendar invite decline does not change the booking (see open TODO).

## Fakes / stubs still in the app

| Item | Notes |
|------|--------|
| `FakeCalendar` / `FakeEmail` | Default until `NOTIFY_MODE=real` + creds (`notify/fakes.py`) |
| No-password login | Name + email only |
| Doctor / clinic intro copy | Placeholder |
| Doctor approve UI | Missing for `pending_doctor` |
| Chat cancel/reschedule | Not in turn graph yet |

## Later options (not v1)

- [ ] Postgres-backed chat sessions (multi-worker; smaller cookies)
- [ ] Deep Agents — only if long-horizon planning is needed
- [ ] Pure continuous voice (barge-in, auto-send)
- [ ] Cloud STT/TTS without local Whisper/Piper
- [ ] Swap Ollama for a hosted LLM behind `llm.py`
