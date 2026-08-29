"""LLM vendor adapter. Ollama today; swap the factory later without touching tools."""

from __future__ import annotations

import os

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama

# Tool-capable local default. Override with OLLAMA_MODEL.
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"


def ollama_model_tag() -> str:
    """Return the configured Ollama model tag."""
    return os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip() or DEFAULT_OLLAMA_MODEL


def ollama_base_url() -> str:
    """Return the Ollama HTTP base URL."""
    return (
        os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL).strip()
        or DEFAULT_OLLAMA_BASE_URL
    )


def make_chat_model(*, model: str | None = None, base_url: str | None = None) -> BaseChatModel:
    """Build the chat model used by create_agent (Ollama)."""
    return ChatOllama(
        model=model or ollama_model_tag(),
        base_url=base_url or ollama_base_url(),
        temperature=0,
    )
