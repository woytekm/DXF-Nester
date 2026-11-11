"""Module 1: quantities file ("manifest").

Format: CSV with a header row containing the columns `filename` and `qty`
(any order, case-insensitive, extra columns ignored) and, optionally, `grain`
(alias `orientation`): one of

    along    the part's LONGER side must run along the sheet's grain
    across   the part's longer side must run across the grain
    any/empty  no requirement (the default)

(for a square bounding box the drawn X axis counts as the longer side). Delimiter is detected
from the header: comma, semicolon (Excel in PL locale) or tab. Blank lines and
lines starting with '#' are skipped. A UTF-8 BOM is accepted.

    filename;qty;grain
    side_panel.dxf;4;across
    shelf.dxf;8;

Relative filenames are resolved against `base_dir` (default: the manifest's
own directory). All problems are collected and reported together.
"""
from __future__ import annotations

import csv
import os
import re
from pathlib import Path

from .models import Grain, PartRequest

_DELIMITERS = ",;\t"
_QTY_RE = re.compile(r"[0-9]+", re.ASCII)
_GRAIN = {"": Grain.ANY, "any": Grain.ANY, "along": Grain.ALONG, "across": Grain.ACROSS}


class ManifestError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


def _detect_delimiter(header_line: str) -> str:
    best = max(_DELIMITERS, key=header_line.count)
    return best if header_line.count(best) else ","


def parse_manifest(
    text: str,
    base_dir: Path,
    *,
    must_exist: bool = True,
    source: str = "<manifest>",
) -> list[PartRequest]:
    numbered = [
        (no, line)
        for no, line in enumerate(text.splitlines(), start=1)
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not numbered:
        raise ManifestError([f"{source}: manifest is empty"])

    hdr_no, hdr_line = numbered[0]
    delim = _detect_delimiter(hdr_line)
    header = [c.strip().lower() for c in next(csv.reader([hdr_line], delimiter=delim))]
    missing = [c for c in ("filename", "qty") if c not in header]
    if missing:
        raise ManifestError(
            [f"{source}:{hdr_no}: header must contain 'filename' and 'qty' (missing: {', '.join(missing)})"]
        )
    i_file, i_qty = header.index("filename"), header.index("qty")
    i_grain = next((header.index(c) for c in ("grain", "orientation") if c in header), None)
    need = max(i_file, i_qty) + 1

    problems: list[str] = []
    requests: list[PartRequest] = []
    seen: dict[str, int] = {}

    for lineno, line in numbered[1:]:
        where = f"{source}:{lineno}"
        row = next(csv.reader([line], delimiter=delim))
        if len(row) < need:
            problems.append(f"{where}: expected at least {need} columns, got {len(row)}")
            continue
        name, qty_s = row[i_file].strip(), row[i_qty].strip()
        grain_s = row[i_grain].strip().lower() if i_grain is not None and len(row) > i_grain else ""

        ok = True
        if grain_s not in _GRAIN:
            problems.append(f"{where}: grain must be along, across or empty, got {grain_s!r}")
            ok = False
        if not name:
            problems.append(f"{where}: empty filename")
            continue
        if not _QTY_RE.fullmatch(qty_s) or int(qty_s) == 0:
            problems.append(f"{where}: qty must be a positive integer, got {qty_s!r}")
            ok = False

        path = Path(name)
        if not path.is_absolute():
            path = base_dir / path
        if path.suffix.lower() != ".dxf":
            problems.append(f"{where}: {name!r} is not a .dxf file")
            ok = False
        elif must_exist and not path.is_file():
            problems.append(f"{where}: file not found: {path}")
            ok = False

        key = os.path.normcase(str(path.resolve()))
        if key in seen:
            problems.append(f"{where}: {name!r} already listed on line {seen[key]}")
            ok = False
        else:
            seen[key] = lineno

        if ok:
            requests.append(PartRequest(path=path, qty=int(qty_s), grain=_GRAIN[grain_s]))

    if not problems and not requests:
        problems.append(f"{source}: manifest has a header but no entries")
    if problems:
        raise ManifestError(problems)
    return requests


def read_manifest(
    path: Path | str,
    base_dir: Path | str | None = None,
    *,
    must_exist: bool = True,
) -> list[PartRequest]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise ManifestError([f"cannot read manifest {path}: {e}"]) from None
    base = Path(base_dir) if base_dir is not None else path.parent
    return parse_manifest(text, base, must_exist=must_exist, source=str(path))
