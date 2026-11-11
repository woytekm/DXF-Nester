"""What the GUI shows and edits, with no GUI toolkit in sight (so it can be tested).

    PartsTable    the table of DXF files with quantity and grain; CSV in and out
    GuiSettings   the option fields, as the strings typed into them
    load_state / save_state   remember the table and settings between sessions
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Iterable, Sequence

from .config import Config, load_config
from .manifest import read_manifest
from .models import Grain, Issue, PartRequest, Severity

GRAIN_CHOICES = tuple(g.value for g in Grain)         # ("any", "along", "across")
_SHORT = 70

# Short table labels for the common problems; the full message stays in the tooltip and the message box.
SHORT_STATUS = {
    "part_too_large": "too large for the sheet",
    "grain_unsatisfiable": "grain rule cannot be met",
    "feature_too_narrow": "slot too narrow for tool",
    "hole_smaller_than_tool": "hole smaller than tool",
    "inner_corner_rounded": "inner corners rounded",
    "open_contour": "contour is not closed",
    "branching_contour": "contours touch or overlap",
    "multiple_contours": "several outer contours",
    "overlapping_contours": "contours overlap",
    "nested_contour": "island inside a hole",
    "invalid_contour": "contour crosses itself",
    "no_contours": "no geometry found",
    "load_failed": "cannot read the file",
    "duplicate_part_name": "duplicate file name",
    "units_unspecified": "no units set (assumed)",
}


def _short(issue: Issue) -> str:
    if issue.code in SHORT_STATUS:
        return SHORT_STATUS[issue.code]
    msg = issue.message
    return msg if len(msg) <= _SHORT else msg[:_SHORT - 1] + "\u2026"


@dataclass
class Row:
    path: Path
    qty: int = 1
    grain: Grain = Grain.ANY
    level: str = ""          # "", "ok", "warning", "error": result of the last check/run
    status: str = ""         # short text for the table
    detail: str = ""         # full text for a tooltip

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def stem(self) -> str:
        return self.path.stem


@dataclass
class AddResult:
    added: int = 0
    duplicates: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


class PartsTable:
    def __init__(self) -> None:
        self.rows: list[Row] = []

    def __len__(self) -> int:
        return len(self.rows)

    # ------------------------------------------------------------ editing
    def add_files(self, paths: Iterable[Path | str]) -> AddResult:
        """Append .dxf files (quantity 1, grain any). Duplicates and other file types are skipped and reported."""
        res = AddResult()
        have = {_key(r.path) for r in self.rows}
        for p in paths:
            p = Path(p)
            if p.suffix.lower() != ".dxf":
                res.rejected.append(p.name)
            elif _key(p) in have:
                res.duplicates.append(p.name)
            else:
                self.rows.append(Row(p))
                have.add(_key(p))
                res.added += 1
        self.check_files()
        return res

    def set_qty(self, index: int, value: int | str) -> str | None:
        """Set the quantity. Returns an error message (and changes nothing) unless it is a positive whole number."""
        try:
            n = int(str(value).strip())
        except ValueError:
            return f"quantity must be a whole number, got {value!r}"
        if n < 1:
            return "quantity must be at least 1"
        self.rows[index].qty = n
        return None

    def set_grain(self, index: int, value: str) -> str | None:
        try:
            self.rows[index].grain = Grain(str(value).strip().lower() or "any")
        except ValueError:
            return f"grain must be one of {', '.join(GRAIN_CHOICES)}, got {value!r}"
        return None

    def remove(self, index: int) -> None:
        del self.rows[index]

    def clear(self) -> None:
        self.rows.clear()

    # ------------------------------------------------------------ CSV
    def load_csv(self, path: Path | str, *, replace: bool = True) -> int:
        """Load a parts CSV (same format the command line reads). Missing DXFs are allowed: they are
        flagged in the table. Raises ManifestError if the CSV itself is invalid. Returns rows added."""
        requests = read_manifest(path, must_exist=False)
        if replace:
            self.rows.clear()
        have = {_key(r.path) for r in self.rows}
        added = 0
        for rq in requests:
            if _key(rq.path) not in have:
                self.rows.append(Row(rq.path, rq.qty, rq.grain))
                have.add(_key(rq.path))
                added += 1
        self.check_files()
        return added

    def save_csv(self, path: Path | str) -> None:
        """Write filename;qty;grain. File names are relative to the CSV's folder when they lie below it."""
        path = Path(path)
        base = path.resolve().parent
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";", lineterminator="\n")
            w.writerow(["filename", "qty", "grain"])
            for r in self.rows:
                try:
                    name = str(r.path.resolve().relative_to(base))
                except ValueError:
                    name = str(r.path)
                w.writerow([name, r.qty, "" if r.grain is Grain.ANY else r.grain.value])

    def to_requests(self) -> list[PartRequest]:
        return [PartRequest(r.path, r.qty, r.grain) for r in self.rows]

    def total_pieces(self) -> int:
        return sum(r.qty for r in self.rows)

    # ------------------------------------------------------------ status
    def clear_status(self) -> None:
        for r in self.rows:
            r.level = r.status = r.detail = ""

    def check_files(self) -> None:
        """Flag rows whose DXF does not exist (a CSV can point at files that moved)."""
        for r in self.rows:
            if not r.path.is_file():
                r.level, r.status, r.detail = "error", "file not found", f"{r.path} does not exist"
            elif r.status == "file not found":
                r.level = r.status = r.detail = ""

    def apply_issues(self, issues: Sequence[Issue], *, ran_ok: bool = False) -> list[Issue]:
        """Put the outcome of a check/run onto the rows (matched by part name = file name without .dxf).
        Returns the issues that belong to no row. `ran_ok`: rows without problems are marked "ok"."""
        by_part: dict[str, list[Issue]] = {}
        general = []
        names = {r.stem for r in self.rows}
        for i in issues:
            if i.part in names:
                by_part.setdefault(i.part, []).append(i)
            elif i.severity is not Severity.INFO or i.part is None:
                general.append(i)
        for r in self.rows:
            mine = [i for i in by_part.get(r.stem, []) if i.severity is not Severity.INFO]
            errors = [i for i in mine if i.severity is Severity.ERROR]
            warnings = [i for i in mine if i.severity is Severity.WARNING]
            if errors:
                r.level, r.status = "error", _short(errors[0])
            elif warnings:
                r.level, r.status = "warning", _short(warnings[0])
            elif r.status != "file not found":
                r.level, r.status = ("ok", "ok") if ran_ok else ("", "")
            if mine:
                r.detail = "\n".join(f"{i.severity.value}: {i.message}" for i in mine)
            elif r.status != "file not found":
                r.detail = ""
        return general


# --------------------------------------------------------------------- settings
@dataclass
class GuiSettings:
    """Option fields exactly as typed; blank means "use the program's default"."""

    sheet: str = "2500x1250"
    units: str = "mm"
    margin: str = ""
    fit_spacing: str = ""
    grain: str = "x"
    tool_diameter: str = ""
    rotations: str = ""
    thickness: str = ""
    feed_rate: str = ""
    pass_depth: str = ""
    dxf_version: str = "R2000"
    output_dir: str = ""           # blank: an "out" folder next to the first part
    write_png: bool = True

    def to_overrides(self) -> dict:
        def blank(v: str):
            v = str(v).strip()
            return v or None
        return {
            "sheet": blank(self.sheet), "units": blank(self.units), "margin": blank(self.margin),
            "fit_spacing": blank(self.fit_spacing), "grain": blank(self.grain),
            "tool_diameter": blank(self.tool_diameter), "rotations": blank(self.rotations),
            "thickness": blank(self.thickness), "feed_rate": blank(self.feed_rate),
            "pass_depth": blank(self.pass_depth), "dxf_version": blank(self.dxf_version),
            "output_dir": blank(self.output_dir),
        }

    def build_config(self, rows: Sequence[Row] = ()) -> Config:
        """Raises ConfigError with a readable message for any bad field."""
        o = self.to_overrides()
        if o["output_dir"] is None and rows:
            o["output_dir"] = str(rows[0].path.resolve().parent / "out")
        return load_config(None, o)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GuiSettings":
        """Tolerant: unknown keys and wrongly typed values are ignored, so an old or damaged file never breaks startup."""
        base = cls()
        for f in fields(cls):
            if f.name in d and isinstance(d[f.name], type(getattr(base, f.name))):
                setattr(base, f.name, d[f.name])
        return base


# ------------------------------------------------------------------ persistence
def default_state_path() -> Path:
    return Path.home() / ".dxfnest_gui.json"


def save_state(path: Path | str, settings: GuiSettings, table: PartsTable, last_dir: str = "") -> bool:
    data = {"settings": settings.to_dict(), "last_dir": last_dir,
            "rows": [{"path": str(r.path), "qty": r.qty, "grain": r.grain.value} for r in table.rows]}
    try:
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except OSError:
        return False


def load_state(path: Path | str) -> tuple[GuiSettings, PartsTable, str]:
    """(settings, table, last_dir). A missing or damaged file gives defaults."""
    table = PartsTable()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (OSError, ValueError):
        return GuiSettings(), table, ""
    settings = GuiSettings.from_dict(data.get("settings", {}) if isinstance(data.get("settings"), dict) else {})
    for item in data.get("rows", []) if isinstance(data.get("rows"), list) else []:
        try:
            row = Row(Path(item["path"]), max(1, int(item["qty"])), Grain(item.get("grain", "any")))
        except (KeyError, TypeError, ValueError):
            continue
        table.rows.append(row)
    table.check_files()
    last = data.get("last_dir", "")
    return settings, table, last if isinstance(last, str) else ""
