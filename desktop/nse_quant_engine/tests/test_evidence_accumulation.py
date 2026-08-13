"""Evidence-accumulation and maturation-counting regressions.

Two defects, both of which made the UI counters lie about the evidence base:

1. `validation_builder` REBUILT forward_return_history.csv from scratch each run
   against the CURRENT raw price file. When a symbol left the universe its
   previously matured forward returns could no longer be recomputed and silently
   vanished — the matured count fell (10k -> 7k) and the reason surfaced as
   "Symbol not found in current raw price file". Retaining only survivors is
   survivorship bias, which biases measured edge upward.

2. Pending signals live in forward_return_missing_signals.csv, not as NaN rows in
   forward_return_history.csv. Counting NaNs on the history file therefore always
   produced "Awaiting maturation: 0" and a 100% maturation rate, no matter how
   many signals were actually waiting.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import validation_builder as vb
from core import ui_readers


def _fwd_rows(rows: list[tuple[str, str, int, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"Signal_Date": d, "Symbol": s, "Horizon_Days": h, "Net_Forward_Return": r}
         for d, s, h, r in rows]
    )


# ── 1. Accumulation / survivorship ──────────────────────────────────────────

def test_forward_history_retains_symbols_that_left_the_universe(tmp_path):
    path = tmp_path / "forward_return_history.csv"
    prior = _fwd_rows([
        ("2026-07-01", "BOSCHLTD.NS", 10, 0.021),   # will leave the universe
        ("2026-07-01", "RELIANCE.NS", 10, 0.011),
    ])
    vb.write_csv_with_headers(prior.copy(), path, vb.FORWARD_COLUMNS)

    # This run can only recompute RELIANCE — BOSCHLTD has no price rows anymore.
    this_run = _fwd_rows([
        ("2026-07-01", "RELIANCE.NS", 10, 0.011),
        ("2026-07-02", "RELIANCE.NS", 10, 0.004),
    ])

    merged, stats = vb.merge_forward_history(this_run, path, vb.FORWARD_COLUMNS)
    syms = set(merged["Symbol"])

    assert "BOSCHLTD.NS" in syms, "matured evidence for a delisted/dropped name was lost"
    assert stats["retained_from_history"] == 1
    assert stats["computed_this_run"] == 2
    assert stats["total"] == 3


def test_recomputed_rows_win_on_conflict(tmp_path):
    path = tmp_path / "forward_return_history.csv"
    vb.write_csv_with_headers(
        _fwd_rows([("2026-07-01", "RELIANCE.NS", 10, 0.011)]), path, vb.FORWARD_COLUMNS)

    merged, _ = vb.merge_forward_history(
        _fwd_rows([("2026-07-01", "RELIANCE.NS", 10, 0.099)]), path, vb.FORWARD_COLUMNS)

    assert len(merged) == 1
    assert float(merged["Net_Forward_Return"].iloc[0]) == 0.099


def test_merge_is_idempotent_and_never_duplicates(tmp_path):
    path = tmp_path / "forward_return_history.csv"
    rows = _fwd_rows([("2026-07-01", "AAA.NS", 10, 0.01),
                      ("2026-07-01", "BBB.NS", 10, 0.02)])
    vb.write_csv_with_headers(rows.copy(), path, vb.FORWARD_COLUMNS)

    merged, stats = vb.merge_forward_history(rows.copy(), path, vb.FORWARD_COLUMNS)
    assert len(merged) == 2
    assert stats["retained_from_history"] == 0
    assert not merged.duplicated(subset=vb.FORWARD_KEY).any()


def test_merge_survives_date_format_round_trip(tmp_path):
    """A CSV round-trip must not create phantom duplicate rows."""
    path = tmp_path / "forward_return_history.csv"
    vb.write_csv_with_headers(
        _fwd_rows([("2026-07-01 00:00:00", "AAA.NS", 10, 0.01)]), path, vb.FORWARD_COLUMNS)

    merged, _ = vb.merge_forward_history(
        _fwd_rows([("2026-07-01", "AAA.NS", 10, 0.01)]), path, vb.FORWARD_COLUMNS)

    assert len(merged) == 1


def test_unreadable_prior_history_does_not_silently_delete_evidence(tmp_path):
    path = tmp_path / "forward_return_history.csv"
    path.write_bytes(b"\x00\x01 not a csv \xff")
    new = _fwd_rows([("2026-07-02", "AAA.NS", 10, 0.01)])

    merged, stats = vb.merge_forward_history(new, path, vb.FORWARD_COLUMNS)

    assert len(merged) == 1          # proceeds with what it has
    assert stats["retained_from_history"] == 0


def test_empty_run_keeps_full_prior_history(tmp_path):
    path = tmp_path / "forward_return_history.csv"
    vb.write_csv_with_headers(
        _fwd_rows([("2026-07-01", "AAA.NS", 10, 0.01)]), path, vb.FORWARD_COLUMNS)

    merged, stats = vb.merge_forward_history(pd.DataFrame(), path, vb.FORWARD_COLUMNS)

    assert len(merged) == 1
    assert stats["retained_from_history"] == 1


# ── 2. Maturation counting ──────────────────────────────────────────────────

def test_pending_count_comes_from_missing_signals_not_nan_rows(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    # History holds ONLY matured rows — this is the file's contract.
    _fwd_rows([("2026-07-01", "AAA.NS", 10, 0.01),
               ("2026-07-01", "BBB.NS", 10, 0.02)]).to_csv(
        out / "forward_return_history.csv", index=False)
    pd.DataFrame([
        {"Signal_Date": "2026-08-11", "Symbol": "CCC.NS", "Horizon_Days": 10,
         "Reason": "Forward horizon not matured yet"},
        {"Signal_Date": "2026-08-11", "Symbol": "DDD.NS", "Horizon_Days": 10,
         "Reason": "Forward horizon not matured yet"},
        {"Signal_Date": "2026-06-01", "Symbol": "OLD.NS", "Horizon_Days": 10,
         "Reason": "Symbol not found in current raw price file"},
    ]).to_csv(out / "forward_return_missing_signals.csv", index=False)

    mp = ui_readers.read_maturation_progress(out, horizon=10)

    assert mp["matured"] == 2
    assert mp["pending"] == 2, "pending must not be read as NaN rows of the history file"
    assert mp["unmatchable"] == 1
    assert mp["total"] == 5
    assert mp["rate_pct"] == 40.0


def test_maturation_rate_is_not_always_100_percent(tmp_path):
    """The old computation could only ever return 100%."""
    out = tmp_path / "output"
    out.mkdir()
    _fwd_rows([("2026-07-01", "AAA.NS", 10, 0.01)]).to_csv(
        out / "forward_return_history.csv", index=False)
    pd.DataFrame([{"Signal_Date": "2026-08-11", "Symbol": "B.NS",
                   "Horizon_Days": 10, "Reason": "Forward horizon not matured yet"}
                  for _ in range(9)]).to_csv(
        out / "forward_return_missing_signals.csv", index=False)

    mp = ui_readers.read_maturation_progress(out, horizon=10)
    assert mp["rate_pct"] == 10.0


def test_maturation_progress_handles_missing_files(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    mp = ui_readers.read_maturation_progress(out, horizon=10)
    assert mp == {"horizon": 10, "matured": 0, "pending": 0, "unmatchable": 0,
                  "total": 0, "rate_pct": None, "reasons": {}}


# ── 3. Shadow-vs-official KPI keys ──────────────────────────────────────────

def test_shadow_report_writes_the_keys_the_ui_reads():
    """The UI read `jaccard_at_20` / `avg_abs_delta_rank`; only the latter was
    ever absent from the writer. Both sides must now agree by name."""
    import inspect

    import run_app
    import shadow_vs_official_report as rep

    writer = inspect.getsource(rep.build)
    reader = inspect.getsource(run_app)
    for key in ("jaccard_top25", "overlap_top_n", "spearman_full", "avg_abs_delta_rank"):
        assert f'"{key}"' in writer, f"writer no longer emits {key}"
        assert f'"{key}"' in reader, f"UI no longer reads {key}"
    # The dead key names must not come back.
    assert "jaccard_at_20" not in reader
    assert "mean_abs_delta_rank" not in reader
