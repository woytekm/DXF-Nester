import math
from pathlib import Path

import pytest

from nestlib.contours import build_part
from nestlib.loader import load_dxf
from nestlib.models import ArcSeg, CircleSeg, LineSeg, RawPart
from nestlib.tool import analyse_tool

DATA = Path(__file__).parent / "data"
SLACK = 0.01


def poly_prims(pts):
    return [LineSeg(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]


def part_from(prims, name="p"):
    return build_part(RawPart(name, Path(f"{name}.dxf"), tuple(prims), "millimetres"))


def plate_with_slot(width=16.6, depth=100.0, w=200.0, h=150.0):
    """Rectangle w x h with a slot of `width` x `depth` cut into the bottom edge."""
    x0 = 80.0
    return part_from(poly_prims([(0, 0), (x0, 0), (x0, depth), (x0 + width, depth), (x0 + width, 0),
                                 (w, 0), (w, h), (0, h)]))


def corpus(name):
    return build_part(load_dxf(DATA / f"{name}.dxf"))


# ----------------------------------------------------------- plain shapes
def test_convex_shapes_have_nothing_to_report():
    f = analyse_tool(part_from(poly_prims([(0, 0), (100, 0), (100, 50), (0, 50)])), 8.0, SLACK)
    assert f.clean


def test_rounded_rectangle_is_clean():
    r, w, h = 10.0, 100.0, 60.0
    prims = [LineSeg((r, 0), (w - r, 0)), LineSeg((w, r), (w, h - r)), LineSeg((w - r, h), (r, h)),
             LineSeg((0, h - r), (0, r)), ArcSeg((w - r, r), r, 270, 0), ArcSeg((w - r, h - r), r, 0, 90),
             ArcSeg((r, h - r), r, 90, 180), ArcSeg((r, r), r, 180, 270)]
    assert analyse_tool(part_from(prims), 8.0, SLACK).clean


# ------------------------------------------------------------- inner corners
def test_slot_wider_than_tool_only_rounds_its_two_bottom_corners():
    f = analyse_tool(plate_with_slot(16.6), 8.0, SLACK)
    assert len(f.rounded_corners) == 2 and not f.unreachable and not f.small_holes
    xs = sorted(c.where[0] for c in f.rounded_corners)
    assert xs[0] < 90 < xs[1]                         # one at each side of the slot centre (x = 88.3)
    assert all(abs(c.where[1] - 100.0) < 4.0 for c in f.rounded_corners)   # at the slot bottom (y = 100)


def test_l_shape_has_exactly_one_inner_corner():
    part = part_from(poly_prims([(0, 0), (100, 0), (100, 40), (40, 40), (40, 100), (0, 100)]))
    f = analyse_tool(part, 6.0, SLACK)
    assert len(f.rounded_corners) == 1 and not f.unreachable
    assert math.dist(f.rounded_corners[0].where, (40, 40)) < 6.0


def test_small_inner_arc_counts_as_rounded_corner_and_big_one_does_not():
    def with_concave_arc(radius):
        # plate with a quarter-circle notch of the given radius in the bottom-left corner
        return part_from([LineSeg((radius, 0), (100, 0)), LineSeg((100, 0), (100, 100)),
                          LineSeg((100, 100), (0, 100)), LineSeg((0, 100), (0, radius)),
                          ArcSeg((0, 0), radius, 0, 90)])
    assert analyse_tool(with_concave_arc(30.0), 8.0, SLACK).clean          # R30 notch, tool R4: fully cut
    assert analyse_tool(with_concave_arc(30.0), 80.0, SLACK).rounded_corners   # tool R40 cannot follow R30


# ------------------------------------------------------------ narrow features
@pytest.mark.parametrize("tool, ok", [(16.0, True), (16.5, True), (16.6, True), (16.8, False), (20.0, False)])
def test_slot_width_against_tool(tool, ok):
    f = analyse_tool(plate_with_slot(16.6), tool, SLACK)
    assert bool(f.unreachable) is (not ok)


def test_narrow_slot_error_points_into_the_slot():
    f = analyse_tool(plate_with_slot(6.0), 8.0, SLACK)
    assert len(f.unreachable) == 1
    u = f.unreachable[0]
    assert 80 < u.where[0] < 86 and u.where[1] < 100 and u.height > 50       # the slot itself, not only its corners


def test_acute_notch_is_unreachable():
    # 30-degree V notch cut into the bottom edge
    half = math.tan(math.radians(15)) * 80
    part = part_from(poly_prims([(0, 0), (100 - half, 0), (100, 80), (100 + half, 0), (200, 0), (200, 120), (0, 120)]))
    f = analyse_tool(part, 8.0, SLACK)
    assert f.unreachable and not f.small_holes


def test_square_hole_corners_and_small_hole():
    prims = poly_prims([(0, 0), (100, 0), (100, 100), (0, 100)]) + poly_prims([(40, 40), (60, 40), (60, 60), (40, 60)])
    part = part_from(prims)
    f = analyse_tool(part, 8.0, SLACK)                       # 20 mm hole, 8 mm tool: 4 rounded corners
    assert len(f.rounded_corners) == 4 and not f.small_holes
    f = analyse_tool(part, 25.0, SLACK)                      # tool does not fit in the hole
    assert len(f.small_holes) == 1 and not f.rounded_corners and not f.unreachable
    assert (f.small_holes[0].width, f.small_holes[0].height) == pytest.approx((20.0, 20.0))


def test_round_holes():
    base = poly_prims([(0, 0), (100, 0), (100, 100), (0, 100)])
    assert analyse_tool(part_from(base + [CircleSeg((50, 50), 5)]), 8.0, SLACK).clean       # 10 mm hole
    f = analyse_tool(part_from(base + [CircleSeg((50, 50), 3)]), 8.0, SLACK)                # 6 mm hole
    assert len(f.small_holes) == 1 and not f.rounded_corners


# -------------------------------------------------------------- real samples
def test_corpus_with_a_typical_8mm_bit():
    assert len(analyse_tool(corpus("polka_typ_3"), 8.0, SLACK).rounded_corners) == 8          # 4 slots x 2
    assert len(analyse_tool(corpus("przegroda_typ_3"), 8.0, SLACK).rounded_corners) == 10     # 5 slots x 2
    assert analyse_tool(corpus("szuflada_bok"), 8.0, SLACK).clean
    assert analyse_tool(corpus("szuflada_front"), 8.0, SLACK).clean                           # R20 corners are fine


def test_corpus_with_a_20mm_bit_the_slots_cannot_be_cut():
    f = analyse_tool(corpus("polka_typ_3"), 20.0, SLACK)
    assert len(f.unreachable) == 4                           # one per 16.6 mm slot
    f = analyse_tool(corpus("przegroda_typ_3"), 20.0, SLACK)
    assert len(f.unreachable) == 5


def test_corpus_boundary_bit_equal_to_slot_width():
    assert not analyse_tool(corpus("polka_typ_3"), 16.6, SLACK).unreachable
    assert analyse_tool(corpus("polka_typ_3"), 16.7, SLACK).unreachable


def test_front_r20_corners_with_a_big_bit():
    f = analyse_tool(corpus("szuflada_front"), 50.0, SLACK)     # R25 tool cannot follow the R20 corners
    assert len(f.rounded_corners) == 2 and not f.unreachable
