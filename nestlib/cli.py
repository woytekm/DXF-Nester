"""Command line interface. Wires modules 0-8 together; contains no logic of its own
beyond argument parsing, printing and exit codes.

    python3 dxfnest.py PARTS.csv --sheet 2500x1250 [options]

Exit codes: 0 done (warnings allowed), 1 problems with the parts/sheet (nothing
written), 2 usage or configuration error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence, TextIO

from . import __version__
from .config import DXF_VERSIONS, ConfigError, load_config
from .job import JobOptions, JobStatus, run_job
from .manifest import ManifestError, read_manifest
from .summary import checked_line, format_issue, stopped_line, summary_lines, visible_issues

EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE = 0, 1, 2

_DESCRIPTION = """\
Nest DXF parts onto plywood sheets and write one DXF (and PNG preview) per sheet.

PARTS.csv lists the parts, one per row:   filename;qty;grain
  filename  a .dxf file (relative to the CSV, or to --dxf-dir)
  qty       number of pieces
  grain     optional: 'along' (the part's longer side runs along the sheet's grain),
            'across', or empty for no requirement
Comma, semicolon or tab separated; the header row names the columns.

Lengths (--sheet, --margin, --fit-spacing, --tool-diameter, --tolerance) are read in
millimetres, or inches with --in, and results are shown in the same unit.
"""


class _Parser(argparse.ArgumentParser):
    """argparse that writes to injected streams (so tests and callers can capture it)."""

    def __init__(self, *a, out: TextIO, err: TextIO, **kw) -> None:
        super().__init__(*a, **kw)
        self._out, self._err = out, err

    def _print_message(self, message, file=None):  # noqa: D401
        if message:
            (self._out if file is sys.stdout else self._err).write(message)


def build_parser(out: TextIO = None, err: TextIO = None) -> argparse.ArgumentParser:
    p = _Parser(prog="dxfnest.py", description=_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter,
                out=out or sys.stdout, err=err or sys.stderr)
    p.add_argument("parts", metavar="PARTS.csv", type=Path, help="quantities file (see above)")
    p.add_argument("-V", "--version", action="version", version=f"dxfnest {__version__}")

    g = p.add_argument_group("sheet and spacing")
    g.add_argument("--sheet", metavar="WxH", help="sheet size, e.g. 2500x1250 (width x height; width is the X axis)")
    g.add_argument("--margin", type=float, metavar="N", help="unusable band along every sheet edge (default 0)")
    g.add_argument("--fit-spacing", type=float, metavar="N",
                   help="minimum gap between nested parts (default 10 mm; with --tool-diameter: at least the tool diameter)")
    g.add_argument("--grain", choices=("x", "y"), help="sheet axis the grain runs along (default x = sheet width)")
    g.add_argument("--rotations", metavar="LIST", help="allowed rotations in degrees, comma separated (default 0,90,180,270)")

    u = p.add_argument_group("units")
    ug = u.add_mutually_exclusive_group()
    ug.add_argument("--mm", dest="units", action="store_const", const="mm", help="lengths in millimetres (default)")
    ug.add_argument("--in", dest="units", action="store_const", const="in", help="lengths in inches")

    t = p.add_argument_group("tool checks")
    t.add_argument("--tool-diameter", type=float, metavar="N",
                   help="router bit diameter; enables checks for slots/holes it cannot cut and inner corners it rounds "
                        "(default: no tool checks)")
    t.add_argument("--tolerance", type=float, metavar="N", help="endpoint matching and geometry tolerance (default 0.01 mm)")

    e = p.add_argument_group("machining time estimate (optional; cut length is always shown)")
    e.add_argument("--feed-rate", type=float, metavar="N", help="cutting feed per minute; enables the time estimate")
    e.add_argument("--thickness", type=float, metavar="N", help="sheet thickness, to count passes (default: 1 pass)")
    e.add_argument("--pass-depth", type=float, metavar="N", help="max depth per pass (default: tool diameter, else one pass)")
    e.add_argument("--rapid-rate", type=float, metavar="N", help="non-cutting travel speed per minute (default 10000 mm/min)")

    o = p.add_argument_group("output")
    o.add_argument("-o", "--output-dir", type=Path, metavar="DIR", help="where to write sheet_NN.dxf/.png (default ./out)")
    o.add_argument("--dxf-version", choices=DXF_VERSIONS, metavar="VER",
                   help=f"DXF version to write: {', '.join(DXF_VERSIONS)} (default R2000; pre-R2007 labels are folded to ASCII)")
    o.add_argument("--no-png", action="store_true", help="do not write the PNG previews")
    o.add_argument("--png-width", type=int, default=2400, metavar="PX", help="width of the PNG previews (default 2400)")
    for key in ("sheet", "cut-outer", "cut-inner", "labels"):
        o.add_argument(f"--layer-{key}", metavar="NAME", help=f"DXF layer name for {key.replace('-', ' ')} "
                                                                f"(default {key.replace('-', '_').upper()})")

    m = p.add_argument_group("other")
    m.add_argument("--config", type=Path, metavar="FILE.toml", help="settings file; command line options override it")
    m.add_argument("--dxf-dir", type=Path, metavar="DIR", help="folder with the part DXFs (default: folder of PARTS.csv)")
    m.add_argument("--check-only", action="store_true", help="load and check the parts, do not nest or write anything")
    v = m.add_mutually_exclusive_group()
    v.add_argument("-v", "--verbose", action="store_true", help="also show info messages and list every piece")
    v.add_argument("-q", "--quiet", action="store_true", help="errors only, no summary")
    return p


def overrides_from_args(a: argparse.Namespace) -> dict:
    layers = {k: v for k, v in (("sheet", a.layer_sheet), ("cut_outer", a.layer_cut_outer),
                                ("cut_inner", a.layer_cut_inner), ("labels", a.layer_labels)) if v}
    return {
        "sheet": a.sheet, "margin": a.margin, "fit_spacing": a.fit_spacing, "grain": a.grain, "rotations": a.rotations,
        "units": a.units, "tool_diameter": a.tool_diameter, "tolerance": a.tolerance,
        "feed_rate": a.feed_rate, "thickness": a.thickness, "pass_depth": a.pass_depth, "rapid_rate": a.rapid_rate,
        "output_dir": a.output_dir, "dxf_version": a.dxf_version, "layers": layers or None,
    }


# ------------------------------------------------------------------ printing
def _print_issues(issues, err: TextIO, *, verbose: bool, quiet: bool) -> None:
    for i in visible_issues(issues, verbose=verbose, quiet=quiet):
        print(format_issue(i), file=err)


# ---------------------------------------------------------------------- main
def main(argv: Sequence[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out, err = stdout or sys.stdout, stderr or sys.stderr
    parser = build_parser(out, err)
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    verbose, quiet = args.verbose, args.quiet

    def say(*a) -> None:
        if not quiet:
            print(*a, file=out)

    # ---- configuration
    try:
        cfg = load_config(args.config, overrides_from_args(args))
    except ConfigError as e:
        print(f"dxfnest: error: {e}", file=err)
        return EXIT_USAGE
    if args.png_width < 200:
        print("dxfnest: error: --png-width must be at least 200", file=err)
        return EXIT_USAGE
    if not args.no_png and not args.check_only:
        try:                                              # fail now, not after the nesting is done
            import PIL  # noqa: F401
        except ImportError:
            print("dxfnest: error: PNG previews need the Pillow module (python3 -m pip install pillow); "
                  "install it or run with --no-png", file=err)
            return EXIT_USAGE

    # ---- quantities file
    try:
        requests = read_manifest(args.parts, base_dir=args.dxf_dir)
    except ManifestError as e:
        for problem in e.problems:
            print(f"error: {problem}", file=err)
        return EXIT_PROBLEMS

    # ---- run
    options = JobOptions(check_only=args.check_only, write_files=True, png=not args.no_png, png_width=args.png_width)
    result = run_job(requests, cfg, options)
    _print_issues(result.issues, err, verbose=verbose, quiet=quiet)
    if result.status is JobStatus.PARTS_FAILED:
        print(f"dxfnest: {stopped_line(result)}", file=err)
        return EXIT_PROBLEMS
    if result.status is JobStatus.CHECKED:
        say(f"{checked_line(result)}, nothing nested or written (--check-only)")
        return EXIT_OK
    if result.status is JobStatus.NEST_FAILED:
        print("dxfnest: stopped: nesting failed; nothing was written", file=err)
        return EXIT_PROBLEMS
    if result.status is JobStatus.VERIFY_FAILED:
        print("dxfnest: internal error: the layout failed verification; nothing was written", file=err)
        return EXIT_PROBLEMS
    if result.status is JobStatus.WRITE_FAILED:
        print(f"dxfnest: error: {result.error}", file=err)
        return EXIT_PROBLEMS

    if result.stale and not quiet:
        print(f"warning: {cfg.output_dir} still contains files from an earlier run that were not overwritten: "
              f"{', '.join(f.name for f in result.stale)}", file=err)
    for line in summary_lines(result, cfg, verbose=verbose):
        say(line)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
