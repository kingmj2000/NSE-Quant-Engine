"""AMFI fetchers run under a Python >= 3.11 helper when the engine can't import amfipy.

amfipy 0.2.0 / 0.3.0 declare requires_python >= 3.11 on PyPI; the engine runs on
Python 3.10. The orchestrator ran both fetchers in-process, so every run since
June 2026 logged amfipy IMPORT_ERROR and AUM/TER were never refreshed
(etf_ter_tracking_auto_status.json: last_attempt_status no_source_rows).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import amfi_helper as ah  # noqa: E402


def test_candidates_windows_and_override():
    assert ah.candidate_commands(env={}, platform="win32") == [["py", "-3.13"], ["py", "-3.12"], ["py", "-3.11"]]
    cmds = ah.candidate_commands(env={"NSE_AMFI_PYTHON": "py -3.12"}, platform="win32")
    assert cmds[0] == ["py", "-3.12"]
    assert ah.candidate_commands(env={}, platform="linux")[0] == ["python3.13"]


def test_find_helper_uses_first_interpreter_that_imports_amfipy():
    seen = []

    def runner(cmd, **kw):
        seen.append(cmd)
        assert cmd[-2:] == ["-c", ah.PROBE_CODE]
        return SimpleNamespace(returncode=0 if cmd[:2] == ["py", "-3.12"] else 1)

    found = ah.find_helper_python(env={}, platform="win32", runner=runner, use_cache=False)
    assert found == ["py", "-3.12"]
    assert seen[0][:2] == ["py", "-3.13"]

    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    assert ah.find_helper_python(env={}, platform="win32", runner=missing, use_cache=False) is None


def _runner(**kw):
    calls = {"in_process": 0, "helper": []}

    def in_proc():
        calls["in_process"] += 1

    def helper(cmd, script_path, cwd, on_line):
        calls["helper"].append((cmd, script_path, cwd))
        return kw.get("rc", 0)
    lines = []
    run = ah.make_amfi_runner("etf_aum_auto_fetcher.py", ROOT, in_proc, on_line=lines.append,
                              finder=lambda: kw.get("found"), in_process_check=lambda: kw.get("inproc", False),
                              helper_runner=helper)
    return run, calls, lines


def test_runner_prefers_in_process_when_amfipy_importable():
    run, calls, _ = _runner(inproc=True, found=["py", "-3.13"])
    run()
    assert calls["in_process"] == 1 and calls["helper"] == []


def test_runner_uses_helper_when_engine_lacks_amfipy():
    run, calls, lines = _runner(found=["py", "-3.13"])
    run()
    assert calls["in_process"] == 0
    cmd, script, cwd = calls["helper"][0]
    assert cmd == ["py", "-3.13"] and script == ROOT / "etf_aum_auto_fetcher.py" and cwd == ROOT
    assert any("helper interpreter" in ln for ln in lines)


def test_runner_helper_failure_raises_for_skippable_step():
    run, _, _ = _runner(found=["py", "-3.13"], rc=2)
    with pytest.raises(RuntimeError):
        run()


def test_runner_without_helper_prints_hint_and_runs_in_process():
    run, calls, lines = _runner(found=None)
    run()
    assert calls["in_process"] == 1
    assert any("Python >= 3.11" in ln for ln in lines)


def test_run_with_helper_streams_output(tmp_path):
    script = tmp_path / "s.py"
    script.write_text("import os\nprint('hello', os.environ.get('PYTHONIOENCODING'))\nprint('Rs \u20b9 ok')\n", encoding="utf-8")
    lines = []
    rc = ah.run_with_helper([sys.executable], script, tmp_path, lines.append)
    assert rc == 0
    assert lines[0] == "hello utf-8" and lines[1] == "Rs \u20b9 ok"


def test_orchestrator_wires_amfi_fetchers_through_helper():
    src = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
    assert '_amfi_runpy("etf_aum_auto_fetcher.py")' in src
    assert '_amfi_runpy("etf_ter_tracking_auto_fetcher.py")' in src
    req = (ROOT / "requirements_optional_etf_metadata.txt").read_text(encoding="utf-8")
    assert "Python >= 3.11" in req
