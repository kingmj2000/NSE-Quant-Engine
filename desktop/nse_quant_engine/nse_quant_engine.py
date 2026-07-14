"""
NSE Quant Engine - Stage 3.5.10 Merged Metadata ETF Quality
============================

Computes the current universe score and appends score/signal history.

Important:
    This script computes the score. It does NOT prove the score works.
    Run validation_builder.py and cross_sectional_validation.py after this.

Run:
    python nse_quant_engine.py
"""

from __future__ import annotations

import math
import sys
import warnings
import logging
from pathlib import Path
from typing import List, Tuple, Dict

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

try:
    import yfinance as yf
except ImportError:
    print("Missing dependency: yfinance. Run: pip install yfinance")
    sys.exit(1)


BASE_DIR = Path(__file__).resolve().parent
CONFIG_CSV = BASE_DIR / "config.csv"
CONFIG_XLSX = BASE_DIR / "config.xlsx"
SCORING_RULES_CSV = BASE_DIR / "scoring_rules.csv"

DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ETF_QUALITY_CSV = DATA_DIR / "etf_quality_latest.csv"
PRICE_CACHE_CSV = DATA_DIR / "price_history_cache.csv"
DOWNLOAD_DIAGNOSTICS_CSV = DATA_DIR / "price_download_diagnostics.csv"

BENCHMARK_SYMBOL = "^NSEI"
PERIOD = "2y"
INTERVAL = "1d"

MIN_HISTORY_DAYS = 90
TOP_N = 25
DOWNLOAD_CHUNK_SIZE = 60
DOWNLOAD_RETRY_PERIODS = ["2y", "1y", "6mo"]

MIN_AVG_TRADED_VALUE_20D = 0
MAX_VOLATILITY_20D_STOCK = 0.60
MAX_VOLATILITY_20D_ETF = 0.70
MAX_DRAWDOWN_60D = -0.20
OVERBOUGHT_RSI = 75
OVERSOLD_RSI = 30

DEFAULT_RULES = {
    "Return_21D_Min": 0.02,
    "Return_21D_Absolute_Pass": 0.00,
    "Return_5D_Min": -0.02,
    "Risk_Score_Min": 70,
    "Final_Score_Min": 65,
    "Abs_NAV_Premium_Max": 0.02,
    "TER_Good": 0.005,
    "Tracking_Error_Good": 0.02,
    "AUM_Good_Cr": 100,
}

STOCK_WEIGHTS = {
    "opportunity": 0.50,
    "safety": 0.35,
    "consistency": 0.05,
    "market_regime": 0.10,
}

ETF_WEIGHTS = {
    "opportunity": 0.42,
    "safety": 0.25,
    "etf_quality": 0.18,
    "consistency": 0.05,
    "market_regime": 0.10,
}


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


def load_config() -> pd.DataFrame:
    if CONFIG_XLSX.exists():
        cfg = pd.read_excel(CONFIG_XLSX)
    elif CONFIG_CSV.exists():
        cfg = pd.read_csv(CONFIG_CSV)
    else:
        raise FileNotFoundError("No config.csv found. Run python universe_builder.py first.")

    required = {"Universe", "Symbol", "Name", "Category", "Include"}
    missing = required - set(cfg.columns)
    if missing:
        raise ValueError(f"Config missing required columns: {missing}")

    defaults = {
        "Universe_Group": "",
        "Raw_Symbol": "",
        "ISIN": "",
        "Source": "",
        "Opportunity_Type": "",
        "Opportunity_Eligible": "Yes",
    }
    for col, default in defaults.items():
        if col not in cfg.columns:
            cfg[col] = default

    cfg["Include"] = cfg["Include"].astype(str).str.strip().str.lower()
    cfg = cfg[cfg["Include"].isin(["yes", "y", "true", "1"])].copy()

    text_cols = [
        "Universe", "Universe_Group", "Opportunity_Type", "Opportunity_Eligible",
        "Symbol", "Raw_Symbol", "Name", "Category", "ISIN", "Source"
    ]
    for col in text_cols:
        cfg[col] = cfg[col].fillna("").astype(str).str.strip()

    cfg = cfg.drop_duplicates(subset=["Symbol"], keep="first")
    if cfg.empty:
        raise ValueError("No included symbols found in config.")
    return cfg


def load_etf_quality() -> pd.DataFrame:
    if not ETF_QUALITY_CSV.exists():
        print("Warning: data/etf_quality_latest.csv not found. ETF quality fields will be missing.")
        return pd.DataFrame(columns=["Symbol"])
    q = pd.read_csv(ETF_QUALITY_CSV)
    if "Symbol" not in q.columns:
        raise ValueError("etf_quality_latest.csv must include Symbol column.")
    for col in ["NAV", "AUM_Cr", "TER", "Tracking_Error", "Tracking_Difference", "Match_Score"]:
        if col in q.columns:
            q[col] = pd.to_numeric(q[col], errors="coerce")
    return q.drop_duplicates(subset=["Symbol"], keep="first")



def normalize_price_frame(temp: pd.DataFrame, symbol: str, method: str, period_used: str) -> tuple[pd.DataFrame | None, dict]:
    """Normalize one yfinance result into the engine's expected long format."""
    diag = {
        "Symbol": symbol,
        "Download_Status": "Failed",
        "Fetch_Method": method,
        "Period_Used": period_used,
        "Rows": 0,
        "First_Date": "",
        "Last_Date": "",
        "Failure_Reason": "",
    }

    if temp is None or temp.empty or temp.dropna(how="all").empty:
        diag["Failure_Reason"] = "empty dataframe"
        return None, diag

    work = temp.copy()
    work.columns = [str(c).strip() for c in work.columns]

    if "Date" not in work.columns:
        work = work.reset_index()

    if "Date" not in work.columns:
        # Some yfinance frames use Datetime as index name after reset.
        possible_date_cols = [c for c in work.columns if str(c).lower() in ["index", "datetime"]]
        if possible_date_cols:
            work = work.rename(columns={possible_date_cols[0]: "Date"})

    if "Date" not in work.columns:
        diag["Failure_Reason"] = "no Date column"
        return None, diag

    for col in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        if col not in work.columns:
            work[col] = np.nan

    work["Symbol"] = symbol
    work["Date"] = pd.to_datetime(work["Date"], errors="coerce").dt.date
    work["Price"] = pd.to_numeric(work["Adj Close"], errors="coerce").where(
        pd.to_numeric(work["Adj Close"], errors="coerce").notna(),
        pd.to_numeric(work["Close"], errors="coerce"),
    )
    work = work.dropna(subset=["Date", "Price"])

    if work.empty:
        diag["Failure_Reason"] = "no usable Date/Price rows"
        return None, diag

    keep_cols = ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume", "Symbol", "Price"]
    work = work[keep_cols].copy()
    diag.update({
        "Download_Status": "Success",
        "Rows": len(work),
        "First_Date": str(min(work["Date"])),
        "Last_Date": str(max(work["Date"])),
        "Failure_Reason": "",
    })
    return work, diag


def chunk_list(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def fetch_batch_symbols(symbols: list[str], period: str) -> tuple[list[pd.DataFrame], list[dict], list[str]]:
    frames: list[pd.DataFrame] = []
    diagnostics: list[dict] = []
    failed: list[str] = []

    try:
        raw = yf.download(
            tickers=symbols,
            period=period,
            interval=INTERVAL,
            auto_adjust=False,
            group_by="ticker",
            progress=False,
            threads=False,
            ignore_tz=True,
        )
    except Exception as exc:
        for sym in symbols:
            failed.append(sym)
            diagnostics.append({
                "Symbol": sym,
                "Download_Status": "Failed",
                "Fetch_Method": "batch",
                "Period_Used": period,
                "Rows": 0,
                "First_Date": "",
                "Last_Date": "",
                "Failure_Reason": f"batch exception: {exc}",
            })
        return frames, diagnostics, failed

    multi = len(symbols) > 1
    for sym in symbols:
        try:
            temp = raw[sym].copy() if multi else raw.copy()
            frame, diag = normalize_price_frame(temp, sym, "batch", period)
            diagnostics.append(diag)
            if frame is None:
                failed.append(sym)
            else:
                frames.append(frame)
        except Exception as exc:
            failed.append(sym)
            diagnostics.append({
                "Symbol": sym,
                "Download_Status": "Failed",
                "Fetch_Method": "batch",
                "Period_Used": period,
                "Rows": 0,
                "First_Date": "",
                "Last_Date": "",
                "Failure_Reason": f"extract exception: {exc}",
            })

    return frames, diagnostics, failed


def fetch_single_symbol(symbol: str) -> tuple[pd.DataFrame | None, dict]:
    last_diag = None
    for period in DOWNLOAD_RETRY_PERIODS:
        try:
            temp = yf.Ticker(symbol).history(
                period=period,
                interval=INTERVAL,
                auto_adjust=False,
                actions=False,
                raise_errors=False,
            )
            frame, diag = normalize_price_frame(temp, symbol, "single_retry", period)
            if frame is not None:
                return frame, diag
            last_diag = diag
        except Exception as exc:
            last_diag = {
                "Symbol": symbol,
                "Download_Status": "Failed",
                "Fetch_Method": "single_retry",
                "Period_Used": period,
                "Rows": 0,
                "First_Date": "",
                "Last_Date": "",
                "Failure_Reason": f"retry exception: {exc}",
            }
    if last_diag is None:
        last_diag = {
            "Symbol": symbol,
            "Download_Status": "Failed",
            "Fetch_Method": "single_retry",
            "Period_Used": "",
            "Rows": 0,
            "First_Date": "",
            "Last_Date": "",
            "Failure_Reason": "unknown failure",
        }
    return None, last_diag


def update_price_cache(candidate_prices: pd.DataFrame) -> None:
    """Persist successful prices across runs so validation is less fragile when Yahoo has a bad day."""
    if candidate_prices.empty:
        return

    cache_cols = ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume", "Symbol", "Price"]
    current = candidate_prices.copy()
    for col in cache_cols:
        if col not in current.columns:
            current[col] = np.nan
    current = current[cache_cols]
    current["Date"] = pd.to_datetime(current["Date"], errors="coerce").dt.date
    current = current.dropna(subset=["Date", "Symbol", "Price"])

    if PRICE_CACHE_CSV.exists():
        old = pd.read_csv(PRICE_CACHE_CSV)
        for col in cache_cols:
            if col not in old.columns:
                old[col] = np.nan
        old = old[cache_cols]
        old["Date"] = pd.to_datetime(old["Date"], errors="coerce").dt.date
        combined = pd.concat([old, current], ignore_index=True)
    else:
        combined = current

    combined = combined.dropna(subset=["Date", "Symbol", "Price"])
    combined = combined.drop_duplicates(subset=["Date", "Symbol"], keep="last")
    combined = combined.sort_values(["Symbol", "Date"])
    combined.to_csv(PRICE_CACHE_CSV, index=False)


def download_price_data(symbols: List[str], benchmark: str) -> Tuple[pd.DataFrame, pd.DataFrame, List[str]]:
    all_symbols = sorted(set(symbols + [benchmark]))
    print(f"Downloading data for {len(all_symbols)} symbols...")
    print("Using chunked yfinance download with individual retry fallback.")

    frames: list[pd.DataFrame] = []
    diagnostics: list[dict] = []
    initial_failed: list[str] = []

    for chunk in chunk_list(all_symbols, DOWNLOAD_CHUNK_SIZE):
        batch_frames, batch_diag, batch_failed = fetch_batch_symbols(chunk, PERIOD)
        frames.extend(batch_frames)
        diagnostics.extend(batch_diag)
        initial_failed.extend(batch_failed)

    retry_failed: list[str] = []
    if initial_failed:
        print(f"Initial missing symbols from batch download: {len(set(initial_failed))}. Retrying individually...")

    for sym in sorted(set(initial_failed)):
        frame, diag = fetch_single_symbol(sym)
        diagnostics.append(diag)
        if frame is None:
            retry_failed.append(sym)
        else:
            frames.append(frame)

    if not frames:
        pd.DataFrame(diagnostics).to_csv(DOWNLOAD_DIAGNOSTICS_CSV, index=False)
        raise ValueError("No data returned by yfinance. Check connection and symbols.")

    prices = pd.concat(frames, ignore_index=True)
    prices.columns = [str(c).strip() for c in prices.columns]
    prices["Date"] = pd.to_datetime(prices["Date"], errors="coerce").dt.date
    prices["Price"] = pd.to_numeric(prices["Price"], errors="coerce")
    prices = prices.dropna(subset=["Date", "Symbol", "Price"])
    prices = prices.drop_duplicates(subset=["Date", "Symbol"], keep="last")

    benchmark_prices = prices[prices["Symbol"] == benchmark][["Date", "Price"]].copy()
    benchmark_prices = benchmark_prices.rename(columns={"Price": "Benchmark_Close"}).dropna(subset=["Benchmark_Close"])

    candidate_prices = prices[prices["Symbol"] != benchmark].copy().dropna(subset=["Price"])

    diag_df = pd.DataFrame(diagnostics)
    if not diag_df.empty:
        # Keep the best status per symbol/method, but preserve retry detail.
        diag_df.to_csv(DOWNLOAD_DIAGNOSTICS_CSV, index=False)

    if retry_failed:
        print("Symbols still unavailable after retry:")
        print(", ".join(retry_failed[:50]) + (" ..." if len(retry_failed) > 50 else ""))
    else:
        print("All initially failed symbols recovered by retry.")

    return candidate_prices, benchmark_prices, sorted(set(retry_failed))


def safe_percentile_score(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    if s.notna().sum() <= 1:
        return pd.Series(50, index=series.index)
    rank = s.rank(pct=True) * 100
    if not higher_is_better:
        rank = 100 - rank
    return rank.fillna(50).clip(0, 100)


def score_lower_is_better(series: pd.Series, good_threshold: float | None = None) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    score = safe_percentile_score(s, higher_is_better=False)
    if good_threshold is not None:
        score = np.where(s <= good_threshold, np.maximum(score, 75), score)
    return pd.Series(score, index=series.index).fillna(50).clip(0, 100)


def score_higher_is_better(series: pd.Series, good_threshold: float | None = None) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    score = safe_percentile_score(s, higher_is_better=True)
    if good_threshold is not None:
        score = np.where(s >= good_threshold, np.maximum(score, 75), score)
    return pd.Series(score, index=series.index).fillna(50).clip(0, 100)


def calculate_rsi(price: pd.Series, window: int = 14) -> pd.Series:
    delta = price.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window, min_periods=window).mean()
    avg_loss = loss.rolling(window, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50).clip(0, 100)


def calculate_current_drawdown(price: pd.Series, window: int = 60) -> pd.Series:
    rolling_high = price.rolling(window, min_periods=10).max()
    return price / rolling_high - 1


def calculate_max_drawdown_window(price: pd.Series, window: int = 60) -> pd.Series:
    roll_max = price.rolling(window, min_periods=10).max()
    dd = price / roll_max - 1
    return dd.rolling(window, min_periods=10).min()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)


def compute_signals(prices: pd.DataFrame, benchmark_prices: pd.DataFrame, config: pd.DataFrame) -> pd.DataFrame:
    df = prices.copy()
    meta_cols = [
        "Symbol", "Universe", "Universe_Group", "Opportunity_Type", "Opportunity_Eligible",
        "Raw_Symbol", "Name", "Category", "ISIN", "Source"
    ]
    df = df.merge(config[meta_cols], on="Symbol", how="left")
    df = df.merge(benchmark_prices, on="Date", how="left")
    df = df.sort_values(["Symbol", "Date"])

    if df["Universe_Group"].isna().all() or df["Universe_Group"].astype(str).str.strip().eq("").all():
        raise ValueError("Universe_Group is missing after merge. Run universe_builder.py and inspect config.csv.")

    signal_frames = []
    for _, g in df.groupby("Symbol"):
        g = g.sort_values("Date").copy()
        g["Daily_Return"] = g["Price"].pct_change()
        g["Benchmark_Return"] = g["Benchmark_Close"].pct_change()

        for n in [5, 10, 21, 63, 126, 252]:
            g[f"Return_{n}D"] = g["Price"].pct_change(n)
            g[f"Benchmark_Return_{n}D"] = g["Benchmark_Close"].pct_change(n)
            g[f"Relative_Strength_{n}D"] = g[f"Return_{n}D"] - g[f"Benchmark_Return_{n}D"]

        for n in [10, 20, 50, 100, 200]:
            g[f"MA_{n}D"] = g["Price"].rolling(n, min_periods=max(5, n // 2)).mean()
            g[f"Above_{n}DMA"] = g["Price"] > g[f"MA_{n}D"]

        g["Volatility_20D"] = g["Daily_Return"].rolling(20, min_periods=10).std() * math.sqrt(252)
        g["Volatility_60D"] = g["Daily_Return"].rolling(60, min_periods=20).std() * math.sqrt(252)
        g["Current_Drawdown_60D"] = calculate_current_drawdown(g["Price"], 60)
        g["Max_Drawdown_60D"] = calculate_max_drawdown_window(g["Price"], 60)
        g["Max_Drawdown_252D"] = calculate_max_drawdown_window(g["Price"], 252)
        g["RSI_14"] = calculate_rsi(g["Price"], 14)

        g["Traded_Value"] = g["Price"] * g["Volume"]
        g["Avg_Traded_Value_20D"] = g["Traded_Value"].rolling(20, min_periods=5).mean()
        g["Volume_Ratio_20D"] = g["Volume"] / g["Volume"].rolling(20, min_periods=5).mean()
        g["Zero_Volume_Days_60D"] = (g["Volume"].fillna(0) <= 0).rolling(60, min_periods=10).sum()

        cov = g["Daily_Return"].rolling(252, min_periods=60).cov(g["Benchmark_Return"])
        var = g["Benchmark_Return"].rolling(252, min_periods=60).var()
        g["Beta_252D"] = cov / var.replace(0, np.nan)

        g["ATR_14"] = true_range(g["High"], g["Low"], g["Close"]).rolling(14, min_periods=7).mean()
        g["Entry_Zone"] = g["Price"]
        g["ATR_Stop_Level"] = g["Price"] - (1.5 * g["ATR_14"])
        g["ATR_Target_1"] = g["Price"] + (1.5 * g["ATR_14"])
        g["ATR_Target_2"] = g["Price"] + (2.5 * g["ATR_14"])
        g["Risk_Reward_Ratio_2"] = (g["ATR_Target_2"] - g["Price"]) / (g["Price"] - g["ATR_Stop_Level"])
        g["Stop_Level"] = g["ATR_Stop_Level"]
        g["Target_Zone"] = g["ATR_Target_1"]

        signal_frames.append(g)

    return pd.concat(signal_frames, ignore_index=True)


def latest_rows(signals: pd.DataFrame) -> pd.DataFrame:
    return signals.sort_values(["Symbol", "Date"]).groupby("Symbol").tail(1).copy()


def market_regime_score(benchmark_prices: pd.DataFrame) -> tuple[float, str]:
    b = benchmark_prices.sort_values("Date").copy()
    b["MA_20D"] = b["Benchmark_Close"].rolling(20, min_periods=10).mean()
    b["MA_50D"] = b["Benchmark_Close"].rolling(50, min_periods=20).mean()
    b["Return_21D"] = b["Benchmark_Close"].pct_change(21)

    latest = b.dropna().tail(1)
    if latest.empty:
        return 50, "Neutral"
    row = latest.iloc[0]
    if row["Benchmark_Close"] > row["MA_20D"] > row["MA_50D"] and row["Return_21D"] > 0:
        return 85, "Risk-On"
    if row["Benchmark_Close"] < row["MA_20D"] < row["MA_50D"] and row["Return_21D"] < 0:
        return 25, "Risk-Off"
    return 55, "Neutral"


def merge_etf_quality(latest: pd.DataFrame, etf_quality: pd.DataFrame) -> pd.DataFrame:
    df = latest.copy()
    if etf_quality.empty:
        for col in [
            "NAV", "NAV_Date", "AUM_Cr", "TER", "Tracking_Error", "Tracking_Difference",
            "Benchmark_Index", "ETF_Quality_Data_Flag", "ETF_Quality_Source",
            "AMFI_Scheme_Code", "AMFI_Scheme_Name", "Match_Score", "Mapping_Status"
        ]:
            df[col] = np.nan if col not in ["NAV_Date", "Benchmark_Index", "ETF_Quality_Data_Flag", "ETF_Quality_Source", "AMFI_Scheme_Code", "AMFI_Scheme_Name", "Mapping_Status"] else ""
        return df

    quality_cols = [
        "Symbol", "NAV", "NAV_Date", "AUM_Cr", "TER", "Tracking_Error",
        "Tracking_Difference", "Benchmark_Index", "ETF_Quality_Data_Flag", "ETF_Quality_Source",
        "AMFI_Scheme_Code", "AMFI_Scheme_Name", "Match_Score", "Mapping_Status"
    ]
    for col in quality_cols:
        if col not in etf_quality.columns:
            etf_quality[col] = np.nan if col not in ["Symbol", "NAV_Date", "Benchmark_Index", "ETF_Quality_Data_Flag", "ETF_Quality_Source", "AMFI_Scheme_Code", "AMFI_Scheme_Name", "Mapping_Status"] else ""
    df = df.merge(etf_quality[quality_cols], on="Symbol", how="left")
    df["NAV_Premium_Discount"] = np.where(
        pd.to_numeric(df["NAV"], errors="coerce").notna() & (pd.to_numeric(df["NAV"], errors="coerce") != 0),
        df["Price"] / pd.to_numeric(df["NAV"], errors="coerce") - 1,
        np.nan
    )
    return df


def build_consistency_features(latest: pd.DataFrame) -> pd.DataFrame:
    df = latest.copy()
    score_path = OUTPUT_DIR / "score_history.csv"

    df["Avg_Final_Score_4W"] = np.nan
    df["Score_Trend_4W"] = np.nan
    df["Score_Consistency_4W"] = 50.0
    df["Days_In_Top_25_8W"] = 0

    if not score_path.exists():
        return df

    hist = pd.read_csv(score_path)
    needed = {"Date", "Symbol", "Final_Score", "Rank"}
    if hist.empty or not needed.issubset(hist.columns):
        return df

    hist["Date"] = pd.to_datetime(hist["Date"], errors="coerce")
    hist["Final_Score"] = pd.to_numeric(hist["Final_Score"], errors="coerce")
    hist["Rank"] = pd.to_numeric(hist["Rank"], errors="coerce")
    max_date = hist["Date"].max()
    if pd.isna(max_date):
        return df

    four_w = hist[hist["Date"] >= max_date - pd.Timedelta(days=28)].copy()
    eight_w = hist[hist["Date"] >= max_date - pd.Timedelta(days=56)].copy()

    features = []
    for symbol, g in four_w.groupby("Symbol"):
        g = g.sort_values("Date")
        if g.empty:
            continue
        avg_score = g["Final_Score"].mean()
        score_trend = g["Final_Score"].iloc[-1] - g["Final_Score"].iloc[0] if len(g) >= 2 else 0
        std_score = g["Final_Score"].std() if len(g) >= 2 else 0
        consistency = max(0, min(100, avg_score - (std_score * 1.5)))
        days_top25 = int((eight_w[eight_w["Symbol"].eq(symbol)]["Rank"] <= 25).sum())
        features.append({
            "Symbol": symbol,
            "Avg_Final_Score_4W": avg_score,
            "Score_Trend_4W": score_trend,
            "Score_Consistency_4W": consistency,
            "Days_In_Top_25_8W": days_top25,
        })

    if features:
        feat = pd.DataFrame(features)
        df = df.drop(columns=["Avg_Final_Score_4W", "Score_Trend_4W", "Score_Consistency_4W", "Days_In_Top_25_8W"], errors="ignore")
        df = df.merge(feat, on="Symbol", how="left")
        df["Score_Consistency_4W"] = df["Score_Consistency_4W"].fillna(50)
        df["Days_In_Top_25_8W"] = df["Days_In_Top_25_8W"].fillna(0)

    return df


def score_candidates(latest: pd.DataFrame, benchmark_prices: pd.DataFrame, rules: Dict[str, float]) -> pd.DataFrame:
    df = latest.copy()
    regime_score, regime_label = market_regime_score(benchmark_prices)
    df["Market_Regime"] = regime_label
    df["Market_Regime_Score"] = regime_score

    df["Absolute_Momentum_Pass"] = (
        (df["Return_21D"].fillna(-999) >= rules["Return_21D_Absolute_Pass"])
        & (df["Return_5D"].fillna(-999) >= rules["Return_5D_Min"])
        & (df["Above_20DMA"].fillna(False) | df["Above_50DMA"].fillna(False))
    )

    df["Momentum_Raw"] = (
        0.25 * df["Return_5D"].fillna(0)
        + 0.45 * df["Return_21D"].fillna(0)
        + 0.30 * df["Return_63D"].fillna(0)
    )
    df["Momentum_Score"] = safe_percentile_score(df["Momentum_Raw"], True)

    df["Trend_Raw"] = (
        df["Above_10DMA"].astype(float).fillna(0) * 20
        + df["Above_20DMA"].astype(float).fillna(0) * 25
        + df["Above_50DMA"].astype(float).fillna(0) * 30
        + (df["MA_20D"] > df["MA_50D"]).astype(float).fillna(0) * 25
    )
    df["Trend_Score"] = df["Trend_Raw"].clip(0, 100)
    df["Relative_Strength_Score"] = safe_percentile_score(df["Relative_Strength_21D"], True)

    df["Opportunity_Score"] = (
        0.40 * df["Momentum_Score"]
        + 0.35 * df["Trend_Score"]
        + 0.25 * df["Relative_Strength_Score"]
    ).clip(0, 100)

    vol_score = safe_percentile_score(df["Volatility_20D"], False)
    current_dd_score = safe_percentile_score(df["Current_Drawdown_60D"], True)
    max_dd_score = safe_percentile_score(df["Max_Drawdown_60D"], True)
    zero_volume_score = safe_percentile_score(df["Zero_Volume_Days_60D"], False)
    liquidity_raw = safe_percentile_score(df["Avg_Traded_Value_20D"], True)
    df["Liquidity_Score"] = (0.80 * liquidity_raw + 0.20 * zero_volume_score).clip(0, 100)

    rsi_penalty = np.where(df["RSI_14"] > OVERBOUGHT_RSI, 25, 0) + np.where(df["RSI_14"] < OVERSOLD_RSI, 10, 0)
    df["Risk_Score"] = (
        0.50 * vol_score
        + 0.35 * current_dd_score
        + 0.15 * max_dd_score
        - rsi_penalty
    ).clip(0, 100)
    df["Safety_Score"] = (0.70 * df["Risk_Score"] + 0.30 * df["Liquidity_Score"]).clip(0, 100)

    df["ETF_Quality_Score"] = 50.0
    is_etf = df["Universe"].astype(str).str.lower().eq("etf")
    nav_premium_abs = pd.to_numeric(df.get("NAV_Premium_Discount", pd.Series(np.nan, index=df.index)), errors="coerce").abs()
    nav_score = score_lower_is_better(nav_premium_abs, rules["Abs_NAV_Premium_Max"])
    aum_score = score_higher_is_better(df.get("AUM_Cr", pd.Series(np.nan, index=df.index)), rules["AUM_Good_Cr"])
    ter_score = score_lower_is_better(df.get("TER", pd.Series(np.nan, index=df.index)), rules["TER_Good"])

    tracking_error_raw = pd.to_numeric(df.get("Tracking_Error", pd.Series(np.nan, index=df.index)), errors="coerce")
    tracking_difference_abs = pd.to_numeric(df.get("Tracking_Difference", pd.Series(np.nan, index=df.index)), errors="coerce").abs()
    tracking_quality_metric = tracking_error_raw.where(tracking_error_raw.notna(), tracking_difference_abs)
    tracking_quality_mode = np.where(
        tracking_error_raw.notna(),
        "Tracking_Error",
        np.where(tracking_difference_abs.notna(), "Tracking_Difference_Fallback", "Missing"),
    )
    te_score = score_lower_is_better(tracking_quality_metric, rules["Tracking_Error_Good"])
    te_score = pd.Series(te_score, index=df.index)
    te_score = te_score.where(pd.Series(tracking_quality_mode, index=df.index).ne("Tracking_Difference_Fallback"), te_score * 0.85)
    df["ETF_Tracking_Quality_Metric"] = tracking_quality_metric
    df["ETF_Tracking_Quality_Mode"] = tracking_quality_mode

    mapping_status = df.get("Mapping_Status", pd.Series("", index=df.index)).astype(str)
    mapping_trust_multiplier = np.where(mapping_status.eq("Verified"), 1.0, np.where(mapping_status.eq("Suggested"), 0.75, 0.50))
    quality_score = (0.30 * nav_score + 0.25 * aum_score + 0.25 * ter_score + 0.20 * te_score) * mapping_trust_multiplier
    df.loc[is_etf, "ETF_Quality_Score"] = pd.Series(quality_score, index=df.index)[is_etf].clip(0, 100)
    df.loc[~is_etf, "ETF_Quality_Score"] = 100.0

    df["Confidence_Score"] = 10.0
    df.loc[is_etf & pd.to_numeric(df.get("NAV", np.nan), errors="coerce").isna(), "Confidence_Score"] -= 1.5
    df.loc[is_etf & pd.to_numeric(df.get("AUM_Cr", np.nan), errors="coerce").isna(), "Confidence_Score"] -= 1.0
    df.loc[is_etf & pd.to_numeric(df.get("TER", np.nan), errors="coerce").isna(), "Confidence_Score"] -= 1.0
    tracking_error_missing = pd.to_numeric(df.get("Tracking_Error", np.nan), errors="coerce").isna()
    tracking_difference_missing = pd.to_numeric(df.get("Tracking_Difference", np.nan), errors="coerce").isna()
    df.loc[is_etf & tracking_error_missing & tracking_difference_missing, "Confidence_Score"] -= 1.0
    df.loc[is_etf & tracking_error_missing & (~tracking_difference_missing), "Confidence_Score"] -= 0.25
    df.loc[is_etf & mapping_status.isin(["Review", "Missing", ""]), "Confidence_Score"] -= 1.5
    df.loc[df["Market_Regime"].eq("Risk-Off"), "Confidence_Score"] -= 1.0
    df.loc[df["RSI_14"] > OVERBOUGHT_RSI, "Confidence_Score"] -= 1.0
    df["Confidence_Score"] = df["Confidence_Score"].clip(1, 10)

    # Keep consistency small. Cross-sectional validation lives outside the live score.
    consistency_score = df["Score_Consistency_4W"].fillna(50).clip(0, 100)

    flags = []
    for _, row in df.iterrows():
        f = []
        row_is_etf = str(row.get("Universe", "")).lower() == "etf"
        max_vol = MAX_VOLATILITY_20D_ETF if row_is_etf else MAX_VOLATILITY_20D_STOCK
        if str(row.get("Opportunity_Eligible", "Yes")).lower() == "no":
            f.append("Parking/debt ETF - excluded from opportunity ranking")
        if not bool(row.get("Absolute_Momentum_Pass", False)):
            f.append("Absolute momentum filter failed")
        if pd.notna(row.get("Volatility_20D")) and row["Volatility_20D"] > max_vol:
            f.append("High volatility")
        if pd.notna(row.get("Current_Drawdown_60D")) and row["Current_Drawdown_60D"] < MAX_DRAWDOWN_60D:
            f.append("High drawdown")
        if pd.notna(row.get("Zero_Volume_Days_60D")) and row["Zero_Volume_Days_60D"] >= 5:
            f.append("Illiquid/zero-volume days")
        if bool(row.get("Above_20DMA")) is False and bool(row.get("Above_50DMA")) is False:
            f.append("Weak trend")
        if pd.notna(row.get("RSI_14")) and row["RSI_14"] > OVERBOUGHT_RSI:
            f.append("Overbought RSI")
        if row_is_etf and pd.notna(row.get("NAV_Premium_Discount")) and abs(row["NAV_Premium_Discount"]) > rules["Abs_NAV_Premium_Max"]:
            f.append("High NAV premium/discount")
        if row_is_etf and row.get("ETF_Quality_Data_Flag"):
            qflag = str(row.get("ETF_Quality_Data_Flag")).lower()
            if any(term in qflag for term in ["nav missing", "aum missing", "ter missing", "tracking quality metric missing"]):
                f.append("ETF quality data incomplete")
            elif "tracking error unavailable" in qflag and "tracking difference available" in qflag:
                f.append("ETF tracking error unavailable; using tracking difference")
            elif "tracking disclosure unavailable" in qflag:
                f.append("ETF tracking disclosure unavailable from current AMFI feed")
        flags.append("; ".join(f) if f else "Clean")
    df["Risk_Flag"] = flags

    final_scores = []
    for _, row in df.iterrows():
        row_is_etf = str(row.get("Universe", "")).lower() == "etf"
        if row_is_etf:
            weights = ETF_WEIGHTS
            final = (
                weights["opportunity"] * row["Opportunity_Score"]
                + weights["safety"] * row["Safety_Score"]
                + weights["etf_quality"] * row["ETF_Quality_Score"]
                + weights["consistency"] * row["Score_Consistency_4W"]
                + weights["market_regime"] * row["Market_Regime_Score"]
            )
        else:
            weights = STOCK_WEIGHTS
            final = (
                weights["opportunity"] * row["Opportunity_Score"]
                + weights["safety"] * row["Safety_Score"]
                + weights["consistency"] * row["Score_Consistency_4W"]
                + weights["market_regime"] * row["Market_Regime_Score"]
            )
        if not bool(row.get("Absolute_Momentum_Pass", False)):
            final = min(final, 64.9)
        final_scores.append(final)

    df["Final_Score"] = np.array(final_scores).clip(0, 100)
    df["Confidence_Adjusted_Score"] = (df["Final_Score"] * (df["Confidence_Score"] / 10)).clip(0, 100)

    df["Bucket"] = df.apply(assign_bucket, axis=1)
    df = df.sort_values("Final_Score", ascending=False).copy()
    df["Rank"] = range(1, len(df) + 1)
    df["Reason"] = df.apply(build_reason, axis=1)
    df["Key_Risk"] = df.apply(build_key_risk, axis=1)

    df["Group_Rank"] = df.groupby("Universe_Group")["Final_Score"].rank(ascending=False, method="first").astype(int)
    df["Opportunity_Rank"] = np.nan
    eligible_mask = df["Opportunity_Eligible"].astype(str).str.lower().eq("yes")
    df.loc[eligible_mask, "Opportunity_Rank"] = range(1, eligible_mask.sum() + 1)

    df["Upside_Range_Low"] = 0.015
    df["Upside_Range_High"] = np.where(df["Momentum_Score"] >= 80, 0.05, 0.03)
    df["Suggested_Hold_Days"] = np.where(df["Momentum_Score"] >= 80, "5-15", "10-30")
    return df


def assign_bucket(row: pd.Series) -> str:
    if str(row.get("Opportunity_Eligible", "Yes")).lower() == "no":
        return "Parking / Not Opportunity"
    if not bool(row.get("Absolute_Momentum_Pass", False)):
        return "Avoid"
    score = row.get("Final_Score", 0)
    flag = row.get("Risk_Flag", "Clean")
    confidence = row.get("Confidence_Score", 10)
    if score >= 80 and flag == "Clean" and confidence >= 7:
        return "Top Candidate"
    if score >= 75:
        return "High Potential but Risky" if flag != "Clean" else "Candidate"
    if score >= 65:
        return "Candidate"
    if score >= 55:
        return "Watch"
    return "Avoid"


def build_reason(row: pd.Series) -> str:
    if str(row.get("Opportunity_Eligible", "Yes")).lower() == "no":
        return "parking/debt ETF; useful for cash parking, not short-term high-return opportunity"
    reasons = []
    if not bool(row.get("Absolute_Momentum_Pass", False)):
        reasons.append("failed absolute momentum filter")
    if row.get("Opportunity_Score", 0) >= 70:
        reasons.append("strong opportunity score")
    if row.get("Safety_Score", 0) >= 70:
        reasons.append("strong safety/liquidity profile")
    if row.get("Score_Consistency_4W", 50) >= 65:
        reasons.append("recent score consistency")
    if row.get("ETF_Quality_Score", 100) >= 70 and str(row.get("Universe", "")).lower() == "etf":
        reasons.append("acceptable ETF quality score")
    if row.get("Market_Regime") == "Risk-On":
        reasons.append("supportive market regime")
    return ", ".join(reasons) if reasons else "mixed signals; review before action"


def build_key_risk(row: pd.Series) -> str:
    if row.get("Risk_Flag") and row["Risk_Flag"] != "Clean":
        return row["Risk_Flag"]
    risks = []
    if row.get("Confidence_Score", 10) < 7:
        risks.append("lower confidence due to missing data")
    if pd.notna(row.get("RSI_14")) and row["RSI_14"] > OVERBOUGHT_RSI:
        risks.append("overbought RSI")
    if pd.notna(row.get("Volatility_20D")) and row["Volatility_20D"] > 0.35:
        risks.append("elevated volatility")
    return "; ".join(risks) if risks else "No major technical risk flagged"


def append_history(file_path: Path, new_rows: pd.DataFrame, key_cols: List[str]) -> None:
    if file_path.exists():
        old = pd.read_csv(file_path)
        combined = pd.concat([old, new_rows], ignore_index=True)
    else:
        combined = new_rows.copy()
    combined = combined.drop_duplicates(subset=key_cols, keep="last")
    combined.to_csv(file_path, index=False)


def make_rank_changes(scored: pd.DataFrame) -> pd.DataFrame:
    history_path = OUTPUT_DIR / "score_history.csv"
    base_cols = ["Date", "Symbol", "Name", "Universe", "Universe_Group", "Rank", "Final_Score", "Bucket"]
    if not history_path.exists():
        out = scored[base_cols].copy()
        out["Previous_Rank"] = np.nan
        out["Previous_Score"] = np.nan
        out["Rank_Change"] = np.nan
        out["Score_Change"] = np.nan
        return out
    old = pd.read_csv(history_path)
    if old.empty or "Date" not in old.columns:
        return pd.DataFrame()
    old["Date"] = pd.to_datetime(old["Date"], errors="coerce")
    latest_old_date = old["Date"].dropna().max()
    if pd.isna(latest_old_date):
        return pd.DataFrame()
    prev = old[old["Date"] == latest_old_date][["Symbol", "Rank", "Final_Score"]].copy()
    prev = prev.rename(columns={"Rank": "Previous_Rank", "Final_Score": "Previous_Score"})
    curr = scored[base_cols].copy()
    out = curr.merge(prev, on="Symbol", how="left")
    out["Rank_Change"] = out["Previous_Rank"] - out["Rank"]
    out["Score_Change"] = out["Final_Score"] - out["Previous_Score"]
    return out.sort_values("Rank")


def save_outputs(scored: pd.DataFrame, failed_symbols: List[str], config: pd.DataFrame, rules: Dict[str, float]) -> None:
    today = pd.Timestamp.today().strftime("%Y-%m-%d")
    run_ts = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

    output_cols = [
        "Date", "Universe", "Universe_Group", "Opportunity_Type", "Opportunity_Eligible",
        "Opportunity_Rank", "Group_Rank", "Rank", "Symbol", "Raw_Symbol",
        "Name", "Category", "ISIN", "Source",
        "Price", "Close", "Adj Close", "Volume",
        "Final_Score", "Confidence_Adjusted_Score", "Confidence_Score",
        "Opportunity_Score", "Safety_Score", "ETF_Quality_Score",
        "Score_Consistency_4W", "Score_Trend_4W", "Days_In_Top_25_8W",
        "Momentum_Score", "Trend_Score", "Risk_Score",
        "Relative_Strength_Score", "Liquidity_Score", "Market_Regime",
        "Absolute_Momentum_Pass", "Return_5D", "Return_10D", "Return_21D", "Return_63D",
        "Volatility_20D", "Volatility_60D", "Current_Drawdown_60D", "Max_Drawdown_60D",
        "RSI_14", "ATR_14", "Avg_Traded_Value_20D", "Zero_Volume_Days_60D", "Beta_252D",
        "NAV", "NAV_Date", "NAV_Premium_Discount", "AUM_Cr", "TER", "Tracking_Error",
        "Tracking_Difference", "ETF_Tracking_Quality_Metric", "ETF_Tracking_Quality_Mode", "Benchmark_Index", "Mapping_Status", "ETF_Quality_Data_Flag", "ETF_Quality_Source",
        # --- Trend / relative-strength inputs consumed by the shadow engine ---
        "MA_10D", "MA_20D", "MA_50D", "MA_100D", "MA_200D",
        "Above_10DMA", "Above_20DMA", "Above_50DMA", "Above_100DMA", "Above_200DMA",
        "Benchmark_Return_5D", "Benchmark_Return_10D", "Benchmark_Return_21D",
        "Benchmark_Return_63D", "Relative_Strength_5D", "Relative_Strength_10D",
        "Relative_Strength_21D", "Relative_Strength_63D",
        "Entry_Zone", "Stop_Level", "Target_Zone", "ATR_Stop_Level", "ATR_Target_1", "ATR_Target_2", "Risk_Reward_Ratio_2",
        "Upside_Range_Low", "Upside_Range_High", "Suggested_Hold_Days",
        "Bucket", "Reason", "Key_Risk", "Risk_Flag",
    ]
    for col in output_cols:
        if col not in scored.columns:
            scored[col] = np.nan

    latest_scores = scored[output_cols].copy()
    latest_xlsx = OUTPUT_DIR / "latest_scores.xlsx"
    latest_csv = OUTPUT_DIR / "latest_scores.csv"
    dated_xlsx = OUTPUT_DIR / f"latest_scores_{today}.xlsx"

    # Assert shadow-required columns are present (fail loudly, never silent).
    _shadow_required = [
        "Price", "MA_20D", "MA_50D", "MA_200D",
        "Above_20DMA", "Above_50DMA", "Above_200DMA",
        "Benchmark_Return_21D", "Relative_Strength_21D",
        "Momentum_Score", "Trend_Score", "Safety_Score",
        "Final_Score", "Confidence_Adjusted_Score",
    ]
    _missing = [c for c in _shadow_required if c not in latest_scores.columns]
    if _missing:
        raise RuntimeError(f"latest_scores.csv missing shadow-required columns: {_missing}")

    latest_scores.to_csv(latest_csv, index=False)

    rank_changes = make_rank_changes(scored)
    if not rank_changes.empty:
        rank_changes.to_csv(OUTPUT_DIR / "rank_changes.csv", index=False)

    universe_summary = (
        config.groupby(["Universe", "Universe_Group", "Opportunity_Type", "Opportunity_Eligible"], dropna=False)
        .size().reset_index(name="Config_Count").sort_values(["Universe", "Universe_Group", "Opportunity_Type"])
    )

    eligible = latest_scores[latest_scores["Opportunity_Eligible"].astype(str).str.lower().eq("yes")].copy()
    top_opportunities = eligible.sort_values("Final_Score", ascending=False).head(TOP_N)
    top_confidence_adjusted = eligible.sort_values("Confidence_Adjusted_Score", ascending=False).head(TOP_N)
    top_etfs = eligible[eligible["Universe"].str.lower().eq("etf")].sort_values("Final_Score", ascending=False).head(TOP_N)
    top_stocks = eligible[eligible["Universe"].str.lower().eq("stock")].sort_values("Final_Score", ascending=False).head(TOP_N)
    low_risk = eligible[
        (eligible["Risk_Score"] >= rules["Risk_Score_Min"])
        & (eligible["Final_Score"] >= rules["Final_Score_Min"])
        & (eligible["Return_21D"] >= rules["Return_21D_Min"])
        & (~eligible["Bucket"].eq("Avoid"))
    ].sort_values(["Risk_Score", "Final_Score"], ascending=False).head(TOP_N)
    etf_quality_review = latest_scores[
        latest_scores["Universe"].str.lower().eq("etf")
        & latest_scores["ETF_Quality_Data_Flag"].astype(str).str.lower().ne("complete")
    ].head(TOP_N)
    parking = latest_scores[latest_scores["Opportunity_Eligible"].astype(str).str.lower().eq("no")].sort_values("Final_Score", ascending=False).head(TOP_N)
    avoid = latest_scores[latest_scores["Bucket"].eq("Avoid")].head(TOP_N)

    for path in [latest_xlsx, dated_xlsx]:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            latest_scores.to_excel(writer, sheet_name="Latest Scores", index=False)
            top_opportunities.to_excel(writer, sheet_name="Top Opportunities", index=False)
            top_confidence_adjusted.to_excel(writer, sheet_name="Top Confidence Adj", index=False)
            top_etfs.to_excel(writer, sheet_name="Top ETFs", index=False)
            top_stocks.to_excel(writer, sheet_name="Top Stocks", index=False)
            low_risk.to_excel(writer, sheet_name="Top Low Risk", index=False)
            etf_quality_review.to_excel(writer, sheet_name="ETF Quality Review", index=False)
            parking.to_excel(writer, sheet_name="Parking Debt ETFs", index=False)
            avoid.to_excel(writer, sheet_name="Avoid Review", index=False)
            universe_summary.to_excel(writer, sheet_name="Universe Summary", index=False)
            pd.DataFrame([rules]).to_excel(writer, sheet_name="Scoring Rules", index=False)
            if not rank_changes.empty:
                rank_changes.to_excel(writer, sheet_name="Rank Changes", index=False)
            if failed_symbols:
                pd.DataFrame({"Failed_Symbols": failed_symbols}).to_excel(writer, sheet_name="Failed Symbols", index=False)

    score_history_cols = [
        "Date", "Symbol", "Universe", "Universe_Group", "Opportunity_Type", "Opportunity_Eligible",
        "Name", "Category", "Final_Score", "Confidence_Adjusted_Score", "Confidence_Score",
        "Opportunity_Score", "Safety_Score", "ETF_Quality_Score", "Score_Consistency_4W",
        "Rank", "Opportunity_Rank", "Group_Rank", "Bucket", "Risk_Flag", "Reason", "Key_Risk"
    ]
    score_hist = scored[score_history_cols].copy()
    score_hist["Run_Timestamp"] = run_ts
    append_history(OUTPUT_DIR / "score_history.csv", score_hist, ["Date", "Symbol"])

    signal_history_cols = [
        "Date", "Symbol", "Universe", "Universe_Group", "Opportunity_Type", "Opportunity_Eligible",
        "Name", "Category", "Price", "Volume", "Return_5D", "Return_21D", "Return_63D",
        "Volatility_20D", "Current_Drawdown_60D", "Max_Drawdown_60D", "RSI_14",
        "Above_20DMA", "Above_50DMA", "Relative_Strength_21D", "Avg_Traded_Value_20D",
        "Zero_Volume_Days_60D", "Momentum_Score", "Trend_Score", "Risk_Score",
        "Opportunity_Score", "Safety_Score", "ETF_Quality_Score", "Final_Score", "Bucket"
    ]
    signal_hist = scored[signal_history_cols].copy()
    signal_hist["Run_Timestamp"] = run_ts
    append_history(OUTPUT_DIR / "signal_history.csv", signal_hist, ["Date", "Symbol"])

    run_log_row = pd.DataFrame([{
        "Run_Timestamp": run_ts,
        "Run_Date": today,
        "Config_Count": len(config),
        "Scored_Count": len(scored),
        "Eligible_Count": int((scored["Opportunity_Eligible"].astype(str).str.lower() == "yes").sum()),
        "Failed_Count": len(failed_symbols),
        "Top_Symbol": str(top_opportunities["Symbol"].iloc[0]) if not top_opportunities.empty else "",
        "Top_Final_Score": float(top_opportunities["Final_Score"].iloc[0]) if not top_opportunities.empty else np.nan,
    }])
    append_history(OUTPUT_DIR / "run_log.csv", run_log_row, ["Run_Timestamp"])

    write_report(scored, failed_symbols, universe_summary, rules)
    print("\nSaved outputs:")
    for p in [latest_xlsx, latest_csv, dated_xlsx, OUTPUT_DIR / "score_history.csv", OUTPUT_DIR / "signal_history.csv", OUTPUT_DIR / "weekly_report.md"]:
        print(f"  {p}")


def write_report(scored: pd.DataFrame, failed_symbols: List[str], universe_summary: pd.DataFrame, rules: Dict[str, float]) -> None:
    report_path = OUTPUT_DIR / "weekly_report.md"
    ts = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    eligible = scored[scored["Opportunity_Eligible"].astype(str).str.lower().eq("yes")].copy()

    def md_table(df: pd.DataFrame, cols: List[str]) -> str:
        if df.empty:
            return "_No rows._"
        temp = df[cols].copy()
        for c in ["Final_Score", "Confidence_Adjusted_Score", "Confidence_Score", "Risk_Score", "Return_21D", "Volatility_20D", "Current_Drawdown_60D", "ETF_Quality_Score"]:
            if c in temp.columns:
                temp[c] = pd.to_numeric(temp[c], errors="coerce").round(4)
        return temp.to_markdown(index=False)

    cols = [
        "Rank", "Universe_Group", "Opportunity_Type", "Symbol", "Name", "Final_Score",
        "Confidence_Adjusted_Score", "Confidence_Score", "Risk_Score", "ETF_Quality_Score",
        "Bucket", "Return_21D", "Volatility_20D", "Current_Drawdown_60D",
        "Suggested_Hold_Days", "Reason", "Key_Risk"
    ]

    top5 = eligible.sort_values("Final_Score", ascending=False).head(5)
    conf = eligible.sort_values("Confidence_Adjusted_Score", ascending=False).head(5)
    etfs = eligible[eligible["Universe"].str.lower().eq("etf")].sort_values("Final_Score", ascending=False).head(5)
    stocks = eligible[eligible["Universe"].str.lower().eq("stock")].sort_values("Final_Score", ascending=False).head(5)
    low_risk = eligible[
        (eligible["Risk_Score"] >= rules["Risk_Score_Min"])
        & (eligible["Final_Score"] >= rules["Final_Score_Min"])
        & (eligible["Return_21D"] >= rules["Return_21D_Min"])
        & (~eligible["Bucket"].eq("Avoid"))
    ].sort_values(["Risk_Score", "Final_Score"], ascending=False).head(5)

    lines = []
    lines.append("# NSE Quant Engine Stage 3.3 Final Report")
    lines.append("")
    lines.append(f"Generated: {ts}")
    lines.append("")
    lines.append("## Universe Summary")
    lines.append(universe_summary.to_markdown(index=False))
    lines.append("")
    lines.append("## Top 5 Opportunities")
    lines.append(md_table(top5, cols))
    lines.append("")
    lines.append("## Top 5 Confidence-Adjusted")
    lines.append(md_table(conf, cols))
    lines.append("")
    lines.append("## Top 5 ETFs")
    lines.append(md_table(etfs, cols))
    lines.append("")
    lines.append("## Top 5 Stocks")
    lines.append(md_table(stocks, cols))
    lines.append("")
    lines.append("## Top 5 Low-Risk Eligible Candidates")
    lines.append(md_table(low_risk, cols))
    lines.append("")
    lines.append("## Notes")
    lines.append("- Stage 3.3 Final separates live scoring from validation.")
    lines.append("- Run validation_builder.py and cross_sectional_validation.py after this to test whether the score actually predicts returns.")
    lines.append("- News/context is handled separately by news_market_builder.py.")
    lines.append("- This is a personal screening output, not financial advice.")
    if failed_symbols:
        lines.append("")
        lines.append("## Failed yfinance Symbols")
        for s in failed_symbols:
            lines.append(f"- {s}")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def print_summary(scored: pd.DataFrame) -> None:
    eligible = scored[scored["Opportunity_Eligible"].astype(str).str.lower().eq("yes")].copy()
    print("\nTop 20 eligible opportunities:")
    show_cols = [
        "Opportunity_Rank", "Universe_Group", "Opportunity_Type", "Symbol", "Name",
        "Final_Score", "Confidence_Adjusted_Score", "Confidence_Score",
        "Risk_Score", "Bucket", "Return_21D", "Volatility_20D", "Current_Drawdown_60D",
        "Reason", "Key_Risk"
    ]
    display_df = eligible.sort_values("Final_Score", ascending=False)[show_cols].head(20).copy()
    for col in ["Final_Score", "Confidence_Adjusted_Score", "Confidence_Score", "Risk_Score", "Return_21D", "Volatility_20D", "Current_Drawdown_60D"]:
        display_df[col] = pd.to_numeric(display_df[col], errors="coerce").round(4)
    print(display_df.to_string(index=False))


def main() -> None:
    print("NSE Quant Engine - Stage 3.5.10 Merged Metadata ETF Quality")
    print("====================================================")

    rules = load_rules()
    config = load_config()
    etf_quality = load_etf_quality()

    symbols = config["Symbol"].dropna().unique().tolist()
    print(f"Loaded config rows: {len(config)}")
    print(f"Loaded ETF quality rows: {len(etf_quality)}")

    print("\nUniverse counts:")
    print(config["Universe_Group"].value_counts(dropna=False).to_string())
    print("\nOpportunity eligibility:")
    print(config["Opportunity_Eligible"].value_counts(dropna=False).to_string())

    prices, benchmark_prices, failed_symbols = download_price_data(symbols, BENCHMARK_SYMBOL)
    if failed_symbols:
        print("\nWarning: no usable yfinance data for some symbols.")
        print(f"Failed count: {len(failed_symbols)}")

    if prices.empty:
        raise ValueError("No candidate price data downloaded. Check symbols in config.")

    prices.to_csv(DATA_DIR / "raw_prices_latest.csv", index=False)
    update_price_cache(prices)
    print(f"Saved/updated price cache: {PRICE_CACHE_CSV}")
    print(f"Saved download diagnostics: {DOWNLOAD_DIAGNOSTICS_CSV}")

    signals = compute_signals(prices, benchmark_prices, config)
    latest = latest_rows(signals)
    counts = signals.groupby("Symbol")["Date"].count().rename("History_Days").reset_index()
    latest = latest.merge(counts, on="Symbol", how="left")
    latest = latest[latest["History_Days"] >= MIN_HISTORY_DAYS].copy()

    if latest.empty:
        raise ValueError("No symbols have enough price history. Reduce MIN_HISTORY_DAYS or check symbols.")

    latest = merge_etf_quality(latest, etf_quality)
    latest = build_consistency_features(latest)
    scored = score_candidates(latest, benchmark_prices, rules)
    save_outputs(scored, failed_symbols, config, rules)
    print_summary(scored)

    print("\nDone. Next run: python validation_builder.py, then python cross_sectional_validation.py, then python news_market_builder.py")


if __name__ == "__main__":
    main()
