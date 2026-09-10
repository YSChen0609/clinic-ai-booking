# clinic-ai-booking

Dental clinic appointment assistant (assignment MVP): browse doctors, book via chat (optional voice), Postgres as source of truth, optional Google/Outlook notify. English only. Timezone: `Asia/Taipei`.

Version: **0.1.0** (see [TODOS.md](TODOS.md) for next work).

**Security:** login is name + email with **no password** — local demo only, not public production.

## Against [instructions.txt](../instructions.txt)

| Requirement | How this repo meets it |
|-------------|------------------------|
| Working web app | FastAPI + Compose: intro, doctor busy calendars, messenger |
| Chat + voice AI, provider-agnostic | Chat via `llm.py` (Ollama today); voice via `voice/` → Voicebox; swap points exist |
| Scope / busy / duration | Engine in `domain/booking.py`; chat cannot invent free slots |
| 3 professionals, A–E (junior A–B only) | Seeded catalog + booking rules |
| Outlook **and** Google | Same `CalendarPort` / `EmailPort`; Google is the known-good real path; Outlook adapters are implemented — real tenant setup is the remaining ops risk |
| Internal + external docs | [internal.md](internal.md), [external.md](external.md) |

## Architecture

**Postgres + booking engine** own truth (hours, duration, junior/senior, caps). The LLM only **extracts** intent/fields (`chat/extract.py`); the **book graph** calls the engine and **deterministic replies** render facts. Calendars hang off notify adapters so Google/Outlook can expand without rewriting booking logic. Why / trade-offs: [internal.md](internal.md).

## Quick start (Compose)

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec ollama ollama pull qwen2.5:7b
curl -s http://localhost:8000/health
```

| | |
|--|--|
| Site | http://localhost:8000 |
| Voice health | http://localhost:8790/health |
| GPU Ollama | `docker compose -f compose.yml -f compose.gpu.yml up --build -d` |

Short manual path (happy book + junior rule + optional Google notify): [docs/smoke_test.md](docs/smoke_test.md).

## Docs

| Doc | Audience |
|-----|----------|
| [external.md](external.md) | Clinic / operator: product, install, OAuth outside the app |
| [internal.md](internal.md) | Developers: architecture, choices, limits, costs |
| [docs/smoke_test.md](docs/smoke_test.md) | End-to-end MVP checklist |
| [docs/oauth-setup.md](docs/oauth-setup.md) | Google + Outlook credential steps |
| [docs/host-requirements.md](docs/host-requirements.md) | CPU vs NVIDIA GPU host setup |
| [docs/ghcr-publish.md](docs/ghcr-publish.md) | Build/push app image to `ghcr.io` |
| [docs/secrets-runtime.md](docs/secrets-runtime.md) | Why `.env` is not in the image; runtime injection |
| [TODOS.md](TODOS.md) | Deferred / next-stage work |

## Tests

```bash
docker compose up -d db
uv sync --group dev
uv run pytest
```

Live Ollama dialogue regression (skipped by default; needs `db` + `ollama` + pulled model):

```bash
docker compose up -d db ollama
docker compose exec ollama ollama pull qwen2.5:7b
uv run pytest -m llm
```

When a messenger smoke bug shows up: add a script under `tests/dialogues/` (or extend `test_llm_dialogues.py`) that fails first, then fix. Assert `facts` / draft / booking id — not exact reply wording. See [docs/smoke_test.md](docs/smoke_test.md).

## Layout

Root holds packaging, Compose, and entry docs. Code lives in `src/`, tests in `tests/`, operator/dev docs in `docs/`.
