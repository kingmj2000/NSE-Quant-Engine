"""UI ↔ producer filename contract + shadow-governance guardrails.

These tests are deliberately source-level: they prove the desktop UI reads the
filenames the pipeline actually writes, and that nothing can auto-promote the
shadow engine.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import output_paths as OP  # noqa: E402
from core import ui_readers  # noqa: E402

OBSOLETE = ("top5_sizing.csv", "top5_event_calendar.csv")
UI_SOURCES = ("run_app.py", "ui/candidates_workbench.py", "ui/decision_center.py")


@pytest.mark.parametrize("rel", UI_SOURCES)
def test_ui_has_no_obsolete_filenames(rel):
    text = (BASE / rel).read_text(encoding="utf-8")
    for bad in OBSOLETE:
        assert bad not in text, f"{rel} still references {bad}"


def test_producer_emits_the_names_the_ui_reads():
    tp = (BASE / "trade_plan_builder.py").read_text(encoding="utf-8")
    assert "OP.TOP5_POSITION_SIZING_CSV" in tp
    assert "OP.TOP5_EVENTS_CSV" in tp
    assert "OP.TOP5_SECTOR_CONTEXT_CSV" in tp
    assert "OP.TOP5_EXPECTED_VALUE_CSV" in tp


def test_portfolio_tab_consumes_shared_constants():
    text = (BASE / "run_app.py").read_text(encoding="utf-8")
    for const in ("TOP5_POSITION_SIZING_CSV", "TOP5_SECTOR_CONTEXT_CSV",
                  "TOP5_EVENTS_CSV", "TOP5_EXPECTED_VALUE_CSV",
                  "TOP5_INSTITUTIONAL_FLOW_CSV"):
        assert f"OP.{const}" in text


def test_workbench_and_decision_center_use_constants():
    for rel in ("ui/candidates_workbench.py", "ui/decision_center.py"):
        text = (BASE / rel).read_text(encoding="utf-8")
        assert "OP.TOP5_EVENTS_CSV" in text


def test_shared_constants_match_expected_filenames():
    assert OP.TOP5_POSITION_SIZING_CSV == "top5_position_sizing.csv"
    assert OP.TOP5_EVENTS_CSV == "top5_events.csv"


# ─── shadow governance ──────────────────────────────────────────────────────

def test_orchestrator_never_promotes_shadow_from_wording():
    text = (BASE / "orchestrator.py").read_text(encoding="utf-8")
    assert 'champion = "shadow"' not in text
    assert "OFFICIAL_ENGINE_VARIANT" in text


def test_config_default_engine_variant_is_official():
    from core import config as C
    assert C.OFFICIAL_ENGINE_VARIANT == "official"


def test_shadow_summary_champion_ignores_recommendation(tmp_path):
    out = tmp_path / "output"; out.mkdir()
    (out / "shadow_vs_official.json").write_text(json.dumps({
        "recommendation": "RECOMMEND: shadow leads on filtered EV/day — consider manual switch",
    }))
    s = ui_readers.read_shadow_summary(out)
    assert s["champion"] == "official"
    assert s["experimental_leader"] == "shadow"


def test_shadow_report_has_no_final_score_fallback_for_official():
    text = (BASE / "shadow_vs_official_report.py").read_text(encoding="utf-8")
    # official column is fixed; only the shadow helper may list alternatives
    assert 'score_col_off = "Confidence_Adjusted_Score"' in text
    assert "INSUFFICIENT_DATA" in text


def test_shadow_report_returns_insufficient_without_cas(tmp_path, monkeypatch):
    import shadow_vs_official_report as rep
    out = tmp_path / "output"; out.mkdir()
    pd.DataFrame({"Symbol": ["A"], "Final_Score": [10.0]}).to_csv(out / "off.csv", index=False)
    pd.DataFrame({"Symbol": ["A"], "Final_Score": [11.0]}).to_csv(out / "sha.csv", index=False)
    monkeypatch.setattr(rep, "OFFICIAL", out / "off.csv")
    monkeypatch.setattr(rep, "SHADOW", out / "sha.csv")
    monkeypatch.setattr(rep, "REPORT", out / "shadow_vs_official.md")
    res = rep.build()
    assert res["recommendation"] == "INSUFFICIENT_DATA"
    assert res["reason"] == "official_missing_Confidence_Adjusted_Score"


# ─── retired sentiment display ──────────────────────────────────────────────

def test_dashboard_renders_no_sentiment():
    text = (BASE / "dashboard_html_builder.py").read_text(encoding="utf-8")
    assert "top5_sent_df" not in text
    assert "c.sent" not in text
    assert not re.search(r'row\.get\("sent"\)', text)
