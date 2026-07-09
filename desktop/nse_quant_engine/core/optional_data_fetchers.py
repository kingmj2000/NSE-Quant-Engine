"""
Step 0.5 — Auto-refresh the four optional overlay CSVs from free public sources.

Files produced (schemas match what the existing overlays already read):

  data/fii_dii_daily.csv         Date, FII_Net_INR_Cr, DII_Net_INR_Cr
  data/bulk_deals.csv            Date, Symbol, Client, Buy_Sell, Qty, Price
  data/fundamentals_latest.csv   Symbol, ROE_TTM, DebtToEquity, EPS_Growth_YoY,
                                 PE_TTM, PEG, ProfitMargin, PromoterPledgePct,
                                 PE_Self_Median_3Y
  data/earnings_calendar.csv     Symbol, Event_Date

Design rules (non-negotiable, matches plan .lovable/plan.md):
  * Fail SOFT. Any exception → log "[fetch][warn] ..." and return. Pipeline
    must never break because a public site changed HTML or is rate-limiting.
  * Never delete an existing file. Refresh only if stale.
  * Respect user-provided files: if a CSV is newer than the freshness window,
    we skip the fetch. Broker/paid exports always win.
  * Zero new dependencies — uses requests / pandas / bs4 / lxml / yfinance
    which are already in requirements.txt.
"""
from __future__ import annotations

import io
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd


# --------- freshness windows (hours) ----------------------------------------
FRESH_FLOW_HOURS  = 24   # FII/DII & bulk deals — daily flow data
FRESH_FUND_HOURS  = 24 * 7   # fundamentals & earnings — weekly refresh is fine
FRESH_EVENT_HOURS = 24 * 3


# ------------------------------- utilities ----------------------------------
def _log(msg: str) -> None:
    print(f"[fetch] {msg}", flush=True)


def _warn(source: str, exc: BaseException) -> None:
    print(f"[fetch][warn] {source}: {type(exc).__name__}: {exc}", flush=True)


def _is_fresh(path: Path, max_age_hours: float) -> bool:
    if not path.exists():
        return False
    try:
        age_h = (time.time() - path.stat().st_mtime) / 3600.0
        return age_h < max_age_hours
    except Exception:
        return False


def _requests_session():
    import requests
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept": "application/json, text/html, */*",
    })
    return s


def _merge_dated(existing: Path, new_df: pd.DataFrame, date_col: str,
                 keep_days: int) -> pd.DataFrame:
    """Union new_df with existing CSV, drop dupes on date_col, keep last N days."""
    frames = [new_df]
    if existing.exists():
        try:
            old = pd.read_csv(existing)
            frames.append(old)
        except Exception:
            pass
    df = pd.concat(frames, ignore_index=True)
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col])
    df = df.drop_duplicates(subset=[date_col] + ([] if date_col == "Date" and "Symbol" not in df.columns else [c for c in ("Symbol", "Client", "Buy_Sell", "Qty", "Price") if c in df.columns]))
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=keep_days)
    df = df[df[date_col] >= cutoff]
    df = df.sort_values(date_col).reset_index(drop=True)
    df[date_col] = df[date_col].dt.strftime("%Y-%m-%d")
    return df


# =========================================================================
# 1) FII / DII daily flow — Moneycontrol table (pandas.read_html)
# =========================================================================
def fetch_fii_dii(data_dir: Path, keep_days: int = 90) -> bool:
    target = data_dir / "fii_dii_daily.csv"
    if _is_fresh(target, FRESH_FLOW_HOURS):
        _log(f"fii_dii_daily.csv fresh (<{FRESH_FLOW_HOURS}h) — skipping fetch")
        return True
    url = "https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/index.php"
    try:
        sess = _requests_session()
        r = sess.get(url, timeout=15)
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
        # find the table with FII / DII columns
        picked = None
        for t in tables:
            cols = " ".join(str(c) for c in t.columns).lower()
            if "fii" in cols and "dii" in cols and "net" in cols:
                picked = t
                break
        if picked is None or picked.empty:
            raise RuntimeError("no FII/DII table found on Moneycontrol page")

        # flatten multi-index headers if present
        if isinstance(picked.columns, pd.MultiIndex):
            picked.columns = [" ".join([str(x) for x in tup if str(x) != "nan"]).strip()
                              for tup in picked.columns.to_list()]
        # Heuristic column matching
        cmap = {c.lower(): c for c in picked.columns}
        def _find(*needles):
            for c_lower, c_orig in cmap.items():
                if all(n in c_lower for n in needles):
                    return c_orig
            return None
        date_c = _find("date")
        fii_net_c = _find("fii", "net") or _find("fii")
        dii_net_c = _find("dii", "net") or _find("dii")
        if not (date_c and fii_net_c and dii_net_c):
            raise RuntimeError(f"could not identify Date/FII/DII columns in {list(picked.columns)}")

        out = pd.DataFrame({
            "Date": pd.to_datetime(picked[date_c], errors="coerce", dayfirst=True),
            "FII_Net_INR_Cr": pd.to_numeric(picked[fii_net_c].astype(str).str.replace(",", "").str.replace("−", "-"), errors="coerce"),
            "DII_Net_INR_Cr": pd.to_numeric(picked[dii_net_c].astype(str).str.replace(",", "").str.replace("−", "-"), errors="coerce"),
        }).dropna(subset=["Date"])
        merged = _merge_dated(target, out, "Date", keep_days)
        data_dir.mkdir(parents=True, exist_ok=True)
        merged.to_csv(target, index=False)
        _log(f"fii_dii_daily.csv refreshed ({len(merged)} rows)")
        return True
    except Exception as e:
        _warn("fii_dii (moneycontrol)", e)
        return target.exists()


# =========================================================================
# 2) NSE bulk deals — official JSON endpoint (needs cookie warmup)
# =========================================================================
def fetch_bulk_deals(data_dir: Path, days: int = 30, keep_days: int = 60) -> bool:
    target = data_dir / "bulk_deals.csv"
    if _is_fresh(target, FRESH_FLOW_HOURS):
        _log(f"bulk_deals.csv fresh (<{FRESH_FLOW_HOURS}h) — skipping fetch")
        return True
    try:
        sess = _requests_session()
        # cookie warm-up — NSE rejects direct API hits without a session cookie
        sess.get("https://www.nseindia.com/", timeout=15)
        sess.get("https://www.nseindia.com/report-detail/display-bulk-and-block-deals", timeout=15)

        end = datetime.now()
        start = end - timedelta(days=days)
        url = (f"https://www.nseindia.com/api/historical/bulk-deals"
               f"?from={start.strftime('%d-%m-%Y')}&to={end.strftime('%d-%m-%Y')}")
        r = sess.get(url, timeout=20)
        r.raise_for_status()
        payload = r.json()
        rows = payload.get("data") or []
        if not rows:
            raise RuntimeError("NSE returned empty bulk-deals payload")

        # Field names in NSE bulk-deals API (as observed): BD_DT_DATE, BD_SYMBOL,
        # BD_CLIENT_NAME, BD_BUY_SELL, BD_QTY_TRD, BD_TP_WATP
        def _get(row, *keys, default=None):
            for k in keys:
                if k in row and row[k] not in (None, "", "-"):
                    return row[k]
            return default

        out = pd.DataFrame([{
            "Date":    _get(r, "BD_DT_DATE", "date"),
            "Symbol":  _get(r, "BD_SYMBOL", "symbol"),
            "Client":  _get(r, "BD_CLIENT_NAME", "clientName"),
            "Buy_Sell": _get(r, "BD_BUY_SELL", "buySell"),
            "Qty":     _get(r, "BD_QTY_TRD", "quantityTraded"),
            "Price":   _get(r, "BD_TP_WATP", "watp"),
        } for r in rows])
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce", dayfirst=True)
        out = out.dropna(subset=["Date", "Symbol"])
        out["Qty"] = pd.to_numeric(out["Qty"].astype(str).str.replace(",", ""), errors="coerce")
        out["Price"] = pd.to_numeric(out["Price"].astype(str).str.replace(",", ""), errors="coerce")
        merged = _merge_dated(target, out, "Date", keep_days)
        data_dir.mkdir(parents=True, exist_ok=True)
        merged.to_csv(target, index=False)
        _log(f"bulk_deals.csv refreshed ({len(merged)} rows)")
        return True
    except Exception as e:
        _warn("bulk_deals (nseindia)", e)
        return target.exists()


# =========================================================================
# 3) Fundamentals via yfinance (thin wrapper, symbol list capped)
# =========================================================================
def _shortlist_symbols(base: Path, cap: int = 120) -> list[str]:
    """Use the latest scored universe if present, else fall back to config.csv."""
    for name in ("latest_scores.csv", "latest_scores_validated.csv"):
        p = base / "output" / name
        if p.exists():
            try:
                df = pd.read_csv(p)
                if "Symbol" in df.columns and not df.empty:
                    return df["Symbol"].astype(str).head(cap).tolist()
            except Exception:
                pass
    p = base / "config.csv"
    if p.exists():
        try:
            df = pd.read_csv(p)
            col = "Symbol" if "Symbol" in df.columns else df.columns[0]
            return df[col].astype(str).head(cap).tolist()
        except Exception:
            pass
    return []


def fetch_fundamentals(data_dir: Path, base: Path, cap: int = 120) -> bool:
    target = data_dir / "fundamentals_latest.csv"
    if _is_fresh(target, FRESH_FUND_HOURS):
        _log(f"fundamentals_latest.csv fresh (<{FRESH_FUND_HOURS}h) — skipping fetch")
        return True
    symbols = _shortlist_symbols(base, cap=cap)
    if not symbols:
        _log("fundamentals: no shortlist yet — will populate on next run after scoring")
        return target.exists()
    try:
        # Reuse the tested wrapper in core.fundamental_factor.
        from core.fundamental_factor import fetch_fundamentals as _yf_fetch
        _log(f"fundamentals: yfinance fetch for {len(symbols)} symbols (~1s each, be patient)")
        raw = _yf_fetch(symbols, sleep=0.15)
        if raw.empty:
            raise RuntimeError("yfinance returned no rows (network / rate-limit?)")
        # Map raw -> schema fundamentals_overlay.py expects
        out = pd.DataFrame({
            "Symbol":            raw["Symbol"].astype(str),
            "ROE_TTM":           pd.to_numeric(raw.get("ROE"), errors="coerce"),
            "DebtToEquity":      pd.to_numeric(raw.get("DebtToEquity"), errors="coerce"),
            "EPS_Growth_YoY":    pd.to_numeric(raw.get("EarningsGrowth"), errors="coerce"),
            "PE_TTM":            pd.to_numeric(raw.get("PE"), errors="coerce"),
            "PEG":               pd.NA,
            "ProfitMargin":      pd.to_numeric(raw.get("ProfitMargin"), errors="coerce"),
            "PromoterPledgePct": pd.NA,
            "PE_Self_Median_3Y": pd.NA,
        })
        # If a user file already exists, prefer user values where present per symbol.
        if target.exists():
            try:
                prev = pd.read_csv(target)
                merged = pd.concat([prev, out], ignore_index=True)
                merged = merged.drop_duplicates(subset=["Symbol"], keep="first")
                out = merged
            except Exception:
                pass
        data_dir.mkdir(parents=True, exist_ok=True)
        out.to_csv(target, index=False)
        _log(f"fundamentals_latest.csv refreshed ({len(out)} rows, coverage varies per symbol)")
        return True
    except Exception as e:
        _warn("fundamentals (yfinance)", e)
        return target.exists()


# =========================================================================
# 4) Earnings calendar via yfinance Ticker.calendar
# =========================================================================
def fetch_earnings_calendar(data_dir: Path, base: Path, cap: int = 120,
                            horizon_days: int = 90) -> bool:
    target = data_dir / "earnings_calendar.csv"
    if _is_fresh(target, FRESH_EVENT_HOURS):
        _log(f"earnings_calendar.csv fresh (<{FRESH_EVENT_HOURS}h) — skipping fetch")
        return True
    symbols = _shortlist_symbols(base, cap=cap)
    if not symbols:
        _log("earnings: no shortlist yet — will populate on next run after scoring")
        return target.exists()
    try:
        import yfinance as yf
    except Exception as e:
        _warn("earnings (yfinance import)", e)
        return target.exists()

    rows: list[dict] = []
    horizon_end = pd.Timestamp.now() + pd.Timedelta(days=horizon_days)
    for sym in symbols:
        try:
            cal = yf.Ticker(sym).calendar
            if cal is None:
                continue
            # yfinance returns either a DataFrame (legacy) or a dict.
            dt = None
            if isinstance(cal, dict):
                v = cal.get("Earnings Date") or cal.get("Earnings Date High")
                if isinstance(v, (list, tuple)) and v:
                    dt = v[0]
                else:
                    dt = v
            else:
                try:
                    if "Earnings Date" in cal.index:
                        dt = cal.loc["Earnings Date"].iloc[0]
                except Exception:
                    pass
            dt = pd.to_datetime(dt, errors="coerce")
            if pd.isna(dt):
                continue
            if pd.Timestamp.now() <= dt <= horizon_end:
                rows.append({"Symbol": sym, "Event_Date": dt.strftime("%Y-%m-%d")})
        except Exception:
            continue
        time.sleep(0.05)

    if not rows:
        _log("earnings: no upcoming dates in horizon; leaving existing file untouched")
        return target.exists()
    out = pd.DataFrame(rows).drop_duplicates(subset=["Symbol"])
    data_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(target, index=False)
    _log(f"earnings_calendar.csv refreshed ({len(out)} upcoming events)")
    return True


# =========================================================================
# Top-level entry
# =========================================================================
def refresh_all(base: Path | None = None, only: Iterable[str] | None = None) -> dict:
    """Refresh whichever feeds are stale/missing.

    only: optional subset of {'fii_dii', 'bulk_deals', 'fundamentals', 'earnings'}
    Returns a small status dict, never raises.
    """
    base = Path(base) if base else Path(__file__).resolve().parent.parent
    data_dir = base / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    _log(f"refreshing optional overlay feeds into {data_dir}")
    wanted = set(only) if only else {"fii_dii", "bulk_deals", "fundamentals", "earnings"}
    status: dict[str, bool] = {}
    if "fii_dii" in wanted:
        status["fii_dii"] = fetch_fii_dii(data_dir)
    if "bulk_deals" in wanted:
        status["bulk_deals"] = fetch_bulk_deals(data_dir)
    if "fundamentals" in wanted:
        status["fundamentals"] = fetch_fundamentals(data_dir, base)
    if "earnings" in wanted:
        status["earnings"] = fetch_earnings_calendar(data_dir, base)
    ok = sum(1 for v in status.values() if v)
    _log(f"done — {ok}/{len(status)} feeds available (missing feeds keep the pipeline running quiet)")
    return status


if __name__ == "__main__":
    refresh_all()
