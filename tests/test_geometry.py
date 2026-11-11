import math

import pytest

from nestlib.geometry import (arc_from_bulge, edge_length, edge_points, loop_bounds, loop_length,
                              loop_points, reverse_loop, signed_area, transform_loop, transform_point)
from nestlib.models import CircleLoop, Edge, PolyLoop

B90 = math.tan(math.radians(90) / 4)  # bulge of a quarter circle


def close(a, b, tol=1e-9):
    return math.dist(a, b) < tol


def semicircle_below(b=1.0):
    """(0,0) -> (2,0) with bulge b, closed by a straight line back."""
    return PolyLoop((Edge((0.0, 0.0), (2.0, 0.0), b), Edge((2.0, 0.0), (0.0, 0.0), 0.0)))


def test_arc_from_bulge_semicircles():
    c, r, a0, sweep = arc_from_bulge((0, 0), (2, 0), 1.0)
    assert close(c, (1, 0)) and math.isclose(r, 1) and math.isclose(a0, 180) and math.isclose(sweep, 180)
    c, r, a0, sweep = arc_from_bulge((0, 0), (2, 0), -1.0)
    assert close(c, (1, 0)) and math.isclose(sweep, -180)


@pytest.mark.parametrize("bulge", [0.2, -0.2, B90, -B90, 1.0, 2.5, -2.5])
def test_arc_from_bulge_is_consistent(bulge):
    p, q = (3.0, 4.0), (-1.0, 7.0)
    c, r, a0, sweep = arc_from_bulge(p, q, bulge)
    assert math.isclose(math.dist(c, p), r) and math.isclose(math.dist(c, q), r)
    a1 = math.radians(a0 + sweep)
    assert close((c[0] + r * math.cos(a1), c[1] + r * math.sin(a1)), q, 1e-7)
    assert math.isclose(sweep, math.degrees(4 * math.atan(bulge)))


def test_edge_length():
    assert math.isclose(edge_length(Edge((0, 0), (3, 4))), 5.0)
    assert math.isclose(edge_length(Edge((0, 0), (2, 0), 1.0)), math.pi)           # semicircle r=1
    assert math.isclose(edge_length(Edge((10, 0), (0, 10), -B90)), math.pi * 10 / 2)  # quarter, r=10


@pytest.mark.parametrize("tol", [0.5, 0.05, 0.01])
def test_flattening_respects_tolerance(tol):
    e = Edge((0.0, 0.0), (200.0, 0.0), 1.0)  # semicircle r=100
    pts = edge_points(e, tol) + [e.end]
    c = (100.0, 0.0)
    for p in pts:
        assert math.isclose(math.dist(p, c), 100.0, rel_tol=1e-9)  # vertices lie on the arc
    for p, q in zip(pts, pts[1:]):  # chord midpoints deviate <= tol (small slack)
        mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        assert 100.0 - math.dist(mid, c) <= tol * 1.001


def test_loop_points_circle_and_polygon_area():
    pts = loop_points(CircleLoop((5.0, 5.0), 50.0), 0.01)
    assert len(pts) >= 16 and all(math.isclose(math.dist(p, (5, 5)), 50.0) for p in pts)
    assert abs(signed_area(pts) - math.pi * 2500) < 0.01 * 2 * math.pi * 50 + 1e-6


def test_signed_area_orientation():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert signed_area(sq) == 1.0 and signed_area(sq[::-1]) == -1.0


def test_loop_length():
    assert math.isclose(loop_length(CircleLoop((0, 0), 2.0)), 4 * math.pi)
    assert math.isclose(loop_length(semicircle_below()), math.pi + 2.0)


def test_loop_bounds_include_arc_extremes():
    # ccw semicircle (0,0)->(2,0) bulges below the chord: min y = -1
    assert loop_bounds(semicircle_below(1.0)) == pytest.approx((0, -1, 2, 0))
    # clockwise semicircle bulges above
    assert loop_bounds(semicircle_below(-1.0)) == pytest.approx((0, 0, 2, 1))
    assert loop_bounds(CircleLoop((10, 20), 5)) == (5, 15, 15, 25)


def test_loop_bounds_quarter_arc_does_not_overshoot():
    # quarter arc centre (0,0) r=10 from (10,0) to (0,10): bounds are the chord ends, no extra extremes
    loop = PolyLoop((Edge((10.0, 0.0), (0.0, 10.0), B90), Edge((0.0, 10.0), (10.0, 0.0), 0.0)))
    assert loop_bounds(loop) == pytest.approx((0, 0, 10, 10))


def test_transform_point_rotate_then_translate():
    assert close(transform_point((1, 0), 90, 5, 6), (5, 7))


def test_transform_loop_preserves_shape():
    loop = semicircle_below(1.0)
    moved = transform_loop(loop, 90.0, 10.0, 20.0)
    assert math.isclose(loop_length(moved), loop_length(loop))
    assert math.isclose(abs(signed_area(loop_points(moved, 0.001))), abs(signed_area(loop_points(loop, 0.001))), rel_tol=1e-6)
    # semicircle below the chord, rotated 90 degrees ccw: bulges toward +x
    x0, y0, x1, y1 = loop_bounds(moved)
    assert (x0, x1) == pytest.approx((10, 11)) and (y0, y1) == pytest.approx((20, 22))
    c = transform_loop(CircleLoop((1, 0), 2), 180, 0, 0)
    assert close(c.center, (-1, 0)) and c.radius == 2


def test_reverse_loop():
    loop = semicircle_below(0.7)
    rev = reverse_loop(loop)
    assert signed_area(loop_points(rev, 0.001)) == pytest.approx(-signed_area(loop_points(loop, 0.001)))
    assert math.isclose(loop_length(rev), loop_length(loop))
    assert reverse_loop(rev) == loop
    for a, b in zip(rev.edges, rev.edges[1:] + rev.edges[:1]):
        assert close(a.end, b.start)
    c = CircleLoop((0, 0), 1)
    assert reverse_loop(c) is c
