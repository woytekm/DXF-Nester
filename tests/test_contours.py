import math
from pathlib import Path

import ezdxf
import pytest

from nestlib.contours import PartBuildError, build_part
from nestlib.geometry import loop_points, signed_area
from nestlib.loader import load_dxf
from nestlib.models import ArcSeg, CircleLoop, CircleSeg, Issue, LineSeg, PolyLoop, RawPart, Severity

DATA = Path(__file__).parent / "data"


def rect(x0, y0, x1, y1, ccw=True):
    c = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if not ccw:
        c = c[::-1]
    return [LineSeg(c[i], c[(i + 1) % 4]) for i in range(4)]


def raw(prims, name="p", issues=()):
    return RawPart(name, Path(f"{name}.dxf"), tuple(prims), "millimetres", tuple(issues))


def err_codes(exc: PartBuildError):
    return [i.code for i in exc.value.issues if i.severity is Severity.ERROR]


# ------------------------------------------------------------ the real file
def test_real_file_polka_typ_3():
    part = build_part(load_dxf(DATA / "polka_typ_3.dxf"))
    assert part.name == "polka_typ_3"
    assert part.width == pytest.approx(2500.0) and part.height == pytest.approx(300.0)
    assert part.source_offset == pytest.approx((1900.0, 0.0))
    assert isinstance(part.outer, PolyLoop) and len(part.outer.edges) == 20 and part.holes == ()
    assert part.polygon.bounds == pytest.approx((0, 0, 2500, 300))
    assert part.polygon.is_valid
    assert part.area == pytest.approx(739986.407, abs=0.001)
    assert part.cut_length == pytest.approx(6806.4, abs=0.001)      # 13612.8 if the duplicate were kept
    dup = [i for i in part.issues if i.code == "duplicate_geometry"]
    assert len(dup) == 1 and dup[0].message.startswith("20 duplicate")
    assert all(i.part == "polka_typ_3" for i in part.issues)
    assert signed_area(loop_points(part.outer, 0.01)) > 0            # counter-clockwise
    # slot at source x 2513.5..2530.1 (now 613.5..630.1), open from the top edge down to y=149.2
    from shapely.geometry import Point
    assert not part.polygon.contains(Point(620, 200))   # inside the slot
    assert part.polygon.contains(Point(620, 100))       # below the slot bottom
    assert part.polygon.contains(Point(300, 200))       # solid body


# ------------------------------------------------------------ outer / holes
def test_plate_moved_to_origin_and_oriented():
    for ccw in (True, False):
        part = build_part(raw(rect(100, 50, 300, 150, ccw=ccw)))
        assert (part.width, part.height) == (200.0, 100.0)
        assert part.source_offset == (100.0, 50.0)
        assert part.polygon.bounds == pytest.approx((0, 0, 200, 100))
        assert signed_area(loop_points(part.outer, 0.01)) > 0
        assert part.area == pytest.approx(20000.0) and part.cut_length == pytest.approx(600.0)


def test_holes_classified_and_oriented_clockwise():
    prims = rect(0, 0, 200, 100) + [CircleSeg((50, 50), 10)] + rect(120, 40, 140, 60, ccw=True)
    part = build_part(raw(prims))
    assert len(part.holes) == 2
    kinds = sorted(type(h).__name__ for h in part.holes)
    assert kinds == ["CircleLoop", "PolyLoop"]
    rect_hole = next(h for h in part.holes if isinstance(h, PolyLoop))
    assert signed_area(loop_points(rect_hole, 0.01)) < 0          # clockwise
    assert len(part.polygon.interiors) == 2
    assert part.area == pytest.approx(20000 - math.pi * 100 - 400, abs=1.0)
    assert part.cut_length == pytest.approx(600 + 2 * math.pi * 10 + 80)


def test_holes_move_with_the_part():
    part = build_part(raw(rect(1000, 500, 1200, 600) + [CircleSeg((1050, 550), 10)]))
    assert part.holes[0] == CircleLoop((50.0, 50.0), 10.0)


def test_circular_part_with_hole():
    part = build_part(raw([CircleSeg((500, 500), 50), CircleSeg((500, 500), 10)]))
    assert isinstance(part.outer, CircleLoop) and part.outer.center == (50.0, 50.0)
    assert (part.width, part.height) == (100.0, 100.0) and len(part.holes) == 1


def test_exact_bounds_for_arcs_not_flattened_bounds():
    r, w, h = 10.0, 100.0, 60.0
    prims = [LineSeg((r, 0), (w - r, 0)), LineSeg((w, r), (w, h - r)),
             LineSeg((w - r, h), (r, h)), LineSeg((0, h - r), (0, r)),
             ArcSeg((w - r, r), r, 270, 0), ArcSeg((w - r, h - r), r, 0, 90),
             ArcSeg((r, h - r), r, 90, 180), ArcSeg((r, r), r, 180, 270)]
    part = build_part(raw(prims))
    assert (part.width, part.height) == (pytest.approx(100.0), pytest.approx(60.0))
    assert part.area == pytest.approx(w * h - (4 - math.pi) * r * r, abs=0.5)


# ------------------------------------------------------------------- errors
def test_two_separate_contours_is_an_error():
    with pytest.raises(PartBuildError) as ei:
        build_part(raw(rect(0, 0, 100, 100) + rect(300, 0, 350, 40), name="two"))
    assert err_codes(ei) == ["multiple_contours"]
    assert "300.0, 0.0" in str(ei.value) and str(ei.value).startswith("two:")


def test_crossing_contours_error():
    with pytest.raises(PartBuildError) as ei:
        build_part(raw(rect(0, 0, 100, 100) + rect(80, 80, 180, 180)))
    assert err_codes(ei) == ["overlapping_contours"]


def test_island_inside_hole_is_an_error():
    prims = rect(0, 0, 300, 300) + rect(50, 50, 250, 250) + rect(100, 100, 150, 150)
    with pytest.raises(PartBuildError) as ei:
        build_part(raw(prims))
    assert "nested_contour" in err_codes(ei)


def test_overlapping_holes_error():
    prims = rect(0, 0, 300, 100) + [CircleSeg((100, 50), 20), CircleSeg((130, 50), 20)]
    with pytest.raises(PartBuildError) as ei:
        build_part(raw(prims))
    assert "overlapping_contours" in err_codes(ei)


def test_open_contour_error_is_propagated_with_part_name():
    prims = rect(0, 0, 100, 100)[:3]
    with pytest.raises(PartBuildError) as ei:
        build_part(raw(prims, name="open1"))
    assert err_codes(ei) == ["open_contour"]
    assert all(i.part == "open1" for i in ei.value.issues)


def test_self_intersecting_contour_error():
    bow = [LineSeg((0, 0), (10, 10)), LineSeg((10, 10), (10, 0)),
           LineSeg((10, 0), (0, 10)), LineSeg((0, 10), (0, 0))]
    with pytest.raises(PartBuildError) as ei:
        build_part(raw(bow))
    assert err_codes(ei) == ["invalid_contour"]


def test_no_geometry_error():
    with pytest.raises(PartBuildError) as ei:
        build_part(raw([]))
    assert err_codes(ei) == ["no_contours"]


def test_loader_issues_are_carried_into_part():
    note = Issue(Severity.INFO, "ignored_entity", "1 POINT entity ignored")
    part = build_part(raw(rect(0, 0, 10, 10), name="x", issues=[note]))
    assert note in part.issues


# ------------------------------------------------- loader -> contours, e2e
def test_end_to_end_bulge_rounded_rectangle(tmp_path):
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    b = math.tan(math.radians(90) / 4)
    r, w, h = 8.0, 120.0, 70.0
    # counter-clockwise rounded rectangle as one closed LWPOLYLINE, offset by (500, 300)
    pts = [(r, 0, 0, 0, 0), (w - r, 0, 0, 0, b), (w, r, 0, 0, 0), (w, h - r, 0, 0, b),
           (w - r, h, 0, 0, 0), (r, h, 0, 0, b), (0, h - r, 0, 0, 0), (0, r, 0, 0, b)]
    pts = [(x + 500, y + 300, s, e, bb) for x, y, s, e, bb in pts]
    msp = doc.modelspace()
    msp.add_lwpolyline(pts, format="xyseb", close=True)
    msp.add_circle((500 + 60, 300 + 35), 12)
    doc.saveas(tmp_path / "rr.dxf")
    part = build_part(load_dxf(tmp_path / "rr.dxf"))
    assert (part.width, part.height) == (pytest.approx(w), pytest.approx(h))
    assert part.source_offset == pytest.approx((500, 300))
    assert part.holes == (CircleLoop((60.0, 35.0), 12.0),)
    assert part.area == pytest.approx(w * h - (4 - math.pi) * r * r - math.pi * 144, abs=1.0)


def test_error_messages_use_the_users_units():
    from nestlib.units import INCH
    with pytest.raises(PartBuildError) as ei:
        build_part(raw(rect(0, 0, 100, 100) + rect(254, 0, 304.8, 25.4)), units=INCH)
    msg = str(ei.value)
    assert "(10.00, 0.00) in" in msg and "size 2.00 x 1.00 in" in msg and " mm" not in msg
