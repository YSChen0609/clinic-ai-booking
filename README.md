# clinic-ai-booking

Dental clinic booking assistant: chat (+ optional voice), Postgres as source of truth, optional Google/Outlook notify. English · `Asia/Taipei` · **0.1.0**.

Login is name + email with **no password** — local demo only.

## Quick start

### 1. Clone

```bash
git clone https://github.com/YSChen0609/clinic-ai-booking.git
cd clinic-ai-booking
```

SSH: `git clone git@github.com:YSChen0609/clinic-ai-booking.git`

### 2. Configure `.env`

```bash
cp .env.example .env
```

Edit `.env` before Compose (never commit it):

| Variable | What to set |
|----------|-------------|
| `POSTGRES_PASSWORD` | Strong password for the Compose Postgres service |
| `SESSION_SECRET` | Uncomment and set a long random string |
| `POSTGRES_PORT` / `APP_PORT` / `VOICE_PORT` | Change if host ports collide |
| `NOTIFY_MODE` | Keep `fake` for local demo without calendars |
| `VOICE_ENABLED` | `false` for text-only |

```env
POSTGRES_PASSWORD=choose_a_local_password
SESSION_SECRET=choose_a_long_random_string
NOTIFY_MODE=fake
```

Calendar credentials (`GOOGLE_*` / `MS_*`) only when `NOTIFY_MODE=real` — see [docs/oauth-setup.md](docs/oauth-setup.md).

### 3. Spin up

```bash
# CPU (default)
docker compose up --build -d

# Or NVIDIA GPU for faster Ollama — see docs/host-requirements.md
docker compose -f compose.yml -f compose.gpu.yml up --build -d

docker compose exec ollama ollama pull qwen2.5:7b
curl -s http://localhost:8000/health
```

| | |
|--|--|
| Site | http://localhost:8000 |
| Voice | http://localhost:8790/health |

## Tech stack

| Layer | Choice |
|-------|--------|
| App | FastAPI, Jinja, cookie session |
| Data | Postgres + SQLAlchemy booking engine |
| Chat | LangGraph + LangChain Ollama (`qwen2.5:7b`) |
| Voice | Voicebox (Whisper + Piper) behind `voice/` |
| Notify | `CalendarPort` / `EmailPort` — Google + Outlook; fake by default |
| Run | Docker Compose (app, db, ollama, voice); optional GPU |

## Demo

Messenger on the site:

![Clinic AI Booking UI](docs/images/demo-ui.png)

Confirmed book mirrored to clinic Google Calendar (`NOTIFY_MODE=real`):

![Clinic Google Calendar event](docs/images/demo-clinic-calendar.png)


## Docs

- [external.md](external.md) — clinic-facing product and use guide
- [internal.md](internal.md) — architecture, install, cost, security, tests
- [docs/smoke_test.md](docs/smoke_test.md)

## Future work

Deferred items: [TODOS.md](TODOS.md) (also summarized at the end of [internal.md](internal.md#future-work)).
