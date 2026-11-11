from pathlib import Path

import pytest

from nestlib.contours import build_part
from nestlib.grain import allowed_rotations, describe, long_axis, quarter_turns, relation, rotated_size
from nestlib.loader import load_dxf
from nestlib.models import ArcSeg, Grain, LineSeg, RawPart

DATA = Path(__file__).parent / "data"
ALL4 = (0.0, 90.0, 180.0, 270.0)
SHEET = (2500.0, 1250.0)     # usable area for the standard sheet with margin 0


def rect_part(w, h, name="r"):
    c = [(0, 0), (w, 0), (w, h), (0, h)]
    prims = tuple(LineSeg(c[i], c[(i + 1) % 4]) for i in range(4))
    return build_part(RawPart(name, Path(f"{name}.dxf"), prims, "millimetres"))


def corpus(name):
    return build_part(load_dxf(DATA / f"{name}.dxf"))


def test_long_axis():
    assert long_axis(2500, 300) == "x" and long_axis(200, 272) == "y"
    assert long_axis(100, 100) == "x" and long_axis(100, 100.0000005) == "x"   # tie -> x


@pytest.mark.parametrize("rot, q", [(0, 0), (90, 1), (180, 2), (270, 3), (360, 0), (450, 1), (-90, 3), (45, None), (90.0000001, None)])
def test_quarter_turns(rot, q):
    assert quarter_turns(rot) == q


def test_relation_landscape_part():
    p = rect_part(2500, 300)
    assert [relation(p, r, "x") for r in (0, 90, 180, 270)] == [Grain.ALONG, Grain.ACROSS, Grain.ALONG, Grain.ACROSS]
    assert [relation(p, r, "y") for r in (0, 90, 180, 270)] == [Grain.ACROSS, Grain.ALONG, Grain.ACROSS, Grain.ALONG]
    assert relation(p, 45, "x") is None and relation(p, 450, "x") is Grain.ACROSS


def test_relation_portrait_part_uses_its_longer_drawn_side():
    p = rect_part(200, 272)           # drawn upright: longer side is Y
    assert relation(p, 0, "x") is Grain.ACROSS and relation(p, 90, "x") is Grain.ALONG


def test_describe():
    assert describe(Grain.ALONG) == "longer side along grain"
    assert describe(Grain.ACROSS) == "longer side across grain"
    assert "no grain relation" in describe(None)


def test_rotated_size():
    assert rotated_size(rect_part(2500, 300), 90) == pytest.approx((300, 2500))
    w, h = rotated_size(rect_part(100, 50), 45)
    assert w == pytest.approx(150 / 2 ** 0.5 * 1.0, rel=1e-9) and h == pytest.approx(w)


# --------------------------------------------------------- allowed_rotations
def allowed(part, rule=Grain.ANY, rotations=ALL4, grain="x", usable=SHEET, slack=0.01):
    return allowed_rotations(part, rule, rotations, grain, usable[0], usable[1], slack)


def test_sheet_length_part_cannot_rotate():
    polka = corpus("polka_typ_3")                    # 2500 x 300 on 2500 x 1250
    assert allowed(polka) == (0.0, 180.0)            # 90/270 would need 2500 of the 1250 height
    assert allowed(polka, Grain.ALONG) == (0.0, 180.0)
    assert allowed(polka, Grain.ACROSS) == ()


def test_long_part_with_margin_does_not_fit_at_all():
    assert allowed(corpus("polka_typ_3"), usable=(2480.0, 1230.0)) == ()


def test_free_rotation_when_everything_fits():
    front = corpus("szuflada_front")                 # 610 x 331
    assert allowed(front) == ALL4
    assert allowed(front, Grain.ALONG) == (0.0, 180.0)
    assert allowed(front, Grain.ACROSS) == (90.0, 270.0)


def test_portrait_part_rules():
    bok = corpus("szuflada_bok")                     # 200 x 272, longer side drawn along Y
    assert allowed(bok, Grain.ALONG) == (90.0, 270.0)
    assert allowed(bok, Grain.ACROSS) == (0.0, 180.0)
    assert allowed(bok, Grain.ALONG, grain="y") == (0.0, 180.0)


def test_grain_rule_and_fit_combine():
    # 1300 x 300: only fits unrotated on 2500 x 1250 ... and 90 would need 1300 > 1250
    p = rect_part(1300, 300)
    assert allowed(p) == (0.0, 180.0)
    assert allowed(p, Grain.ACROSS) == ()
    q = rect_part(1200, 300)                          # fits both ways
    assert allowed(q, Grain.ACROSS) == (90.0, 270.0)


def test_non_right_angle_rotations_only_without_rule():
    p = rect_part(100, 50)
    rots = (0.0, 45.0, 90.0)
    assert allowed(p, rotations=rots) == (0.0, 45.0, 90.0)
    assert allowed(p, Grain.ALONG, rotations=rots) == (0.0,)
    assert allowed(p, Grain.ACROSS, rotations=rots) == (90.0,)


def test_fit_slack():
    assert allowed(rect_part(2500.004, 300), usable=SHEET, slack=0.01) == (0.0, 180.0)
    assert allowed(rect_part(2500.5, 300), usable=SHEET, slack=0.01) == ()


def test_order_of_rotations_is_kept():
    assert allowed(rect_part(100, 50), rotations=(270.0, 0.0)) == (270.0, 0.0)
