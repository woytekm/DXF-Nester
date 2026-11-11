import math

from nestlib.models import ArcSeg, SheetSpec


def test_arc_endpoints_and_sweep():
    a = ArcSeg((10.0, 0.0), 5.0, 0.0, 90.0)
    assert a.start_point == (15.0, 0.0)
    assert math.isclose(a.end_point[0], 10.0, abs_tol=1e-12) and math.isclose(a.end_point[1], 5.0)
    assert a.sweep == 90.0


def test_arc_sweep_wraps_through_zero():
    assert ArcSeg((0, 0), 1, 270.0, 90.0).sweep == 180.0
    assert ArcSeg((0, 0), 1, 90.0, 90.0).sweep == 360.0  # degenerate: treated as full


def test_sheetspec_usable_area():
    s = SheetSpec(100.0, 50.0, 5.0)
    assert (s.usable_width, s.usable_height) == (90.0, 40.0)
