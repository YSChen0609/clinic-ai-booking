# Smoke test — stage 2 auth

Manual checks after `docker compose up --build -d` (app on http://localhost:8000).

Security note: **no-password login (name + email only) is MVP-only.** Do not use this against the public internet without password or OAuth (see `TODOS.md`).

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

Chat (after Ollama model is pulled): as a visitor, ask to reschedule or cancel — the bot should tell you to log in (middleware + tools).

## Chat agent (stage 3)

1. Pull the model: `docker compose exec ollama ollama pull qwen2.5:7b`
2. Open chat on the intro page.
3. Ask something out of scope (e.g. a recipe) → polite refusal.
4. Ask for services / junior availability → tool-backed reply.
5. Book as visitor (give name + email when asked) → row in DB; app logs show fake calendar/email.
6. Log in; book without re-entering name/email.
7. Multi-turn: ask a follow-up in the same browser session (same cookie) — agent keeps thread history.

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
