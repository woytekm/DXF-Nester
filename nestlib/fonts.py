"""Finding a TrueType font that has Central European letters (Półka, Przegroda...).

Used by the PNG renderer (through Pillow) and the GUI (which needs a file path).
Override with the DXFNEST_FONT environment variable.
"""
from __future__ import annotations

import os

FONT_CANDIDATES = (
    # macOS
    "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
    # Linux
    "DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    # Windows
    "arial.ttf", "C:/Windows/Fonts/arial.ttf",
)


def candidates(first: str | None = None) -> list[str]:
    out = [first] if first else []
    if os.environ.get("DXFNEST_FONT"):
        out.append(os.environ["DXFNEST_FONT"])
    return out + list(FONT_CANDIDATES)


def find_font_path(first: str | None = None) -> str | None:
    """Path of the first candidate Pillow can open, or None."""
    from PIL import ImageFont
    for name in candidates(first):
        try:
            return ImageFont.truetype(name, 12).path
        except OSError:
            continue
    return None


MONO_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "LiberationMono-Regular.ttf",
    "consola.ttf", "C:/Windows/Fonts/consola.ttf",
)


def find_mono_font_path() -> str | None:
    """Fixed-width font for the summary (column alignment), or None."""
    from PIL import ImageFont
    for name in MONO_CANDIDATES:
        try:
            return ImageFont.truetype(name, 12).path
        except OSError:
            continue
    return None
