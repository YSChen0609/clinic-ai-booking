# Internal — architecture and ops

Dental clinic booking assistant (English UI, `Asia/Taipei`). Assignment / local demo MVP.

## Layout

```
clinic-ai-booking/
  README.md, LICENSE, TODOS.md, pyproject.toml, uv.lock
  Dockerfile, compose.yml, compose.gpu.yml, .env.example
  docs/          # this file, external.md, smoke, OAuth, host, rules
  src/clinic_ai_booking/
    main.py          # FastAPI: pages, auth, booking API, chat, voice proxy
    config.py        # env settings (no secrets in source)
    auth.py, db.py, llm.py
    domain/          # models, hours, doctors, booking engine
    chat/            # LangGraph turn graph + Ollama
    notify/          # CalendarPort / EmailPort + adapters/
    voice/           # STT/TTS client → Compose voice service
    templates/, static/
  tests/
```

`prompts/` is local Agent staging only (gitignored).

## Data flow

1. **Postgres** is source of truth for professionals, services, users, bookings.
2. **Booking engine** (`domain/booking.py`) enforces hours, caps, junior/senior rules, `pending_doctor` for service E overtime — see [booking_rules.md](booking_rules.md).
3. **Public doctor calendars** read busy blocks only from Postgres (`GET /api/doctors/{slug}/busy`). No patient PII on the UI.
4. **Chat** `POST /api/chat` → `ClinicAgent` → LangGraph turn graph (`chat/turn_graph.py`: extract → `book_graph` over the engine → reply). LLM via LangChain Ollama (`llm.py`). Sticky draft / contact / `thread_id` on the signed session cookie; in-memory LangGraph checkpointer. Not Deep Agents; leftover `create_agent` / `chat/tools.py` middleware is unused dead code (see [TODOS.md](../TODOS.md)).
5. **Voice** — messenger mic → app proxies to Voicebox STT → text fills input → same chat path; Play → Piper TTS.
6. **Notify** — on book/cancel/reschedule, `notify/service.py` calls `CalendarPort` / `EmailPort`. Default fakes; `NOTIFY_MODE=real` wires Google and/or Outlook adapters. Confirmed → calendar upsert + email; `pending_doctor` → pending email only.

## Main choices

| Choice | Why |
|--------|-----|
| Postgres booking engine first | Deterministic clinic rules; LLM must not invent slots |
| LangGraph turn graph (not Deep Agents) | Fixed book loop; enough for MVP multi-turn |
| Cookie session + name/email login | Fast demo auth; not production identity |
| Fake notify by default | Tests and local UI without vendor secrets |
| Clinic-owned Google/Outlook only | No patient OAuth; patient is calendar attendee |
| Voice cascaded (STT→text→TTS) | Reuses chat path; no realtime barge-in |
| Compose + pinned images | Reproducible local stack; GPU optional overlay |

## Security that exists

- Signed session cookie (`SESSION_SECRET`); change for anything beyond local demo.
- Cancel / reschedule API gated on login (same user as booking).
- Secrets only via `.env` / Compose env — not baked into the app image (`.dockerignore` excludes `.env`).
- App container runs as non-root `appuser` (uid 10001).
- Public busy calendars omit names/emails.
- **Limitation:** login is **name + email only (no password)**. Fine for local demo; do **not** expose to the public internet without password or OAuth ([TODOS.md](../TODOS.md)).

## Test

```bash
docker compose up -d db
uv sync --group dev
uv run pytest
```

Manual MVP path: [smoke_test.md](smoke_test.md). DB peek: [db.md](db.md).

## Deploy / run

Local: [README.md](../README.md). Host GPU notes: [host-requirements.md](host-requirements.md). Real calendars: [oauth-setup.md](oauth-setup.md).

```bash
docker compose up --build -d          # CPU
# or: docker compose -f compose.yml -f compose.gpu.yml up --build -d
docker compose exec ollama ollama pull qwen2.5:7b
curl -s http://localhost:8000/health
```

No Kubernetes / CI in this repo unless added later.

Production-ish checklist if you host it: set strong `SESSION_SECRET` + DB password; TLS terminator in front; password/OAuth before public access; keep `NOTIFY_MODE=real` credentials in a secret store, not the image; swap Ollama for a hosted LLM behind `llm.py` if you leave the laptop (adapter boundary already exists).

## Cost notes

| Item | Estimate | How to measure |
|------|----------|----------------|
| App + Postgres containers | Low CPU/RAM on a laptop | `docker stats` |
| Ollama `qwen2.5:7b` | Dominant; CPU slow, GPU needs ~4–6+ GiB VRAM (card-dependent) | `ollama ps`, `nvidia-smi` |
| Voicebox (Whisper + Piper) | Roughly 0.5–1+ GiB RAM extra; first image build multi-minute | `docker stats` on `voice` |
| Google Calendar / Gmail / Graph | Vendor free tiers / M365 license; **API $ unknown** for your volume | Cloud billing consoles after real smoke |
| Build/maintenance (human) | Unknown | Track hours per stage / deploy |

Do not treat cloud API or electricity cost as zero without measuring your own usage.
