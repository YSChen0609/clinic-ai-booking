# Deferred work

## Still open (by stage)

- [ ] **Redesign chat agent** — stage-03 agent/middleware stack fully removed; only `chat_tools.py` (BOOKING_TOOLS) remains. Wire a new agent flow yourself; keep Ollama/GPU Compose path when you re-add an LLM adapter.
- [ ] Stage 6: replace fake `CalendarPort` / `EmailPort` (`fakes.py` / `notify.py`) with real Google Calendar + Outlook (see `prompts/stage-06-google-outlook.md`)
- [ ] Voice (STT/TTS in messenger) — stage 5
- [ ] Doctor approve UI for service E `pending_doctor` overtime bookings (engine stores them; no staff UI yet)
- [ ] Password auth (replace no-password name+email MVP login)
- [ ] OAuth (after password / stronger auth)
- [ ] CI (GitHub Actions) — skip until asked
- [ ] MCP — skip for v1

## Done / noted

- [x] Stage 3 agent stack (`create_agent` / custom loop / FAQ / middleware) **removed**; booking tools kept in `chat_tools.py`
- [x] Fake calendar/email adapters record/log calls; swap at `set_ports` / `notify.py` in stage 6

## Later options (not planned for v1)

- [ ] **Deep Agents** — only if we need long-horizon planning / subagents; not for the clinic booking loop.
- [ ] Cloud STT/TTS for deploys without local Whisper/Piper
