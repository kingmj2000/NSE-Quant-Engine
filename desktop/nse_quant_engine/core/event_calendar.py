"""
Step 11 — Event & Catalyst Calendar (offline).

Reads optional event columns from the fundamentals cache (populated by
`core.fundamental_factor.fetch_fundamentals` when yfinance exposes them):
  * NextEarningsDate    (YYYY-MM-DD or pandas timestamp)
  * ExDividendDate      (YYYY-MM-DD)
  * LastEarningsSurprise (numeric, optional)

For each top-5 pick, decides whether the recommended hold horizon crosses
the next earnings window (± EARNINGS_BUFFER_DAYS) and emits Event_Risk_Flag.

No network. Missing data → Event_Risk_Flag='Unknown'. Never raises.
"""
from __future__ import annotations
from datetime import datetime, timedelta
import numpy as np
import pandas as pd


EARNINGS_BUFFER_DAYS = 2  # ±days around the reported date counts as "in-window"


def _as_date(v) -> pd.Timestamp | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        d = pd.to_datetime(v, errors="coerce")
        if pd.isna(d):
            return None
        return d.normalize()
    except Exception:
        return None


def classify_event(next_earn: pd.Timestamp | None,
                   as_of: pd.Timestamp,
                   horizon_days: int) -> tuple[str, int | None]:
    """Return (flag, days_to_earnings). Flag ∈ {In_Window, Pre_Earnings,
    Post_Earnings, Clear, Unknown}."""
    if next_earn is None:
        return "Unknown", None
    delta = (next_earn - as_of).days
    if -EARNINGS_BUFFER_DAYS <= delta <= horizon_days + EARNINGS_BUFFER_DAYS:
        if delta < 0:
            return "Post_Earnings", delta
        return "In_Window", delta
    if 0 < delta <= 30:
        return "Pre_Earnings", delta
    if -30 <= delta < 0:
        return "Post_Earnings", delta
    return "Clear", delta


def build(top5: pd.DataFrame,
          horizon_df: pd.DataFrame | None,
          fund_cache: pd.DataFrame | None,
          default_horizon: int = 10,
          as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    """Return per-symbol event frame. Never raises."""
    cols = ["Symbol", "Rec_Horizon_Days", "Next_Earnings_Date",
            "Days_To_Earnings", "Ex_Dividend_Date", "Last_Earnings_Surprise",
            "Event_Risk_Flag", "Event_Note"]
    if top5 is None or top5.empty:
        return pd.DataFrame(columns=cols)
    as_of = as_of or pd.Timestamp.now().normalize()

    hmap: dict[str, int] = {}
    if horizon_df is not None and not horizon_df.empty and "Symbol" in horizon_df.columns:
        col = "Rec_Horizon_Days" if "Rec_Horizon_Days" in horizon_df.columns else None
        if col:
            for _, r in horizon_df.iterrows():
                try:
                    hmap[str(r["Symbol"])] = int(r[col])
                except Exception:
                    pass

    fmap: dict[str, dict] = {}
    if fund_cache is not None and not fund_cache.empty and "Symbol" in fund_cache.columns:
        for _, r in fund_cache.iterrows():
            fmap[str(r["Symbol"])] = r.to_dict()

    rows = []
    for _, r in top5.iterrows():
        sym = str(r.get("Symbol", ""))
        if not sym:
            continue
        hz = hmap.get(sym, default_horizon)
        f = fmap.get(sym, {})
        ne = _as_date(f.get("NextEarningsDate") or f.get("earningsDate"))
        xd = _as_date(f.get("ExDividendDate") or f.get("exDividendDate"))
        surp = f.get("LastEarningsSurprise")
        try:
            surp = float(surp) if surp is not None and not pd.isna(surp) else np.nan
        except Exception:
            surp = np.nan
        flag, dte = classify_event(ne, as_of, hz)
        note = ""
        if flag == "In_Window":
            note = f"Earnings inside hold window ({dte}d) — expect gap risk."
        elif flag == "Pre_Earnings":
            note = f"Earnings in {dte}d, after hold window."
        elif flag == "Post_Earnings":
            note = f"Reported {abs(dte)}d ago."
        elif flag == "Clear":
            note = "No earnings inside hold window."
        else:
            note = "Earnings date unknown — treat as neutral."
        rows.append({
            "Symbol": sym,
            "Rec_Horizon_Days": hz,
            "Next_Earnings_Date": ne.strftime("%Y-%m-%d") if ne is not None else "",
            "Days_To_Earnings": dte if dte is not None else "",
            "Ex_Dividend_Date": xd.strftime("%Y-%m-%d") if xd is not None else "",
            "Last_Earnings_Surprise": round(surp, 4) if pd.notna(surp) else np.nan,
            "Event_Risk_Flag": flag,
            "Event_Note": note,
        })
    return pd.DataFrame(rows, columns=cols)
