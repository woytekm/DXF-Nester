"""The whole job, from a list of parts to files on disk, with no printing.

    run_job(requests, config, options) -> JobResult

Shared by the command line and the GUI. Every problem is returned as data
(`issues`, `status`, `error`); callers decide how to show it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence

from .checks import NestItem, check_parts
from .config import Config
from .contours import PartBuildError, build_part
from .dxf_writer import write_sheet_dxfs
from .estimate import CutSummary, TimeEstimate, cut_summary, estimate_time
from .loader import DxfLoadError, load_dxf
from .models import Issue, Layout, Part, PartRequest, Severity
from .nest_rect import nest_rectangles
from .verify import verify_layouts


class JobStatus(str, Enum):
    OK = "ok"                          # nested (and written, if asked)
    CHECKED = "checked"                # check_only: parts load and pass the checks
    PARTS_FAILED = "parts_failed"      # a file failed to load or a check failed; nothing nested or written
    NEST_FAILED = "nest_failed"
    VERIFY_FAILED = "verify_failed"    # internal safety net: the layout did not verify
    WRITE_FAILED = "write_failed"      # `error` says why


@dataclass(frozen=True)
class JobOptions:
    check_only: bool = False
    write_files: bool = True           # False: nest and report only, touch nothing on disk
    png: bool = True                   # with write_files: also write the PNG previews
    png_width: int = 2400


@dataclass(frozen=True)
class JobResult:
    status: JobStatus
    issues: tuple[Issue, ...]
    items: tuple[NestItem, ...] = ()
    layouts: tuple[Layout, ...] = ()
    parts: Mapping[str, Part] = field(default_factory=dict)
    cuts: CutSummary | None = None
    estimate: TimeEstimate | None = None
    files: tuple[tuple[Path, ...], ...] = ()    # per sheet; empty tuples when nothing was written
    stale: tuple[Path, ...] = ()                # old sheet_NN files that were not overwritten
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in (JobStatus.OK, JobStatus.CHECKED)

    def count(self, severity: Severity) -> int:
        return sum(1 for i in self.issues if i.severity is severity)


def stale_outputs(directory: Path, n_sheets: int, extensions: Sequence[str]) -> list[Path]:
    stale = []
    for ext in extensions:
        for f in sorted(directory.glob(f"sheet_*.{ext}")):
            digits = f.stem.removeprefix("sheet_")
            if digits.isdigit() and int(digits) > n_sheets:
                stale.append(f)
    return stale


def run_job(requests: Sequence[PartRequest], cfg: Config, options: JobOptions = JobOptions()) -> JobResult:
    u = cfg.units

    # ---- load every part; collect every problem before giving up
    pairs: list[tuple[PartRequest, Part]] = []
    issues: list[Issue] = []
    failed = False
    for req in requests:
        try:
            raw = load_dxf(req.path, flatten_tol=cfg.tolerance, units=u)
            part = build_part(raw, tolerance=cfg.tolerance, flatten_tol=cfg.tolerance, units=u)
        except DxfLoadError as e:
            issues.append(Issue(Severity.ERROR, "load_failed", str(e), part=req.path.stem))
            failed = True
        except PartBuildError as e:
            issues.extend(e.issues)
            failed = True
        else:
            issues.extend(part.issues)
            pairs.append((req, part))

    checked = check_parts(pairs, cfg)
    issues.extend(checked.issues)
    parts = {p.name: p for _, p in pairs}
    if failed or not checked.ok:
        return JobResult(JobStatus.PARTS_FAILED, tuple(issues), checked.items, parts=parts)
    if options.check_only:
        return JobResult(JobStatus.CHECKED, tuple(issues), checked.items, parts=parts)

    # ---- nest, verify
    nested = nest_rectangles(checked.items, cfg)
    issues.extend(i for i in nested.issues if i.code != "sheet_utilisation")
    if not nested.ok:
        return JobResult(JobStatus.NEST_FAILED, tuple(issues), checked.items, parts=parts)
    problems = verify_layouts(nested.layouts, checked.items, cfg)
    if problems:
        issues.extend(problems)
        return JobResult(JobStatus.VERIFY_FAILED, tuple(issues), checked.items, parts=parts)

    layouts = nested.layouts
    cuts = cut_summary(layouts, parts)
    estimate = estimate_time(layouts, parts, cfg)
    common = dict(items=checked.items, layouts=layouts, parts=parts, cuts=cuts, estimate=estimate)
    if not options.write_files:
        return JobResult(JobStatus.OK, tuple(issues), files=tuple(() for _ in layouts), **common)

    # ---- write
    outdir = cfg.output_dir
    written: list[list[Path]] = [[] for _ in layouts]
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        for i, path in enumerate(write_sheet_dxfs(layouts, parts, cfg, outdir)):
            written[i].append(path)
        if options.png:
            from .png_render import write_layout_pngs
            for i, path in enumerate(write_layout_pngs(layouts, parts, cfg, outdir, width_px=options.png_width)):
                written[i].append(path)
    except ImportError as e:
        return JobResult(JobStatus.WRITE_FAILED, tuple(issues), error=f"PNG output needs Pillow ({e}); install it or use --no-png", **common)
    except OSError as e:
        return JobResult(JobStatus.WRITE_FAILED, tuple(issues), error=f"cannot write to {outdir}: {e}", **common)
    stale = stale_outputs(outdir, len(layouts), ["dxf"] + (["png"] if options.png else []))
    return JobResult(JobStatus.OK, tuple(issues), files=tuple(tuple(w) for w in written), stale=tuple(stale), **common)
