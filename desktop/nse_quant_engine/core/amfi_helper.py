"""Run the AMFI AUM/TER fetchers under an interpreter that has amfipy.

amfipy (the AMFI client both fetchers use) requires Python >= 3.11, while the
engine runs on Python 3.10. Run in-process, the fetchers only ever logged
amfipy IMPORT_ERROR, so AUM/TER were never refreshed after June 2026.

Resolution order:
  1. amfipy importable by the engine interpreter -> run in-process (old path).
  2. NSE_AMFI_PYTHON env var (path to python.exe, or a command like "py -3.13").
  3. Windows py launcher: py -3.13, py -3.12, py -3.11; elsewhere python3.13/12/11.
  A candidate is used only if it can import amfipy, pandas, numpy, openpyxl
  and requests. Otherwise the fetcher runs in-process as before (it then falls
  back to cached / existing files) and a one-line install hint is printed.
"""
from __future__ import annotations

import importlib.util
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

PROBE_CODE = "import amfipy, pandas, numpy, openpyxl, requests"
INSTALL_HINT = (
    "[amfi] amfipy needs Python >= 3.11 and is not importable here. To refresh AUM/TER: "
    "py -3.13 -m pip install -r requirements_optional_etf_metadata.txt "
    "(or set NSE_AMFI_PYTHON to a Python >= 3.11 that has it). Using cached/existing files."
)

_cache: dict[str, Optional[list[str]]] = {}


def amfipy_in_process() -> bool:
    try:
        return importlib.util.find_spec("amfipy") is not None
    except Exception:
        return False


def candidate_commands(env: Optional[dict] = None, platform: Optional[str] = None) -> list[list[str]]:
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    out: list[list[str]] = []
    custom = str(env.get("NSE_AMFI_PYTHON", "") or "").strip()
    if custom:
        if os.path.exists(custom.strip('"')):
            out.append([custom.strip('"')])
        else:
            out.append(shlex.split(custom, posix=not platform.startswith("win")))
    if platform.startswith("win"):
        out += [["py", "-3.13"], ["py", "-3.12"], ["py", "-3.11"]]
    else:
        out += [["python3.13"], ["python3.12"], ["python3.11"]]
    return out


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def probe(cmd: Sequence[str], runner: Callable = subprocess.run) -> bool:
    try:
        res = runner(list(cmd) + ["-c", PROBE_CODE], capture_output=True, text=True,
                     timeout=90, creationflags=_creationflags())
        return getattr(res, "returncode", 1) == 0
    except Exception:
        return False


def find_helper_python(env: Optional[dict] = None, platform: Optional[str] = None,
                       runner: Callable = subprocess.run, use_cache: bool = True) -> Optional[list[str]]:
    if use_cache and "helper" in _cache:
        return _cache["helper"]
    found = None
    for cmd in candidate_commands(env, platform):
        if probe(cmd, runner):
            found = list(cmd)
            break
    if use_cache:
        _cache["helper"] = found
    return found


def run_with_helper(cmd: Sequence[str], script_path: Path, cwd: Path, on_line: Callable[[str], None] = print,
                    popen: Callable = subprocess.Popen) -> int:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    proc = popen(list(cmd) + [str(script_path)], cwd=str(cwd), env=env,
                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                 encoding="utf-8", errors="replace", creationflags=_creationflags())
    assert proc.stdout is not None
    for line in proc.stdout:
        on_line(line.rstrip("\r\n"))
    return proc.wait()


def make_amfi_runner(script: str, base: Path, in_process_runner: Callable[[], None],
                     on_line: Callable[[str], None] = print,
                     finder: Callable[[], Optional[list[str]]] = find_helper_python,
                     in_process_check: Callable[[], bool] = amfipy_in_process,
                     helper_runner: Callable = run_with_helper) -> Callable[[], None]:
    def _run():
        if in_process_check():
            in_process_runner()
            return
        cmd = finder()
        if not cmd:
            on_line(INSTALL_HINT)
            in_process_runner()
            return
        on_line(f"[amfi] running {script} with helper interpreter: {' '.join(cmd)}")
        rc = helper_runner(cmd, base / script, base, on_line)
        if rc not in (0, None):
            raise RuntimeError(f"{script} (helper {' '.join(cmd)}) exited with code {rc}")
    return _run
