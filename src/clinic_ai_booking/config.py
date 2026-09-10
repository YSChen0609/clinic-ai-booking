"""App settings loaded from environment (no secrets in source)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no"}


def _env_str(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


@dataclass(frozen=True)
class ChatSettings:
    """Chat agent and LLM configuration."""

    ollama_model: str
    chat_enabled: bool

    @classmethod
    def from_env(cls) -> ChatSettings:
        """Load chat settings from environment variables."""
        model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b").strip() or "qwen2.5:7b"
        return cls(ollama_model=model, chat_enabled=_env_bool("CHAT_ENABLED", True))


@dataclass(frozen=True)
class VoiceSettings:
    """Local STT/TTS (Voicebox: faster-whisper + Piper)."""

    base_url: str
    enabled: bool
    stt_model: str
    tts_voice: str
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> VoiceSettings:
        """Load voice settings from environment variables."""
        base = (
            os.environ.get("VOICE_BASE_URL", "").strip()
            or "http://127.0.0.1:8790"
        ).rstrip("/")
        timeout_raw = os.environ.get("VOICE_TIMEOUT_SECONDS", "60").strip() or "60"
        try:
            timeout = float(timeout_raw)
        except ValueError:
            timeout = 60.0
        return cls(
            base_url=base,
            enabled=_env_bool("VOICE_ENABLED", True),
            stt_model=(os.environ.get("VOICE_STT_MODEL", "").strip() or "stt"),
            tts_voice=(
                os.environ.get("VOICE_TTS_VOICE", "").strip() or "en_US-amy-medium"
            ),
            timeout_seconds=max(1.0, timeout),
        )


@dataclass(frozen=True)
class NotifySettings:
    """Clinic Google/Outlook calendar + email credentials (env only)."""

    mode: str
    email_provider: str
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    google_calendar_id: str
    google_sender: str
    ms_tenant_id: str
    ms_client_id: str
    ms_client_secret: str
    ms_refresh_token: str
    ms_user_upn: str
    ms_calendar_id: str

    @classmethod
    def from_env(cls) -> NotifySettings:
        """Load notify adapter settings from environment variables."""
        mode = _env_str("NOTIFY_MODE", "fake").lower() or "fake"
        if mode not in {"fake", "real"}:
            mode = "fake"
        email_provider = _env_str("EMAIL_PROVIDER", "auto").lower() or "auto"
        if email_provider not in {"auto", "google", "outlook", "both"}:
            email_provider = "auto"
        return cls(
            mode=mode,
            email_provider=email_provider,
            google_client_id=_env_str("GOOGLE_CLIENT_ID"),
            google_client_secret=_env_str("GOOGLE_CLIENT_SECRET"),
            google_refresh_token=_env_str("GOOGLE_REFRESH_TOKEN"),
            google_calendar_id=_env_str("GOOGLE_CALENDAR_ID", "primary") or "primary",
            google_sender=_env_str("GOOGLE_SENDER"),
            ms_tenant_id=_env_str("MS_TENANT_ID"),
            ms_client_id=_env_str("MS_CLIENT_ID"),
            ms_client_secret=_env_str("MS_CLIENT_SECRET"),
            ms_refresh_token=_env_str("MS_REFRESH_TOKEN"),
            ms_user_upn=_env_str("MS_USER_UPN"),
            ms_calendar_id=_env_str("MS_CALENDAR_ID"),
        )

    @property
    def google_ready(self) -> bool:
        """True when Google OAuth refresh credentials are present."""
        return bool(
            self.google_client_id
            and self.google_client_secret
            and self.google_refresh_token
        )

    @property
    def outlook_ready(self) -> bool:
        """True when Microsoft Graph app + clinic mailbox identity are present."""
        return bool(
            self.ms_tenant_id
            and self.ms_client_id
            and self.ms_client_secret
            and self.ms_user_upn
        )
