"""Voice-to-text transcription via Deepgram's Nova-3 API.

Provides a single async function to transcribe voice messages using
Deepgram's pre-recorded audio endpoint. Uses httpx directly.

Key function: transcribe_voice(ogg_data) -> str
"""

import logging

import httpx

from .config import config

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None

DEEPGRAM_API_URL = "https://api.deepgram.com/v1/listen"


def _get_client() -> httpx.AsyncClient:
    """Return a lazily-initialized httpx client singleton."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


async def transcribe_voice(ogg_data: bytes) -> str:
    """Transcribe OGG voice data to text via Deepgram API.

    Raises:
        httpx.HTTPStatusError: On API errors (401, 429, 5xx, etc.)
        ValueError: If the API returns an empty transcription.
    """
    client = _get_client()
    response = await client.post(
        DEEPGRAM_API_URL,
        headers={
            "Authorization": f"Token {config.deepgram_api_key}",
            "Content-Type": "audio/ogg",
        },
        params={
            "model": "nova-3",
            "language": "ru",
            "smart_format": "true",
        },
        content=ogg_data,
    )
    response.raise_for_status()

    data = response.json()
    try:
        text = data["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
    except (KeyError, IndexError):
        raise ValueError(f"Unexpected Deepgram response: keys={list(data.keys())}")
    if not text:
        raise ValueError("Empty transcription returned by Deepgram")
    return text


async def close_client() -> None:
    """Close the httpx client (call on shutdown)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None
