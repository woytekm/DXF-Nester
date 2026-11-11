"""Tool reach analysis: what can a round router bit NOT cut in a part?

    analyse_tool(part, tool_diameter, slack) -> ToolFindings

The waste region E (everything outside the part, plus the inside of holes) must
be swept by a disc of radius r. The part of E the disc can reach is the
morphological opening of E by that disc; what is left over is unreachable:

* a thin sliver tucked into an inner corner (or into a concave arc tighter than
  the tool): the cut is made, but the corner ends up rounded to radius r
  -> `rounded_corners`  (a deviation: warn);
* a larger leftover: a slot narrower than the tool, or an acute notch
  -> `unreachable`      (cannot be made with this tool: error);
* a hole into which the tool does not fit at all
  -> `small_holes`      (cannot be made with this tool: error).

`slack` (mm) makes the tool effectively that much smaller, so a 16.6 mm slot
does not fail against a 16.6 mm bit because of rounding noise.

All coordinates are in the part's own frame (lower-left corner of its bounding
box at the origin), in mm.
"""
from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry

from .models import Part, Point

_QUAD_SEGS = 32
_MIN_AREA = 1e-3          # mm^2: ignore numerical dust
_MIN_THICKNESS = 0.02     # mm: slivers thinner than this are flattening artefacts, not corners
_CORNER_AREA = 0.5        # of r^2: a 90-degree corner sliver is ~0.21 r^2, two merged ~0.43 r^2
_CORNER_EXTENT = 2.1      # of r


@dataclass(frozen=True)
class Finding:
    where: Point           # representative point, part frame
    width: float           # bounding-box size of the region, mm
    height: float
    area: float


@dataclass(frozen=True)
class ToolFindings:
    tool_diameter: float
    rounded_corners: tuple[Finding, ...] = ()
    unreachable: tuple[Finding, ...] = ()
    small_holes: tuple[Finding, ...] = ()

    @property
    def clean(self) -> bool:
        return not (self.rounded_corners or self.unreachable or self.small_holes)


def _polygons(g: BaseGeometry) -> list[Polygon]:
    if g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [p for geom in getattr(g, "geoms", []) for p in _polygons(geom)]


def _finding(poly: Polygon) -> Finding:
    x0, y0, x1, y1 = poly.bounds
    pt = poly.representative_point()
    return Finding((pt.x, pt.y), x1 - x0, y1 - y0, poly.area)


def analyse_tool(part: Part, tool_diameter: float, slack: float = 0.0) -> ToolFindings:
    r = max(tool_diameter / 2.0 - slack, 1e-6)
    poly = part.polygon

    holes = [Polygon(ring) for ring in poly.interiors]
    small = [h for h in holes if h.buffer(-r, quad_segs=_QUAD_SEGS).is_empty]

    x0, y0, x1, y1 = poly.bounds
    pad = 3.0 * tool_diameter
    waste = box(x0 - pad, y0 - pad, x1 + pad, y1 + pad).difference(poly)
    reach = waste.buffer(-r, quad_segs=_QUAD_SEGS).buffer(r, quad_segs=_QUAD_SEGS)
    leftover = waste.difference(reach)

    corners: list[Finding] = []
    bad: list[Finding] = []
    for comp in _polygons(leftover):
        if comp.area < _MIN_AREA:
            continue
        if comp.distance(poly) > 2.0 * slack + 1e-6:      # artefact at the padding frame
            continue
        if any(comp.within(h.buffer(1e-6)) for h in small):  # reported as a small hole instead
            continue
        f = _finding(comp)
        if comp.area / max(f.width, f.height) < max(_MIN_THICKNESS, 2.0 * slack):
            continue    # vertex of a flattened arc (a 90-degree corner sliver is ~0.2 r thick)
        sliver = comp.area <= _CORNER_AREA * r * r and max(f.width, f.height) <= _CORNER_EXTENT * r
        (corners if sliver else bad).append(f)

    key = lambda f: (round(f.where[1], 3), round(f.where[0], 3))
    return ToolFindings(
        tool_diameter,
        rounded_corners=tuple(sorted(corners, key=key)),
        unreachable=tuple(sorted(bad, key=key)),
        small_holes=tuple(sorted((_finding(h) for h in small), key=key)),
    )
