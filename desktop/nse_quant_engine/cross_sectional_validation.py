"""
Cross-Sectional Validation - Stage 3.3 Hotfix
=============================================

Fixes first-run issue:
    If forward_return_history.csv is empty or has only headers/no rows,
    produce "Insufficient History" outputs instead of crashing.

Outputs:
    output/cross_sectional_validation_detail.csv
    output/score_bucket_performance.csv
    output/cross_sectional_spread_by_date.csv
    output/cross_sectional_spread_summary.csv
    output/cross_sectional_validation_report.md
    output/latest_scores_validated.xlsx

Run after validation_builder.py:
    python cross_sectional_validation.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict
import math
import numpy as np
import pandas as pd
from pandas.errors import EmptyDataError

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
SCORING_RULES_CSV = BASE_DIR / "scoring_rules.csv"

SCORE_HISTORY = OUTPUT_DIR / "score_history.csv"
FORWARD_RETURNS = OUTPUT_DIR / "forward_return_history.csv"
LATEST_SCORES_CSV = OUTPUT_DIR / "latest_scores.csv"
MISSING_SIGNALS = OUTPUT_DIR / "forward_return_missing_signals.csv"

DETAIL_OUT = OUTPUT_DIR / "cross_sectional_validation_detail.csv"
BUCKET_PERF_OUT = OUTPUT_DIR / "score_bucket_performance.csv"
SPREAD_DATES_OUT = OUTPUT_DIR / "cross_sectional_spread_by_date.csv"
SPREAD_SUMMARY_OUT = OUTPUT_DIR / "cross_sectional_spread_summary.csv"
REPORT_OUT = OUTPUT_DIR / "cross_sectional_validation_report.md"
VALIDATED_XLSX = OUTPUT_DIR / "latest_scores_validated.xlsx"

FORWARD_COLUMNS = [
    "Signal_Date",
    "Signal_Date_Actual_In_Price_File",
    "Symbol",
    "Universe",
    "Universe_Group",
    "Opportunity_Type",
    "Opportunity_Eligible",
    "Name",
    "Horizon_Days",
    "Signal_Price_CurrentBasis",
    "Forward_Date",
    "Forward_Price_CurrentBasis",
    "Gross_Forward_Return",
    "Round_Trip_Cost",
    "Cost_Note",
    "Net_Forward_Return",
    "Signal_Final_Score",
    "Signal_Bucket",
]

DETAIL_COLUMNS = [
    "Signal_Date",
    "Symbol",
    "Universe",
    "Universe_Group",
    "Opportunity_Type",
    "Opportunity_Eligible",
    "Name",
    "Horizon_Days",
    "Net_Forward_Return",
    "Gross_Forward_Return",
    "Round_Trip_Cost",
    "Final_Score",
    "Confidence_Adjusted_Score",
    "Bucket",
    "Rank",
    "Score_Rank_On_Date",
    "Score_Percentile_On_Date",
    "Bucket_TopN",
    "Score_Decile",
    "Score_Quintile",
]

BUCKET_PERF_COLUMNS = [
    "Horizon_Days",
    "Bucket",
    "Obs",
    "Avg_Net_Return",
    "Median_Net_Return",
    "Hit_Rate_Net",
    "Worst_Net_Return",
    "Best_Net_Return",
    "Std_Net_Return",
    "Avg_Gross_Return",
    "Avg_Cost",
    "Avg_Final_Score",
    "Bucket_Type",
]

SPREAD_BY_DATE_COLUMNS = [
    "Signal_Date",
    "Horizon_Days",
    "All_Avg_Net_Return",
    "Top_Quintile_Avg_Net_Return",
    "Bottom_Quintile_Avg_Net_Return",
    "Top10_Avg_Net_Return",
    "Top25_Avg_Net_Return",
    "Obs_All",
    "Obs_Top_Quintile",
    "Obs_Bottom_Quintile",
    "Obs_Top10",
    "Obs_Top25",
    "TopMinusBottom_Quintile",
    "Top10MinusAll",
    "Top25MinusAll",
]

SPREAD_SUMMARY_COLUMNS = [
    "Horizon_Days",
    "Validation_Dates",
    "Median_Signal_Gap_Days",
    "Overlap_Adjustment_Factor",
    "Effective_Validation_Dates",
    "Avg_TopMinusBottom_Quintile",
    "Median_TopMinusBottom_Quintile",
    "Std_TopMinusBottom_Quintile",
    "TStat_TopMinusBottom",
    "Adjusted_TStat_TopMinusBottom",
    "Hit_Rate_TopBeatsBottom",
    "Avg_Top10MinusAll",
    "Hit_Rate_Top10BeatsAll",
    "Avg_Top25MinusAll",
    "Hit_Rate_Top25BeatsAll",
    "Avg_Obs_All",
    "Bootstrap_Mean",
    "Bootstrap_CI5",
    "Bootstrap_CI95",
    "Bootstrap_Prob_Positive",
]

DEFAULT_RULES = {
    "CrossVal_Min_Obs": 50,
    "CrossVal_Min_Dates": 10,
    "CrossVal_Min_Effective_Dates": 6,
    "CrossVal_Min_Spread": 0.005,
    "CrossVal_Min_HitRate": 0.55,
    "CrossVal_Min_TStat": 1.50,
    "CrossVal_Min_Bootstrap_Prob": 0.70,
    "CrossVal_Horizon": 10,
    "Bootstrap_Iterations": 2000,
    "Bootstrap_Random_Seed": 42,
}


def empty_df(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def safe_read_csv(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    if not path.exists():
        return empty_df(columns or [])

    try:
        df = pd.read_csv(path)
    except EmptyDataError:
        return empty_df(columns or [])

    if columns:
        for col in columns:
            if col not in df.columns:
                df[col] = np.nan
        return df

    return df


def write_csv_with_headers(df: pd.DataFrame, path: Path, columns: list[str]) -> None:
    if df is None or df.empty:
        empty_df(columns).to_csv(path, index=False)
    else:
        for col in columns:
            if col not in df.columns:
                df[col] = np.nan
        df[columns].to_csv(path, index=False)


def load_rules() -> Dict[str, float]:
    rules = DEFAULT_RULES.copy()
    if not SCORING_RULES_CSV.exists():
        return rules

    df = safe_read_csv(SCORING_RULES_CSV)
    if df.empty or "Parameter" not in df.columns or "Value" not in df.columns:
        return rules

    for _, row in df.iterrows():
        key = str(row.get("Parameter", "")).strip()
        if not key:
            continue
        try:
            rules[key] = float(row.get("Value"))
        except Exception:
            pass
    return rules


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    scores = safe_read_csv(SCORE_HISTORY)
    fwd = safe_read_csv(FORWARD_RETURNS, FORWARD_COLUMNS)
    missing = safe_read_csv(MISSING_SIGNALS)

    if not scores.empty and "Date" in scores.columns:
        scores["Date"] = pd.to_datetime(scores["Date"], errors="coerce")
        for col in ["Final_Score", "Confidence_Adjusted_Score"]:
            if col in scores.columns:
                scores[col] = pd.to_numeric(scores[col], errors="coerce")

    if not fwd.empty:
        fwd["Signal_Date"] = pd.to_datetime(fwd["Signal_Date"], errors="coerce")
        for col in ["Net_Forward_Return", "Gross_Forward_Return", "Round_Trip_Cost"]:
            if col in fwd.columns:
                fwd[col] = pd.to_numeric(fwd[col], errors="coerce")

    return scores, fwd, missing


def assign_buckets(work: pd.DataFrame) -> pd.DataFrame:
    pieces = []

    for (signal_date, horizon), g in work.groupby(["Signal_Date", "Horizon_Days"]):
        g = g.dropna(subset=["Final_Score", "Net_Forward_Return"]).copy()

        if len(g) < 10:
            continue

        g = g.sort_values("Final_Score", ascending=False).reset_index(drop=True)
        g["Score_Rank_On_Date"] = range(1, len(g) + 1)
        g["Score_Percentile_On_Date"] = 1 - ((g["Score_Rank_On_Date"] - 1) / max(len(g) - 1, 1))

        g["Bucket_TopN"] = "Other"
        g.loc[g["Score_Rank_On_Date"] <= 5, "Bucket_TopN"] = "Top 5"
        g.loc[(g["Score_Rank_On_Date"] > 5) & (g["Score_Rank_On_Date"] <= 10), "Bucket_TopN"] = "Top 6-10"
        g.loc[(g["Score_Rank_On_Date"] > 10) & (g["Score_Rank_On_Date"] <= 25), "Bucket_TopN"] = "Top 11-25"

        try:
            g["Score_Decile"] = pd.qcut(
                g["Final_Score"].rank(method="first", ascending=True),
                10,
                labels=["D10_Lowest", "D9", "D8", "D7", "D6", "D5", "D4", "D3", "D2", "D1_Highest"],
            )
        except Exception:
            g["Score_Decile"] = "Unbucketed"

        try:
            g["Score_Quintile"] = pd.qcut(
                g["Final_Score"].rank(method="first", ascending=True),
                5,
                labels=["Q5_Lowest", "Q4", "Q3", "Q2", "Q1_Highest"],
            )
        except Exception:
            g["Score_Quintile"] = "Unbucketed"

        pieces.append(g)

    if not pieces:
        return empty_df(DETAIL_COLUMNS)

    return pd.concat(pieces, ignore_index=True)


def make_detail(scores: pd.DataFrame, fwd: pd.DataFrame) -> pd.DataFrame:
    if scores.empty or fwd.empty:
        return empty_df(DETAIL_COLUMNS)

    score_cols = [
        "Date",
        "Symbol",
        "Universe",
        "Universe_Group",
        "Opportunity_Type",
        "Opportunity_Eligible",
        "Name",
        "Category",
        "Final_Score",
        "Confidence_Adjusted_Score",
        "Bucket",
        "Rank",
        "Risk_Flag",
    ]

    for col in score_cols:
        if col not in scores.columns:
            scores[col] = np.nan

    s = scores[score_cols].copy().rename(columns={"Date": "Signal_Date"})
    work = fwd.merge(s, on=["Signal_Date", "Symbol"], how="left", suffixes=("", "_ScoreHist"))

    if "Opportunity_Eligible" in work.columns:
        work = work[work["Opportunity_Eligible"].astype(str).str.lower().eq("yes")].copy()

    detail = assign_buckets(work)

    for col in DETAIL_COLUMNS:
        if col not in detail.columns:
            detail[col] = np.nan

    return detail


def perf_stats(g: pd.DataFrame) -> pd.Series:
    net = pd.to_numeric(g["Net_Forward_Return"], errors="coerce").dropna()
    gross = pd.to_numeric(g["Gross_Forward_Return"], errors="coerce").dropna()

    if net.empty:
        return pd.Series({
            "Obs": 0,
            "Avg_Net_Return": np.nan,
            "Median_Net_Return": np.nan,
            "Hit_Rate_Net": np.nan,
            "Worst_Net_Return": np.nan,
            "Best_Net_Return": np.nan,
            "Std_Net_Return": np.nan,
            "Avg_Gross_Return": np.nan,
            "Avg_Cost": np.nan,
            "Avg_Final_Score": np.nan,
        })

    return pd.Series({
        "Obs": len(net),
        "Avg_Net_Return": net.mean(),
        "Median_Net_Return": net.median(),
        "Hit_Rate_Net": float((net > 0).mean()),
        "Worst_Net_Return": net.min(),
        "Best_Net_Return": net.max(),
        "Std_Net_Return": net.std(),
        "Avg_Gross_Return": gross.mean() if not gross.empty else np.nan,
        "Avg_Cost": pd.to_numeric(g.get("Round_Trip_Cost", pd.Series(np.nan, index=g.index)), errors="coerce").mean(),
        "Avg_Final_Score": pd.to_numeric(g["Final_Score"], errors="coerce").mean(),
    })


def build_bucket_performance(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return empty_df(BUCKET_PERF_COLUMNS)

    rows = []

    for bucket_col in ["Score_Quintile", "Score_Decile", "Bucket_TopN"]:
        perf = (
            detail.groupby(["Horizon_Days", bucket_col])
            .apply(perf_stats)
            .reset_index()
            .rename(columns={bucket_col: "Bucket"})
        )
        perf["Bucket_Type"] = bucket_col
        rows.append(perf)

    all_perf = detail.groupby(["Horizon_Days"]).apply(perf_stats).reset_index()
    all_perf["Bucket"] = "All Eligible"
    all_perf["Bucket_Type"] = "All"
    rows.append(all_perf)

    out = pd.concat(rows, ignore_index=True)

    for col in BUCKET_PERF_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan

    return out[BUCKET_PERF_COLUMNS]


def bootstrap_mean(values: np.ndarray, iterations: int, seed: int) -> dict:
    values = np.asarray(values, dtype=float)
    values = values[~np.isnan(values)]

    if len(values) < 2:
        return {
            "Bootstrap_Mean": np.nan,
            "Bootstrap_CI5": np.nan,
            "Bootstrap_CI95": np.nan,
            "Bootstrap_Prob_Positive": np.nan,
        }

    rng = np.random.default_rng(seed)
    means = np.empty(iterations)
    n = len(values)

    for i in range(iterations):
        sample = rng.choice(values, size=n, replace=True)
        means[i] = np.mean(sample)

    return {
        "Bootstrap_Mean": float(np.mean(means)),
        "Bootstrap_CI5": float(np.percentile(means, 5)),
        "Bootstrap_CI95": float(np.percentile(means, 95)),
        "Bootstrap_Prob_Positive": float(np.mean(means > 0)),
    }


def median_gap_days(dates: pd.Series) -> float:
    d = pd.to_datetime(pd.Series(dates).dropna().drop_duplicates()).sort_values()

    if len(d) < 2:
        return np.nan

    gaps = d.diff().dt.days.dropna()
    return float(gaps.median()) if not gaps.empty else np.nan


def build_spread_by_date(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return empty_df(SPREAD_BY_DATE_COLUMNS)

    spreads = []

    for (signal_date, horizon), g in detail.groupby(["Signal_Date", "Horizon_Days"]):
        all_avg = pd.to_numeric(g["Net_Forward_Return"], errors="coerce").mean()
        top_q = g[g["Score_Quintile"].astype(str).eq("Q1_Highest")]
        bot_q = g[g["Score_Quintile"].astype(str).eq("Q5_Lowest")]
        top10 = g[g["Score_Rank_On_Date"] <= 10]
        top25 = g[g["Score_Rank_On_Date"] <= 25]

        row = {
            "Signal_Date": signal_date,
            "Horizon_Days": horizon,
            "All_Avg_Net_Return": all_avg,
            "Top_Quintile_Avg_Net_Return": pd.to_numeric(top_q["Net_Forward_Return"], errors="coerce").mean() if not top_q.empty else np.nan,
            "Bottom_Quintile_Avg_Net_Return": pd.to_numeric(bot_q["Net_Forward_Return"], errors="coerce").mean() if not bot_q.empty else np.nan,
            "Top10_Avg_Net_Return": pd.to_numeric(top10["Net_Forward_Return"], errors="coerce").mean() if not top10.empty else np.nan,
            "Top25_Avg_Net_Return": pd.to_numeric(top25["Net_Forward_Return"], errors="coerce").mean() if not top25.empty else np.nan,
            "Obs_All": len(g),
            "Obs_Top_Quintile": len(top_q),
            "Obs_Bottom_Quintile": len(bot_q),
            "Obs_Top10": len(top10),
            "Obs_Top25": len(top25),
        }

        row["TopMinusBottom_Quintile"] = row["Top_Quintile_Avg_Net_Return"] - row["Bottom_Quintile_Avg_Net_Return"]
        row["Top10MinusAll"] = row["Top10_Avg_Net_Return"] - row["All_Avg_Net_Return"]
        row["Top25MinusAll"] = row["Top25_Avg_Net_Return"] - row["All_Avg_Net_Return"]

        spreads.append(row)

    out = pd.DataFrame(spreads, columns=SPREAD_BY_DATE_COLUMNS)

    if not out.empty:
        out["Signal_Date"] = pd.to_datetime(out["Signal_Date"], errors="coerce")

    return out


def build_spread_summary(spread_by_date: pd.DataFrame, rules: Dict[str, float]) -> pd.DataFrame:
    if spread_by_date.empty:
        return empty_df(SPREAD_SUMMARY_COLUMNS)

    rows = []
    iterations = int(rules.get("Bootstrap_Iterations", 2000))
    seed = int(rules.get("Bootstrap_Random_Seed", 42))

    for horizon, g in spread_by_date.groupby("Horizon_Days"):
        spreads = pd.to_numeric(g["TopMinusBottom_Quintile"], errors="coerce").dropna()
        n = len(spreads)
        std = spreads.std(ddof=1) if n > 1 else np.nan
        mean = spreads.mean() if n > 0 else np.nan
        raw_t = mean / (std / np.sqrt(n)) if n > 1 and pd.notna(std) and std > 0 else np.nan

        gap = median_gap_days(g["Signal_Date"])
        overlap_factor = max(1.0, float(horizon) / gap) if pd.notna(gap) and gap > 0 else 1.0
        effective_n = n / overlap_factor if overlap_factor > 0 else n
        adjusted_t = mean / (std / np.sqrt(effective_n)) if n > 1 and pd.notna(std) and std > 0 and effective_n > 1 else np.nan
        boot = bootstrap_mean(spreads.to_numpy(), iterations=iterations, seed=seed + int(horizon))

        rows.append({
            "Horizon_Days": horizon,
            "Validation_Dates": n,
            "Median_Signal_Gap_Days": gap,
            "Overlap_Adjustment_Factor": overlap_factor,
            "Effective_Validation_Dates": effective_n,
            "Avg_TopMinusBottom_Quintile": mean,
            "Median_TopMinusBottom_Quintile": spreads.median() if n > 0 else np.nan,
            "Std_TopMinusBottom_Quintile": std,
            "TStat_TopMinusBottom": raw_t,
            "Adjusted_TStat_TopMinusBottom": adjusted_t,
            "Hit_Rate_TopBeatsBottom": float((spreads > 0).mean()) if n > 0 else np.nan,
            "Avg_Top10MinusAll": pd.to_numeric(g["Top10MinusAll"], errors="coerce").mean(),
            "Hit_Rate_Top10BeatsAll": float((pd.to_numeric(g["Top10MinusAll"], errors="coerce") > 0).mean()),
            "Avg_Top25MinusAll": pd.to_numeric(g["Top25MinusAll"], errors="coerce").mean(),
            "Hit_Rate_Top25BeatsAll": float((pd.to_numeric(g["Top25MinusAll"], errors="coerce") > 0).mean()),
            "Avg_Obs_All": pd.to_numeric(g["Obs_All"], errors="coerce").mean(),
            **boot,
        })

    out = pd.DataFrame(rows)

    for col in SPREAD_SUMMARY_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan

    return out[SPREAD_SUMMARY_COLUMNS]


def validation_verdict(spread_summary: pd.DataFrame, rules: Dict[str, float]) -> str:
    if spread_summary.empty:
        return "Insufficient History"

    horizon = int(rules.get("CrossVal_Horizon", 10))
    row_df = spread_summary[spread_summary["Horizon_Days"].eq(horizon)]

    if row_df.empty:
        return "Insufficient History"

    row = row_df.iloc[0]

    validation_dates = row.get("Validation_Dates", 0)
    effective_dates = row.get("Effective_Validation_Dates", 0)
    avg_obs = row.get("Avg_Obs_All", 0)
    spread = row.get("Avg_TopMinusBottom_Quintile", np.nan)
    hit_rate = row.get("Hit_Rate_TopBeatsBottom", np.nan)
    adj_t = row.get("Adjusted_TStat_TopMinusBottom", np.nan)
    boot_prob = row.get("Bootstrap_Prob_Positive", np.nan)

    if validation_dates < rules.get("CrossVal_Min_Dates", 10):
        return "Insufficient History"
    if effective_dates < rules.get("CrossVal_Min_Effective_Dates", 6):
        return "Insufficient Independent History"
    if avg_obs < rules.get("CrossVal_Min_Obs", 50):
        return "Insufficient Breadth"
    if pd.isna(spread) or pd.isna(hit_rate) or pd.isna(adj_t) or pd.isna(boot_prob):
        return "Insufficient Statistical Evidence"

    if (
        spread >= rules.get("CrossVal_Min_Spread", 0.005)
        and hit_rate >= rules.get("CrossVal_Min_HitRate", 0.55)
        and adj_t >= rules.get("CrossVal_Min_TStat", 1.5)
        and boot_prob >= rules.get("CrossVal_Min_Bootstrap_Prob", 0.70)
    ):
        return "Validation Positive"

    if spread <= -rules.get("CrossVal_Min_Spread", 0.005) and adj_t <= -rules.get("CrossVal_Min_TStat", 1.5):
        return "Validation Negative"

    return "No Proven Edge Yet"


def evidence_grade(spread_summary: pd.DataFrame, rules: Dict[str, float]) -> str:
    verdict = validation_verdict(spread_summary, rules)

    if verdict == "Validation Positive":
        horizon = int(rules.get("CrossVal_Horizon", 10))
        row = spread_summary[spread_summary["Horizon_Days"].eq(horizon)].iloc[0]

        if row.get("Effective_Validation_Dates", 0) >= 20 and row.get("Adjusted_TStat_TopMinusBottom", 0) >= 2:
            return "Strong Evidence"

        return "Moderate Evidence"

    if verdict in ["No Proven Edge Yet", "Validation Negative"]:
        return "Weak or Negative Evidence"

    return "Insufficient Evidence"


def write_report(bucket_perf: pd.DataFrame, spread_by_date: pd.DataFrame, spread_summary: pd.DataFrame, verdict: str, grade: str, missing: pd.DataFrame) -> None:
    lines = []
    ts = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")

    lines.append("# Cross-Sectional Validation Report")
    lines.append("")
    lines.append(f"Generated: {ts}")
    lines.append("")
    lines.append("## Validation Verdict")
    lines.append("")
    lines.append(f"**{verdict}**")
    lines.append("")
    lines.append(f"Evidence grade: **{grade}**")
    lines.append("")
    lines.append("This tests whether high-scored candidates outperformed lower-scored candidates after estimated round-trip costs.")
    lines.append("")

    lines.append("## Spread Summary")
    if spread_summary.empty:
        lines.append("_No spread summary yet. Need more historical runs with completed forward-return horizons._")
    else:
        lines.append(spread_summary.round(6).to_markdown(index=False))
    lines.append("")

    lines.append("## Bucket Performance")
    if bucket_perf.empty:
        lines.append("_No bucket performance yet._")
    else:
        show = bucket_perf[bucket_perf["Bucket_Type"].isin(["Score_Quintile", "All"])].copy()
        lines.append(show.round(6).to_markdown(index=False))
    lines.append("")

    lines.append("## Missing / Unmatured Signal Diagnostics")
    if missing.empty:
        lines.append("_No missing signal diagnostics found._")
    elif "Reason" in missing.columns:
        lines.append(missing["Reason"].value_counts(dropna=False).to_frame("Count").to_markdown())
    else:
        lines.append("_Missing signal file exists but has no Reason column._")
    lines.append("")

    lines.append("## Interpretation rules")
    lines.append("- Validation Positive requires minimum validation dates, effective dates, positive net spread, hit rate, overlap-adjusted t-stat, and bootstrap probability.")
    lines.append("- Insufficient History is expected in early paper-mode runs.")
    lines.append("- No Proven Edge Yet means the model may still be useful for watchlisting, but historical evidence is not strong enough.")
    lines.append("- Validation Negative means the scoring logic should be revised before relying on it.")

    REPORT_OUT.write_text("\n".join(lines), encoding="utf-8")


def write_validated_workbook(detail: pd.DataFrame, bucket_perf: pd.DataFrame, spread_by_date: pd.DataFrame, spread_summary: pd.DataFrame, verdict: str, grade: str, missing: pd.DataFrame) -> None:
    latest = safe_read_csv(LATEST_SCORES_CSV)
    verdict_df = pd.DataFrame([{"Validation_Verdict": verdict, "Evidence_Grade": grade}])

    with pd.ExcelWriter(VALIDATED_XLSX, engine="openpyxl") as writer:
        if not latest.empty:
            latest.to_excel(writer, sheet_name="Latest Scores", index=False)

            if "Opportunity_Eligible" in latest.columns:
                eligible = latest[latest["Opportunity_Eligible"].astype(str).str.lower().eq("yes")].copy()
            else:
                eligible = latest.copy()

            if "Final_Score" in eligible.columns:
                eligible.sort_values("Final_Score", ascending=False).head(25).to_excel(writer, sheet_name="Top Opportunities", index=False)

            if "Confidence_Adjusted_Score" in eligible.columns:
                eligible.sort_values("Confidence_Adjusted_Score", ascending=False).head(25).to_excel(writer, sheet_name="Top Confidence Adj", index=False)

        verdict_df.to_excel(writer, sheet_name="Validation Verdict", index=False)

        if not spread_summary.empty:
            spread_summary.to_excel(writer, sheet_name="Spread Summary", index=False)
        else:
            empty_df(SPREAD_SUMMARY_COLUMNS).to_excel(writer, sheet_name="Spread Summary", index=False)

        if not spread_by_date.empty:
            spread_by_date.to_excel(writer, sheet_name="Spread By Date", index=False)
        else:
            empty_df(SPREAD_BY_DATE_COLUMNS).to_excel(writer, sheet_name="Spread By Date", index=False)

        if not bucket_perf.empty:
            bucket_perf.to_excel(writer, sheet_name="Bucket Performance", index=False)
        else:
            empty_df(BUCKET_PERF_COLUMNS).to_excel(writer, sheet_name="Bucket Performance", index=False)

        if not detail.empty:
            detail.head(2000).to_excel(writer, sheet_name="Validation Detail", index=False)
        else:
            empty_df(DETAIL_COLUMNS).to_excel(writer, sheet_name="Validation Detail", index=False)

        if not missing.empty:
            missing.head(2000).to_excel(writer, sheet_name="Missing Signals", index=False)


def _write_validation_status(spread_summary: pd.DataFrame, verdict: str, grade: str, rules: Dict[str, float]) -> None:
    """Always emit validation_status.json — never let this be skipped by an uninitialised variable."""
    try:
        from core import validation_status as _vs
        horizon = int(rules.get("CrossVal_Horizon", 10))
        stats_payload: Dict[str, float] = {}
        if isinstance(spread_summary, pd.DataFrame) and not spread_summary.empty and "Horizon_Days" in spread_summary.columns:
            row = spread_summary[spread_summary["Horizon_Days"].eq(horizon)]
            if not row.empty:
                r = row.iloc[0]
                stats_payload = {
                    "validation_dates": float(r.get("Validation_Dates", 0) or 0),
                    "effective_validation_dates": float(r.get("Effective_Validation_Dates", 0) or 0),
                    "avg_obs": float(r.get("Avg_Obs_All", 0) or 0),
                    "spread": float(r.get("Avg_TopMinusBottom_Quintile", 0) or 0),
                    "hit_rate": float(r.get("Hit_Rate_TopBeatsBottom", 0) or 0),
                    "adj_tstat": float(r.get("Adjusted_TStat_TopMinusBottom", 0) or 0),
                    "bootstrap_prob": float(r.get("Bootstrap_Prob_Positive", 0) or 0),
                }
        try:
            stats_payload = _vs.apply_bayes_shrink(stats_payload)
        except Exception:
            pass
        _vs.write_status(OUTPUT_DIR / "validation_status.json", verdict, grade, stats_payload, horizon=horizon)
        print("Saved: " + str(OUTPUT_DIR / "validation_status.json"))
    except Exception as _e:
        print(f"validation_status.json write skipped: {_e}")


def main() -> None:
    print("Cross-Sectional Validation - Stage 3.3 Hotfix")
    print("=============================================")

    rules = load_rules()
    scores, fwd, missing = load_inputs()

    if scores.empty or fwd.empty:
        print("Insufficient data: score history exists but forward returns are empty or not matured yet.")
        detail = empty_df(DETAIL_COLUMNS)
        bucket_perf = empty_df(BUCKET_PERF_COLUMNS)
        spread_by_date = empty_df(SPREAD_BY_DATE_COLUMNS)
        spread_summary = empty_df(SPREAD_SUMMARY_COLUMNS)
        verdict = "Insufficient History"
        grade = "Insufficient Evidence"

        write_csv_with_headers(detail, DETAIL_OUT, DETAIL_COLUMNS)
        write_csv_with_headers(bucket_perf, BUCKET_PERF_OUT, BUCKET_PERF_COLUMNS)
        write_csv_with_headers(spread_by_date, SPREAD_DATES_OUT, SPREAD_BY_DATE_COLUMNS)
        write_csv_with_headers(spread_summary, SPREAD_SUMMARY_OUT, SPREAD_SUMMARY_COLUMNS)
        write_report(bucket_perf, spread_by_date, spread_summary, verdict, grade, missing)
        write_validated_workbook(detail, bucket_perf, spread_by_date, spread_summary, verdict, grade, missing)
        _write_validation_status(spread_summary, verdict, grade, rules)
        print(f"Saved: {REPORT_OUT}")
        print(f"Saved: {VALIDATED_XLSX}")
        print("Verdict: Insufficient History")
        return

    detail = make_detail(scores, fwd)
    bucket_perf = build_bucket_performance(detail)
    spread_by_date = build_spread_by_date(detail)
    spread_summary = build_spread_summary(spread_by_date, rules)
    verdict = validation_verdict(spread_summary, rules)
    grade = evidence_grade(spread_summary, rules)

    write_csv_with_headers(detail, DETAIL_OUT, DETAIL_COLUMNS)
    write_csv_with_headers(bucket_perf, BUCKET_PERF_OUT, BUCKET_PERF_COLUMNS)
    write_csv_with_headers(spread_by_date, SPREAD_DATES_OUT, SPREAD_BY_DATE_COLUMNS)
    write_csv_with_headers(spread_summary, SPREAD_SUMMARY_OUT, SPREAD_SUMMARY_COLUMNS)
    write_report(bucket_perf, spread_by_date, spread_summary, verdict, grade, missing)
    write_validated_workbook(detail, bucket_perf, spread_by_date, spread_summary, verdict, grade, missing)

    _write_validation_status(spread_summary, verdict, grade, rules)

    print(f"Saved: {DETAIL_OUT}")
    print(f"Saved: {BUCKET_PERF_OUT}")
    print(f"Saved: {SPREAD_DATES_OUT}")
    print(f"Saved: {SPREAD_SUMMARY_OUT}")
    print(f"Saved: {REPORT_OUT}")
    print(f"Saved: {VALIDATED_XLSX}")
    print(f"Verdict: {verdict}")
    print(f"Evidence grade: {grade}")


if __name__ == "__main__":
    main()
