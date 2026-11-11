"""Module 3b: RawPart -> Part.

    build_part(raw, tolerance=0.01, flatten_tol=0.01) -> Part
    raises PartBuildError (with .issues) when the file cannot be a single part

Rules (one part per file):
  * the largest loop is the outer contour, every other loop must lie inside it
    and becomes a hole;
  * a second, separate outer contour is an error (`multiple_contours`);
  * loops that cross/touch each other, or islands inside holes, are errors;
  * outer contour is made counter-clockwise, holes clockwise;
  * the part is moved so its exact bounding box starts at (0, 0).
"""
from __future__ import annotations

from shapely.geometry import Polygon
from shapely.validation import explain_validity

from .chain import join_primitives
from .geometry import (loop_bounds, loop_length, loop_points, reverse_loop, signed_area,
                       transform_loop)
from .models import Issue, Loop, Part, RawPart, Severity
from .units import MM, Units


class PartBuildError(Exception):
    def __init__(self, name: str, issues: list[Issue]) -> None:
        self.name = name
        self.issues = list(issues)
        errors = [i.message for i in issues if i.severity is Severity.ERROR]
        super().__init__(f"{name}: " + "; ".join(errors))


def _polygon(outer: Loop, holes: tuple[Loop, ...], tol: float) -> Polygon:
    return Polygon(loop_points(outer, tol), [loop_points(h, tol) for h in holes])


def build_part(raw: RawPart, *, tolerance: float = 0.01, flatten_tol: float = 0.01,
               units: Units = MM) -> Part:
    """Tolerances are in mm; `units` only affects how messages show lengths."""
    name = raw.name

    def issue(sev: Severity, code: str, msg: str) -> Issue:
        return Issue(sev, code, msg, part=name)

    chain = join_primitives(raw.primitives, tolerance, units)
    issues = list(raw.issues) + [Issue(i.severity, i.code, i.message, part=name) for i in chain.issues]
    if not chain.ok:
        raise PartBuildError(name, issues)
    if not chain.loops:
        raise PartBuildError(name, issues + [issue(Severity.ERROR, "no_contours", "no usable geometry found")])

    # --- per-loop polygons, validity ---------------------------------------
    errors: list[Issue] = []
    items: list[tuple[Loop, Polygon]] = []
    for loop in chain.loops:
        poly = Polygon(loop_points(loop, flatten_tol))
        if not poly.is_valid:
            b = poly.bounds
            errors.append(issue(Severity.ERROR, "invalid_contour",
                                f"contour near {units.xy((b[0], b[1]), units.label_decimals)} is invalid: "
                                f"{explain_validity(poly)}"))
        elif poly.area <= tolerance * tolerance:
            errors.append(issue(Severity.ERROR, "invalid_contour", "contour with zero area"))
        else:
            items.append((loop, poly))
    if errors:
        raise PartBuildError(name, issues + errors)

    # --- outer vs holes -----------------------------------------------------
    items.sort(key=lambda it: it[1].area, reverse=True)
    outer, outer_poly = items[0]
    holes: list[tuple[Loop, Polygon]] = []
    for loop, poly in items[1:]:
        b = poly.bounds
        where = (f"at {units.xy((b[0], b[1]), units.label_decimals)}, "
                 f"size {units.size(b[2] - b[0], b[3] - b[1], units.label_decimals)}")
        if outer_poly.contains(poly):
            holes.append((loop, poly))
        elif outer_poly.intersects(poly):
            errors.append(issue(Severity.ERROR, "overlapping_contours",
                                f"contour {where} crosses or touches the outer contour"))
        else:
            errors.append(issue(Severity.ERROR, "multiple_contours",
                                f"separate second contour {where}; one closed outer contour per file is expected"))
    for i, (_, a) in enumerate(holes):
        for _, b_ in holes[i + 1:]:
            if a.contains(b_) or b_.contains(a):
                errors.append(issue(Severity.ERROR, "nested_contour",
                                    "a contour lies inside a hole (island); not supported"))
            elif a.intersects(b_):
                errors.append(issue(Severity.ERROR, "overlapping_contours", "two holes overlap or touch"))
    if errors:
        raise PartBuildError(name, issues + errors)

    # --- orientation, origin ------------------------------------------------
    def orient(loop: Loop, want_ccw: bool) -> Loop:
        ccw = signed_area(loop_points(loop, flatten_tol)) > 0
        return loop if ccw == want_ccw else reverse_loop(loop)

    outer = orient(outer, True)
    hole_loops = [orient(h, False) for h, _ in holes]
    minx, miny, maxx, maxy = loop_bounds(outer)
    outer = transform_loop(outer, 0.0, -minx, -miny)
    hole_loops = [transform_loop(h, 0.0, -minx, -miny) for h in hole_loops]
    hole_t = tuple(hole_loops)
    polygon = _polygon(outer, hole_t, flatten_tol)

    return Part(
        name=name,
        path=raw.path,
        outer=outer,
        holes=hole_t,
        polygon=polygon,
        width=maxx - minx,
        height=maxy - miny,
        area=polygon.area,
        cut_length=loop_length(outer) + sum(loop_length(h) for h in hole_t),
        source_offset=(minx, miny),
        issues=tuple(issues),
    )
