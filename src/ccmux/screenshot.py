"""Convert terminal text to a PNG image."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_FONTS_DIR = Path(__file__).parent / "fonts"

# Font fallback chain (highest priority first):
#   1. JetBrains Mono (OFL-1.1) — Latin, symbols, box-drawing, blocks
#   2. Noto Sans Mono CJK SC (OFL-1.1) — CJK, additional symbols
#   3. Symbola (free license) — remaining miscellaneous symbols, dingbats
_FONT_PATHS: list[Path] = [
    _FONTS_DIR / "JetBrainsMono-Regular.ttf",
    _FONTS_DIR / "NotoSansMonoCJKsc-Regular.otf",
    _FONTS_DIR / "Symbola.ttf",
]

# Pre-computed codepoint sets for characters NOT in JetBrains Mono.
# Tier 2: present in Noto Sans Mono CJK SC (CJK ideographs, fullwidth punctuation, etc.)
_NOTO_CODEPOINTS: set[int] = {
    0x23BF,  # ⎿ DENTISTRY SYMBOL LIGHT VERTICAL AND BOTTOM RIGHT
}
# Tier 3: only in Symbola (misc symbols not in either JB or Noto)
_SYMBOLA_CODEPOINTS: set[int] = {
    0x23F5,  # ⏵ BLACK MEDIUM RIGHT-POINTING TRIANGLE
    0x2714,  # ✔ HEAVY CHECK MARK
    0x274C,  # ❌ CROSS MARK
}


def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a TrueType/OpenType font, falling back to Pillow default."""
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        logger.warning("Failed to load font %s, using Pillow default", path)
        return ImageFont.load_default()


def _font_tier(ch: str) -> int:
    """Return 0 (JetBrains), 1 (Noto CJK), or 2 (Symbola) for a character."""
    cp = ord(ch)
    if cp in _SYMBOLA_CODEPOINTS:
        return 2
    # CJK Unified Ideographs + CJK compat + fullwidth forms + known Noto-only codepoints
    if cp in _NOTO_CODEPOINTS or cp >= 0x2E80 and (
        cp <= 0x9FFF  # CJK radicals, kangxi, ideographs
        or 0xF900 <= cp <= 0xFAFF  # CJK compat ideographs
        or 0xFE30 <= cp <= 0xFE4F  # CJK compat forms
        or 0xFF00 <= cp <= 0xFFEF  # fullwidth forms
        or 0x20000 <= cp <= 0x2FA1F  # CJK extension B+
    ):
        return 1
    return 0


def _split_line_segments(line: str) -> list[tuple[str, int]]:
    """Split a line into (text, font_tier) segments.

    Consecutive characters sharing the same tier are merged.
    """
    if not line:
        return [("", 0)]
    segments: list[tuple[str, int]] = []
    cur_tier = _font_tier(line[0])
    start = 0
    for i in range(1, len(line)):
        tier = _font_tier(line[i])
        if tier != cur_tier:
            segments.append((line[start:i], cur_tier))
            cur_tier = tier
            start = i
    segments.append((line[start:], cur_tier))
    return segments


def text_to_image(text: str, font_size: int = 28) -> bytes:
    """Render monospace text onto a dark-background image and return PNG bytes."""
    fonts = [_load_font(p, font_size) for p in _FONT_PATHS]

    lines = text.split("\n")
    padding = 16

    # Pre-split lines into segments
    line_segments = [_split_line_segments(line) for line in lines]

    # Measure text size
    dummy = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(dummy)
    line_height = int(font_size * 1.4)
    max_width = 0
    for segments in line_segments:
        w = 0
        for seg_text, tier in segments:
            bbox = draw.textbbox((0, 0), seg_text, font=fonts[tier])
            w += bbox[2] - bbox[0]
        max_width = max(max_width, w)

    img_width = int(max_width) + padding * 2
    img_height = line_height * len(lines) + padding * 2

    bg_color = (30, 30, 30)
    fg_color = (212, 212, 212)

    img = Image.new("RGB", (img_width, img_height), bg_color)
    draw = ImageDraw.Draw(img)

    y = padding
    for segments in line_segments:
        x = padding
        for seg_text, tier in segments:
            f = fonts[tier]
            draw.text((x, y), seg_text, fill=fg_color, font=f)
            bbox = draw.textbbox((0, 0), seg_text, font=f)
            x += bbox[2] - bbox[0]
        y += line_height

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
