"""Module 0: configuration.

Sources, lowest to highest precedence:
    built-in defaults  <  TOML file  <  explicit overrides (CLI args)

The CLI module will pass argparse values as `overrides`; keys whose value is
None are ignored, so "option not given" never clobbers the TOML file.

Units: every source is self-describing. Length values (sheet, margin, fit_spacing,
tool_diameter, tolerance, thickness, pass_depth, feed_rate and rapid_rate per minute) of a source are read in THAT source's `units` ("mm" default, or
"in"); the TOML file has a `units` key, the CLI has --mm / --in. Config stores
millimetres. `Config.units` is what we present results in: the CLI's choice if
given, else the TOML's, else mm.
"""
from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping

from .models import SheetSpec
from .units import MM, Units, get_units


class ConfigError(ValueError):
    """Invalid or inconsistent configuration."""


DEFAULT_FIT_SPACING = 10.0  # mm
DEFAULT_RAPID_RATE = 10000.0  # mm/min
DXF_VERSIONS = ("R2000", "R2004", "R2007", "R2010", "R2013", "R2018")


_SIZE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*[xX\u00d7*]\s*(\d+(?:[.,]\d+)?)\s*$")


def parse_size(text: str) -> tuple[float, float]:
    """'2500x1250' -> (2500.0, 1250.0). Accepts x, X, ×, * and decimal comma."""
    m = _SIZE_RE.match(text)
    if not m:
        raise ConfigError(f"invalid sheet size {text!r}, expected e.g. '2500x1250'")
    w, h = (float(g.replace(",", ".")) for g in m.groups())
    return w, h


@dataclass(frozen=True)
class LayerMap:
    """DXF layer names used in the output files."""

    sheet: str = "SHEET"
    cut_outer: str = "CUT_OUTER"
    cut_inner: str = "CUT_INNER"
    labels: str = "LABELS"

    def __post_init__(self) -> None:
        names = [self.sheet, self.cut_outer, self.cut_inner, self.labels]
        if any(not n.strip() for n in names):
            raise ConfigError("layer names must not be empty")
        if len(set(names)) != len(names):
            raise ConfigError(f"layer names must be distinct, got {names}")


@dataclass(frozen=True)
class Config:
    sheet_width: float
    sheet_height: float
    margin: float = 0.0        # mm, unusable band along every sheet edge
    fit_spacing: float = DEFAULT_FIT_SPACING  # mm, minimum gap between nested parts
    rotations: tuple[float, ...] = (0.0, 90.0, 180.0, 270.0)  # allowed, degrees
    tolerance: float = 0.01    # mm, endpoint matching when joining segments
    grain: str = "x"           # sheet axis the grain runs along: "x" (width) or "y" (height)
    tool_diameter: float | None = None  # mm; None = tool checks disabled
    feed_rate: float | None = None      # mm/min cutting feed; None = no time estimate
    thickness: float | None = None      # mm sheet thickness (for the number of passes)
    pass_depth: float | None = None     # mm max depth per pass (default: tool diameter, else one pass)
    rapid_rate: float = DEFAULT_RAPID_RATE  # mm/min non-cutting moves
    dxf_version: str = "R2000"  # version of the DXF files we write (R2000 = widest CAM support)
    output_dir: Path = Path("out")
    layers: LayerMap = field(default_factory=LayerMap)
    units: Units = MM          # input/presentation units; all lengths above are mm

    def __post_init__(self) -> None:
        errors: list[str] = []
        for name in ("sheet_width", "sheet_height", "margin", "fit_spacing", "tolerance"):
            if not math.isfinite(getattr(self, name)):
                errors.append(f"{name} must be a finite number")
        if errors:
            raise ConfigError("; ".join(errors))
        if self.sheet_width <= 0 or self.sheet_height <= 0:
            errors.append("sheet size must be positive")
        if self.margin < 0:
            errors.append("margin must be >= 0")
        if self.fit_spacing < 0:
            errors.append("fit_spacing must be >= 0")
        for name in ("tool_diameter", "feed_rate", "thickness", "pass_depth"):
            v = getattr(self, name)
            if v is not None and not (math.isfinite(v) and v > 0):
                errors.append(f"{name} must be > 0")
        if not (math.isfinite(self.rapid_rate) and self.rapid_rate > 0):
            errors.append("rapid_rate must be > 0")
        if self.tolerance <= 0:
            errors.append("tolerance must be > 0")
        if self.grain not in ("x", "y"):
            errors.append("grain must be 'x' or 'y'")
        if self.dxf_version not in DXF_VERSIONS:
            errors.append(f"dxf_version must be one of {', '.join(DXF_VERSIONS)}")
        if (
            self.sheet_width > 0
            and self.sheet_height > 0
            and self.margin >= 0
            and 2 * self.margin >= min(self.sheet_width, self.sheet_height)
        ):
            errors.append("margin leaves no usable sheet area")
        if not self.rotations:
            errors.append("at least one rotation is required")
        elif not all(math.isfinite(a) and 0.0 <= a < 360.0 for a in self.rotations):
            errors.append("rotations must be in [0, 360)")
        if errors:
            raise ConfigError("; ".join(errors))

    @property
    def sheet(self) -> SheetSpec:
        return SheetSpec(self.sheet_width, self.sheet_height, self.margin)


# ---------------------------------------------------------------- loading ---

def _num(key: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{key} must be a number, got {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{key} must be a number, got {value!r}") from None


_LENGTH_KEYS = ("sheet_width", "sheet_height", "margin", "fit_spacing", "tool_diameter", "tolerance",
                "feed_rate", "thickness", "pass_depth", "rapid_rate")   # feed/rapid: length per minute


def _normalise(source: Mapping[str, Any]) -> tuple[dict[str, Any], Units | None]:
    """Per source: expand the 'sheet' shorthand and convert every length from the
    source's own units to mm. Returns (values in mm, the source's units or None)."""
    d = dict(source)
    units: Units | None = None
    if "units" in d:
        try:
            units = get_units(str(d.pop("units")))
        except ValueError as e:
            raise ConfigError(str(e)) from None
    if "sheet" in d:
        if "sheet_width" in d or "sheet_height" in d:
            raise ConfigError("give either 'sheet' or 'sheet_width'/'sheet_height', not both")
        d["sheet_width"], d["sheet_height"] = parse_size(str(d.pop("sheet")))
    factor = (units or MM).mm_per_unit
    for key in _LENGTH_KEYS:
        if key in d:
            d[key] = _num(key, d[key]) * factor
    return d, units


def _layers(value: Any) -> LayerMap:
    if isinstance(value, LayerMap):
        return value
    if not isinstance(value, Mapping):
        raise ConfigError("layers must be a table of layer names")
    unknown = set(value) - {f.name for f in fields(LayerMap)}
    if unknown:
        raise ConfigError(f"unknown layer key(s): {', '.join(sorted(unknown))}")
    return LayerMap(**{k: str(v) for k, v in value.items()})


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except OSError as e:
        raise ConfigError(f"cannot read config file {path}: {e}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from None


def load_config(
    toml_path: Path | str | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Config:
    data: dict[str, Any] = {}
    display: Units | None = None
    layer_tables: list[Any] = []                      # `layers` merge key by key across sources
    sources = []
    if toml_path is not None:
        sources.append(_read_toml(Path(toml_path)))
    sources.append({k: v for k, v in (overrides or {}).items() if v is not None})
    for source in sources:
        values, units = _normalise(source)
        if "layers" in values:
            layer_tables.append(values.pop("layers"))
        data.update(values)
        display = units or display
    if layer_tables:
        merged: dict[str, Any] = {}
        for table in layer_tables:
            if isinstance(table, LayerMap):
                table = {f.name: getattr(table, f.name) for f in fields(LayerMap)}
            if not isinstance(table, Mapping):
                raise ConfigError("layers must be a table of layer names")
            merged.update(table)
        data["layers"] = merged

    unknown = set(data) - {f.name for f in fields(Config)}
    if unknown:
        raise ConfigError(f"unknown config key(s): {', '.join(sorted(unknown))}")
    if "sheet_width" not in data or "sheet_height" not in data:
        raise ConfigError('sheet size is required (--sheet WxH, or sheet = "WxH" in the config file)')

    if "rotations" in data:
        raw = data["rotations"]
        if isinstance(raw, str):
            raw = [p for p in raw.split(",") if p.strip()]
        angles = (_num("rotations", a) % 360.0 for a in raw)
        data["rotations"] = tuple(dict.fromkeys(angles))  # dedupe, keep order
    if "grain" in data:
        data["grain"] = str(data["grain"]).strip().lower()
    if "dxf_version" in data:
        data["dxf_version"] = str(data["dxf_version"]).strip().upper()
    if "output_dir" in data:
        data["output_dir"] = Path(data["output_dir"])
    if "layers" in data:
        data["layers"] = _layers(data["layers"])
    if "fit_spacing" not in data:
        # a tool wider than the default gap would make the default itself invalid
        tool = data.get("tool_diameter")
        data["fit_spacing"] = max(DEFAULT_FIT_SPACING, tool) if tool else DEFAULT_FIT_SPACING
    data["units"] = display or MM
    return Config(**data)
