#!/usr/bin/env python3
"""dxfnest with a window: pick a parts CSV or DXF files, edit quantity and grain in a table,
set the options, and see the nested sheets.

    python3 dxfnest_gui.py                 # start empty (or with what you had last time)
    python3 dxfnest_gui.py parts.csv       # open a parts CSV right away
    python3 dxfnest_gui.py --help

Needs the same modules as dxfnest.py plus pillow and dearpygui (see requirements.txt).
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dxfnest as _launcher                       # the command-line launcher: shares its start-up checks

GUI_REQUIRED = _launcher.REQUIRED + (("PIL", "pillow"), ("numpy", "numpy"), ("dearpygui", "dearpygui"))
INSTALL_HINT = "python3 -m pip install ezdxf shapely pillow dearpygui"


def main(argv=None):
    problems = _launcher.preflight(required=GUI_REQUIRED)
    if problems:
        print("dxfnest_gui: cannot start:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(f"  (all modules: {INSTALL_HINT}; see requirements.txt)", file=sys.stderr)
        return 2
    from nestlib.gui import main as run
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
