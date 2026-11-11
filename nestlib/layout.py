"""Apply a Placement to a Part: the one place that defines what a Placement means.

Used by the DXF writer, the preview and the layout verifier, so all of them
agree on where a placed piece actually is.
"""
from __future__ import annotations

from shapely import affinity
from shapely.geometry import Polygon

from .geometry import transform_loop
from .grain import rotated_bounds
from .models import Loop, Part, Placement


def placement_offset(part: Part, placement: Placement) -> tuple[float, float]:
    """(dx, dy) to add after rotating the part about its origin."""
    minx, miny, _, _ = rotated_bounds(part, placement.rotation)
    return placement.x - minx, placement.y - miny


def place_loops(part: Part, placement: Placement) -> tuple[Loop, tuple[Loop, ...]]:
    """(outer, holes) in sheet coordinates. Arcs stay arcs."""
    dx, dy = placement_offset(part, placement)
    return (transform_loop(part.outer, placement.rotation, dx, dy),
            tuple(transform_loop(h, placement.rotation, dx, dy) for h in part.holes))


def place_polygon(part: Part, placement: Placement) -> Polygon:
    """Flattened outline (holes included) in sheet coordinates."""
    dx, dy = placement_offset(part, placement)
    rotated = affinity.rotate(part.polygon, placement.rotation, origin=(0.0, 0.0))
    return affinity.translate(rotated, dx, dy)
