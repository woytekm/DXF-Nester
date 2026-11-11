"""PNG preview of nested sheets, rendered with Pillow (no Cairo or other system library).

    render_layout_image(layout, parts, config)       -> PIL.Image   (in memory)
    render_layout_png(layout, parts, config, path)  -> Path
    write_layout_pngs(...)                          -> list[Path]
    png_geometry(sheet, width_px)                    -> PngGeometry   (pixel <-> mm mapping)

Draws the exact outlines (arcs flattened finely), holes as real holes, the margin
as a dashed red line, and the same labels the DXF carries (dxf_writer.label_for).
Anti-aliased by drawing at 2x and downscaling.

Fonts: the first usable TrueType font from a list of common system locations
(override with the DXFNEST_FONT environment variable). If none is found Pillow's
built-in font is used, which lacks accented letters, so labels are then folded to
ASCII exactly as the old DXF versions do.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from .caption import sheet_caption
from .config import Config
from .fonts import candidates as _font_candidates
from .dxf_writer import ascii_fold, label_anchor, label_box, label_for
from .geometry import loop_points
from .layout import place_loops, place_polygon
from .models import Layout, Part, SheetSpec
from .preview import PALETTE

_SS = 2                                   # supersampling factor
BACKGROUND = (255, 255, 255)
SHEET_COLOUR = (246, 246, 246)
LINE_COLOUR = (34, 34, 34)
MARGIN_COLOUR = (204, 51, 51)


@dataclass(frozen=True)
class PngGeometry:
    """Mapping between sheet millimetres and image pixels (final, not supersampled)."""

    scale: float          # pixels per mm
    pad: int              # blank border in px
    caption: int          # px reserved under the sheet for the caption
    width: int
    height: int

    def px(self, x_mm: float, y_mm: float, sheet_h: float) -> tuple[float, float]:
        """Sheet coordinates (origin lower-left) -> pixel position (origin top-left)."""
        return self.pad + x_mm * self.scale, self.pad + (sheet_h - y_mm) * self.scale


def png_geometry(sheet: SheetSpec, width_px: int = 2400) -> PngGeometry:
    pad = max(8, round(width_px * 0.015))
    scale = (width_px - 2 * pad) / sheet.width
    caption = max(36, round(width_px * 0.03))
    height = round(2 * pad + sheet.height * scale + caption)
    return PngGeometry(scale, pad, caption, width_px, height)


@lru_cache(maxsize=64)
def _font(size: int, path: str | None) -> tuple[ImageFont.FreeTypeFont, bool]:
    """(font, supports_accents) for a pixel size."""
    size = max(size, 6)
    for name in ([path] if path else _font_candidates()):
        try:
            return ImageFont.truetype(name, size), True
        except OSError:
            continue
    return ImageFont.load_default(size=size), False


def _dashed(draw: ImageDraw.ImageDraw, p0, p1, dash: float, gap: float, fill, width: int) -> None:
    length = math.dist(p0, p1)
    if length == 0:
        return
    ux, uy = (p1[0] - p0[0]) / length, (p1[1] - p0[1]) / length
    t = 0.0
    while t < length:
        e = min(t + dash, length)
        draw.line([(p0[0] + ux * t, p0[1] + uy * t), (p0[0] + ux * e, p0[1] + uy * e)], fill=fill, width=width)
        t += dash + gap


def _text_layer(text: str, font, rotation: float) -> Image.Image:
    box = font.getbbox(text)
    w, h = box[2] - box[0] + 4, box[3] - box[1] + 4
    layer = Image.new("L", (w, h), 0)
    ImageDraw.Draw(layer).text((2 - box[0], 2 - box[1]), text, font=font, fill=255)
    return layer.rotate(rotation, expand=True) if rotation else layer


def render_layout_image(
    layout: Layout,
    parts: Mapping[str, Part],
    cfg: Config,
    *,
    width_px: int = 2400,
    colour_order: Sequence[str] | None = None,
    outline: bool = True,
    labels: bool = True,
    caption: bool = True,
    font_path: str | None = None,
) -> Image.Image:
    """The sheet as a Pillow RGB image (no file involved)."""
    sheet = layout.sheet
    g = png_geometry(sheet, width_px)
    S = _SS
    img = Image.new("RGB", (g.width * S, g.height * S), BACKGROUND)
    d = ImageDraw.Draw(img)
    order = list(colour_order) if colour_order is not None else sorted(parts)
    colour = {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(order)}
    rgb = lambda hexcol: tuple(int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
    flat_tol = 0.35 / g.scale                                   # flatten arcs to ~1/3 of a pixel

    def px(x, y):
        a, b = g.px(x, y, sheet.height)
        return a * S, b * S

    x0, y0 = px(0, sheet.height)
    x1, y1 = px(sheet.width, 0)
    d.rectangle([x0, y0, x1, y1], fill=SHEET_COLOUR, outline=LINE_COLOUR, width=S * 2)
    if sheet.margin > 0:
        m = sheet.margin
        a, b, c, e = px(m, sheet.height - m)[0], px(m, sheet.height - m)[1], px(sheet.width - m, m)[0], px(sheet.width - m, m)[1]
        for p, q in (((a, b), (c, b)), ((c, b), (c, e)), ((c, e), (a, e)), ((a, e), (a, b))):
            _dashed(d, p, q, 7 * S, 5 * S, MARGIN_COLOUR, S)

    for pl in layout.placements:
        part = parts[pl.part_name]
        outer, holes = place_loops(part, pl)
        pts = [[px(x, y) for x, y in loop_points(l, flat_tol)] for l in (outer, *holes)]
        d.polygon(pts[0], fill=rgb(colour[pl.part_name]))
        for hole in pts[1:]:
            d.polygon(hole, fill=SHEET_COLOUR)
        if outline:
            for ring in pts:
                d.line(ring + [ring[0]], fill=LINE_COLOUR, width=S * 2, joint="curve")

    if labels:
        for pl in layout.placements:
            part = parts[pl.part_name]
            text, h_mm, rot = label_for(pl, pl.width, pl.height)
            font, unicode_ok = _font(round(h_mm * g.scale * S / 0.72), font_path)
            if not unicode_ok:
                text = ascii_fold(text)
            ax, ay = label_anchor(place_polygon(part, pl), *label_box(text, h_mm, rot))
            layer = _text_layer(text, font, rot)
            cx, cy = px(ax, ay)
            img.paste(Image.new("RGB", layer.size, LINE_COLOUR), (round(cx - layer.width / 2), round(cy - layer.height / 2)), layer)

    if caption:
        text = sheet_caption(layout, parts, cfg)
        font, unicode_ok = _font(round(g.caption * 0.42 * S), font_path)
        d.text((g.pad * S, (g.pad + sheet.height * g.scale + g.caption * 0.25) * S),
               text if unicode_ok else ascii_fold(text), font=font, fill=LINE_COLOUR)

    return img.resize((g.width, g.height), Image.LANCZOS)


def render_layout_png(layout: Layout, parts: Mapping[str, Part], cfg: Config, path: Path | str, **kw) -> Path:
    """render_layout_image(), saved as a PNG file."""
    path = Path(path)
    image = render_layout_image(layout, parts, cfg, **kw)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, optimize=True)
    return path


def write_layout_pngs(layouts: Sequence[Layout], parts: Mapping[str, Part], cfg: Config,
                      directory: Path | str, **kw) -> list[Path]:
    directory = Path(directory)
    order = sorted(parts)
    return [render_layout_png(l, parts, cfg, directory / f"sheet_{l.index:02d}.png", colour_order=order, **kw)
            for l in layouts]
