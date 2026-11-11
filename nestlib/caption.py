"""One-line description of a nested sheet, shared by the SVG and PNG previews."""
from __future__ import annotations

from typing import Mapping

from .config import Config
from .estimate import sheet_cut
from .models import Layout, Part


def sheet_caption(layout: Layout, parts: Mapping[str, Part], cfg: Config) -> str:
    u = cfg.units
    sheet = layout.sheet
    used = sum(parts[p.part_name].area for p in layout.placements)
    usable = sheet.usable_width * sheet.usable_height
    cut = sheet_cut(layout, parts)
    axis = "X (sheet width)" if cfg.grain == "x" else "Y (sheet height)"
    return (f"Sheet {layout.index}: {u.size(sheet.width, sheet.height, u.label_decimals)}, "
            f"{len(layout.placements)} piece(s), {100 * used / usable:.1f}% material, "
            f"cut {u.long_len(cut.length)} ({cut.pierces} pierces), "
            f"margin {u.len(sheet.margin, u.label_decimals)}, spacing {u.len(cfg.fit_spacing, u.label_decimals)}, "
            f"grain along {axis}")
