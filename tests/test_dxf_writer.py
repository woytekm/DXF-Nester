import math
from collections import Counter, defaultdict
from pathlib import Path

import ezdxf
import pytest
from helpers import cfg, item, nested_sample, rect_item, rect_part
from shapely.geometry import Polygon
from shapely.ops import unary_union

from nestlib.chain import join_primitives
from nestlib.contours import build_part
from nestlib.dxf_writer import ascii_fold, label_for, write_sheet_dxf, write_sheet_dxfs
from nestlib.geometry import loop_points
from nestlib.layout import place_polygon
from nestlib.loader import load_dxf
from nestlib.models import ArcSeg, CircleSeg, LineSeg, Placement, RawPart
from nestlib.nest_rect import nest_rectangles


def plate_with_hole(name="plate"):
    r, w, h = 10.0, 100.0, 60.0
    prims = [LineSeg((r, 0), (w - r, 0)), LineSeg((w, r), (w, h - r)), LineSeg((w - r, h), (r, h)),
             LineSeg((0, h - r), (0, r)), ArcSeg((w - r, r), r, 270, 0), ArcSeg((w - r, h - r), r, 0, 90),
             ArcSeg((r, h - r), r, 90, 180), ArcSeg((r, r), r, 180, 270), CircleSeg((30, 30), 8)]
    return build_part(RawPart(name, Path(f"{name}.dxf"), tuple(prims), "millimetres"))


def prim_length(p):
    if isinstance(p, LineSeg):
        return math.dist(p.start, p.end)
    if isinstance(p, ArcSeg):
        return p.radius * math.radians(p.sweep)
    return 2 * math.pi * p.radius


def read_back(path, c):
    """Load a written sheet with our own loader and split the primitives by layer."""
    raw = load_dxf(path, flatten_tol=c.tolerance, units=c.units)
    by_layer = defaultdict(list)
    for p in raw.primitives:
        by_layer[p.layer].append(p)
    return by_layer


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    d = tmp_path_factory.mktemp("sample")
    c, res, parts = nested_sample(d)[:3]
    paths = write_sheet_dxfs(res.layouts, parts, c, d / "out")
    return c, res, parts, paths


# --------------------------------------------------------------- structure
def test_one_file_per_sheet_with_the_four_layers(sample):
    c, res, parts, paths = sample
    assert [p.name for p in paths] == ["sheet_01.dxf", "sheet_02.dxf"]
    doc = ezdxf.readfile(paths[0])
    assert doc.dxfversion == "AC1015" and doc.header["$INSUNITS"] == 4
    layers = {l.dxf.name: l.dxf.color for l in doc.layers if l.dxf.name != "0" and l.dxf.name != "Defpoints"}
    assert layers == {"SHEET": 8, "CUT_OUTER": 1, "CUT_INNER": 5, "LABELS": 3}


def test_entities_per_layer(sample):
    c, res, parts, paths = sample
    for layout, path in zip(res.layouts, paths):
        n = len(layout.placements)
        got = Counter((e.dxftype(), e.dxf.layer) for e in ezdxf.readfile(path).modelspace())
        assert got == Counter({("LWPOLYLINE", "SHEET"): 1, ("LWPOLYLINE", "CUT_OUTER"): n, ("TEXT", "LABELS"): n})


def test_sheet_outline_is_the_full_sheet(sample):
    c, res, parts, paths = sample
    sheet = [e for e in ezdxf.readfile(paths[0]).modelspace() if e.dxf.layer == "SHEET"][0]
    assert sheet.closed and list(sheet.get_points("xy")) == [(0, 0), (2500, 0), (2500, 1250), (0, 1250)]


# ----------------------------------------------------- round trip, true geometry
def test_round_trip_geometry_matches_the_nesting(sample):
    c, res, parts, paths = sample
    for layout, path in zip(res.layouts, paths):
        cut = read_back(path, c)["CUT_OUTER"]
        chain = join_primitives(cut, c.tolerance)
        assert chain.ok and len(chain.loops) == len(layout.placements)         # every contour exactly once
        got = unary_union([Polygon(loop_points(l, 0.01)) for l in chain.loops])
        want = unary_union([Polygon(place_polygon(parts[p.part_name], p).exterior) for p in layout.placements])
        assert got.symmetric_difference(want).area < 1.0                        # mm^2 on ~3 m^2


def test_cut_length_is_counted_once(sample):
    c, res, parts, paths = sample
    for layout, path in zip(res.layouts, paths):
        by = read_back(path, c)
        cut = sum(prim_length(p) for p in by["CUT_OUTER"] + by["CUT_INNER"])
        want = sum(parts[p.part_name].cut_length for p in layout.placements)
        assert cut == pytest.approx(want, abs=0.01)


def test_labels_are_text_on_the_labels_layer_and_sit_on_solid_material(sample):
    from shapely.geometry import box
    from nestlib.dxf_writer import label_box
    c, res, parts, paths = sample
    for layout, path in zip(res.layouts, paths):
        texts = [e for e in ezdxf.readfile(path).modelspace() if e.dxftype() == "TEXT"]
        assert all(t.dxf.layer == "LABELS" for t in texts)
        labels = {t.dxf.text: t for t in texts}
        for pl in layout.placements:
            t = labels.get(f"{pl.part_name} #{pl.instance}") or labels.get(f"#{pl.instance}")
            assert t is not None, (pl.part_name, pl.instance)
            assert t.dxf.rotation == (90.0 if pl.height > pl.width else 0.0)
            assert 0.5 <= t.dxf.height <= 25.0
            bw, bh = label_box(t.dxf.text, t.dxf.height, t.dxf.rotation)
            x, y = t.dxf.align_point.x, t.dxf.align_point.y
            # the whole text box lies inside the piece: never across a slot or a hole
            assert place_polygon(parts[pl.part_name], pl).contains(box(x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2)), (pl.part_name, pl.instance)


# ------------------------------------------------------------------ holes, arcs
def test_holes_go_to_cut_inner_and_arcs_stay_arcs(tmp_path):
    part = plate_with_hole()
    items = [item(part, 3)]
    c = cfg("400x300")
    res = nest_rectangles(items, c)
    path = write_sheet_dxf(res.layouts[0], {"plate": part}, c, tmp_path / "s.dxf")
    msp = ezdxf.readfile(path).modelspace()
    assert Counter(e.dxf.layer for e in msp if e.dxftype() == "CIRCLE") == {"CUT_INNER": 3}
    outers = [e for e in msp if e.dxftype() == "LWPOLYLINE" and e.dxf.layer == "CUT_OUTER"]
    assert len(outers) == 3 and all(any(b != 0 for _, _, b in e.get_points("xyb")) for e in outers)   # bulges kept
    by = read_back(path, c)
    assert sum(isinstance(p, ArcSeg) for p in by["CUT_OUTER"]) == 12 and sum(isinstance(p, CircleSeg) for p in by["CUT_INNER"]) == 3


def test_round_part_is_written_as_a_circle(tmp_path):
    disc = build_part(RawPart("disc", Path("disc.dxf"), (CircleSeg((5, 5), 50),), "mm"))
    c = cfg("300x300")
    res = nest_rectangles([item(disc, 2)], c)
    msp = ezdxf.readfile(write_sheet_dxf(res.layouts[0], {"disc": disc}, c, tmp_path / "d.dxf")).modelspace()
    assert Counter((e.dxftype(), e.dxf.layer) for e in msp if e.dxf.layer == "CUT_OUTER") == {("CIRCLE", "CUT_OUTER"): 2}


# -------------------------------------------------------------------- units
def test_inch_output_is_scaled_and_labelled_as_inches(tmp_path):
    # 98.425197 x 49.212598 in = 2500 x 1250 mm; spacing 10/25.4 in = 10 mm
    c, res, parts = nested_sample(tmp_path, sheet="98.425197x49.212598", spacing=10 / 25.4, units="in")[:3]
    path = write_sheet_dxf(res.layouts[0], parts, c, tmp_path / "in.dxf")
    doc = ezdxf.readfile(path)
    assert doc.header["$INSUNITS"] == 1 and doc.header["$MEASUREMENT"] == 0
    sheet = [e for e in doc.modelspace() if e.dxf.layer == "SHEET"][0]
    assert list(sheet.get_points("xy"))[2] == pytest.approx((98.425197, 49.212598))
    texts = [e for e in doc.modelspace() if e.dxftype() == "TEXT"]
    assert max(t.dxf.height for t in texts) == pytest.approx(25.0 / 25.4)                 # label height scaled too
    # our loader converts back to mm using $INSUNITS: same geometry as the nesting decided
    chain = join_primitives(read_back(path, c)["CUT_OUTER"], c.tolerance)
    got = unary_union([Polygon(loop_points(l, 0.01)) for l in chain.loops])
    want = unary_union([Polygon(place_polygon(parts[p.part_name], p).exterior) for p in res.layouts[0].placements])
    assert chain.ok and got.symmetric_difference(want).area < 1.0
    assert got.bounds[2] <= 2500.01 and got.bounds[3] <= 1250.01                         # read back in mm: inside the sheet


# ---------------------------------------------------------- versions, names
@pytest.mark.parametrize("version, code", [("R2000", "AC1015"), ("R2004", "AC1018"), ("R2010", "AC1024"), ("R2018", "AC1032")])
def test_dxf_versions_are_written_and_readable(tmp_path, version, code):
    c, res, parts = nested_sample(tmp_path, dxf_version=version)[:3]
    path = write_sheet_dxf(res.layouts[0], parts, c, tmp_path / "v.dxf")
    assert ezdxf.readfile(path).dxfversion == code
    chain = join_primitives(read_back(path, c)["CUT_OUTER"], c.tolerance)
    assert chain.ok and len(chain.loops) == len(res.layouts[0].placements)


@pytest.mark.parametrize("version, expected", [("R2000", "Polka_typ_3 #1"), ("R2004", "Polka_typ_3 #1"),
                                              ("R2007", "Pó\u0142ka_typ_3 #1"), ("R2010", "Pó\u0142ka_typ_3 #1"),
                                              ("R2018", "Pó\u0142ka_typ_3 #1")])
def test_polish_part_names_in_labels(tmp_path, version, expected):
    part = build_part(RawPart("Pó\u0142ka_typ_3", Path("x.dxf"), tuple(LineSeg(a, b) for a, b in
                      [((0, 0), (800, 0)), ((800, 0), (800, 300)), ((800, 300), (0, 300)), ((0, 300), (0, 0))]), "mm"))
    c = cfg("1000x500", dxf_version=version)
    res = nest_rectangles([item(part, 1)], c)
    path = write_sheet_dxf(res.layouts[0], {part.name: part}, c, tmp_path / "pl.dxf")
    texts = [e.dxf.text for e in ezdxf.readfile(path).modelspace() if e.dxftype() == "TEXT"]
    assert texts == [expected]                 # and never a literal \\U+0142 escape in the file
    assert "\\U+" not in path.read_text(encoding="utf-8", errors="replace") or version in ("R2007", "R2010", "R2018")


def test_ascii_fold():
    assert ascii_fold("Pó\u0142ka_typ_3") == "Polka_typ_3"
    assert ascii_fold("\u0105\u0107\u0119\u0142\u0144\u00f3\u015b\u017a\u017c \u0104\u0106\u0118\u0141\u0143\u00d3\u015a\u0179\u017b") == "acelnoszz ACELNOSZZ"
    assert ascii_fold("plain_ASCII #12") == "plain_ASCII #12"
    assert ascii_fold("\u677f_1") == "__1"


def test_custom_layer_names_are_used(tmp_path):
    c, res, parts = nested_sample(tmp_path, layers={"sheet": "ARKUSZ", "cut_outer": "TNIJ", "cut_inner": "OTWORY", "labels": "OPISY"})[:3]
    msp = ezdxf.readfile(write_sheet_dxf(res.layouts[0], parts, c, tmp_path / "l.dxf")).modelspace()
    assert {e.dxf.layer for e in msp} == {"ARKUSZ", "TNIJ", "OPISY"}


def test_directory_is_created_and_file_overwritten(tmp_path):
    c, res, parts = nested_sample(tmp_path)[:3]
    target = tmp_path / "a" / "b" / "s.dxf"
    write_sheet_dxf(res.layouts[1], parts, c, target)
    size = target.stat().st_size
    write_sheet_dxf(res.layouts[0], parts, c, target)
    assert target.stat().st_size != size and ezdxf.readfile(target)


# --------------------------------------------------------------- label sizing
def pl(name="part", n=1):
    return Placement(name, n, 0.0, 0, 0, 0, 0)


def test_label_for_long_landscape_piece():
    text, h, rot = label_for(pl("polka_typ_3"), 2500, 300)
    assert (text, rot) == ("polka_typ_3 #1", 0.0) and h == pytest.approx(25.0)           # capped at 25 mm


def test_label_for_portrait_piece_runs_along_its_length():
    text, h, rot = label_for(pl("szuflada_bok", 4), 200, 272)
    assert (text, rot) == ("szuflada_bok #4", 90.0) and 20 < h <= 25


def test_label_shrinks_then_shortens_for_small_pieces():
    text, h, rot = label_for(pl("a_rather_long_part_name"), 120, 60)
    assert text == "a_rather_long_part_name #1" and 2.5 <= h < 7.2                       # shrunk to fit 90 % of 120 mm
    text, h, rot = label_for(pl("a_rather_long_part_name", 12), 40, 25)
    assert text == "#12" and h >= 0.5                                                      # too small: short form
    text, h, _ = label_for(pl("x"), 3, 3)
    assert text == "#1" and h >= 0.5                                                        # never zero


def test_label_text_never_wider_than_its_piece():
    for w, hh in [(2500, 300), (200, 272), (120, 60), (80, 80), (30, 20)]:
        text, h, rot = label_for(pl("szuflada_front", 3), w, hh)
        assert len(text) * 0.62 * h <= 0.9 * max(w, hh) + 1e-9 or h <= 0.5 + 1e-9


# ---------------------------------------------------------------- label_anchor
def test_label_anchor_avoids_notches_and_holes():
    from shapely.geometry import box
    from nestlib.dxf_writer import label_anchor
    # 1000 x 300 strip with a 40 mm slot (open at the top) through the middle, 250 deep
    strip = Polygon([(0, 0), (1000, 0), (1000, 300), (520, 300), (520, 50), (480, 50), (480, 300), (0, 300)])
    slot = box(480, 50, 520, 300)
    x, y = label_anchor(strip, 200, 30)
    text_box = box(x - 100, y - 15, x + 100, y + 15)
    assert strip.contains(text_box) and not text_box.intersects(slot)      # never across the slot
    assert abs(y - 150) < 150 and (x, y) != (500, 150)                     # the plain centre would have hit it


def test_label_anchor_prefers_the_middle_when_everything_fits():
    from nestlib.dxf_writer import label_anchor
    x, y = label_anchor(Polygon([(0, 0), (400, 0), (400, 200), (0, 200)]), 100, 20)
    assert (x, y) == pytest.approx((200, 100), abs=3)


def test_label_anchor_falls_back_when_text_cannot_fit():
    from nestlib.dxf_writer import label_anchor
    x, y = label_anchor(Polygon([(0, 0), (40, 0), (40, 40), (0, 40)]), 100, 20)
    assert (x, y) == pytest.approx((20, 20), abs=1)


def test_label_anchor_with_hole_in_the_middle():
    from shapely.geometry import box
    from nestlib.dxf_writer import label_anchor
    plate = Polygon([(0, 0), (300, 0), (300, 200), (0, 200)], [[(130, 70), (170, 70), (170, 130), (130, 130)]])
    x, y = label_anchor(plate, 100, 20)
    assert plate.contains(box(x - 50, y - 10, x + 50, y + 10))
