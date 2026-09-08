"""Build and tear down the process-wide ClinicAgent."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver

from clinic_ai_booking.chat.agent import ClinicAgent
from clinic_ai_booking.config import ChatSettings
from clinic_ai_booking.llm import make_chat_model


def create_chat_agent(
    *,
    settings: ChatSettings | None = None,
    model: BaseChatModel | None = None,
    checkpointer: InMemorySaver | None = None,
) -> ClinicAgent:
    """Compile a ClinicAgent from settings (used at app startup and in tests)."""
    cfg = settings or ChatSettings.from_env()
    return ClinicAgent(
        model=model or make_chat_model(tag=cfg.ollama_model),
        settings=cfg,
        checkpointer=checkpointer,
    )


def shutdown_chat_agent(agent: ClinicAgent | None) -> None:
    """Release agent resources on app shutdown (placeholder for future cleanup)."""
    del agent
