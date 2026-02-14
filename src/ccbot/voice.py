"""Voice message transcription via Google Gemini API.

Downloads voice/audio files from Telegram and transcribes them using
Gemini's multimodal capabilities (inline base64 audio). Uses httpx
for the REST API call — no additional dependencies needed.

On transient errors (429/503), retries once with a fallback model.

Key function: transcribe_voice(audio_bytes, mime_type) -> str
"""

import asyncio
import base64
import logging
from dataclasses import dataclass

import httpx

from .config import config

logger = logging.getLogger(__name__)

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_FALLBACK_MODEL = "gemini-2.5-flash"

_TRANSCRIBE_PROMPT = (
    "Transcribe this audio exactly as spoken. "
    "Return only the transcription text, no explanations or formatting."
)


@dataclass
class TranscriptionResult:
    """Result of a voice transcription."""

    text: str
    model: str


async def _call_gemini(
    payload: dict, model: str, client: httpx.AsyncClient,
) -> httpx.Response:
    """POST to Gemini generateContent endpoint."""
    url = f"{_GEMINI_BASE}/{model}:generateContent"
    return await client.post(url, params={"key": config.gemini_api_key}, json=payload)


async def transcribe_voice(
    audio_bytes: bytes,
    mime_type: str = "audio/ogg",
    on_status: "asyncio.Future[None] | None" = None,
) -> TranscriptionResult:
    """Transcribe audio bytes using Google Gemini API.

    Tries the configured model first; on 429/503 retries once after 2s,
    then falls back to gemini-2.5-flash.
    """
    if not config.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is not configured")

    encoded = base64.b64encode(audio_bytes).decode("ascii")

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": encoded,
                        },
                    },
                    {"text": _TRANSCRIBE_PROMPT},
                ],
            },
        ],
    }

    primary = config.gemini_model
    async with httpx.AsyncClient(timeout=30.0) as client:
        logger.info("Trying Gemini model: %s", primary)
        resp = await _call_gemini(payload, primary, client)

        if resp.status_code in (429, 503):
            logger.warning("Gemini %s returned %d, retrying in 2s...", primary, resp.status_code)
            await asyncio.sleep(2)
            resp = await _call_gemini(payload, primary, client)

        if resp.status_code in (429, 503) and primary != _FALLBACK_MODEL:
            logger.warning("Gemini %s still %d, falling back to %s", primary, resp.status_code, _FALLBACK_MODEL)
            resp = await _call_gemini(payload, _FALLBACK_MODEL, client)
            if resp.status_code == 200:
                primary = _FALLBACK_MODEL

    if resp.status_code != 200:
        logger.error("Gemini API error %d: %s", resp.status_code, resp.text)
        raise RuntimeError(f"Gemini API error {resp.status_code}")

    data = resp.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected Gemini response: %s", data)
        raise RuntimeError("No transcription in Gemini response") from exc

    text = text.strip()
    if not text:
        raise RuntimeError("Empty transcription returned")

    return TranscriptionResult(text=text, model=primary)
