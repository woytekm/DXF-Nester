import io
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from nestlib.contours import build_part
from nestlib.loader import load_dxf
from nestlib.models import ArcSeg, CircleSeg, LineSeg, RawPart
from nestlib.preview import part_svg, write_part_svg

DATA = Path(__file__).parent / "data"
FILL = (0xE8, 0xD3, 0xA8)


def rounded_rect_with_hole(name="rr", w=100.0, h=60.0, r=25.0):
    prims = [LineSeg((r, 0), (w - r, 0)), LineSeg((w, r), (w, h - r)),
             LineSeg((w - r, h), (r, h)), LineSeg((0, h - r), (0, r)),
             ArcSeg((w - r, r), r, 270, 0), ArcSeg((w - r, h - r), r, 0, 90),
             ArcSeg((r, h - r), r, 90, 180), ArcSeg((r, r), r, 180, 270),
             CircleSeg((50, 30), 12)]
    return build_part(RawPart(name, Path(f"{name}.dxf"), tuple(prims), "millimetres"))


def test_svg_is_well_formed_xml_with_one_path():
    part = rounded_rect_with_hole()
    root = ET.fromstring(part_svg(part))
    paths = root.findall(".//{http://www.w3.org/2000/svg}path")
    assert len(paths) == 1
    d = paths[0].attrib["d"]
    assert d.count("M") == 2 and d.count("Z") == 2          # outer + one hole
    assert " A " in d and root.attrib["height"] and root.attrib["width"]


def test_name_is_escaped():
    part = rounded_rect_with_hole(name="a&b<c>")
    root = ET.fromstring(part_svg(part))   # would raise if not escaped
    assert "a&amp;b" in part_svg(part)


def test_write_part_svg(tmp_path):
    p = write_part_svg(rounded_rect_with_hole(), tmp_path / "x.svg")
    assert p.read_text(encoding="utf-8").startswith("<svg")


def filled_area_mm2(part, px_width=1500):
    """Rasterise the SVG (outline stroke removed, so only the fill is measured)
    and convert the filled pixel count back to mm^2."""
    cairosvg = pytest.importorskip("cairosvg")
    from PIL import Image

    svg = part_svg(part, show_dimensions=False).replace('stroke="#222"', 'stroke="none"')
    vb_w = float(ET.fromstring(svg).attrib["viewBox"].split()[2])
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=px_width)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    colours = img.getcolors(maxcolors=1 << 22)
    n = sum(c for c, rgb in colours if all(abs(a - b) <= 6 for a, b in zip(rgb, FILL)))
    return n / (px_width / vb_w) ** 2


def test_rendered_area_matches_part_area_arcs_and_hole():
    # Wrong arc sweep flags would turn the rounded corners into bumps (+/- 9 % area);
    # a missing even-odd rule would fill the hole.
    part = rounded_rect_with_hole()
    assert filled_area_mm2(part) == pytest.approx(part.area, rel=0.015)


@pytest.mark.parametrize("name", ["szuflada_front", "przegroda_typ_3", "polka_typ_3", "szuflada_bok"])
def test_rendered_area_matches_corpus_parts(name):
    part = build_part(load_dxf(DATA / f"{name}.dxf"))
    assert filled_area_mm2(part) == pytest.approx(part.area, rel=0.02)


def test_label_uses_the_users_units():
    from nestlib.units import INCH
    part = rounded_rect_with_hole()
    assert "100.0 x 60.0 mm" in part_svg(part)
    assert "3.94 x 2.36 in" in part_svg(part, units=INCH)


# ----------------------------------------------------------- nested sheets
from helpers import nested_sample as _nested_sample


def test_layout_svg_is_well_formed_and_has_one_path_per_piece(tmp_path):
    from nestlib.preview import layout_svg
    cfg, res, parts = _nested_sample(tmp_path)
    for lay in res.layouts:
        root = ET.fromstring(layout_svg(lay, parts, cfg))
        assert len(root.findall(".//{http://www.w3.org/2000/svg}path")) == len(lay.placements)


def test_layout_svg_rendered_fill_equals_part_material(tmp_path):
    cairosvg = pytest.importorskip("cairosvg")
    from PIL import Image
    from nestlib.preview import layout_svg
    cfg, res, parts = _nested_sample(tmp_path)
    lay = res.layouts[0]
    svg = layout_svg(lay, parts, cfg, labels=False).replace('stroke="#222"', 'stroke="none"')
    root = ET.fromstring(svg)
    vb_w = float(root.attrib["viewBox"].split()[2])
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=2000)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    from nestlib.preview import PALETTE as _PALETTE
    fills = [tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in _PALETTE]
    n = sum(c for c, rgb in img.getcolors(maxcolors=1 << 22)
            if any(all(abs(a - b) <= 6 for a, b in zip(rgb, f)) for f in fills))
    expected = sum(parts[p.part_name].area for p in lay.placements)
    assert n / (2000 / vb_w) ** 2 == pytest.approx(expected, rel=0.02)


def test_layout_svg_shows_margin_spacing_and_grain(tmp_path):
    from nestlib.preview import layout_svg
    cfg, res, parts = _nested_sample(tmp_path, margin=15, spacing=12, grain="y", sheet="2540x1290")
    svg = layout_svg(res.layouts[0], parts, cfg)
    assert 'stroke="#c33"' in svg                                  # margin outline drawn
    assert "margin 15.0 mm" in svg and "spacing 12.0 mm" in svg and "grain along Y" in svg
    assert "polka_typ_3 #1 (0\u00b0)" in svg or "polka_typ_3 #1 (180\u00b0)" in svg      # long piece: full label
    assert ">#1</text>" in svg                                                       # narrow pieces: compact label


def test_write_layout_svgs(tmp_path):
    from nestlib.preview import write_layout_svgs
    cfg, res, parts = _nested_sample(tmp_path)
    paths = write_layout_svgs(res.layouts, parts, cfg, tmp_path / "out")
    assert [p.name for p in paths] == ["sheet_01.svg", "sheet_02.svg"] and all(p.stat().st_size > 500 for p in paths)
