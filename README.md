# clinic-ai-booking

Dental clinic appointment assistant: patients browse doctors and book via chat (with optional voice in the messenger). English only. Clinic timezone: `Asia/Taipei`.

## What you get today

**Stage 0–2 behavior:** intro + doctor pages, Postgres booking engine, visitor vs login (name + email, no password v1), `POST /api/bookings` and cancel/reschedule gated behind login. Fake calendar/email adapters log actions.

**Stage 3 chat:** `POST /api/chat` → `ClinicAgent` (LangGraph turn graph: extract → booking engine subgraph → reply; needs Ollama).

**Stage 4 calendars:** each doctor page shows a placeholder intro plus a **busy-only** week view from Postgres (`GET /api/doctors/{slug}/busy`). No patient names or emails on public calendars.

**Stage 5 voice:** messenger mic → STT (faster-whisper via Voicebox) fills the text input (edit, then Send) → same `/api/chat` path. Bot replies stay text; **Play** runs Piper TTS. Text-only chat still works if the mic or voice service is unavailable.

**Stage 6 notify:** confirmed bookings dual-write the shared clinic **Google Calendar** and/or **Outlook** calendar (patient as attendee / invite) and email the patient. `pending_doctor` sends a pending email only (no confirmed calendar event until approved). Default `NOTIFY_MODE=fake`; set `NOTIFY_MODE=real` plus OAuth env vars (see `.env.example`). No patient OAuth.

**Security (MVP):** login is name + email only — fine for local demo, not for public production ([TODOS.md](TODOS.md)).

More: [smoke_test.md](smoke_test.md) · [oauth-setup.md](oauth-setup.md) · [host-requirements.md](host-requirements.md)

## Run (Docker + GPU)

```bash
cp .env.example .env
docker compose -f compose.yml -f compose.gpu.yml up --build -d
docker compose exec ollama ollama pull qwen2.5:7b
curl -s http://localhost:8000/health
curl -s http://localhost:8790/health
```

| What | Where |
|------|--------|
| Site | http://localhost:8000 |
| Doctors | `/doctors/junior`, `/doctors/senior-1`, `/doctors/senior-2` (busy blocks only) |
| Chat | `POST /api/chat` (Ollama); graph in `chat/turn_graph.py` + `chat/book_graph.py` |
| Voice STT/TTS | Compose `voice` (Voicebox `v0.2.16` built from git): `POST /api/voice/transcribe`, `POST /api/voice/speak` |
| Calendar/email | `NOTIFY_MODE=real` + Google/MS env (adapters in `adapters/`); fakes otherwise |
| Postgres | `localhost:${POSTGRES_PORT:-5432}` |

CPU-only: `docker compose up --build -d`.

### OAuth setup (stage 6 real notify)

Step-by-step client id / secret / refresh token (Google + Outlook) and references: **[oauth-setup.md](oauth-setup.md)**. Then set `NOTIFY_MODE=real` and run the stage 6 section in [smoke_test.md](smoke_test.md#google--outlook-notify-stage-6--real-credentials-local-only).

### Voice hardware / RAM notes

- Voice service image: built from [voicebox `v0.2.16`](https://github.com/agjs/voicebox) (CPU). The published `ghcr.io/agjs/voicebox` package is private, so Compose builds from git. STT is **faster-distil-whisper-small.en**; TTS is **Piper** (`en_US-amy-medium` by default). First `--build` can take several minutes (models bake into the image).
- Budget roughly **0.5–1+ GiB RAM** for the voice container on top of Ollama + Postgres + app.
- Disable voice without removing Compose: `VOICE_ENABLED=false` (messenger stays text-only).
- Optional cloud STT/TTS for deploys without local Whisper/Piper: see [TODOS.md](TODOS.md).

## Tests

```bash
docker compose up -d db
uv sync --group dev
uv run pytest
```

## Trade-offs

- Chat: fixed LangGraph turn (extract + engine booking subgraph + reply). Multi-turn via draft cookie + checkpointer.
- Voice is cascaded (STT → text chat → TTS of the same reply), not realtime barge-in / pure voice mode.
- Postgres is source of truth for hours/slots; public UI calendars filter busy blocks per professional; Google/Outlook are clinic notify mirrors.
- Cookie session + name/email login.
