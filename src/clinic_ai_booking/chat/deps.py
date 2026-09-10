"""FastAPI dependencies for the chat agent."""

from __future__ import annotations

from fastapi import HTTPException, Request

from clinic_ai_booking.chat.agent import ClinicAgent


def get_chat_agent(request: Request) -> ClinicAgent:
    """Return the ClinicAgent bound on app.state during lifespan startup."""
    agent = getattr(request.app.state, "chat_agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="chat agent is not initialized")
    return agent
