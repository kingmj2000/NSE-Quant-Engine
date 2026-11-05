"""run_manifest.json must carry the workspace snapshot that run_all() records.

run_all() stored summary["workspace"] (which folder, git clone or not, history
row counts) but write_manifest() dropped it, so after the 2026-10-15 clone
launch "which folder did this run in?" still had no durable answer. The
2026-11-05 fix added the key without a regression test; this is that test.
It exercises the real run_all() -> write_manifest() path in tmp_path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ENG = Path(__file__).resolve().parents[1]
if str(ENG) not in sys.path:
    sys.path.insert(0, str(ENG))

import orchestrator


def _run(tmp_path, monkeypatch) -> tuple[dict, dict, Path]:
    base = tmp_path / "run_folder"
    (base / "output").mkdir(parents=True)
    monkeypatch.setattr(orchestrator, "BASE", base)
    monkeypatch.setattr(orchestrator, "OUT", base / "output")
    monkeypatch.setattr(sys, "path", list(sys.path))   # write_manifest inserts BASE
    summary = orchestrator.run_all([], on_log=lambda *_a, **_k: None)
    manifest = json.loads((base / "output" / "run_manifest.json").read_text(encoding="utf-8"))
    return summary, manifest, base


def test_manifest_records_the_workspace_it_ran_in(tmp_path, monkeypatch):
    summary, manifest, base = _run(tmp_path, monkeypatch)
    ws = manifest.get("workspace")
    assert isinstance(ws, dict), "run_manifest.json dropped the workspace snapshot"
    assert Path(ws["base"]) == base.resolve()
    assert ws == summary["workspace"]


def test_manifest_flags_an_empty_workspace(tmp_path, monkeypatch):
    """The clone case: no history must be visible in the manifest itself."""
    _, manifest, _ = _run(tmp_path, monkeypatch)
    assert manifest["workspace"]["has_history"] is False
    assert manifest["workspace"]["score_history_rows"] == 0
