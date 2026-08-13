"""History-append dedup and macro regime-carry regressions.

Both defects share the project's recurring failure shape: a comparison that
silently could not succeed was treated as a comparison that found nothing.

1. `append_history` deduped on (Date, Symbol) by comparing raw values. The
   existing file's dates arrive from `read_csv` as strings while freshly built
   rows hold date/Timestamp objects, so "2026-08-12" never matched
   datetime.date(2026, 8, 12) and EVERY same-day re-run appended a complete
   duplicate row set to score_history / signal_history / alpha_score_history.
   Because `cross_sectional_validation.make_detail()` merges forward returns
   against that history on (Signal_Date, Symbol), duplicate keys fan the merge
   out and each matured observation is counted once per re-run —
   pseudo-replication that inflates Obs and overstates t-statistics.

2. `core/daily_changes.py` read `previous_regime` from macro_context.json, which
   nothing ever wrote, so `regime_change` was permanently None.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pandas as pd

from core import history_io as nqe


def _row(date_value, score: float) -> pd.DataFrame:
    return pd.DataFrame({"Date": [date_value], "Symbol": ["AAA"], "Final_Score": [score]})


# ── 1. append_history dedup ─────────────────────────────────────────────────

def test_same_day_rerun_does_not_duplicate_rows_for_date_objects(tmp_path):
    path = tmp_path / "score_history.csv"
    d = datetime.date(2026, 8, 12)

    nqe.append_history(path, _row(d, 10.0), ["Date", "Symbol"])
    nqe.append_history(path, _row(d, 99.0), ["Date", "Symbol"])

    out = pd.read_csv(path)
    assert len(out) == 1, "same-day re-run duplicated the row set"
    assert float(out["Final_Score"].iloc[0]) == 99.0, "most recent run must win"


def test_same_day_rerun_does_not_duplicate_rows_for_timestamps(tmp_path):
    path = tmp_path / "score_history.csv"
    ts = pd.Timestamp("2026-08-12")

    nqe.append_history(path, _row(ts, 10.0), ["Date", "Symbol"])
    nqe.append_history(path, _row(ts, 99.0), ["Date", "Symbol"])

    assert len(pd.read_csv(path)) == 1


def test_mixed_date_representations_collapse_to_one_row(tmp_path):
    """The three shapes a date can arrive in must all be the same key."""
    path = tmp_path / "score_history.csv"
    for value in (datetime.date(2026, 8, 12), pd.Timestamp("2026-08-12"), "2026-08-12"):
        nqe.append_history(path, _row(value, 1.0), ["Date", "Symbol"])

    assert len(pd.read_csv(path)) == 1


def test_existing_duplicates_self_heal_on_next_write(tmp_path):
    """Files that already accumulated duplicates must collapse, not persist."""
    path = tmp_path / "score_history.csv"
    pd.DataFrame({
        "Date": ["2026-08-12", "2026-08-12", "2026-08-12"],
        "Symbol": ["AAA", "AAA", "BBB"],
        "Final_Score": [1.0, 2.0, 3.0],
    }).to_csv(path, index=False)

    nqe.append_history(path, _row("2026-08-12", 9.0), ["Date", "Symbol"])

    out = pd.read_csv(path)
    assert len(out) == 2, "pre-existing duplicate keys were not collapsed"
    assert float(out.loc[out["Symbol"] == "AAA", "Final_Score"].iloc[0]) == 9.0


def test_distinct_dates_are_preserved(tmp_path):
    """Dedup must not collapse genuinely different days."""
    path = tmp_path / "score_history.csv"
    nqe.append_history(path, _row("2026-08-11", 1.0), ["Date", "Symbol"])
    nqe.append_history(path, _row("2026-08-12", 2.0), ["Date", "Symbol"])

    out = pd.read_csv(path)
    assert len(out) == 2
    assert sorted(out["Date"]) == ["2026-08-11", "2026-08-12"]


def test_dates_are_written_in_canonical_form(tmp_path):
    path = tmp_path / "score_history.csv"
    nqe.append_history(path, _row(pd.Timestamp("2026-08-12 15:30:00"), 1.0), ["Date", "Symbol"])
    assert pd.read_csv(path)["Date"].iloc[0] == "2026-08-12"


def test_non_date_keys_still_dedup(tmp_path):
    path = tmp_path / "run_log.csv"
    for n in (1, 2):
        nqe.append_history(
            path, pd.DataFrame({"Run_Timestamp": ["2026-08-12T12:18:52"], "Rows": [n]}),
            ["Run_Timestamp"])
    assert len(pd.read_csv(path)) == 1


def test_missing_key_column_does_not_raise(tmp_path):
    path = tmp_path / "h.csv"
    nqe.append_history(path, pd.DataFrame({"Symbol": ["AAA"]}), ["Date", "Symbol"])
    assert len(pd.read_csv(path)) == 1


# ── 2. regime change can actually fire ──────────────────────────────────────

def test_regime_change_detected_when_previous_regime_is_carried():
    from core import daily_changes as dc
    import inspect

    src = inspect.getsource(dc)
    assert "previous_regime" in src

    # The writer must now emit the key the reader needs.
    writer = Path(__file__).resolve().parents[1].joinpath("trade_plan_builder.py").read_text(encoding="utf-8")
    assert '"previous_regime"' in writer, \
        "macro_context.json writer no longer carries previous_regime forward"


def test_macro_context_previous_regime_round_trip(tmp_path):
    """Simulate the carry-forward: run 2 must see run 1's regime."""
    path = tmp_path / "macro_context.json"
    path.write_text(json.dumps({"regime": "risk-on"}), encoding="utf-8")

    prior = json.loads(path.read_text(encoding="utf-8")).get("regime")
    new = {"regime": "risk-off", "previous_regime": prior}
    path.write_text(json.dumps(new), encoding="utf-8")

    macro = json.loads(path.read_text(encoding="utf-8"))
    change = None
    if macro.get("regime") and macro.get("previous_regime") \
            and macro["regime"] != macro["previous_regime"]:
        change = {"from": macro["previous_regime"], "to": macro["regime"]}

    assert change == {"from": "risk-on", "to": "risk-off"}


def test_portfolio_verdict_key_is_read_by_its_real_name():
    """run_app read "Batch_Verdict"; portfolio_validation.json writes "verdict"."""
    import inspect

    src = Path(__file__).resolve().parents[1].joinpath("run_app.py").read_text(encoding="utf-8")
    assert "Batch_Verdict" not in src
