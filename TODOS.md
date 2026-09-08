# Deferred work

## Still open (by stage)

- [ ] Chat cancel/reschedule subgraph (book-only turn graph is in place)
- [ ] Drop unused `chat/tools.py` + create_agent middleware once smoke is green
- [ ] Doctor approve UI for service E `pending_doctor` overtime bookings (engine stores them; notify sends pending email only — **no** confirmed calendar write until status becomes `confirmed`)
- [ ] Password auth (replace no-password name+email MVP login)
- [ ] OAuth (after password / stronger auth)
- [ ] CI (GitHub Actions) — skip until asked
- [ ] MCP — skip for v1 (optional learning later)
- [ ] Replace placeholder clinic/doctor intro copy on public pages
- [ ] Calendar invite decline (“No” / decline): today invite+email are **notifications only** — Postgres booking stays confirmed even if the patient declines the Google/Outlook invite. Decide later: ignore declines, sync attendee response into booking status, or stop using attendee invites and email-only notify

## Done / noted

- [x] Stage 6: real `CalendarPort` / `EmailPort` for clinic Google Calendar + Outlook (calendar + email); `NOTIFY_MODE=fake|real`; dual-write calendars when both creds set; patient invite/attendee (no patient OAuth); see `.env.example` + `smoke_test.md`
- [x] Stage 5: messenger voice (Voicebox faster-whisper STT + Piper TTS; fill input then Send; Play on replies)
- [x] Stage 4: doctor pages show busy-only calendars from Postgres (filter by professional; no patient name/email)
- [x] Stage 3 turn graph: `turn_graph` + `book_graph` over `booking.py` (option A)
- [x] Fake calendar/email adapters record/log calls; swap at `set_ports` / `notify.py`

## Stage 6 behavior notes

- Confirmed book → upsert clinic Google and/or Outlook calendar events (patient as attendee) + patient email.
- `pending_doctor` → pending patient email only; adapters also no-op calendar upsert if called.
- After doctor approve (future UI): set status to `confirmed` and call the same notify path so calendars get the event then.
- Public doctor UI remains Postgres busy-only (no patient PII).
- **v1 assumption:** calendar invite is a notification mirror. Patient clicking “No” / decline on the invite does **not** cancel or change the Postgres booking (see open TODO above).

## Fakes / stubs still in the app

**Notify adapters (default until `NOTIFY_MODE=real` + credentials):**

| Fake | File | What it does |
|------|------|----------------|
| `FakeCalendar` | `fakes.py` | Implements `CalendarPort`; logs + records upsert/remove; no Google/Outlook HTTP |
| `FakeEmail` | `fakes.py` | Implements `EmailPort`; logs + records created/cancelled/rescheduled; no mail send |

Wiring: `notify.py` starts on these; `adapters/wiring.py` keeps them when `NOTIFY_MODE` is unset/`fake`, or when `real` but Google/Outlook env is incomplete (partial: missing calendar or email side can stay fake while the other is real).

**Not Fake* classes, but still MVP / placeholder (leave until their stage):**

| Item | Notes |
|------|--------|
| No-password login (name + email) | Auth MVP; password/OAuth later |
| Doctor / clinic intro copy | Placeholder text on public pages |
| Doctor approve UI | `pending_doctor` has no staff confirm path yet |
| Chat cancel/reschedule | Book-only turn graph; cancel/reschedule not in chat yet |

## Later options (not planned for v1)

- [ ] **Postgres chat session** — move sticky agent state off the signed cookie: cookie holds only `session_id`; `chat_sessions` table stores `thread_id`, visitor name/email/phone, `booking_draft` JSON. Same rehydrate pattern (`load` → `ClinicContext` → `invoke` → `save`); enables multi-worker deploys and drops cookie size limits. v1 uses cookie for visitor + draft + `thread_id`.
- [ ] **Deep Agents** — only if we need long-horizon planning / subagents; not for the clinic booking loop.
- [ ] **Pure / continuous voice mode** — streaming STT, auto-send, barge-in, no text input required (stage 5 is push-to-talk → text → optional TTS play).
- [ ] Cloud STT/TTS for deploys without local Whisper/Piper
