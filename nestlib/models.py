"""Shared data contracts.

Modules exchange only the types defined here (plus Config from config.py),
so any module can be tested or replaced in isolation. New types are added
together with the module that produces them.

Conventions: all lengths in millimetres; sheet X axis = width (first number of
"2500x1250"), Y axis = height.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from shapely.geometry import Polygon

Point = tuple[float, float]


class Grain(str, Enum):
    """Grain rule for a part, relative to the part's LONGER side:
    ALONG  = longer side runs along the sheet's grain,
    ACROSS = longer side runs across the grain,
    ANY    = no requirement."""

    ANY = "any"
    ALONG = "along"
    ACROSS = "across"


@dataclass(frozen=True)
class PartRequest:
    """One line of the quantities file: which DXF, how many pieces, grain rule."""

    path: Path
    qty: int
    grain: Grain = Grain.ANY

    @property
    def name(self) -> str:
        return self.path.stem


@dataclass(frozen=True)
class SheetSpec:
    """Plywood sheet as seen by the nester."""

    width: float
    height: float
    margin: float = 0.0  # unusable band along every edge

    @property
    def usable_width(self) -> float:
        return self.width - 2 * self.margin

    @property
    def usable_height(self) -> float:
        return self.height - 2 * self.margin


# ------------------------------------------------------------------ issues ---

class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Issue:
    """Something worth telling the user. `code` is stable and machine-testable."""

    severity: Severity
    code: str
    message: str
    part: str | None = None


# -------------------------------------------------------- neutral geometry ---
# Every DXF entity we support is reduced to these three primitives (mm, XY
# plane). Arcs are always counter-clockwise from start_angle to end_angle
# (degrees, in [0, 360)), exactly like the DXF ARC entity.

@dataclass(frozen=True)
class LineSeg:
    start: Point
    end: Point
    layer: str = "0"


@dataclass(frozen=True)
class ArcSeg:
    center: Point
    radius: float
    start_angle: float
    end_angle: float
    layer: str = "0"

    @property
    def start_point(self) -> Point:
        a = math.radians(self.start_angle)
        return (self.center[0] + self.radius * math.cos(a), self.center[1] + self.radius * math.sin(a))

    @property
    def end_point(self) -> Point:
        a = math.radians(self.end_angle)
        return (self.center[0] + self.radius * math.cos(a), self.center[1] + self.radius * math.sin(a))

    @property
    def sweep(self) -> float:
        """Counter-clockwise angular extent in degrees, in (0, 360]."""
        s = (self.end_angle - self.start_angle) % 360.0
        return s if s > 0.0 else 360.0


@dataclass(frozen=True)
class CircleSeg:
    center: Point
    radius: float
    layer: str = "0"


Primitive = LineSeg | ArcSeg | CircleSeg


# ----------------------------------------------------------------- loader ---

@dataclass(frozen=True)
class RawPart:
    """Output of module 2: everything geometric found in one DXF, in mm,
    with blocks exploded. No joining, no inside/outside decisions yet."""

    name: str                      # file stem
    path: Path
    primitives: tuple[Primitive, ...]
    source_units: str              # human-readable, e.g. "millimetres"
    issues: tuple[Issue, ...] = ()


# ------------------------------------------------------------------ loops ---
# Output of module 3. A closed contour is either a circle, or a closed chain of
# edges written exactly like LWPOLYLINE vertices: start point plus a bulge
# (0 = straight line; DXF convention: bulge = tan(sweep/4), >0 = counter-
# clockwise from start to end). end of edge i == start of edge i+1 (cyclic).

@dataclass(frozen=True)
class Edge:
    start: Point
    end: Point
    bulge: float = 0.0


@dataclass(frozen=True)
class PolyLoop:
    edges: tuple[Edge, ...]


@dataclass(frozen=True)
class CircleLoop:
    center: Point
    radius: float


Loop = PolyLoop | CircleLoop


@dataclass(frozen=True)
class Part:
    """One physical part, ready for nesting: bounding box at the origin,
    outer contour counter-clockwise, holes clockwise."""

    name: str
    path: Path
    outer: Loop
    holes: tuple[Loop, ...]
    polygon: Polygon               # outer + holes, arcs flattened, at the origin
    width: float                   # exact bounding box (X extent)
    height: float                  # exact bounding box (Y extent)
    area: float                    # of `polygon`, holes subtracted
    cut_length: float              # exact: outer + holes, mm
    source_offset: Point           # translation removed: source = part + offset
    issues: tuple[Issue, ...] = ()


# ------------------------------------------------------------ nesting result
# Output of module 5 (any nester). Sheet coordinates: origin at the sheet's
# lower-left corner, X = sheet width, Y = sheet height, millimetres.

@dataclass(frozen=True)
class Placement:
    """One physical piece on a sheet. The part is rotated counter-clockwise by
    `rotation` degrees about its own origin and then moved so that the lower-left
    corner of its rotated bounding box is at (x, y); width/height are that box."""

    part_name: str
    instance: int          # 1-based, among the qty of this part
    rotation: float
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Layout:
    """One sheet and what is cut from it."""

    index: int             # 1-based
    sheet: SheetSpec
    placements: tuple[Placement, ...]


@dataclass(frozen=True)
class NestResult:
    layouts: tuple[Layout, ...]
    issues: tuple[Issue, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(i.severity is Severity.ERROR for i in self.issues)
