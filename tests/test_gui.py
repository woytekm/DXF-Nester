"""The real GUI, run under a virtual display (xvfb-run) and driven through its own widgets/callbacks.

Skipped where xvfb-run or dearpygui is missing (for example on macOS, where you just use the window).
Every scenario also saves a screenshot; the pixel checks below make sure things are really drawn.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

XVFB = shutil.which("xvfb-run")
HARNESS = Path(__file__).parent / "gui_harness.py"
pytestmark = pytest.mark.skipif(not XVFB or importlib.util.find_spec("dearpygui") is None,
                                reason="needs xvfb-run and dearpygui")

PLYWOOD = (0xE8, 0xD3, 0xA8)       # fill colour of the first part in the previews


def run_scenario(name, tmp_path_factory):
    out = tmp_path_factory.mktemp(name)
    r = subprocess.run([XVFB, "-a", "-s", "-screen 0 1400x1000x24", sys.executable, str(HARNESS), name, str(out)],
                       capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, r.stderr[-3000:]
    line = next((l for l in r.stdout.splitlines() if l.startswith("FACTS ")), None)
    assert line, r.stdout[-2000:] + r.stderr[-2000:]
    return json.loads(line[6:]), out


@pytest.fixture(scope="module")
def scenario(tmp_path_factory):
    cache = {}
    def get(name):
        if name not in cache:
            cache[name] = run_scenario(name, tmp_path_factory)
        return cache[name]
    return get


def colour_pixels(path, rgb, tol=8):
    img = Image.open(path).convert("RGB")
    return sum(n for n, c in img.getcolors(maxcolors=1 << 22) if all(abs(a - b) <= tol for a, b in zip(c, rgb)))


def distinct_colours(path):
    return len(Image.open(path).convert("RGB").getcolors(maxcolors=1 << 22))


# ------------------------------------------------------------------- drawing
def test_empty_window_is_drawn(scenario):
    facts, out = scenario("empty")
    assert (out / "empty.png").stat().st_size > 5000 and distinct_colours(out / "empty.png") > 10


def test_loading_a_csv_fills_the_table(scenario):
    facts, out = scenario("loaded")
    assert facts["rows"] == 4 and (out / "loaded.png").exists()
    assert colour_pixels(out / "loaded.png", PLYWOOD) == 0                         # no preview yet


def test_restore_brings_back_the_previous_session(scenario):
    f, _ = scenario("restore")
    assert (f["rows"], f["qty"], f["grains"]) == (2, [3, 1], ["any", "across"])
    assert (f["sheet"], f["margin"], f["grain"], f["tool"]) == ("3050x1525", "7", "y", "6")
    assert f["totals"] == "2 file(s), 4 piece(s) in total"


# ----------------------------------------------------------------------- runs
def test_preview_run(scenario):
    f, out = scenario("preview")
    assert (f["status"], f["sheets"], f["tabs"]) == ("ok", 2, 2)
    assert f["row_levels"] == ["ok"] * 4 and f["row_status"] == ["ok"] * 4
    assert f["buttons_disabled_while_running"] is True and f["buttons_enabled_after"] is True    # the window stays usable, no double runs
    assert f["files"] == [] and "Nothing was written" in f["status_text"]                        # preview touches no disk
    assert colour_pixels(out / "preview.png", PLYWOOD) > 5000                                    # the sheet picture is really on screen


def test_failed_run_flags_the_part_and_explains(scenario):
    f, out = scenario("errors")
    assert f["status"] == "parts_failed" and f["sheets"] == 0 and f["tabs"] == 0
    assert f["row_levels"] == ["error", "", "", ""] and f["row_status"][0] == "too large for the sheet"
    assert any("it would fit without the margin" in line for line in f["log"])
    assert "nothing was nested or written" in f["status_text"] and f["buttons_enabled_after"] is True
    assert colour_pixels(out / "errors.png", PLYWOOD) == 0


def test_write_run_creates_the_files_and_shows_tool_warnings(scenario):
    f, out = scenario("written")
    assert f["files"] == ["sheet_01.dxf", "sheet_01.png", "sheet_02.dxf", "sheet_02.png"]
    assert f["row_levels"] == ["warning", "warning", "ok", "ok"]
    assert f["row_status"][:2] == ["inner corners rounded"] * 2
    assert "Files are in" in f["status_text"] and any("inner corner(s)" in line for line in f["log"])


def test_polish_file_names(scenario):
    f, out = scenario("polish")
    assert f["names"][0] == "P\u00f3\u0142ka_typ_3.dxf" and "P\u00f3\u0142ka_typ_3" in f["part_names"] and f["status"] == "ok"


# ---------------------------------------------------------------- interaction
def test_editing_the_table(scenario):
    f, _ = scenario("edit")
    assert f["after_edit"] == [["polka_typ_3.dxf", 7, "across"]]
    assert f["totals_after_qty"] == "4 file(s), 20 piece(s) in total"                # 7 + 1 + 4 + 8
    assert f["rows_after_remove"] == 3 and f["table_widgets_after_remove"] == 3
    assert "1 already in the table" in f["status_after_dup"] and "readme.txt" in f["status_after_dup"]
    assert f["saved_csv"].splitlines() == ["filename;qty;grain", "polka_typ_3.dxf;7;across", "przegroda_typ_3.dxf;1;", "szuflada_front.dxf;4;across"]
    assert f["status_after_save"].startswith("Saved 3 part(s)")
    assert f["rows_after_clear"] == 0 and f["totals_after_clear"] == "No parts yet."


def test_dialogs_open_and_their_callbacks_work(scenario):
    f, out = scenario("dialog")
    assert f["dxf_dialog_shown"] and f["save_dialog_shown"] and f["save_default"].endswith("parts.csv")
    assert f["rows_after_dxf_dialog"] == 2 and f["rows_after_csv_dialog"] == 4
    assert f["outdir_after_dir_dialog"].endswith("elsewhere")
    assert (out / "dlg_dxf.png").exists() and (out / "dlg_save.png").exists()
