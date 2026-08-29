# Deferred work

## Still open (by stage)

- [ ] Stage 6: replace fake `CalendarPort` / `EmailPort` (`fakes.py` / `notify.py`) with real Google Calendar + Outlook (see `prompts/stage-06-google-outlook.md`)
- [ ] Voice (STT/TTS in messenger) — stage 5
- [ ] Doctor approve UI for service E `pending_doctor` overtime bookings (engine stores them; no staff UI yet)
- [ ] Password auth (replace no-password name+email MVP login)
- [ ] OAuth (after password / stronger auth)
- [ ] CI (GitHub Actions) — skip until asked
- [ ] MCP — skip for v1

## Done / noted

- [x] Stage 3: chat is LangChain **`create_agent`** (LangGraph runtime) + Ollama + booking tools + middleware guardrails — **not** Deep Agents
- [x] Stage 3: fake calendar/email adapters record/log calls; swap at `set_ports` / `notify.py` in stage 6

## Later options (not planned for v1)

- [ ] **Custom LangGraph graph** — if `create_agent` + middleware is not enough (e.g. need explicit deterministic edges / mixed agentic+fixed pipeline, or clearer node-level audit of every step). Keep booking tools and ports; rewrite only the agent orchestration. Prefer staying on `create_agent` until that pain is real.
- [ ] **Deep Agents** — only if we need long-horizon planning, subagents, or filesystem/context offloading; not for the clinic booking loop.
- [ ] Harden chat guardrails further (eval set, stricter classifiers) beyond stage-3 middleware baseline
- [ ] Cloud STT/TTS for deploys without local Whisper/Piper
- [ ] Swap Ollama for a hosted LLM behind the same `llm.py` adapter
