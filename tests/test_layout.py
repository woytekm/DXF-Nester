import math

import pytest
from helpers import cfg, l_part, rect_part

from nestlib.geometry import loop_bounds
from nestlib.layout import place_loops, place_polygon, placement_offset
from nestlib.models import ArcSeg, CircleSeg, LineSeg, Placement, RawPart
from nestlib.contours import build_part
from pathlib import Path


def rounded_plate_with_hole():
    r, w, h = 10.0, 100.0, 60.0
    prims = [LineSeg((r, 0), (w - r, 0)), LineSeg((w, r), (w, h - r)), LineSeg((w - r, h), (r, h)),
             LineSeg((0, h - r), (0, r)), ArcSeg((w - r, r), r, 270, 0), ArcSeg((w - r, h - r), r, 0, 90),
             ArcSeg((r, h - r), r, 90, 180), ArcSeg((r, r), r, 180, 270), CircleSeg((30, 30), 8)]
    return build_part(RawPart("plate", Path("plate.dxf"), tuple(prims), "millimetres"))


@pytest.mark.parametrize("rot, size", [(0, (100, 60)), (90, (60, 100)), (180, (100, 60)), (270, (60, 100))])
def test_bbox_lands_exactly_at_x_y(rot, size):
    part = rounded_plate_with_hole()
    pl = Placement("plate", 1, rot, 250.0, 130.0, *size)
    outer, holes = place_loops(part, pl)
    assert loop_bounds(outer) == pytest.approx((250.0, 130.0, 250.0 + size[0], 130.0 + size[1]))
    assert place_polygon(part, pl).bounds == pytest.approx((250.0, 130.0, 250.0 + size[0], 130.0 + size[1]), abs=0.01)


def test_holes_and_arcs_travel_with_the_part():
    part = rounded_plate_with_hole()
    pl = Placement("plate", 1, 90, 500.0, 500.0, 60.0, 100.0)
    outer, (hole,) = place_loops(part, pl)
    assert sum(1 for e in outer.edges if e.bulge) == 4          # arcs stay arcs
    poly = place_polygon(part, pl)
    assert len(poly.interiors) == 1 and poly.area == pytest.approx(part.area, rel=1e-9)
    # the hole centre (30, 30) -> rotated 90 ccw about the origin = (-30, 30); bbox min of the rotated part is (-60, 0)
    assert hole.center == pytest.approx((500.0 + (-30 + 60), 500.0 + 30))


def test_offset_formula():
    part = rect_part(100, 40)
    assert placement_offset(part, Placement("r", 1, 0, 10, 20, 100, 40)) == pytest.approx((10, 20))
    # rotated 90: bbox min corner is (-40, 0), so dx = x + 40
    assert placement_offset(part, Placement("r", 1, 90, 10, 20, 40, 100)) == pytest.approx((50, 20))


def test_polygon_and_loops_agree():
    part = l_part()
    for rot in (0, 90, 180, 270):
        pl = Placement("L", 1, rot, 7.0, 9.0, 100, 100)
        outer, _ = place_loops(part, pl)
        assert place_polygon(part, pl).bounds == pytest.approx(loop_bounds(outer), abs=1e-9)
        assert place_polygon(part, pl).area == pytest.approx(part.area)
