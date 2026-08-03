"""Validated-workbook sheet semantics.

Top Opportunities must be ordered by the authoritative ranking column
(Confidence_Adjusted_Score desc, Symbol asc) even when another symbol has the
highest Final_Score. Raw Score Diagnostic is the only Final_Score-ordered sheet
and must be explicitly labelled non-authoritative.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cross_sectional_validation as csv_mod  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")


def _latest() -> pd.DataFrame:
    # AAA highest Final_Score, BBB highest Confidence_Adjusted_Score.
    return pd.DataFrame([
        {"Symbol": "CCC", "Opportunity_Eligible": "Yes",
         "Confidence_Adjusted_Score": 55.0, "Final_Score": 80.0},
        {"Symbol": "AAA", "Opportunity_Eligible": "Yes",
         "Confidence_Adjusted_Score": 70.0, "Final_Score": 99.0},
        {"Symbol": "BBB", "Opportunity_Eligible": "Yes",
         "Confidence_Adjusted_Score": 90.0, "Final_Score": 60.0},
    ])


def _build(tmp_path, monkeypatch) -> Path:
    latest_csv = tmp_path / "latest_scores.csv"
    _latest().to_csv(latest_csv, index=False)
    xlsx = tmp_path / "validated.xlsx"
    monkeypatch.setattr(csv_mod, "LATEST_SCORES_CSV", latest_csv)
    monkeypatch.setattr(csv_mod, "VALIDATED_XLSX", xlsx)
    empty = pd.DataFrame()
    csv_mod.write_validated_workbook(empty, empty, empty, empty,
                                     "No Proven Edge Yet", "Insufficient Evidence", empty)
    return xlsx


def _sheet_rows(xlsx: Path, sheet: str) -> list[list]:
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    try:
        return [list(r) for r in wb[sheet].iter_rows(values_only=True)]
    finally:
        wb.close()


def test_top_opportunities_is_cas_ordered(tmp_path, monkeypatch):
    xlsx = _build(tmp_path, monkeypatch)
    rows = _sheet_rows(xlsx, "Top Opportunities")
    header, data = rows[0], rows[1:]
    sym_i = header.index("Symbol")
    symbols = [r[sym_i] for r in data]
    assert symbols == ["BBB", "AAA", "CCC"], symbols


def test_raw_score_sheet_is_labelled_non_authoritative(tmp_path, monkeypatch):
    xlsx = _build(tmp_path, monkeypatch)
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    names = wb.sheetnames
    wb.close()
    assert "Raw Score Diagnostic" in names
    assert "Top Confidence Adj" not in names  # no duplicate authoritative sheet

    rows = _sheet_rows(xlsx, "Raw Score Diagnostic")
    header, data = rows[0], rows[1:]
    assert header[0] == "Authority_Note"
    assert "DIAGNOSTIC ONLY" in str(data[0][0])
    sym_i = header.index("Symbol")
    assert [r[sym_i] for r in data] == ["AAA", "CCC", "BBB"]
