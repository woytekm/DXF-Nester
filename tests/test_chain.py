import math
import random
import time

import pytest

from nestlib.chain import join_primitives
from nestlib.geometry import loop_bounds, loop_length, loop_points, signed_area
from nestlib.models import ArcSeg, CircleLoop, CircleSeg, LineSeg, PolyLoop

TOL = 0.01


def square(x0=0.0, y0=0.0, s=10.0, layer="0"):
    c = [(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s)]
    return [LineSeg(c[i], c[(i + 1) % 4], layer) for i in range(4)]


def codes(res):
    return {i.code for i in res.issues}


def assert_closed_chain(loop: PolyLoop):
    for a, b in zip(loop.edges, loop.edges[1:] + loop.edges[:1]):
        assert math.dist(a.end, b.start) < 1e-9


def test_square_any_order_any_direction():
    prims = square()
    random.Random(1).shuffle(prims)
    prims = [LineSeg(p.end, p.start) if i % 2 else p for i, p in enumerate(prims)]  # flip some
    res = join_primitives(prims, TOL)
    assert res.ok and len(res.loops) == 1 and len(res.loops[0].edges) == 4
    assert_closed_chain(res.loops[0])
    assert math.isclose(abs(signed_area(loop_points(res.loops[0], TOL))), 100.0)


def test_gap_within_tolerance_is_joined():
    prims = square()
    prims[1] = LineSeg((10.004, 0.0), (10.0, 10.0))  # 4 um gap
    res = join_primitives(prims, TOL)
    assert res.ok and len(res.loops) == 1


def test_gap_beyond_tolerance_reports_open_contour_with_hint():
    prims = square()
    prims[1] = LineSeg((10.5, 0.0), (10.0, 10.0))
    res = join_primitives(prims, TOL)
    assert not res.ok and "open_contour" in codes(res) and res.loops == ()
    msg = next(i.message for i in res.issues if i.code == "open_contour")
    assert "2 open end(s)" in msg and "0.500 mm" in msg and "(10.000, 0.000)" in msg


def test_duplicates_removed_regardless_of_direction():
    prims = square() + [LineSeg(p.end, p.start) for p in square()] + square()
    res = join_primitives(prims, TOL)
    assert res.ok and len(res.loops) == 1 and len(res.loops[0].edges) == 4
    msg = next(i.message for i in res.issues if i.code == "duplicate_geometry")
    assert msg.startswith("8 duplicate")


def test_zero_length_segments_dropped():
    prims = square() + [LineSeg((10.0, 0.0), (10.001, 0.0))]
    res = join_primitives(prims, TOL)
    assert res.ok and len(res.loops[0].edges) == 4 and "degenerate_segment" in codes(res)


def test_two_separate_squares_give_two_loops():
    res = join_primitives(square() + square(50, 50), TOL)
    assert res.ok and len(res.loops) == 2


def test_squares_touching_at_a_corner_is_branching_error():
    res = join_primitives(square() + square(10, 10), TOL)
    assert not res.ok and "branching_contour" in codes(res)
    assert "(10.000, 10.000)" in next(i.message for i in res.issues if i.code == "branching_contour")


def test_squares_sharing_an_edge_is_branching_error():
    res = join_primitives(square() + square(10, 0), TOL)
    assert "branching_contour" in codes(res)


def rounded_rect(w=100.0, h=60.0, r=10.0):
    lines = [LineSeg((r, 0), (w - r, 0)), LineSeg((w, r), (w, h - r)),
             LineSeg((w - r, h), (r, h)), LineSeg((0, h - r), (0, r))]
    arcs = [ArcSeg((w - r, r), r, 270, 0), ArcSeg((w - r, h - r), r, 0, 90),
            ArcSeg((r, h - r), r, 90, 180), ArcSeg((r, r), r, 180, 270)]
    return lines, arcs


def test_rounded_rectangle_with_arcs():
    lines, arcs = rounded_rect()
    prims = lines + arcs
    random.Random(7).shuffle(prims)
    res = join_primitives(prims, TOL)
    assert res.ok and len(res.loops) == 1
    loop = res.loops[0]
    assert len(loop.edges) == 8 and sum(1 for e in loop.edges if e.bulge) == 4
    assert_closed_chain(loop)
    assert loop_bounds(loop) == pytest.approx((0, 0, 100, 60))
    expected_area = 100 * 60 - (4 - math.pi) * 10 ** 2
    assert abs(signed_area(loop_points(loop, 0.001))) == pytest.approx(expected_area, abs=0.5)
    assert loop_length(loop) == pytest.approx(2 * (80 + 40) + 2 * math.pi * 10)


def test_arc_traversal_direction_gives_consistent_bulge_sign():
    # same rounded rectangle; whichever way the walk goes, every arc edge must
    # bulge outward: all bulges share a sign relative to the loop orientation
    lines, arcs = rounded_rect()
    loop = join_primitives(lines + arcs, TOL).loops[0]
    ccw = signed_area(loop_points(loop, TOL)) > 0
    signs = {(e.bulge > 0) for e in loop.edges if e.bulge}
    assert signs == {ccw}


def test_circles_and_dedupe():
    res = join_primitives([CircleSeg((0, 0), 5), CircleSeg((0.001, 0), 5.002), CircleSeg((50, 0), 5)], TOL)
    assert res.ok and res.loops == (CircleLoop((0, 0), 5), CircleLoop((50, 0), 5))
    assert "duplicate_geometry" in codes(res)


def test_two_semicircle_arcs_form_a_loop():
    res = join_primitives([ArcSeg((0, 0), 5, 0, 180), ArcSeg((0, 0), 5, 180, 0)], TOL)
    assert res.ok and len(res.loops) == 1 and len(res.loops[0].edges) == 2
    assert loop_length(res.loops[0]) == pytest.approx(10 * math.pi)


def test_arc_closing_on_itself_becomes_circle():
    res = join_primitives([ArcSeg((0, 0), 5, 0, 359.9999)], TOL)
    assert res.ok and res.loops == (CircleLoop((0, 0), 5),)


def test_tiny_arc_with_coincident_ends_is_dropped():
    res = join_primitives(square() + [ArcSeg((0, 0), 5, 0, 0.01)], TOL)
    assert res.ok and len(res.loops) == 1 and "degenerate_segment" in codes(res)


def test_two_arcs_over_same_chord_are_not_duplicates():
    # P=(0,0) -> Q=(10,0), both counter-clockwise: a minor arc (bulge 0.5) and a
    # major arc (bulge 2.0). Same node pair, same direction, different circles:
    # they must NOT be merged as duplicates; together they bound a crescent.
    from nestlib.geometry import arc_from_bulge

    def arc(bulge):
        c, r, a0, sweep = arc_from_bulge((0.0, 0.0), (10.0, 0.0), bulge)
        return ArcSeg(c, r, a0 % 360.0, (a0 + sweep) % 360.0)

    res = join_primitives([arc(0.5), arc(2.0)], TOL)
    assert res.ok and "duplicate_geometry" not in codes(res)
    assert len(res.loops) == 1 and len(res.loops[0].edges) == 2


def test_large_polygon_is_fast():
    n = 5000
    pts = [(1000 * math.cos(2 * math.pi * i / n), 1000 * math.sin(2 * math.pi * i / n)) for i in range(n)]
    prims = [LineSeg(pts[i], pts[(i + 1) % n]) for i in range(n)]
    random.Random(3).shuffle(prims)
    t = time.perf_counter()
    res = join_primitives(prims, TOL)
    assert res.ok and len(res.loops) == 1 and len(res.loops[0].edges) == n
    assert time.perf_counter() - t < 2.0


def test_empty_input():
    res = join_primitives([], TOL)
    assert res.ok and res.loops == () and res.issues == ()


@pytest.mark.parametrize("clockwise_start", [False, True])
def test_arcs_correct_when_walk_runs_clockwise(clockwise_start):
    # The walk direction is set by the first segment. Starting with a line that
    # is drawn clockwise forces every ccw-defined arc to be traversed in reverse.
    lines, arcs = rounded_rect()
    if clockwise_start:
        lines = [LineSeg(l.end, l.start) for l in lines]
    res = join_primitives(lines + arcs, TOL)
    assert res.ok and len(res.loops) == 1
    loop = res.loops[0]
    assert_closed_chain(loop)
    arc_edges = [e for e in loop.edges if e.bulge]
    assert len(arc_edges) == 4
    ccw = signed_area(loop_points(loop, TOL)) > 0
    assert ccw == (not clockwise_start)
    assert all((e.bulge > 0) == ccw for e in arc_edges)          # outward bulge for either direction
    assert abs(signed_area(loop_points(loop, 0.001))) == pytest.approx(100 * 60 - (4 - math.pi) * 100, abs=0.5)
    assert loop_bounds(loop) == pytest.approx((0, 0, 100, 60))


def test_messages_use_the_users_units():
    from nestlib.units import INCH
    prims = square()
    prims[1] = LineSeg((10.5, 0.0), (10.0, 10.0))
    msg = next(i.message for i in join_primitives(prims, TOL, INCH).issues if i.code == "open_contour")
    assert "(0.3937, 0.0000) in" in msg and "0.0197 in" in msg and " mm" not in msg
    res = join_primitives(square() + square(10, 10), TOL, INCH)
    assert "(0.3937, 0.3937) in" in next(i.message for i in res.issues if i.code == "branching_contour")
