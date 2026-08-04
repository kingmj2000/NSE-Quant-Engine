"""Closeout alignment pass — evidence bundle, sentiment retirement, UI, labels."""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import evidence_bundle as eb  # noqa: E402


def _write_plan(out: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(out / "trade_plan_latest.csv", index=False)


# ── 1. Evidence bundle ranking authority ────────────────────────────────────

def test_evidence_top5_uses_cas_and_symbol_never_final_score(tmp_path):
    _write_plan(tmp_path, [
        {"Symbol": "ZZZ", "Trade_Status": "Watch", "Confidence_Adjusted_Score": 50.0,
         "Final_Score": 99.0},
        {"Symbol": "AAA", "Trade_Status": "Watch", "Confidence_Adjusted_Score": 50.0,
         "Final_Score": 10.0},
    ])
    top5 = eb._read_top5(tmp_path)
    assert list(top5["Symbol"]) == ["AAA", "ZZZ"]

    # reversed input order must not change the outcome
    _write_plan(tmp_path, [
        {"Symbol": "AAA", "Trade_Status": "Watch", "Confidence_Adjusted_Score": 50.0,
         "Final_Score": 10.0},
        {"Symbol": "ZZZ", "Trade_Status": "Watch", "Confidence_Adjusted_Score": 50.0,
         "Final_Score": 99.0},
    ])
    assert list(eb._read_top5(tmp_path)["Symbol"]) == ["AAA", "ZZZ"]


def test_evidence_top5_prefers_cas_over_final_score(tmp_path):
    _write_plan(tmp_path, [
        {"Symbol": "AAA", "Trade_Status": "Watch", "Confidence_Adjusted_Score": 70.0,
         "Final_Score": 99.0},
        {"Symbol": "BBB", "Trade_Status": "Watch", "Confidence_Adjusted_Score": 90.0,
         "Final_Score": 60.0},
    ])
    assert list(eb._read_top5(tmp_path)["Symbol"])[0] == "BBB"


def test_evidence_top5_still_filters_avoid(tmp_path):
    _write_plan(tmp_path, [
        {"Symbol": "AAA", "Trade_Status": "Avoid", "Confidence_Adjusted_Score": 99.0,
         "Final_Score": 99.0},
        {"Symbol": "BBB", "Trade_Status": "Watch", "Confidence_Adjusted_Score": 10.0,
         "Final_Score": 10.0},
    ])
    assert list(eb._read_top5(tmp_path)["Symbol"]) == ["BBB"]


# ── 2. Bundle contents ──────────────────────────────────────────────────────

def _minimal_bundle(tmp_path: Path, with_news: bool) -> Path:
    out = tmp_path / "output"; out.mkdir()
    prompts = tmp_path / "prompts"; prompts.mkdir()
    (prompts / "rationale_prompt.md").write_text("spec", encoding="utf-8")
    _write_plan(out, [{"Symbol": "AAA", "Trade_Status": "Watch",
                       "Confidence_Adjusted_Score": 50.0, "Final_Score": 1.0}])
    (out / "validation_status.json").write_text(json.dumps({"verdict": "x"}), encoding="utf-8")
    (out / "daily_changes.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    if with_news:
        (out / "news_digest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
        (out / "news_market_context.md").write_text("# news", encoding="utf-8")
    zpath = eb.build_bundle(out, prompts)
    assert zpath is not None and zpath.exists()
    assert zpath.name.startswith("insight_bundle_") and zpath.suffix == ".zip"
    return zpath


def test_bundle_includes_news_digest_when_available(tmp_path):
    zpath = _minimal_bundle(tmp_path, with_news=True)
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("run_manifest.json"))
    assert "news_digest.json" in names
    assert "news_market_context.md" in names
    assert "validation_status.json" in names
    assert "daily_changes.json" in names
    assert "news_digest.json" not in manifest["missing_files"]


def test_bundle_fails_softly_and_lists_missing_news(tmp_path):
    zpath = _minimal_bundle(tmp_path, with_news=False)
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("run_manifest.json"))
    assert "news_digest.json" not in names
    assert "news_digest.json" in manifest["missing_files"]


def test_obsolete_news_market_latest_not_required():
    assert "news_market_latest.csv" not in eb._CANDIDATE_FILES
    assert "top5_sentiment.csv" not in eb._CANDIDATE_FILES


# ── 3. Bundle runs after the news step ──────────────────────────────────────

def test_bundle_step_runs_after_news_step():
    import orchestrator
    names = [s.name for s in orchestrator.build_steps(include_shadow=True,
                                                      include_fetch=False)]
    assert "evidence_bundle" in names
    assert names.index("evidence_bundle") > names.index("news_market_builder")


def test_trade_plan_builder_does_not_build_the_bundle():
    src = (ROOT / "trade_plan_builder.py").read_text(encoding="utf-8")
    assert "build_bundle(" not in src


# ── 4. Sentiment retirement / macro preservation ────────────────────────────

def test_default_workflow_has_no_numerical_sentiment_veto():
    from core import config as C
    assert C.SENTIMENT_OVERLAY_ON is False
    assert C.SENTIMENT_VETO_ON is False
    src = (ROOT / "trade_plan_builder.py").read_text(encoding="utf-8")
    assert "sentiment_veto" not in src
    assert "score_headlines" not in src
    assert "top5_sentiment.csv" not in src


def test_macro_context_remains_generated():
    from core import config as C
    assert C.MACRO_CONTEXT_ON is True
    src = (ROOT / "trade_plan_builder.py").read_text(encoding="utf-8")
    assert "MACRO_CONTEXT_ON" in src
    assert "macro_tape_score" in src


def test_prompt_declares_validation_authority_and_drops_sentiment():
    spec = (ROOT / "prompts" / "rationale_prompt.md").read_text(encoding="utf-8")
    assert "validation_status.json" in spec
    assert "WATCHLIST ONLY" in spec
    assert "Confidence_Adjusted_Score" in spec
    assert "news_digest.json" in spec
    assert "top5_sentiment.csv" not in spec


# ── 5. UI zip action ────────────────────────────────────────────────────────

def test_ui_searches_only_for_insight_bundle_zip():
    src = (ROOT / "run_app.py").read_text(encoding="utf-8")
    assert 'insight_bundle_*.zip' in src
    assert 'evidence_bundle_*.zip' not in src
    assert 'OUT.glob("*.zip")' not in src


# ── 6. Raw-score bucket labelling ───────────────────────────────────────────

def test_bucket_labels_say_diagnostic():
    wb = (ROOT / "ui" / "candidates_workbench.py").read_text(encoding="utf-8")
    assert "Raw Score Bucket" in wb
    app = (ROOT / "run_app.py").read_text(encoding="utf-8")
    assert "Raw Score Bucket — diagnostic" in app
    eng = (ROOT / "nse_quant_engine.py").read_text(encoding="utf-8")
    assert 'sheet_name="Top Low Risk"' not in eng
    assert 'sheet_name="Raw Score Low-Risk Diag"' in eng


# ── 7. Windows setup / workflow scripts ─────────────────────────────────────

def test_setup_windows_uses_venv_and_requirements():
    bat = (ROOT / "setup_windows.bat").read_text(encoding="utf-8")
    assert "-m venv .venv" in bat
    assert ".venv\\Scripts\\python.exe -m pip install -r requirements.txt" in bat
    assert ".venv\\Scripts\\python.exe -m pip install --upgrade pip" in bat
    assert "PySide6-WebEngine" not in bat
    assert "(3,11)" in bat and "(3,12)" in bat


def test_run_full_workflow_delegates_to_orchestrator():
    bat = (ROOT / "run_full_workflow.bat").read_text(encoding="utf-8")
    assert "orchestrator.py --all" in bat
    assert "universe_builder.py" not in bat
    assert "--skip-fetch" in bat


def test_quickstart_filenames_exist():
    qs = (ROOT / "QUICKSTART_WINDOWS.md").read_text(encoding="utf-8")
    assert "latest_scores.xlsx" in qs
    assert "latest_scores_v4_shadow.xlsx" in qs
    assert "nse_quant_scores.xlsx" not in qs
    for name in ["run_app.py", "run_app.bat", "orchestrator.py",
                 "setup_windows.bat", "run_full_workflow.bat", "requirements.txt"]:
        assert name in qs
        assert (ROOT / name).exists(), name
