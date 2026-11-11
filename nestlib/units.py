"""Length units. Internally everything is millimetres; `Units` converts at the
edges: reading user input (config, CLI) and presenting lengths (messages,
reports, previews, output DXF).
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Point


@dataclass(frozen=True)
class Units:
    name: str              # "mm" | "in"
    mm_per_unit: float
    insunits: int          # DXF $INSUNITS code for output files
    decimals: int          # default precision for messages
    label_decimals: int    # precision for compact labels (preview)
    word: str              # long name, e.g. "millimetres"
    big_area_div: float    # mm^2 per "big" area unit (m^2 / ft^2)
    big_area_name: str
    long_div: float = 1000.0   # mm per "long" length unit (m / ft), for totals such as cut length
    long_name: str = "m"

    def to_mm(self, value: float) -> float:
        return value * self.mm_per_unit

    def from_mm(self, mm: float) -> float:
        return mm / self.mm_per_unit

    def num(self, mm: float, decimals: int | None = None) -> str:
        d = self.decimals if decimals is None else decimals
        s = f"{self.from_mm(mm):.{d}f}"
        return s[1:] if s.startswith("-") and float(s) == 0.0 else s  # no "-0.000"

    def len(self, mm: float, decimals: int | None = None) -> str:
        return f"{self.num(mm, decimals)} {self.name}"

    def xy(self, p: Point, decimals: int | None = None) -> str:
        return f"({self.num(p[0], decimals)}, {self.num(p[1], decimals)}) {self.name}"

    def size(self, w: float, h: float, decimals: int | None = None) -> str:
        return f"{self.num(w, decimals)} x {self.num(h, decimals)} {self.name}"

    def long_len(self, mm: float) -> str:
        """Totals such as cut length: metres or feet."""
        return f"{mm / self.long_div:.2f} {self.long_name}"

    def rate(self, mm_per_min: float) -> str:
        return f"{self.num(mm_per_min, 0 if self.name == 'mm' else 1)} {self.name}/min"

    def area(self, mm2: float) -> str:
        return f"{mm2 / self.big_area_div:.{3 if self.name == 'mm' else 2}f} {self.big_area_name}"


MM = Units("mm", 1.0, 4, 3, 1, "millimetres", 1e6, "m\u00b2")
INCH = Units("in", 25.4, 1, 4, 2, "inches", 25.4 ** 2 * 144.0, "ft\u00b2", 304.8, "ft")

_ALIASES = {"mm": MM, "millimetre": MM, "millimetres": MM, "millimeter": MM, "millimeters": MM,
            "in": INCH, "inch": INCH, "inches": INCH}


def get_units(name: str) -> Units:
    try:
        return _ALIASES[name.strip().lower()]
    except KeyError:
        raise ValueError(f"unknown units {name!r}, expected 'mm' or 'in'") from None
