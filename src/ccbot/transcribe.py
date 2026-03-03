"""Voice message transcription — converts audio to text via Whisper.

Supports two backends selected by CCBOT_WHISPER_BACKEND:
  - "local": Uses faster-whisper (CPU, no API key needed, requires ffmpeg)
  - "openai": Uses OpenAI Whisper API (requires OPENAI_API_KEY)
  - "off": Voice messages are rejected

Key function: transcribe() — async, returns transcribed text.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from .config import config

logger = logging.getLogger(__name__)

# Lazy-loaded backends
_local_model: Any = None
_openai_client: Any = None


class TranscriptionError(Exception):
    """Raised when transcription fails."""


class TranscriptionDisabled(Exception):
    """Raised when voice transcription is disabled or unavailable."""


def _get_local_model() -> Any:
    """Lazy-load the faster-whisper model (downloads on first use)."""
    global _local_model
    if _local_model is not None:
        return _local_model
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-untyped]
    except ImportError:
        raise TranscriptionDisabled(
            "faster-whisper is not installed. "
            'Install with: uv pip install -e ".[voice]"\n'
            "Or set CCBOT_WHISPER_BACKEND=off to disable voice messages."
        )
    logger.info(
        "Loading faster-whisper model '%s' (may download on first use)...",
        config.whisper_model,
    )
    _local_model = WhisperModel(config.whisper_model, device="cpu", compute_type="int8")
    logger.info("faster-whisper model loaded successfully")
    return _local_model


def _transcribe_local_sync(file_path: Path) -> str:
    """Synchronous transcription using faster-whisper (CPU-bound)."""
    model = _get_local_model()
    segments, info = model.transcribe(str(file_path), beam_size=5)
    text = " ".join(segment.text.strip() for segment in segments)
    if not text.strip():
        raise TranscriptionError("Transcription produced empty text")
    logger.info(
        "Transcribed %s: language=%s, duration=%.1fs, text_len=%d",
        file_path.name,
        info.language,
        info.duration,
        len(text),
    )
    return text.strip()


async def _transcribe_openai(file_path: Path) -> str:
    """Async transcription using OpenAI Whisper API."""
    global _openai_client
    if _openai_client is None:
        try:
            from openai import AsyncOpenAI  # type: ignore[import-untyped]
        except ImportError:
            raise TranscriptionDisabled(
                "openai package is not installed. "
                'Install with: uv pip install -e ".[voice-openai]"\n'
                "Or set CCBOT_WHISPER_BACKEND=local to use local transcription."
            )
        if not config.openai_api_key:
            raise TranscriptionDisabled(
                "OPENAI_API_KEY is required for the openai whisper backend."
            )
        _openai_client = AsyncOpenAI(api_key=config.openai_api_key)

    with open(file_path, "rb") as audio_file:
        transcript = await _openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
        )
    text = transcript.text.strip()
    if not text:
        raise TranscriptionError("Transcription produced empty text")
    logger.info(
        "Transcribed %s via OpenAI: text_len=%d",
        file_path.name,
        len(text),
    )
    return text


async def transcribe(file_path: Path) -> str:
    """Transcribe an audio file to text using the configured backend.

    Args:
        file_path: Path to the audio file (OGG/Opus from Telegram).

    Returns:
        Transcribed text string.

    Raises:
        TranscriptionDisabled: Backend is off or dependency missing.
        TranscriptionError: Transcription failed or produced empty text.
    """
    backend = config.whisper_backend

    if backend == "off":
        raise TranscriptionDisabled("Voice transcription is disabled.")

    if backend == "local":
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _transcribe_local_sync, file_path)

    if backend == "openai":
        return await _transcribe_openai(file_path)

    raise TranscriptionDisabled(f"Unknown backend: {backend}")
