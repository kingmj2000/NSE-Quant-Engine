"""
Trade Plan Builder - Stage 3.4.1 Validation Sync Patch
======================================================

Creates practical entry / stop / target / hold-duration levels from the latest
NSE Quant Engine output.

Patch fixes:
    1. Strictly reads the actual validation verdict from
       cross_sectional_validation_report.md.
    2. Does NOT accidentally read "Validation Positive" from interpretation text.
    3. Decision_Use is WATCHLIST_ONLY unless the actual verdict is exactly
       Validation Positive.
    4. Always includes Model_Edge_%_Per_Day and Model_Edge_Per_Day_Pct columns.
    5. Adds Validation_Source and Expected_Data_State fields for AI review.

Reads:
    output/latest_scores.csv
    output/cross_sectional_validation_report.md
    output/cross_sectional_spread_summary.csv
    scoring_rules.csv

Writes:
    output/trade_plan_latest.csv
    output/trade_plan_latest.xlsx
    output/trade_plan_report.md

Run after cross_sectional_validation.py:
    python trade_plan_builder.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict
import re
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
SCORING_RULES_CSV = BASE_DIR / "scoring_rules.csv"

LATEST_SCORES = OUTPUT_DIR / "latest_scores.csv"
VALIDATION_REPORT = OUTPUT_DIR / "cross_sectional_validation_report.md"
SPREAD_SUMMARY = OUTPUT_DIR / "cross_sectional_spread_summary.csv"

TRADE_PLAN_CSV = OUTPUT_DIR / "trade_plan_latest.csv"
TRADE_PLAN_XLSX = OUTPUT_DIR / "trade_plan_latest.xlsx"
TRADE_PLAN_MD = OUTPUT_DIR / "trade_plan_report.md"
TOP5_CORR_CSV = OUTPUT_DIR / "top5_corr_matrix.csv"
TOP5_BENCH_CSV = OUTPUT_DIR / "top5_benchmark_stats.csv"
TOP5_HORIZON_CSV = OUTPUT_DIR / "top5_horizon.csv"
TOP5_SENT_CSV = OUTPUT_DIR / "top5_sentiment.csv"
MACRO_CTX_JSON = OUTPUT_DIR / "macro_context.json"
ALPHA_IC_CSV = OUTPUT_DIR / "alpha_zoo_ic_report.csv"
ALPHA_SURVIVORS_JSON = OUTPUT_DIR / "alpha_zoo_survivors.json"
NEWS_LATEST_CSV = OUTPUT_DIR / "news_market_latest.csv"
RAW_PRICES = BASE_DIR / "data" / "raw_prices_latest.csv"
TOP5_FUND_CSV = OUTPUT_DIR / "top5_fundamentals.csv"
TOP5_SIZING_CSV = OUTPUT_DIR / "top5_position_sizing.csv"
BACKTEST_CSV = OUTPUT_DIR / "backtest_scorecard.csv"
BACKTEST_CURVE_CSV = OUTPUT_DIR / "backtest_equity_curve.csv"
FUND_CACHE_CSV = OUTPUT_DIR / "fundamentals_cache.csv"
TOP5_SECTOR_CSV = OUTPUT_DIR / "top5_sector_context.csv"
TOP5_EVENTS_CSV = OUTPUT_DIR / "top5_events.csv"
TOP5_EV_CSV = OUTPUT_DIR / "top5_expected_value.csv"
PORTFOLIO_VAL_JSON = OUTPUT_DIR / "portfolio_validation.json"
PROMPTS_DIR = BASE_DIR / "prompts"

DEFAULT_RULES = {
    "Round_Trip_Cost": 0.0030,
    "Stock_Round_Trip_Cost": 0.0035,
    "ETF_Round_Trip_Cost": 0.0025,
    "ETF_MidLiquidity_Round_Trip_Cost": 0.0050,
    "ETF_LowLiquidity_Round_Trip_Cost": 0.0100,
    "ETF_MidLiquidity_Value": 100000000.0,
    "ETF_LowLiquidity_Value": 20000000.0,
    "CrossVal_Horizon": 10,
}

VERDICTS = [
    "Validation Positive",
    "Validation Negative",
    "No Proven Edge Yet",
    "Insufficient Independent History",
    "Insufficient Statistical Evidence",
    "Insufficient Breadth",
    "Insufficient History",
]

OUTPUT_COLUMNS = [
    "Plan_Date",
    "Validation_Verdict",
    "Evidence_Grade",
    "Validation_Source",
    "Expected_Data_State",
    "Decision_Use",
    "Plan_Label",
    "Trade_Status",
    "Rank",
    "Opportunity_Rank",
    "Universe",
    "Universe_Group",
    "Opportunity_Type",
    "Symbol",
    "Raw_Symbol",
    "Name",
    "Category",
    "Bucket",
    "Final_Score",
    "Confidence_Adjusted_Score",
    "Confidence_Score",
    "Opportunity_Score",
    "Risk_Score",
    "Liquidity_Score",
    "ETF_Quality_Score",
    "Absolute_Momentum_Pass",
    "Price",
    "ATR_14",
    "RSI_14",
    "Current_Drawdown_60D",
    "Volatility_20D",
    "Avg_Traded_Value_20D",
    "Buy_Zone_Low",
    "Buy_Zone_High",
    "Reference_Entry",
    "Stop_Loss",
    "Target_1",
    "Target_2",
    "Stop_Loss_%",
    "Gross_Target_1_%",
    "Gross_Target_2_%",
    "Round_Trip_Cost_%",
    "Net_Target_1_%",
    "Net_Target_2_%",
    "Risk_Reward_Target_1",
    "Risk_Reward_Target_2",
    "Hold_Days_Min",
    "Hold_Days_Max",
    "Net_Target_1_%_Per_Day_MinHold",
    "Net_Target_1_%_Per_Day_MaxHold",
    "Net_Target_2_%_Per_Day_MinHold",
    "Net_Target_2_%_Per_Day_MaxHold",
    "Model_Edge_%_Per_Day",
    "Model_Edge_Per_Day_Pct",
    "Entry_Note",
    "Reason",
    "Key_Risk",
    "Risk_Flag",
]


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


def extract_validation_section(text: str) -> str:
    if not text:
        return ""

    match = re.search(
        r"##\s*Validation Verdict\s*(.*?)(?:\n##\s+|\Z)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1)

    # fallback: only inspect beginning of file, not interpretation rules
    return text[:1200]


def strict_extract_verdict(text: str) -> str:
    section = extract_validation_section(text)

    # In our report, the first bold verdict line is authoritative.
    bolds = re.findall(r"\*\*(.*?)\*\*", section)
    for raw in bolds:
        value = raw.strip()
        for verdict in VERDICTS:
            if value.lower() == verdict.lower():
                return verdict

    # Fallback: scan only the validation section, never the whole document.
    for verdict in VERDICTS:
        if re.search(rf"\b{re.escape(verdict)}\b", section[:1000], flags=re.IGNORECASE):
            return verdict

    return "Insufficient History"


def strict_extract_grade(text: str) -> str:
    section = extract_validation_section(text)

    match = re.search(r"Evidence grade:\s*\*\*(.*?)\*\*", section, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()

    match = re.search(r"Evidence\s+Grade\s*:\s*([A-Za-z /-]+)", section, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return "Insufficient Evidence"


def parse_validation_report() -> tuple[str, str, str]:
    # PREFER structured validation_status.json (no markdown scraping).
    try:
        from core import validation_status as _vs
        status_path = OUTPUT_DIR / "validation_status.json"
        if status_path.exists():
            data = _vs.read_status(status_path)
            return data.get("verdict", "Insufficient History"), data.get("evidence_grade", "Insufficient Evidence"), "validation_status.json"
    except Exception as _e:
        print(f"validation_status.json read skipped: {_e}")

    if not VALIDATION_REPORT.exists():
        return "Insufficient History", "Insufficient Evidence", "cross_sectional_validation_report.md missing"

    text = VALIDATION_REPORT.read_text(encoding="utf-8", errors="ignore")
    verdict = strict_extract_verdict(text)
    grade = strict_extract_grade(text)

    return verdict, grade, "cross_sectional_validation_report.md"


def expected_data_state(verdict: str) -> str:
    if verdict == "Insufficient History":
        return "Expected early-run state: forward-return windows have not matured yet"
    if verdict == "Insufficient Independent History":
        return "Expected early-run state: not enough independent matured signal dates"
    if verdict == "Insufficient Statistical Evidence":
        return "Validation has some data but not enough statistical evidence"
    if verdict == "Insufficient Breadth":
        return "Validation has too few instruments per matured date"
    if verdict == "No Proven Edge Yet":
        return "Validation matured but no proven edge after costs"
    if verdict == "Validation Negative":
        return "Validation matured and score relationship appears negative"
    if verdict == "Validation Positive":
        return "Validation matured and score relationship passed evidence gates"
    return "Unknown validation state"


def load_model_edge_per_day(rules: Dict[str, float], verdict: str) -> float:
    """
    Returns model-level historical top-minus-bottom edge per day in percent.
    Only populated when validation is positive. Otherwise NaN.
    """
    if verdict != "Validation Positive":
        return np.nan

    if not SPREAD_SUMMARY.exists():
        return np.nan

    try:
        df = pd.read_csv(SPREAD_SUMMARY)
    except Exception:
        return np.nan

    if df.empty or "Horizon_Days" not in df.columns or "Avg_TopMinusBottom_Quintile" not in df.columns:
        return np.nan

    horizon = int(rules.get("CrossVal_Horizon", 10))
    row = df[pd.to_numeric(df["Horizon_Days"], errors="coerce").eq(horizon)]
    if row.empty:
        return np.nan

    spread = pd.to_numeric(row.iloc[0]["Avg_TopMinusBottom_Quintile"], errors="coerce")
    if pd.isna(spread):
        return np.nan

    return (float(spread) / horizon) * 100.0


def cost_for_row(row: pd.Series, rules: Dict[str, float]) -> tuple[float, str]:
    universe = str(row.get("Universe", "")).lower()

    if universe == "etf":
        base = rules.get("ETF_Round_Trip_Cost", rules["Round_Trip_Cost"])
        traded_value = pd.to_numeric(row.get("Avg_Traded_Value_20D", np.nan), errors="coerce")

        if pd.isna(traded_value):
            return max(base, rules.get("ETF_MidLiquidity_Round_Trip_Cost", 0.0050)), "ETF cost: missing traded value"

        if traded_value < rules.get("ETF_LowLiquidity_Value", 20000000.0):
            return max(base, rules.get("ETF_LowLiquidity_Round_Trip_Cost", 0.0100)), "ETF low-liquidity cost"

        if traded_value < rules.get("ETF_MidLiquidity_Value", 100000000.0):
            return max(base, rules.get("ETF_MidLiquidity_Round_Trip_Cost", 0.0050)), "ETF mid-liquidity cost"

        return base, "ETF high-liquidity cost"

    if universe == "stock":
        return rules.get("Stock_Round_Trip_Cost", rules["Round_Trip_Cost"]), "Stock cost"

    return rules["Round_Trip_Cost"], "Default cost"


def parse_hold_days(value: str, momentum_score: float) -> tuple[int, int]:
    text = str(value)
    nums = re.findall(r"\d+", text)
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    if len(nums) == 1:
        n = int(nums[0])
        return n, n
    if pd.notna(momentum_score) and momentum_score >= 80:
        return 5, 15
    return 10, 30


def atr_value(row: pd.Series) -> tuple[float, str]:
    price = pd.to_numeric(row.get("Price", np.nan), errors="coerce")
    atr = pd.to_numeric(row.get("ATR_14", np.nan), errors="coerce")

    if pd.notna(price) and pd.notna(atr) and atr > 0:
        return float(atr), "ATR-based"

    vol = pd.to_numeric(row.get("Volatility_20D", np.nan), errors="coerce")
    if pd.notna(price) and pd.notna(vol) and vol > 0:
        daily_vol = float(vol) / np.sqrt(252)
        fallback_atr = price * max(0.015, min(daily_vol, 0.05))
        return float(fallback_atr), "ATR missing; volatility fallback"

    if pd.notna(price):
        return float(price) * 0.02, "ATR missing; 2 pct price fallback"

    return np.nan, "No price/ATR available"


def build_buy_zone(price: float, atr: float, rsi: float, amp: bool) -> tuple[float, float, str]:
    if pd.isna(price) or pd.isna(atr) or atr <= 0:
        return np.nan, np.nan, "No buy zone; missing price/ATR"

    if not amp:
        low = price - 1.00 * atr
        high = price - 0.50 * atr
        return max(0, low), max(0, high), "Failed momentum; only monitor deep pullback"

    if pd.notna(rsi) and rsi >= 75:
        low = price - 1.00 * atr
        high = price - 0.50 * atr
        return max(0, low), max(0, high), "Overbought; wait for deeper pullback"

    if pd.notna(rsi) and rsi >= 70:
        low = price - 0.75 * atr
        high = price - 0.25 * atr
        return max(0, low), max(0, high), "RSI elevated; pullback entry preferred"

    low = price - 0.25 * atr
    high = price + 0.10 * atr
    return max(0, low), max(0, high), "Near-current entry zone"


def truthy(value) -> bool:
    return str(value).strip().lower() in ["true", "1", "yes", "y"]


def trade_status(row: pd.Series, verdict: str) -> str:
    eligible = str(row.get("Opportunity_Eligible", "Yes")).lower() == "yes"
    amp = truthy(row.get("Absolute_Momentum_Pass", False))
    risk_flag = str(row.get("Risk_Flag", "")).strip()
    key_risk = str(row.get("Key_Risk", "")).strip()
    rsi = pd.to_numeric(row.get("RSI_14", np.nan), errors="coerce")

    if not eligible:
        return "Avoid for now - not opportunity eligible"
    if not amp:
        return "Avoid for now - failed absolute momentum"
    if pd.notna(rsi) and rsi >= 75:
        return "Watch only - overbought"

    risk_text = f"{risk_flag} {key_risk}".lower()
    serious_risks = ["high drawdown", "high volatility", "illiquid", "low liquidity", "nav premium", "quality data incomplete", "elevated volatility"]

    if any(x in risk_text for x in serious_risks):
        return "Watch only - risk flag present"

    if verdict != "Validation Positive":
        return "Watch only - validation not positive"

    return "Review - evidence gated"


def make_trade_plan(latest: pd.DataFrame, rules: Dict[str, float], verdict: str, grade: str, validation_source: str) -> pd.DataFrame:
    df = latest.copy()
    plan_date = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    model_edge = load_model_edge_per_day(rules, verdict)
    state = expected_data_state(verdict)

    numeric_cols = [
        "Price", "ATR_14", "RSI_14", "Current_Drawdown_60D", "Volatility_20D",
        "Avg_Traded_Value_20D", "Momentum_Score", "Final_Score", "Confidence_Adjusted_Score",
        "Confidence_Score", "Opportunity_Score", "Risk_Score", "Liquidity_Score",
        "ETF_Quality_Score", "Rank", "Opportunity_Rank"
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    rows = []

    for _, row in df.iterrows():
        price = pd.to_numeric(row.get("Price", np.nan), errors="coerce")
        if pd.isna(price) or price <= 0:
            continue

        atr, atr_note = atr_value(row)
        rsi = pd.to_numeric(row.get("RSI_14", np.nan), errors="coerce")
        amp = truthy(row.get("Absolute_Momentum_Pass", False))
        buy_low, buy_high, entry_note = build_buy_zone(price, atr, rsi, amp)

        reference_entry = (buy_low + buy_high) / 2 if pd.notna(buy_low) and pd.notna(buy_high) else price

        stop = reference_entry - 1.5 * atr if pd.notna(atr) else np.nan
        target_1 = reference_entry + 1.5 * atr if pd.notna(atr) else np.nan
        target_2 = reference_entry + 2.5 * atr if pd.notna(atr) else np.nan
        stop = max(0, stop) if pd.notna(stop) else np.nan

        gross_t1 = (target_1 / reference_entry - 1) * 100 if reference_entry and pd.notna(target_1) else np.nan
        gross_t2 = (target_2 / reference_entry - 1) * 100 if reference_entry and pd.notna(target_2) else np.nan
        stop_pct = (stop / reference_entry - 1) * 100 if reference_entry and pd.notna(stop) else np.nan

        cost, cost_note = cost_for_row(row, rules)
        cost_pct = cost * 100
        net_t1 = gross_t1 - cost_pct if pd.notna(gross_t1) else np.nan
        net_t2 = gross_t2 - cost_pct if pd.notna(gross_t2) else np.nan

        risk_amount = reference_entry - stop if pd.notna(stop) else np.nan
        rr1 = (target_1 - reference_entry) / risk_amount if pd.notna(risk_amount) and risk_amount > 0 else np.nan
        rr2 = (target_2 - reference_entry) / risk_amount if pd.notna(risk_amount) and risk_amount > 0 else np.nan

        hold_min, hold_max = parse_hold_days(row.get("Suggested_Hold_Days", ""), row.get("Momentum_Score", np.nan))

        def per_day(value: float, days: int) -> float:
            if pd.isna(value) or not days:
                return np.nan
            return value / days

        if verdict == "Validation Positive":
            decision_use = "REVIEW_WITH_RISK_CONTROLS"
            plan_label = "Evidence-gated mechanical plan"
        else:
            decision_use = "WATCHLIST_ONLY"
            plan_label = "Mechanical reference levels - validation not positive"

        rows.append({
            "Plan_Date": plan_date,
            "Validation_Verdict": verdict,
            "Evidence_Grade": grade,
            "Validation_Source": validation_source,
            "Expected_Data_State": state,
            "Decision_Use": decision_use,
            "Plan_Label": plan_label,
            "Trade_Status": trade_status(row, verdict),
            "Rank": row.get("Rank", np.nan),
            "Opportunity_Rank": row.get("Opportunity_Rank", np.nan),
            "Universe": row.get("Universe", ""),
            "Universe_Group": row.get("Universe_Group", ""),
            "Opportunity_Type": row.get("Opportunity_Type", ""),
            "Symbol": row.get("Symbol", ""),
            "Raw_Symbol": row.get("Raw_Symbol", ""),
            "Name": row.get("Name", ""),
            "Category": row.get("Category", ""),
            "Bucket": row.get("Bucket", ""),
            "Final_Score": row.get("Final_Score", np.nan),
            "Confidence_Adjusted_Score": row.get("Confidence_Adjusted_Score", np.nan),
            "Confidence_Score": row.get("Confidence_Score", np.nan),
            "Opportunity_Score": row.get("Opportunity_Score", np.nan),
            "Risk_Score": row.get("Risk_Score", np.nan),
            "Liquidity_Score": row.get("Liquidity_Score", np.nan),
            "ETF_Quality_Score": row.get("ETF_Quality_Score", np.nan),
            "Absolute_Momentum_Pass": amp,
            "Price": price,
            "ATR_14": atr,
            "RSI_14": rsi,
            "Current_Drawdown_60D": row.get("Current_Drawdown_60D", np.nan),
            "Volatility_20D": row.get("Volatility_20D", np.nan),
            "Avg_Traded_Value_20D": row.get("Avg_Traded_Value_20D", np.nan),
            "Buy_Zone_Low": buy_low,
            "Buy_Zone_High": buy_high,
            "Reference_Entry": reference_entry,
            "Stop_Loss": stop,
            "Target_1": target_1,
            "Target_2": target_2,
            "Stop_Loss_%": stop_pct,
            "Gross_Target_1_%": gross_t1,
            "Gross_Target_2_%": gross_t2,
            "Round_Trip_Cost_%": cost_pct,
            "Net_Target_1_%": net_t1,
            "Net_Target_2_%": net_t2,
            "Risk_Reward_Target_1": rr1,
            "Risk_Reward_Target_2": rr2,
            "Hold_Days_Min": hold_min,
            "Hold_Days_Max": hold_max,
            "Net_Target_1_%_Per_Day_MinHold": per_day(net_t1, hold_min),
            "Net_Target_1_%_Per_Day_MaxHold": per_day(net_t1, hold_max),
            "Net_Target_2_%_Per_Day_MinHold": per_day(net_t2, hold_min),
            "Net_Target_2_%_Per_Day_MaxHold": per_day(net_t2, hold_max),
            "Model_Edge_%_Per_Day": model_edge,
            "Model_Edge_Per_Day_Pct": model_edge,
            "Entry_Note": f"{entry_note}; {atr_note}; {cost_note}",
            "Reason": row.get("Reason", ""),
            "Key_Risk": row.get("Key_Risk", ""),
            "Risk_Flag": row.get("Risk_Flag", ""),
        })

    out = pd.DataFrame(rows)
    for col in OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan

    out = out[OUTPUT_COLUMNS].copy()

    sort_cols = [c for c in ["Confidence_Adjusted_Score", "Final_Score"] if c in out.columns]
    if sort_cols:
        out = out.sort_values(sort_cols, ascending=False)

    return out


def md_table(df: pd.DataFrame, cols: list[str], n: int = 10) -> str:
    if df.empty:
        return "_No rows._"
    temp = df[cols].head(n).copy()
    for col in temp.columns:
        if pd.api.types.is_numeric_dtype(temp[col]):
            temp[col] = temp[col].round(4)
    return temp.to_markdown(index=False)


def write_outputs(plan: pd.DataFrame, verdict: str, grade: str) -> None:
    plan.to_csv(TRADE_PLAN_CSV, index=False)

    top_review = plan[
        ~plan["Trade_Status"].astype(str).str.contains("Avoid", case=False, na=False)
    ].sort_values(["Confidence_Adjusted_Score", "Final_Score"], ascending=False).head(25)

    avoid_wait = plan[
        plan["Trade_Status"].astype(str).str.contains("Avoid|Watch only", case=False, na=False)
    ].sort_values(["Confidence_Adjusted_Score", "Final_Score"], ascending=False).head(100)

    with pd.ExcelWriter(TRADE_PLAN_XLSX, engine="openpyxl") as writer:
        plan.to_excel(writer, sheet_name="All Trade Plans", index=False)
        top_review.to_excel(writer, sheet_name="Top Review Plans", index=False)
        avoid_wait.to_excel(writer, sheet_name="Avoid or Wait", index=False)
        pd.DataFrame([{
            "Validation_Verdict": verdict,
            "Evidence_Grade": grade,
            "Decision_Use": "REVIEW_WITH_RISK_CONTROLS" if verdict == "Validation Positive" else "WATCHLIST_ONLY",
            "Model_Edge_Filled": verdict == "Validation Positive",
        }]).to_excel(writer, sheet_name="Validation", index=False)

    cols = [
        "Trade_Status", "Symbol", "Name", "Price", "Buy_Zone_Low", "Buy_Zone_High",
        "Stop_Loss", "Target_1", "Target_2", "Net_Target_1_%",
        "Net_Target_2_%", "Hold_Days_Min", "Hold_Days_Max",
        "Net_Target_1_%_Per_Day_MaxHold", "Net_Target_2_%_Per_Day_MaxHold",
        "Model_Edge_%_Per_Day", "Entry_Note", "Key_Risk"
    ]

    lines = []
    lines.append("# Trade Plan Report")
    lines.append("")
    lines.append(f"Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append(f"Validation verdict: **{verdict}**")
    lines.append("")
    lines.append(f"Evidence grade: **{grade}**")
    lines.append("")
    lines.append("These are mechanical reference levels. They are not guaranteed profit predictions.")
    lines.append("")

    if verdict == "Validation Positive":
        lines.append("Current use: **REVIEW WITH RISK CONTROLS** because validation is positive.")
    else:
        lines.append("Current use: **WATCHLIST ONLY** because validation is not positive.")
    lines.append("")

    lines.append("## Top Review Plans")
    lines.append(md_table(top_review, cols, n=10))
    lines.append("")
    lines.append("## Avoid / Wait")
    lines.append(md_table(avoid_wait, cols, n=10))
    lines.append("")
    lines.append("## Column meaning")
    lines.append("- Buy_Zone_Low / Buy_Zone_High: mechanical entry range based on ATR and RSI.")
    lines.append("- Target_1 / Target_2: ATR-based reference targets.")
    lines.append("- Net_Target_%: target return after estimated round-trip transaction cost.")
    lines.append("- Net_Target_%_Per_Day_MaxHold: conservative target-return-per-day using the longer hold duration.")
    lines.append("- Model_Edge_%_Per_Day: only filled when validation is positive; this is model-level historical edge, not candidate-specific certainty.")
    lines.append("- If validation is not positive, all trade levels are watchlist-only reference levels.")

    TRADE_PLAN_MD.write_text("\n".join(lines), encoding="utf-8")


def _emit_corr_and_benchmark(plan: pd.DataFrame) -> None:
    """Step-2 add-on: reorder top-5 by diversification + emit corr matrix and
    per-candidate benchmark stats. Guarded — any failure is silent so the
    core pipeline can't regress on this feature."""
    try:
        from core import config as C
        from core import portfolio_selection as psel
    except Exception:
        return
    if plan is None or plan.empty or not RAW_PRICES.exists():
        return
    try:
        prices = pd.read_csv(RAW_PRICES)
    except Exception:
        return

    try:
        reviewable = plan[
            ~plan["Trade_Status"].astype(str).str.contains("Avoid", case=False, na=False)
        ].copy()
        if reviewable.empty:
            return
        sort_cols = [c for c in ["Confidence_Adjusted_Score", "Final_Score"] if c in reviewable.columns]
        if sort_cols:
            reviewable = reviewable.sort_values(sort_cols, ascending=False)
        pool_n = int(getattr(C, "CORR_AWARE_POOL_N", 25))
        pool = reviewable.head(pool_n)
        pool_syms = pool["Symbol"].astype(str).tolist()

        corr = psel.pairwise_corr(prices, pool_syms,
                                  window=int(getattr(C, "CORR_WINDOW_DAYS", 60)))
        if not corr.empty:
            if getattr(C, "CORR_AWARE_TOP5", True):
                top5 = psel.diversified_top_n(
                    pool, corr, n=5,
                    alpha=float(getattr(C, "CORR_AWARE_ALPHA", 0.65)),
                    score_col="Final_Score" if "Final_Score" in pool.columns else "Confidence_Adjusted_Score",
                )
            else:
                top5 = pool_syms[:5]
            top5 = [s for s in top5 if s in corr.index][:5]
            sub_corr = corr.loc[top5, top5] if top5 else corr
            sub_corr.to_csv(TOP5_CORR_CSV, index=True)

            bench = psel.benchmark_stats(prices, top5)
            bench.to_csv(TOP5_BENCH_CSV, index=False)
            print(f"Saved: {TOP5_CORR_CSV.name} (avg|corr|={psel.avg_abs_offdiag(sub_corr):.2f})")
            print(f"Saved: {TOP5_BENCH_CSV.name}")
    except Exception as e:
        print(f"[step2] corr/benchmark stage skipped: {e}")


def _emit_horizon_sentiment_alpha(plan: pd.DataFrame) -> None:
    """Steps 3-5 add-on: horizon optimiser, sentiment overlay + veto, and
    alpha-zoo IC report. All fully guarded — failures are logged and skipped;
    the base pipeline never regresses."""
    try:
        from core import config as C
    except Exception:
        return
    if plan is None or plan.empty or not RAW_PRICES.exists():
        return
    try:
        prices = pd.read_csv(RAW_PRICES)
    except Exception:
        return

    # top-5 symbols (post-veto, already correlation-diversified when step 2 ran)
    try:
        reviewable = plan[
            ~plan["Trade_Status"].astype(str).str.contains("Avoid", case=False, na=False)
        ].copy()
        sort_cols = [c for c in ["Confidence_Adjusted_Score", "Final_Score"] if c in reviewable.columns]
        if sort_cols:
            reviewable = reviewable.sort_values(sort_cols, ascending=False)
        top5_syms = reviewable["Symbol"].astype(str).head(5).tolist()
    except Exception:
        top5_syms = []

    # ── Step 3: Hold-Horizon Optimizer ──
    if getattr(C, "HORIZON_OPTIMIZER_ON", True) and top5_syms:
        try:
            from core import horizon_optimizer as hopt
            recs = hopt.optimise_batch(
                prices, top5_syms,
                horizons=getattr(C, "HORIZON_GRID", [3, 5, 10, 21, 42, 63]),
                hist_days=int(getattr(C, "HORIZON_HIST_DAYS", 250)),
                risk_cap_pct=float(getattr(C, "HORIZON_RISK_CAP_PCT", 6.0)),
            )
            # store the curve as a JSON string for CSV portability
            if not recs.empty:
                recs["Exp_Ret_Curve"] = recs["Exp_Ret_Curve"].apply(lambda x: str(x))
                recs["Horizons"] = recs["Horizons"].apply(lambda x: str(x))
                recs.to_csv(TOP5_HORIZON_CSV, index=False)
                print(f"Saved: {TOP5_HORIZON_CSV.name}")
        except Exception as e:
            print(f"[step3] horizon optimizer skipped: {e}")

    # ── Step 4: Sentiment + macro overlay ──
    if getattr(C, "SENTIMENT_OVERLAY_ON", True):
        try:
            from core import sentiment_overlay as sent
            macro = sent.macro_tape_score(prices)
            import json as _j
            MACRO_CTX_JSON.write_text(_j.dumps(macro, default=str, indent=2),
                                       encoding="utf-8")
            print(f"Saved: {MACRO_CTX_JSON.name} (regime={macro.get('regime')})")

            if NEWS_LATEST_CSV.exists() and top5_syms:
                try:
                    news = pd.read_csv(NEWS_LATEST_CSV)
                except Exception:
                    news = pd.DataFrame()
                s_df = sent.score_headlines(
                    news[news.get("Symbol", pd.Series(dtype=str)).astype(str).isin(top5_syms)]
                    if not news.empty else news,
                    lookback_days=int(getattr(C, "SENT_LOOKBACK_DAYS", 7)),
                )
                if not s_df.empty:
                    s_df.to_csv(TOP5_SENT_CSV, index=False)
                    print(f"Saved: {TOP5_SENT_CSV.name}")
                    if getattr(C, "SENTIMENT_VETO_ON", True):
                        vetoed = sent.sentiment_veto(
                            s_df,
                            min_headlines=int(getattr(C, "SENT_MIN_HEADLINES", 3)),
                            neg_pct_veto=float(getattr(C, "SENT_NEG_VETO_PCT", 0.60)),
                        )
                        if vetoed:
                            print(f"[step4] sentiment veto would demote: {sorted(vetoed)}")
        except Exception as e:
            print(f"[step4] sentiment overlay skipped: {e}")

    # ── Step 5: Alpha-Zoo evaluator (report only — tilt gated on survivors) ──
    if getattr(C, "ALPHA_ZOO_ON", True):
        try:
            from core import alpha_evaluator as ae
            ic = ae.evaluate_alphas(
                prices,
                horizons=(5, 10, 21),
                eval_days=int(getattr(C, "ALPHA_EVAL_DAYS", 250)),
                folds=int(getattr(C, "ALPHA_EVAL_FOLDS", 4)),
            )
            if not ic.empty:
                ic.to_csv(ALPHA_IC_CSV, index=False)
                survivors = ae.promote_alphas(
                    ic,
                    min_ic=float(getattr(C, "ALPHA_IC_MIN", 0.03)),
                    min_tstat=float(getattr(C, "ALPHA_TSTAT_MIN", 2.0)),
                )
                import json as _j
                ALPHA_SURVIVORS_JSON.write_text(
                    _j.dumps({"survivors": survivors,
                              "threshold_ic": float(getattr(C, "ALPHA_IC_MIN", 0.03)),
                              "threshold_tstat": float(getattr(C, "ALPHA_TSTAT_MIN", 2.0)),
                              "min_for_tilt": int(getattr(C, "ALPHA_MIN_SURVIVORS_FOR_TILT", 3))},
                             default=str, indent=2),
                    encoding="utf-8")
                print(f"Saved: {ALPHA_IC_CSV.name} + {ALPHA_SURVIVORS_JSON.name} "
                      f"({len(survivors)} survivors)")
        except Exception as e:
            print(f"[step5] alpha-zoo evaluator skipped: {e}")


def _emit_fundamentals_sizing_backtest_bundle(plan: pd.DataFrame) -> None:
    """Steps 6, 8, 9, 7. All fully guarded — failures print and skip; the
    base pipeline never regresses. Order: fundamentals → sizing → backtest
    → evidence bundle (so the bundle can zip everything above)."""
    try:
        from core import config as C
    except Exception:
        return
    if plan is None or plan.empty:
        return

    # top-5 slice reused across steps
    try:
        reviewable = plan[
            ~plan["Trade_Status"].astype(str).str.contains("Avoid", case=False, na=False)
        ].copy()
        sort_cols = [c for c in ["Confidence_Adjusted_Score", "Final_Score"]
                     if c in reviewable.columns]
        if sort_cols:
            reviewable = reviewable.sort_values(sort_cols, ascending=False)
        top5 = reviewable.head(5).copy()
        top5_syms = top5["Symbol"].astype(str).tolist()
    except Exception:
        top5 = pd.DataFrame(); top5_syms = []

    prices = None
    if RAW_PRICES.exists():
        try:
            prices = pd.read_csv(RAW_PRICES)
        except Exception:
            prices = None

    # ── Step 6: Fundamentals & Quality overlay ──
    if getattr(C, "FUNDAMENTALS_OVERLAY_ON", True) and top5_syms:
        try:
            from core import fundamentals_overlay as fo
            fund_df = pd.DataFrame()
            if FUND_CACHE_CSV.exists():
                try:
                    fund_df = pd.read_csv(FUND_CACHE_CSV)
                except Exception:
                    fund_df = pd.DataFrame()
            # tolerate legacy column names from fundamental_factor.fetch_fundamentals
            if not fund_df.empty:
                rename = {"PE": "PE_TTM", "ROE": "ROE_TTM",
                          "EarningsGrowth": "EPS_Growth_YoY"}
                fund_df = fund_df.rename(columns={k: v for k, v in rename.items()
                                                  if k in fund_df.columns})
            enr = fo.enrich(top5[["Symbol"]], fund_df)
            enr.to_csv(TOP5_FUND_CSV, index=False)
            print(f"Saved: {TOP5_FUND_CSV.name} "
                  f"({int(enr['Quality_Score'].notna().sum())} scored)")
        except Exception as e:
            print(f"[step6] fundamentals overlay skipped: {e}")

    # ── Step 8: Position sizer ──
    if getattr(C, "POSITION_SIZER_ON", True) and not top5.empty:
        try:
            from core import position_sizer as ps
            corr = None
            if TOP5_CORR_CSV.exists():
                try:
                    corr = pd.read_csv(TOP5_CORR_CSV, index_col=0)
                except Exception:
                    corr = None
            sizing = ps.size_portfolio(
                top5[[c for c in ["Symbol", "Price", "Stop_Loss"] if c in top5.columns]],
                prices_long=prices,
                corr=corr,
                mode=str(getattr(C, "SIZING_MODE", "risk_parity_lite")),
                nav_inr=float(getattr(C, "PORTFOLIO_NAV_INR", 1_000_000.0)),
                vol_target=float(getattr(C, "PORTFOLIO_VOL_TARGET", 0.12)),
                max_weight=float(getattr(C, "MAX_WEIGHT", 0.30)),
                cash_buffer=float(getattr(C, "CASH_BUFFER", 0.10)),
            )
            if not sizing.empty:
                sizing.to_csv(TOP5_SIZING_CSV, index=False)
                print(f"Saved: {TOP5_SIZING_CSV.name} "
                      f"(sum weight={sizing['Weight_%'].sum():.1f}%)")
        except Exception as e:
            print(f"[step8] position sizer skipped: {e}")

    # ── Step 9: Walk-forward style backtest ──
    if getattr(C, "BACKTEST_ON", True) and prices is not None:
        try:
            # skip if scorecard was refreshed recently
            stale_days = int(getattr(C, "BACKTEST_STALE_DAYS", 7))
            fresh = False
            if BACKTEST_CSV.exists():
                age_days = (pd.Timestamp.now() -
                            pd.Timestamp.fromtimestamp(BACKTEST_CSV.stat().st_mtime)).days
                fresh = age_days < stale_days
            if not fresh:
                from core import backtest_engine as bt
                res = bt.run_backtest(
                    prices,
                    lookback_days=int(getattr(C, "BACKTEST_LOOKBACK_DAYS", 250)),
                    rebal_every=int(getattr(C, "BACKTEST_REBAL_EVERY", 5)),
                    hold_days=int(getattr(C, "BACKTEST_HOLD_DAYS", 10)),
                )
                sc = res.get("scorecard")
                cv = res.get("equity_curve")
                if sc is not None and not sc.empty:
                    sc.to_csv(BACKTEST_CSV, index=False)
                    print(f"Saved: {BACKTEST_CSV.name}")
                if cv is not None and not cv.empty:
                    cv.to_csv(BACKTEST_CURVE_CSV, index=False)
        except Exception as e:
            print(f"[step9] backtest skipped: {e}")

    # ── Step 10: Sector & Peer Context ──
    fund_df_cached = pd.DataFrame()
    if FUND_CACHE_CSV.exists():
        try:
            fund_df_cached = pd.read_csv(FUND_CACHE_CSV)
        except Exception:
            fund_df_cached = pd.DataFrame()
    if getattr(C, "SECTOR_CONTEXT_ON", True) and not top5.empty:
        try:
            from core import sector_context as sc
            sec = sc.enrich(top5[["Symbol"]], prices, fund_df_cached)
            if not sec.empty:
                sec.to_csv(TOP5_SECTOR_CSV, index=False)
                print(f"Saved: {TOP5_SECTOR_CSV.name}")
        except Exception as e:
            print(f"[step10] sector context skipped: {e}")

    # ── Step 11: Event & Catalyst Calendar ──
    if getattr(C, "EVENT_CALENDAR_ON", True) and not top5.empty:
        try:
            from core import event_calendar as ec
            hz_df = pd.read_csv(TOP5_HORIZON_CSV) if TOP5_HORIZON_CSV.exists() else None
            ev_df = ec.build(top5[["Symbol"]], hz_df, fund_df_cached)
            if not ev_df.empty:
                ev_df.to_csv(TOP5_EVENTS_CSV, index=False)
                n_in = int((ev_df["Event_Risk_Flag"] == "In_Window").sum())
                print(f"Saved: {TOP5_EVENTS_CSV.name} ({n_in} in-window)")
        except Exception as e:
            print(f"[step11] event calendar skipped: {e}")

    # ── Step 12: Expected-Value / Kelly-Lite cross-check (report-only) ──
    if getattr(C, "EV_REPORT_ON", True) and not top5.empty:
        try:
            from core import expected_value as ev
            bt_sc = pd.read_csv(BACKTEST_CSV) if BACKTEST_CSV.exists() else None
            hz_df = pd.read_csv(TOP5_HORIZON_CSV) if TOP5_HORIZON_CSV.exists() else None
            sz_df = pd.read_csv(TOP5_SIZING_CSV) if TOP5_SIZING_CSV.exists() else None
            evr = ev.top5_ev_report(
                top5[["Symbol"]], bt_sc, hz_df, sz_df,
                kelly_cap_of_weight=float(getattr(C, "KELLY_CAP_OF_WEIGHT", 0.25)),
            )
            if not evr.empty:
                evr.to_csv(TOP5_EV_CSV, index=False)
                print(f"Saved: {TOP5_EV_CSV.name}")
        except Exception as e:
            print(f"[step12] EV report skipped: {e}")

    # ── Step 13: Portfolio-Level Validation Gate ──
    if getattr(C, "PORTFOLIO_VALIDATION_ON", True):
        try:
            from core import portfolio_validation as pv
            report = pv.validate_batch(OUTPUT_DIR, thresholds={
                "max_avg_abs_corr":       float(getattr(C, "PV_MAX_AVG_ABS_CORR", 0.70)),
                "max_portfolio_loss_pct_nav": float(getattr(C, "PV_MAX_PORTFOLIO_LOSS_PCT", 3.0)),
                "max_single_sector_pct":  float(getattr(C, "PV_MAX_SINGLE_SECTOR_PCT", 60.0)),
                "min_backtest_hit_rate":  float(getattr(C, "PV_MIN_BACKTEST_HIT_RATE", 0.50)),
                "min_alpha_survivors":    int(getattr(C, "PV_MIN_ALPHA_SURVIVORS", 2)),
            })
            pv.write_report(OUTPUT_DIR, report)
            print(f"Saved: {PORTFOLIO_VAL_JSON.name} — Batch_Verdict={report['verdict']}")
            if report["reasons"]:
                print(f"[step13] reasons: {'; '.join(report['reasons'])}")
            if report["caveats"]:
                print(f"[step13] caveats: {'; '.join(report['caveats'])}")
        except Exception as e:
            print(f"[step13] portfolio validation skipped: {e}")


    if getattr(C, "EVIDENCE_BUNDLE_ON", True):
        try:
            from core import evidence_bundle as eb
            zpath = eb.build_bundle(
                OUTPUT_DIR, PROMPTS_DIR,
                bundle_max_mb=float(getattr(C, "BUNDLE_MAX_MB", 5.0)),
                keep_last_n=int(getattr(C, "BUNDLE_KEEP_LAST_N", 10)),
            )
            if zpath and zpath.exists():
                size_kb = zpath.stat().st_size / 1024.0
                print(f"Saved: {zpath.name} ({size_kb:.0f} KB)")
                print(f"[step7] Upload {zpath.name} to Claude with the "
                      f"included README_for_AI.md as the system prompt.")
        except Exception as e:
            print(f"[step7] evidence bundle skipped: {e}")


def main() -> None:
    print("Trade Plan Builder - Stage 3.4.1 Validation Sync Patch")
    print("======================================================")

    if not LATEST_SCORES.exists():
        raise FileNotFoundError("output/latest_scores.csv not found. Run nse_quant_engine.py first.")

    rules = load_rules()
    verdict, grade, source = parse_validation_report()

    latest = pd.read_csv(LATEST_SCORES)
    plan = make_trade_plan(latest, rules, verdict, grade, source)
    write_outputs(plan, verdict, grade)
    _emit_corr_and_benchmark(plan)
    _emit_horizon_sentiment_alpha(plan)
    _emit_fundamentals_sizing_backtest_bundle(plan)

    print(f"Saved: {TRADE_PLAN_CSV}")
    print(f"Saved: {TRADE_PLAN_XLSX}")
    print(f"Saved: {TRADE_PLAN_MD}")
    print(f"Validation verdict: {verdict}")
    print(f"Evidence grade: {grade}")

    if verdict != "Validation Positive":
        print("Use mode: WATCHLIST ONLY. Trade levels are mechanical reference levels, not validated action signals.")


if __name__ == "__main__":
    main()
