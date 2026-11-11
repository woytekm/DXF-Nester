"""Module 2: DXF loader.

    load_dxf(path) -> RawPart

Reads one DXF and reduces its modelspace geometry to the neutral primitives of
models.py (LineSeg / ArcSeg / CircleSeg), in millimetres. It makes no geometric
decisions: no joining of segments, no outer/inner classification, no checks.
Those belong to modules 3 and 4.

Supported entities: LINE, ARC, CIRCLE, LWPOLYLINE, POLYLINE (2D, bulges kept as
arcs), SPLINE and ELLIPSE (approximated by line segments within `flatten_tol`),
INSERT (blocks exploded recursively). Everything else is ignored and reported.

Mirrored entities (extrusion = -Z, common in files drawn "from behind") are
converted to true WCS geometry; other extrusion directions are skipped.
"""
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Iterable, Iterator

import ezdxf
from ezdxf import recover

from .models import ArcSeg, CircleSeg, Issue, LineSeg, Point, Primitive, RawPart, Severity
from .units import MM, Units


class DxfLoadError(Exception):
    """The file cannot be read or has a property we cannot handle (e.g. units)."""


# $INSUNITS code -> (name, millimetres per unit). 0 = unitless.
_UNITS: dict[int, tuple[str, float | None]] = {
    0: ("unitless", None),
    1: ("inches", 25.4),
    2: ("feet", 304.8),
    4: ("millimetres", 1.0),
    5: ("centimetres", 10.0),
    6: ("metres", 1000.0),
    9: ("mils", 0.0254),
    10: ("yards", 914.4),
    13: ("microns", 0.001),
    14: ("decimetres", 100.0),
}

# Entity types that carry no cut geometry: ignoring them is INFO, not WARNING.
_ANNOTATION_TYPES = {
    "POINT", "TEXT", "MTEXT", "DIMENSION", "HATCH", "LEADER", "MLEADER",
    "ATTDEF", "ATTRIB", "IMAGE", "WIPEOUT", "TOLERANCE", "VIEWPORT",
}

_MAX_INSERT_DEPTH = 8
_EPS = 1e-9


# ----------------------------------------------------------------- reading ---

def _read(path: Path, issues: list[Issue]):
    try:
        return ezdxf.readfile(path)
    except OSError as e:
        raise DxfLoadError(f"cannot read {path}: {e}") from None
    except (ezdxf.DXFError, UnicodeError) as first:
        try:
            doc, _auditor = recover.readfile(path)
        except Exception:
            raise DxfLoadError(f"{path} is not a readable DXF file: {first}") from None
        issues.append(Issue(Severity.WARNING, "recovered_file",
                            "file structure was damaged and has been recovered; verify the result"))
        return doc


def _unit_scale(doc, issues: list[Issue], assume: Units) -> tuple[float, str]:
    code = int(doc.header.get("$INSUNITS", 0))
    if code not in _UNITS:
        raise DxfLoadError(f"unsupported drawing units ($INSUNITS = {code})")
    name, factor = _UNITS[code]
    if factor is None:
        issues.append(Issue(Severity.WARNING, "units_unspecified",
                            f"drawing has no units set ($INSUNITS = 0); assuming {assume.word}"))
        return assume.mm_per_unit, f"{assume.word} (assumed)"
    if factor != 1.0:
        issues.append(Issue(Severity.INFO, "units_converted",
                            f"drawing is in {name}; converted to millimetres (x{factor:g})"))
    return factor, name


# ------------------------------------------------------- entity iteration ---

def _explode(entities: Iterable, skipped: Counter, depth: int = 0) -> Iterator:
    for e in entities:
        if e.dxftype() != "INSERT":
            yield e
        elif depth >= _MAX_INSERT_DEPTH:
            skipped["INSERT (nesting too deep)"] += 1
        else:
            yield from _explode(e.virtual_entities(), skipped, depth + 1)


def _mirrored(e) -> bool | None:
    """True: extrusion -Z (mirror X). False: +Z. None: not parallel to XY."""
    x, y, z = e.dxf.get("extrusion", (0.0, 0.0, 1.0))
    if abs(x) < 1e-9 and abs(y) < 1e-9:
        return z < 0
    return None


# ------------------------------------------------------------- converters ---

def _line(a: Point, b: Point, layer: str) -> list[Primitive]:
    if math.dist(a, b) < _EPS:
        return []
    return [LineSeg(a, b, layer)]


def _bulge_segment(p: Point, q: Point, bulge: float, layer: str) -> list[Primitive]:
    """Segment p->q with DXF bulge (tan of a quarter of the included angle;
    >0 means counter-clockwise from p to q)."""
    if math.dist(p, q) < _EPS:
        return []
    if abs(bulge) < 1e-12:
        return [LineSeg(p, q, layer)]
    dx, dy = q[0] - p[0], q[1] - p[1]
    chord = math.hypot(dx, dy)
    offset = (chord / 2.0) * (1.0 - bulge * bulge) / (2.0 * bulge)  # along left normal
    cx = (p[0] + q[0]) / 2.0 - dy / chord * offset
    cy = (p[1] + q[1]) / 2.0 + dx / chord * offset
    radius = math.hypot(p[0] - cx, p[1] - cy)
    ap = math.degrees(math.atan2(p[1] - cy, p[0] - cx)) % 360.0
    aq = math.degrees(math.atan2(q[1] - cy, q[0] - cx)) % 360.0
    if bulge > 0:
        return [ArcSeg((cx, cy), radius, ap, aq, layer)]
    return [ArcSeg((cx, cy), radius, aq, ap, layer)]  # clockwise p->q == ccw q->p


def _polyline(verts: list[tuple[float, float, float]], closed: bool,
              k: float, mirror: bool, layer: str) -> list[Primitive]:
    pts = [((-x if mirror else x) * k, y * k, -b if mirror else b) for x, y, b in verts]
    n = len(pts)
    out: list[Primitive] = []
    for i in range(n if closed else n - 1):
        p, q = pts[i], pts[(i + 1) % n]
        out += _bulge_segment((p[0], p[1]), (q[0], q[1]), p[2], layer)
    return out


def _convert(e, k: float, flatten_tol: float, approximated: Counter) -> list[Primitive] | None:
    """Primitives for one entity; None = unsupported type; raises _Skip for
    supported types that cannot be used (e.g. wrong plane)."""
    t = e.dxftype()
    layer = e.dxf.layer

    if t == "LINE":
        s, f = e.dxf.start, e.dxf.end
        return _line((s.x * k, s.y * k), (f.x * k, f.y * k), layer)

    if t in ("ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE"):
        mirror = _mirrored(e)
        if mirror is None:
            raise _Skip(f"{t} (not parallel to XY plane)")

        if t == "CIRCLE":
            c = e.dxf.center
            return [CircleSeg(((-c.x if mirror else c.x) * k, c.y * k), e.dxf.radius * k, layer)]

        if t == "ARC":
            c = e.dxf.center
            a0, a1 = float(e.dxf.start_angle), float(e.dxf.end_angle)
            if mirror:
                a0, a1 = 180.0 - a1, 180.0 - a0
            a0, a1 = a0 % 360.0, a1 % 360.0
            center = ((-c.x if mirror else c.x) * k, c.y * k)
            if abs(a1 - a0) < 1e-9:  # start == end: DXF means a full circle
                return [CircleSeg(center, e.dxf.radius * k, layer)]
            return [ArcSeg(center, e.dxf.radius * k, a0, a1, layer)]

        if t == "LWPOLYLINE":
            verts = [(float(x), float(y), float(b)) for x, y, b in e.get_points("xyb")]
            return _polyline(verts, bool(e.closed), k, mirror, layer)

        if not e.is_2d_polyline:
            raise _Skip("POLYLINE (3D or mesh)")
        verts = [(v.dxf.location.x, v.dxf.location.y, v.dxf.get("bulge", 0.0)) for v in e.vertices]
        return _polyline(verts, bool(e.is_closed), k, mirror, layer)

    if t in ("SPLINE", "ELLIPSE"):
        pts = [(v.x * k, v.y * k) for v in e.flattening(flatten_tol / k)]
        approximated[t] += 1
        out: list[Primitive] = []
        for a, b in zip(pts, pts[1:]):
            out += _line(a, b, layer)
        return out

    return None


class _Skip(Exception):
    pass


# ------------------------------------------------------------------- main ---

def load_dxf(path: Path | str, *, flatten_tol: float = 0.01, units: Units = MM) -> RawPart:
    """`flatten_tol` is in mm. `units` is the user's unit system: drawings that
    declare no units are assumed to be drawn in it, and messages use it."""
    path = Path(path)
    issues: list[Issue] = []
    doc = _read(path, issues)
    k, units_name = _unit_scale(doc, issues, units)

    prims: list[Primitive] = []
    ignored: Counter = Counter()      # entity type -> count
    skipped: Counter = Counter()      # supported type, unusable instance
    approximated: Counter = Counter()

    for e in _explode(doc.modelspace(), skipped):
        try:
            result = _convert(e, k, flatten_tol, approximated)
        except _Skip as s:
            skipped[str(s)] += 1
            continue
        except (ezdxf.DXFError, ValueError, ZeroDivisionError):
            skipped[f"{e.dxftype()} (could not be converted)"] += 1
            continue
        if result is None:
            ignored[e.dxftype()] += 1
        else:
            prims.extend(result)

    for t, n in sorted(ignored.items()):
        sev = Severity.INFO if t in _ANNOTATION_TYPES else Severity.WARNING
        issues.append(Issue(sev, "ignored_entity", f"{n} {t} entit{'y' if n == 1 else 'ies'} ignored"))
    for what, n in sorted(skipped.items()):
        issues.append(Issue(Severity.WARNING, "skipped_entity", f"{n} x {what} skipped"))
    for t, n in sorted(approximated.items()):
        issues.append(Issue(Severity.INFO, "approximated_entity",
                            f"{n} {t} approximated by line segments (tolerance {units.len(flatten_tol)})"))

    issues = [Issue(i.severity, i.code, i.message, part=path.stem) for i in issues]
    return RawPart(name=path.stem, path=path, primitives=tuple(prims),
                   source_units=units_name, issues=tuple(issues))
