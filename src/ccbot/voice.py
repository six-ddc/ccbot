"""Voice message transcription via Google Gemini API.

Downloads voice/audio files from Telegram and transcribes them using
Gemini's multimodal capabilities (inline base64 audio). Uses httpx
for the REST API call — no additional dependencies needed.

Key function: transcribe_voice(audio_bytes, mime_type) -> str
"""

import base64
import logging

import httpx

from .config import config

logger = logging.getLogger(__name__)

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

_TRANSCRIBE_PROMPT = (
    "Transcribe this audio exactly as spoken. "
    "Return only the transcription text, no explanations or formatting."
)


async def transcribe_voice(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """Transcribe audio bytes using Google Gemini API.

    Args:
        audio_bytes: Raw audio file content.
        mime_type: MIME type of the audio (default: audio/ogg for Telegram voice).

    Returns:
        Transcription text.

    Raises:
        ValueError: If GEMINI_API_KEY is not configured.
        RuntimeError: If the API call fails or returns no transcription.
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

    url = f"{_GEMINI_BASE}/{config.gemini_model}:generateContent"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            params={"key": config.gemini_api_key},
            json=payload,
        )

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

    return text
