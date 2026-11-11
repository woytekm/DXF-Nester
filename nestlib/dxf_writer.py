"""Module 6: DXF writer, one file per sheet.

    write_sheet_dxf(layout, parts, config, path) -> Path
    write_sheet_dxfs(layouts, parts, config, directory) -> list[Path]

Layers (names from config.layers):
    SHEET       the sheet outline (a closed rectangle, 0,0 .. width,height). Not a cut.
    CUT_OUTER   one closed LWPOLYLINE per piece (arcs as bulges), CIRCLE for round parts
    CUT_INNER   one closed LWPOLYLINE / CIRCLE per hole
    LABELS      TEXT "name #n", placed on solid material near the middle of each piece. Not a cut.

Label text: DXF before R2007 has no real Unicode, and importers disagree on how to
show the \\U+XXXX escapes, so for those versions labels are folded to plain ASCII
("Pó\u0142ka" -> "Polka"). R2007 and later keep the original characters.

Each contour is written exactly once, so a shop measuring cut length per layer
gets the true figure. Coordinates and $INSUNITS follow config.units (mm or
inches); the origin is the sheet's lower-left corner.
"""
from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Mapping, Sequence

import ezdxf
from ezdxf.enums import TextEntityAlignment
from shapely.geometry import Polygon, box
from shapely.ops import polylabel
from shapely.prepared import prep

from .config import Config
from .layout import place_loops, place_polygon
from .models import CircleLoop, Layout, Loop, Part, Placement

# AutoCAD colour indices: grey sheet, red outer cuts, blue inner cuts, green labels
_COLOURS = {"sheet": 8, "cut_outer": 1, "cut_inner": 5, "labels": 3}
_MAX_LABEL_HEIGHT = 25.0   # mm
_MIN_LABEL_HEIGHT = 2.5    # mm: below this the label is shortened to "#n"
_CHAR_WIDTH = 0.62         # text width per character, in text heights


_FOLD = {"\u0142": "l", "\u0141": "L", "\u00f8": "o", "\u00d8": "O", "\u0111": "d", "\u0110": "D",
         "\u00df": "ss", "\u00e6": "ae", "\u00c6": "AE", "\u0153": "oe", "\u0152": "OE"}


def ascii_fold(text: str) -> str:
    """Strip diacritics; characters with no ASCII equivalent become '_'."""
    out = []
    for ch in text:
        if ch in _FOLD:
            out.append(_FOLD[ch])
            continue
        base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii")
        out.append(base if base else ("_" if ord(ch) > 127 else ch))
    return "".join(out)


def _is_unicode_version(version: str) -> bool:
    return version >= "R2007"          # "R2000" < "R2004" < "R2007" < ... compares correctly as text


def label_for(placement: Placement, width: float, height: float) -> tuple[str, float, float]:
    """(text, height_mm, rotation_deg) for a piece's label, in mm. The text runs along
    the piece's longer side and shrinks to fit; if it would become too small it is
    shortened from 'name #n' to '#n'."""
    along, across = max(width, height), min(width, height)
    rotation = 90.0 if height > width else 0.0
    for text in (f"{placement.part_name} #{placement.instance}", f"#{placement.instance}"):
        h = min(_MAX_LABEL_HEIGHT, 0.12 * across)
        need = len(text) * _CHAR_WIDTH * h
        if need > 0.9 * along:
            h *= 0.9 * along / need
        if h >= _MIN_LABEL_HEIGHT:
            return text, h, rotation
    return f"#{placement.instance}", max(min(h, 0.3 * across), 0.5), rotation


def label_anchor(poly: Polygon, text_w: float, text_h: float) -> tuple[float, float]:
    """Centre for a label box of text_w x text_h (mm, as it appears on the sheet) that lies
    completely inside the piece and as close to the middle of its bounding box as possible.
    Falls back to the visual centre (pole of inaccessibility) if the text cannot fit anywhere."""
    x0, y0, x1, y1 = poly.bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hw, hh = text_w / 2, text_h / 2
    p = prep(poly)
    step_x = max(text_h * 0.5, (x1 - x0) / 80)
    step_y = max(text_h * 0.5, (y1 - y0) / 80)
    best = None
    nx, ny = int((x1 - x0 - text_w) / step_x) + 1, int((y1 - y0 - text_h) / step_y) + 1
    for i in range(max(nx, 0) + 1):
        x = x0 + hw + min(i * step_x, max(x1 - x0 - text_w, 0))
        for j in range(max(ny, 0) + 1):
            y = y0 + hh + min(j * step_y, max(y1 - y0 - text_h, 0))
            d = (x - cx) ** 2 + (y - cy) ** 2
            if (best is None or d < best[0]) and p.contains(box(x - hw, y - hh, x + hw, y + hh)):
                best = (d, x, y)
    if best is not None:
        return best[1], best[2]
    c = polylabel(poly, tolerance=0.1)
    return c.x, c.y


def label_box(text: str, height: float, rotation: float) -> tuple[float, float]:
    """Approximate (width, height) in mm of a label on the sheet, with a little padding."""
    w, h = len(text) * _CHAR_WIDTH * height * 1.06, height * 1.3
    return (h, w) if rotation == 90.0 else (w, h)


def _add_loop(msp, loop: Loop, layer: str, k: float) -> None:
    attribs = {"layer": layer}
    if isinstance(loop, CircleLoop):
        msp.add_circle((loop.center[0] * k, loop.center[1] * k), loop.radius * k, dxfattribs=attribs)
        return
    pts = [(e.start[0] * k, e.start[1] * k, e.bulge) for e in loop.edges]
    msp.add_lwpolyline(pts, format="xyb", close=True, dxfattribs=attribs)


def write_sheet_dxf(layout: Layout, parts: Mapping[str, Part], cfg: Config, path: Path | str) -> Path:
    path = Path(path)
    k = 1.0 / cfg.units.mm_per_unit               # internal mm -> output units
    names = cfg.layers
    doc = ezdxf.new(cfg.dxf_version, setup=True)
    doc.header["$INSUNITS"] = cfg.units.insunits
    doc.header["$MEASUREMENT"] = 1 if cfg.units.name == "mm" else 0
    for key, layer in (("sheet", names.sheet), ("cut_outer", names.cut_outer),
                       ("cut_inner", names.cut_inner), ("labels", names.labels)):
        doc.layers.add(layer, color=_COLOURS[key])
    msp = doc.modelspace()

    W, H = layout.sheet.width * k, layout.sheet.height * k
    msp.add_lwpolyline([(0, 0), (W, 0), (W, H), (0, H)], close=True, dxfattribs={"layer": names.sheet})

    for pl in layout.placements:
        part = parts[pl.part_name]
        outer, holes = place_loops(part, pl)
        _add_loop(msp, outer, names.cut_outer, k)
        for h in holes:
            _add_loop(msp, h, names.cut_inner, k)

        text, height, rotation = label_for(pl, pl.width, pl.height)
        if not _is_unicode_version(cfg.dxf_version):
            text = ascii_fold(text)
        cx, cy = label_anchor(place_polygon(part, pl), *label_box(text, height, rotation))
        t = msp.add_text(text, height=height * k, rotation=rotation, dxfattribs={"layer": names.labels})
        t.set_placement((cx * k, cy * k), align=TextEntityAlignment.MIDDLE_CENTER)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


def write_sheet_dxfs(layouts: Sequence[Layout], parts: Mapping[str, Part], cfg: Config,
                     directory: Path | str) -> list[Path]:
    directory = Path(directory)
    return [write_sheet_dxf(l, parts, cfg, directory / f"sheet_{l.index:02d}.dxf") for l in layouts]
