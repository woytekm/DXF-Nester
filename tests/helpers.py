"""Small builders shared by the nesting tests."""
from pathlib import Path

from nestlib.checks import NestItem
from nestlib.config import load_config
from nestlib.contours import build_part
from nestlib.models import Grain, LineSeg, RawPart

ALL4 = (0.0, 90.0, 180.0, 270.0)


def poly_part(points, name):
    prims = tuple(LineSeg(points[i], points[(i + 1) % len(points)]) for i in range(len(points)))
    return build_part(RawPart(name, Path(f"{name}.dxf"), prims, "millimetres"))


def rect_part(w, h, name="r"):
    return poly_part([(0, 0), (w, 0), (w, h), (0, h)], name)


def l_part(name="L", size=100.0, arm=40.0):
    """L-shaped part: size x size box, arms of thickness `arm` along the bottom and left."""
    return poly_part([(0, 0), (size, 0), (size, arm), (arm, arm), (arm, size), (0, size)], name)


def item(part, qty=1, rotations=ALL4, grain=Grain.ANY):
    return NestItem(part, qty, grain, tuple(rotations))


def rect_item(w, h, qty=1, rotations=ALL4, name=None):
    return item(rect_part(w, h, name or f"r{int(w)}x{int(h)}"), qty, rotations)


def cfg(sheet="1000x1000", **kw):
    return load_config(overrides={"sheet": sheet, **kw})


# ------------------------------------------------ the four real sample parts, nested
import shutil

DATA = Path(__file__).parent / "data"
SAMPLE_CSV = ("filename,qty\npolka_typ_3.dxf,2\nprzegroda_typ_3.dxf,1\n"
              "szuflada_front.dxf,4\nszuflada_bok.dxf,8\n")


def nested_sample(tmp_path, margin=0, spacing=10, grain="x", sheet="2500x1250", **cfg_kw):
    """Run manifest -> loader -> contours -> checks -> rectangle nester on tests/data.
    Returns (config, NestResult, {name: Part}, check items)."""
    from nestlib.checks import check_parts
    from nestlib.loader import load_dxf
    from nestlib.manifest import read_manifest
    from nestlib.nest_rect import nest_rectangles

    for f in DATA.glob("*.dxf"):
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / "parts.csv").write_text(SAMPLE_CSV, encoding="utf-8")
    c = load_config(overrides={"sheet": sheet, "margin": margin, "fit_spacing": spacing, "grain": grain, **cfg_kw})
    pairs = [(r, build_part(load_dxf(r.path))) for r in read_manifest(tmp_path / "parts.csv")]
    chk = check_parts(pairs, c)
    assert chk.ok, [i.message for i in chk.issues if i.severity.value == "error"]
    res = nest_rectangles(chk.items, c)
    return c, res, {p.name: p for _, p in pairs}
