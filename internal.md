# Internal

Dental clinic booking assistant (English, `Asia/Taipei`). MVP **0.1.0**. Open work: [TODOS.md](TODOS.md).

## Architecture

| Piece | Role |
|-------|------|
| Postgres + `domain/booking.py` | Source of truth: hours, duration, junior/senior, free slots |
| `chat/` (extract → book graph → reply) | LLM extracts fields; engine books; replies are deterministic |
| `llm.py` / `voice/` | Provider swap points (Ollama + Voicebox today) |
| `notify/` (`CalendarPort` / `EmailPort`) | Google + Outlook adapters; fake by default |
| FastAPI + Compose | Web UI, session cookie, app/db/ollama/voice stack |

Flow: patient chat → extract/sanitize draft → engine list/check/book → optional calendar/email upsert.

## Choices

| Choice | Why | Trade-off |
|--------|-----|-----------|
| Engine before LLM | Rules and free starts must be deterministic | Chat UX follows engine statuses |
| Extract + sanitize, not free-form ReAct Agents with tools | Closed A–E / catalog doctors; tests assert draft/facts | Extra Python merge logic; edge typos still flake |
| LangGraph book loop | Enough for multi-turn book | Cancel/reschedule not in chat yet |
| Name + email cookie login | Fast local demo | No password — not public-internet safe |
| Clinic-owned calendars via ports | Expand vendors without rewriting booking | Real Outlook tenant setup is ops-heavy; Google is known-good |
| Cascaded voice (STT→text→TTS) | Reuses chat path | No barge-in |
| Compose | Reproducible local/prod-like stack | Rebuild app image after baked static/Python changes |

## Security

- Signed session cookie (`SESSION_SECRET`); cancel/reschedule API requires same user.
- Secrets via `.env` / Compose only (not in image); app runs as non-root.
- Public busy calendars omit patient names/emails.
- **Gap:** login has no password — add password/OAuth before any public deploy.

## Cost (production assumption)

| Item | Notes |
|------|--------|
| App + Postgres | Modest always-on compute; measure with host metrics |
| LLM | Dominant: local GPU/CPU for Ollama (free), or hosted API $ behind `llm.py` |
| Voice | Extra RAM/CPU for Whisper/Piper, or cloud STT/TTS $ |
| Google / Microsoft Graph | Free tiers / M365 license; API $ unknown — check vendor billing |
| Build / maintenance | Human hours for OAuth, model upgrades, booking-rule changes — track separately |

## Test / deploy

```bash
uv run pytest
docker compose up --build -d
docker compose exec ollama ollama pull qwen2.5:7b
```

GPU: `compose.yml` + `compose.gpu.yml`. Real calendars: [docs/oauth-setup.md](docs/oauth-setup.md). Smoke: [docs/smoke_test.md](docs/smoke_test.md).

## Next work

1. Reliable Outlook + dual Google/Outlook smoke
2. Chat cancel/reschedule
3. Constrained decoding for extract enums
4. Password/OAuth before public access

Details: [TODOS.md](TODOS.md).
