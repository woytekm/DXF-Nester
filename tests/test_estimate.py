import math

import pytest
from helpers import cfg, item, nested_sample, rect_item, rect_part

from nestlib.caption import sheet_caption
from nestlib.estimate import (cut_summary, describe_assumptions, estimate_time, format_duration, nearest_neighbour_length,
                              plan_passes, contour_starts)
from nestlib.config import load_config
from nestlib.models import ArcSeg, CircleSeg, Layout, LineSeg, Placement, RawPart
from nestlib.contours import build_part
from nestlib.nest_rect import nest_rectangles
from pathlib import Path


def one_sheet(parts_and_positions, c):
    pls = tuple(Placement(p.name, i + 1, 0.0, x, y, p.width, p.height) for i, (p, x, y) in enumerate(parts_and_positions))
    return Layout(1, c.sheet, pls)


def plate_with_hole():
    prims = [LineSeg((0, 0), (100, 0)), LineSeg((100, 0), (100, 60)), LineSeg((100, 60), (0, 60)), LineSeg((0, 60), (0, 0)),
             CircleSeg((50, 30), 10), CircleSeg((20, 30), 5)]
    return build_part(RawPart("plate", Path("plate.dxf"), tuple(prims), "mm"))


# ------------------------------------------------------------------ lengths
def test_cut_summary_of_one_rectangle():
    part = rect_part(1000, 100, "r")
    c = cfg("2500x1250")
    cs = cut_summary([one_sheet([(part, 0, 0)], c)], {"r": part})
    assert cs.length == pytest.approx(2200.0) and cs.outer_length == pytest.approx(2200.0) and cs.inner_length == 0
    assert cs.pierces == 1 and cs.pieces == 1


def test_holes_count_as_inner_length_and_extra_pierces():
    part = plate_with_hole()
    c = cfg("2500x1250")
    cs = cut_summary([one_sheet([(part, 0, 0), (part, 200, 0)], c)], {"plate": part})
    assert cs.outer_length == pytest.approx(2 * 320.0)
    assert cs.inner_length == pytest.approx(2 * (2 * math.pi * 10 + 2 * math.pi * 5))
    assert cs.pierces == 2 * 3 and cs.pieces == 2
    assert cs.length == pytest.approx(cs.outer_length + cs.inner_length)


def test_cut_summary_matches_the_real_parts(tmp_path):
    c, res, parts = nested_sample(tmp_path)[:3]
    cs = cut_summary(res.layouts, parts)
    want = 2 * 6806.4 + 6248.0 + 4 * 1950.412 + 8 * 944.0
    assert cs.length == pytest.approx(want, abs=0.05) and cs.pieces == 15 and cs.pierces == 15
    assert [s.index for s in cs.sheets] == [1, 2] and sum(s.length for s in cs.sheets) == pytest.approx(cs.length)
    assert cs.sheets[1].pieces == 3 and cs.sheets[1].length == pytest.approx(3 * 1950.412, abs=0.05)   # three fronts


def test_rotation_does_not_change_lengths():
    part = rect_part(500, 80, "r")
    c = cfg("1000x1000")
    a = cut_summary([Layout(1, c.sheet, (Placement("r", 1, 0.0, 0, 0, 500, 80),))], {"r": part})
    b = cut_summary([Layout(1, c.sheet, (Placement("r", 1, 90.0, 0, 0, 80, 500),))], {"r": part})
    assert a.length == b.length


# ------------------------------------------------------------------- passes
@pytest.mark.parametrize("kw, expect", [
    ({}, (1, None)),                                              # nothing known: one pass
    ({"thickness": 18}, (1, 18.0)),                               # no tool, no pass depth: full depth in one pass
    ({"thickness": 18, "tool_diameter": 8}, (3, 6.0)),            # default depth = tool diameter
    ({"thickness": 18, "tool_diameter": 8, "pass_depth": 5}, (4, 4.5)),
    ({"thickness": 18, "pass_depth": 20}, (1, 18.0)),             # deeper than the sheet: one pass
    ({"thickness": 18, "pass_depth": 6}, (3, 6.0)),               # exact division, no extra pass
    ({"thickness": 18.001, "pass_depth": 5.9995}, (3, 18.001 / 3)),   # rounding noise (inch input) costs no extra pass
    ({"thickness": 18, "pass_depth": 5.9}, (4, 4.5)),             # a real excess does
    ({"tool_diameter": 8}, (1, None)),                            # tool without thickness: depth unknown
])
def test_plan_passes(kw, expect):
    assert plan_passes(cfg("100x100", **kw)) == expect


# --------------------------------------------------------------------- time
def test_no_feed_rate_no_estimate():
    part = rect_part(100, 100, "r")
    c = cfg("500x500")
    assert estimate_time([one_sheet([(part, 0, 0)], c)], {"r": part}, c) is None


def test_time_model_by_hand():
    part = rect_part(1000, 100, "r")                     # contour 2200 mm
    c = cfg("2500x1250", feed_rate=6000, thickness=18, pass_depth=9)
    est = estimate_time([one_sheet([(part, 0, 0)], c)], {"r": part}, c)
    assert est.passes == 2 and est.depth_per_pass == 9.0
    assert est.cutting_s == pytest.approx(2200 * 2 / 6000 * 60)                       # 44 s
    assert est.plunge_s == pytest.approx(1 * 2 * 9 / 3000 * 60)                       # 0.36 s at half the feed
    assert est.rapid_s == pytest.approx(0.0)                                          # a single contour: nothing to travel
    assert est.total_s == pytest.approx(44.36) and est.per_sheet_s == pytest.approx((44.36,))


def test_time_without_thickness_is_one_pass_and_no_plunge():
    part = rect_part(1000, 100, "r")
    c = cfg("2500x1250", feed_rate=6000)
    est = estimate_time([one_sheet([(part, 0, 0)], c)], {"r": part}, c)
    assert est.passes == 1 and est.depth_per_pass is None and est.plunge_s == 0 and est.cutting_s == pytest.approx(22.0)
    assert "no --thickness given" in describe_assumptions(est, c)


def test_more_passes_scale_the_cutting_time_linearly():
    part = rect_part(1000, 100, "r")
    lay = lambda c: [one_sheet([(part, 0, 0)], c)]
    t1 = estimate_time(lay(cfg("2500x1250", feed_rate=6000, thickness=18, pass_depth=18)), {"r": part}, cfg("2500x1250", feed_rate=6000, thickness=18, pass_depth=18))
    c3 = cfg("2500x1250", feed_rate=6000, thickness=18, pass_depth=6)
    t3 = estimate_time(lay(c3), {"r": part}, c3)
    assert t3.cutting_s == pytest.approx(3 * t1.cutting_s)


def test_inch_inputs_give_the_same_time_as_mm():
    part = rect_part(1000, 100, "r")
    cm = cfg("2500x1250", feed_rate=6000, thickness=18, tool_diameter=8)
    ci = load_config(overrides={"units": "in", "sheet": "98.425197x49.212598", "feed_rate": 6000 / 25.4,
                                "thickness": 18 / 25.4, "tool_diameter": 8 / 25.4})
    a = estimate_time([one_sheet([(part, 0, 0)], cm)], {"r": part}, cm)
    b = estimate_time([one_sheet([(part, 0, 0)], ci)], {"r": part}, ci)
    assert (a.passes, a.total_s) == (b.passes, pytest.approx(b.total_s))


def test_rapid_tour_known_geometry():
    assert nearest_neighbour_length([(100, 0), (300, 0), (200, 0)], start=(0, 0)) == pytest.approx(100 + 100 + 100)
    assert nearest_neighbour_length([(30, 40)]) == pytest.approx(50)
    assert nearest_neighbour_length([]) == 0.0
    # greedy, not optimal: from 0 it goes to 10, then 20 (closer than -15), then back to -15
    assert nearest_neighbour_length([(10, 0), (20, 0), (-15, 0)]) == pytest.approx(10 + 10 + 35)


def test_rapid_time_uses_contour_starts_holes_included():
    part = plate_with_hole()
    c = cfg("2500x1250", feed_rate=6000, rapid_rate=1000)
    lay = one_sheet([(part, 500, 0)], c)
    starts = contour_starts(lay, {"plate": part})
    assert len(starts) == 3                                    # two holes + the outer contour
    est = estimate_time([lay], {"plate": part}, c)
    assert est.rapid_s == pytest.approx(nearest_neighbour_length(starts) / 1000 * 60)
    assert est.rapid_s > 0


def test_real_job_numbers(tmp_path):
    c, res, parts = nested_sample(tmp_path, feed_rate=6000, thickness=18, tool_diameter=8)[:3]
    est = estimate_time(res.layouts, parts, c)
    total = cut_summary(res.layouts, parts).length
    assert est.passes == 3
    assert est.cutting_s == pytest.approx(total * 3 / 6000 * 60)             # ~17.6 min
    assert 1000 < est.total_s < 1300 and sum(est.per_sheet_s) == pytest.approx(est.total_s)


# ----------------------------------------------------------- text, captions
@pytest.mark.parametrize("sec, text", [(0, "0 s"), (44.4, "44 s"), (89, "89 s"), (90, "2 min"), (1056, "18 min"),
                                       (3599, "60 min"), (3600, "1 h 00 min"), (8040, "2 h 14 min")])
def test_format_duration(sec, text):
    assert format_duration(sec) == text


def test_caption_has_cut_length_and_pierces(tmp_path):
    c, res, parts = nested_sample(tmp_path)[:3]
    cap = sheet_caption(res.layouts[0], parts, c)
    assert cap.startswith("Sheet 1: 2500.0 x 1250.0 mm, 12 piece(s), 87.0% material, cut 29.36 m (12 pierces), margin 0.0 mm")
    assert "grain along X (sheet width)" in cap
    ci = load_config(overrides={"units": "in", "sheet": "98.425197x49.212598"})
    assert "ft (12 pierces)" in sheet_caption(res.layouts[0], parts, ci) and "cut 96.3" in sheet_caption(res.layouts[0], parts, ci)
