"""Local STT/TTS adapters (faster-whisper + Piper via Voicebox)."""

from clinic_ai_booking.voice.client import (
    VoiceError,
    synthesize_speech,
    transcribe_audio,
)

__all__ = ["VoiceError", "synthesize_speech", "transcribe_audio"]
