# External — clinic product guide

## What it is

A **local web assistant** for a dental clinic so patients can:

- Browse doctors and see **busy times only** (no other patients’ names)
- Book appointments in a **chat** messenger (optional **voice**: speak → text → send; play bot replies aloud)
- Get a **calendar event + email** on the clinic’s Google and/or Outlook account when a booking is confirmed

Clinic timezone: **Asia/Taipei**. Product language: **English**.

## What it is not

- Not a full practice-management system (billing, charts, imaging)
- Not production-hardened identity (login is name + email **without a password** — demo only)
- Not automatic doctor approval UI for overtime / service E (`pending_doctor` waits for a future staff tool)
- Not patient Google/Microsoft sign-in — the **clinic** calendar/mailbox creates invites

## Install (on a clinic PC or demo laptop)

### 1. Prerequisites

- Docker Desktop (or Docker Engine + Compose v2)
- Copy env: `cp .env.example .env` and edit passwords / ports if needed
- GPU optional for faster chat — see [host-requirements.md](host-requirements.md)

### 2. Start the stack

```bash
docker compose up --build -d
docker compose exec ollama ollama pull qwen2.5:7b
```

Open http://localhost:8000 — expect `/health` → `{"status":"ok"}`.

CPU-only is the default. NVIDIA GPU:  
`docker compose -f compose.yml -f compose.gpu.yml up --build -d` (same pull).

### 3. Use without real calendars

Leave `NOTIFY_MODE=fake` (default). Bookings still store in Postgres and show on doctor busy calendars; calendar/email actions are logged only.

### 4. Optional: real clinic Google / Outlook

Done **outside this app** in Google Cloud / Microsoft Entra, then pasted into `.env`. Full steps: **[oauth-setup.md](oauth-setup.md)**.

Summary:

1. **Google:** enable Calendar + Gmail APIs → OAuth Web client → OAuth Playground refresh token with calendar + `gmail.send` scopes → set `GOOGLE_*`.
2. **Outlook:** Microsoft 365 mailbox + Entra app registration with Application permissions `Calendars.ReadWrite` + `Mail.Send` and admin consent → set `MS_*`.
3. Set `NOTIFY_MODE=real`, recreate app: `docker compose up -d --force-recreate app`.
4. Book a test slot to an inbox you control; confirm clinic calendar + patient mail/invite.

You can enable Google only, Outlook only, or both (dual-write calendars).

### 5. Voice

Comes up with Compose (`voice` on port 8790). First build can take several minutes (models). Set `VOICE_ENABLED=false` to keep text-only chat.

## Day-to-day use

1. Open the site → intro links to each doctor.
2. Optional: **Log in** with name + email (session cookie). Required to cancel/reschedule via API.
3. Ask the messenger to book (service + doctor + day). Prefer times the bot lists — it reads real free slots.
4. After a confirmed book: doctor page shows a busy block; with real notify, clinic calendar + patient email update.
5. Service E overtime → status `pending_doctor`: patient gets a pending email; **no** confirmed calendar event until a doctor approve path exists.

Demo checklist: [smoke_test.md](smoke_test.md). Booking rules: [booking_rules.md](booking_rules.md).

## Limits to tell staff

- Declining a calendar invite does **not** cancel the Postgres booking (notify is a mirror today).
- Chat cancel/reschedule is not fully wired; use login + API or wait for a later release.
- Keep `.env` private; never commit it.
