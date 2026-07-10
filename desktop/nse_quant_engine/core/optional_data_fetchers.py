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
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd


# --------- data health snapshot --------------------------------------------
HEALTH_FILE_NAME = "data_health.json"


def _norm_header(s: str) -> str:
    """Normalize a CSV header for case/whitespace/punctuation-insensitive match."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _health_load(data_dir: Path) -> dict:
    p = data_dir / HEALTH_FILE_NAME
    if not p.exists():
        return {"generated_at": None, "feeds": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"generated_at": None, "feeds": {}}


def _health_write(data_dir: Path, doc: dict) -> None:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        doc["generated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        (data_dir / HEALTH_FILE_NAME).write_text(
            json.dumps(doc, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        print(f"[fetch][warn] health-write: {e}", flush=True)


def _health_status_from_age(last_date: str | None, warn_days: int = 2,
                            fail_days: int = 7) -> str:
    if not last_date:
        return "red"
    try:
        d = pd.to_datetime(last_date).normalize()
        age = (pd.Timestamp.now().normalize() - d).days
    except Exception:
        return "red"
    if age <= warn_days:
        return "green"
    if age <= fail_days:
        return "amber"
    return "red"


def _write_health_row(data_dir: Path, feed: str, status: str,
                      rows: int | None, last_date: str | None,
                      note: str = "") -> None:
    """Update one feed entry in data_health.json. Fail-soft, never raises."""
    try:
        doc = _health_load(data_dir)
        feeds = doc.setdefault("feeds", {})
        feeds[feed] = {
            "status": status,
            "rows": int(rows) if rows is not None else None,
            "last_date": last_date,
            "note": str(note or ""),
        }
        _health_write(data_dir, doc)
    except Exception as e:
        print(f"[fetch][warn] health-row {feed}: {e}", flush=True)


def _cache_last_date(csv_path: Path, col: str = "Date") -> str | None:
    try:
        if not csv_path.exists():
            return None
        df = pd.read_csv(csv_path, usecols=[col])
        d = pd.to_datetime(df[col], errors="coerce").dropna().max()
        return None if pd.isna(d) else d.strftime("%Y-%m-%d")
    except Exception:
        return None


def _cache_row_count(csv_path: Path) -> int:
    try:
        if not csv_path.exists():
            return 0
        return int(sum(1 for _ in csv_path.open("r", encoding="utf-8")) - 1)
    except Exception:
        return 0


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
# ---------------------------------------------------------------------------
# HTML parser helper — pandas.read_html needs a flavor. Try lxml first (fast),
# then html5lib (tolerant of Moneycontrol's malformed markup), then bs4.
# ---------------------------------------------------------------------------
def _try_read_html(text: str) -> list[pd.DataFrame]:
    last_exc: BaseException | None = None
    for flavor in ("lxml", "html5lib", "bs4"):
        try:
            return pd.read_html(io.StringIO(text), flavor=flavor)
        except Exception as e:  # ImportError, ValueError, XMLSyntaxError, ...
            last_exc = e
            continue
    raise RuntimeError(f"no HTML parser succeeded: {last_exc}")


def _normalize_flow_table(picked: pd.DataFrame) -> pd.DataFrame:
    if isinstance(picked.columns, pd.MultiIndex):
        picked.columns = [" ".join([str(x) for x in tup if str(x) != "nan"]).strip()
                          for tup in picked.columns.to_list()]
    cmap = {str(c).lower(): c for c in picked.columns}
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
    return pd.DataFrame({
        "Date": pd.to_datetime(picked[date_c], errors="coerce", dayfirst=True),
        "FII_Net_INR_Cr": pd.to_numeric(
            picked[fii_net_c].astype(str).str.replace(",", "").str.replace("−", "-"),
            errors="coerce"),
        "DII_Net_INR_Cr": pd.to_numeric(
            picked[dii_net_c].astype(str).str.replace(",", "").str.replace("−", "-"),
            errors="coerce"),
    }).dropna(subset=["Date"])


def _fii_dii_from_moneycontrol(sess) -> pd.DataFrame:
    url = "https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/index.php"
    r = sess.get(url, timeout=15)
    r.raise_for_status()
    tables = _try_read_html(r.text)
    picked = None
    for t in tables:
        cols = " ".join(str(c) for c in t.columns).lower()
        if "fii" in cols and "dii" in cols and "net" in cols:
            picked = t
            break
    if picked is None or picked.empty:
        raise RuntimeError("no FII/DII table found on Moneycontrol page")
    return _normalize_flow_table(picked)


def _fii_dii_from_groww(sess) -> pd.DataFrame:
    # Groww's public FII/DII widget is a JSON endpoint used by their web page.
    url = "https://groww.in/v1/api/stocks_data/v1/accord_points/exchange/NSE/type/index/BSEIndex_fii_dii"
    r = sess.get(url, timeout=15)
    r.raise_for_status()
    payload = r.json()
    rows = payload.get("data") or payload.get("results") or []
    if not rows:
        raise RuntimeError("groww returned no FII/DII rows")
    df = pd.DataFrame(rows)
    # heuristic mapping
    cmap = {c.lower(): c for c in df.columns}
    def pick(*names):
        for n in names:
            for k, v in cmap.items():
                if n in k:
                    return v
        return None
    date_c = pick("date")
    fii_c = pick("fii_net", "fiinet", "fii")
    dii_c = pick("dii_net", "diinet", "dii")
    if not (date_c and fii_c and dii_c):
        raise RuntimeError(f"groww: cannot map columns {list(df.columns)}")
    return pd.DataFrame({
        "Date": pd.to_datetime(df[date_c], errors="coerce"),
        "FII_Net_INR_Cr": pd.to_numeric(df[fii_c], errors="coerce"),
        "DII_Net_INR_Cr": pd.to_numeric(df[dii_c], errors="coerce"),
    }).dropna(subset=["Date"])


# =========================================================================
# 1) FII / DII daily flow — NSE official primary, then Moneycontrol/Groww
# =========================================================================
def _nse_warmup(sess) -> None:
    """Prime cookies that NSE's JSON APIs require. Best-effort; ignores errors."""
    for u in (
        "https://www.nseindia.com/",
        "https://www.nseindia.com/market-data/live-equity-market",
        "https://www.nseindia.com/reports/fii-dii",
    ):
        try:
            sess.get(u, timeout=15)
        except Exception:
            pass


def _normalize_nse_fiidii_rows(rows: list) -> pd.DataFrame:
    """Fold NSE fiidiiTradeReact rows into Date/FII_Net/DII_Net (INR crore).

    NSE returns per-category per-date entries like:
      {category: 'FII/FPI **', date: '09-Jul-2026', buyValue, sellValue, netValue}
      {category: 'DII **',     date: '09-Jul-2026', ...}
    We sum netValue per (date, side).
    """
    if not rows:
        return pd.DataFrame(columns=["Date", "FII_Net_INR_Cr", "DII_Net_INR_Cr"])
    df = pd.DataFrame(rows)
    cmap = {str(c).lower(): c for c in df.columns}
    def pick(*names):
        for n in names:
            for k, v in cmap.items():
                if n == k or n in k:
                    return v
        return None
    date_c = pick("date")
    cat_c  = pick("category")
    net_c  = pick("netvalue", "net_value", "netval", "net")
    if not (date_c and cat_c and net_c):
        raise RuntimeError(f"NSE FII/DII: cannot map columns in {list(df.columns)}")
    df["_date"] = pd.to_datetime(df[date_c], errors="coerce", dayfirst=True)
    df["_net"]  = pd.to_numeric(
        df[net_c].astype(str).str.replace(",", "").str.replace("−", "-"),
        errors="coerce",
    )
    df["_cat"] = df[cat_c].astype(str).str.upper()
    df = df.dropna(subset=["_date"])
    fii_mask = df["_cat"].str.contains("FII") | df["_cat"].str.contains("FPI")
    dii_mask = df["_cat"].str.contains("DII")
    fii = df[fii_mask].groupby("_date", as_index=False)["_net"].sum().rename(
        columns={"_date": "Date", "_net": "FII_Net_INR_Cr"})
    dii = df[dii_mask].groupby("_date", as_index=False)["_net"].sum().rename(
        columns={"_date": "Date", "_net": "DII_Net_INR_Cr"})
    out = pd.merge(fii, dii, on="Date", how="outer").sort_values("Date")
    return out


def _fii_dii_from_nse_api(sess) -> pd.DataFrame:
    """NSE's live FII/DII trade activity JSON. Returns 1–2 most-recent trading days."""
    _nse_warmup(sess)
    url = "https://www.nseindia.com/api/fiidiiTradeReact"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/reports/fii-dii",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    last_exc: BaseException | None = None
    for _ in range(2):
        try:
            r = sess.get(url, timeout=20, headers=headers)
            if r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            payload = r.json()
            rows = payload if isinstance(payload, list) else (payload.get("data") or [])
            out = _normalize_nse_fiidii_rows(rows)
            if out.empty:
                raise RuntimeError("NSE live API returned empty payload")
            return out
        except Exception as e:
            last_exc = e
            time.sleep(2.0)
    raise RuntimeError(f"NSE live FII/DII failed after retry: {last_exc}")


def _fii_dii_from_nse_archive(sess, days: int = 90) -> pd.DataFrame:
    """NSE historical FII/DII endpoint — up to ~90 days in one shot."""
    _nse_warmup(sess)
    end = datetime.now()
    start = end - timedelta(days=days)
    url = ("https://www.nseindia.com/api/historical/fiidiiTradeReact"
           f"?from={start.strftime('%d-%m-%Y')}&to={end.strftime('%d-%m-%Y')}")
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/reports/fii-dii",
        "X-Requested-With": "XMLHttpRequest",
    }
    last_exc: BaseException | None = None
    for _ in range(2):
        try:
            r = sess.get(url, timeout=25, headers=headers)
            if r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            payload = r.json()
            rows = payload if isinstance(payload, list) else (payload.get("data") or [])
            out = _normalize_nse_fiidii_rows(rows)
            if out.empty:
                raise RuntimeError("NSE historical API returned empty payload")
            return out
        except Exception as e:
            last_exc = e
            time.sleep(2.0)
    raise RuntimeError(f"NSE historical FII/DII failed after retry: {last_exc}")


def fetch_fii_dii(data_dir: Path, keep_days: int = 90) -> bool:
    target = data_dir / "fii_dii_daily.csv"
    if _is_fresh(target, FRESH_FLOW_HOURS):
        _log(f"fii_dii_daily.csv fresh (<{FRESH_FLOW_HOURS}h) — skipping fetch")
        return True
    sess = _requests_session()
    # Order matters: NSE first (official + free). Union what we can — nse-api
    # only returns 1–2 rows, nse-archive backfills ~90 days.
    sources = [
        ("nse-api",      _fii_dii_from_nse_api),
        ("nse-archive",  lambda s: _fii_dii_from_nse_archive(s, days=keep_days)),
        ("moneycontrol", _fii_dii_from_moneycontrol),
        ("groww",        _fii_dii_from_groww),
    ]
    collected: list[pd.DataFrame] = []
    used: list[str] = []
    for name, fn in sources:
        try:
            out = fn(sess)
            if out is None or out.empty:
                raise RuntimeError("empty result")
            collected.append(out)
            used.append(name)
            _log(f"fii_dii source '{name}' ok ({len(out)} rows)")
            # If archive succeeded we already have ~90 days; stop hammering fallbacks.
            if name == "nse-archive":
                break
        except Exception as e:
            _log(f"fii_dii source '{name}' failed: {type(e).__name__}: {e}")
            continue
    if not collected:
        _warn("fii_dii (all sources)",
              RuntimeError("nse-api + nse-archive + moneycontrol + groww all failed"))
        return target.exists()
    union = pd.concat(collected, ignore_index=True)
    merged = _merge_dated(target, union, "Date", keep_days)
    data_dir.mkdir(parents=True, exist_ok=True)
    merged.to_csv(target, index=False)
    _log(f"fii_dii_daily.csv refreshed via {'+'.join(used)} ({len(merged)} rows in cache)")
    return True


# =========================================================================
# 2) Bulk deals — NSE archives CSV (primary), NSE JSON API (fallback),
#    BSE bulk deals JSON (last-resort cross-exchange fallback).
# =========================================================================
def _bulk_from_nse_archive(sess) -> pd.DataFrame:
    """Static daily CSV — no cookie handshake required. Covers today only."""
    url = "https://archives.nseindia.com/content/equities/bulk.csv"
    r = sess.get(url, timeout=15, headers={"Referer": "https://www.nseindia.com/"})
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    cmap = {c.strip().lower(): c for c in df.columns}
    def pick(*names):
        for n in names:
            for k, v in cmap.items():
                if all(part in k for part in n.split()):
                    return v
        return None
    out = pd.DataFrame({
        "Date":     pd.to_datetime(df[pick("date")], errors="coerce", dayfirst=True),
        "Symbol":   df[pick("symbol")].astype(str).str.strip(),
        "Client":   df[pick("client")].astype(str).str.strip(),
        "Buy_Sell": df[pick("buy")].astype(str).str.strip(),
        "Qty":      pd.to_numeric(df[pick("quantity")].astype(str).str.replace(",", ""), errors="coerce"),
        "Price":    pd.to_numeric(df[pick("price")].astype(str).str.replace(",", ""), errors="coerce"),
    }).dropna(subset=["Date", "Symbol"])
    if out.empty:
        raise RuntimeError("NSE archive CSV parsed but empty")
    return out


def _bulk_from_nse_api(sess, days: int) -> pd.DataFrame:
    api_headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/report-detail/display-bulk-and-block-deals",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "X-Requested-With": "XMLHttpRequest",
    }
    sess.get("https://www.nseindia.com/", timeout=15)
    sess.get("https://www.nseindia.com/market-data/live-equity-market", timeout=15)
    sess.get("https://www.nseindia.com/report-detail/display-bulk-and-block-deals", timeout=15)
    end = datetime.now()
    start = end - timedelta(days=days)
    url = (f"https://www.nseindia.com/api/historical/bulk-deals"
           f"?from={start.strftime('%d-%m-%Y')}&to={end.strftime('%d-%m-%Y')}")
    last_exc: BaseException | None = None
    payload = None
    for _ in range(2):
        try:
            r = sess.get(url, timeout=20, headers=api_headers)
            if r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            payload = r.json()
            break
        except Exception as e:
            last_exc = e
            time.sleep(2.0)
    if payload is None:
        raise RuntimeError(f"NSE JSON API failed after retry: {last_exc}")
    rows = payload.get("data") or []
    if not rows:
        raise RuntimeError("NSE returned empty bulk-deals payload")
    def _get(row, *keys, default=None):
        for k in keys:
            if k in row and row[k] not in (None, "", "-"):
                return row[k]
        return default
    out = pd.DataFrame([{
        "Date":     _get(r, "BD_DT_DATE", "date"),
        "Symbol":   _get(r, "BD_SYMBOL", "symbol"),
        "Client":   _get(r, "BD_CLIENT_NAME", "clientName"),
        "Buy_Sell": _get(r, "BD_BUY_SELL", "buySell"),
        "Qty":      _get(r, "BD_QTY_TRD", "quantityTraded"),
        "Price":    _get(r, "BD_TP_WATP", "watp"),
    } for r in rows])
    out["Date"]  = pd.to_datetime(out["Date"], errors="coerce", dayfirst=True)
    out = out.dropna(subset=["Date", "Symbol"])
    out["Qty"]   = pd.to_numeric(out["Qty"].astype(str).str.replace(",", ""), errors="coerce")
    out["Price"] = pd.to_numeric(out["Price"].astype(str).str.replace(",", ""), errors="coerce")
    return out


def _bulk_from_bse(sess) -> pd.DataFrame:
    end = datetime.now()
    start = end - timedelta(days=7)
    url = ("https://api.bseindia.com/BseIndiaAPI/api/BulkDeals/w"
           f"?Fdate={start.strftime('%Y-%m-%d')}&Tdate={end.strftime('%Y-%m-%d')}"
           "&Bflag=B&pageno=1")
    r = sess.get(url, timeout=15, headers={
        "Referer": "https://www.bseindia.com/",
        "Accept": "application/json, text/plain, */*",
    })
    r.raise_for_status()
    payload = r.json()
    rows = payload.get("Table") or payload.get("data") or []
    if not rows:
        raise RuntimeError("BSE returned empty bulk-deals payload")
    df = pd.DataFrame(rows)
    cmap = {c.lower(): c for c in df.columns}
    def pick(*names):
        for n in names:
            for k, v in cmap.items():
                if n in k:
                    return v
        return None
    out = pd.DataFrame({
        "Date":     pd.to_datetime(df[pick("date", "dt")], errors="coerce"),
        "Symbol":   df[pick("scrip_name", "scripname", "symbol", "scrip")].astype(str).str.strip(),
        "Client":   df[pick("client")].astype(str).str.strip(),
        "Buy_Sell": df[pick("deal", "buy")].astype(str).str.strip().str[:1].str.upper(),
        "Qty":      pd.to_numeric(df[pick("qty", "quantity")], errors="coerce"),
        "Price":    pd.to_numeric(df[pick("price", "rate")], errors="coerce"),
    }).dropna(subset=["Date", "Symbol"])
    if out.empty:
        raise RuntimeError("BSE parsed but empty")
    return out


def fetch_bulk_deals(data_dir: Path, days: int = 30, keep_days: int = 60) -> bool:
    target = data_dir / "bulk_deals.csv"
    if _is_fresh(target, FRESH_FLOW_HOURS):
        _log(f"bulk_deals.csv fresh (<{FRESH_FLOW_HOURS}h) — skipping fetch")
        return True
    sess = _requests_session()
    sources = [
        ("nse-archives", lambda: _bulk_from_nse_archive(sess)),
        ("nse-api",      lambda: _bulk_from_nse_api(sess, days)),
        ("bse",          lambda: _bulk_from_bse(sess)),
    ]
    for name, fn in sources:
        try:
            out = fn()
            merged = _merge_dated(target, out, "Date", keep_days)
            data_dir.mkdir(parents=True, exist_ok=True)
            merged.to_csv(target, index=False)
            _log(f"bulk_deals.csv refreshed via {name} ({len(out)} new rows, {len(merged)} in cache)")
            return True
        except Exception as e:
            _log(f"bulk_deals source '{name}' failed: {type(e).__name__}: {e}")
            continue
    _warn("bulk_deals (all sources)", RuntimeError("nse-archives + nse-api + bse all failed"))
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
# 5) Delivery % daily (NSE sec_bhavdata_full) — appended cache, fail-soft
# =========================================================================
def _bhavcopy_urls(d: datetime) -> list[str]:
    """Current NSE archive host first, legacy host as fallback."""
    dd = d.strftime("%d%m%Y")
    return [
        f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{dd}.csv",
        f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{dd}.csv",
    ]


def parse_delivery_bhavcopy(text: str) -> pd.DataFrame:
    """Parse NSE sec_bhavdata_full CSV text into (Date, Symbol, Delivery_Pct).
    Header detection is whitespace/case/punctuation-insensitive so a future
    rename degrades gracefully instead of hard-failing. Exposed for tests."""
    df = pd.read_csv(io.StringIO(text))
    # NSE headers ship with leading spaces: ' SYMBOL', ' SERIES', ' DATE1'
    df.columns = [str(c).strip() for c in df.columns]
    nmap: dict[str, str] = {}
    for c in df.columns:
        nmap[_norm_header(c)] = c

    def pick(*keys: str) -> str | None:
        for k in keys:
            nk = _norm_header(k)
            if nk in nmap:
                return nmap[nk]
        # loose contains match on normalized keys
        for k in keys:
            nk = _norm_header(k)
            for nh, orig in nmap.items():
                if nk and nk in nh:
                    return orig
        return None

    sym_c   = pick("symbol")
    ser_c   = pick("series")
    date_c  = pick("date1", "date")
    # deliverable percentage — several historical spellings
    dely_c  = pick("delivper", "dlyqttotradedqty", "delivperc", "percdlyqt")
    if not (sym_c and date_c):
        raise RuntimeError(f"bhavcopy: missing SYMBOL/DATE columns in {list(df.columns)[:16]}")
    if not dely_c:
        # Fallback: compute from DELIV_QTY / TTL_TRD_QNTY when % column absent
        num_c = pick("delivqty", "delivqty")
        den_c = pick("ttltrdqnty", "ttltrdqty", "totaltradedquantity")
        if not (num_c and den_c):
            raise RuntimeError(f"bhavcopy: missing DELIV_PER and DELIV_QTY/TTL_TRD_QNTY in {list(df.columns)[:16]}")
        num = pd.to_numeric(df[num_c].astype(str).str.replace(",", ""), errors="coerce")
        den = pd.to_numeric(df[den_c].astype(str).str.replace(",", ""), errors="coerce")
        deliv = (num / den.replace(0, pd.NA)) * 100.0
    else:
        deliv = pd.to_numeric(
            df[dely_c].astype(str).str.replace("%", "").str.strip(),
            errors="coerce")

    out = pd.DataFrame({
        "Date":   pd.to_datetime(df[date_c].astype(str).str.strip(), errors="coerce", dayfirst=True),
        "Symbol": df[sym_c].astype(str).str.strip(),
        "Series": df[ser_c].astype(str).str.strip() if ser_c else "",
        "Delivery_Pct": deliv,
    }).dropna(subset=["Date", "Symbol", "Delivery_Pct"])
    if ser_c:
        out = out[out["Series"].astype(str).str.strip().isin(["EQ", "BE", ""])]
    return out.drop(columns=["Series"], errors="ignore").reset_index(drop=True)


def _fetch_delivery_pct_day(sess, d: datetime) -> pd.DataFrame:
    last_exc: BaseException | None = None
    headers = {
        "Accept": "text/csv, text/plain, */*",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://www.nseindia.com/all-reports",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Dest": "empty",
    }
    for url in _bhavcopy_urls(d):
        for attempt in range(2):
            try:
                r = sess.get(url, timeout=25, headers=headers)
                if r.status_code == 404:
                    raise RuntimeError("404 (holiday / not yet published)")
                if r.status_code >= 500:
                    raise RuntimeError(f"HTTP {r.status_code}")
                r.raise_for_status()
                return parse_delivery_bhavcopy(r.text)
            except Exception as e:
                last_exc = e
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"bhavcopy {d:%d-%m-%Y} failed on both hosts: {last_exc}")


def fetch_delivery_pct(data_dir: Path, days: int = 5, keep_days: int = 365) -> bool:
    """Append-only cache. A failed fetch NEVER wipes the existing CSV."""
    target = data_dir / "delivery_pct_daily.csv"
    if _is_fresh(target, FRESH_FLOW_HOURS):
        _log(f"delivery_pct_daily.csv fresh (<{FRESH_FLOW_HOURS}h) — skipping fetch")
        _write_health_row(data_dir, "delivery_pct",
                          _health_status_from_age(_cache_last_date(target)),
                          _cache_row_count(target), _cache_last_date(target),
                          "cache fresh, skipped fetch")
        return True
    sess = _requests_session()
    _nse_warmup(sess)

    collected: list[pd.DataFrame] = []
    end = datetime.now()
    for i in range(1, days + 1):
        d = end - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        try:
            df = _fetch_delivery_pct_day(sess, d)
            if not df.empty:
                collected.append(df)
                _log(f"delivery% for {d:%Y-%m-%d}: {len(df)} symbols")
        except Exception as e:
            _log(f"delivery% {d:%Y-%m-%d} skipped: {type(e).__name__}: {e}")
            continue

    if not collected:
        _warn("delivery_pct (bhavcopy)", RuntimeError("no trading days fetched"))
        last = _cache_last_date(target)
        if last:
            _log(f"reused cached delivery_pct ({_cache_row_count(target)} rows, last={last})")
        _write_health_row(data_dir, "delivery_pct",
                          _health_status_from_age(last),
                          _cache_row_count(target), last,
                          "fresh fetch failed — bhavcopy unreachable" if not last
                          else "fresh fetch failed — using cached data")
        return target.exists()

    new_df = pd.concat(collected, ignore_index=True)
    # append + dedupe on (Date, Symbol); NEVER overwrite good cache on failure
    if target.exists():
        try:
            old = pd.read_csv(target)
            merged = pd.concat([old, new_df], ignore_index=True)
        except Exception:
            merged = new_df
    else:
        merged = new_df
    merged["Date"] = pd.to_datetime(merged["Date"], errors="coerce")
    merged = merged.dropna(subset=["Date", "Symbol"])
    merged = merged.drop_duplicates(subset=["Date", "Symbol"], keep="last")
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=keep_days)
    merged = merged[merged["Date"] >= cutoff].sort_values(["Date", "Symbol"])
    merged["Date"] = merged["Date"].dt.strftime("%Y-%m-%d")
    data_dir.mkdir(parents=True, exist_ok=True)
    merged.to_csv(target, index=False)  # NEVER overwrite good cache on failure
    _log(f"delivery_pct_daily.csv refreshed ({len(merged)} rows in cache)")
    _write_health_row(data_dir, "delivery_pct", "green",
                      len(merged), _cache_last_date(target),
                      f"{len(new_df)} new rows from {len(collected)} trading day(s)")
    return True



# =========================================================================
# 6) IV Rank daily (NSE option-chain-equities) — appended cache, fail-soft
# =========================================================================
def _nse_browser_session():
    """Full browser-style session — NSE JSON APIs refuse anything less."""
    import requests
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    return s


def _nse_option_chain_warmup(sess) -> None:
    """Prime cookies for the option-chain JSON API. Best-effort."""
    for u in (
        "https://www.nseindia.com/",
        "https://www.nseindia.com/option-chain",
        "https://www.nseindia.com/market-data/live-equity-market",
    ):
        try:
            sess.get(u, timeout=15)
            time.sleep(0.4)
        except Exception:
            pass


def _iv_rank_from_option_chain(sess, symbol: str) -> float | None:
    url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
    last_exc: BaseException | None = None
    backoffs = [1.0, 3.0, 7.0]
    for attempt in range(3):
        try:
            r = sess.get(url, timeout=20, headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.nseindia.com/option-chain",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                "X-Requested-With": "XMLHttpRequest",
            })
            if r.status_code in (401, 403, 429):
                raise RuntimeError(f"blocked HTTP {r.status_code}")
            if r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            payload = r.json()
            rec = (payload.get("records") or {})
            underlying = float(rec.get("underlyingValue") or 0)
            data = rec.get("data") or []
            if underlying <= 0 or not data:
                return None
            atm = min(data, key=lambda row: abs(float(row.get("strikePrice", 0)) - underlying))
            ce_iv = float(((atm.get("CE") or {}).get("impliedVolatility")) or 0)
            pe_iv = float(((atm.get("PE") or {}).get("impliedVolatility")) or 0)
            iv = max(ce_iv, pe_iv)
            return iv if iv > 0 else None
        except Exception as e:
            last_exc = e
            time.sleep(backoffs[attempt])
    raise RuntimeError(f"option-chain {symbol} failed after 3 tries: {last_exc}")


def _iv_rank_percentile(series: pd.Series, current: float, lookback: int = 252) -> float:
    s = pd.to_numeric(series.tail(lookback), errors="coerce").dropna()
    if s.empty:
        return float("nan")
    return float((s < current).mean() * 100.0)


def fetch_iv_rank(data_dir: Path, base: Path, cap: int = 60,
                  keep_days: int = 400) -> bool:
    """Fetch today's ATM IV per shortlisted F&O name; append to cache.
    A failed run NEVER wipes cached data."""
    target = data_dir / "iv_rank_daily.csv"
    if _is_fresh(target, FRESH_FLOW_HOURS):
        _log(f"iv_rank_daily.csv fresh (<{FRESH_FLOW_HOURS}h) — skipping fetch")
        _write_health_row(data_dir, "iv_rank",
                          _health_status_from_age(_cache_last_date(target)),
                          _cache_row_count(target), _cache_last_date(target),
                          "cache fresh, skipped fetch")
        return True

    symbols = _shortlist_symbols(base, cap=cap)
    symbols = [s.replace(".NS", "").strip().upper() for s in symbols if s]
    if not symbols:
        _log("iv_rank: no shortlist yet — skipping")
        _write_health_row(data_dir, "iv_rank", "amber", 0, None,
                          "no shortlist yet — run scoring first")
        return target.exists()

    sess = _nse_browser_session()
    _nse_option_chain_warmup(sess)
    time.sleep(1.0)

    today = pd.Timestamp.now().normalize()
    old = pd.DataFrame()
    if target.exists():
        try:
            old = pd.read_csv(target)
            old["Date"] = pd.to_datetime(old["Date"], errors="coerce")
        except Exception:
            old = pd.DataFrame()

    rows: list[dict] = []
    hit = miss = 0
    rewarmed = False
    for sym in symbols:
        try:
            iv = _iv_rank_from_option_chain(sess, sym)
            if iv is None:
                miss += 1
                continue
            hist = old[old["Symbol"].astype(str) == sym]["IV"] if not old.empty and "IV" in old.columns else pd.Series(dtype=float)
            rank = _iv_rank_percentile(hist, iv)
            rows.append({"Date": today.strftime("%Y-%m-%d"),
                         "Symbol": sym, "IV": iv, "IV_Rank": rank})
            hit += 1
        except Exception as e:
            miss += 1
            _log(f"iv_rank {sym} failed: {type(e).__name__}: {e}")
            if not rewarmed and hit == 0 and miss >= 3:
                _log("iv_rank: re-warming NSE session after early failures")
                _nse_option_chain_warmup(sess)
                time.sleep(1.0)
                rewarmed = True
            continue
        time.sleep(0.6)  # polite pacing

    if not rows:
        _warn("iv_rank (option-chain)",
              RuntimeError(f"no symbols returned IV ({miss} misses) — NSE likely blocked the session"))
        last = _cache_last_date(target)
        if last:
            _log(f"reused cached iv_rank ({_cache_row_count(target)} rows, last={last})")
        _write_health_row(data_dir, "iv_rank",
                          "red" if not last else _health_status_from_age(last),
                          _cache_row_count(target), last,
                          f"NSE option-chain blocked ({miss} misses)")
        return target.exists()

    new_df = pd.DataFrame(rows)
    merged = pd.concat([old, new_df], ignore_index=True) if not old.empty else new_df
    merged["Date"] = pd.to_datetime(merged["Date"], errors="coerce")
    merged = merged.dropna(subset=["Date", "Symbol"])
    merged = merged.drop_duplicates(subset=["Date", "Symbol"], keep="last")
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=keep_days)
    merged = merged[merged["Date"] >= cutoff].sort_values(["Date", "Symbol"])
    merged["Date"] = merged["Date"].dt.strftime("%Y-%m-%d")
    data_dir.mkdir(parents=True, exist_ok=True)
    merged.to_csv(target, index=False)  # NEVER overwrite good cache on failure
    _log(f"iv_rank_daily.csv refreshed (hit={hit} miss={miss}, {len(merged)} rows in cache)")
    _write_health_row(data_dir, "iv_rank",
                      "green" if hit >= max(1, len(symbols) // 4) else "amber",
                      len(merged), _cache_last_date(target),
                      f"today hit={hit} miss={miss}")
    return True



# =========================================================================
# Top-level entry
# =========================================================================
def refresh_all(base: Path | None = None, only: Iterable[str] | None = None) -> dict:
    """Refresh whichever feeds are stale/missing.

    only: optional subset of {'fii_dii', 'bulk_deals', 'fundamentals',
                              'earnings', 'delivery_pct', 'iv_rank'}
    Returns a small status dict, never raises.
    """
    base = Path(base) if base else Path(__file__).resolve().parent.parent
    data_dir = base / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    _log(f"refreshing optional overlay feeds into {data_dir}")
    wanted = set(only) if only else {"fii_dii", "bulk_deals", "fundamentals",
                                     "earnings", "delivery_pct", "iv_rank"}
    status: dict[str, bool] = {}
    if "fii_dii" in wanted:
        status["fii_dii"] = fetch_fii_dii(data_dir)
    if "bulk_deals" in wanted:
        status["bulk_deals"] = fetch_bulk_deals(data_dir)
    if "fundamentals" in wanted:
        status["fundamentals"] = fetch_fundamentals(data_dir, base)
    if "earnings" in wanted:
        status["earnings"] = fetch_earnings_calendar(data_dir, base)
    if "delivery_pct" in wanted:
        try:
            status["delivery_pct"] = fetch_delivery_pct(data_dir)
        except Exception as e:
            _warn("delivery_pct", e)
            status["delivery_pct"] = (data_dir / "delivery_pct_daily.csv").exists()
    if "iv_rank" in wanted:
        try:
            status["iv_rank"] = fetch_iv_rank(data_dir, base)
        except Exception as e:
            _warn("iv_rank", e)
            status["iv_rank"] = (data_dir / "iv_rank_daily.csv").exists()
    ok = sum(1 for v in status.values() if v)
    _log(f"done — {ok}/{len(status)} feeds available (missing feeds keep the pipeline running quiet)")
    return status


if __name__ == "__main__":
    refresh_all()
