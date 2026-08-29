# clinic-ai-booking

Dental clinic appointment assistant: patients browse doctors and book via chat (voice and calendar sync come later). English only. Clinic timezone: `Asia/Taipei`.

## What you get today

A local demo: intro page, three doctor placeholders, messenger chat on every page, Postgres booking engine, visitor vs login (name + email, no password in v1), and a **LangChain `create_agent`** chatbot over **Ollama** with booking tools and middleware guardrails. Calendar/email use **fake adapters** that log actions (real Google/Outlook later).

**Security (MVP):** login is name + email only — anyone who knows an email can open that session. Fine for local demo; not for public production. Password auth and OAuth are deferred ([TODOS.md](TODOS.md)).

Deferred work: [TODOS.md](TODOS.md). Stage checklists: [prompts/](prompts/). Auth smoke steps: [smoke_test.md](smoke_test.md). Machine prerequisites (Docker / optional NVIDIA GPU): [host-requirements.md](host-requirements.md).

## Use it

Needs Docker Desktop (or another Compose engine). **Host prerequisites** (Docker, optional NVIDIA GPU path): [host-requirements.md](host-requirements.md).

```bash
cp .env.example .env
# CPU (works anywhere Docker runs):
docker compose up --build -d
# NVIDIA GPU inference (host must pass the checklist in host-requirements.md):
# docker compose -f compose.yml -f compose.gpu.yml up --build -d
```

### Ollama model (required for chat)

Chat uses LangChain `create_agent` + `langchain-ollama`. Default model tag: **`qwen2.5:7b`** (override with `OLLAMA_MODEL` in `.env`). Inference runs in the **Ollama** container (or a host Ollama if you point `OLLAMA_BASE_URL` there); the app image itself does not use the GPU.

Pull after Compose is up (host port default `11434`):

```bash
docker compose exec ollama ollama pull qwen2.5:7b
# with GPU overlay, use the same -f flags as `up`:
# docker compose -f compose.yml -f compose.gpu.yml exec ollama ollama pull qwen2.5:7b
# or from a machine with the ollama CLI pointed at localhost:11434:
# ollama pull qwen2.5:7b
```

Confirm: `docker compose exec ollama ollama list`

| What | Where |
|------|--------|
| Site | http://localhost:8000 |
| Doctors | `/doctors/junior`, `/doctors/senior-1`, `/doctors/senior-2` |
| Health | http://localhost:8000/health |
| Chat API | `POST /api/chat` `{"message":"..."}` (session cookie for login + thread) |
| Postgres (host) | `localhost:${POSTGRES_PORT}` (default `5432`; change in `.env` if taken) |
| Ollama | http://localhost:11434 |

```bash
docker compose logs -f    # follow logs (tool_call / fake calendar+email lines)
docker compose down       # stop
```

Booking-engine, auth, and chat-agent tests need Postgres (Compose `db` is enough). They use database `clinic_test` on `localhost:${POSTGRES_PORT}`. Chat tests mock the LLM (no Ollama required for pytest).

```bash
docker compose up -d db
uv sync --group dev
uv run pytest
uv run python tests/smoke_booking.py          # optional stage-1 booking smoke
# Auth + chat smoke (browser + curl): see smoke_test.md
```

## Trade-offs (so far)

- **Compose + pinned images** — one command to run app, DB, and LLM host; first pull needs registry access. We avoid depending on ghcr.io for `uv` (install via pip in the image).
- **GPU is host passthrough, not in the image** — official `ollama/ollama` already has CUDA userspace; `compose.gpu.yml` requests NVIDIA devices. Driver + toolkit/Desktop GPU stay on the machine ([host-requirements.md](host-requirements.md)). Base Compose stays CPU-safe for shipability.
- **Server-rendered pages + plain JS chat** — posts to `/api/chat`; not a SPA.
- **`create_agent` (not Deep Agents)** — model ↔ tools loop with middleware guardrails; multi-turn via LangGraph checkpointer + session `thread_id`. Swap the LLM behind `llm.py` later.
- **Postgres is the booking source of truth** — overlap exclusion in the database; hours/breaks/E overtime enforced in the engine. Fake calendar/email log until stage 6.
- **Signed cookie session + name/email login** — Starlette `SessionMiddleware`; account created on first successful book (and on login upsert). Cancel/reschedule require login. No OAuth / no admin UI yet.
