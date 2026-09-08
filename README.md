# clinic-ai-booking

Dental clinic appointment assistant: browse doctors, book via chat (optional voice), Postgres as source of truth, optional Google/Outlook notify. English only. Timezone: `Asia/Taipei`.

**Security:** login is name + email with **no password** — local demo only, not public production.

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

## Docs

| Doc | Audience |
|-----|----------|
| [docs/external.md](docs/external.md) | Clinic / operator: product, install, OAuth outside the app |
| [docs/internal.md](docs/internal.md) | Developers: architecture, security, test/deploy, costs |
| [docs/smoke_test.md](docs/smoke_test.md) | End-to-end MVP demo checklist |
| [docs/oauth-setup.md](docs/oauth-setup.md) | Google + Outlook credential steps |
| [docs/host-requirements.md](docs/host-requirements.md) | CPU vs NVIDIA GPU host setup |
| [docs/ghcr-publish.md](docs/ghcr-publish.md) | Build/push app image to `ghcr.io` |
| [TODOS.md](TODOS.md) | Deferred work |

## Tests

```bash
docker compose up -d db
uv sync --group dev
uv run pytest
```

## Layout

Root holds packaging, Compose, and entry docs. Code lives in `src/`, tests in `tests/`, operator/dev docs in `docs/`.
