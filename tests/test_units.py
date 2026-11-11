import pytest

from nestlib.units import INCH, MM, get_units


def test_conversions_round_trip():
    assert INCH.to_mm(1) == 25.4 and INCH.from_mm(25.4) == 1.0
    assert MM.to_mm(7.5) == 7.5
    assert INCH.from_mm(INCH.to_mm(3.3)) == pytest.approx(3.3)


def test_formatting():
    assert MM.len(2500.0) == "2500.000 mm" and MM.len(2500.0, 0) == "2500 mm"
    assert INCH.len(12.7) == "0.5000 in"
    assert MM.xy((10, 2.5)) == "(10.000, 2.500) mm"
    assert INCH.size(2500, 1250) == "98.4252 x 49.2126 in"
    assert INCH.size(2500, 1250, INCH.label_decimals) == "98.43 x 49.21 in"


def test_no_negative_zero():
    assert MM.num(-0.0004) == "0.000" and MM.xy((-0.0001, 1)) == "(0.000, 1.000) mm"
    assert MM.num(-0.5) == "-0.500"


def test_area():
    assert MM.area(2_500_000) == "2.500 m\u00b2"
    assert INCH.area(25.4 ** 2 * 144) == "1.00 ft\u00b2"


@pytest.mark.parametrize("name, units", [("mm", MM), (" MM ", MM), ("millimetres", MM), ("in", INCH),
                                         ("Inch", INCH), ("inches", INCH)])
def test_get_units(name, units):
    assert get_units(name) is units


def test_get_units_rejects_unknown():
    with pytest.raises(ValueError, match="unknown units"):
        get_units("furlongs")


def test_dxf_insunits_codes():
    assert MM.insunits == 4 and INCH.insunits == 1


def test_long_length_and_rate():
    assert MM.long_len(35214.4) == "35.21 m" and INCH.long_len(304.8 * 12) == "12.00 ft"
    assert MM.rate(6000) == "6000 mm/min" and INCH.rate(6000) == "236.2 in/min"
