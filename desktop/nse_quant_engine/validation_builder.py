"""
Validation Builder - Stage 3.3 Hotfix
=====================================

Fixes first-run issue:
    If no forward-return horizon has matured yet, write forward_return_history.csv
    with headers instead of creating a zero-byte/columnless CSV.

Outputs:
    output/forward_return_history.csv
    output/per_symbol_forward_summary.csv
    output/forward_return_missing_signals.csv

Run after nse_quant_engine.py:
    python validation_builder.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"

SIGNAL_HISTORY = OUTPUT_DIR / "signal_history.csv"
RAW_PRICES = DATA_DIR / "raw_prices_latest.csv"
SCORING_RULES_CSV = BASE_DIR / "scoring_rules.csv"

FORWARD_OUT = OUTPUT_DIR / "forward_return_history.csv"
PER_SYMBOL_SUMMARY_OUT = OUTPUT_DIR / "per_symbol_forward_summary.csv"
MISSING_OUT = OUTPUT_DIR / "forward_return_missing_signals.csv"

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

MISSING_COLUMNS = [
    "Signal_Date",
    "Symbol",
    "Universe",
    "Name",
    "Signal_Final_Score",
    "Horizon_Days",
    "Reason",
]

SUMMARY_COLUMNS = [
    "Symbol",
    "Horizon_Days",
    "Obs",
    "Hit_Rate_Net",
    "Avg_Net_Return",
    "Median_Net_Return",
    "Worst_Net_Return",
    "Best_Net_Return",
    "Avg_Gross_Return",
    "Avg_Cost",
]

DEFAULT_RULES = {
    "Round_Trip_Cost": 0.0030,
    "Stock_Round_Trip_Cost": 0.0035,
    "ETF_Round_Trip_Cost": 0.0025,
    "ETF_MidLiquidity_Round_Trip_Cost": 0.0050,
    "ETF_LowLiquidity_Round_Trip_Cost": 0.0100,
    "ETF_MidLiquidity_Value": 100000000.0,
    "ETF_LowLiquidity_Value": 20000000.0,
}


def empty_df(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


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

    df = pd.read_csv(SCORING_RULES_CSV)
    if "Parameter" not in df.columns or "Value" not in df.columns:
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


def cost_for_signal(signal: pd.Series, rules: Dict[str, float]) -> tuple[float, str]:
    universe = str(signal.get("Universe", "")).lower()

    if universe == "etf":
        base = rules.get("ETF_Round_Trip_Cost", rules["Round_Trip_Cost"])
        traded_value = pd.to_numeric(signal.get("Avg_Traded_Value_20D", np.nan), errors="coerce")

        if pd.isna(traded_value):
            return max(base, rules.get("ETF_MidLiquidity_Round_Trip_Cost", 0.005)), "ETF cost: missing traded value"

        if traded_value < rules.get("ETF_LowLiquidity_Value", 20000000.0):
            return max(base, rules.get("ETF_LowLiquidity_Round_Trip_Cost", 0.010)), "ETF low-liquidity cost"

        if traded_value < rules.get("ETF_MidLiquidity_Value", 100000000.0):
            return max(base, rules.get("ETF_MidLiquidity_Round_Trip_Cost", 0.005)), "ETF mid-liquidity cost"

        return base, "ETF high-liquidity cost"

    if universe == "stock":
        return rules.get("Stock_Round_Trip_Cost", rules["Round_Trip_Cost"]), "Stock cost"

    return rules["Round_Trip_Cost"], "Default cost"


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not SIGNAL_HISTORY.exists():
        raise FileNotFoundError("output/signal_history.csv not found. Run nse_quant_engine.py first.")
    if not RAW_PRICES.exists():
        raise FileNotFoundError("data/raw_prices_latest.csv not found. Run nse_quant_engine.py first.")

    sig = pd.read_csv(SIGNAL_HISTORY)
    px = pd.read_csv(RAW_PRICES)

    for col in ["Date", "Symbol"]:
        if col not in sig.columns:
            raise ValueError(f"signal_history.csv missing required column: {col}")
        if col not in px.columns:
            raise ValueError(f"raw_prices_latest.csv missing required column: {col}")

    sig["Date"] = pd.to_datetime(sig["Date"], errors="coerce")
    px["Date"] = pd.to_datetime(px["Date"], errors="coerce")
    px["Price"] = pd.to_numeric(px["Price"], errors="coerce")

    sig = sig.dropna(subset=["Date", "Symbol"])
    px = px.dropna(subset=["Date", "Symbol", "Price"]).sort_values(["Symbol", "Date"])

    return sig, px


def lookup_forward_rows(sig: pd.DataFrame, px: pd.DataFrame, rules: Dict[str, float]) -> tuple[pd.DataFrame, pd.DataFrame]:
    horizons = [5, 10, 21]
    rows = []
    missing = []

    px_by_symbol = {sym: g.sort_values("Date").reset_index(drop=True) for sym, g in px.groupby("Symbol")}

    for _, signal in sig.iterrows():
        symbol = signal["Symbol"]

        base_missing = {
            "Signal_Date": signal["Date"].date(),
            "Symbol": symbol,
            "Universe": signal.get("Universe", ""),
            "Name": signal.get("Name", ""),
            "Signal_Final_Score": signal.get("Final_Score", np.nan),
        }

        if symbol not in px_by_symbol:
            item = base_missing.copy()
            item["Horizon_Days"] = ""
            item["Reason"] = "Symbol not found in current raw price file"
            missing.append(item)
            continue

        p = px_by_symbol[symbol]
        idxs = p.index[p["Date"] >= signal["Date"]].tolist()

        if not idxs:
            item = base_missing.copy()
            item["Horizon_Days"] = ""
            item["Reason"] = "No price found on or after signal date"
            missing.append(item)
            continue

        signal_idx = idxs[0]
        signal_date_actual = p.loc[signal_idx, "Date"]
        signal_price_current_basis = p.loc[signal_idx, "Price"]
        cost, cost_note = cost_for_signal(signal, rules)

        for h in horizons:
            fwd_idx = signal_idx + h

            if fwd_idx >= len(p):
                item = base_missing.copy()
                item["Horizon_Days"] = h
                item["Reason"] = "Forward horizon not matured yet"
                missing.append(item)
                continue

            forward_date = p.loc[fwd_idx, "Date"]
            forward_price = p.loc[fwd_idx, "Price"]
            gross_return = forward_price / signal_price_current_basis - 1
            net_return = gross_return - cost

            rows.append({
                "Signal_Date": signal["Date"].date(),
                "Signal_Date_Actual_In_Price_File": signal_date_actual.date(),
                "Symbol": symbol,
                "Universe": signal.get("Universe", ""),
                "Universe_Group": signal.get("Universe_Group", ""),
                "Opportunity_Type": signal.get("Opportunity_Type", ""),
                "Opportunity_Eligible": signal.get("Opportunity_Eligible", ""),
                "Name": signal.get("Name", ""),
                "Horizon_Days": h,
                "Signal_Price_CurrentBasis": signal_price_current_basis,
                "Forward_Date": forward_date.date(),
                "Forward_Price_CurrentBasis": forward_price,
                "Gross_Forward_Return": gross_return,
                "Round_Trip_Cost": cost,
                "Cost_Note": cost_note,
                "Net_Forward_Return": net_return,
                "Signal_Final_Score": signal.get("Final_Score", np.nan),
                "Signal_Bucket": signal.get("Bucket", ""),
            })

    fwd = pd.DataFrame(rows, columns=FORWARD_COLUMNS)
    if not fwd.empty:
        fwd = fwd.drop_duplicates(subset=["Signal_Date", "Symbol", "Horizon_Days"], keep="last")

    missing_df = pd.DataFrame(missing, columns=MISSING_COLUMNS)
    if not missing_df.empty:
        missing_df = missing_df.drop_duplicates(subset=["Signal_Date", "Symbol", "Horizon_Days", "Reason"], keep="last")

    return fwd, missing_df


def build_per_symbol_summary(fwd: pd.DataFrame) -> pd.DataFrame:
    if fwd.empty:
        return empty_df(SUMMARY_COLUMNS)

    work = fwd.copy()
    work["Net_Forward_Return"] = pd.to_numeric(work["Net_Forward_Return"], errors="coerce")
    work["Gross_Forward_Return"] = pd.to_numeric(work["Gross_Forward_Return"], errors="coerce")
    work["Round_Trip_Cost"] = pd.to_numeric(work["Round_Trip_Cost"], errors="coerce")
    work = work.dropna(subset=["Net_Forward_Return"])

    if work.empty:
        return empty_df(SUMMARY_COLUMNS)

    summary = (
        work.groupby(["Symbol", "Horizon_Days"])
        .agg(
            Obs=("Net_Forward_Return", "count"),
            Hit_Rate_Net=("Net_Forward_Return", lambda x: float((x > 0).mean())),
            Avg_Net_Return=("Net_Forward_Return", "mean"),
            Median_Net_Return=("Net_Forward_Return", "median"),
            Worst_Net_Return=("Net_Forward_Return", "min"),
            Best_Net_Return=("Net_Forward_Return", "max"),
            Avg_Gross_Return=("Gross_Forward_Return", "mean"),
            Avg_Cost=("Round_Trip_Cost", "mean"),
        )
        .reset_index()
        .sort_values(["Horizon_Days", "Avg_Net_Return"], ascending=[True, False])
    )

    for col in SUMMARY_COLUMNS:
        if col not in summary.columns:
            summary[col] = np.nan

    return summary[SUMMARY_COLUMNS]


def main() -> None:
    print("Validation Builder - Stage 3.3 Hotfix")
    print("=====================================")

    rules = load_rules()
    sig, px = load_inputs()

    print(f"Signal history rows: {len(sig)}")
    print(f"Raw price rows: {len(px)}")

    fwd, missing = lookup_forward_rows(sig, px, rules)

    write_csv_with_headers(fwd, FORWARD_OUT, FORWARD_COLUMNS)
    write_csv_with_headers(missing, MISSING_OUT, MISSING_COLUMNS)

    summary = build_per_symbol_summary(fwd)
    write_csv_with_headers(summary, PER_SYMBOL_SUMMARY_OUT, SUMMARY_COLUMNS)

    print(f"Saved: {FORWARD_OUT}")
    print(f"Saved: {PER_SYMBOL_SUMMARY_OUT}")
    print(f"Saved: {MISSING_OUT}")

    if fwd.empty:
        print("No forward returns yet. This is normal until enough days pass after first runs.")
    else:
        print("\nForward return rows by horizon:")
        print(fwd["Horizon_Days"].value_counts().sort_index().to_string())

    if not missing.empty:
        print("\nMissing/unmatured forward signal rows:")
        print(missing["Reason"].value_counts().to_string())


if __name__ == "__main__":
    main()
