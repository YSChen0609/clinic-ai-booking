# External — clinic guide

## Product

Patients can browse doctors (busy times only), book in **chat** (optional **voice**), and — when notify is real — get a **clinic Google and/or Outlook** calendar invite plus email.

English only. Timezone: **Asia/Taipei**. Three professionals: junior (services A–B), two seniors (A–E). Durations: A/B 1h, C 2.5h, D 2h, E 6h.

## Not included

- Full practice management (billing, charts)
- Password login (name + email only — MVP 0.1.0)
- Patient Google/Microsoft sign-in (clinic calendar sends invites)
- Doctor approve UI for overtime service E (`pending_doctor`)

## Install

1. Docker Desktop (or Engine + Compose v2). Optional GPU: [docs/host-requirements.md](docs/host-requirements.md).
2. `cp .env.example .env` — set passwords / ports; keep `.env` private.
3. Start:

```bash
docker compose up --build -d
docker compose exec ollama ollama pull qwen2.5:7b
```

Open [http://localhost:8000](http://localhost:8000) (`/health` → ok). GPU: `docker compose -f compose.yml -f compose.gpu.yml up --build -d`.

4. **Without calendars:** leave `NOTIFY_MODE=fake` (default). Bookings still appear on site busy calendars.
5. **With calendars (outside the app):** create Google Cloud / Microsoft Entra credentials — [docs/oauth-setup.md](docs/oauth-setup.md) — set `GOOGLE_*` and/or `MS_*`, then `NOTIFY_MODE=real` and recreate the app. Google is the known-good path; Outlook uses the same adapters but Entra setup is easy to misconfigure.
6. Voice comes with Compose (`voice` :8790). Disable with `VOICE_ENABLED=false`.

## Use

1. Open the site → doctor pages → messenger.
2. Book: service + doctor + day; pick a start the bot lists; confirm.
3. Optional login (name + email) for cancel/reschedule API.
4. Confirmed book → busy block on doctor page; with real notify → clinic calendar + patient email. Service E overtime may stay `pending_doctor` (email only until approve exists).

Rules: [docs/booking_rules.md](docs/booking_rules.md). Checklist: [docs/smoke_test.md](docs/smoke_test.md).

## If something fails

Check `/health`, Compose logs (`app`, `db`, `ollama`, `voice`), and `.env`. Calendar/OAuth issues: [docs/oauth-setup.md](docs/oauth-setup.md). Product gaps: [TODOS.md](TODOS.md). Contact whoever runs this stack for the clinic.
