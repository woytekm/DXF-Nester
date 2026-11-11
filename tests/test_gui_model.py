import json
import shutil
from pathlib import Path

import pytest

from nestlib.config import ConfigError
from nestlib.gui_model import (GRAIN_CHOICES, GuiSettings, PartsTable, Row, load_state, save_state)
from nestlib.manifest import ManifestError
from nestlib.models import Grain, Issue, Severity

DATA = Path(__file__).parent / "data"


@pytest.fixture
def folder(tmp_path):
    for f in DATA.glob("*.dxf"):
        shutil.copy(f, tmp_path / f.name)
    return tmp_path


def err(part, msg="boom"):
    return Issue(Severity.ERROR, "x", msg, part=part)


# ------------------------------------------------------------------ adding
def test_add_files_defaults(folder):
    t = PartsTable()
    r = t.add_files([folder / "polka_typ_3.dxf", folder / "szuflada_bok.dxf"])
    assert r.added == 2 and not r.duplicates and not r.rejected
    assert [(x.name, x.qty, x.grain) for x in t.rows] == [("polka_typ_3.dxf", 1, Grain.ANY), ("szuflada_bok.dxf", 1, Grain.ANY)]
    assert all(x.level == "" for x in t.rows)


def test_add_files_skips_duplicates_and_non_dxf(folder):
    t = PartsTable()
    (folder / "notes.txt").write_text("x")
    t.add_files([folder / "polka_typ_3.dxf"])
    r = t.add_files([folder / "polka_typ_3.dxf", str(folder / "polka_typ_3.dxf"), folder / "notes.txt", folder / "przegroda_typ_3.DXF"])
    assert (r.added, r.duplicates, r.rejected) == (1, ["polka_typ_3.dxf", "polka_typ_3.dxf"], ["notes.txt"])
    assert len(t) == 2                                       # upper-case extension is accepted


def test_duplicate_detection_resolves_paths(folder):
    t = PartsTable()
    t.add_files([folder / "polka_typ_3.dxf"])
    assert t.add_files([folder / "." / "polka_typ_3.dxf"]).duplicates == ["polka_typ_3.dxf"]


def test_missing_file_is_flagged(tmp_path):
    t = PartsTable()
    t.add_files([tmp_path / "ghost.dxf"])
    assert t.rows[0].level == "error" and t.rows[0].status == "file not found"


# ------------------------------------------------------------------ editing
def test_set_qty_validates(folder):
    t = PartsTable(); t.add_files([folder / "polka_typ_3.dxf"])
    assert t.set_qty(0, 5) is None and t.rows[0].qty == 5
    assert t.set_qty(0, " 12 ") is None and t.rows[0].qty == 12
    for bad in (0, -3, "abc", "2.5", ""):
        assert t.set_qty(0, bad) is not None
    assert t.rows[0].qty == 12                               # bad input never changes the value
    assert "at least 1" in t.set_qty(0, 0)


def test_set_grain(folder):
    t = PartsTable(); t.add_files([folder / "polka_typ_3.dxf"])
    assert GRAIN_CHOICES == ("any", "along", "across")
    assert t.set_grain(0, "along") is None and t.rows[0].grain is Grain.ALONG
    assert t.set_grain(0, "ACROSS") is None and t.rows[0].grain is Grain.ACROSS
    assert t.set_grain(0, "") is None and t.rows[0].grain is Grain.ANY
    assert "grain must be one of" in t.set_grain(0, "sideways") and t.rows[0].grain is Grain.ANY


def test_remove_clear_and_totals(folder):
    t = PartsTable(); t.add_files(sorted(folder.glob("*.dxf")))
    for i in range(len(t)):
        t.set_qty(i, i + 1)
    assert t.total_pieces() == 10
    t.remove(1)
    assert len(t) == 3 and t.total_pieces() == 8
    t.clear()
    assert len(t) == 0


def test_to_requests(folder):
    t = PartsTable(); t.add_files([folder / "polka_typ_3.dxf"])
    t.set_qty(0, 4); t.set_grain(0, "across")
    (rq,) = t.to_requests()
    assert (rq.path, rq.qty, rq.grain) == (folder / "polka_typ_3.dxf", 4, Grain.ACROSS)


# ---------------------------------------------------------------------- CSV
def test_load_csv_in_the_command_line_format(folder):
    (folder / "parts.csv").write_text("filename;qty;grain\npolka_typ_3.dxf;2;along\nszuflada_bok.dxf;8;\n", encoding="utf-8-sig")
    t = PartsTable()
    assert t.load_csv(folder / "parts.csv") == 2
    assert [(r.name, r.qty, r.grain.value) for r in t.rows] == [("polka_typ_3.dxf", 2, "along"), ("szuflada_bok.dxf", 8, "any")]
    assert all(r.level == "" for r in t.rows)


def test_load_csv_replaces_or_appends(folder):
    (folder / "a.csv").write_text("filename,qty\npolka_typ_3.dxf,2\n")
    (folder / "b.csv").write_text("filename,qty\npolka_typ_3.dxf,9\nszuflada_bok.dxf,1\n")
    t = PartsTable(); t.load_csv(folder / "a.csv")
    assert t.load_csv(folder / "b.csv", replace=False) == 1            # the duplicate row is skipped, existing qty kept
    assert [(r.name, r.qty) for r in t.rows] == [("polka_typ_3.dxf", 2), ("szuflada_bok.dxf", 1)]
    t.load_csv(folder / "b.csv")
    assert [(r.name, r.qty) for r in t.rows] == [("polka_typ_3.dxf", 9), ("szuflada_bok.dxf", 1)]


def test_load_csv_with_moved_files_still_loads_and_flags_them(tmp_path):
    (tmp_path / "p.csv").write_text("filename,qty\nghost.dxf,3\n")
    t = PartsTable(); t.load_csv(tmp_path / "p.csv")
    assert t.rows[0].qty == 3 and t.rows[0].status == "file not found"


def test_load_csv_errors_are_the_manifest_errors(folder):
    (folder / "bad.csv").write_text("filename,qty\npolka_typ_3.dxf,0\n")
    with pytest.raises(ManifestError, match="qty must be a positive integer"):
        PartsTable().load_csv(folder / "bad.csv")


def test_save_csv_round_trip_with_relative_names(folder):
    t = PartsTable(); t.add_files(sorted(folder.glob("*.dxf")))
    t.set_qty(0, 3); t.set_grain(0, "along"); t.set_grain(1, "across")
    t.save_csv(folder / "out.csv")
    text = (folder / "out.csv").read_text(encoding="utf-8-sig")
    assert text.splitlines()[0] == "filename;qty;grain" and "polka_typ_3.dxf;" in text and str(folder) not in text   # relative
    t2 = PartsTable(); t2.load_csv(folder / "out.csv")
    assert [(r.path, r.qty, r.grain) for r in t2.rows] == [(r.path, r.qty, r.grain) for r in t.rows]


def test_save_csv_keeps_absolute_paths_outside_the_csv_folder(folder, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    t = PartsTable(); t.add_files([folder / "polka_typ_3.dxf"])
    t.save_csv(elsewhere / "p.csv")
    assert str(folder / "polka_typ_3.dxf") in (elsewhere / "p.csv").read_text(encoding="utf-8-sig")
    t2 = PartsTable(); t2.load_csv(elsewhere / "p.csv")
    assert t2.rows[0].path == folder / "polka_typ_3.dxf"


def test_save_csv_handles_semicolons_and_polish_names(tmp_path):
    p = tmp_path / "Pó\u0142ka; typ 3.dxf"
    p.write_text("x")
    t = PartsTable(); t.add_files([p]); t.set_qty(0, 2)
    t.save_csv(tmp_path / "p.csv")
    t2 = PartsTable(); t2.load_csv(tmp_path / "p.csv")
    assert t2.rows[0].name == "Pó\u0142ka; typ 3.dxf" and t2.rows[0].qty == 2


# ------------------------------------------------------------------- status
def test_apply_issues_marks_rows_and_returns_general_ones(folder):
    t = PartsTable(); t.add_files([folder / "polka_typ_3.dxf", folder / "szuflada_bok.dxf", folder / "szuflada_front.dxf"])
    issues = [err("polka_typ_3", "part 2500 x 300 does not fit"), Issue(Severity.WARNING, "w", "2 corners", part="szuflada_bok"),
              Issue(Severity.INFO, "i", "fyi", part="szuflada_front"), Issue(Severity.ERROR, "g", "spacing below tool"),
              Issue(Severity.INFO, "m", "at least 2 sheets")]
    general = t.apply_issues(issues)
    levels = {r.stem: (r.level, r.status) for r in t.rows}
    assert levels == {"polka_typ_3": ("error", "part 2500 x 300 does not fit"), "szuflada_bok": ("warning", "2 corners"),
                      "szuflada_front": ("", "")}
    assert [i.code for i in general] == ["g", "m"]                       # not tied to a row: shown in the log instead


def test_apply_issues_long_message_is_shortened_but_kept_in_detail(folder):
    t = PartsTable(); t.add_files([folder / "polka_typ_3.dxf"])
    t.apply_issues([err("polka_typ_3", "x" * 300)])
    assert len(t.rows[0].status) == 70 and t.rows[0].status.endswith("\u2026") and "x" * 300 in t.rows[0].detail


def test_apply_issues_error_wins_over_warning_and_ok_after_a_clean_run(folder):
    t = PartsTable(); t.add_files([folder / "polka_typ_3.dxf", folder / "szuflada_bok.dxf"])
    t.apply_issues([Issue(Severity.WARNING, "w", "careful", part="polka_typ_3"), err("polka_typ_3", "bad")])
    assert t.rows[0].level == "error" and "careful" in t.rows[0].detail and "bad" in t.rows[0].detail
    t.apply_issues([], ran_ok=True)
    assert [r.level for r in t.rows] == ["ok", "ok"] and t.rows[0].detail == ""
    t.apply_issues([])
    assert [r.level for r in t.rows] == ["", ""]


def test_missing_file_status_survives_a_run(tmp_path):
    t = PartsTable(); t.add_files([tmp_path / "ghost.dxf"])
    t.apply_issues([], ran_ok=True)
    assert t.rows[0].status == "file not found"


# ------------------------------------------------------------------ settings
def test_blank_fields_mean_default():
    o = GuiSettings().to_overrides()
    assert o["sheet"] == "2500x1250" and o["units"] == "mm" and o["grain"] == "x" and o["dxf_version"] == "R2000"
    assert o["margin"] is None and o["fit_spacing"] is None and o["tool_diameter"] is None and o["feed_rate"] is None


def test_build_config_from_typed_strings(folder):
    s = GuiSettings(sheet=" 2500 x 1250 ", margin="5", fit_spacing="8", tool_diameter="6", thickness="18", feed_rate="6000")
    cfg = s.build_config([Row(folder / "polka_typ_3.dxf")])
    assert (cfg.sheet_width, cfg.margin, cfg.fit_spacing, cfg.tool_diameter, cfg.thickness, cfg.feed_rate) == (2500, 5, 8, 6, 18, 6000)
    assert cfg.output_dir == folder / "out"                              # blank: next to the first part


def test_build_config_inches():
    cfg = GuiSettings(sheet="100x50", units="in", margin="0.5").build_config()
    assert cfg.sheet_width == pytest.approx(2540) and cfg.margin == pytest.approx(12.7) and cfg.units.name == "in"


@pytest.mark.parametrize("field, value, message", [("sheet", "big", "invalid sheet size"), ("margin", "abc", "margin must be a number"),
                                                   ("fit_spacing", "-1", "fit_spacing must be >= 0"), ("sheet", "", "sheet size is required"),
                                                   ("rotations", "x", "rotations must be a number"), ("grain", "z", "grain must be")])
def test_bad_fields_give_readable_errors(field, value, message):
    with pytest.raises(ConfigError, match=message):
        GuiSettings(**{field: value}).build_config()


def test_explicit_output_dir_is_used(folder, tmp_path):
    cfg = GuiSettings(output_dir=str(tmp_path / "mine")).build_config([Row(folder / "polka_typ_3.dxf")])
    assert cfg.output_dir == tmp_path / "mine"


# --------------------------------------------------------------- persistence
def test_state_round_trip(folder, tmp_path):
    t = PartsTable(); t.add_files([folder / "polka_typ_3.dxf", folder / "szuflada_bok.dxf"])
    t.set_qty(0, 4); t.set_grain(1, "across")
    s = GuiSettings(sheet="3050x1525", margin="12", write_png=False)
    assert save_state(tmp_path / "state.json", s, t, last_dir="/some/dir")
    s2, t2, last = load_state(tmp_path / "state.json")
    assert s2 == s and last == "/some/dir"
    assert [(r.path, r.qty, r.grain) for r in t2.rows] == [(r.path, r.qty, r.grain) for r in t.rows]


def test_missing_or_damaged_state_gives_defaults(tmp_path):
    for content in (None, "not json", "[1, 2]", '{"settings": "oops", "rows": 5}'):
        p = tmp_path / "s.json"
        if content is not None:
            p.write_text(content)
        s, t, last = load_state(p)
        assert s == GuiSettings() and len(t) == 0 and last == ""


def test_state_ignores_bad_pieces_but_keeps_good_ones(folder, tmp_path):
    data = {"settings": {"sheet": "1000x500", "margin": 5, "bogus": "x", "write_png": "yes"},
            "rows": [{"path": str(folder / "polka_typ_3.dxf"), "qty": 2, "grain": "along"}, {"qty": 1}, {"path": "x.dxf", "qty": "many"},
                     {"path": str(folder / "szuflada_bok.dxf"), "qty": 0, "grain": "any"}]}
    (tmp_path / "s.json").write_text(json.dumps(data))
    s, t, _ = load_state(tmp_path / "s.json")
    assert s.sheet == "1000x500" and s.margin == "" and s.write_png is True        # wrongly typed values fall back to defaults
    assert [(r.name, r.qty) for r in t.rows] == [("polka_typ_3.dxf", 2), ("szuflada_bok.dxf", 1)]   # qty 0 -> 1


def test_save_state_failure_is_not_fatal(tmp_path):
    assert save_state(tmp_path / "no" / "such" / "dir" / "s.json", GuiSettings(), PartsTable()) is False


# ------------------------------------------------------ short status labels
def test_common_problems_get_a_short_label_and_keep_the_full_text(folder):
    t = PartsTable(); t.add_files([folder / "polka_typ_3.dxf", folder / "szuflada_bok.dxf"])
    long_msg = "part 2500.000 x 300.000 mm does not fit the usable sheet area 2480.000 x 1230.000 mm in any allowed rotation"
    t.apply_issues([Issue(Severity.ERROR, "part_too_large", long_msg, part="polka_typ_3"),
                    Issue(Severity.WARNING, "inner_corner_rounded", "8 inner corner(s) tighter than ...", part="szuflada_bok")])
    assert (t.rows[0].level, t.rows[0].status) == ("error", "too large for the sheet") and long_msg in t.rows[0].detail
    assert (t.rows[1].level, t.rows[1].status) == ("warning", "inner corners rounded")


def test_every_short_label_fits_the_table_column():
    from nestlib.gui_model import SHORT_STATUS
    assert all(len(v) <= 25 for v in SHORT_STATUS.values())   # fits the Status column at the default font size


def test_unknown_codes_fall_back_to_the_message(folder):
    t = PartsTable(); t.add_files([folder / "polka_typ_3.dxf"])
    t.apply_issues([Issue(Severity.ERROR, "brand_new_code", "something specific happened", part="polka_typ_3")])
    assert t.rows[0].status == "something specific happened"


def test_short_labels_cover_the_codes_the_checks_can_produce():
    # every ERROR/WARNING code raised per part by the pipeline has a label, so the table never shows a raw sentence for them
    import re
    from pathlib import Path as P
    from nestlib.gui_model import SHORT_STATUS
    src = "".join(f.read_text(encoding="utf-8") for f in P(__file__).parent.parent.joinpath("nestlib").glob("*.py"))
    per_part = {"part_too_large", "grain_unsatisfiable", "feature_too_narrow", "hole_smaller_than_tool", "inner_corner_rounded",
                "open_contour", "branching_contour", "multiple_contours", "overlapping_contours", "nested_contour",
                "invalid_contour", "no_contours", "load_failed", "duplicate_part_name", "units_unspecified"}
    assert per_part == set(SHORT_STATUS)
    for code in per_part:
        assert re.search(rf'["\']{code}["\']', src), f"{code} is no longer raised anywhere: remove its label"
