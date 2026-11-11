import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest
from PIL import Image

from nestlib import __version__
from nestlib.cli import EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, main

DATA = Path(__file__).parent / "data"
SRC = Path(__file__).parent.parent / "src"
CSV = ("filename;qty;grain\npolka_typ_3.dxf;2;along\nprzegroda_typ_3.dxf;1;\n"
       "szuflada_front.dxf;4;across\nszuflada_bok.dxf;8;\n")


@pytest.fixture
def work(tmp_path):
    for f in DATA.glob("*.dxf"):
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / "parts.csv").write_text(CSV, encoding="utf-8")
    return tmp_path


def run(*argv, cwd=None):
    out, err = io.StringIO(), io.StringIO()
    old = os.getcwd()
    try:
        if cwd:
            os.chdir(cwd)
        code = main([str(a) for a in argv], out, err)
    finally:
        os.chdir(old)
    return code, out.getvalue(), err.getvalue()


def base(work, *extra):
    """Standard job; PNG rendering is off unless the test passes png=True via base_png()."""
    return (work / "parts.csv", "--sheet", "2500x1250", "-o", work / "out", "--no-png", *extra)


def base_png(work, *extra):
    return (work / "parts.csv", "--sheet", "2500x1250", "-o", work / "out", *extra)


# ----------------------------------------------------------------- happy path
def test_full_run_writes_dxf_and_png_per_sheet(work):
    code, out, err = run(*base_png(work))
    assert code == EXIT_OK and err == ""
    files = sorted(p.name for p in (work / "out").iterdir())
    assert files == ["sheet_01.dxf", "sheet_01.png", "sheet_02.dxf", "sheet_02.png"]
    assert ezdxf.readfile(work / "out" / "sheet_01.dxf").header["$INSUNITS"] == 4
    assert Image.open(work / "out" / "sheet_01.png").size[0] == 2400


def test_summary_reports_sheets_and_grain_orientation(work):
    code, out, err = run(*base(work))
    assert "Nested 15 pieces of 4 parts on 2 sheets (2500.0 x 1250.0 mm, margin 0.0 mm, fit spacing 10.0 mm)" in out
    assert "Sheet 1: 12 pieces" in out and "Sheet 2: 3 pieces" in out and "87.0% material" in out
    assert "which runs along the X axis (sheet width)" in out
    lines = {l.split()[0]: l for l in out.splitlines() if l.startswith("  ") and "grain" in l}
    assert "longer side along grain" in lines["polka_typ_3"] and "[required: along]" in lines["polka_typ_3"]
    assert "longer side across grain" in lines["szuflada_front"] and "[required: across]" in lines["szuflada_front"]
    assert "rotation 90" in lines["szuflada_front"] and "[required" not in lines["szuflada_bok"]


def test_verbose_lists_every_piece_and_shows_info(work):
    code, out, err = run(*base(work, "-v"))
    assert out.count("rotation") >= 15 + 4 and "polka_typ_3 #1" in out and "przegroda_typ_3 #1" in out
    assert "info: " in err and "duplicate segment(s) removed" in err
    code, out, err = run(*base(work))
    assert "info:" not in err                     # info only with -v


def test_quiet_prints_nothing_on_success_but_still_writes(work):
    code, out, err = run(*base(work, "-q"))
    assert code == EXIT_OK and out == "" and err == ""
    assert (work / "out" / "sheet_01.dxf").exists()


def test_no_png(work):
    code, *_ = run(*base(work))
    assert code == EXIT_OK and sorted(p.suffix for p in (work / "out").iterdir()) == [".dxf", ".dxf"]


def test_png_width(work):
    run(*base_png(work, "--png-width", "900"))
    assert Image.open(work / "out" / "sheet_01.png").size[0] == 900


def test_default_output_dir_is_out_in_the_current_directory(work):
    code, *_ = run(work / "parts.csv", "--sheet", "2500x1250", "--no-png", cwd=work)
    assert code == EXIT_OK and (work / "out" / "sheet_01.dxf").exists()


def test_output_dir_is_created(work):
    code, *_ = run(work / "parts.csv", "--sheet", "2500x1250", "-o", work / "deep" / "er" / "out", "--no-png")
    assert code == EXIT_OK and (work / "deep" / "er" / "out" / "sheet_02.dxf").exists()


# ------------------------------------------------------------ options reach the result
def test_inches_flag(work):
    code, out, err = run(work / "parts.csv", "--in", "--sheet", "98.425197x49.212598", "--fit-spacing", "0.3937",
                         "-o", work / "out", "--no-png")
    assert code == EXIT_OK, err
    assert "(98.43 x 49.21 in, margin 0.00 in, fit spacing 0.39 in)" in out
    doc = ezdxf.readfile(work / "out" / "sheet_01.dxf")
    assert doc.header["$INSUNITS"] == 1
    sheet = next(e for e in doc.modelspace() if e.dxf.layer == "SHEET")
    assert list(sheet.get_points("xy"))[2] == pytest.approx((98.425197, 49.212598))


def test_margin_and_spacing_are_applied(work):
    code, out, err = run(work / "parts.csv", "--sheet", "2540x1290", "--margin", "15", "--fit-spacing", "12", "-o", work / "out", "--no-png")
    assert code == EXIT_OK and "margin 15.0 mm, fit spacing 12.0 mm" in out
    pts = [e for e in ezdxf.readfile(work / "out" / "sheet_01.dxf").modelspace() if e.dxf.layer == "CUT_OUTER"]
    xs = [p[0] for e in pts for p in e.get_points("xy")]
    ys = [p[1] for e in pts for p in e.get_points("xy")]
    assert min(xs) >= 15 - 1e-6 and min(ys) >= 15 - 1e-6 and max(xs) <= 2525 + 1e-6 and max(ys) <= 1275 + 1e-6


def test_margin_makes_the_full_length_shelf_fail_and_nothing_is_written(work):
    code, out, err = run(*base(work, "--margin", "10"))
    assert code == EXIT_PROBLEMS and out == ""
    assert "error: polka_typ_3: part 2500.000 x 300.000 mm does not fit" in err and "it would fit without the margin" in err
    assert "stopped: 1 error, 0 warnings; nothing was written" in err
    assert not (work / "out").exists()


def test_grain_axis_y_flips_orientation_text(work):
    (work / "parts.csv").write_text("filename,qty\nszuflada_front.dxf,2\n", encoding="utf-8")
    _, out_x, _ = run(*base(work))
    _, out_y, _ = run(*base(work, "--grain", "y"))
    assert "longer side along grain" in out_x and "longer side across grain" in out_y


def test_rotations_option_restricts_rotation(work):
    (work / "parts.csv").write_text("filename,qty\nszuflada_front.dxf,4\n", encoding="utf-8")
    _, out, _ = run(*base(work, "--rotations", "0,180", "-v"))
    assert "rotation     0" in out or "rotation 0" in out
    assert "rotation    90" not in out and "rotation   270" not in out


def test_layer_names_from_the_command_line(work):
    run(*base(work, "--layer-cut-outer", "TNIJ", "--layer-labels", "OPISY"))
    layers = {e.dxf.layer for e in ezdxf.readfile(work / "out" / "sheet_01.dxf").modelspace()}
    assert layers == {"SHEET", "TNIJ", "OPISY"}


def test_dxf_version(work):
    run(*base(work, "--dxf-version", "R2018"))
    assert ezdxf.readfile(work / "out" / "sheet_01.dxf").dxfversion == "AC1032"


# ------------------------------------------------------------------ tool checks
def test_tool_diameter_warns_about_slot_corners_but_succeeds(work):
    code, out, err = run(*base(work, "--tool-diameter", "8"))
    assert code == EXIT_OK
    assert "warning: polka_typ_3: 8 inner corner(s) tighter than the tool radius 4.000 mm" in err
    assert "warning: przegroda_typ_3: 10 inner corner(s)" in err
    code, out, err = run(*base(work, "--tool-diameter", "8", "-q"))
    assert code == EXIT_OK and err == "" and out == ""         # --quiet hides warnings and the summary


def test_big_bit_stops_the_run(work):
    code, out, err = run(*base(work, "--tool-diameter", "20"))
    assert code == EXIT_PROBLEMS
    assert "error: polka_typ_3: 4 region(s) the 20.000 mm tool cannot reach" in err and "nothing was written" in err
    assert not (work / "out").exists()


def test_spacing_below_tool_diameter(work):
    code, out, err = run(*base(work, "--tool-diameter", "8", "--fit-spacing", "5"))
    assert code == EXIT_PROBLEMS and "fit spacing 5.000 mm is smaller than the tool diameter 8.000 mm" in err


def test_without_tool_diameter_no_tool_messages(work):
    code, out, err = run(*base(work))
    assert "tool" not in err


# ------------------------------------------------------------- bad input
def test_missing_sheet_is_a_usage_error(work):
    code, out, err = run(work / "parts.csv")
    assert code == EXIT_USAGE and "sheet size is required" in err


def test_bad_option_values(work):
    assert run(*base(work, "--grain", "z"))[0] == EXIT_USAGE
    assert run(*base(work, "--margin", "abc"))[0] == EXIT_USAGE
    assert run(*base(work, "--mm", "--in"))[0] == EXIT_USAGE
    code, out, err = run(*base(work, "--margin", "2000"))
    assert code == EXIT_USAGE and "no usable sheet area" in err
    code, out, err = run(*base_png(work, "--png-width", "50"))
    assert code == EXIT_USAGE and "--png-width" in err


def test_missing_and_broken_files_are_all_reported_together(work):
    (work / "broken.dxf").write_text("not a dxf\n")
    (work / "parts.csv").write_text("filename,qty\nghost.dxf,1\nbroken.dxf,1\nszuflada_bok.dxf,1\n", encoding="utf-8")
    code, out, err = run(*base(work))
    assert code == EXIT_PROBLEMS
    assert "file not found" in err and "ghost.dxf" in err                 # manifest stage stops first
    ezdxf_open_contour(work / "open.dxf")
    (work / "parts.csv").write_text("filename,qty\nbroken.dxf,1\nopen.dxf,1\nszuflada_bok.dxf,1\n", encoding="utf-8")
    code, out, err = run(*base(work))
    assert code == EXIT_PROBLEMS
    assert "error: broken: " in err and "error: open: contour is not closed" in err   # both reported in one run
    assert "2 errors" in err and not (work / "out").exists()


def ezdxf_open_contour(path):
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    for a, b in [((0, 0), (100, 0)), ((100, 0), (100, 50)), ((100, 50), (0, 50))]:
        msp.add_line(a, b)
    doc.saveas(path)


def test_bad_manifest_rows_are_listed(work):
    (work / "parts.csv").write_text("filename,qty,grain\nszuflada_bok.dxf,0,\nszuflada_front.dxf,2,sideways\n", encoding="utf-8")
    code, out, err = run(*base(work))
    assert code == EXIT_PROBLEMS and "qty must be a positive integer" in err and "grain must be along, across or empty" in err


def test_unsatisfiable_grain_rule(work):
    (work / "parts.csv").write_text("filename,qty,grain\npolka_typ_3.dxf,1,across\n", encoding="utf-8")
    code, out, err = run(*base(work))
    assert code == EXIT_PROBLEMS and "grain rule 'across'" in err and "longer side along the grain" in err


def test_missing_csv(work):
    code, out, err = run(work / "nope.csv", "--sheet", "2500x1250")
    assert code == EXIT_PROBLEMS and "cannot read manifest" in err


# ------------------------------------------------------- config file, dxf dir
def test_config_file_with_command_line_override(work):
    (work / "job.toml").write_text('sheet = "2540x1290"\nmargin = 5\nfit_spacing = 8\n[layers]\nlabels = "OPISY"\n', encoding="utf-8")
    code, out, err = run(work / "parts.csv", "--config", work / "job.toml", "--margin", "12", "-o", work / "out", "--no-png")
    assert code == EXIT_OK, err
    assert "margin 12.0 mm, fit spacing 8.0 mm" in out                                   # CLI beat the file, file filled the rest
    assert "OPISY" in {e.dxf.layer for e in ezdxf.readfile(work / "out" / "sheet_01.dxf").modelspace()}


def test_dxf_dir(work):
    parts = work / "dxf"
    parts.mkdir()
    for f in work.glob("*.dxf"):
        shutil.move(f, parts / f.name)
    code, out, err = run(work / "parts.csv", "--sheet", "2500x1250", "--dxf-dir", parts, "-o", work / "out", "--no-png")
    assert code == EXIT_OK, err


def test_check_only_writes_nothing(work):
    code, out, err = run(*base(work, "--check-only"))
    assert code == EXIT_OK and "Checked 4 parts (15 pieces)" in out and "nothing nested or written" in out
    assert not (work / "out").exists()


def test_stale_files_from_a_previous_run_are_flagged(work):
    (work / "out").mkdir()
    (work / "out" / "sheet_03.dxf").write_text("old")
    (work / "out" / "sheet_03.png").write_text("old")
    code, out, err = run(*base_png(work, "--png-width", "400"))
    assert code == EXIT_OK and "earlier run" in err and "sheet_03.dxf" in err and "sheet_03.png" in err
    assert (work / "out" / "sheet_03.dxf").read_text() == "old"          # never deleted
    code, out, err = run(*base(work))                                    # without PNG output a stale PNG is not our business
    assert "sheet_03.dxf" in err and "sheet_03.png" not in err


def test_second_run_overwrites_cleanly(work):
    run(*base(work))
    first = (work / "out" / "sheet_01.dxf").read_bytes()
    code, out, err = run(*base(work))
    assert code == EXIT_OK and err == "" and (work / "out" / "sheet_01.dxf").read_bytes()[:200] == first[:200]


# ---------------------------------------------------------------- meta
def test_version_and_help(work):
    code, out, err = run("--version")
    assert code == 0 and out.strip() == f"dxfnest {__version__}"
    code, out, err = run("--help")
    assert code == 0 and "--fit-spacing" in out and "--tool-diameter" in out and "--in" in out and "grain" in out
    assert run()[0] == EXIT_USAGE                                        # missing PARTS.csv


def test_missing_pillow_is_reported_before_any_work(work, monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)                 # makes `import PIL` fail
    code, out, err = run(*base_png(work))
    assert code == EXIT_USAGE and "Pillow" in err and "--no-png" in err and "pip install pillow" in err
    assert not (work / "out").exists()                            # failed fast: nothing was nested or written
    code, out, err = run(*base(work))                             # --no-png does not need Pillow at all
    assert code == EXIT_OK and (work / "out" / "sheet_01.dxf").exists()
    code, out, err = run(*base_png(work, "--check-only"))         # nor does checking
    assert code == EXIT_OK
