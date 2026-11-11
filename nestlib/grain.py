"""Grain and rotation logic. Pure functions on a Part; no I/O.

Conventions
-----------
* A Part is at its own origin, as drawn (outer contour counter-clockwise).
* A placement rotates the part counter-clockwise by `rotation` degrees about
  that origin and then translates it (geometry.transform_loop).
* The sheet's grain runs along one sheet axis: "x" (its width, the first number
  of "2500x1250") or "y" (its height).
* A part's grain rule is relative to its LONGER side: Grain.ALONG = the longer
  side runs along the grain, Grain.ACROSS = across it. A square bounding box
  counts the drawn X axis as its longer side.
* Grain relations only exist for rotations that are multiples of 90 degrees;
  at any other angle `relation` returns None.
"""
from __future__ import annotations

from typing import Sequence

from .geometry import loop_bounds, transform_loop
from .models import Grain, Part


def long_axis(width: float, height: float, tol: float = 1e-6) -> str:
    """Drawn axis of the longer side: "x" or "y" ("x" on a tie)."""
    return "x" if width >= height - tol else "y"


def quarter_turns(rotation: float) -> int | None:
    """rotation / 90 modulo 4, or None if not a multiple of 90 degrees."""
    q = rotation / 90.0
    r = round(q)
    return r % 4 if abs(q - r) < 1e-9 else None


def relation(part: Part, rotation: float, grain_axis: str) -> Grain | None:
    """Grain.ALONG / Grain.ACROSS for the part placed at `rotation`, or None."""
    q = quarter_turns(rotation)
    if q is None:
        return None
    axis = long_axis(part.width, part.height)
    if q % 2:  # a quarter turn swaps the axes
        axis = "y" if axis == "x" else "x"
    return Grain.ALONG if axis == grain_axis else Grain.ACROSS


def describe(rel: Grain | None) -> str:
    return {Grain.ALONG: "longer side along grain", Grain.ACROSS: "longer side across grain"}.get(
        rel, "no grain relation (rotation is not a multiple of 90 degrees)")


def rotated_bounds(part: Part, rotation: float) -> tuple[float, float, float, float]:
    """Exact (minx, miny, maxx, maxy) of the part rotated by `rotation` about its origin."""
    return loop_bounds(transform_loop(part.outer, rotation))


def rotated_size(part: Part, rotation: float) -> tuple[float, float]:
    """Exact bounding-box size (width, height) of the part rotated by `rotation`."""
    x0, y0, x1, y1 = rotated_bounds(part, rotation)
    return x1 - x0, y1 - y0


def allowed_rotations(
    part: Part,
    rule: Grain,
    rotations: Sequence[float],
    grain_axis: str,
    usable_width: float,
    usable_height: float,
    slack: float = 0.0,
) -> tuple[float, ...]:
    """Rotations (from `rotations`, order kept) at which the part satisfies its
    grain rule AND fits on an empty usable sheet area (within `slack`)."""
    out = []
    for r in rotations:
        if rule is not Grain.ANY and relation(part, r, grain_axis) is not rule:
            continue
        w, h = rotated_size(part, r)
        if w <= usable_width + slack and h <= usable_height + slack:
            out.append(r)
    return tuple(out)
