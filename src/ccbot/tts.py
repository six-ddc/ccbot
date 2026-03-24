"""Text-to-speech via Microsoft Edge neural voices.

Provides async TTS using edge-tts to generate OGG/Opus audio for Telegram
voice messages. Voice selection and TTS toggle are per-user.

Key functions:
  - synthesize: Generate OGG audio bytes from text
  - send_voice_message: Send voice message to Telegram chat
  - is_tts_enabled: Check if TTS is active for a user
  - toggle_tts: Toggle TTS on/off for a user

Dependencies: edge-tts (Microsoft Edge TTS, free, no API key)
"""

import logging
import re
from pathlib import Path

from .config import config

logger = logging.getLogger(__name__)

# Regex patterns to strip non-speech content before TTS
_TTS_CLEANUP = [
    # Emojis (common ranges)
    re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map
        "\U0001F700-\U0001F77F"  # alchemical symbols
        "\U0001F780-\U0001F7FF"  # geometric shapes extended
        "\U0001F800-\U0001F8FF"  # supplemental arrows-C
        "\U0001F900-\U0001F9FF"  # supplemental symbols & pictographs
        "\U0001FA00-\U0001FA6F"  # chess symbols
        "\U0001FA70-\U0001FAFF"  # symbols & pictographs extended-A
        "\U00002702-\U000027B0"  # dingbats
        "\U000024C2-\U0001F251"  # enclosed characters
        "\U0001F200-\U0001F2FF"  # enclosed ideographic supplement
        "\U00002600-\U000026FF"  # misc symbols
        "\U00002700-\U000027BF"  # dingbats (overlap, intentional)
        "\U0000FE00-\U0000FE0F"  # variation selectors
        "\U0000200D"             # zero-width joiner
        "]+",
        flags=re.UNICODE,
    ),
    # Code fences (must run BEFORE markdown cleanup strips the backticks)
    re.compile(r"```[\s\S]*?```"),
    # Telegram-style expandable quotes and blockquotes
    re.compile(r"[▁-▉]+"),
    # Markdown/code artifacts (inline only — fences handled above)
    re.compile(r"[*_`~#|>]+"),
    # Arrow-like symbols
    re.compile(r"[→←↑↓↔↕➜➤➡⇒⇐⇑⇓⇔⇕]+"),
    # Decorative box-drawing and block elements
    re.compile(r"[═║╔╗╚╝╠╣╦╩─│┌┐└┘├┤┬┴┼]+"),
    # Bullet points and list markers
    re.compile(r"[•●○◦▪▫➢➣➤◆◇★☆►◄▲▼]+"),
    # Misc symbols that TTS reads badly
    re.compile(r"[⚡🔥💡✅❌⚠️🔊🗣💡]+"),
    # Multiple consecutive whitespace
    re.compile(r"\n{3,}", re.MULTILINE),
]


def clean_text_for_tts(text: str) -> str:
    """Strip emojis, symbols, and markdown artifacts for TTS synthesis.

    Keeps normal punctuation (.,;:!?¿¡), letters, numbers, and whitespace.
    Collapses multiple newlines to double newlines for natural pauses.
    """
    for pattern in _TTS_CLEANUP:
        text = pattern.sub("", text)
    text = text.strip()
    return text if text else ""

_per_user_tts: dict[int, bool] = {}
_per_user_voice: dict[int, str] = {}

_audio_dir: Path | None = None


def _get_audio_dir() -> Path:
    global _audio_dir
    if _audio_dir is not None:
        return _audio_dir
    from .utils import ccbot_dir

    d = ccbot_dir() / "audio"
    d.mkdir(parents=True, exist_ok=True)
    _audio_dir = d
    return _audio_dir


def is_tts_enabled(user_id: int) -> bool:
    """Check if TTS is enabled for a user (global + per-user)."""
    if not config.tts_enabled:
        return False
    return _per_user_tts.get(user_id, config.tts_auto) is not False


def toggle_tts(user_id: int) -> bool:
    """Toggle TTS for a user. Returns new state."""
    _per_user_tts[user_id] = not is_tts_enabled(user_id)
    return _per_user_tts[user_id]


def get_voice(user_id: int) -> str:
    """Get the TTS voice for a user (per-user override or global default)."""
    return _per_user_voice.get(user_id, config.tts_voice)


def set_voice(user_id: int, voice: str) -> str:
    """Set a per-user voice override. Returns the voice name set."""
    # Basic sanity: voice names are like "es-ES-ElviraNeural", not commands
    if "/" in voice or voice.startswith(("list", "all")):
        raise ValueError(f"'{voice}' doesn't look like a voice name.")
    _per_user_voice[user_id] = voice
    return voice


async def synthesize(text: str, user_id: int | None = None, voice: str | None = None) -> bytes:
    """Synthesize text to OGG/Opus audio bytes using edge-tts.

    Args:
        text: Text to synthesize (max ~4000 chars for Telegram voice).
        user_id: User ID for per-user voice selection.
        voice: Voice name override (takes priority over user_id).

    Returns:
        OGG/Opus audio bytes ready for Telegram send_voice.
    """
    if not voice and user_id:
        voice = get_voice(user_id)
    voice = voice or config.tts_voice

    import edge_tts

    audio_dir = _get_audio_dir()
    tmp_path = audio_dir / f"tts_{id(text) % 10**8}.ogg"

    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(tmp_path))
        data = tmp_path.read_bytes()
        if not data:
            raise ValueError("TTS produced empty audio")
        return data
    finally:
        tmp_path.unlink(missing_ok=True)


async def send_voice_message(
    bot, chat_id: int, text: str, thread_id: int | None = None, user_id: int | None = None
) -> None:
    """Send text as a Telegram voice message.

    Generates audio via edge-tts and sends as a voice note.
    Falls back silently to text-only if TTS fails.
    """
    from telegram.constants import ChatAction

    truncated = clean_text_for_tts(text[:4000])
    if not truncated:
        return
    # Prefix with brief pause to prevent first-word truncation in OGG/Opus encoding
    truncated = "... " + truncated
    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
        audio_data = await synthesize(truncated, user_id=user_id)
        kwargs = {"chat_id": chat_id, "voice": audio_data}
        if thread_id is not None:
            kwargs["message_thread_id"] = thread_id
        await bot.send_voice(**kwargs)
    except Exception:
        logger.warning("TTS failed, skipping voice message", exc_info=True)


async def close() -> None:
    """Cleanup TTS resources."""
    global _audio_dir
    _per_user_tts.clear()
    _audio_dir = None
