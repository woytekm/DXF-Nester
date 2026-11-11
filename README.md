# !AI coded 

Simple DXF nester in python, GUI works weel on OS X. Coded using free version of Claude @2026.

# dxfnest

Nest DXF parts onto plywood sheets from the command line. Input: a CSV of parts and a
sheet size. Output: one DXF per sheet (for the CNC shop) and a PNG preview of each sheet.

```
python3 dxfnest.py parts.csv --sheet 2500x1250
```

## Install (no virtual environment)

You need Python 3.11 or newer (tested on 3.12) and three third-party modules in your normal
site-packages. There is nothing else to install: the program is the files in this folder.

| module | needed for | comes with it automatically |
|--------|------------|-----------------------------|
| `ezdxf` (>= 1.1) | reading and writing DXF | `numpy`, `pyparsing`, `typing_extensions`, `fonttools` |
| `shapely` (>= 2.0) | geometry checks, label placement, tool-reach analysis | `numpy` |
| `pillow` (>= 10.1) | PNG previews only (not needed with `--no-png`) | nothing |

```
python3 -m pip install ezdxf shapely pillow        # or: -r requirements.txt
python3 -m pip install --user ezdxf shapely pillow  # same, into your home folder: no sudo needed
```

* If pip refuses with "externally-managed-environment" (Homebrew and Debian Pythons do), add
  `--break-system-packages`, or use your system's own packages instead.
* If you install by hand without pip, install the four "comes with it" modules too.
* If something is missing, `dxfnest.py` says exactly what and prints the pip command; it does
  not dump a traceback.

## Run: the window

```
python3 dxfnest_gui.py              # start; remembers your table and options from last time
python3 dxfnest_gui.py parts.csv    # open a parts CSV right away
python3 dxfnest_gui.py --fresh      # start empty
```

It needs `dearpygui` in addition to the three modules above (`python3 -m pip install dearpygui`).

* **Parts table**: *Open CSV...* loads a parts CSV, *Add DXF files...* adds files (Ctrl- or Shift-click
  selects several), and every row has an editable quantity, a grain drop-down (`any`, `along`, `across`),
  a status column and a remove button. *Save CSV...* writes the table back in the format the command line
  reads. CSV rows that point at moved files are loaded and marked "file not found".
* **Sheet and options**: the same settings as the command line (sheet size, mm/in, margin, fit spacing,
  tool diameter, grain axis; under *More options*: rotations, feed rate, thickness, pass depth, DXF
  version, PNG on/off). Empty fields use the defaults shown in grey.
* **Check & preview** nests and shows the sheets (one tab each) without touching the disk.
  **Run & write files** also writes `sheet_NN.dxf` (and `.png`) to the output folder (empty = an `out`
  folder next to the first part). After a run each row says `ok`, a short warning/error, and the
  tooltip has the full text; the Messages box below has everything in full.
* The nesting runs in the background, so the window stays responsive.
* Your table and options are saved when you close the window, in `~/.dxfnest_gui.json`.
* Not in the window (use the command line or a config file): layer names, DXF tolerance, rapid rate.
* Text: it looks for Arial/Helvetica/DejaVu so that `Półka` shows correctly (`DXFNEST_FONT=/path/to/font.ttf`
  overrides); `--font-size N` changes the size. The file pickers are Dear PyGui's own, not macOS's.

## Run: the command line

From this folder:

```
python3 dxfnest.py parts.csv --sheet 2500x1250 -o out
python3 dxfnest.py --help
```

It also works from any other folder (`python3 /path/to/dxfnest.py ...`), and you can make it a
command by symlinking it into a folder on your PATH:

```
ln -s "$PWD/dxfnest.py" ~/bin/dxfnest      # then: dxfnest parts.csv --sheet 2500x1250
```

The `nestlib` folder next to `dxfnest.py` holds the modules; keep the two together.

## Tests (optional)

```
python3 -m pip install pytest               # cairosvg is optional: 4 SVG tests skip without it
python3 -m pytest
```

The window tests start the real GUI under `xvfb-run` (Linux) and are skipped where that is missing.

## The parts file

CSV with a header row. Comma, semicolon (what Excel writes in a Polish locale) or tab
separated; a UTF-8 BOM is fine. Blank lines and lines starting with `#` are ignored.

| column     | required | meaning |
|------------|----------|---------|
| `filename` | yes | a `.dxf` file, relative to the CSV (or to `--dxf-dir`) |
| `qty`      | yes | number of pieces |
| `grain`    | no (alias `orientation`) | `along`: the part's **longer side** runs along the sheet's grain; `across`: the longer side runs across it; empty or `any`: no requirement |

```
filename;qty;grain
Półka_typ_3.dxf;2;along
Przegroda_typ_3.dxf;1;
Szuflada_front.dxf;4;across
Szuflada_bok.dxf;8;
```

Each DXF must contain **one closed outer contour** (holes inside it are fine). The outline
may be drawn more than once; duplicates are removed so the cut length is not doubled.

## Options

Lengths are read in millimetres, or inches with `--in`, and results are shown in the same unit.

| option | meaning |
|--------|---------|
| `--sheet WxH` | sheet size; W is the X axis. Required (here or in the config file) |
| `--margin N` | unusable band along every sheet edge (default 0) |
| `--fit-spacing N` | minimum gap between parts (default 10 mm; with `--tool-diameter`, at least that) |
| `--grain x\|y` | sheet axis the grain runs along (default `x`, the sheet width) |
| `--rotations LIST` | allowed rotations, e.g. `0,90,180,270` (default) or `0,180` |
| `--mm` / `--in` | length unit for options, messages and the output DXF (default mm) |
| `--tool-diameter N` | enables tool checks (see below); default: no tool checks |
| `--tolerance N` | geometry tolerance (default 0.01 mm) |
| `--feed-rate N` | cutting feed per minute; **enables the machining-time estimate** |
| `--thickness N`, `--pass-depth N` | sheet thickness and max depth per pass (see below) |
| `--rapid-rate N` | non-cutting travel speed per minute (default 10000 mm/min) |
| `-o, --output-dir DIR` | where `sheet_NN.dxf/.png` go (default `./out`) |
| `--dxf-version VER` | R2000 (default), R2004, R2007, R2010, R2013, R2018 |
| `--no-png`, `--png-width PX` | skip / size the PNG previews (default 2400 px) |
| `--layer-sheet/-cut-outer/-cut-inner/-labels NAME` | DXF layer names |
| `--config FILE.toml` | settings file; command-line options override it |
| `--dxf-dir DIR` | folder with the part DXFs (default: folder of the CSV) |
| `--check-only` | load and check the parts, write nothing |
| `-v`, `-q` | more output (info messages, every piece) / errors only |

Settings file (same names as the options; its own `units` key says how *its* lengths are read):

```toml
units = "mm"
sheet = "2500x1250"
margin = 0
fit_spacing = 10
tool_diameter = 8
dxf_version = "R2010"

[layers]
labels = "OPISY"
```

## What it does

* **Rotation** is free by default, but only to orientations in which the part fits the sheet,
  so a part as long as the sheet simply cannot turn. A `grain` rule restricts it further; if it
  cannot be met the run stops with an explanation. The summary lists each part's orientation
  relative to the grain.
* **Spacing and margin** are exact: parts are never closer than `--fit-spacing`, and stay
  inside the sheet minus `--margin`.
* **Tool checks** (only with `--tool-diameter`): spacing smaller than the bit, slots/notches
  narrower than the bit and holes it does not fit into are **errors**; inner corners tighter
  than the bit radius are **warnings** (they will be rounded, so a mating part with a square
  corner will not seat fully).
* **Nothing is written** unless every part loads and every check passes.

## Cut length and time

Every run prints the **cut length** (sheet by sheet and in total, outer contours and holes separately)
and the number of **pierces** (plunge points: one per closed contour). It is exact, needs no
machine data, and is the figure a shop prices by. The same numbers are in each PNG caption.

With `--feed-rate` it also prints an **estimated machining time**, followed by the assumptions it used:

* passes = thickness / depth, where depth is `--pass-depth`, else the tool diameter, else the full
  thickness (one pass); no `--thickness` means one pass and plunge time is ignored;
* cutting time = contour length x passes / feed rate; plunging at half the feed rate;
  rapids = a nearest-neighbour tour over the contours at `--rapid-rate`.

It uses the contour length (the tool-centre path is a few percent longer or shorter) and leaves out
setup, tool changes, tabs, ramps, acceleration and sheet changes. It is an order-of-magnitude figure
for planning, not a quote.

## Output

`sheet_01.dxf`, `sheet_02.dxf`, ... in `--output-dir`, origin at the sheet's lower-left corner:

| layer | content | a cut? |
|-------|---------|--------|
| `SHEET` | sheet rectangle | no |
| `CUT_OUTER` | one closed polyline per piece (arcs kept as arcs) | yes |
| `CUT_INNER` | holes | yes |
| `LABELS` | text `name #n` on solid material of each piece | no |

Labels in R2000/R2004 files are folded to ASCII (`Półka` becomes `Polka`); R2007 and later keep
the original characters. The PNG shows what is in the DXF.

Exit codes (also of `dxfnest.py`): `0` done (warnings allowed), `1` problem with the parts or sheet (nothing written),
`2` usage, configuration or missing-module error.

## Folder layout

```
dxfnest.py            the command-line program
dxfnest_gui.py        the same with a window
nestlib/              the modules (package), keep next to dxfnest.py
requirements.txt      third-party modules (requirements-dev.txt: for the tests)
tests/                the test suite and four sample DXFs in tests/data
```

Inside `nestlib`: `manifest` (CSV) -> `loader` (DXF to lines/arcs/circles in mm) -> `chain` +
`contours` (closed loops, outer/holes, dedupe, origin) -> `checks` (+ `grain`, `tool`) ->
`nest_rect` (MaxRects) -> `verify` (independent check on the true outlines) -> `dxf_writer` +
`png_render` (`preview` makes SVGs), `estimate` (cut length, time). `job` runs the whole pipeline and returns data;
`cli` and `gui` (with `gui_model`, which holds the table and options without any toolkit) are two front ends on it.
