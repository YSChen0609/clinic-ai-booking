# Smoke test — MVP end-to-end

Manual checks after `docker compose up --build -d` (app on http://localhost:8000). Covers auth, chat, busy calendars, voice, and optional real notify.

Security note: **no-password login (name + email only) is MVP-only.** Do not use this against the public internet without password or OAuth (see [TODOS.md](../TODOS.md)).

## Browse as visitor

1. Open http://localhost:8000 in a private/incognito window (or after log out).
2. Intro and `/doctors/junior` (and senior pages) load without signing in.
3. Header shows the name + email login form (not “Signed in as …”).

## Log in; session persists

1. In the header, enter name + email → **Log in**.
2. Header shows **Signed in as \<name\>**.
3. Reload the page or open another doctor page — still signed in (cookie `clinic_session`).

## Log out

1. Click **Log out**.
2. Header returns to the login form.
3. Alternate API clear: `POST /auth/logout.json` (or delete the `clinic_session` cookie).

## Visitor tries to reschedule / cancel (must prompt login)

API while **not** logged in:

```bash
# Create a booking as visitor (creates account row; does not log you in).
# Note the numeric "id" in the JSON — use that below, not the literal text BOOKING_ID.
curl -s -X POST http://localhost:8000/api/bookings \
  -H "Content-Type: application/json" \
  -d "{\"professional_slug\":\"junior\",\"service_code\":\"A\",\"starts_at\":\"2026-08-31T09:00:00+08:00\",\"patient_name\":\"Smoke Visitor\",\"patient_email\":\"smoke-visitor@example.com\"}"

# Attempt reschedule without session → 401 + login message (example id: 42)
curl -s -o - -w "\nHTTP %{http_code}\n" -X POST http://localhost:8000/api/bookings/42/reschedule \
  -H "Content-Type: application/json" \
  -d "{\"starts_at\":\"2026-08-31T11:00:00+08:00\"}"

# Attempt cancel without session → same (use the same numeric id)
curl -s -o - -w "\nHTTP %{http_code}\n" -X POST http://localhost:8000/api/bookings/42/cancel
```

Expect HTTP **401** and detail: `Please log in to cancel or reschedule.`

Chat (after Ollama model is pulled): as a visitor, ask to reschedule or cancel — the bot should tell you to log in (turn graph; cancel/reschedule not fully implemented).

## Chat (agent)

`POST /api/chat` runs `ClinicAgent.run_one_turn` (Ollama required).

Flow: FAQ/scope extract → in-scope booking StateGraph over `domain/booking.py` → client reply.
Multi-turn: sticky `booking_draft` + identity on the session cookie; graph stops when the user must answer.

Suggested smoke path (messenger or `POST /api/chat`):

1. Out of scope (“what’s the weather?”) → polite clinic-only reply.
2. FAQ chip or “what services do you offer?” → catalog from DB.
3. “Book service A with Dr. Chen tomorrow morning” → offers real starts only (no invented times).
4. Pick a listed time → ask name+email once per chat thread (kept for later bookings in the same chat).
5. Booking succeeds → draft clears but **thread memory** keeps last doctor/day; contact stays until Reset chat.
6. Follow-ups like “also … with him in the afternoon” reuse that memory; “I’ve told you” works because contact was kept.
7. Cancel/reschedule in chat → login / not-in-flow message (book-only stage).

Use **Reset chat** (or `/api/chat/reset`) to clear transcript, draft, contact, and thread memory.


## Doctor busy calendars (stage 4)

Public doctor pages and `GET /api/doctors/{slug}/busy` show **busy blocks only** (start/end times). No patient names or emails.

1. Open http://localhost:8000 — intro links to junior, senior-1, senior-2.
2. Open each `/doctors/...` page — placeholder intro + “Busy times” calendar; messenger still present.
3. Book doctor 1 only, then check calendars:

```bash
# Pick a weekday in Asia/Taipei within the week you will view (example Monday).
curl -s -X POST http://localhost:8000/api/bookings \
  -H "Content-Type: application/json" \
  -d "{\"professional_slug\":\"junior\",\"service_code\":\"A\",\"starts_at\":\"2026-08-31T09:00:00+08:00\",\"patient_name\":\"Cal Smoke\",\"patient_email\":\"cal-smoke@example.com\"}"

# Junior has the busy block; senior-1 does not. Payload keys are times only.
curl -s "http://localhost:8000/api/doctors/junior/busy?from=2026-08-31&to=2026-09-06"
curl -s "http://localhost:8000/api/doctors/senior-1/busy?from=2026-08-31&to=2026-09-06"
```

4. Open `/doctors/junior?from=2026-08-31` — see **Busy 09:00–10:00**. Open senior pages for the same week — that block is absent.
5. Sat/Sun cells show **Clinic closed (weekend)** (not “No busy blocks”).

Busy calendars read Postgres on every page/API request (no cache). After you delete rows, refresh the doctor page to see the change. See [Manual cleanup — booking records](#manual-cleanup--booking-records).


## Voice in messenger (stage 5)

Compose service `voice` (Voicebox: faster-whisper + Piper). App proxies via `/api/voice/*` — chat path unchanged.

1. `docker compose up --build -d` (or with `compose.gpu.yml` for Ollama GPU). First voice image build clones Voicebox and bakes Whisper + Piper (several minutes).
2. Wait until voice is healthy:

```bash
curl -s http://localhost:8790/health
# expect models_loaded true (may take ~1 min on first start)
curl -s http://localhost:8000/health
```

3. Open the site messenger. **Text chat still works** if you skip the mic.
4. Click **Mic** → allow microphone → speak a short phrase → click **Mic** again to stop.
5. Transcript should **fill the input** (edit if needed) → **Send** → same bot reply path as typed chat.
6. On a bot bubble, click **Play** → hear Piper TTS of that reply text.

API smoke (optional; needs a short WAV/WebM):

```bash
# TTS round-trip
curl -s -X POST http://localhost:8000/api/voice/speak \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"Hello from the clinic assistant.\"}" \
  --output /tmp/clinic-speak.wav

# STT (replace path with a real short recording)
# curl -s -X POST http://localhost:8000/api/voice/transcribe \
#   -F "file=@/tmp/clinic-speak.wav"
```

If mic permission is denied or `VOICE_ENABLED=false`, typing + Send must still work.


## Google / Outlook notify (stage 6 — real credentials, local only)

Requires secrets in `.env` (from `.env.example`). Do **not** commit `.env`.

**How to get client id / secret / refresh token:** see [oauth-setup.md](oauth-setup.md) (also summarized in [external.md](external.md)).

1. Follow [oauth-setup.md](oauth-setup.md); set `NOTIFY_MODE=real`.
2. Recreate the app so env is picked up:

```bash
docker compose up -d --force-recreate app
# or local: uv run clinic-ai-booking
```

3. Confirmed book (service A) → clinic Google **and** Outlook calendars (whichever creds are set) show an event; title/description name the professional; patient gets email and/or calendar invite as attendee. Public doctor page still shows **busy only** (no patient name).

```bash
# Use a real weekday in Asia/Taipei and an inbox you control.
curl -s -X POST http://localhost:8000/api/bookings \
  -H "Content-Type: application/json" \
  -d "{\"professional_slug\":\"junior\",\"service_code\":\"A\",\"starts_at\":\"2026-08-31T09:00:00+08:00\",\"patient_name\":\"Notify Smoke\",\"patient_email\":\"YOUR_INBOX@example.com\"}"
```

4. Check clinic Google Calendar + Outlook calendar for the event; check patient inbox for mail/invite.
5. `pending_doctor` (service E overtime) → pending email only; **no** confirmed calendar event:

```bash
curl -s -X POST http://localhost:8000/api/bookings \
  -H "Content-Type: application/json" \
  -d "{\"professional_slug\":\"senior-1\",\"service_code\":\"E\",\"starts_at\":\"2026-08-31T16:00:00+08:00\",\"patient_name\":\"Pending Smoke\",\"patient_email\":\"YOUR_INBOX@example.com\"}"
```

Expect JSON `"status":"pending_doctor"`. Confirm calendars did not gain a new confirmed event for that booking id; pending email arrived.

To free slots and re-smoke: [Manual cleanup — booking records](#manual-cleanup--booking-records). Also delete leftover events in Google Calendar / Outlook by hand (DB delete does not remove vendor events already created).


## Manual cleanup — booking records

Postgres is source of truth for busy slots. Use these when a smoke slot is taken or you want a clean slate.

```bash
# Interactive psql
docker compose exec db psql -U clinic -d clinic

# List bookings
docker compose exec -T db psql -U clinic -d clinic -c \
  "SELECT id, professional_id, status, starts_at, ends_at, patient_email FROM bookings ORDER BY starts_at;"

# Delete all bookings
docker compose exec -T db psql -U clinic -d clinic -c \
  "DELETE FROM bookings;"

# Delete one booking (example id 42)
docker compose exec -T db psql -U clinic -d clinic -c \
  "DELETE FROM bookings WHERE id = 42;"

# Optional: remove smoke users too
# docker compose exec -T db psql -U clinic -d clinic -c \
#   "DELETE FROM users WHERE email LIKE '%smoke%' OR email LIKE '%example.com';"
```

After delete, refresh the doctor page (or call `/api/doctors/{slug}/busy` again). Google/Outlook events created during `NOTIFY_MODE=real` must be removed in those calendars separately.


## Book as visitor → account exists

After the visitor `POST /api/bookings` above (or another visitor book with name+email):

```bash
docker compose exec -T db psql -U clinic -d clinic -c \
  "SELECT id, name, email FROM users WHERE email = 'smoke-visitor@example.com';"
```

Expect one row. Booking `user_id` should match that user.

## Logged-in cancel / reschedule (optional)

1. Log in with the same name + email used on the booking.
2. `POST /api/bookings/{id}/reschedule` and/or `/cancel` with the browser cookie (or after `POST /auth/login.json`) → **200**.
