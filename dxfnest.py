#!/usr/bin/env python3
"""dxfnest: nest DXF parts onto plywood sheets.

Run it from this directory (or give the full path, or symlink it into your PATH):

    python3 dxfnest.py parts.csv --sheet 2500x1250
    python3 dxfnest.py --help

The modules live in the `nestlib` folder next to this file. Third-party Python modules
needed: ezdxf, shapely and (for PNG previews) pillow; see requirements.txt.
"""
import importlib
import sys
from pathlib import Path

MIN_PYTHON = (3, 11)
# import name -> name to give pip. Pillow is only needed for PNG output: the CLI checks it
# itself, so that `--no-png` keeps working without it.
REQUIRED = (("ezdxf", "ezdxf"), ("shapely", "shapely"))
INSTALL_HINT = "python3 -m pip install ezdxf shapely pillow"


def preflight(version_info=None, import_module=importlib.import_module, required=None):
    """Return a list of problems that stop the program from starting (empty = fine).
    Arguments exist so the checks can be tested without a second Python installed."""
    version_info = version_info or sys.version_info
    if tuple(version_info[:2]) < MIN_PYTHON:
        return [f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required "
                f"(this is {version_info[0]}.{version_info[1]}). Try python3.11 or python3.12."]
    problems, missing = [], []
    for module, pip_name in (REQUIRED if required is None else required):
        try:
            import_module(module)
        except ImportError as e:
            missing.append(pip_name)
            problems.append(f"cannot import '{module}' ({e})")
    if missing:
        problems.append(f"install the missing module(s) with:  python3 -m pip install {' '.join(missing)}")
    return problems


def main(argv=None):
    problems = preflight()
    if problems:
        print("dxfnest: cannot start:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(f"  (all modules: {INSTALL_HINT}; see requirements.txt)", file=sys.stderr)
        return 2
    here = str(Path(__file__).resolve().parent)       # resolve(): works through a symlink too
    if here not in sys.path:
        sys.path.insert(0, here)
    from nestlib.cli import main as run
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
