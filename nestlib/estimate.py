"""Cut length, pierce count and a rough machining-time estimate for a nested job.

    cut_summary(layouts, parts)            -> CutSummary     exact, no assumptions
    estimate_time(layouts, parts, config)  -> TimeEstimate | None   None unless feed_rate is set

Cut length is the length of the part contours (outer + holes), the figure a shop
prices by. A pierce is one plunge point: one per closed contour, holes included.

The time model is deliberately simple and says so:

  passes        = ceil(thickness / depth - 1 %)  with depth = pass_depth, else the tool diameter,
                  else the full thickness; 1 pass if no thickness is given
  cutting time  = contour length x passes / feed_rate
  plunge time   = pierces x passes x (thickness / passes) / (feed_rate / 2)
  rapid time    = a nearest-neighbour tour over the contour start points / rapid_rate

It uses the contour length, not the tool-centre path (a few percent shorter or
longer), and leaves out setup, tool changes, tabs, ramps, lead-ins, acceleration
and the time to change sheets. Treat it as an order-of-magnitude figure.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

from .config import Config
from .geometry import loop_length
from .layout import place_loops
from .models import CircleLoop, Layout, Loop, Part, Point

PLUNGE_FRACTION = 0.5     # plunge feed = feed_rate x this


# ------------------------------------------------------------------ lengths
@dataclass(frozen=True)
class SheetCut:
    index: int
    pieces: int
    outer_length: float     # mm
    inner_length: float     # mm
    pierces: int

    @property
    def length(self) -> float:
        return self.outer_length + self.inner_length


@dataclass(frozen=True)
class CutSummary:
    sheets: tuple[SheetCut, ...]

    @property
    def pieces(self) -> int:
        return sum(s.pieces for s in self.sheets)

    @property
    def outer_length(self) -> float:
        return sum(s.outer_length for s in self.sheets)

    @property
    def inner_length(self) -> float:
        return sum(s.inner_length for s in self.sheets)

    @property
    def length(self) -> float:
        return self.outer_length + self.inner_length

    @property
    def pierces(self) -> int:
        return sum(s.pierces for s in self.sheets)


def sheet_cut(layout: Layout, parts: Mapping[str, Part]) -> SheetCut:
    outer = inner = 0.0
    pierces = 0
    for pl in layout.placements:
        part = parts[pl.part_name]
        outer += loop_length(part.outer)                    # rotation does not change lengths
        inner += sum(loop_length(h) for h in part.holes)
        pierces += 1 + len(part.holes)
    return SheetCut(layout.index, len(layout.placements), outer, inner, pierces)


def cut_summary(layouts: Sequence[Layout], parts: Mapping[str, Part]) -> CutSummary:
    return CutSummary(tuple(sheet_cut(l, parts) for l in layouts))


# --------------------------------------------------------------------- time
def plan_passes(cfg: Config) -> tuple[int, float | None]:
    """(number of passes, depth per pass or None if thickness is unknown)."""
    if cfg.thickness is None:
        return 1, None
    depth = cfg.pass_depth or cfg.tool_diameter or cfg.thickness
    # Tolerate 1 % of a pass: rounded inch input (0.7087 in / 0.2362 in = 3.0003) must not cost a fourth pass.
    n = max(1, math.ceil(cfg.thickness / depth - 0.01))
    return n, cfg.thickness / n


def _start(loop: Loop) -> Point:
    if isinstance(loop, CircleLoop):
        return (loop.center[0] + loop.radius, loop.center[1])
    return loop.edges[0].start


def contour_starts(layout: Layout, parts: Mapping[str, Part]) -> list[Point]:
    pts: list[Point] = []
    for pl in layout.placements:
        outer, holes = place_loops(parts[pl.part_name], pl)
        pts.extend(_start(l) for l in (*holes, outer))
    return pts


def nearest_neighbour_length(points: Sequence[Point], start: Point = (0.0, 0.0)) -> float:
    """Length of a greedy nearest-neighbour path through the points, beginning at `start`."""
    left = list(points)
    here, total = start, 0.0
    while left:
        i = min(range(len(left)), key=lambda k: math.dist(here, left[k]))
        total += math.dist(here, left[i])
        here = left.pop(i)
    return total


@dataclass(frozen=True)
class TimeEstimate:
    passes: int
    depth_per_pass: float | None     # mm; None when thickness is unknown
    feed_rate: float                 # mm/min
    rapid_rate: float                # mm/min
    cutting_s: float
    plunge_s: float
    rapid_s: float
    per_sheet_s: tuple[float, ...]

    @property
    def total_s(self) -> float:
        return self.cutting_s + self.plunge_s + self.rapid_s


def estimate_time(layouts: Sequence[Layout], parts: Mapping[str, Part], cfg: Config) -> TimeEstimate | None:
    if cfg.feed_rate is None:
        return None
    passes, depth = plan_passes(cfg)
    plunge_rate = cfg.feed_rate * PLUNGE_FRACTION
    cutting = plunge = rapid = 0.0
    per_sheet = []
    for layout in layouts:
        sc = sheet_cut(layout, parts)
        c = sc.length * passes / cfg.feed_rate * 60.0
        p = sc.pierces * passes * (depth or 0.0) / plunge_rate * 60.0
        r = nearest_neighbour_length(contour_starts(layout, parts)) / cfg.rapid_rate * 60.0
        cutting, plunge, rapid = cutting + c, plunge + p, rapid + r
        per_sheet.append(c + p + r)
    return TimeEstimate(passes, depth, cfg.feed_rate, cfg.rapid_rate, cutting, plunge, rapid, tuple(per_sheet))


def format_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{round(seconds / 60)} min"
    minutes = round(seconds / 60)
    return f"{minutes // 60} h {minutes % 60:02d} min"


def describe_assumptions(est: TimeEstimate, cfg: Config) -> str:
    u = cfg.units
    if est.depth_per_pass is None:
        depth = "1 pass (no --thickness given, plunge time ignored)"
    else:
        depth = f"{est.passes} pass(es) of {u.len(est.depth_per_pass, u.label_decimals)} (thickness {u.len(cfg.thickness, u.label_decimals)})"
    return (f"feed {u.rate(est.feed_rate)}, {depth}, rapids {u.rate(est.rapid_rate)}; contour length, no tool offset; "
            f"excludes setup, tool changes, tabs, ramps and sheet changes")
