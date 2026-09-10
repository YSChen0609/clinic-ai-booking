"""Tests for STT/TTS adapters and voice API wiring (voice service mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from clinic_ai_booking.config import VoiceSettings
from clinic_ai_booking.main import app
from clinic_ai_booking.voice.client import VoiceError, synthesize_speech, transcribe_audio


def _settings(**overrides: object) -> VoiceSettings:
    base = {
        "base_url": "http://voice.test:8790",
        "enabled": True,
        "stt_model": "stt",
        "tts_voice": "en_US-amy-medium",
        "timeout_seconds": 5.0,
    }
    base.update(overrides)
    return VoiceSettings(**base)  # type: ignore[arg-type]


def test_transcribe_audio_returns_text_when_ok() -> None:
    settings = _settings()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"text": " Book tomorrow morning "}
    mock_response.text = ""

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = mock_response

    with patch("clinic_ai_booking.voice.client.httpx.Client", return_value=mock_client):
        text = transcribe_audio(
            b"fake-audio",
            filename="clip.webm",
            content_type="audio/webm",
            settings=settings,
        )

    assert text == "Book tomorrow morning"
    mock_client.post.assert_called_once()
    args, kwargs = mock_client.post.call_args
    assert args[0] == "http://voice.test:8790/v1/audio/transcriptions"


def test_transcribe_audio_raises_when_service_unreachable() -> None:
    settings = _settings()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.side_effect = httpx.ConnectError("down")

    with patch("clinic_ai_booking.voice.client.httpx.Client", return_value=mock_client):
        with pytest.raises(VoiceError, match="unreachable"):
            transcribe_audio(
                b"fake-audio",
                filename="clip.webm",
                content_type="audio/webm",
                settings=settings,
            )


def test_transcribe_audio_rejects_empty_bytes() -> None:
    with pytest.raises(VoiceError, match="empty"):
        transcribe_audio(b"", filename="x.webm", content_type="audio/webm", settings=_settings())


def test_synthesize_speech_returns_wav_bytes_when_ok() -> None:
    settings = _settings()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"RIFF....WAVE"
    mock_response.text = ""

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = mock_response

    with patch("clinic_ai_booking.voice.client.httpx.Client", return_value=mock_client):
        audio = synthesize_speech("Hello clinic.", settings=settings)

    assert audio.startswith(b"RIFF")
    args, kwargs = mock_client.post.call_args
    assert args[0] == "http://voice.test:8790/v1/audio/speech"
    assert kwargs["json"]["voice"] == "en_US-amy-medium"
    assert kwargs["json"]["response_format"] == "wav"


def test_synthesize_speech_rejects_blank_text() -> None:
    with pytest.raises(VoiceError, match="empty"):
        synthesize_speech("   ", settings=_settings())


def test_api_transcribe_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_ENABLED", "true")
    monkeypatch.setenv("CHAT_ENABLED", "false")

    def fake_transcribe(audio: bytes, **kwargs: object) -> str:
        assert audio == b"abc"
        return "I need a cleaning"

    with patch("clinic_ai_booking.main.transcribe_audio", side_effect=fake_transcribe):
        with TestClient(app) as client:
            response = client.post(
                "/api/voice/transcribe",
                files={"file": ("clip.webm", b"abc", "audio/webm")},
            )

    assert response.status_code == 200
    assert response.json() == {"text": "I need a cleaning"}


def test_api_transcribe_rejects_empty_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_ENABLED", "true")
    monkeypatch.setenv("CHAT_ENABLED", "false")

    with TestClient(app) as client:
        response = client.post(
            "/api/voice/transcribe",
            files={"file": ("clip.webm", b"", "audio/webm")},
        )

    assert response.status_code == 400


def test_api_speak_returns_wav(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_ENABLED", "true")
    monkeypatch.setenv("CHAT_ENABLED", "false")

    with patch("clinic_ai_booking.main.synthesize_speech", return_value=b"RIFFWAV"):
        with TestClient(app) as client:
            response = client.post("/api/voice/speak", json={"text": "Hello"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content == b"RIFFWAV"


def test_api_speak_returns_502_when_voice_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_ENABLED", "true")
    monkeypatch.setenv("CHAT_ENABLED", "false")

    with patch(
        "clinic_ai_booking.main.synthesize_speech",
        side_effect=VoiceError("down"),
    ):
        with TestClient(app) as client:
            response = client.post("/api/voice/speak", json={"text": "Hello"})

    assert response.status_code == 502
