from pathlib import Path

import pytest
from helpers import cfg, item, nested_sample
from PIL import Image
from shapely.geometry import Point

from nestlib.contours import build_part
from nestlib.layout import place_polygon
from nestlib.models import ArcSeg, CircleSeg, LineSeg, RawPart
from nestlib.nest_rect import nest_rectangles
from nestlib.png_render import (PALETTE, SHEET_COLOUR, MARGIN_COLOUR, png_geometry, render_layout_png,
                                write_layout_pngs, _font)

BARE = dict(outline=False, labels=False, caption=False)


def hexrgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def close(a, b, tol=6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    d = tmp_path_factory.mktemp("png")
    c, res, parts = nested_sample(d)[:3]
    return c, res, parts


def colour_of(parts, name):
    return hexrgb(PALETTE[sorted(parts).index(name)])


# ------------------------------------------------------------------ geometry
def test_png_geometry_mapping():
    c = cfg("2000x1000")
    g = png_geometry(c.sheet, 1000)
    assert g.width == 1000 and g.scale == pytest.approx((1000 - 2 * g.pad) / 2000)
    assert g.px(0, 1000, 1000) == pytest.approx((g.pad, g.pad))                    # top-left of the sheet
    assert g.px(2000, 0, 1000) == pytest.approx((1000 - g.pad, g.pad + 1000 * g.scale))
    assert g.height == round(2 * g.pad + 1000 * g.scale + g.caption)


def test_image_size_follows_the_width(sample, tmp_path):
    c, res, parts = sample
    for w in (800, 2400):
        p = render_layout_png(res.layouts[0], parts, c, tmp_path / f"w{w}.png", width_px=w)
        assert Image.open(p).size == (w, png_geometry(c.sheet, w).height)


# --------------------------------------------------------------- pixel truth
def test_filled_area_equals_part_material(sample, tmp_path):
    c, res, parts = sample
    for layout in res.layouts:
        p = render_layout_png(layout, parts, c, tmp_path / "a.png", width_px=2000, colour_order=sorted(parts), **BARE)
        img = Image.open(p).convert("RGB")
        fills = [hexrgb(h) for h in PALETTE]
        n = sum(cnt for cnt, rgb in img.getcolors(maxcolors=1 << 22) if any(close(rgb, f) for f in fills))
        scale = png_geometry(c.sheet, 2000).scale
        want = sum(parts[pl.part_name].area for pl in layout.placements)
        assert n / scale ** 2 == pytest.approx(want, rel=0.01)


def test_pieces_notches_and_orientation(sample, tmp_path):
    c, res, parts = sample
    layout = res.layouts[0]
    p = render_layout_png(layout, parts, c, tmp_path / "b.png", width_px=2400, colour_order=sorted(parts), **BARE)
    img = Image.open(p).convert("RGB")
    g = png_geometry(c.sheet, 2400)
    px = lambda x, y: img.getpixel(tuple(round(v) for v in g.px(x, y, c.sheet.height)))
    for pl in layout.placements:
        poly = place_polygon(parts[pl.part_name], pl)
        inside = poly.representative_point()
        assert close(px(inside.x, inside.y), colour_of(parts, pl.part_name)), pl            # solid: the part's colour
        # a point in the bounding box but outside the outline (a slot / notch) shows the sheet
        x0, y0, x1, y1 = poly.bounds
        gap = next(((x, y) for i in range(1, 40) for j in range(1, 40)
                    for x, y in [(x0 + (x1 - x0) * i / 40, y0 + (y1 - y0) * j / 40)]
                    if not poly.buffer(1.5).contains(Point(x, y)) and c.sheet.margin <= x <= c.sheet.width), None)
        if gap:
            assert close(px(*gap), SHEET_COLOUR), (pl, gap)
    # the sheet's lower-left corner is the bottom of the image: first Półka-like piece sits there
    low = min(layout.placements, key=lambda q: (q.y, q.x))
    assert close(px(low.x + 5, low.y + 5), colour_of(parts, low.part_name))


def test_holes_show_the_sheet(tmp_path):
    r, w, h = 10.0, 100.0, 60.0
    prims = [LineSeg((r, 0), (w - r, 0)), LineSeg((w, r), (w, h - r)), LineSeg((w - r, h), (r, h)), LineSeg((0, h - r), (0, r)),
             ArcSeg((w - r, r), r, 270, 0), ArcSeg((w - r, h - r), r, 0, 90), ArcSeg((r, h - r), r, 90, 180),
             ArcSeg((r, r), r, 180, 270), CircleSeg((50, 30), 15)]
    part = build_part(RawPart("plate", Path("plate.dxf"), tuple(prims), "mm"))
    c = cfg("200x100", margin=0, fit_spacing=0)
    res = nest_rectangles([item(part, 1, rotations=(0.0,))], c)
    p = render_layout_png(res.layouts[0], {"plate": part}, c, tmp_path / "h.png", width_px=1600, **BARE)
    img = Image.open(p).convert("RGB")
    g = png_geometry(c.sheet, 1600)
    px = lambda x, y: img.getpixel(tuple(round(v) for v in g.px(x, y, 100)))
    assert close(px(50, 30), SHEET_COLOUR)                        # hole centre
    assert close(px(50 + 15 + 3, 30), hexrgb(PALETTE[0]))        # just outside the hole: material
    assert close(px(1, 1), SHEET_COLOUR)                          # rounded corner (R10) cut away at the plate's corner
    assert close(px(5, 20), hexrgb(PALETTE[0]))


# -------------------------------------------------------- margin, labels, text
def test_margin_line_only_when_there_is_a_margin(tmp_path):
    (tmp_path / "m").mkdir()
    (tmp_path / "n").mkdir()
    c, res, parts = nested_sample(tmp_path / "m", margin=15, sheet="2540x1290")[:3]
    with_margin = Image.open(render_layout_png(res.layouts[0], parts, c, tmp_path / "m.png", **BARE)).convert("RGB")
    assert sum(cnt for cnt, rgb in with_margin.getcolors(maxcolors=1 << 22) if close(rgb, MARGIN_COLOUR, 40)) > 500
    c0, res0, parts0 = nested_sample(tmp_path / "n", margin=0)[:3]
    no_margin = Image.open(render_layout_png(res0.layouts[0], parts0, c0, tmp_path / "n.png", **BARE)).convert("RGB")
    assert sum(cnt for cnt, rgb in no_margin.getcolors(maxcolors=1 << 22) if close(rgb, MARGIN_COLOUR, 40)) == 0


def test_labels_and_caption_add_ink_only_where_expected(sample, tmp_path):
    c, res, parts = sample
    layout = res.layouts[0]
    bare = Image.open(render_layout_png(layout, parts, c, tmp_path / "1.png", **BARE)).convert("RGB")
    lab = Image.open(render_layout_png(layout, parts, c, tmp_path / "2.png", outline=False, labels=True, caption=False)).convert("RGB")
    cap = Image.open(render_layout_png(layout, parts, c, tmp_path / "3.png", outline=False, labels=False, caption=True)).convert("RGB")
    g = png_geometry(c.sheet, 2400)
    from PIL import ImageChops
    d_lab = ImageChops.difference(bare, lab).getbbox()
    d_cap = ImageChops.difference(bare, cap).getbbox()
    assert d_lab is not None and d_cap is not None
    sheet_bottom = g.pad + c.sheet.height * g.scale
    assert d_lab[3] <= sheet_bottom + 2                       # labels stay on the sheet
    assert d_cap[1] >= sheet_bottom                           # the caption is entirely below it


def test_polish_names_render_with_and_without_a_unicode_font(tmp_path):
    part = build_part(RawPart("Pó\u0142ka_typ_3", Path("x.dxf"),
                      tuple(LineSeg(a, b) for a, b in [((0, 0), (800, 0)), ((800, 0), (800, 300)), ((800, 300), (0, 300)), ((0, 300), (0, 0))]), "mm"))
    c = cfg("1000x500")
    res = nest_rectangles([item(part, 1)], c)
    parts = {part.name: part}
    a = render_layout_png(res.layouts[0], parts, c, tmp_path / "u.png")
    b = render_layout_png(res.layouts[0], parts, c, tmp_path / "f.png", font_path="/nonexistent/font.ttf")   # -> built-in font, folded text
    assert a.stat().st_size > 1000 and b.stat().st_size > 1000
    font, ok = _font(20, "/nonexistent/font.ttf")
    assert ok is False


def test_font_environment_override(monkeypatch):
    _font.cache_clear()
    monkeypatch.setenv("DXFNEST_FONT", "/definitely/missing.ttf")
    _, ok = _font(14, None)          # missing override is skipped, system candidates still work if present
    _font.cache_clear()
    assert isinstance(ok, bool)


def test_write_layout_pngs_names_and_colour_stability(sample, tmp_path):
    c, res, parts = sample
    paths = write_layout_pngs(res.layouts, parts, c, tmp_path / "out", width_px=1200)
    assert [p.name for p in paths] == ["sheet_01.png", "sheet_02.png"]
    # the same part has the same colour on every sheet
    fronts = [Image.open(p).convert("RGB") for p in paths]
    want = colour_of(parts, "szuflada_front")
    for img in fronts:
        assert any(close(rgb, want) for _, rgb in img.getcolors(maxcolors=1 << 22))


def test_render_is_fast_enough(sample, tmp_path):
    import time
    c, res, parts = sample
    t = time.perf_counter()
    render_layout_png(res.layouts[0], parts, c, tmp_path / "t.png", width_px=3000)
    assert time.perf_counter() - t < 10
