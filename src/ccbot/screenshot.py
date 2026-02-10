"""Terminal text → PNG screenshot renderer.

Converts captured tmux pane text (with optional ANSI color codes) into a
dark-background PNG image. Supports full ANSI color parsing (16/256/RGB)
and a three-tier font fallback chain:
  1. JetBrains Mono — Latin, symbols, box-drawing
  2. Noto Sans Mono CJK SC — CJK characters
  3. Symbola — remaining special symbols

Key function: text_to_image(text, font_size, with_ansi) → PNG bytes.
"""

import asyncio
import io
import logging
import re
from dataclasses import dataclass
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

# ANSI color mapping (basic 16 colors)
_ANSI_COLORS: dict[int, tuple[int, int, int]] = {
    # Standard colors (30-37, 40-47)
    0: (0, 0, 0),  # Black
    1: (205, 49, 49),  # Red
    2: (13, 188, 121),  # Green
    3: (229, 229, 16),  # Yellow
    4: (36, 114, 200),  # Blue
    5: (188, 63, 188),  # Magenta
    6: (17, 168, 205),  # Cyan
    7: (229, 229, 229),  # White
    # Bright colors (90-97, 100-107)
    8: (102, 102, 102),  # Bright Black
    9: (241, 76, 76),  # Bright Red
    10: (35, 209, 139),  # Bright Green
    11: (245, 245, 67),  # Bright Yellow
    12: (59, 142, 234),  # Bright Blue
    13: (214, 112, 214),  # Bright Magenta
    14: (41, 184, 219),  # Bright Cyan
    15: (255, 255, 255),  # Bright White
}

# Default colors for terminals
_DEFAULT_FG = (212, 212, 212)  # Light gray
_DEFAULT_BG = (30, 30, 30)  # Dark gray


@dataclass
class TextStyle:
    """Text styling information from ANSI codes."""

    fg_color: tuple[int, int, int] = _DEFAULT_FG
    bg_color: tuple[int, int, int] | None = None


@dataclass
class StyledSegment:
    """A text segment with its styling."""

    text: str
    style: TextStyle
    font_tier: int


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
    if (
        cp in _NOTO_CODEPOINTS
        or cp >= 0x2E80
        and (
            cp <= 0x9FFF  # CJK radicals, kangxi, ideographs
            or 0xF900 <= cp <= 0xFAFF  # CJK compat ideographs
            or 0xFE30 <= cp <= 0xFE4F  # CJK compat forms
            or 0xFF00 <= cp <= 0xFFEF  # fullwidth forms
            or 0x20000 <= cp <= 0x2FA1F  # CJK extension B+
        )
    ):
        return 1
    return 0


def _parse_ansi_line(line: str) -> list[StyledSegment]:
    """Parse a line with ANSI escape codes into styled segments."""
    # ANSI escape sequence pattern
    ansi_pattern = re.compile(r"\x1b\[([0-9;]*)m")

    segments: list[StyledSegment] = []
    current_style = TextStyle()
    pos = 0

    for match in ansi_pattern.finditer(line):
        # Add text before this escape code
        text_before = line[pos : match.start()]
        if text_before:
            # Split by font tier
            for seg_text, tier in _split_line_segments_plain(text_before):
                if seg_text:
                    segments.append(StyledSegment(seg_text, current_style, tier))

        # Parse escape code
        codes = match.group(1)
        if codes:
            current_style = _apply_ansi_codes(current_style, codes)
        else:
            # Empty code means reset
            current_style = TextStyle()

        pos = match.end()

    # Add remaining text after last escape code
    text_after = line[pos:]
    if text_after:
        for seg_text, tier in _split_line_segments_plain(text_after):
            if seg_text:
                segments.append(StyledSegment(seg_text, current_style, tier))

    return segments if segments else [StyledSegment("", TextStyle(), 0)]


def _apply_ansi_codes(style: TextStyle, codes: str) -> TextStyle:
    """Apply ANSI color codes to a text style."""
    # Create a new style (copy current)
    new_style = TextStyle(
        fg_color=style.fg_color,
        bg_color=style.bg_color,
    )

    parts = [int(c) for c in codes.split(";") if c]
    i = 0
    while i < len(parts):
        code = parts[i]

        if code == 0:  # Reset
            new_style = TextStyle()
        elif 30 <= code <= 37:  # Foreground color
            new_style.fg_color = _ANSI_COLORS[code - 30]
        elif code == 38:  # Extended foreground color
            if i + 1 < len(parts) and parts[i + 1] == 5:  # 256 color
                if i + 2 < len(parts):
                    color_idx = parts[i + 2] % 256
                    if color_idx < 16:
                        new_style.fg_color = _ANSI_COLORS[color_idx]
                    else:
                        # Approximate 256 colors (simplified)
                        new_style.fg_color = _approximate_256_color(color_idx)
                    i += 2
            elif i + 1 < len(parts) and parts[i + 1] == 2:  # RGB color
                if i + 4 < len(parts):
                    new_style.fg_color = (parts[i + 2], parts[i + 3], parts[i + 4])
                    i += 4
        elif code == 39:  # Default foreground
            new_style.fg_color = _DEFAULT_FG
        elif 40 <= code <= 47:  # Background color
            new_style.bg_color = _ANSI_COLORS[code - 40]
        elif code == 48:  # Extended background color
            if i + 1 < len(parts) and parts[i + 1] == 5:  # 256 color
                if i + 2 < len(parts):
                    color_idx = parts[i + 2] % 256
                    if color_idx < 16:
                        new_style.bg_color = _ANSI_COLORS[color_idx]
                    else:
                        new_style.bg_color = _approximate_256_color(color_idx)
                    i += 2
            elif i + 1 < len(parts) and parts[i + 1] == 2:  # RGB color
                if i + 4 < len(parts):
                    new_style.bg_color = (parts[i + 2], parts[i + 3], parts[i + 4])
                    i += 4
        elif code == 49:  # Default background
            new_style.bg_color = None
        elif 90 <= code <= 97:  # Bright foreground color
            new_style.fg_color = _ANSI_COLORS[code - 90 + 8]
        elif 100 <= code <= 107:  # Bright background color
            new_style.bg_color = _ANSI_COLORS[code - 100 + 8]

        i += 1

    return new_style


def _approximate_256_color(idx: int) -> tuple[int, int, int]:
    """Approximate a 256-color palette index to RGB."""
    if idx < 16:
        return _ANSI_COLORS[idx]
    elif idx < 232:
        # 216 color cube: 16 + 36*r + 6*g + b
        idx -= 16
        r = (idx // 36) * 51
        g = ((idx % 36) // 6) * 51
        b = (idx % 6) * 51
        return (r, g, b)
    else:
        # Grayscale: 232-255
        gray = 8 + (idx - 232) * 10
        return (gray, gray, gray)


def _split_line_segments_plain(line: str) -> list[tuple[str, int]]:
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


async def text_to_image(
    text: str, font_size: int = 28, with_ansi: bool = True
) -> bytes:
    """Render monospace text onto a dark-background image and return PNG bytes.

    Args:
        text: The text to render (may contain ANSI color codes)
        font_size: Font size in pixels
        with_ansi: If True, parse and render ANSI color codes

    Returns:
        PNG image bytes
    """

    def _render_image() -> bytes:
        fonts = [_load_font(p, font_size) for p in _FONT_PATHS]

        lines = text.split("\n")
        padding = 16

        # Parse lines into styled segments
        if with_ansi:
            line_segments = [_parse_ansi_line(line) for line in lines]
        else:
            # Legacy plain text mode
            line_segments_plain = [_split_line_segments_plain(line) for line in lines]
            line_segments = [
                [
                    StyledSegment(seg_text, TextStyle(), tier)
                    for seg_text, tier in segments
                ]
                for segments in line_segments_plain
            ]

        # Measure text size
        dummy = Image.new("RGB", (1, 1))
        draw = ImageDraw.Draw(dummy)
        line_height = int(font_size * 1.4)
        max_width = 0
        for segments in line_segments:
            w = 0
            for seg in segments:
                bbox = draw.textbbox((0, 0), seg.text, font=fonts[seg.font_tier])
                w += bbox[2] - bbox[0]
            max_width = max(max_width, w)

        img_width = int(max_width) + padding * 2
        img_height = line_height * len(lines) + padding * 2

        img = Image.new("RGB", (img_width, img_height), _DEFAULT_BG)
        draw = ImageDraw.Draw(img)

        y = padding
        for segments in line_segments:
            x = padding
            for seg in segments:
                f = fonts[seg.font_tier]

                # Draw background if specified
                if seg.style.bg_color:
                    bbox = draw.textbbox((x, y), seg.text, font=f)
                    draw.rectangle(
                        [bbox[0], y, bbox[2], y + line_height], fill=seg.style.bg_color
                    )

                # Draw text with foreground color
                draw.text((x, y), seg.text, fill=seg.style.fg_color, font=f)

                bbox = draw.textbbox((0, 0), seg.text, font=f)
                x += bbox[2] - bbox[0]
            y += line_height

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    # Run CPU-intensive image rendering in thread pool
    return await asyncio.to_thread(_render_image)
