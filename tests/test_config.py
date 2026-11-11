import pytest

from nestlib.config import Config, ConfigError, LayerMap, load_config, parse_size
from nestlib.models import SheetSpec
from nestlib.units import INCH, MM


# ---------------------------------------------------------------- parse_size
@pytest.mark.parametrize(
    "text, expected",
    [
        ("2500x1250", (2500.0, 1250.0)),
        (" 2500 X 1250 ", (2500.0, 1250.0)),
        ("2440,5x1220", (2440.5, 1220.0)),
        ("1525\u00d71525", (1525.0, 1525.0)),
        ("2500*1250", (2500.0, 1250.0)),
    ],
)
def test_parse_size_ok(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "2500", "2500x", "x1250", "abc", "2500x1250x3", "-5x10"])
def test_parse_size_bad(text):
    with pytest.raises(ConfigError):
        parse_size(text)


# ------------------------------------------------------------------ defaults
def test_minimal_config_uses_defaults():
    cfg = load_config(overrides={"sheet": "2500x1250"})
    assert (cfg.sheet_width, cfg.sheet_height) == (2500.0, 1250.0)
    assert cfg.margin == 0.0 and cfg.fit_spacing == 10.0 and cfg.grain == "x" and cfg.units == MM
    assert cfg.rotations == (0.0, 90.0, 180.0, 270.0)
    assert cfg.layers == LayerMap()


def test_sheet_property():
    cfg = load_config(overrides={"sheet": "2500x1250", "margin": 15})
    assert cfg.sheet == SheetSpec(2500.0, 1250.0, 15.0)
    assert cfg.sheet.usable_width == 2470.0
    assert cfg.sheet.usable_height == 1220.0


def test_sheet_size_is_required():
    with pytest.raises(ConfigError, match="sheet size is required"):
        load_config()


# ------------------------------------------------------------- TOML + merge
def write(tmp_path, text):
    p = tmp_path / "cfg.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_toml_values(tmp_path):
    p = write(tmp_path, 'sheet = "3050x1525"\nmargin = 5\nfit_spacing = 8.5\nrotations = [0, 180]\noutput_dir = "o"\n'
                        '[layers]\ncut_outer = "OUT"\n')
    cfg = load_config(p)
    assert (cfg.sheet_width, cfg.sheet_height) == (3050.0, 1525.0)
    assert cfg.margin == 5.0 and cfg.fit_spacing == 8.5
    assert cfg.rotations == (0.0, 180.0)
    assert str(cfg.output_dir) == "o"
    assert cfg.layers.cut_outer == "OUT" and cfg.layers.cut_inner == "CUT_INNER"


def test_overrides_beat_toml_and_none_is_ignored(tmp_path):
    p = write(tmp_path, 'sheet = "3050x1525"\nfit_spacing = 8\n')
    cfg = load_config(p, {"fit_spacing": 12, "margin": None, "sheet": "2500x1250"})
    assert cfg.fit_spacing == 12.0
    assert cfg.margin == 0.0  # None did not clobber the default
    assert (cfg.sheet_width, cfg.sheet_height) == (2500.0, 1250.0)


def test_partial_size_override_of_toml_sheet(tmp_path):
    p = write(tmp_path, 'sheet = "3050x1525"\n')
    cfg = load_config(p, {"sheet_width": 2000})
    assert (cfg.sheet_width, cfg.sheet_height) == (2000.0, 1525.0)


def test_sheet_and_width_in_same_source_is_error():
    with pytest.raises(ConfigError, match="not both"):
        load_config(overrides={"sheet": "1x1", "sheet_width": 5})


def test_unknown_key_is_error():
    with pytest.raises(ConfigError, match="unknown config key"):
        load_config(overrides={"sheet": "2500x1250", "spacin": 5})


def test_bad_toml_and_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(write(tmp_path, "sheet = = 3"))
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "nope.toml")


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"margin": -1}, "margin must be >= 0"),
        ({"fit_spacing": -0.1}, "fit_spacing must be >= 0"),
        ({"tolerance": 0}, "tolerance must be > 0"),
        ({"margin": 625}, "no usable sheet area"),
        ({"rotations": []}, "at least one rotation"),
        ({"fit_spacing": "abc"}, "fit_spacing must be a number"),
        ({"margin": float("nan")}, "finite"),
    ],
)
def test_validation(overrides, message):
    with pytest.raises(ConfigError, match=message):
        load_config(overrides={"sheet": "2500x1250", **overrides})


def test_rotations_normalised_and_deduped():
    cfg = load_config(overrides={"sheet": "2500x1250", "rotations": [-90, 360, 0, 270]})
    assert cfg.rotations == (270.0, 0.0)


def test_rotations_from_comma_string():
    cfg = load_config(overrides={"sheet": "2500x1250", "rotations": "0, 90"})
    assert cfg.rotations == (0.0, 90.0)


def test_layers_validation():
    with pytest.raises(ConfigError, match="distinct"):
        LayerMap(sheet="A", cut_outer="A")
    with pytest.raises(ConfigError, match="empty"):
        LayerMap(labels=" ")
    with pytest.raises(ConfigError, match="unknown layer key"):
        load_config(overrides={"sheet": "1x1", "layers": {"cutt": "X"}})


def test_direct_construction_is_validated():
    with pytest.raises(ConfigError):
        Config(sheet_width=-1, sheet_height=10)


# ------------------------------------------------------------------- units
def test_inches_from_cli_are_converted_to_mm():
    cfg = load_config(overrides={"units": "in", "sheet": "100x50", "margin": 0.5, "fit_spacing": 0.25, "tolerance": 0.001})
    assert (cfg.sheet_width, cfg.sheet_height) == pytest.approx((2540.0, 1270.0))
    assert cfg.margin == pytest.approx(12.7) and cfg.fit_spacing == pytest.approx(6.35)
    assert cfg.tolerance == pytest.approx(0.0254)
    assert cfg.units == INCH


def test_toml_units_describe_the_toml_values(tmp_path):
    p = write(tmp_path, 'units = "in"\nsheet = "100x50"\nmargin = 1\n')
    cfg = load_config(p)
    assert (cfg.sheet_width, cfg.margin) == pytest.approx((2540.0, 25.4)) and cfg.units == INCH


def test_cli_in_flag_only_changes_presentation_of_mm_toml(tmp_path):
    # TOML values are mm (its own default); --in must not reinterpret them
    p = write(tmp_path, 'sheet = "2500x1250"\nmargin = 5\n')
    cfg = load_config(p, {"units": "in"})
    assert (cfg.sheet_width, cfg.sheet_height, cfg.margin) == (2500.0, 1250.0, 5.0)
    assert cfg.units == INCH


def test_cli_values_use_cli_units_toml_values_use_toml_units(tmp_path):
    p = write(tmp_path, 'units = "in"\nsheet = "100x50"\nmargin = 1\n')
    cfg = load_config(p, {"units": "mm", "margin": 3})          # CLI says mm: margin 3 mm
    assert cfg.margin == 3.0 and cfg.units == MM
    assert cfg.sheet_width == pytest.approx(2540.0)              # TOML sheet still read as inches
    cfg = load_config(p, {"sheet_width": 2000})                  # CLI value without units flag: mm
    assert cfg.sheet_width == 2000.0 and cfg.sheet_height == pytest.approx(1270.0) and cfg.units == INCH


def test_units_aliases_and_errors():
    assert load_config(overrides={"sheet": "1x1", "units": "inches"}).units == INCH
    assert load_config(overrides={"sheet": "1x1", "units": "MM"}).units == MM
    with pytest.raises(ConfigError, match="unknown units"):
        load_config(overrides={"sheet": "1x1", "units": "cubits"})


# ------------------------------------------------------------------- grain
def test_grain_axis():
    assert load_config(overrides={"sheet": "1x1", "grain": "Y"}).grain == "y"
    with pytest.raises(ConfigError, match="grain must be"):
        load_config(overrides={"sheet": "1x1", "grain": "z"})


# ------------------------------------------------- fit_spacing / tool_diameter
def test_tool_diameter_defaults_to_not_checked():
    assert load_config(overrides={"sheet": "2500x1250"}).tool_diameter is None


def test_tool_diameter_value_and_units():
    assert load_config(overrides={"sheet": "1x1", "tool_diameter": 8}).tool_diameter == 8.0
    cfg = load_config(overrides={"units": "in", "sheet": "100x50", "tool_diameter": 0.25, "fit_spacing": 0.5})
    assert cfg.tool_diameter == pytest.approx(6.35) and cfg.fit_spacing == pytest.approx(12.7)


@pytest.mark.parametrize("bad", [0, -3])
def test_tool_diameter_must_be_positive(bad):
    with pytest.raises(ConfigError, match="tool_diameter must be > 0"):
        load_config(overrides={"sheet": "1x1", "tool_diameter": bad})


def test_default_fit_spacing_follows_a_big_tool_but_not_a_small_one():
    base = {"sheet": "2500x1250"}
    assert load_config(overrides=base).fit_spacing == 10.0
    assert load_config(overrides={**base, "tool_diameter": 6}).fit_spacing == 10.0      # default already wide enough
    assert load_config(overrides={**base, "tool_diameter": 12}).fit_spacing == 12.0     # would otherwise violate the rule


def test_explicit_fit_spacing_is_never_overridden_by_the_tool():
    cfg = load_config(overrides={"sheet": "2500x1250", "tool_diameter": 12, "fit_spacing": 5})
    assert cfg.fit_spacing == 5.0        # contradiction is reported by checks, not silently fixed


def test_tool_diameter_and_fit_spacing_from_toml(tmp_path):
    p = write(tmp_path, 'sheet = "2500x1250"\ntool_diameter = 8\n')
    cfg = load_config(p)
    assert cfg.tool_diameter == 8.0 and cfg.fit_spacing == 10.0
    assert load_config(p, {"fit_spacing": 9}).fit_spacing == 9.0


def test_old_spacing_key_is_rejected_not_ignored():
    with pytest.raises(ConfigError, match="unknown config key"):
        load_config(overrides={"sheet": "1x1", "spacing": 5})


# -------------------------------------------------------------- dxf_version
def test_dxf_version_default_and_normalisation():
    assert load_config(overrides={"sheet": "1x1"}).dxf_version == "R2000"
    assert load_config(overrides={"sheet": "1x1", "dxf_version": " r2010 "}).dxf_version == "R2010"


def test_dxf_version_validated():
    with pytest.raises(ConfigError, match="dxf_version must be one of"):
        load_config(overrides={"sheet": "1x1", "dxf_version": "R12"})


def test_layer_names_merge_across_sources(tmp_path):
    p = write(tmp_path, 'sheet = "1x1"\n[layers]\ncut_outer = "TNIJ"\nlabels = "OPISY"\n')
    cfg = load_config(p, {"layers": {"labels": "TEXT"}})
    assert (cfg.layers.cut_outer, cfg.layers.labels, cfg.layers.sheet, cfg.layers.cut_inner) == ("TNIJ", "TEXT", "SHEET", "CUT_INNER")
    assert load_config(p).layers.labels == "OPISY"
    with pytest.raises(ConfigError, match="layers must be a table"):
        load_config(overrides={"sheet": "1x1", "layers": "nope"})


# --------------------------------------------------- machining time parameters
def test_machining_parameters_default_to_off():
    c = load_config(overrides={"sheet": "1x1"})
    assert (c.feed_rate, c.thickness, c.pass_depth, c.rapid_rate) == (None, None, None, 10000.0)


def test_machining_parameters_are_converted_from_inches():
    c = load_config(overrides={"units": "in", "sheet": "100x50", "feed_rate": 236.22, "thickness": 0.7087,
                               "pass_depth": 0.25, "rapid_rate": 393.7})
    assert (c.feed_rate, c.thickness, c.pass_depth, c.rapid_rate) == pytest.approx((6000, 18.0, 6.35, 10000), rel=1e-3)


@pytest.mark.parametrize("key", ["feed_rate", "thickness", "pass_depth", "rapid_rate"])
def test_machining_parameters_must_be_positive(key):
    with pytest.raises(ConfigError, match=f"{key} must be > 0"):
        load_config(overrides={"sheet": "1x1", key: 0})
