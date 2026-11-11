import math
import random
import shutil
import time
from pathlib import Path

import pytest
from helpers import ALL4, cfg, item, rect_item, rect_part

from nestlib.checks import check_parts
from nestlib.config import load_config
from nestlib.contours import build_part
from nestlib.loader import load_dxf
from nestlib.manifest import read_manifest
from nestlib.models import Severity
from nestlib.nest_rect import MaxRectsBin, nest_rectangles
from nestlib.verify import verify_layouts

DATA = Path(__file__).parent / "data"


def run(items, c):
    res = nest_rectangles(items, c)
    assert res.ok, [i.message for i in res.issues if i.severity is Severity.ERROR]
    assert verify_layouts(res.layouts, items, c) == []      # the oracle, for every single run
    return res


def n_pieces(res):
    return sum(len(l.placements) for l in res.layouts)


# ------------------------------------------------------------------ MaxRectsBin
def no_free_rect_contains_another(free):
    for i, a in enumerate(free):
        for j, b in enumerate(free):
            if i != j and b[0] <= a[0] + 1e-9 and b[1] <= a[1] + 1e-9 \
                    and a[0] + a[2] <= b[0] + b[2] + 1e-9 and a[1] + a[3] <= b[1] + b[3] + 1e-9:
                return False
    return True


def test_bin_split_after_first_placement():
    b = MaxRectsBin(100, 100)
    b.place(0, 0, 40, 30)
    assert sorted(b.free) == [(0, 30, 100, 70), (40, 0, 60, 100)]      # two overlapping maximal rectangles
    assert b.used_area == 1200


def test_bin_exact_fit_and_overflow():
    b = MaxRectsBin(100, 100)
    assert b.find(100, 100, "bssf") is not None
    assert b.find(100.0000000001, 100, "bssf") is not None      # within epsilon
    assert b.find(100.1, 50, "bssf") is None
    b.place(0, 0, 100, 100)
    assert b.free == [] and b.find(1, 1, "bssf") is None


def test_bin_heuristics_choose_differently():
    b = MaxRectsBin(200, 100)
    b.place(0, 0, 100, 60)               # leaves (100,0,100,100) and (0,60,200,40)
    # a 100x40 box. bottom-left: lowest top edge -> beside the first box. best-area-fit: the snug
    # 200x40 strip above it (area slack 4000 vs 6000). BSSF happens to agree with bottom-left here.
    assert b.find(100, 40, "bl")[1:] == (100, 0)
    assert b.find(100, 40, "bssf")[1:] == (100, 0)
    assert b.find(100, 40, "baf")[1:] == (0, 60)


def test_random_placements_never_overlap_and_stay_inside():
    rnd = random.Random(5)
    for heur in ("bssf", "blsf", "baf", "bl"):
        b = MaxRectsBin(300, 200)
        boxes = []
        for _ in range(80):
            w, h = rnd.uniform(10, 90), rnd.uniform(10, 70)
            f = b.find(w, h, heur)
            if f is None:
                continue
            _, x, y = f
            b.place(x, y, w, h)
            boxes.append((x, y, w, h))
            assert no_free_rect_contains_another(b.free)
        assert len(boxes) > 10
        for i, (x, y, w, h) in enumerate(boxes):
            assert x >= -1e-9 and y >= -1e-9 and x + w <= 300 + 1e-9 and y + h <= 200 + 1e-9
            for (x2, y2, w2, h2) in boxes[i + 1:]:
                assert x + w <= x2 + 1e-9 or x2 + w2 <= x + 1e-9 or y + h <= y2 + 1e-9 or y2 + h2 <= y + 1e-9
        assert b.used_area == pytest.approx(sum(w * h for _, _, w, h in boxes))


# ----------------------------------------------------------- basic behaviour
def test_single_part_sits_in_the_corner_of_the_usable_area():
    res = run([rect_item(100, 50)], cfg("1000x500", margin=7, fit_spacing=10))
    (p,) = res.layouts[0].placements
    assert (p.x, p.y, p.width, p.height) == (7.0, 7.0, 100.0, 50.0)


def test_exact_tiling_with_zero_spacing():
    c = cfg("1000x1000", fit_spacing=0)
    assert len(run([rect_item(500, 500, 4)], c).layouts) == 1
    assert len(run([rect_item(500, 500, 5)], c).layouts) == 2


def test_spacing_is_part_of_the_budget():
    # two 100-wide parts plus a 10 mm gap need 210
    assert len(run([rect_item(100, 100, 2, rotations=(0.0,))], cfg("210x100", fit_spacing=10)).layouts) == 1
    assert len(run([rect_item(100, 100, 2, rotations=(0.0,))], cfg("209x100", fit_spacing=10)).layouts) == 2


def test_margin_reduces_the_usable_area():
    assert len(run([rect_item(100, 100, 2, rotations=(0.0,))], cfg("210x100", fit_spacing=10, margin=0)).layouts) == 1
    res = nest_rectangles([rect_item(100, 100, 1, rotations=(0.0,))], cfg("205x110", margin=5, fit_spacing=0))
    assert res.ok and len(res.layouts) == 1                                  # 100 fits in 205 - 10
    res = nest_rectangles([rect_item(196, 50, 1, rotations=(0.0,))], cfg("205x110", margin=5, fit_spacing=0))
    assert not res.ok and res.layouts == ()                                  # 196 > 195 usable


def test_parts_larger_than_the_sheet_are_reported_not_crashed():
    big, ok = rect_item(2000, 100, 2, rotations=(0.0,), name="big"), rect_item(100, 100, 1, name="ok")
    res = nest_rectangles([big, ok], cfg("1000x500"))
    assert [i.code for i in res.issues if i.severity is Severity.ERROR] == ["unplaced_part", "unplaced_part"]
    assert {p.part_name for l in res.layouts for p in l.placements} == {"ok"}


def test_empty_input():
    res = nest_rectangles([], cfg())
    assert res.ok and res.layouts == ()


# ------------------------------------------------------------------ rotation
def test_forced_single_rotation_is_never_violated():
    it = rect_item(100, 50, 6, rotations=(0.0, 180.0))
    res = run([it], cfg("150x100", fit_spacing=0))
    assert {p.rotation for l in res.layouts for p in l.placements} == {0.0}      # 0 chosen over 180 (same box)


def test_rotation_is_used_when_allowed_and_helps():
    # usable 150 x 100: two 100x50 stacked + one 50x100 turned beside them = 3 per sheet
    c = cfg("150x100", fit_spacing=0)
    free = run([rect_item(100, 50, 3, rotations=ALL4)], c)
    locked = run([rect_item(100, 50, 3, rotations=(0.0, 180.0))], c)
    assert len(free.layouts) == 1 and len(locked.layouts) == 2
    assert {p.rotation for p in free.layouts[0].placements} == {0.0, 90.0}


def test_part_that_only_fits_turned_is_turned():
    res = run([rect_item(200, 50, 2, rotations=(90.0, 270.0))], cfg("100x200", fit_spacing=0))
    assert {p.rotation for l in res.layouts for p in l.placements} == {90.0}
    assert len(res.layouts) == 1                                             # two 50x200 strips side by side


def test_every_allowed_rotation_is_considered_for_non_right_angles():
    it = rect_item(100, 20, 1, rotations=(45.0,))
    res = run([it], cfg("200x200"))
    (p,) = res.layouts[0].placements
    assert p.rotation == 45.0 and p.width == pytest.approx(120 / math.sqrt(2), rel=1e-9)


# ---------------------------------------------------------- determinism, scale
def test_deterministic():
    items = [rect_item(310, 120, 7, name="a"), rect_item(90, 410, 5, name="b"), rect_item(150, 150, 9, name="c")]
    c = cfg("1000x700")
    assert nest_rectangles(items, c) == nest_rectangles(items, c)


def test_sheet_utilisation_and_summary_reported():
    res = run([rect_item(500, 500, 5)], cfg("1000x1000", fit_spacing=0))
    infos = [i.message for i in res.issues if i.code == "sheet_utilisation"]
    assert len(infos) == 2 and "100.0%" in infos[0] and "25.0%" in infos[1]
    assert any(i.code == "nest_summary" and "2 sheet(s)" in i.message for i in res.issues)


@pytest.mark.parametrize("seed", range(12))
def test_random_jobs_are_always_valid_and_complete(seed):
    rnd = random.Random(seed)
    items = []
    for k in range(rnd.randint(2, 7)):
        w, h = rnd.uniform(30, 480), rnd.uniform(30, 480)
        rots = rnd.choice([ALL4, (0.0, 180.0), (90.0, 270.0), (0.0,), (0.0, 90.0)])
        items.append(item(rect_part(w, h, f"p{k}"), rnd.randint(1, 6), rots))
    c = cfg("1250x1000", margin=rnd.choice([0, 5, 12]), fit_spacing=rnd.choice([0, 3, 10]))
    res = run(items, c)                                   # verify_layouts inside: overlap, spacing, margin, rotations, counts
    assert n_pieces(res) == sum(i.qty for i in items)
    usable = c.sheet.usable_width * c.sheet.usable_height
    lower = math.ceil(sum(i.part.area * i.qty for i in items) / usable - 1e-9)
    assert lower <= len(res.layouts) <= 2 * lower + 2     # never below the area bound; sanity upper bound


def test_larger_job_is_fast():
    rnd = random.Random(1)
    items = [item(rect_part(rnd.uniform(40, 300), rnd.uniform(40, 300), f"p{k}"), 10) for k in range(40)]
    t = time.perf_counter()
    res = run(items, cfg("2500x1250", fit_spacing=10))
    assert n_pieces(res) == 400 and time.perf_counter() - t < 20.0


# --------------------------------------------------------------- real parts
def test_the_four_sample_parts_on_a_standard_sheet(tmp_path):
    for f in DATA.glob("*.dxf"):
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / "parts.csv").write_text(
        "filename;qty;grain\npolka_typ_3.dxf;2;along\nprzegroda_typ_3.dxf;1;\nszuflada_front.dxf;4;across\nszuflada_bok.dxf;8;\n",
        encoding="utf-8")
    c = load_config(overrides={"sheet": "2500x1250"})
    pairs = [(r, build_part(load_dxf(r.path))) for r in read_manifest(tmp_path / "parts.csv")]
    chk = check_parts(pairs, c)
    assert chk.ok
    res = run(chk.items, c)
    assert len(res.layouts) == 2                                  # = the area lower bound (3.30 m2 on 3.125 m2 sheets)
    fronts = [p for l in res.layouts for p in l.placements if p.part_name == "szuflada_front"]
    assert len(fronts) == 4 and {p.rotation for p in fronts} <= {90.0, 270.0}        # grain 'across' honoured
    shelves = [p for l in res.layouts for p in l.placements if p.part_name == "polka_typ_3"]
    assert len(shelves) == 2 and all(p.rotation in (0.0, 180.0) and p.width == 2500 for p in shelves)
