# clinic-ai-booking

Dental clinic appointment assistant: patients browse doctors and book via chat (voice and calendar sync come later). English only. Clinic timezone: `Asia/Taipei`.

## What you get today

**Stage 0–2 behavior:** intro + doctor pages, Postgres booking engine, visitor vs login (name + email, no password v1), `POST /api/bookings` and cancel/reschedule gated behind login. Fake calendar/email adapters log actions.

**Not stage 3:** the messenger panel is still on every page, but the **chat agent is unwired** — `POST /api/chat` returns a stub, `POST /api/faq` returns 501. Booking tools live in `chat_tools.py` for your redesign; they are not called from the UI yet.

**Security (MVP):** login is name + email only — fine for local demo, not for public production ([TODOS.md](TODOS.md)).

More: [prompts/](prompts/) · [smoke_test.md](smoke_test.md) · [host-requirements.md](host-requirements.md)

## Run (Docker + GPU)

Assumes NVIDIA driver + [Container Toolkit](host-requirements.md) (or Docker Desktop GPU on WSL2).

```bash
cp .env.example .env
docker compose -f compose.yml -f compose.gpu.yml up --build -d
docker compose exec ollama ollama pull qwen2.5:7b   # once; for when you re-wire chat
curl -s http://localhost:8000/health              # {"status":"ok"}
```

| What | Where |
|------|--------|
| Site | http://localhost:8000 |
| Doctors | `/doctors/junior`, `/doctors/senior-1`, `/doctors/senior-2` |
| Booking API | `POST /api/bookings`, cancel/reschedule when logged in |
| Chat | stub only — redesign pending |
| Postgres | `localhost:${POSTGRES_PORT:-5432}` |

CPU-only host: drop the GPU overlay — `docker compose up --build -d` (Ollama runs on CPU).

```bash
docker compose logs -f
docker compose down
```

## Tests

```bash
docker compose up -d db
uv sync --group dev
uv run pytest
uv run python tests/smoke_booking.py
```

## Trade-offs

- **Compose + pinned images** — app, Postgres, Ollama (GPU passthrough via `compose.gpu.yml`).
- **Messenger shell, no agent** — `chat_tools.BOOKING_TOOLS` wraps the booking engine for a future agent.
- **Postgres is source of truth** — hours, breaks, E overtime enforced in the engine.
- **Session cookie + name/email login** — cancel/reschedule require login. No OAuth / admin UI yet.
