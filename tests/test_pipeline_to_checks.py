"""Integration: quantities CSV -> loader -> contours -> checks, on the sample files."""
import shutil
from pathlib import Path

from nestlib.checks import check_parts
from nestlib.config import load_config
from nestlib.contours import build_part
from nestlib.loader import load_dxf
from nestlib.manifest import read_manifest
from nestlib.models import Grain, Severity

DATA = Path(__file__).parent / "data"


def run(tmp_path, csv_text, **cfg_kw):
    for f in DATA.glob("*.dxf"):
        shutil.copy(f, tmp_path / f.name)
    m = tmp_path / "parts.csv"
    m.write_text(csv_text, encoding="utf-8-sig")
    cfg = load_config(overrides={"sheet": "2500x1250", **cfg_kw})
    reqs = read_manifest(m)
    pairs = [(r, build_part(load_dxf(r.path, flatten_tol=cfg.tolerance, units=cfg.units),
                            tolerance=cfg.tolerance, flatten_tol=cfg.tolerance, units=cfg.units)) for r in reqs]
    return reqs, check_parts(pairs, cfg)


CSV = """filename;qty;grain
polka_typ_3.dxf;2;along
przegroda_typ_3.dxf;1;
szuflada_front.dxf;4;across
szuflada_bok.dxf;8;any
"""


def test_whole_chain_with_grain_column(tmp_path):
    reqs, res = run(tmp_path, CSV)
    assert [r.grain for r in reqs] == [Grain.ALONG, Grain.ANY, Grain.ACROSS, Grain.ANY]
    assert res.ok
    by = {i.part.name: i for i in res.items}
    assert by["polka_typ_3"].rotations == (0.0, 180.0)
    assert by["szuflada_front"].rotations == (90.0, 270.0)
    assert by["szuflada_bok"].rotations == (0.0, 90.0, 180.0, 270.0)
    assert [i.qty for i in res.items] == [2, 1, 4, 8]


def test_impossible_grain_request_stops_the_run(tmp_path):
    csv = CSV.replace("polka_typ_3.dxf;2;along", "polka_typ_3.dxf;2;across")
    _, res = run(tmp_path, csv)
    assert not res.ok
    assert [i.code for i in res.issues if i.severity is Severity.ERROR] == ["grain_unsatisfiable"]


def test_ten_mm_margin_rejects_only_the_full_length_shelf(tmp_path):
    _, res = run(tmp_path, CSV, margin=10)
    assert [(i.code, i.part) for i in res.issues if i.severity is Severity.ERROR] == [("part_too_large", "polka_typ_3")]
