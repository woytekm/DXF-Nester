"""Pure geometry helpers on the Loop types of models.py. No I/O, no DXF.

Conventions: angles in degrees, counter-clockwise positive; lengths in mm.
"""
from __future__ import annotations

import math

from .models import CircleLoop, Edge, Loop, Point, PolyLoop


def arc_from_bulge(start: Point, end: Point, bulge: float) -> tuple[Point, float, float, float]:
    """(center, radius, start_angle_deg, signed_sweep_deg) of a bulge segment.

    Sweep is positive (counter-clockwise) for bulge > 0, negative otherwise.
    """
    dx, dy = end[0] - start[0], end[1] - start[1]
    chord = math.hypot(dx, dy)
    offset = (chord / 2.0) * (1.0 - bulge * bulge) / (2.0 * bulge)  # along the left normal
    cx = (start[0] + end[0]) / 2.0 - dy / chord * offset
    cy = (start[1] + end[1]) / 2.0 + dx / chord * offset
    radius = math.hypot(start[0] - cx, start[1] - cy)
    a0 = math.degrees(math.atan2(start[1] - cy, start[0] - cx))
    return (cx, cy), radius, a0, math.degrees(4.0 * math.atan(bulge))


def _angular_step(radius: float, tol: float) -> float:
    """Largest step (radians) whose chord deviates from the arc by <= tol."""
    return 2.0 * math.acos(1.0 - min(tol / radius, 1.0))


def edge_points(edge: Edge, tol: float) -> list[Point]:
    """Points along the edge from start (inclusive) to end (exclusive)."""
    if edge.bulge == 0.0:
        return [edge.start]
    (cx, cy), r, a0, sweep = arc_from_bulge(edge.start, edge.end, edge.bulge)
    theta = math.radians(sweep)
    n = max(1, math.ceil(abs(theta) / _angular_step(r, tol)))
    a0r = math.radians(a0)
    pts = [edge.start]
    for i in range(1, n):
        a = a0r + theta * i / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def loop_points(loop: Loop, tol: float) -> list[Point]:
    """Open ring of points (first point not repeated) approximating the loop
    within `tol`. Circles start at angle 0 and run counter-clockwise."""
    if isinstance(loop, CircleLoop):
        n = max(16, math.ceil(2.0 * math.pi / _angular_step(loop.radius, tol)))
        return [
            (loop.center[0] + loop.radius * math.cos(2.0 * math.pi * i / n),
             loop.center[1] + loop.radius * math.sin(2.0 * math.pi * i / n))
            for i in range(n)
        ]
    pts: list[Point] = []
    for e in loop.edges:
        pts += edge_points(e, tol)
    return pts


def signed_area(points: list[Point]) -> float:
    """Shoelace area of an open ring: > 0 for counter-clockwise."""
    s = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]):
        s += x0 * y1 - x1 * y0
    return s / 2.0


def edge_length(edge: Edge) -> float:
    if edge.bulge == 0.0:
        return math.dist(edge.start, edge.end)
    _, r, _, sweep = arc_from_bulge(edge.start, edge.end, edge.bulge)
    return r * math.radians(abs(sweep))


def loop_length(loop: Loop) -> float:
    if isinstance(loop, CircleLoop):
        return 2.0 * math.pi * loop.radius
    return sum(edge_length(e) for e in loop.edges)


def loop_bounds(loop: Loop) -> tuple[float, float, float, float]:
    """Exact (minx, miny, maxx, maxy), including arc extremes."""
    if isinstance(loop, CircleLoop):
        (cx, cy), r = loop.center, loop.radius
        return cx - r, cy - r, cx + r, cy + r
    pts: list[Point] = []
    for e in loop.edges:
        pts += [e.start, e.end]
        if e.bulge != 0.0:
            (cx, cy), r, a0, sweep = arc_from_bulge(e.start, e.end, e.bulge)
            for q in (0.0, 90.0, 180.0, 270.0):
                t = (q - a0) % 360.0 if sweep > 0 else (a0 - q) % 360.0
                if t <= abs(sweep):
                    pts.append((cx + r * math.cos(math.radians(q)), cy + r * math.sin(math.radians(q))))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def transform_point(p: Point, angle_deg: float = 0.0, dx: float = 0.0, dy: float = 0.0) -> Point:
    """Rotate counter-clockwise about the origin, then translate."""
    c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    return (p[0] * c - p[1] * s + dx, p[0] * s + p[1] * c + dy)


def transform_loop(loop: Loop, angle_deg: float = 0.0, dx: float = 0.0, dy: float = 0.0) -> Loop:
    """Rigid motion (rotation about the origin, then translation). Bulges are
    invariant under rigid motions, so arcs stay arcs."""
    if isinstance(loop, CircleLoop):
        return CircleLoop(transform_point(loop.center, angle_deg, dx, dy), loop.radius)
    return PolyLoop(tuple(
        Edge(transform_point(e.start, angle_deg, dx, dy), transform_point(e.end, angle_deg, dx, dy), e.bulge)
        for e in loop.edges
    ))


def reverse_loop(loop: Loop) -> Loop:
    if isinstance(loop, CircleLoop):
        return loop
    return PolyLoop(tuple(Edge(e.end, e.start, -e.bulge if e.bulge else 0.0) for e in reversed(loop.edges)))
