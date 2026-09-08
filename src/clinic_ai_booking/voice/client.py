"""HTTP client for OpenAI-shaped STT/TTS (Voicebox: faster-whisper + Piper)."""

from __future__ import annotations

import logging

import httpx

from clinic_ai_booking.config import VoiceSettings

logger = logging.getLogger(__name__)


class VoiceError(Exception):
    """STT or TTS call failed."""


def transcribe_audio(
    audio: bytes,
    *,
    filename: str,
    content_type: str,
    settings: VoiceSettings | None = None,
) -> str:
    """Transcribe audio bytes to text via the voice service."""
    cfg = settings or VoiceSettings.from_env()
    if not cfg.enabled:
        raise VoiceError("voice is disabled")
    if not audio:
        raise VoiceError("audio is empty")

    name = (filename or "audio.webm").strip() or "audio.webm"
    mime = (content_type or "application/octet-stream").strip() or "application/octet-stream"
    url = f"{cfg.base_url}/v1/audio/transcriptions"
    try:
        with httpx.Client(timeout=cfg.timeout_seconds) as client:
            response = client.post(
                url,
                files={"file": (name, audio, mime)},
                data={"model": cfg.stt_model, "response_format": "json"},
            )
    except httpx.HTTPError as exc:
        logger.exception("STT request failed")
        raise VoiceError("speech-to-text service unreachable") from exc

    if response.status_code >= 400:
        logger.error("STT error status=%s body=%s", response.status_code, response.text[:300])
        raise VoiceError("speech-to-text failed")

    try:
        payload = response.json()
    except ValueError as exc:
        raise VoiceError("speech-to-text returned invalid JSON") from exc

    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        raise VoiceError("speech-to-text returned no text")
    return text.strip()


def synthesize_speech(
    text: str,
    *,
    settings: VoiceSettings | None = None,
) -> bytes:
    """Synthesize WAV audio from text via Piper on the voice service."""
    cfg = settings or VoiceSettings.from_env()
    if not cfg.enabled:
        raise VoiceError("voice is disabled")
    cleaned = text.strip()
    if not cleaned:
        raise VoiceError("text is empty")

    url = f"{cfg.base_url}/v1/audio/speech"
    body = {
        "model": "tts",
        "input": cleaned,
        "voice": cfg.tts_voice,
        "response_format": "wav",
    }
    try:
        with httpx.Client(timeout=cfg.timeout_seconds) as client:
            response = client.post(url, json=body)
    except httpx.HTTPError as exc:
        logger.exception("TTS request failed")
        raise VoiceError("text-to-speech service unreachable") from exc

    if response.status_code >= 400:
        logger.error("TTS error status=%s body=%s", response.status_code, response.text[:300])
        raise VoiceError("text-to-speech failed")

    audio = response.content
    if not audio:
        raise VoiceError("text-to-speech returned empty audio")
    return audio
