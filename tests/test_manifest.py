from pathlib import Path

import pytest

from nestlib.manifest import ManifestError, parse_manifest, read_manifest
from nestlib.models import PartRequest

BASE = Path("/base")


def parse(text, **kw):
    kw.setdefault("must_exist", False)
    return parse_manifest(text, BASE, **kw)


def test_basic():
    reqs = parse("filename,qty\na.dxf,4\nb.dxf,1\n")
    assert reqs == [PartRequest(BASE / "a.dxf", 4), PartRequest(BASE / "b.dxf", 1)]
    assert reqs[0].name == "a"


@pytest.mark.parametrize("delim", [",", ";", "\t"])
def test_delimiters(delim):
    reqs = parse(f"filename{delim}qty\na.dxf{delim}2\n")
    assert reqs == [PartRequest(BASE / "a.dxf", 2)]


def test_header_case_order_and_extra_columns():
    reqs = parse("Note,QTY,FileName\nx,3,a.DXF\n")
    assert reqs == [PartRequest(BASE / "a.DXF", 3)]


def test_comments_blank_lines_and_crlf():
    reqs = parse("# parts\r\n\r\nfilename,qty\r\n# skip\r\na.dxf,2\r\n\r\n")
    assert len(reqs) == 1


def test_quoted_filename_with_delimiter():
    reqs = parse('filename,qty\n"part, big.dxf",2\n')
    assert reqs[0].path == BASE / "part, big.dxf"


def test_absolute_path_kept():
    reqs = parse("filename,qty\n/abs/a.dxf,1\n")
    assert reqs[0].path == Path("/abs/a.dxf")


@pytest.mark.parametrize("qty", ["0", "-1", "2.5", "abc", "", "\u00b2"])
def test_bad_qty(qty):
    with pytest.raises(ManifestError, match="qty must be a positive integer"):
        parse(f"filename,qty\na.dxf,{qty}\n")


def test_non_dxf_extension():
    with pytest.raises(ManifestError, match="not a .dxf file"):
        parse("filename,qty\na.dwg,1\n")


def test_duplicate_entry():
    with pytest.raises(ManifestError, match="already listed on line 2"):
        parse("filename,qty\na.dxf,1\nb.dxf,1\na.dxf,2\n")


def test_empty_and_header_only():
    with pytest.raises(ManifestError, match="empty"):
        parse("\n# nothing\n")
    with pytest.raises(ManifestError, match="no entries"):
        parse("filename,qty\n")


def test_bad_header():
    with pytest.raises(ManifestError, match="missing: qty"):
        parse("filename,count\na.dxf,1\n")
    with pytest.raises(ManifestError, match="missing: filename, qty"):
        parse("a.dxf 3\n")


def test_short_row():
    with pytest.raises(ManifestError, match="expected at least 2 columns"):
        parse("filename,qty\na.dxf\n")


def test_all_problems_reported_together():
    with pytest.raises(ManifestError) as ei:
        parse("filename,qty\na.dxf,0\nb.dwg,1\nc.dxf,x\n")
    assert len(ei.value.problems) == 3
    assert "<manifest>:2" in ei.value.problems[0]


# --------------------------------------------------------------- filesystem
def test_read_manifest_resolves_relative_to_manifest_dir(tmp_path):
    (tmp_path / "a.dxf").write_text("x")
    m = tmp_path / "parts.csv"
    m.write_text("filename;qty\na.dxf;2\n", encoding="utf-8-sig")  # BOM like Excel
    assert read_manifest(m) == [PartRequest(tmp_path / "a.dxf", 2)]


def test_read_manifest_base_dir_override(tmp_path):
    d = tmp_path / "dxf"
    d.mkdir()
    (d / "a.dxf").write_text("x")
    m = tmp_path / "parts.csv"
    m.write_text("filename,qty\na.dxf,1\n")
    assert read_manifest(m, base_dir=d)[0].path == d / "a.dxf"


def test_missing_dxf_reported(tmp_path):
    m = tmp_path / "parts.csv"
    m.write_text("filename,qty\nghost.dxf,1\n")
    with pytest.raises(ManifestError, match="file not found"):
        read_manifest(m)


def test_missing_manifest(tmp_path):
    with pytest.raises(ManifestError, match="cannot read manifest"):
        read_manifest(tmp_path / "nope.csv")


# ------------------------------------------------------------------- grain
from nestlib.models import Grain


@pytest.mark.parametrize("cell, expected", [("along", Grain.ALONG), ("ACROSS", Grain.ACROSS), (" Along ", Grain.ALONG),
                                            ("any", Grain.ANY), ("", Grain.ANY)])
def test_grain_values(cell, expected):
    assert parse(f"filename,qty,grain\na.dxf,2,{cell}\n")[0].grain is expected


def test_grain_column_optional_and_short_rows_mean_any():
    assert parse("filename,qty\na.dxf,2\n")[0].grain is Grain.ANY
    reqs = parse("filename;qty;grain\na.dxf;2;across\nb.dxf;1\n")      # second row has no grain cell
    assert [r.grain for r in reqs] == [Grain.ACROSS, Grain.ANY]


def test_orientation_is_an_alias_for_grain():
    assert parse("filename,qty,Orientation\na.dxf,1,across\n")[0].grain is Grain.ACROSS


def test_grain_column_any_position():
    r = parse("grain;filename;qty\nalong;a.dxf;3\n")[0]
    assert (r.path.name, r.qty, r.grain) == ("a.dxf", 3, Grain.ALONG)


def test_bad_grain_value_names_line():
    with pytest.raises(ManifestError, match=r"<manifest>:2: grain must be along, across or empty, got 'diagonal'"):
        parse("filename,qty,grain\na.dxf,1,diagonal\n")
