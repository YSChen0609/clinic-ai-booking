# TODOS

Criterion for **minimal**: [instructions.txt](../instructions.txt) (chat+voice bot, 3 pros / services A–E, scope + availability, Google **and** Outlook calendars, English, working web app, internal + external docs).

Known gaps: Outlook setup friction (Google is known-good for real notify). Agent happy path is MVP-ready; residual edges remain. See [internal.md](internal.md) and [docs/smoke_test.md](docs/smoke_test.md). Below is open work by priority.

---

## Minimal (still needed for instructions.txt)

- [ ] **Outlook calendar + email in practice** — adapters exist (`NOTIFY_MODE=real` + `MS_*`), but tenant/mailbox/app-registration setup is still painful and easy to misconfigure. Make the real path reliably demoable (docs/smoke and/or clearer failure messages). **Google path is the current known-good side** of “update Outlook **and** Google Calendar.”
- [ ] **End-to-end demo of both calendars** — confirmed book should show on clinic Google **and** Outlook when both creds are set (instructions require both systems, not Google-only).
- [x] **Booking agent reliability (MVP bar)** — happy path + practiced recoveries work; engine blocks illegal books. Remaining edge-case flakiness is not production-grade ([docs/smoke_test.md](docs/smoke_test.md)).

Nothing else from `instructions.txt` is missing as a product surface: web app, chat + voice (provider behind adapters), scope/busy awareness (intended), 3 professionals / junior vs senior services, English, `internal.md` + `external.md` are in.

---

## Agent hardening (this tranche)

- [x] **Date cheat sheet + `day_phrase`** — inject next-14-day mapping into extract prompt; resolve free phrases with `dateparser` (not Qwen calendar math)
- [x] **Entity enums / aliases** — closed service A–E + doctor alias table in prompt and `resolve.py` (classifier-style, server still maps to slug)
- [x] **Server closed-select reject** — letters outside A–E (e.g. F) stay in scope with a clear catalog reply; draft clamped to A–E + catalog slugs; no LLM substitute fill when an invalid letter was named
- [x] **Confirm-before-book** — `need_confirm_book` before `book_appointment` (checkout “yes”); LangGraph `interrupt()` deferred until durable checkpointer
- [x] **Live LLM dialogue pack** — `tests/dialogues/` + `pytest -m llm` (end-state asserts); every smoke bug → new script before the fix
- [x] **Multi-service one-at-a-time notice** — if the patient names two+ services in one message, ask which to book first (no silent pick)
- [ ] **Multi-appointment cart (better)** — cookie `cart: list[draft]` (max 2) so “book A and B” queues both without overwriting the first mid-flow; not LangGraph `operator.add` on `TurnState` alone. Sequential booking + notice is the current path.
- [ ] **Durable checkpointer + `interrupt()`** — Postgres/Redis checkpointer, then pause before DB write for a real frontend checkout resume
- [ ] **True constrained decoding (upgrade)** — Ollama/vLLM JSON-schema or grammar so extract can only emit `service_code ∈ {A–E,null}` and catalog doctor enums at token level (today: prompt + server clamp/reject)
- [ ] **`BookingDraft` Literal types (upgrade)** — type `service_code` / slug fields as `Literal[...] | None` for static checking (runtime clamp already exists)
---

## Later hardening (beyond instructions.txt)

### Product / UX

- [ ] Chat **list my bookings** — “what are my scheduled appointments?” (and similar) should list upcoming bookings for the known email / logged-in user via `list_patient_appointments`, not fall into the book flow asking for service + doctor. Need contact or login first if unknown.
- [ ] Chat **cancel / reschedule** in the turn graph (API + login gate exist; chat is book-only and tells the user that)
- [ ] Doctor **approve UI** for service E `pending_doctor` (engine + pending email exist; no confirmed calendar write until status → `confirmed`)
- [ ] Replace placeholder clinic/doctor intro copy on public pages
- [ ] Calendar invite decline policy — today invite/email are notify mirrors; Postgres stays confirmed if the patient declines (ignore vs sync vs email-only)

### Auth / security (demo is name+email, no password)

- [ ] Password auth
- [ ] OAuth for patients/staff (after stronger auth)

### Engineering cleanup

- [ ] Remove unused `chat/tools.py` + leftover `create_agent` middleware once tests no longer need them
- [ ] CI (GitHub Actions) — when asked
- [ ] Publish/pull app image via GHCR ([docs/ghcr-publish.md](docs/ghcr-publish.md))

### Optional / not planned for v1

- [ ] MCP
- [ ] Postgres-backed chat sessions (multi-worker; smaller cookies)
- [ ] Deep Agents (only if long-horizon planning is needed)
- [ ] Pure continuous voice (barge-in, auto-send)
- [ ] Cloud STT/TTS without local Whisper/Piper
- [ ] Swap Ollama for a hosted LLM behind `llm.py`
- [ ] Extra calendar/email vendors (ports already allow it)

---

## Done (against instructions + stages 0–7)

- [x] Working web app (Compose, intro/doctor pages, messenger, `/health`)
- [x] Chat + voice (Ollama chat; Voicebox STT/TTS; swap points in `llm.py` / `voice/`)
- [x] Booking engine: 3 pros, A–E durations, junior A–B vs seniors A–E, busy/available from Postgres
- [x] Bot scope / out-of-scope handling (engine + extract clamp; residual agent edges — see minimal)
- [x] Google Calendar + email adapters; Outlook adapters coded; `CalendarPort` / `EmailPort` for expansion
- [x] English only; `Asia/Taipei`
- [x] Internal + external docs (plus smoke, OAuth, host, GHCR notes)
- [x] Package layout: `domain/`, `notify/` (+ adapters), `chat/`, `voice/`

### Notify behavior (current)

- Confirmed book → upsert clinic Google and/or Outlook (when wired) + patient email.
- `pending_doctor` → pending patient email only.
- Public doctor UI: Postgres busy blocks only (no patient PII).
- Default `NOTIFY_MODE=fake` until real creds are set.
