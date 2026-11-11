"""Every DXF in tests/data must go through loader + contours cleanly.

Drop a new sample file into tests/data and the generic invariants below cover it
automatically; add an entry to EXPECTED once you have checked its numbers by hand.
"""
import math
from pathlib import Path

import pytest
from shapely.geometry import Point

from nestlib.contours import build_part
from nestlib.geometry import arc_from_bulge, loop_points, signed_area
from nestlib.loader import load_dxf
from nestlib.models import PolyLoop, Severity

DATA = Path(__file__).parent / "data"
FILES = sorted(DATA.glob("*.dxf"))

# name -> (width, height, area, cut_length, duplicate segments removed)
# Values derived by hand from the drawings, not read back from the code.
EXPECTED = {
    "polka_typ_3": (2500.0, 300.0, 2500 * 300 - 4 * 16.6 * 150.8, 2 * (2500 + 300) + 4 * 301.6, 20),
    "przegroda_typ_3": (2070.0, 300.0, 2070 * 300 - 5 * 16.6 * 150.8, 2 * (2070 + 300) + 5 * 301.6, 24),
    "szuflada_bok": (200.0, 272.0, 200 * 272, 2 * (200 + 272), 3),
    # 170 mm wide handle cut-out, 42.79 deep, two R20 inner corners
    "szuflada_front": (610.0, 331.0,
                       610 * 331 - (170 * 42.79 - 2 * (1 - math.pi / 4) * 20 ** 2),
                       2 * (610 + 331) - 170 + 2 * 22.79 + 130 + 2 * (math.pi / 2 * 20), 10),
}


@pytest.fixture(scope="module", params=FILES, ids=lambda p: p.stem)
def part(request):
    return build_part(load_dxf(request.param))


def by_name(name):
    return build_part(load_dxf(DATA / f"{name}.dxf"))


def test_corpus_not_empty():
    assert len(FILES) >= 4


def test_generic_invariants(part):
    assert not [i for i in part.issues if i.severity is Severity.ERROR]
    assert part.polygon.is_valid and not part.polygon.is_empty
    assert part.polygon.bounds == pytest.approx((0.0, 0.0, part.width, part.height), abs=0.02)
    assert signed_area(loop_points(part.outer, 0.01)) > 0
    assert all(signed_area(loop_points(h, 0.01)) < 0 for h in part.holes)
    assert 0 < part.area <= part.width * part.height
    assert part.cut_length >= 2 * (part.width + part.height) - 1e-6
    assert all(i.part == part.name for i in part.issues)
    if isinstance(part.outer, PolyLoop):  # closed, continuous chain
        e = part.outer.edges
        assert all(math.dist(a.end, b.start) < 1e-9 for a, b in zip(e, e[1:] + e[:1]))


def test_known_values(part):
    if part.name not in EXPECTED:
        pytest.skip("no hand-checked expectation yet")
    w, h, area, cut, dups = EXPECTED[part.name]
    assert (part.width, part.height) == (pytest.approx(w), pytest.approx(h))
    assert part.area == pytest.approx(area, abs=1.0)
    assert part.cut_length == pytest.approx(cut, abs=0.01)
    msg = next(i.message for i in part.issues if i.code == "duplicate_geometry")
    assert msg.startswith(f"{dups} duplicate")


def test_every_sample_has_its_outline_drawn_twice():
    # Pattern in this CAD's exports: without de-duplication every cut length doubles.
    for f in FILES:
        p = build_part(load_dxf(f))
        assert any(i.code == "duplicate_geometry" for i in p.issues), f.name


def test_przegroda_five_open_slots():
    p = by_name("przegroda_typ_3")
    assert p.holes == ()
    for k in range(5):
        x = 319.2 + 350 * k + 8.3                          # centre of slot k
        assert not p.polygon.contains(Point(x, 100)), k    # inside the slot
        assert p.polygon.contains(Point(x, 200)), k        # solid above the slot
        assert p.polygon.contains(Point(x + 100, 100)), k  # solid between slots


def test_front_handle_cutout_has_two_concave_r20_corners():
    p = by_name("szuflada_front")
    arcs = [e for e in p.outer.edges if e.bulge]
    assert len(arcs) == 2
    for e in arcs:
        _, r, _, sweep = arc_from_bulge(e.start, e.end, e.bulge)
        assert r == pytest.approx(20.0) and sweep == pytest.approx(-90.0)  # clockwise = concave in a ccw loop
    assert not p.polygon.contains(Point(300, 10))    # in the cut-out
    assert not p.polygon.contains(Point(300, 40))    # just under its top edge
    assert p.polygon.contains(Point(300, 60))        # solid above it
    # rounded corner around centre (240, 22.79), R20: the corner zone outside that circle is material
    assert p.polygon.contains(Point(222, 40))        # 24.9 mm from the centre -> solid
    assert not p.polygon.contains(Point(235, 35))    # 13.2 mm from the centre -> cut-out
