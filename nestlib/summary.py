"""Human-readable text for a finished job. Shared by the command line and the GUI."""
from __future__ import annotations

from collections import Counter
from typing import Sequence

from .config import Config
from .estimate import describe_assumptions, format_duration
from .grain import describe, relation
from .job import JobResult
from .models import Grain, Issue, Severity


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def format_issue(i: Issue) -> str:
    where = f"{i.part}: " if i.part else ""
    return f"{i.severity.value}: {where}{i.message}"


def visible_issues(issues: Sequence[Issue], *, verbose: bool = False, quiet: bool = False) -> list[Issue]:
    """Errors always; warnings unless quiet; info only when verbose."""
    out = []
    for i in issues:
        if i.severity is Severity.INFO and not verbose:
            continue
        if i.severity is Severity.WARNING and quiet:
            continue
        out.append(i)
    return out


def stopped_line(result: JobResult) -> str:
    return (f"stopped: {plural(result.count(Severity.ERROR), 'error')}, "
            f"{plural(result.count(Severity.WARNING), 'warning')}; nothing was written")


def checked_line(result: JobResult) -> str:
    n = sum(i.qty for i in result.items)
    return (f"Checked {plural(len(result.items), 'part')} ({plural(n, 'piece')}): "
            f"{plural(result.count(Severity.WARNING), 'warning')}")


def orientation_lines(result: JobResult, cfg: Config) -> list[str]:
    counts: dict[tuple[str, float], int] = Counter()
    for l in result.layouts:
        for p in l.placements:
            counts[(p.part_name, p.rotation)] += 1
    rows = []
    for it in result.items:
        for (name, rot), n in sorted((k, v) for k, v in counts.items() if k[0] == it.part.name):
            rel = relation(it.part, rot, cfg.grain)
            rule = f"  [required: {it.grain.value}]" if it.grain is not Grain.ANY else ""
            rows.append((name, f"{n} pc{'s' if n != 1 else ''}", describe(rel), f"rotation {rot:g}\u00b0{rule}"))
    w = [max(len(r[i]) for r in rows) for i in range(3)] if rows else [0, 0, 0]
    return [f"  {a:<{w[0]}}  {b:>{w[1]}}  {c:<{w[2]}}  {d}" for a, b, c, d in rows]


def summary_lines(result: JobResult, cfg: Config, *, verbose: bool = False, show_files: bool = True) -> list[str]:
    """The text printed after a successful nesting. Empty strings are blank lines."""
    u = cfg.units
    sheet = cfg.sheet
    usable = sheet.usable_width * sheet.usable_height
    area = {name: p.area for name, p in result.parts.items()}
    n_pieces = sum(i.qty for i in result.items)
    L: list[str] = [
        f"Nested {plural(n_pieces, 'piece')} of {plural(len(result.items), 'part')} on "
        f"{plural(len(result.layouts), 'sheet')} ({u.size(sheet.width, sheet.height, u.label_decimals)}, "
        f"margin {u.len(cfg.margin, u.label_decimals)}, fit spacing {u.len(cfg.fit_spacing, u.label_decimals)}).",
        "",
    ]
    cuts = result.cuts
    for layout, files, sc in zip(result.layouts, result.files, cuts.sheets):
        used = sum(area[p.part_name] for p in layout.placements)
        tail = f"   {', '.join(str(f) for f in files)}" if files and show_files else ""
        L.append(f"  Sheet {layout.index}: {plural(len(layout.placements), 'piece'):<10} {100 * used / usable:5.1f}% material"
                 f"   cut {u.long_len(sc.length):>9}{tail}")
    L += ["", f"Cutting, all sheets: {u.long_len(cuts.length)} of contours "
              f"(outer {u.long_len(cuts.outer_length)}, holes {u.long_len(cuts.inner_length)}), "
              f"{plural(cuts.pierces, 'pierce')}"]
    est = result.estimate
    if est is not None:
        L.append(f"Estimated machining time: about {format_duration(est.total_s)}  "
                 f"(cutting {format_duration(est.cutting_s)}, plunging {format_duration(est.plunge_s)}, "
                 f"rapids {format_duration(est.rapid_s)})")
        L.append(f"  assumes: {describe_assumptions(est, cfg)}")
        if verbose:
            for layout, sec in zip(result.layouts, est.per_sheet_s):
                L.append(f"  sheet {layout.index}: about {format_duration(sec)}")
    elif verbose:
        L.append("No time estimate: give --feed-rate (and --thickness) to get one.")
    axis = "the X axis (sheet width)" if cfg.grain == "x" else "the Y axis (sheet height)"
    L += ["", f"Orientation relative to the grain, which runs along {axis}:"]
    L += orientation_lines(result, cfg)
    if verbose:
        L += ["", "Pieces:"]
        for layout in result.layouts:
            for p in layout.placements:
                L.append(f"  sheet {layout.index}  {p.part_name} #{p.instance:<3} rotation {p.rotation:>5g}\u00b0  "
                         f"at {u.xy((p.x, p.y), u.label_decimals)}  size {u.size(p.width, p.height, u.label_decimals)}")
    return L
