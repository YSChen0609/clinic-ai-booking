# clinic-ai-booking

Dental clinic booking assistant: chat (+ optional voice), Postgres as source of truth, optional Google/Outlook notify. English · `Asia/Taipei` · **0.1.0**.

Login is name + email with **no password** — local demo only.

## Quick start

```bash
git clone https://github.com/YSChen0609/clinic-ai-booking.git
cd clinic-ai-booking
cp .env.example .env

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

SSH clone: `git clone git@github.com:YSChen0609/clinic-ai-booking.git`

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

- [external.md](external.md) — product, install, use (clinic / operator)
- [internal.md](internal.md) — architecture, LangGraph, choices, cost, security, tests
- [docs/smoke_test.md](docs/smoke_test.md) · [TODOS.md](TODOS.md)
