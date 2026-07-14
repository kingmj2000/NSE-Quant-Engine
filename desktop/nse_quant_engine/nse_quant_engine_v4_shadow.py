"""
NSE Quant Engine - Stage 4.0 Shadow Scoring Runner
==================================================

Purpose
-------
Runs the reviewed Core v4.1 scoring logic in SHADOW MODE.

It does NOT overwrite your official engine outputs. It reads the normal output
from run_full_workflow.bat and creates a separate comparison workbook:

    output/latest_scores_v4_shadow.xlsx
    output/latest_scores_v4_shadow.csv

Run order
---------
1) run_full_workflow.bat
2) python nse_quant_engine_v4_shadow.py

Why separate?
-------------
The current engine is your official, battle-tested workflow. Shadow mode lets
Core v4.1 compete beside it for a few weeks before you trust it with the throne.
Because apparently even spreadsheets need probation now.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
import json
import sys
import traceback
import warnings

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR = BASE_DIR / "data"
INPUT_CSV = OUTPUT_DIR / "latest_scores.csv"
INPUT_XLSX = OUTPUT_DIR / "latest_scores.xlsx"
OUT_CSV = OUTPUT_DIR / "latest_scores_v4_shadow.csv"
OUT_XLSX = OUTPUT_DIR / "latest_scores_v4_shadow.xlsx"
SUMMARY_JSON = OUTPUT_DIR / "shadow_mode_summary.json"


def setup_core_import() -> None:
    """Allow both correct and common accidental extraction layouts."""
    candidates = [
        BASE_DIR,
        BASE_DIR / "core_v4_1_reviewed",
        BASE_DIR / "nse_quant_engine_core_v4_1_reviewed_guardrails" / "core_v4_1_reviewed",
    ]
    for c in candidates:
        if (c / "core" / "scoring.py").exists():
            sys.path.insert(0, str(c))
            return
    raise FileNotFoundError(
        "Could not find core/scoring.py. Copy the 'core' folder from the v4.1 zip "
        "into the project root, or keep it under core_v4_1_reviewed/core."
    )


def read_latest_scores() -> pd.DataFrame:
    if INPUT_CSV.exists():
        return pd.read_csv(INPUT_CSV)
    if INPUT_XLSX.exists():
        return pd.read_excel(INPUT_XLSX)
    raise FileNotFoundError(
        f"Could not find {INPUT_CSV} or {INPUT_XLSX}. Run run_full_workflow.bat first."
    )


def first_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lower_map = {str(c).lower(): c for c in df.columns}
    for cand in candidates:
        if cand in df.columns:
            return cand
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    # loose contains match, but conservative
    normalized = {str(c).lower().replace(" ", "_"): c for c in df.columns}
    for cand in candidates:
        key = cand.lower().replace(" ", "_")
        if key in normalized:
            return normalized[key]
    return None


def numeric_series(df: pd.DataFrame, candidates: list[str], default=np.nan) -> pd.Series:
    col = first_col(df, candidates)
    if col is None:
        return pd.Series(default, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def text_series(df: pd.DataFrame, candidates: list[str], default="") -> pd.Series:
    col = first_col(df, candidates)
    if col is None:
        return pd.Series(default, index=df.index, dtype="object")
    return df[col].fillna(default).astype(str)


def build_core_input(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[dict]]:
    """Map current engine output columns into the Core v4.1 scoring schema.

    Returns (out, warnings_out, neutralized_inputs). neutralized_inputs is the
    structured, machine-readable audit trail of every input the shadow engine
    could not source and had to neutralize. Anything appended to it invalidates
    a direct shadow-vs-official comparison for the affected factor.
    """
    warnings_out: list[str] = []
    neutralized: list[dict] = []
    out = df.copy()

    # Identity / universe
    out["Universe"] = text_series(out, ["Universe", "Universe_Group"], "")

    # Technical inputs expected by core.scoring
    out["Return_5D"] = numeric_series(out, ["Return_5D", "Return_1W", "Ret_5D"])
    out["Return_21D"] = numeric_series(out, ["Return_21D", "Return_1M", "Ret_21D"])
    out["Return_63D"] = numeric_series(out, ["Return_63D", "Return_3M", "Ret_63D"])
    out["Volatility_20D"] = numeric_series(out, ["Volatility_20D", "Vol_20D", "Volatility"])
    out["Drawdown_60D"] = numeric_series(out, ["Drawdown_60D", "Current_Drawdown_60D", "Max_Drawdown_60D"])
    out["RSI"] = numeric_series(out, ["RSI", "RSI_14", "RSI14"])

    price_col = first_col(out, ["Price", "Close", "Last_Close", "Current_Price", "Adj_Close"])
    ma50_col = first_col(out, ["MA50", "SMA50", "Moving_Avg_50", "Price_MA50", "MA_50D", "MA_50"])
    ma200_col = first_col(out, ["MA200", "SMA200", "Moving_Avg_200", "Price_MA200", "MA_200D", "MA_200"])

    if price_col and ma50_col:
        out["Price"] = pd.to_numeric(out[price_col], errors="coerce")
        out["MA50"] = pd.to_numeric(out[ma50_col], errors="coerce")
        out["MA200"] = pd.to_numeric(out[ma200_col], errors="coerce") if ma200_col else np.nan
        if not ma200_col:
            warnings_out.append("MA200 missing: trend check uses MA50 only / neutral MA200 behavior inside core.")
            neutralized.append({"name": "MA200", "reason": "column absent from latest_scores.csv"})
    else:
        out["Price"] = 1.0
        out["MA50"] = 1.0
        out["MA200"] = 1.0
        warnings_out.append("Price/MA trend columns missing in latest_scores: trend confirmation neutralized in shadow run.")
        neutralized.append({"name": "trend (Price/MA50/MA200)",
                            "reason": "Price and/or MA columns absent from latest_scores.csv"})

    bench_col = first_col(out, ["Bench_Return_21D", "Benchmark_Return_21D", "Nifty_Return_21D", "Index_Return_21D"])
    if bench_col:
        out["Bench_Return_21D"] = pd.to_numeric(out[bench_col], errors="coerce")
    else:
        out["Bench_Return_21D"] = out["Return_21D"]
        warnings_out.append("Benchmark 21D return missing: relative-strength confirmation neutralized in shadow run.")
        neutralized.append({"name": "relative_strength (Benchmark_Return_21D)",
                            "reason": "Benchmark_Return_21D absent from latest_scores.csv"})

    # Optional fundamentals — check both data/ and output/ for the scaffold.
    fund_paths = [DATA_DIR / "fundamentals_latest.csv", OUTPUT_DIR / "fundamentals_latest.csv"]
    fpath = next((p for p in fund_paths if p.exists()), None)
    if fpath is not None:
        try:
            fund = pd.read_csv(fpath)
            keep = [c for c in ["Symbol", "Fundamental_Score", "Fundamental_Coverage"] if c in fund.columns]
            if "Symbol" in keep and "Fundamental_Score" in keep:
                out = out.merge(fund[keep], on="Symbol", how="left")
                if out["Fundamental_Score"].notna().sum() == 0:
                    warnings_out.append("fundamentals_latest.csv present but all Fundamental_Score values are NaN.")
                    neutralized.append({"name": "fundamentals (Fundamental_Score)",
                                        "reason": "all values NaN (AMBER health)"})
            else:
                warnings_out.append("fundamentals_latest.csv exists but lacks Symbol/Fundamental_Score; ignored.")
                neutralized.append({"name": "fundamentals (Fundamental_Score)",
                                    "reason": "file present but missing Symbol/Fundamental_Score"})
        except Exception as exc:
            warnings_out.append(f"Could not read fundamentals_latest.csv; ignored. Error: {exc}")
            neutralized.append({"name": "fundamentals (Fundamental_Score)",
                                "reason": f"read error: {exc}"})
    else:
        warnings_out.append("No fundamentals_latest.csv found: v4.1 shadow run is technical-only for now.")
        neutralized.append({"name": "fundamentals (Fundamental_Score)",
                            "reason": "fundamentals_latest.csv not found in data/ or output/"})

    # Required minimum check
    if out["Return_21D"].notna().sum() == 0 or out["Volatility_20D"].notna().sum() == 0:
        raise ValueError(
            "latest_scores does not contain usable Return_21D and Volatility_20D columns. "
            "Run the current workflow first and confirm latest_scores.csv has technical columns."
        )

    return out, warnings_out, neutralized


def assign_shadow_buckets(df: pd.DataFrame) -> pd.Series:
    buckets = []
    for _, r in df.iterrows():
        rank = r.get("V4_Rank")
        score = r.get("V4_Final_Score")
        if pd.isna(rank) or pd.isna(score):
            buckets.append("No Score")
        elif rank <= 5 and score >= 70:
            buckets.append("V4 Top Candidate")
        elif rank <= 20 and score >= 60:
            buckets.append("V4 Candidate")
        elif rank <= 50 and score >= 50:
            buckets.append("V4 Watchlist")
        else:
            buckets.append("V4 Lower Priority")
    return pd.Series(buckets, index=df.index)


def make_summary(old: pd.DataFrame, shadow: pd.DataFrame, warnings_out: list[str]) -> dict:
    old_rank_col = first_col(shadow, ["Opportunity_Rank", "Rank", "Current_Rank"])
    if old_rank_col:
        current_top20 = set(shadow.loc[pd.to_numeric(shadow[old_rank_col], errors="coerce") <= 20, "Symbol"].astype(str))
    else:
        # fallback: sort by old score
        old_score_col = first_col(shadow, ["Old_Final_Score", "Final_Score", "Confidence_Adjusted_Score"])
        current_top20 = set(shadow.sort_values(old_score_col, ascending=False).head(20)["Symbol"].astype(str)) if old_score_col else set()

    v4_top20 = set(shadow.loc[shadow["V4_Rank"] <= 20, "Symbol"].astype(str))
    overlap = sorted(current_top20 & v4_top20)
    added = sorted(v4_top20 - current_top20)
    dropped = sorted(current_top20 - v4_top20)

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "input_rows": int(len(old)),
        "scored_rows": int(shadow["V4_Final_Score"].notna().sum()),
        "current_top20_count": int(len(current_top20)),
        "v4_top20_count": int(len(v4_top20)),
        "top20_overlap_count": int(len(overlap)),
        "top20_overlap_symbols": overlap,
        "v4_added_to_top20": added,
        "v4_dropped_from_top20": dropped,
        "warnings": warnings_out,
        "official_outputs_touched": False,
        "shadow_outputs": [str(OUT_CSV.relative_to(BASE_DIR)), str(OUT_XLSX.relative_to(BASE_DIR))],
    }


def write_outputs(df: pd.DataFrame, summary: dict) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    df.to_csv(OUT_CSV, index=False)

    top20_union_symbols = set(summary["top20_overlap_symbols"]) | set(summary["v4_added_to_top20"]) | set(summary["v4_dropped_from_top20"])
    top20_cmp = df[df["Symbol"].astype(str).isin(top20_union_symbols)].copy()
    if not top20_cmp.empty:
        top20_cmp = top20_cmp.sort_values(["V4_Rank", "Old_Rank"], na_position="last")

    summary_rows = []
    for k, v in summary.items():
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        summary_rows.append({"Metric": k, "Value": v})
    summary_df = pd.DataFrame(summary_rows)

    try:
        with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Shadow_Ranking", index=False)
            top20_cmp.to_excel(writer, sheet_name="Top20_Comparison", index=False)
            summary_df.to_excel(writer, sheet_name="Summary", index=False)
    except Exception:
        # CSV still exists if xlsx fails.
        raise

    SUMMARY_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _run_adaptive_shadow() -> None:
    """Fit adaptive alpha weights (dormant by default). Always writes both
    adaptive_weights_shadow.json AND adaptive_weights_log.json so the evidence
    bundle has audit trail even when nothing changed."""
    try:
        from core import adaptive_weights as aw, config as C, validation_status as vs
    except Exception as e:
        print(f"adaptive shadow skipped (import): {e}")
        return

    baseline = getattr(C, "ALPHA_WEIGHTS", None) or {"momentum": 0.5, "trend": 0.3, "safety": 0.2}
    v_status = vs.read_status(OUTPUT_DIR / "validation_status.json")
    n_eff = int(v_status.get("stats", {}).get("effective_validation_dates") or 0)

    # Real training panel: inner-join per-(Date,Symbol) alpha component scores
    # with matured forward returns. Without this join alpha_cols is empty and
    # the fit can never learn anything.
    panel = build_adaptive_panel(
        alpha_history_path=OUTPUT_DIR / "alpha_score_history.csv",
        forward_history_path=OUTPUT_DIR / "forward_return_history.csv",
        baseline_keys=list(baseline.keys()),
    )

    log = aw.fit_adaptive_weights(
        panel=panel,
        baseline=baseline,
        target_date=pd.Timestamp.now(),
        horizon=int(getattr(C, "BACKTEST_HOLD_DAYS", 10)),
        enabled=bool(getattr(C, "ADAPTIVE_ENABLED", False)),
        n_effective_dates=n_eff,
        min_dates=int(getattr(C, "ADAPTIVE_MIN_DATES", 60)),
        validation_verdict=str(v_status.get("verdict", "")),
        shrinkage_alpha=float(getattr(C, "ADAPTIVE_SHRINKAGE_ALPHA", 0.20)),
        max_step=float(getattr(C, "ADAPTIVE_MAX_STEP", 0.05)),
        max_total_drift=float(getattr(C, "ADAPTIVE_MAX_TOTAL_DRIFT", 0.30)),
        ridge_alpha=float(getattr(C, "ADAPTIVE_RIDGE_ALPHA", 1.0)),
        max_alpha_corr=float(getattr(C, "ADAPTIVE_MAX_ALPHA_CORR", 0.8)),
    )
    log["panel_columns"] = list(panel.columns)
    log["panel_rows"] = int(len(panel))
    (OUTPUT_DIR / "adaptive_weights_log.json").write_text(
        json.dumps(log, indent=2), encoding="utf-8")
    (OUTPUT_DIR / "adaptive_weights_shadow.json").write_text(
        json.dumps({"weights": log.get("shrunk_final") or log.get("baseline"),
                    "dormant": log.get("dormant", True),
                    "dormant_reason": log.get("dormant_reason")}, indent=2),
        encoding="utf-8",
    )
    tag = "dormant" if log.get("dormant") else "active-shadow"
    print(f"adaptive_weights: {tag} ({log.get('dormant_reason') or 'ok'}) "
          f"[panel rows={log['panel_rows']}, cols={log['panel_columns']}]")


def build_adaptive_panel(alpha_history_path: Path,
                         forward_history_path: Path,
                         baseline_keys: list[str]) -> pd.DataFrame:
    """Inner-join alpha_score_history and forward_return_history on (Date, Symbol).

    Returns a DataFrame with columns: Date, <baseline alpha keys present>, Fwd_Return.
    'Symbol' is intentionally dropped so guardrail #5 in fit_adaptive_weights
    (no per-symbol learning) passes. Missing files or empty joins yield an
    empty DataFrame — the fit records a specific dormant_reason.
    """
    if not alpha_history_path.exists() or not forward_history_path.exists():
        return pd.DataFrame()
    try:
        alphas = pd.read_csv(alpha_history_path)
        fwd = pd.read_csv(forward_history_path)
    except Exception:
        return pd.DataFrame()
    if "Signal_Date" in fwd.columns:
        fwd = fwd.rename(columns={"Signal_Date": "Date"})
    if "Net_Forward_Return" in fwd.columns:
        fwd = fwd.rename(columns={"Net_Forward_Return": "Fwd_Return"})
    if not {"Date", "Symbol", "Fwd_Return"}.issubset(fwd.columns):
        return pd.DataFrame()
    if not {"Date", "Symbol"}.issubset(alphas.columns):
        return pd.DataFrame()
    keep_alpha = [k for k in baseline_keys if k in alphas.columns]
    if not keep_alpha:
        return pd.DataFrame()
    fwd = fwd[["Date", "Symbol", "Fwd_Return"]].copy()
    alphas = alphas[["Date", "Symbol"] + keep_alpha].copy()
    fwd["Date"] = fwd["Date"].astype(str)
    alphas["Date"] = alphas["Date"].astype(str)
    fwd["Symbol"] = fwd["Symbol"].astype(str)
    alphas["Symbol"] = alphas["Symbol"].astype(str)
    merged = alphas.merge(fwd, on=["Date", "Symbol"], how="inner")
    # Drop Symbol before returning (guardrail #5).
    return merged.drop(columns=["Symbol"])


def main() -> None:
    print("NSE Quant Engine - Stage 4.0 Shadow Scoring Runner")
    print("==================================================")
    setup_core_import()
    from core import scoring

    old = read_latest_scores()
    print(f"Loaded official latest scores: {len(old)} rows")

    core_input, warnings_out, neutralized_inputs = build_core_input(old)
    shadow_scored = scoring.compute_opportunity_scores(core_input)
    shadow_scored = scoring.apply_fundamental_factor(shadow_scored)

    out = old.copy()
    # Preserve old official score/rank clearly.
    old_score_col = first_col(out, ["Final_Score", "Confidence_Adjusted_Score", "Opportunity_Score"])
    old_rank_col = first_col(out, ["Opportunity_Rank", "Rank", "Current_Rank"])
    out["Old_Final_Score"] = pd.to_numeric(out[old_score_col], errors="coerce") if old_score_col else np.nan
    out["Old_Rank"] = pd.to_numeric(out[old_rank_col], errors="coerce") if old_rank_col else out["Old_Final_Score"].rank(ascending=False, method="min")

    out["V4_Opportunity_Score"] = shadow_scored["Opportunity_Score"]
    out["V4_Final_Score"] = shadow_scored["Final_Score"] if "Final_Score" in shadow_scored.columns else shadow_scored["Opportunity_Score"]
    out["V4_Blended_Momentum"] = shadow_scored.get("Blended_Momentum")
    out["V4_Risk_Adj_Momentum"] = shadow_scored.get("Risk_Adj_Momentum")
    out["V4_Momentum_Pctile"] = shadow_scored.get("Momentum_Pctile")
    out["V4_Vol_Pctile"] = shadow_scored.get("Vol_Pctile")
    if "Fundamental_Score" in shadow_scored.columns:
        out["V4_Fundamental_Score"] = shadow_scored.get("Fundamental_Score")
    if "Fundamental_Coverage" in shadow_scored.columns:
        out["V4_Fundamental_Coverage"] = shadow_scored.get("Fundamental_Coverage")

    # Rank only eligible rows if possible, but retain all rows.
    eligible_col = first_col(out, ["Opportunity_Eligible", "Eligible"])
    if eligible_col:
        eligible_mask = out[eligible_col].astype(str).str.lower().isin(["yes", "true", "1", "eligible"])
    else:
        eligible_mask = pd.Series(True, index=out.index)
    out["V4_Rank"] = np.nan
    out.loc[eligible_mask, "V4_Rank"] = out.loc[eligible_mask, "V4_Final_Score"].rank(ascending=False, method="min")
    out["V4_Score_Delta_vs_Current"] = out["V4_Final_Score"] - out["Old_Final_Score"]
    out["V4_Rank_Change_vs_Current"] = out["Old_Rank"] - out["V4_Rank"]  # positive = improved
    out["V4_Bucket"] = assign_shadow_buckets(out)
    out["V4_Shadow_Status"] = "Shadow only - official ranking unchanged"
    out["V4_Warnings"] = " | ".join(warnings_out)

    # Sort for viewing by V4 rank first.
    out = out.sort_values(["V4_Rank", "Old_Rank"], na_position="last").reset_index(drop=True)

    summary = make_summary(old, out, warnings_out)
    summary["neutralized_inputs"] = neutralized_inputs
    if neutralized_inputs:
        print(f"[shadow] neutralized_inputs ({len(neutralized_inputs)}): "
              + "; ".join(f"{n['name']} — {n['reason']}" for n in neutralized_inputs))
    else:
        print("[shadow] neutralized_inputs: [] (all required inputs present)")
    write_outputs(out, summary)
    try:
        _run_adaptive_shadow()
    except Exception as e:
        print(f"adaptive shadow non-fatal: {e}")

    print("")
    print("Shadow mode complete. Official outputs were NOT overwritten.")
    print(f"Saved: {OUT_CSV}")
    print(f"Saved: {OUT_XLSX}")
    print(f"Top 20 overlap: {summary['top20_overlap_count']} / {min(summary['current_top20_count'], summary['v4_top20_count']) if summary['current_top20_count'] and summary['v4_top20_count'] else 20}")
    if summary["warnings"]:
        print("")
        print("Warnings / neutralizations:")
        for w in summary["warnings"]:
            print(f"- {w}")
    print("")
    print("Open output\\latest_scores_v4_shadow.xlsx and compare sheets:")
    print("- Shadow_Ranking")
    print("- Top20_Comparison")
    print("- Summary")


if __name__ == "__main__":
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=FutureWarning)
            main()
    except Exception:
        print("Shadow scoring failed.")
        traceback.print_exc()
        sys.exit(1)
