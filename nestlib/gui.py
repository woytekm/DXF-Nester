"""Dear PyGui front end: a table of DXF files with quantity and grain, the options, and a preview of the sheets.

All state lives in gui_model (tested without a window) and all work in job.run_job (shared with the command
line). This module only shows and edits. The nesting runs in a background thread; its result comes back
through a queue that the main loop polls, so Dear PyGui is only ever touched from the main thread.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import dearpygui.dearpygui as dpg

from .config import ConfigError, DXF_VERSIONS
from .fonts import find_font_path, find_mono_font_path
from .gui_model import GRAIN_CHOICES, GuiSettings, PartsTable, default_state_path, load_state, save_state
from .job import JobOptions, JobResult, JobStatus, run_job
from .manifest import ManifestError
from .models import Severity
from .summary import checked_line, summary_lines, visible_issues
from . import __version__

LEFT_W = 640
PREVIEW_PX = 1400
RED, YELLOW, GREEN, GREY, BLUE = (255, 120, 120), (240, 205, 90), (120, 215, 130), (150, 150, 150), (130, 180, 255)
LEVEL_COLOUR = {"error": RED, "warning": YELLOW, "ok": GREEN, "": GREY}


def open_folder(path: Path) -> None:
    """Show a folder in Finder / Explorer / the file manager."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


class App:
    def __init__(self, state_path: Path | None = None, font_size: int = 16, restore: bool = True) -> None:
        self.state_path = state_path if state_path is not None else default_state_path()
        self.font_size = font_size
        if restore:
            self.settings, self.table, self.last_dir = load_state(self.state_path)
        else:
            self.settings, self.table, self.last_dir = GuiSettings(), PartsTable(), ""
        self.csv_path: Path | None = None
        self.queue: queue.Queue = queue.Queue()
        self.running = False
        self.result: JobResult | None = None
        self.cfg = None
        self.textures: list[int | str] = []
        self.image_sizes: dict[str, tuple[int, int]] = {}
        self._frame_shot: str | None = None

    # ================================================================== build
    def build(self) -> None:
        dpg.create_context()
        self._fonts()
        with dpg.texture_registry(tag="tex_reg"):
            pass
        self._dialogs()
        with dpg.window(tag="main", label=f"dxfnest {__version__}"):
            with dpg.group(horizontal=True):
                with dpg.child_window(tag="left", width=LEFT_W, border=False):
                    self._parts_panel()
                    dpg.add_spacer(height=6)
                    self._options_panel()
                    dpg.add_spacer(height=6)
                    self._run_panel()
                with dpg.child_window(tag="right", border=False):
                    self._results_panel()
        dpg.set_primary_window("main", True)
        dpg.create_viewport(title=f"dxfnest {__version__}", width=1340, height=900, min_width=980, min_height=620)
        dpg.set_viewport_resize_callback(lambda *_: self.fit_images())
        dpg.setup_dearpygui()
        self.refresh_table()
        self.load_settings_into_widgets()
        self.set_status("Ready. Add DXF files or open a parts CSV.")

    def _fonts(self) -> None:
        """Dear PyGui 2.x picks glyph ranges itself; 1.x needs Latin Extended-A added by hand for Polish letters."""
        legacy = int(dpg.get_dearpygui_version().split(".")[0]) < 2
        path, mono = find_font_path(), find_mono_font_path()
        self.mono = None

        def load(file: str, size: int):
            f = dpg.add_font(file, size)
            if legacy:
                dpg.add_font_range(0x0100, 0x017F, parent=f)     # Latin Extended-A: l-stroke, ogonek, acute accents
                dpg.add_font_range(0x2000, 0x206F, parent=f)     # general punctuation: the ellipsis
            return f

        with dpg.font_registry():
            if path:
                dpg.bind_font(load(path, self.font_size))
            if mono:
                self.mono = load(mono, self.font_size - 1)

    def _dialogs(self) -> None:
        start = self.last_dir or str(Path.home())
        with dpg.file_dialog(tag="dlg_csv", show=False, modal=True, width=760, height=460, callback=self._csv_chosen,
                             directory_selector=False, default_path=start):
            for ext in (".csv", ".txt", ".tsv"):
                dpg.add_file_extension(ext, color=(120, 215, 130))
            dpg.add_file_extension(".*")
        with dpg.file_dialog(tag="dlg_dxf", show=False, modal=True, width=760, height=460, callback=self._dxf_chosen,
                             directory_selector=False, file_count=1000, default_path=start):
            dpg.add_file_extension(".dxf", color=(120, 215, 130))
            dpg.add_file_extension(".DXF", color=(120, 215, 130))
        with dpg.file_dialog(tag="dlg_dir", show=False, modal=True, width=760, height=460, callback=self._dir_chosen,
                             directory_selector=True, default_path=start):
            pass
        with dpg.window(tag="dlg_save", label="Save parts CSV", show=False, modal=True, no_resize=True, width=620, height=150):
            dpg.add_text("Write the table to a parts CSV (the same file the command line reads).\nAn existing file with this name is overwritten.")
            dpg.add_input_text(tag="save_path", width=-1)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Save", width=100, callback=self.on_save_csv_ok)
                dpg.add_button(label="Cancel", width=100, callback=lambda: dpg.configure_item("dlg_save", show=False))

    def _parts_panel(self) -> None:
        dpg.add_text("Parts", color=BLUE)
        with dpg.group(horizontal=True):
            dpg.add_button(label="Open CSV...", callback=lambda: self.show_dialog("dlg_csv"))
            dpg.add_button(label="Add DXF files...", callback=lambda: self.show_dialog("dlg_dxf"))
            dpg.add_button(label="Save CSV...", callback=self.on_save_csv)
            dpg.add_button(label="Clear", callback=self.on_clear)
        dpg.add_text("", tag="csv_label", color=GREY)
        with dpg.child_window(tag="table_box", height=290, border=True):
            with dpg.table(tag="parts_table", header_row=True, policy=dpg.mvTable_SizingFixedFit, borders_innerH=True,
                           borders_innerV=True, borders_outerH=True, borders_outerV=True, row_background=True,
                           scrollY=False):
                dpg.add_table_column(label="File", width_stretch=True, init_width_or_weight=2.2)
                dpg.add_table_column(label="Qty", width_fixed=True, init_width_or_weight=96)
                dpg.add_table_column(label="Grain", width_fixed=True, init_width_or_weight=104)
                dpg.add_table_column(label="Status", width_stretch=True, init_width_or_weight=3.0)
                dpg.add_table_column(label="", width_fixed=True, init_width_or_weight=34)
        dpg.add_text("", tag="totals", color=GREY)

    def _options_panel(self) -> None:
        dpg.add_text("Sheet and options", color=BLUE)
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit):
            dpg.add_table_column(width_fixed=True, init_width_or_weight=190)
            dpg.add_table_column(width_stretch=True)
            def row(label, tip=None):
                dpg.add_table_row()
            with dpg.table_row():
                dpg.add_text("Sheet size (W x H)")
                with dpg.group(horizontal=True):
                    dpg.add_input_text(tag="o_sheet", width=150, hint="2500x1250")
                    dpg.add_radio_button(("mm", "in"), tag="o_units", horizontal=True, default_value="mm")
            for tag, label, hint in (("o_margin", "Margin", "0"), ("o_fit_spacing", "Fit spacing", "10 mm (at least the tool)"),
                                     ("o_tool", "Tool diameter", "blank = no tool checks")):
                with dpg.table_row():
                    dpg.add_text(label)
                    dpg.add_input_text(tag=tag, width=-1, hint=hint)
            with dpg.table_row():
                dpg.add_text("Grain runs along")
                dpg.add_combo(("x", "y"), tag="o_grain", width=70, default_value="x")
                dpg.add_text("")
        with dpg.collapsing_header(label="More options", default_open=False):
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit):
                dpg.add_table_column(width_fixed=True, init_width_or_weight=190)
                dpg.add_table_column(width_stretch=True)
                for tag, label, hint in (("o_rotations", "Allowed rotations", "0,90,180,270"),
                                         ("o_feed", "Feed rate / min", "blank = no time estimate"),
                                         ("o_thickness", "Sheet thickness", "for the number of passes"),
                                         ("o_pass", "Max depth per pass", "default: tool diameter")):
                    with dpg.table_row():
                        dpg.add_text(label)
                        dpg.add_input_text(tag=tag, width=-1, hint=hint)
                with dpg.table_row():
                    dpg.add_text("DXF version")
                    dpg.add_combo(DXF_VERSIONS, tag="o_dxf_version", width=110, default_value="R2000")
                with dpg.table_row():
                    dpg.add_text("PNG files")
                    dpg.add_checkbox(tag="o_png", label="also write PNG previews", default_value=True)
        dpg.add_spacer(height=4)
        dpg.add_text("Output folder")
        with dpg.group(horizontal=True):
            dpg.add_input_text(tag="o_outdir", width=LEFT_W - 215, hint="blank = 'out' next to the first part")
            dpg.add_button(label="Browse...", callback=lambda: self.show_dialog("dlg_dir"))
            dpg.add_button(label="Open", callback=self.on_open_output)

    def _run_panel(self) -> None:
        with dpg.group(horizontal=True):
            dpg.add_button(label="Check & preview", tag="btn_preview", width=200, height=34, callback=lambda: self.start(False))
            dpg.add_button(label="Run & write files", tag="btn_write", width=200, height=34, callback=lambda: self.start(True))
        with dpg.tooltip("btn_preview"):
            dpg.add_text("Nest and show the sheets; writes nothing to disk.")
        with dpg.tooltip("btn_write"):
            dpg.add_text("Nest, then write sheet_NN.dxf (and .png) to the output folder.")
        dpg.add_text("", tag="status", wrap=LEFT_W - 20)

    def _results_panel(self) -> None:
        dpg.add_text("Result", color=BLUE)
        with dpg.child_window(tag="summary_box", height=230, border=True):
            dpg.add_group(tag="summary")
        dpg.add_spacer(height=4)
        with dpg.child_window(tag="preview_box", height=-175, border=True):
            dpg.add_text("The nested sheets appear here after 'Check & preview'.", tag="preview_hint", color=GREY)
            dpg.add_tab_bar(tag="tabs")
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_text("Messages", color=BLUE)
            dpg.add_checkbox(tag="show_info", label="show info", default_value=False, callback=lambda: self.show_log())
        with dpg.child_window(tag="log_box", height=-1, border=True):
            dpg.add_group(tag="log")

    # ============================================================== dialogs
    def show_dialog(self, tag: str) -> None:
        start = self.last_dir if self.last_dir and Path(self.last_dir).is_dir() else str(Path.home())
        dpg.configure_item(tag, default_path=start, show=True)

    def _remember(self, path: Path) -> None:
        self.last_dir = str(path if path.is_dir() else path.parent)

    def _csv_chosen(self, sender, app_data) -> None:
        sel = list(app_data.get("selections", {}).values()) or [app_data.get("file_path_name", "")]
        if sel and sel[0]:
            self.load_csv(Path(sel[0]))

    def _dxf_chosen(self, sender, app_data) -> None:
        self.add_files([Path(p) for p in app_data.get("selections", {}).values()])

    def _dir_chosen(self, sender, app_data) -> None:
        p = app_data.get("file_path_name") or app_data.get("current_path", "")
        if p:
            dpg.set_value("o_outdir", p)

    # =============================================================== actions
    def load_csv(self, path: Path, *, replace: bool = True) -> None:
        self._remember(path)
        try:
            n = self.table.load_csv(path, replace=replace)
        except ManifestError as e:
            self.clear_log()
            for problem in e.problems:
                self.log("error", problem)
            self.set_status(f"Could not read {path.name}: see the messages below.", RED)
            return
        self.csv_path = path
        dpg.set_value("csv_label", f"CSV: {path}")
        self.table.clear_status()
        self.table.check_files()
        self.refresh_table()
        self.clear_log()
        missing = sum(1 for r in self.table.rows if r.status == "file not found")
        self.set_status(f"Loaded {n} part(s) from {path.name}" + (f"; {missing} file(s) not found." if missing else "."),
                        YELLOW if missing else None)

    def add_files(self, paths: list[Path]) -> None:
        if paths:
            self._remember(paths[0])
        res = self.table.add_files(paths)
        self.refresh_table()
        notes = []
        if res.duplicates:
            notes.append(f"{len(res.duplicates)} already in the table")
        if res.rejected:
            notes.append(f"{len(res.rejected)} not .dxf: {', '.join(res.rejected[:3])}")
        self.set_status(f"Added {res.added} file(s)" + (f" ({'; '.join(notes)})" if notes else "") + ".",
                        YELLOW if notes else None)

    def on_clear(self) -> None:
        self.table.clear()
        self.csv_path = None
        dpg.set_value("csv_label", "")
        self.refresh_table()
        self.set_status("Table cleared.")

    def on_remove(self, sender, app_data, index) -> None:
        self.table.remove(index)
        self.refresh_table()

    def on_qty(self, sender, app_data, index) -> None:
        err = self.table.set_qty(index, app_data)
        if err:                                           # cannot happen with min_clamped, but never trust a widget
            dpg.set_value(sender, self.table.rows[index].qty)
            self.set_status(err, RED)
        self.update_totals()

    def on_grain(self, sender, app_data, index) -> None:
        self.table.set_grain(index, app_data)

    def on_save_csv(self) -> None:
        default = self.csv_path or ((self.table.rows[0].path.parent / "parts.csv") if self.table.rows else Path.home() / "parts.csv")
        dpg.set_value("save_path", str(default))
        vw = dpg.get_viewport_client_width()
        dpg.set_item_pos("dlg_save", [max(20, vw // 2 - 310), 160])
        dpg.configure_item("dlg_save", show=True)

    def on_save_csv_ok(self) -> None:
        path = Path(dpg.get_value("save_path").strip()).expanduser()
        try:
            self.table.save_csv(path)
        except OSError as e:
            self.set_status(f"Could not save {path}: {e}", RED)
        else:
            self.csv_path = path
            dpg.set_value("csv_label", f"CSV: {path}")
            self.set_status(f"Saved {len(self.table)} part(s) to {path}.", GREEN)
        dpg.configure_item("dlg_save", show=False)

    def on_open_output(self) -> None:
        cfg = self.cfg
        folder = Path(dpg.get_value("o_outdir").strip()).expanduser() if dpg.get_value("o_outdir").strip() else (
            cfg.output_dir if cfg else (self.table.rows[0].path.parent / "out" if self.table.rows else Path.home()))
        if folder.is_dir():
            open_folder(folder)
        else:
            self.set_status(f"{folder} does not exist yet (use 'Run & write files').", YELLOW)

    # ================================================================ table
    def refresh_table(self) -> None:
        dpg.delete_item("parts_table", children_only=True, slot=1)
        for i, r in enumerate(self.table.rows):
            with dpg.table_row(parent="parts_table"):
                t = dpg.add_text(r.name)
                with dpg.tooltip(t):
                    dpg.add_text(str(r.path))
                dpg.add_input_int(default_value=r.qty, min_value=1, min_clamped=True, step=1, step_fast=10, width=-1,
                                  callback=self.on_qty, user_data=i)
                dpg.add_combo(GRAIN_CHOICES, default_value=r.grain.value, width=-1, callback=self.on_grain, user_data=i)
                s = dpg.add_text(r.status or "", color=LEVEL_COLOUR.get(r.level, GREY))
                if r.detail:
                    with dpg.tooltip(s):
                        dpg.add_text(r.detail, wrap=520)
                dpg.add_button(label="x", callback=self.on_remove, user_data=i)
        self.update_totals()

    def update_totals(self) -> None:
        n = len(self.table)
        dpg.set_value("totals", f"{n} file(s), {self.table.total_pieces()} piece(s) in total" if n else "No parts yet.")

    # ============================================================= settings
    def read_settings(self) -> GuiSettings:
        g = dpg.get_value
        self.settings = GuiSettings(
            sheet=g("o_sheet"), units=g("o_units"), margin=g("o_margin"), fit_spacing=g("o_fit_spacing"), grain=g("o_grain"),
            tool_diameter=g("o_tool"), rotations=g("o_rotations"), thickness=g("o_thickness"), feed_rate=g("o_feed"),
            pass_depth=g("o_pass"), dxf_version=g("o_dxf_version"), output_dir=g("o_outdir"), write_png=g("o_png"))
        return self.settings

    def load_settings_into_widgets(self) -> None:
        s, v = self.settings, dpg.set_value
        v("o_sheet", s.sheet); v("o_units", s.units if s.units in ("mm", "in") else "mm"); v("o_margin", s.margin)
        v("o_fit_spacing", s.fit_spacing); v("o_grain", s.grain if s.grain in ("x", "y") else "x"); v("o_tool", s.tool_diameter)
        v("o_rotations", s.rotations); v("o_thickness", s.thickness); v("o_feed", s.feed_rate); v("o_pass", s.pass_depth)
        v("o_dxf_version", s.dxf_version if s.dxf_version in DXF_VERSIONS else "R2000"); v("o_outdir", s.output_dir)
        v("o_png", s.write_png)

    # ================================================================= run
    def start(self, write: bool) -> None:
        if self.running:
            return
        self.read_settings()
        if not len(self.table):
            self.set_status("Add some DXF files first (Add DXF files... or Open CSV...).", YELLOW)
            return
        try:
            cfg = self.settings.build_config(self.table.rows)
        except ConfigError as e:
            self.clear_log()
            self.log("error", str(e))
            self.set_status(f"Check the options: {e}", RED)
            return
        self.cfg = cfg
        self.table.clear_status()
        self.table.check_files()
        self.refresh_table()
        options = JobOptions(write_files=write, png=self.settings.write_png, png_width=2400)
        self.running = True
        dpg.configure_item("btn_preview", enabled=False)
        dpg.configure_item("btn_write", enabled=False)
        self.set_status("Working...")
        threading.Thread(target=self._work, args=(self.table.to_requests(), cfg, options), daemon=True).start()

    def _work(self, requests, cfg, options: JobOptions) -> None:
        """Worker thread: no Dear PyGui calls in here."""
        try:
            import numpy as np
            from .png_render import render_layout_image
            result = run_job(requests, cfg, options)
            images = []
            if result.ok and result.layouts:
                order = sorted(result.parts)
                for layout in result.layouts:
                    img = render_layout_image(layout, result.parts, cfg, width_px=PREVIEW_PX, colour_order=order).convert("RGBA")
                    images.append((img.width, img.height, (np.asarray(img, dtype=np.float32) / 255.0).ravel()))
            self.queue.put(("done", result, cfg, images))
        except Exception:                                  # a bug, not a user error: show it instead of dying silently
            self.queue.put(("crash", traceback.format_exc()))

    def poll(self) -> None:
        while True:
            try:
                msg = self.queue.get_nowait()
            except queue.Empty:
                return
            self.running = False
            dpg.configure_item("btn_preview", enabled=True)
            dpg.configure_item("btn_write", enabled=True)
            if msg[0] == "crash":
                self.clear_log()
                self.log("error", "internal error, please report this:\n" + msg[1])
                self.set_status("Internal error: see the messages below.", RED)
                print(msg[1], file=sys.stderr)
            else:
                self.show_result(*msg[1:])

    def show_result(self, result: JobResult, cfg, images) -> None:
        self.result, self.cfg = result, cfg
        self.table.apply_issues(result.issues, ran_ok=result.ok)
        self.refresh_table()
        self.show_log()
        self.show_summary()
        self.show_previews(images)
        if result.status is JobStatus.OK:
            where = f" Files are in {cfg.output_dir}." if any(result.files) else " Nothing was written (preview only)."
            self.set_status(f"Done: {len(result.layouts)} sheet(s).{where}", GREEN)
        elif result.status is JobStatus.CHECKED:
            self.set_status(checked_line(result), GREEN)
        elif result.status is JobStatus.PARTS_FAILED:
            self.set_status(f"Stopped: {result.count(Severity.ERROR)} error(s), {result.count(Severity.WARNING)} warning(s); "
                            f"nothing was nested or written. See the status column and the messages.", RED)
        elif result.status is JobStatus.WRITE_FAILED:
            self.set_status(f"Error: {result.error}", RED)
        else:
            self.set_status(f"Stopped: {result.status.value}.", RED)

    # ================================================================ output
    def set_status(self, text: str, colour=None) -> None:
        dpg.set_value("status", text)
        dpg.configure_item("status", color=colour or (220, 220, 220))

    def clear_log(self) -> None:
        self._log_lines: list[tuple[str, str]] = []
        dpg.delete_item("log", children_only=True)

    def log(self, level: str, text: str) -> None:
        if not hasattr(self, "_log_lines"):
            self._log_lines = []
        self._log_lines.append((level, text))
        dpg.add_text(f"{level}: {text}" if level in ("error", "warning") else text, parent="log", wrap=0,
                     color={"error": RED, "warning": YELLOW}.get(level, GREY))

    def show_log(self) -> None:
        dpg.delete_item("log", children_only=True)
        self._log_lines = []
        if self.result is None:
            return
        for i in visible_issues(self.result.issues, verbose=dpg.get_value("show_info")):
            self.log(i.severity.value, (f"{i.part}: " if i.part else "") + i.message)
        for f in self.result.stale:
            self.log("warning", f"{f.name} is left over from an earlier run and was not overwritten")
        if self.result.error:
            self.log("error", self.result.error)
        if not self._log_lines:
            self.log("info", "No warnings or errors.")

    def show_summary(self) -> None:
        dpg.delete_item("summary", children_only=True)
        r = self.result
        if r is None:
            return
        if r.status is JobStatus.OK:
            lines = summary_lines(r, self.cfg, show_files=False)
        elif r.status is JobStatus.CHECKED:
            lines = [checked_line(r)]
        else:
            lines = ["No result: " + ("a part failed to load or a check failed (see below)" if r.status is JobStatus.PARTS_FAILED
                                    else r.status.value)]
        for line in lines:
            t = dpg.add_text(line or " ", parent="summary", wrap=0)        # long lines wrap at the box edge
            if self.mono:
                dpg.bind_item_font(t, self.mono)

    def show_previews(self, images) -> None:
        for tab in dpg.get_item_children("tabs", 1) or []:
            dpg.delete_item(tab)
        for tex in self.textures:
            dpg.delete_item(tex)
        self.textures.clear()
        self.image_sizes.clear()
        dpg.configure_item("preview_hint", show=not images)
        if not images or self.result is None:
            return
        for layout, (w, h, data) in zip(self.result.layouts, images):
            tex = dpg.add_static_texture(w, h, data, parent="tex_reg")
            self.textures.append(tex)
            with dpg.tab(label=f"Sheet {layout.index}", parent="tabs"):
                dpg.add_image(tex, tag=f"img_{layout.index}")
            self.image_sizes[f"img_{layout.index}"] = (w, h)
        self.fit_images()

    def fit_images(self) -> None:
        avail = max(300, dpg.get_viewport_client_width() - LEFT_W - 60)
        for tag, (w, h) in self.image_sizes.items():
            scale = min(1.0, avail / w)
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, width=round(w * scale), height=round(h * scale))

    # ============================================================= main loop
    def pump(self, frames: int = 1) -> None:
        for _ in range(frames):
            self.poll()
            dpg.render_dearpygui_frame()

    def run(self) -> None:
        self.build()
        dpg.show_viewport()
        while dpg.is_dearpygui_running():
            self.pump()
        self.shutdown()

    def shutdown(self) -> None:
        try:
            self.read_settings()
            save_state(self.state_path, self.settings, self.table, self.last_dir)
        finally:
            dpg.destroy_context()


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="dxfnest_gui.py", description="dxfnest with a window: pick a parts CSV or DXF files, edit quantities and grain, see the nested sheets.")
    p.add_argument("--font-size", type=int, default=16, help="text size in pixels (default 16)")
    p.add_argument("--state", type=Path, metavar="FILE", help=f"where to remember the table and options (default {default_state_path()})")
    p.add_argument("--fresh", action="store_true", help="start empty: do not restore the previous table and options")
    p.add_argument("csv", nargs="?", type=Path, help="optionally open this parts CSV at start")
    a = p.parse_args(argv)
    app = App(state_path=a.state, font_size=a.font_size, restore=not a.fresh)
    app.build()
    if a.csv:
        app.load_csv(a.csv)
    dpg.show_viewport()
    while dpg.is_dearpygui_running():
        app.pump()
    app.shutdown()
    return 0
