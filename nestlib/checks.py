"""Module 4: feasibility checks and per-part rotation sets.

    check_parts(pairs, config) -> CheckResult(items, issues)

`pairs` is [(PartRequest, Part)] in manifest order. For every part this decides
which rotations the nester may use: those from `config.rotations` that satisfy
the part's grain rule and make the part fit on an empty usable sheet area.
Rotation is therefore "forced" only as far as needed: long parts simply end up
with fewer (or one) allowed rotation, and a grain rule that cannot be met is an
error, not a silent fallback.

If `config.tool_diameter` is set (otherwise none of this runs):
  * fit_spacing smaller than the tool is an error (the bit cannot pass between
    neighbouring parts);
  * per part, slots/notches the tool cannot reach and holes it does not fit
    into are errors; inner corners it will round to its radius are warnings
    (see tool.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .config import Config
from .grain import allowed_rotations, relation
from .models import Grain, Issue, Part, PartRequest, Severity
from .tool import analyse_tool


@dataclass(frozen=True)
class NestItem:
    """Input unit for the nester: a part, how many, and where it may rotate."""

    part: Part
    qty: int
    grain: Grain
    rotations: tuple[float, ...]   # non-empty


@dataclass(frozen=True)
class CheckResult:
    items: tuple[NestItem, ...]
    issues: tuple[Issue, ...]

    @property
    def ok(self) -> bool:
        return not any(i.severity is Severity.ERROR for i in self.issues)


def _angles(rotations: Sequence[float]) -> str:
    return ", ".join(f"{a:g}" for a in rotations)


def check_parts(pairs: Sequence[tuple[PartRequest, Part]], cfg: Config) -> CheckResult:
    u = cfg.units
    sheet = cfg.sheet
    uw, uh = sheet.usable_width, sheet.usable_height
    slack = cfg.tolerance
    issues: list[Issue] = []
    items: list[NestItem] = []

    names: dict[str, int] = {}
    for _, part in pairs:
        names[part.name] = names.get(part.name, 0) + 1
    for name, n in names.items():
        if n > 1:
            issues.append(Issue(Severity.ERROR, "duplicate_part_name",
                                f"{n} different files are named '{name}'; part names must be unique", part=name))

    tool = cfg.tool_diameter
    if tool is not None and cfg.fit_spacing + slack < tool:
        issues.append(Issue(Severity.ERROR, "spacing_below_tool",
                            f"fit spacing {u.len(cfg.fit_spacing)} is smaller than the tool diameter {u.len(tool)}: "
                            f"the tool cannot pass between neighbouring parts"))

    for req, part in pairs:
        def add(sev: Severity, code: str, msg: str) -> None:
            issues.append(Issue(sev, code, msg, part=part.name))

        def allowed(rule: Grain, rotations=cfg.rotations, w=uw, h=uh) -> tuple[float, ...]:
            return allowed_rotations(part, rule, rotations, cfg.grain, w, h, slack)

        tool_failed = False
        if tool is not None:
            tf = analyse_tool(part, tool, slack)
            d = u.label_decimals   # sizes/positions come from the flattened polygon: show compact precision
            if tf.small_holes:
                tool_failed = True
                smallest = min(tf.small_holes, key=lambda f: min(f.width, f.height))
                add(Severity.ERROR, "hole_smaller_than_tool",
                    f"{len(tf.small_holes)} hole(s) the {u.len(tool)} tool does not fit into "
                    f"(e.g. {u.size(smallest.width, smallest.height, d)} near {u.xy(smallest.where, d)})")
            if tf.unreachable:
                tool_failed = True
                f0 = tf.unreachable[0]
                add(Severity.ERROR, "feature_too_narrow",
                    f"{len(tf.unreachable)} region(s) the {u.len(tool)} tool cannot reach (slot narrower than "
                    f"the tool, or an acute notch), e.g. {u.size(f0.width, f0.height, d)} near {u.xy(f0.where, d)}")
            if tf.rounded_corners:
                f0 = tf.rounded_corners[0]
                add(Severity.WARNING, "inner_corner_rounded",
                    f"{len(tf.rounded_corners)} inner corner(s) tighter than the tool radius "
                    f"{u.len(tool / 2)} will be rounded to that radius, e.g. near {u.xy(f0.where, d)}; "
                    f"a mating part with a square corner will not seat fully unless the corner is relieved")

        fits_any = allowed(Grain.ANY)
        if not fits_any:
            hints = []
            if cfg.margin > 0 and allowed(Grain.ANY, w=sheet.width, h=sheet.height):
                hints.append("it would fit without the margin")
            if allowed(Grain.ANY, rotations=(0.0, 90.0, 180.0, 270.0)):
                hints.append("it would fit if 90-degree rotations were allowed")
            tool_failed = True
            add(Severity.ERROR, "part_too_large",
                f"part {u.size(part.width, part.height)} does not fit the usable sheet area "
                f"{u.size(uw, uh)} in any allowed rotation ({_angles(cfg.rotations)}; sheet "
                f"{u.size(sheet.width, sheet.height)}, margin {u.len(cfg.margin)})"
                + (f"; {'; '.join(hints)}" if hints else ""))
            continue

        final = allowed(req.grain)
        if not final:
            rels = {relation(part, r, cfg.grain) for r in fits_any}
            if rels <= {None}:
                why = "grain rules need rotations that are multiples of 90 degrees"
            else:
                other = Grain.ACROSS if req.grain is Grain.ALONG else Grain.ALONG
                why = (f"in that orientation the part {u.size(part.width, part.height)} does not fit the "
                       f"usable sheet area {u.size(uw, uh)}; it can only be placed with its longer side "
                       f"{other.value} the grain")
            add(Severity.ERROR, "grain_unsatisfiable",
                f"grain rule '{req.grain.value}' (longer side {req.grain.value} the grain) cannot be met: {why}")
            continue

        if len(final) < len(cfg.rotations):
            reasons = []
            if len(fits_any) < len(cfg.rotations):
                reasons.append("does not fit the sheet in the other rotations")
            if len(final) < len(fits_any):
                reasons.append(f"grain rule '{req.grain.value}'")
            add(Severity.INFO, "rotation_restricted",
                f"placed only at rotation {_angles(final)} ({'; '.join(reasons)})")
        if not tool_failed:
            items.append(NestItem(part, req.qty, req.grain, final))

    if items:
        total = sum(i.part.area * i.qty for i in items)
        n = max(1, math.ceil(total / (uw * uh) - 1e-9))
        issues.append(Issue(Severity.INFO, "min_sheets",
                            f"total part area {u.area(total)} on usable sheet area {u.area(uw * uh)}: "
                            f"at least {n} sheet(s) needed"))
    return CheckResult(tuple(items), tuple(issues))
