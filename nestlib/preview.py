"""Module 8: SVG preview.

    part_svg(part)                      -> str   standalone SVG of one Part
    write_part_svg(part, path)
    layout_svg(layout, parts, config)   -> str   one nested sheet with all placed pieces
    write_layout_svgs(layouts, parts, config, directory) -> list[Path]

Draws exact geometry (straight lines and true SVG arcs, not flattened
polylines), so what you see is what the writer will emit. Y is flipped so the
picture has the same orientation as the CAD drawing. No dependencies beyond
the standard library.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence
from xml.sax.saxutils import escape

from .caption import sheet_caption
from .config import Config
from .layout import place_loops
from .models import CircleLoop, Layout, Loop, Part
from .units import MM, Units


def loop_path(loop: Loop, y_flip: float) -> str:
    """SVG path data for one loop. y -> y_flip - y turns the CAD frame (Y up)
    into the SVG frame (Y down)."""
    def f(v: float) -> str:
        return f"{v:.3f}".rstrip("0").rstrip(".") if v else "0"

    if isinstance(loop, CircleLoop):
        cx, cy, r = loop.center[0], y_flip - loop.center[1], loop.radius
        return (f"M {f(cx - r)} {f(cy)} A {f(r)} {f(r)} 0 1 0 {f(cx + r)} {f(cy)} "
                f"A {f(r)} {f(r)} 0 1 0 {f(cx - r)} {f(cy)} Z")
    from .geometry import arc_from_bulge

    first = loop.edges[0].start
    d = [f"M {f(first[0])} {f(y_flip - first[1])}"]
    for e in loop.edges:
        x, y = f(e.end[0]), f(y_flip - e.end[1])
        if e.bulge == 0.0:
            d.append(f"L {x} {y}")
        else:
            _, r, _, sweep = arc_from_bulge(e.start, e.end, e.bulge)
            large = 1 if abs(sweep) > 180.0 else 0
            # After the Y flip a counter-clockwise (bulge > 0) arc is flag 0.
            flag = 0 if e.bulge > 0 else 1
            d.append(f"A {f(r)} {f(r)} 0 {large} {flag} {x} {y}")
    d.append("Z")
    return " ".join(d)


def part_svg(part: Part, *, show_dimensions: bool = True, units: Units = MM) -> str:
    w, h = part.width, part.height
    big = max(w, h)
    pad = big * 0.08
    stroke = big * 0.002
    fs = big * 0.03
    vb_x, vb_y = -pad, -pad
    vb_w, vb_h = w + 2 * pad, h + 2 * pad + (fs * 1.6 if show_dimensions else 0)

    paths = " ".join(loop_path(l, h) for l in (part.outer, *part.holes))
    px_w = min(1000.0, max(300.0, vb_w))
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb_x:.3f} {vb_y:.3f} {vb_w:.3f} {vb_h:.3f}" '
        f'width="{px_w:.0f}" height="{px_w * vb_h / vb_w:.0f}" role="img" aria-label="{escape(part.name)}">',
        f'<rect x="{vb_x:.3f}" y="{vb_y:.3f}" width="{vb_w:.3f}" height="{vb_h:.3f}" fill="white"/>',
        f'<rect x="0" y="0" width="{w:.3f}" height="{h:.3f}" fill="none" stroke="#999" '
        f'stroke-width="{stroke:.3f}" stroke-dasharray="{stroke * 4:.3f} {stroke * 3:.3f}"/>',
        f'<path d="{paths}" fill="#e8d3a8" fill-rule="evenodd" stroke="#222" stroke-width="{stroke * 1.5:.3f}" '
        f'stroke-linejoin="round"/>',
    ]
    if show_dimensions:
        d = units.label_decimals
        label = (f"{escape(part.name)}: {units.size(w, h, d)}, {len(part.holes)} hole(s), "
                 f"cut {units.len(part.cut_length, d)}")
        out.append(f'<text x="0" y="{h + fs * 1.5:.3f}" font-family="sans-serif" font-size="{fs:.3f}" '
                   f'fill="#222">{label}</text>')
    out.append("</svg>")
    return "\n".join(out)


def write_part_svg(part: Part, path: Path | str, **kw) -> Path:
    path = Path(path)
    path.write_text(part_svg(part, **kw), encoding="utf-8")
    return path


PALETTE = ["#e8d3a8", "#bcd4e6", "#c9e2b3", "#f2c6c2", "#d9c3e8", "#f5e29a", "#b7e0d8", "#f0b98d"]


def layout_svg(layout: Layout, parts: Mapping[str, Part], cfg: Config, *,
               colour_order: Sequence[str] | None = None, fill: bool = True, labels: bool = True) -> str:
    """One nested sheet: sheet outline, usable area (margin) dashed, every piece with
    its exact outline, label 'name #n' and rotation. `colour_order` keeps colours
    stable across sheets (default: sorted part names)."""
    sheet = layout.sheet
    W, H = sheet.width, sheet.height
    big = max(W, H)
    pad = big * 0.02
    stroke = big * 0.0007
    fs = big * 0.0095
    footer = fs * 3.2
    order = list(colour_order) if colour_order is not None else sorted(parts)
    colour = {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(order)}

    px_w = 1400.0
    vb_w, vb_h = W + 2 * pad, H + 2 * pad + footer
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-pad:.3f} {-pad:.3f} {vb_w:.3f} {vb_h:.3f}" '
        f'width="{px_w:.0f}" height="{px_w * vb_h / vb_w:.0f}" role="img" aria-label="sheet {layout.index}">',
        f'<rect x="{-pad:.3f}" y="{-pad:.3f}" width="{vb_w:.3f}" height="{vb_h:.3f}" fill="white"/>',
        f'<rect x="0" y="0" width="{W:.3f}" height="{H:.3f}" fill="#fafafa" stroke="#555" stroke-width="{stroke * 2:.3f}"/>',
    ]
    if sheet.margin > 0:
        m = sheet.margin
        out.append(f'<rect x="{m:.3f}" y="{m:.3f}" width="{W - 2 * m:.3f}" height="{H - 2 * m:.3f}" fill="none" '
                   f'stroke="#c33" stroke-width="{stroke:.3f}" stroke-dasharray="{stroke * 5:.3f} {stroke * 4:.3f}"/>')
    for pl in layout.placements:
        part = parts[pl.part_name]
        outer, holes = place_loops(part, pl)
        d = " ".join(loop_path(l, H) for l in (outer, *holes))
        attrs = f'fill="{colour[pl.part_name]}" fill-rule="evenodd"' if fill else 'fill="none"'
        out.append(f'<path d="{d}" {attrs} stroke="#222" stroke-width="{stroke * 1.5:.3f}" stroke-linejoin="round"/>')
        if labels:
            full = f"{pl.part_name} #{pl.instance} ({pl.rotation:g}\u00b0)"
            short = f"#{pl.instance}"
            text = (full if pl.width >= len(full) * fs * 0.62 else
                    short if pl.width >= len(short) * fs * 0.8 else None)
            if text is not None and pl.height > fs * 2.5:
                cx, cy = pl.x + pl.width / 2, H - (pl.y + pl.height / 2)
                out.append(f'<text x="{cx:.3f}" y="{cy:.3f}" text-anchor="middle" dominant-baseline="middle" '
                           f'font-family="sans-serif" font-size="{fs:.3f}" fill="#222">{escape(text)}</text>')
    out.append(f'<text x="0" y="{H + fs * 1.6:.3f}" font-family="sans-serif" font-size="{fs * 1.2:.3f}" fill="#222">'
               f'{escape(sheet_caption(layout, parts, cfg))}</text>')
    out.append("</svg>")
    return "\n".join(out)


def write_layout_svgs(layouts: Sequence[Layout], parts: Mapping[str, Part], cfg: Config,
                      directory: Path | str, **kw) -> list[Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    order = sorted(parts)
    paths = []
    for layout in layouts:
        p = directory / f"sheet_{layout.index:02d}.svg"
        p.write_text(layout_svg(layout, parts, cfg, colour_order=order, **kw), encoding="utf-8")
        paths.append(p)
    return paths
