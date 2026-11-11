"""Run the real GUI under a virtual display (xvfb-run) and drive it like a user, saving screenshots.

    xvfb-run -a -s "-screen 0 1400x1000x24" python3 tests/gui_harness.py SCENARIO OUTDIR

Scenarios: empty, loaded, preview, errors, written, polish, edit, dialog, restore.
Prints one line "FACTS {json}" that test_gui.py checks.
"""
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import dearpygui.dearpygui as dpg  # noqa: E402

from nestlib.gui import App  # noqa: E402
from nestlib.gui_model import GuiSettings, PartsTable, save_state  # noqa: E402

DATA = ROOT / "tests" / "data"
CSV = "filename;qty;grain\npolka_typ_3.dxf;2;along\nprzegroda_typ_3.dxf;1;\nszuflada_front.dxf;4;across\nszuflada_bok.dxf;8;\n"


def shot(app, path, frames=8):
    app.pump(frames)
    dpg.output_frame_buffer(str(path))
    app.pump(frames)


def wait_idle(app, timeout=90):
    t0 = time.time()
    while app.running and time.time() - t0 < timeout:
        app.pump(1)
        time.sleep(0.02)
    app.pump(6)


def prepare(out: Path) -> Path:
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    for f in DATA.glob("*.dxf"):
        shutil.copy(f, work / f.name)
    (work / "parts.csv").write_text(CSV, encoding="utf-8")
    return work


def table_cells(row_index):
    """The widgets of one table row: [file text, qty input, grain combo, status text, remove button]."""
    rows = dpg.get_item_children("parts_table", 1)
    cells = dpg.get_item_children(rows[row_index], 1)
    return [c for c in cells if dpg.get_item_type(c) != "mvAppItemType::mvTooltip"]      # tooltips are children too


def fire(widget, value):
    """Call a widget's callback exactly like Dear PyGui does when the user changes it."""
    cb = dpg.get_item_callback(widget)
    cb(widget, value, dpg.get_item_user_data(widget))


def main():
    scenario, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    work = prepare(out)
    state = out / "state.json"
    if scenario == "restore":
        t = PartsTable()
        t.add_files([work / "polka_typ_3.dxf", work / "szuflada_bok.dxf"])
        t.set_qty(0, 3); t.set_grain(1, "across")
        save_state(state, GuiSettings(sheet="3050x1525", margin="7", grain="y", tool_diameter="6", units="mm"), t, str(work))
    app = App(state_path=state, restore=(scenario == "restore"))
    app.build()
    dpg.show_viewport()
    app.pump(5)
    facts = {"scenario": scenario}

    if scenario == "restore":
        facts.update(rows=len(app.table), qty=[r.qty for r in app.table.rows], grains=[r.grain.value for r in app.table.rows],
                     sheet=dpg.get_value("o_sheet"), margin=dpg.get_value("o_margin"), grain=dpg.get_value("o_grain"),
                     tool=dpg.get_value("o_tool"), totals=dpg.get_value("totals"))

    elif scenario in ("loaded", "preview", "errors", "written"):
        app.load_csv(work / "parts.csv")
        facts["rows"] = len(app.table)
        if scenario != "loaded":
            if scenario == "errors":
                dpg.set_value("o_margin", "10")
            if scenario == "written":
                dpg.set_value("o_outdir", str(out / "result"))
                dpg.set_value("o_feed", "6000"); dpg.set_value("o_thickness", "18"); dpg.set_value("o_tool", "8")
            app.start(scenario == "written")
            facts["buttons_disabled_while_running"] = not dpg.get_item_configuration("btn_preview")["enabled"]
            wait_idle(app)
            r = app.result
            facts.update(status=r.status.value, sheets=len(r.layouts), tabs=len(dpg.get_item_children("tabs", 1) or []),
                         row_levels=[x.level for x in app.table.rows], row_status=[x.status for x in app.table.rows],
                         buttons_enabled_after=dpg.get_item_configuration("btn_preview")["enabled"],
                         status_text=dpg.get_value("status"),
                         log=[l for _, l in getattr(app, "_log_lines", [])],
                         files=sorted(p.name for p in (out / "result").glob("*")) if (out / "result").exists() else [])

    elif scenario == "polish":
        shutil.copy(work / "polka_typ_3.dxf", work / "Pó\u0142ka_typ_3.dxf")
        app.add_files([work / "Pó\u0142ka_typ_3.dxf", work / "szuflada_bok.dxf"])
        app.start(False)
        wait_idle(app)
        facts.update(rows=len(app.table), status=app.result.status.value, names=[r.name for r in app.table.rows],
                     part_names=sorted(app.result.parts))

    elif scenario == "edit":
        app.load_csv(work / "parts.csv")
        qty, grain, remove = (table_cells(0)[i] for i in (1, 2, 4))
        fire(qty, 7)
        fire(grain, "across")
        facts["after_edit"] = [(r.name, r.qty, r.grain.value) for r in app.table.rows[:1]]
        facts["totals_after_qty"] = dpg.get_value("totals")
        fire(table_cells(3)[4], None)                                   # remove the last row
        facts["rows_after_remove"] = len(app.table)
        facts["table_widgets_after_remove"] = len(dpg.get_item_children("parts_table", 1))
        app.add_files([work / "polka_typ_3.dxf", work / "readme.txt"])
        facts["status_after_dup"] = dpg.get_value("status")
        dpg.set_value("save_path", str(work / "saved.csv"))
        app.on_save_csv_ok()
        facts["saved_csv"] = (work / "saved.csv").read_text(encoding="utf-8-sig")
        facts["status_after_save"] = dpg.get_value("status")
        shot(app, out / "edit_state.png")
        app.on_clear()
        facts.update(rows_after_clear=len(app.table), totals_after_clear=dpg.get_value("totals"))

    elif scenario == "dialog":
        app.last_dir = str(work)
        app.show_dialog("dlg_dxf")
        app.pump(10)
        facts["dxf_dialog_shown"] = dpg.is_item_shown("dlg_dxf")
        shot(app, out / "dlg_dxf.png")
        dpg.configure_item("dlg_dxf", show=False)
        app.load_csv(work / "parts.csv")
        app.pump(6)
        app.on_save_csv()
        app.pump(6)
        facts["save_dialog_shown"] = dpg.is_item_shown("dlg_save")
        facts["save_default"] = dpg.get_value("save_path")
        shot(app, out / "dlg_save.png")
        # the callbacks the file dialogs would call
        app.on_clear()
        app._dxf_chosen(None, {"selections": {"polka_typ_3.dxf": str(work / "polka_typ_3.dxf"), "szuflada_bok.dxf": str(work / "szuflada_bok.dxf")}})
        facts["rows_after_dxf_dialog"] = len(app.table)
        app._csv_chosen(None, {"selections": {"parts.csv": str(work / "parts.csv")}, "file_path_name": str(work / "parts.csv")})
        facts["rows_after_csv_dialog"] = len(app.table)
        app._dir_chosen(None, {"file_path_name": str(out / "elsewhere"), "current_path": str(out)})
        facts["outdir_after_dir_dialog"] = dpg.get_value("o_outdir")

    shot(app, out / f"{scenario}.png")
    print("FACTS " + json.dumps(facts, ensure_ascii=False))
    app.shutdown()


if __name__ == "__main__":
    main()
