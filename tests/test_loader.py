import math
import shutil
from pathlib import Path

import ezdxf
import pytest

from nestlib.loader import DxfLoadError, load_dxf
from nestlib.models import ArcSeg, CircleSeg, LineSeg, Severity

DATA = Path(__file__).parent / "data"


# ------------------------------------------------------------------ helpers
def make(tmp_path, build, *, units=4, version="R2010", name="t.dxf"):
    doc = ezdxf.new(version)
    doc.header["$INSUNITS"] = units
    build(doc, doc.modelspace())
    p = tmp_path / name
    doc.saveas(p)
    return p


def codes(part):
    return {i.code for i in part.issues}


def close(a, b, tol=1e-6):
    return math.dist(a, b) < tol


def arc_mid(a: ArcSeg):
    ang = math.radians(a.start_angle + a.sweep / 2)
    return (a.center[0] + a.radius * math.cos(ang), a.center[1] + a.radius * math.sin(ang))


def endpoints(prim):
    return (prim.start_point, prim.end_point) if isinstance(prim, ArcSeg) else (prim.start, prim.end)


# -------------------------------------------------------- the real sample file
def test_real_file_polka_typ_3(tmp_path):
    src = DATA / "polka_typ_3.dxf"
    part = load_dxf(src)
    assert part.name == "polka_typ_3"
    assert part.source_units == "millimetres"
    # two closed 20-vertex LWPOLYLINEs -> 2 x 20 line segments, no arcs
    assert len(part.primitives) == 40
    assert all(isinstance(p, LineSeg) and p.layer == "0" for p in part.primitives)
    xs = [c[0] for p in part.primitives for c in (p.start, p.end)]
    ys = [c[1] for p in part.primitives for c in (p.start, p.end)]
    assert (min(xs), max(xs)) == (1900.0, 4400.0)
    assert (min(ys), max(ys)) == (0.0, 300.0)
    # the stray POINT is reported, not silently dropped, and is only INFO
    ign = [i for i in part.issues if i.code == "ignored_entity"]
    assert len(ign) == 1 and "POINT" in ign[0].message and ign[0].severity is Severity.INFO
    assert all(i.part == "polka_typ_3" for i in part.issues)


# ------------------------------------------------------------------- basics
def test_line_arc_circle(tmp_path):
    def build(doc, msp):
        msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "A"})
        msp.add_arc((0, 0), 5, 30, 120)
        msp.add_circle((3, 4), 2)

    part = load_dxf(make(tmp_path, build))
    line, arc, circ = part.primitives
    assert line == LineSeg((0.0, 0.0), (10.0, 0.0), "A")
    assert (arc.radius, arc.start_angle, arc.end_angle) == (5.0, 30.0, 120.0)
    assert circ == CircleSeg((3.0, 4.0), 2.0, "0")


def test_zero_length_line_dropped(tmp_path):
    part = load_dxf(make(tmp_path, lambda d, m: m.add_line((1, 1), (1, 1))))
    assert part.primitives == ()


def test_full_arc_becomes_circle(tmp_path):
    part = load_dxf(make(tmp_path, lambda d, m: m.add_arc((0, 0), 3, 0, 360)))
    assert part.primitives == (CircleSeg((0.0, 0.0), 3.0, "0"),)


def test_paperspace_ignored(tmp_path):
    def build(doc, msp):
        msp.add_line((0, 0), (1, 0))
        doc.layout("Layout1").add_line((0, 0), (99, 99))

    assert len(load_dxf(make(tmp_path, build)).primitives) == 1


# -------------------------------------------------------------------- units
def test_inches_converted(tmp_path):
    part = load_dxf(make(tmp_path, lambda d, m: m.add_line((0, 0), (1, 0)), units=1))
    assert part.primitives[0].end == (25.4, 0.0)
    assert part.source_units == "inches" and "units_converted" in codes(part)


def test_unitless_assumed_mm_with_warning(tmp_path):
    part = load_dxf(make(tmp_path, lambda d, m: m.add_line((0, 0), (7, 0)), units=0))
    assert part.primitives[0].end == (7.0, 0.0)
    w = [i for i in part.issues if i.code == "units_unspecified"]
    assert w and w[0].severity is Severity.WARNING


def test_unsupported_units(tmp_path):
    with pytest.raises(DxfLoadError, match="unsupported drawing units"):
        load_dxf(make(tmp_path, lambda d, m: m.add_line((0, 0), (1, 0)), units=15))


# ------------------------------------------------------------ polylines/bulge
def test_open_vs_closed_polyline(tmp_path):
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    def build(doc, msp):
        msp.add_lwpolyline(sq, close=False)
        msp.add_lwpolyline(sq, close=True)
    part = load_dxf(make(tmp_path, build))
    assert len(part.primitives) == 3 + 4


def test_closed_polyline_with_repeated_last_vertex(tmp_path):
    pts = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    part = load_dxf(make(tmp_path, lambda d, m: m.add_lwpolyline(pts, close=True)))
    assert len(part.primitives) == 4  # no zero-length closing segment


@pytest.mark.parametrize(
    "p, q, bulge",
    [
        ((0, 0), (2, 0), 1.0),     # CCW semicircle
        ((0, 0), (2, 0), -1.0),    # CW semicircle
        ((0, 0), (2, 0), 0.5),     # minor arc
        ((0, 0), (2, 0), -0.5),
        ((0, 0), (2, 0), 2.0),     # major arc
        ((3, 4), (-1, 7), 0.3),    # arbitrary chord
        ((3, 4), (-1, 7), -3.0),
    ],
)
def test_bulge_geometry(tmp_path, p, q, bulge):
    def build(doc, msp):
        msp.add_lwpolyline([(*p, 0, 0, bulge), q], format="xyseb")
    (arc,) = load_dxf(make(tmp_path, build)).primitives
    assert isinstance(arc, ArcSeg)
    # endpoints coincide with the chord ends (in either order)
    s, e = arc.start_point, arc.end_point
    assert (close(s, p) and close(e, q)) or (close(s, q) and close(e, p))
    # the start of travel is p for bulge > 0 (ccw p->q), q for bulge < 0
    assert close(s, p if bulge > 0 else q)
    # sagitta of the bulge is |bulge| * chord / 2, on the right of p->q for bulge>0
    chord = math.dist(p, q)
    mid_chord = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
    sag = math.dist(arc_mid(arc), mid_chord)
    if abs(bulge) <= 1:
        assert math.isclose(sag, abs(bulge) * chord / 2, rel_tol=1e-9)
    else:  # major arc: farthest point from the chord is radius + centre offset
        assert sag > chord / 2
    dx, dy = q[0] - p[0], q[1] - p[1]
    side = dx * (arc_mid(arc)[1] - p[1]) - dy * (arc_mid(arc)[0] - p[0])  # >0: left of p->q
    assert (side < 0) == (bulge > 0)


def test_bulge_semicircle_exact(tmp_path):
    def build(doc, msp):
        msp.add_lwpolyline([(0, 0, 0, 0, 1), (2, 0)], format="xyseb")
    (arc,) = load_dxf(make(tmp_path, build)).primitives
    assert close(arc.center, (1, 0)) and math.isclose(arc.radius, 1.0)
    assert close(arc_mid(arc), (1, -1))


def test_old_style_polyline_with_bulge(tmp_path):
    def build(doc, msp):
        pl = msp.add_polyline2d([(0, 0), (4, 0), (4, 4)], close=True)
        pl.vertices[0].dxf.bulge = 1.0
    part = load_dxf(make(tmp_path, build))
    kinds = [type(p).__name__ for p in part.primitives]
    assert kinds == ["ArcSeg", "LineSeg", "LineSeg"]


# ----------------------------------------------------------- spline/ellipse
def test_spline_approximated(tmp_path):
    part = load_dxf(make(tmp_path, lambda d, m: m.add_spline(fit_points=[(0, 0), (5, 5), (10, 0)])))
    assert len(part.primitives) > 5 and all(isinstance(p, LineSeg) for p in part.primitives)
    assert close(part.primitives[0].start, (0, 0), 1e-6) and close(part.primitives[-1].end, (10, 0), 1e-6)
    assert "approximated_entity" in codes(part)
    for a, b in zip(part.primitives, part.primitives[1:]):  # continuous chain
        assert close(a.end, b.start)


def test_ellipse_approximated(tmp_path):
    part = load_dxf(make(tmp_path, lambda d, m: m.add_ellipse((0, 0), major_axis=(10, 0), ratio=0.5)))
    assert len(part.primitives) > 8
    for p in part.primitives:
        for x, y in (p.start, p.end):
            assert abs((x / 10) ** 2 + (y / 5) ** 2 - 1) < 0.01
    assert close(part.primitives[-1].end, part.primitives[0].start, 1e-6)


# ------------------------------------------------------------------- blocks
def test_insert_with_scale_and_rotation(tmp_path):
    def build(doc, msp):
        doc.blocks.new("B").add_line((0, 0), (1, 0))
        msp.add_blockref("B", (10, 10), dxfattribs={"xscale": 2, "yscale": 2, "rotation": 90})
    (ln,) = load_dxf(make(tmp_path, build)).primitives
    assert close(ln.start, (10, 10)) and close(ln.end, (10, 12))


def test_nested_inserts(tmp_path):
    def build(doc, msp):
        doc.blocks.new("INNER").add_line((0, 0), (1, 0))
        doc.blocks.new("OUTER").add_blockref("INNER", (5, 0))
        msp.add_blockref("OUTER", (0, 100))
    (ln,) = load_dxf(make(tmp_path, build)).primitives
    assert close(ln.start, (5, 100)) and close(ln.end, (6, 100))


def test_insert_recursion_is_bounded(tmp_path):
    def build(doc, msp):
        blk = doc.blocks.new("LOOP")
        blk.add_blockref("LOOP", (1, 0))
        msp.add_blockref("LOOP", (0, 0))
    part = load_dxf(make(tmp_path, build))
    assert part.primitives == () and "skipped_entity" in codes(part)


# ----------------------------------------------------------- mirrored (-Z)
def test_mirrored_arc(tmp_path):
    def build(doc, msp):
        msp.add_arc((10, 0), 5, 0, 90, dxfattribs={"extrusion": (0, 0, -1)})
    (arc,) = load_dxf(make(tmp_path, build)).primitives
    assert close(arc.center, (-10, 0))
    assert {tuple(round(v, 6) for v in arc.start_point), tuple(round(v, 6) for v in arc.end_point)} == {
        (-10.0, 5.0), (-15.0, 0.0)}
    assert close(arc.start_point, (-10, 5))  # ccw in WCS runs from the 90-degree point to the 180-degree point


def test_mirrored_circle_and_polyline(tmp_path):
    def build(doc, msp):
        msp.add_circle((10, 3), 2, dxfattribs={"extrusion": (0, 0, -1)})
        msp.add_lwpolyline([(1, 0), (1, 1)], dxfattribs={"extrusion": (0, 0, -1)})
    circ, ln = load_dxf(make(tmp_path, build)).primitives
    assert circ.center == (-10.0, 3.0)
    assert close(ln.start, (-1, 0)) and close(ln.end, (-1, 1))


def test_mirrored_bulge_flips_direction(tmp_path):
    def build(doc, msp):
        msp.add_lwpolyline([(0, 0, 0, 0, 1), (2, 0)], format="xyseb", dxfattribs={"extrusion": (0, 0, -1)})
    (arc,) = load_dxf(make(tmp_path, build)).primitives
    # OCS: ccw semicircle below the chord. Mirrored in X: chord (0,0)->(-2,0), arc must still be below
    assert close(arc_mid(arc), (-1, -1))


def test_tilted_plane_skipped(tmp_path):
    def build(doc, msp):
        msp.add_circle((0, 0), 2, dxfattribs={"extrusion": (1, 0, 0)})
    part = load_dxf(make(tmp_path, build))
    assert part.primitives == () and "skipped_entity" in codes(part)


# ------------------------------------------------------------ ignored things
def test_text_is_info_and_3dface_is_warning(tmp_path):
    def build(doc, msp):
        msp.add_text("hello")
        msp.add_3dface([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    part = load_dxf(make(tmp_path, build))
    sev = {i.message.split()[1]: i.severity for i in part.issues if i.code == "ignored_entity"}
    assert sev == {"TEXT": Severity.INFO, "3DFACE": Severity.WARNING}


# ----------------------------------------------------------------- failures
def test_missing_file(tmp_path):
    with pytest.raises(DxfLoadError, match="cannot read"):
        load_dxf(tmp_path / "nope.dxf")


def test_garbage_file(tmp_path):
    p = tmp_path / "bad.dxf"
    p.write_text("this is not a dxf file\n")
    with pytest.raises(DxfLoadError):
        load_dxf(p)


def test_binary_garbage_file(tmp_path):
    p = tmp_path / "bad.dxf"
    p.write_bytes(bytes(range(256)) * 20)
    with pytest.raises(DxfLoadError):
        load_dxf(p)


def test_truncated_file_is_recovered_with_warning(tmp_path):
    good = make(tmp_path, lambda d, m: [m.add_line((i, 0), (i, 1)) for i in range(50)])
    text = good.read_text(encoding="utf-8", errors="replace")
    cut = text[: int(len(text) * 0.9)]
    p = tmp_path / "cut.dxf"
    p.write_text(cut, encoding="utf-8")
    part = load_dxf(p)
    assert "recovered_file" in codes(part)
    assert len(part.primitives) == 50


# ------------------------------------------------------- user units / messages
from nestlib.units import INCH


def test_unitless_drawing_is_assumed_to_use_the_users_units(tmp_path):
    p = make(tmp_path, lambda d, m: m.add_line((0, 0), (7, 0)), units=0)
    part = load_dxf(p, units=INCH)
    assert part.primitives[0].end == pytest.approx((177.8, 0.0))     # 7 inches, stored as mm
    assert part.source_units == "inches (assumed)"
    assert "assuming inches" in next(i.message for i in part.issues if i.code == "units_unspecified")


def test_declared_units_win_over_the_users_units(tmp_path):
    p = make(tmp_path, lambda d, m: m.add_line((0, 0), (10, 0)), units=4)   # drawing says mm
    assert load_dxf(p, units=INCH).primitives[0].end == (10.0, 0.0)


def test_approximation_message_uses_users_units(tmp_path):
    p = make(tmp_path, lambda d, m: m.add_spline(fit_points=[(0, 0), (5, 5), (10, 0)]))
    msg = next(i.message for i in load_dxf(p, flatten_tol=0.0254, units=INCH).issues if i.code == "approximated_entity")
    assert "tolerance 0.0010 in" in msg
