import subprocess
import sys
from pathlib import Path

import dxfnest_gui as gui_launcher

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "dxfnest_gui.py"


def test_gui_needs_more_modules_than_the_cli():
    names = {pip for _, pip in gui_launcher.GUI_REQUIRED}
    assert {"ezdxf", "shapely", "pillow", "numpy", "dearpygui"} <= names


def test_missing_dearpygui_is_named_with_the_pip_command():
    def fake(name):
        if name == "dearpygui":
            raise ImportError("No module named 'dearpygui'")
    p = gui_launcher._launcher.preflight(import_module=fake, required=gui_launcher.GUI_REQUIRED)
    assert any("cannot import 'dearpygui'" in x for x in p) and any(x.endswith("pip install dearpygui") for x in p)


def test_help_works_without_a_display():
    r = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0 and "usage: dxfnest_gui.py" in r.stdout and "--font-size" in r.stdout and "--fresh" in r.stdout


def test_friendly_message_when_dearpygui_is_missing(tmp_path):
    code = ("import sys, runpy; sys.modules['dearpygui'] = None; sys.argv = ['dxfnest_gui.py']; "
            f"runpy.run_path({str(SCRIPT)!r}, run_name='__main__')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 2 and "Traceback" not in r.stderr
    assert "dxfnest_gui: cannot start:" in r.stderr and "dearpygui" in r.stderr and "pip install" in r.stderr


def test_script_is_executable():
    import os
    assert SCRIPT.read_text().startswith("#!/usr/bin/env python3") and os.access(SCRIPT, os.X_OK)
