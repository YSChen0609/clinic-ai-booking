"""LLM vendor adapter. Ollama today; swap the factory later without touching the agent."""

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from clinic_ai_booking.config import ChatSettings

DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"


def make_chat_model(
    *, tag: str | None = None, temperature: float | None = None
) -> BaseChatModel:
    """Build the chat model (Ollama by default)."""
    model_tag = tag or ChatSettings.from_env().ollama_model
    base_url = (
        os.environ.get("OLLAMA_BASE_URL", "").strip()
        or os.environ.get("OLLAMA_HOST", "").strip()
        or None
    )
    kwargs: dict = {}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if base_url:
        return init_chat_model(f"ollama:{model_tag}", base_url=base_url, **kwargs)
    return init_chat_model(f"ollama:{model_tag}", **kwargs)
