"""Module 5a: rectangle-packing nester (bounding boxes, MaxRects).

    nest_rectangles(items, config) -> NestResult

Every piece is treated as its rotated bounding box. Notches are therefore not
interlocked: this is the baseline. What it does honour exactly:

  * per-part allowed rotations (grain rules, fit) from checks.NestItem;
  * margin (unusable band along the sheet edges);
  * fit_spacing between pieces (each box is grown by the spacing and the bin by
    the same amount, so pieces keep the gap but may touch the usable edge).

Search: instances sorted by one of several keys, packed first-fit over sheets with
a MaxRects free-rectangle list under one of several placement heuristics. All
(sort, heuristic) variants are tried and the best result is kept: fewest
unplaced pieces, then fewest sheets, then the least-used last sheet (which leaves
the most useful offcut). Fully deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .checks import NestItem
from .config import Config
from .grain import rotated_size
from .models import Issue, Layout, NestResult, Placement, Severity

_EPS = 1e-9
Rect = tuple[float, float, float, float]  # x, y, w, h

HEURISTICS = ("bssf", "blsf", "baf", "bl")
SORTS = ("area", "long", "short", "perimeter")


# --------------------------------------------------------------- MaxRects bin
def _score(heur: str, fx: float, fy: float, fw: float, fh: float, w: float, h: float) -> tuple:
    if heur == "bssf":   # best short side fit
        return (min(fw - w, fh - h), max(fw - w, fh - h))
    if heur == "blsf":   # best long side fit
        return (max(fw - w, fh - h), min(fw - w, fh - h))
    if heur == "baf":    # best area fit
        return (fw * fh - w * h, min(fw - w, fh - h))
    return (fy + h, fx)  # "bl": bottom-left


class MaxRectsBin:
    def __init__(self, width: float, height: float) -> None:
        self.width, self.height = width, height
        self.free: list[Rect] = [(0.0, 0.0, width, height)]
        self.used_area = 0.0

    def find(self, w: float, h: float, heur: str) -> tuple[tuple, float, float] | None:
        """Best (score, x, y) for a w x h box, or None."""
        best = None
        for fx, fy, fw, fh in self.free:
            if w <= fw + _EPS and h <= fh + _EPS:
                sc = _score(heur, fx, fy, fw, fh, w, h)
                if best is None or sc < best[0]:
                    best = (sc, fx, fy)
        return best

    def place(self, x: float, y: float, w: float, h: float) -> None:
        ux1, uy1 = x + w, y + h
        out: list[Rect] = []
        for fx, fy, fw, fh in self.free:
            fx1, fy1 = fx + fw, fy + fh
            if x >= fx1 - _EPS or ux1 <= fx + _EPS or y >= fy1 - _EPS or uy1 <= fy + _EPS:
                out.append((fx, fy, fw, fh))      # no overlap: keep
                continue
            if x > fx + _EPS:
                out.append((fx, fy, x - fx, fh))
            if ux1 < fx1 - _EPS:
                out.append((ux1, fy, fx1 - ux1, fh))
            if y > fy + _EPS:
                out.append((fx, fy, fw, y - fy))
            if uy1 < fy1 - _EPS:
                out.append((fx, uy1, fw, fy1 - uy1))
        # drop rectangles contained in another one
        keep: list[Rect] = []
        for i, a in enumerate(out):
            contained = any(
                j != i and b[0] <= a[0] + _EPS and b[1] <= a[1] + _EPS
                and a[0] + a[2] <= b[0] + b[2] + _EPS and a[1] + a[3] <= b[1] + b[3] + _EPS
                and (b != a or j < i)
                for j, b in enumerate(out))
            if not contained:
                keep.append(a)
        self.free = keep
        self.used_area += w * h


# ------------------------------------------------------------------- packing
@dataclass(frozen=True)
class _Cand:
    rotation: float
    w: float          # real bounding box
    h: float
    pw: float         # box grown by the spacing (what the packer places)
    ph: float


@dataclass(frozen=True)
class _Inst:
    order: int        # position in the input (item index, instance) for stable ties
    name: str
    instance: int
    cands: tuple[_Cand, ...]


def _sort_key(kind: str):
    def key(i: _Inst):
        c = i.cands[0]
        v = {"area": min(k.pw * k.ph for k in i.cands),
             "long": max(c.pw, c.ph),
             "short": min(c.pw, c.ph),
             "perimeter": c.pw + c.ph}[kind]
        return (-v, i.order)
    return key


def _best_in_bin(b: MaxRectsBin, inst: _Inst, heur: str):
    best = None
    for c in inst.cands:
        f = b.find(c.pw, c.ph, heur)
        if f is not None and (best is None or f[0] < best[0]):
            best = (f[0], f[1], f[2], c)
    return best


def _pack(instances: list[_Inst], bin_w: float, bin_h: float, heur: str):
    bins: list[MaxRectsBin] = []
    placed: list[list[tuple[_Inst, _Cand, float, float]]] = []
    unplaced: list[_Inst] = []
    for inst in instances:
        target = None
        for bi, b in enumerate(bins):
            hit = _best_in_bin(b, inst, heur)
            if hit:
                target = (bi, hit)
                break
        if target is None:
            fresh = MaxRectsBin(bin_w, bin_h)
            hit = _best_in_bin(fresh, inst, heur)
            if hit is None:
                unplaced.append(inst)
                continue
            bins.append(fresh)
            placed.append([])
            target = (len(bins) - 1, hit)
        bi, (_, x, y, c) = target
        bins[bi].place(x, y, c.pw, c.ph)
        placed[bi].append((inst, c, x, y))
    return bins, placed, unplaced


def nest_rectangles(items: Sequence[NestItem], cfg: Config) -> NestResult:
    sheet = cfg.sheet
    s = cfg.fit_spacing
    bin_w, bin_h = sheet.usable_width + s, sheet.usable_height + s

    instances: list[_Inst] = []
    for ii, item in enumerate(items):
        cands: list[_Cand] = []
        seen: set[tuple[float, float]] = set()
        for rot in item.rotations:
            w, h = rotated_size(item.part, rot)
            key = (round(w, 6), round(h, 6))
            if key not in seen:               # rotations 0/180 (90/270) share a box: keep the first
                seen.add(key)
                cands.append(_Cand(rot, w, h, w + s, h + s))
        for k in range(1, item.qty + 1):
            instances.append(_Inst(ii * 100000 + k, item.part.name, k, tuple(cands)))

    best = None
    for sort in SORTS:
        ordered = sorted(instances, key=_sort_key(sort))
        for heur in HEURISTICS:
            bins, placed, unplaced = _pack(ordered, bin_w, bin_h, heur)
            score = (len(unplaced), len(bins), bins[-1].used_area if bins else 0.0)
            if best is None or score < best[0]:
                best = (score, sort, heur, bins, placed, unplaced)

    _, sort, heur, bins, placed, unplaced = best
    layouts = []
    for bi, rows in enumerate(placed):
        pls = tuple(Placement(inst.name, inst.instance, c.rotation,
                              sheet.margin + x, sheet.margin + y, c.w, c.h)
                    for inst, c, x, y in rows)
        layouts.append(Layout(bi + 1, sheet, pls))

    issues: list[Issue] = []
    for inst in unplaced:
        issues.append(Issue(Severity.ERROR, "unplaced_part",
                            f"{inst.name} #{inst.instance} does not fit on an empty sheet", part=inst.name))
    areas = {i.part.name: i.part.area for i in items}
    u = cfg.units
    usable = sheet.usable_width * sheet.usable_height
    for l in layouts:
        used = sum(areas[p.part_name] for p in l.placements)
        issues.append(Issue(Severity.INFO, "sheet_utilisation",
                            f"sheet {l.index}: {len(l.placements)} piece(s), {100 * used / usable:.1f}% of the usable "
                            f"area is part material ({u.area(used)} of {u.area(usable)})"))
    issues.append(Issue(Severity.INFO, "nest_summary",
                        f"rectangle nesting: {len(layouts)} sheet(s) (sort '{sort}', heuristic '{heur}')"))
    return NestResult(tuple(layouts), tuple(issues))
