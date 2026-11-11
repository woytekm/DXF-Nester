"""dxfnest.py: the file you actually run."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import dxfnest as launcher                       # the script itself, imported as a module
from nestlib import __version__

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "dxfnest.py"
DATA = Path(__file__).parent / "data"
CSV = "filename;qty;grain\npolka_typ_3.dxf;2;along\nszuflada_front.dxf;4;across\nszuflada_bok.dxf;8;\n"


def run_script(*args, cwd, script=SCRIPT, env=None):
    return subprocess.run([sys.executable, str(script), *map(str, args)], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


@pytest.fixture
def job(tmp_path):
    for f in DATA.glob("*.dxf"):
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / "parts.csv").write_text(CSV, encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------- it just runs
def test_runs_from_any_working_directory(job, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    r = run_script(job / "parts.csv", "--sheet", "2500x1250", "-o", job / "out", "--no-png", cwd=elsewhere)
    assert r.returncode == 0, r.stderr
    assert "Nested 14 pieces of 3 parts" in r.stdout and (job / "out" / "sheet_01.dxf").exists()


def test_runs_from_its_own_directory(job):
    r = subprocess.run([sys.executable, "dxfnest.py", str(job / "parts.csv"), "--sheet", "2500x1250", "-o",
                        str(job / "o2"), "--no-png"], cwd=ROOT, capture_output=True, text=True)    # cwd = the code directory
    assert r.returncode == 0 and "Nested 14 pieces" in r.stdout


def test_works_through_a_symlink(job, tmp_path_factory):
    bin_dir = tmp_path_factory.mktemp("bin")
    link = bin_dir / "dxfnest"
    link.symlink_to(SCRIPT)
    r = run_script(job / "parts.csv", "--sheet", "2500x1250", "-o", job / "out", "--no-png", cwd=bin_dir, script=link)
    assert r.returncode == 0, r.stderr


def test_png_output_from_the_launcher(job):
    r = run_script(job / "parts.csv", "--sheet", "2500x1250", "-o", job / "out", "--png-width", "600", cwd=job)
    assert r.returncode == 0 and (job / "out" / "sheet_01.png").exists()


def test_version_help_and_module_entry(job):
    assert run_script("--version", cwd=job).stdout.strip() == f"dxfnest {__version__}"
    assert "usage: dxfnest.py" in run_script("--help", cwd=job).stdout
    m = subprocess.run([sys.executable, "-m", "nestlib", "--version"], cwd=ROOT, capture_output=True, text=True)
    assert m.stdout.strip() == f"dxfnest {__version__}"


def test_exit_code_is_passed_through(job):
    r = run_script(job / "parts.csv", "--sheet", "2500x1250", "--margin", "10", "-o", job / "out", cwd=job)
    assert r.returncode == 1 and "nothing was written" in r.stderr
    assert run_script(job / "parts.csv", cwd=job).returncode == 2


def test_script_has_a_shebang_and_is_executable():
    assert SCRIPT.read_text().startswith("#!/usr/bin/env python3")
    assert os.access(SCRIPT, os.X_OK)


# ------------------------------------------------------------ friendly failures
def test_preflight_ok_here():
    assert launcher.preflight() == []


def test_preflight_old_python():
    p = launcher.preflight(version_info=(3, 9, 6))
    assert len(p) == 1 and "Python 3.11 or newer is required (this is 3.9)" in p[0]


def test_preflight_names_every_missing_module_and_the_pip_command():
    def fake_import(name):
        raise ImportError(f"No module named '{name}'")
    p = launcher.preflight(import_module=fake_import)
    assert any("cannot import 'ezdxf'" in x for x in p) and any("cannot import 'shapely'" in x for x in p)
    assert any("python3 -m pip install ezdxf shapely" in x for x in p)


def test_preflight_reports_only_what_is_missing():
    def fake_import(name):
        if name == "shapely":
            raise ImportError("boom")
    p = launcher.preflight(import_module=fake_import)
    assert len(p) == 2 and "cannot import 'shapely' (boom)" in p[0] and p[1].endswith("pip install shapely")


def test_launcher_prints_a_readable_message_instead_of_a_traceback(tmp_path):
    code = ("import sys, runpy; sys.modules['shapely'] = None; sys.argv = ['dxfnest.py', '--version']; "
            f"runpy.run_path({str(SCRIPT)!r}, run_name='__main__')")
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 2 and "Traceback" not in r.stderr
    assert "dxfnest: cannot start:" in r.stderr and "shapely" in r.stderr and "pip install" in r.stderr
    assert "requirements.txt" in r.stderr


def test_requirements_file_lists_what_the_code_imports():
    import ast
    std = set(sys.stdlib_module_names)
    used = set()
    for f in (ROOT / "nestlib").glob("*.py"):
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                used.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                used.update(a.name.split(".")[0] for a in n.names)
    third_party = {m for m in used if m not in std and m != "nestlib"}
    text = (ROOT / "requirements.txt").read_text().lower()
    names = {"PIL": "pillow"}
    assert third_party == {"ezdxf", "shapely", "PIL", "numpy", "dearpygui"}   # numpy: the GUI converts images with it
    for m in third_party:
        assert names.get(m, m).lower() in text, f"{m} missing from requirements.txt"
