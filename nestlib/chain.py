"""Module 3a: join primitives into closed loops.

    join_primitives(primitives, tolerance) -> ChainResult(loops, issues)

Steps: snap endpoints that lie within `tolerance` of each other onto shared
nodes; drop zero-length segments; drop duplicate segments (same geometry drawn
twice, which would double the cut length); then walk the node graph. Every node
must have exactly two segments: dangling ends (open contour) and junctions
(branching/overlapping contours) are reported as ERROR issues with coordinates.

Full circles, and arcs whose ends meet themselves, become CircleLoop.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from .models import (ArcSeg, CircleLoop, CircleSeg, Edge, Issue, LineSeg, Loop, Point,
                     PolyLoop, Primitive, Severity)
from .units import MM, Units


@dataclass(frozen=True)
class ChainResult:
    loops: tuple[Loop, ...]
    issues: tuple[Issue, ...]

    @property
    def ok(self) -> bool:
        return not any(i.severity is Severity.ERROR for i in self.issues)


class _Snapper:
    """Assigns node ids to points; points within `tol` share a node."""

    def __init__(self, tol: float) -> None:
        self.tol = tol
        self.points: list[Point] = []
        self._grid: dict[tuple[int, int], list[int]] = defaultdict(list)

    def node(self, p: Point) -> int:
        cx, cy = math.floor(p[0] / self.tol), math.floor(p[1] / self.tol)
        for i in (cx - 1, cx, cx + 1):
            for j in (cy - 1, cy, cy + 1):
                for nid in self._grid.get((i, j), ()):
                    if math.dist(self.points[nid], p) <= self.tol:
                        return nid
        nid = len(self.points)
        self.points.append(p)
        self._grid[(cx, cy)].append(nid)
        return nid


@dataclass
class _Seg:
    na: int
    nb: int
    arc: ArcSeg | None  # None = straight line; arcs run counter-clockwise na -> nb


def _same_geometry(a: ArcSeg | None, b: ArcSeg | None, tol: float) -> bool:
    """Segments sharing the same node pair: lines are always identical; arcs
    must also share centre and radius (two different arcs can span one chord)."""
    if a is None or b is None:
        return a is None and b is None
    return math.dist(a.center, b.center) <= tol and abs(a.radius - b.radius) <= tol


def join_primitives(primitives: tuple[Primitive, ...] | list[Primitive], tolerance: float,
                    units: Units = MM) -> ChainResult:
    """`tolerance` is in mm; `units` only affects how messages show lengths."""
    tol = tolerance
    snap = _Snapper(tol)
    circles: list[CircleLoop] = []
    segs: list[_Seg] = []
    seen: dict[tuple, list[_Seg]] = defaultdict(list)
    degenerate = 0
    duplicates = 0

    def add_circle(center: Point, radius: float) -> None:
        nonlocal duplicates
        if any(math.dist(c.center, center) <= tol and abs(c.radius - radius) <= tol for c in circles):
            duplicates += 1
        else:
            circles.append(CircleLoop(center, radius))

    for prim in primitives:
        if isinstance(prim, CircleSeg):
            add_circle(prim.center, prim.radius)
            continue
        if isinstance(prim, LineSeg):
            na, nb = snap.node(prim.start), snap.node(prim.end)
            if na == nb:
                degenerate += 1
                continue
            key: tuple = ("L", min(na, nb), max(na, nb))
            arc = None
        else:
            na, nb = snap.node(prim.start_point), snap.node(prim.end_point)
            if na == nb:
                if prim.sweep > 180.0:
                    add_circle(prim.center, prim.radius)  # arc closing on itself
                else:
                    degenerate += 1
                continue
            key = ("A", na, nb)
            arc = prim
        same = any(_same_geometry(e.arc, arc, tol) for e in seen[key])
        if same:
            duplicates += 1
            continue
        seg = _Seg(na, nb, arc)
        seen[key].append(seg)
        segs.append(seg)

    issues: list[Issue] = []
    if degenerate:
        issues.append(Issue(Severity.INFO, "degenerate_segment",
                            f"{degenerate} zero-length segment(s) removed"))
    if duplicates:
        issues.append(Issue(Severity.INFO, "duplicate_geometry",
                            f"{duplicates} duplicate segment(s) removed (identical geometry drawn "
                            f"more than once would double the cut length)"))

    adj: dict[int, list[int]] = defaultdict(list)
    for i, s in enumerate(segs):
        adj[s.na].append(i)
        adj[s.nb].append(i)

    open_nodes = [n for n, l in adj.items() if len(l) == 1]
    branch_nodes = [n for n, l in adj.items() if len(l) > 2]
    if open_nodes:
        parts = []
        for n in open_nodes[:5]:
            others = [math.dist(snap.points[n], snap.points[m]) for m in open_nodes if m != n]
            hint = f", nearest other open end {units.len(min(others))} away" if others else ""
            parts.append(f"{units.xy(snap.points[n])}{hint}")
        more = f" (+{len(open_nodes) - 5} more)" if len(open_nodes) > 5 else ""
        issues.append(Issue(Severity.ERROR, "open_contour",
                            f"contour is not closed: {len(open_nodes)} open end(s) at " + "; ".join(parts) + more))
    if branch_nodes:
        where = "; ".join(units.xy(snap.points[n]) for n in branch_nodes[:5])
        issues.append(Issue(Severity.ERROR, "branching_contour",
                            f"{len(branch_nodes)} point(s) where 3 or more segments meet "
                            f"(overlapping or touching contours) at {where}"))
    if open_nodes or branch_nodes:
        return ChainResult(tuple(circles), tuple(issues))

    loops: list[Loop] = list(circles)
    used = [False] * len(segs)
    pts = snap.points
    for first in range(len(segs)):
        if used[first]:
            continue
        edges: list[Edge] = []
        start = cur = segs[first].na
        sid = first
        while True:
            used[sid] = True
            s = segs[sid]
            forward = cur == s.na
            nxt = s.nb if forward else s.na
            bulge = 0.0
            if s.arc is not None:
                bulge = math.tan(math.radians(s.arc.sweep) / 4.0)
                if not forward:
                    bulge = -bulge
            edges.append(Edge(pts[cur], pts[nxt], bulge))
            if nxt == start:
                break
            a, b = adj[nxt]
            sid = b if a == sid else a
            cur = nxt
        loops.append(PolyLoop(tuple(edges)))
    return ChainResult(tuple(loops), tuple(issues))
