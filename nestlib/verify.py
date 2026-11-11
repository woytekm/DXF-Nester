"""Independent layout verifier. Trusts nothing the nester says.

    verify_layouts(layouts, items, config) -> list[Issue]    (empty = valid)

Re-derives every placed outline from the Part and the Placement (layout.py) and
checks, on the TRUE flattened shapes rather than bounding boxes:

  * every instance 1..qty of every part is placed exactly once, nothing else;
  * the rotation is one the item allows (grain rules, fit);
  * the recorded bounding box matches the real rotated outline;
  * each piece lies inside the usable sheet area (sheet minus margin);
  * no two pieces on a sheet overlap or are closer than fit_spacing.

Run it in tests for every nester, and in the CLI as a safety net.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from .checks import NestItem
from .config import Config
from .layout import place_polygon
from .models import Issue, Layout, Severity


def _err(code: str, msg: str, part: str | None = None) -> Issue:
    return Issue(Severity.ERROR, code, msg, part=part)


def verify_layouts(layouts: Sequence[Layout], items: Sequence[NestItem], cfg: Config) -> list[Issue]:
    issues: list[Issue] = []
    by_name = {i.part.name: i for i in items}
    tol = cfg.tolerance
    sheet = cfg.sheet
    u = cfg.units

    if [l.index for l in layouts] != list(range(1, len(layouts) + 1)):
        issues.append(_err("bad_sheet_index", f"sheet indices are {[l.index for l in layouts]}, expected 1..{len(layouts)}"))

    seen: dict[str, list[int]] = defaultdict(list)
    for layout in layouts:
        polys = []
        for pl in layout.placements:
            item = by_name.get(pl.part_name)
            if item is None:
                issues.append(_err("unknown_part", f"sheet {layout.index}: placement of unknown part '{pl.part_name}'"))
                continue
            seen[pl.part_name].append(pl.instance)
            if not any(abs(pl.rotation - r) < 1e-9 for r in item.rotations):
                issues.append(_err("rotation_not_allowed",
                                   f"sheet {layout.index}: {pl.part_name} #{pl.instance} rotated {pl.rotation:g}, "
                                   f"allowed {', '.join(f'{r:g}' for r in item.rotations)}", pl.part_name))
            poly = place_polygon(item.part, pl)
            x0, y0, x1, y1 = poly.bounds
            if (abs(x0 - pl.x) > tol or abs(y0 - pl.y) > tol
                    or abs((x1 - x0) - pl.width) > tol or abs((y1 - y0) - pl.height) > tol):
                issues.append(_err("bbox_mismatch",
                                   f"sheet {layout.index}: {pl.part_name} #{pl.instance} recorded box does not match "
                                   f"its outline", pl.part_name))
            if (x0 < sheet.margin - tol or y0 < sheet.margin - tol
                    or x1 > sheet.width - sheet.margin + tol or y1 > sheet.height - sheet.margin + tol):
                issues.append(_err("outside_usable_area",
                                   f"sheet {layout.index}: {pl.part_name} #{pl.instance} reaches "
                                   f"({u.num(x0)}, {u.num(y0)})-({u.num(x1)}, {u.num(y1)}) {u.name}, outside the "
                                   f"usable area", pl.part_name))
            polys.append((pl, poly))

        for i, (a, pa) in enumerate(polys):
            for b, pb in polys[i + 1:]:
                # touching along an edge is legal (zero spacing); overlapping interiors never is
                if pa.intersects(pb) and pa.intersection(pb).area > 1e-6:
                    issues.append(_err("overlap", f"sheet {layout.index}: {a.part_name} #{a.instance} overlaps "
                                                  f"{b.part_name} #{b.instance}"))
                elif pa.distance(pb) < cfg.fit_spacing - tol:
                    issues.append(_err("spacing_violation",
                                       f"sheet {layout.index}: {a.part_name} #{a.instance} and {b.part_name} "
                                       f"#{b.instance} are {u.len(pa.distance(pb))} apart, fit spacing is "
                                       f"{u.len(cfg.fit_spacing)}"))

    for name, item in by_name.items():
        got = sorted(seen.get(name, []))
        want = list(range(1, item.qty + 1))
        if got != want:
            missing = sorted(set(want) - set(got))
            extra = sorted(i for i in set(got) if got.count(i) > 1 or i not in want)
            issues.append(_err("instance_mismatch",
                               f"{name}: expected instances 1..{item.qty}, missing {missing}, duplicated/extra {extra}", name))
    return issues
