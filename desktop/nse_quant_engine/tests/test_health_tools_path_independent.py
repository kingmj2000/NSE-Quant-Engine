"""The verification tools must give the same verdict wherever the folder lives.

Both verify_repo_health.py and tools_scan_undefined_names.py skipped any file
whose ABSOLUTE path contained a folder named data/ or output/. Measured on
2026-11-05 with the run folder unpacked under /data/...: py_files() returned
0 of 108 files, the scanner read nothing, and the tools printed "36 passed" and
"0 undefined name(s)". A scan over zero files is a check that could not be
performed, recorded as a check that passed.

The same health check also counted two checks that did not run as PASS: the
runtime-data check outside a git checkout, and the undefined-name check when the
scanner could not be imported. Those are now SKIP and FAIL respectively.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ENG = Path(__file__).resolve().parents[1]
if str(ENG) not in sys.path:
    sys.path.insert(0, str(ENG))

import tools_scan_undefined_names as tsu

PLANTED_UNDEFINED = "name_planted_and_never_defined"
PLANTED_PRIVATE = "_planted_private_never_defined"


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# -- tools_scan_undefined_names ----------------------------------------------

def test_scanner_reads_files_below_a_folder_named_data(tmp_path):
    eng = tmp_path / "data" / "engine"          # parent folder named data/
    _write(eng / "core" / "mod.py", f"def f():\n    return {PLANTED_UNDEFINED}\n")
    _write(eng / "data" / "runtime.py", "def g():\n    return also_undefined\n")

    found = tsu.scan(eng)

    assert any(PLANTED_UNDEFINED in f for f in found), \
        f"scan read nothing under a data/ parent: {found}"
    # SKIP still applies INSIDE the engine: its own data/ folder is not code.
    assert not any("also_undefined" in f for f in found)


def test_scanner_reports_an_unparseable_file(tmp_path):
    _write(tmp_path / "broken.py", "def f(:\n    pass\n")
    found = tsu.scan(tmp_path)
    assert any("broken.py" in f and "unparseable" in f for f in found), found


def test_scanner_cli_refuses_to_report_clean_on_zero_files(tmp_path):
    res = subprocess.run([sys.executable, str(ENG / "tools_scan_undefined_names.py"),
                          str(tmp_path)], capture_output=True, text=True, timeout=120)
    assert res.returncode != 0, res.stdout


# -- verify_repo_health -------------------------------------------------------

def _copy_engine(dst: Path) -> Path:
    def _ignore(d, names):
        top = Path(d) == ENG
        return [n for n in names
                if n in ("__pycache__", ".pytest_cache")
                or (top and (n in ("data", "output") or n.endswith(".zip")))]
    shutil.copytree(ENG, dst, ignore=_ignore)
    return dst


def _in_git_checkout(p: Path) -> bool:
    return any((c / ".git").exists() for c in [p, *p.parents])


def test_health_check_verdict_does_not_depend_on_the_parent_folder(tmp_path):
    eng = _copy_engine(tmp_path / "data" / "nse_quant_engine")
    # A defect only the AST scans can see...
    _write(eng / "core" / "zz_planted_defect.py",
           f"class Planted:\n    def run(self):\n        self.{PLANTED_PRIVATE}()\n")
    # ...and a check that cannot run.
    (eng / "tools_scan_undefined_names.py").unlink()

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    res = subprocess.run([sys.executable, "verify_repo_health.py"], cwd=eng, env=env,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=300)
    out = res.stdout
    rows = re.findall(r"^\s+(PASS|FAIL|SKIP|WARN)\s+(.+)$", out, flags=re.M)

    assert res.returncode == 1, out[-3000:]
    assert PLANTED_PRIVATE in out, "private-method scan read no files under data/"
    assert ("FAIL", "no function uses an undefined name") in rows, \
        "a scan that could not run was reported as a PASS"
    if not _in_git_checkout(eng):
        assert not any(s == "PASS" and "runtime data" in n for s, n in rows), \
            "runtime-data check counted as PASS outside a git checkout"
        m = re.search(r"(\d+) passed, (\d+) failed, (\d+) skipped", out)
        assert m and int(m.group(3)) >= 1, out[-1500:]
