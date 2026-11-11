import math
from pathlib import Path

import pytest

from nestlib.checks import check_parts
from nestlib.config import load_config
from nestlib.contours import build_part
from nestlib.loader import load_dxf
from nestlib.models import CircleSeg, Grain, LineSeg, PartRequest, RawPart, Severity

DATA = Path(__file__).parent / "data"
_cache = {}


def corpus(name):
    if name not in _cache:
        _cache[name] = build_part(load_dxf(DATA / f"{name}.dxf"))
    return _cache[name]


def cfg(**kw):
    kw.setdefault("sheet", "2500x1250")
    return load_config(overrides=kw)


def pair(name, qty=1, grain=Grain.ANY):
    p = corpus(name)
    return PartRequest(p.path, qty, grain), p


def codes(res, sev=None):
    return [i.code for i in res.issues if sev is None or i.severity is sev]


def item(res, name):
    return next(i for i in res.items if i.part.name == name)


# ------------------------------------------------------------ the standard case
def test_all_four_parts_fit_the_standard_sheet_with_zero_margin():
    pairs = [pair("polka_typ_3", 2), pair("przegroda_typ_3", 1), pair("szuflada_front", 4), pair("szuflada_bok", 8)]
    res = check_parts(pairs, cfg())
    assert res.ok and len(res.items) == 4
    assert item(res, "polka_typ_3").rotations == (0.0, 180.0)           # too long to turn
    assert item(res, "przegroda_typ_3").rotations == (0.0, 180.0)
    assert item(res, "szuflada_front").rotations == (0.0, 90.0, 180.0, 270.0)
    assert [i.part.name for i in res.items] == ["polka_typ_3", "przegroda_typ_3", "szuflada_front", "szuflada_bok"]


def test_restriction_is_reported_as_info_with_reason():
    res = check_parts([pair("polka_typ_3")], cfg())
    msg = next(i for i in res.issues if i.code == "rotation_restricted")
    assert msg.severity is Severity.INFO and msg.part == "polka_typ_3"
    assert "rotation 0, 180" in msg.message and "does not fit the sheet in the other rotations" in msg.message
    assert "rotation_restricted" not in codes(check_parts([pair("szuflada_front")], cfg()))


def test_min_sheets_estimate():
    pairs = [pair("polka_typ_3", 3), pair("szuflada_bok", 10)]
    res = check_parts(pairs, cfg())
    total = 3 * corpus("polka_typ_3").area + 10 * corpus("szuflada_bok").area
    n = math.ceil(total / (2500 * 1250))
    assert f"at least {n} sheet(s)" in next(i.message for i in res.issues if i.code == "min_sheets")


# -------------------------------------------------------------------- margin
def test_margin_makes_the_full_length_shelf_impossible_with_a_helpful_hint():
    res = check_parts([pair("polka_typ_3"), pair("przegroda_typ_3")], cfg(margin=10))
    assert not res.ok
    errs = [i for i in res.issues if i.severity is Severity.ERROR]
    assert [(e.code, e.part) for e in errs] == [("part_too_large", "polka_typ_3")]
    m = errs[0].message
    assert "2500.000 x 300.000 mm" in m and "2480.000 x 1230.000 mm" in m and "margin 10.000 mm" in m
    assert "it would fit without the margin" in m
    assert [i.part.name for i in res.items] == ["przegroda_typ_3"]       # the fitting part is still prepared


def test_no_margin_hint_when_it_would_not_help():
    res = check_parts([pair("polka_typ_3")], cfg(sheet="2000x1250"))
    m = next(i.message for i in res.issues if i.code == "part_too_large")
    assert "without the margin" not in m


def test_hint_when_rotation_would_help():
    # 1300 x 1200 part on a 1250 x 2500 sheet: only fits turned by 90
    c = [(0, 0), (1300, 0), (1300, 1200), (0, 1200)]
    part = build_part(RawPart("wide", Path("wide.dxf"), tuple(LineSeg(c[i], c[(i + 1) % 4]) for i in range(4)), "mm"))
    res = check_parts([(PartRequest(part.path, 1), part)], cfg(sheet="1250x2500", rotations="0"))
    assert "it would fit if 90-degree rotations were allowed" in next(i.message for i in res.issues if i.code == "part_too_large")
    res = check_parts([(PartRequest(part.path, 1), part)], cfg(sheet="1250x2500"))
    assert res.ok and item(res, "wide").rotations == (90.0, 270.0)


# --------------------------------------------------------------------- grain
def test_grain_rule_selects_rotations():
    res = check_parts([pair("szuflada_front", 1, Grain.ACROSS), pair("szuflada_bok", 1, Grain.ALONG)], cfg())
    assert res.ok
    assert item(res, "szuflada_front").rotations == (90.0, 270.0)
    assert item(res, "szuflada_bok").rotations == (90.0, 270.0)
    assert item(res, "szuflada_front").grain is Grain.ACROSS
    assert "grain rule 'across'" in next(i.message for i in res.issues if i.code == "rotation_restricted" and i.part == "szuflada_front")


def test_grain_axis_y_flips_the_meaning():
    res = check_parts([pair("szuflada_front", 1, Grain.ACROSS)], cfg(grain="y"))
    assert item(res, "szuflada_front").rotations == (0.0, 180.0)


def test_unsatisfiable_grain_rule_is_an_error_naming_the_alternative():
    res = check_parts([pair("polka_typ_3", 1, Grain.ACROSS)], cfg())
    assert not res.ok and res.items == ()
    m = next(i.message for i in res.issues if i.code == "grain_unsatisfiable")
    assert "'across'" in m and "does not fit" in m and "only be placed with its longer side along the grain" in m


def test_satisfiable_grain_rule_for_the_long_part():
    assert check_parts([pair("polka_typ_3", 1, Grain.ALONG)], cfg()).ok


def test_grain_rule_without_right_angle_rotations():
    res = check_parts([pair("szuflada_front", 1, Grain.ALONG)], cfg(rotations="45"))
    m = next(i.message for i in res.issues if i.code == "grain_unsatisfiable")
    assert "multiples of 90 degrees" in m


def test_grain_any_ignores_rule_machinery():
    res = check_parts([pair("szuflada_front", 1, Grain.ANY)], cfg(rotations="0,45,90"))
    assert item(res, "szuflada_front").rotations == (0.0, 45.0, 90.0)


# --------------------------------------------------------------- misc / units
def test_duplicate_part_names():
    a, b = pair("szuflada_bok"), pair("szuflada_bok")
    res = check_parts([a, b], cfg())
    assert "duplicate_part_name" in codes(res, Severity.ERROR)


def test_messages_in_inches():
    # sheet, margin given in inches: 98.4252 x 49.2126 in = 2500 x 1250 mm, margin 0.4 in = 10.16 mm
    c = load_config(overrides={"units": "in", "sheet": "98.4252x49.2126", "margin": 0.4})
    res = check_parts([pair("polka_typ_3")], c)
    m = next(i.message for i in res.issues if i.code == "part_too_large")
    assert "98.4252 x 11.8110 in" in m and "margin 0.4000 in" in m and " mm" not in m
    assert "97.6252 x 48.4126 in" in m                      # usable area, also in inches


def test_inches_and_mm_give_identical_decisions():
    mm = check_parts([pair("szuflada_front", 1, Grain.ACROSS)], cfg())
    inch = check_parts([pair("szuflada_front", 1, Grain.ACROSS)],
                       load_config(overrides={"units": "in", "sheet": "98.4251969x49.2125984"}))
    assert item(mm, "szuflada_front").rotations == item(inch, "szuflada_front").rotations == (90.0, 270.0)


def test_empty_input():
    res = check_parts([], cfg())
    assert res.ok and res.items == () and res.issues == ()


# ----------------------------------------------------------- tool diameter
ALL = ["polka_typ_3", "przegroda_typ_3", "szuflada_front", "szuflada_bok"]


def all_pairs():
    return [pair(n) for n in ALL]


def test_no_tool_diameter_means_no_tool_checks():
    res = check_parts(all_pairs(), cfg())
    assert not {"inner_corner_rounded", "feature_too_narrow", "hole_smaller_than_tool", "spacing_below_tool"} & set(codes(res))


def test_8mm_bit_warns_about_slot_bottoms_only():
    res = check_parts(all_pairs(), cfg(tool_diameter=8))
    assert res.ok and len(res.items) == 4
    warn = {i.part: i for i in res.issues if i.code == "inner_corner_rounded"}
    assert set(warn) == {"polka_typ_3", "przegroda_typ_3"}                  # slotted parts only
    assert warn["polka_typ_3"].severity is Severity.WARNING
    assert warn["polka_typ_3"].message.startswith("8 inner corner(s) tighter than the tool radius 4.000 mm")
    assert warn["przegroda_typ_3"].message.startswith("10 inner corner(s)")
    assert "will not seat fully" in warn["polka_typ_3"].message


def test_20mm_bit_cannot_cut_the_16_6_mm_slots():
    res = check_parts(all_pairs(), cfg(tool_diameter=20, fit_spacing=20))
    assert not res.ok
    errs = {i.part: i for i in res.issues if i.code == "feature_too_narrow"}
    assert set(errs) == {"polka_typ_3", "przegroda_typ_3"}
    assert errs["polka_typ_3"].message.startswith("4 region(s) the 20.000 mm tool cannot reach")
    assert errs["przegroda_typ_3"].message.startswith("5 region(s)")
    assert [i.part.name for i in res.items] == ["szuflada_front", "szuflada_bok"]     # unusable parts are not handed on


def test_bit_just_under_the_slot_width_is_fine():
    assert check_parts([pair("polka_typ_3")], cfg(tool_diameter=16.6, fit_spacing=20)).ok


def test_spacing_smaller_than_tool_is_an_error():
    res = check_parts(all_pairs(), cfg(tool_diameter=8, fit_spacing=5))
    err = next(i for i in res.issues if i.code == "spacing_below_tool")
    assert err.severity is Severity.ERROR and err.part is None
    assert "fit spacing 5.000 mm is smaller than the tool diameter 8.000 mm" in err.message


def test_spacing_equal_to_tool_is_fine_and_default_follows_tool():
    assert "spacing_below_tool" not in codes(check_parts(all_pairs(), cfg(tool_diameter=8, fit_spacing=8)))
    assert "spacing_below_tool" not in codes(check_parts(all_pairs(), cfg(tool_diameter=12)))    # default became 12


def test_small_hole_is_an_error():
    c = [(0, 0), (100, 0), (100, 100), (0, 100)]
    prims = tuple(LineSeg(c[i], c[(i + 1) % 4]) for i in range(4)) + (CircleSeg((50, 50), 3),)
    part = build_part(RawPart("drilled", Path("drilled.dxf"), prims, "mm"))
    res = check_parts([(PartRequest(part.path, 1), part)], cfg(tool_diameter=8))
    err = next(i for i in res.issues if i.code == "hole_smaller_than_tool")
    assert err.severity is Severity.ERROR and "1 hole(s) the 8.000 mm tool does not fit into" in err.message
    assert "6.0 x 6.0 mm" in err.message and res.items == ()


def test_tool_messages_in_inches():
    c = load_config(overrides={"units": "in", "sheet": "98.4252x49.2126", "tool_diameter": 0.3})  # 0.3 in = 7.62 mm
    res = check_parts([pair("polka_typ_3")], c)
    m = next(i.message for i in res.issues if i.code == "inner_corner_rounded")
    assert "tool radius 0.1500 in" in m and " mm" not in m and "8 inner corner(s)" in m


def test_tool_and_grain_problems_are_both_reported():
    res = check_parts([pair("polka_typ_3", 1, Grain.ACROSS)], cfg(tool_diameter=20, fit_spacing=20))
    assert {"feature_too_narrow", "grain_unsatisfiable"} <= set(codes(res, Severity.ERROR))
