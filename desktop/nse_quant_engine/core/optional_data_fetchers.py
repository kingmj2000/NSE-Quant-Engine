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

import inspect
import io
import json
import os
import re
import sys
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
import re as _re


def _log(msg: str) -> None:
    print(f"[fetch] {msg}", flush=True)


def _warn(source: str, exc: BaseException) -> None:
    print(f"[fetch][warn] {source}: {type(exc).__name__}: {exc}", flush=True)


def _is_fresh(path: Path, max_age_hours: float) -> bool:
    if not path.exists():
        return False
    try:
        # A zero-byte cache is not a usable fresh cache. In particular, never
        # let an interrupted fundamentals write suppress the next fetch.
        if path.stat().st_size == 0:
            return False
        age_h = (time.time() - path.stat().st_mtime) / 3600.0
        return age_h < max_age_hours
    except Exception:
        return False


def _describe_source_error(exc: BaseException) -> tuple[str, str]:
    """Compact, human-readable reason for one source attempt failing.

    Returns (kind, message). `kind` includes dns, blocked, shape, network,
    library, and other.
    """
    text = str(exc)
    low = text.lower()
    if isinstance(exc, ImportError):
        return "library", "nselib not installed for this interpreter — run: python -m pip install nselib"
    if isinstance(exc, AttributeError) and "nselib" in low:
        return "library", "nselib API changed — see the resolver log for available names"
    if ("getaddrinfo failed" in low or "nameresolutionerror" in low
            or "could not resolve host" in low or "name or service not known" in low):
        host = ""
        m = _re.search(r"resolve '([^']+)'", text)
        if m:
            host = m.group(1)
        return "dns", f"hostname {host or 'lookup'} did not resolve (local DNS/network)"
    for code in ("503", "429", "403", "401"):
        if f"HTTP {code}" in text:
            return "blocked", f"HTTP {code} (source is rate-limiting or blocking us)"
    if "no tables found" in low or "no html parser succeeded" in low:
        return "shape", "page structure not recognised (site layout likely changed)"
    if "empty result" in low:
        return "shape", "returned no rows"
    if "timeout" in low or "timed out" in low:
        return "network", "timed out"
    if "connection" in low:
        return "network", "connection failed"
    msg = text.splitlines()[0][:120]
    return "other", f"{type(exc).__name__}: {msg}"


def _log_source_attempt(feed: str, name: str, exc: BaseException) -> None:
    """One fallback source failing is EXPECTED, not an error.

    These chains try several providers and use whichever answers first. Logging
    every attempt at the same volume as a real failure made a fully successful
    refresh read like a broken one. Attempts are tagged [try]; only an
    all-sources-failed outcome is a [warn].
    """
    kind, why = _describe_source_error(exc)
    print(f"[fetch][try] {feed}: '{name}' unavailable — {why}", flush=True)
    if kind == "dns":
        _DNS_FAILURES.add(name)


def _log_source_summary(feed: str, used: list[str], skipped: list[str]) -> None:
    if used:
        extra = f" ({len(skipped)} other source(s) unavailable)" if skipped else ""
        _log(f"{feed}: ok via {', '.join(used)}{extra}")


#: Sources that failed specifically because a hostname would not resolve. These
#: point at this machine's DNS rather than at the provider, so they are reported
#: once at the end with an actionable hint instead of being buried per-attempt.
_DNS_FAILURES: set[str] = set()


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
# Source policy — what is actually reachable from this machine
# =========================================================================
# Measured by a direct probe on 2026-08-31 (not assumed):
#
#   NSE /api/historical/fiidiiTradeReact  HTTP 503, 18,404-byte HTML block page
#                                         titled "Nse India". Same for 3-day and
#                                         20-day ranges. Kept in the chain but
#                                         attempted at most once per day.
#   NSE /api/fiidiiTradeReact             200 JSON, works. Exactly 2 rows (FII +
#                                         DII) for the last published day. The
#                                         ?date= parameter is ignored, so it
#                                         cannot backfill.
#   Moneycontrol                          200, but zero <table> tags in 105 KB —
#                                         JS-rendered. No parser can extract it.
#   Trendlyne                             405 "Human Verification" — bot-blocked.
#   Groww                                 502 / empty body.
#
# Conclusion: no range/backfill source is reachable. Retired adapters stay in
# this file (unused) so the evidence is not lost and re-enabling is one env var.
KNOWN_UNAVAILABLE_SOURCES: dict[str, str] = {
    "moneycontrol": "2026-08-31 probe: 200 OK but zero <table> tags in 105 KB — JS-rendered",
    "groww": "2026-08-31 probe: HTTP 502 / empty body",
    "trendlyne": "2026-08-31 probe: HTTP 405 'Human Verification' — bot-blocked",
}


def _try_all_sources() -> bool:
    """NSE_TRY_ALL_SOURCES=1 re-enables the retired adapters for a re-probe."""
    return str(os.environ.get("NSE_TRY_ALL_SOURCES", "")).strip() == "1"


def _filter_known_unavailable(feed: str, sources: list) -> list:
    """Drop retired sources from a fallback chain and say so once."""
    if _try_all_sources():
        return list(sources)
    dropped = [n for n, _ in sources if n in KNOWN_UNAVAILABLE_SOURCES]
    if dropped:
        _log(f"{feed}: {', '.join(dropped)} skipped — known unavailable "
             f"(see KNOWN_UNAVAILABLE_SOURCES)")
    return [(n, f) for n, f in sources if n not in KNOWN_UNAVAILABLE_SOURCES]


# --------- per-source attempt memory (data/source_health.json) --------------
SOURCE_HEALTH_FILE_NAME = "source_health.json"


def _source_health_load(data_dir: Path) -> dict:
    p = Path(data_dir) / SOURCE_HEALTH_FILE_NAME
    try:
        if not p.exists() or p.stat().st_size == 0:
            return {}
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _source_health_record(data_dir: Path, feed: str, source: str,
                          outcome: str) -> None:
    """Remember the last attempt date + outcome for one (feed, source)."""
    try:
        doc = _source_health_load(data_dir)
        doc.setdefault(feed, {})[source] = {
            "last_attempt_date": datetime.now().strftime("%Y-%m-%d"),
            "outcome": str(outcome),
        }
        if outcome == "ok":
            hist = doc.setdefault("_fetch_success_dates", {}).setdefault(feed, [])
            today = datetime.now().strftime("%Y-%m-%d")
            if today not in hist:
                hist.append(today)
                hist.sort()
                del hist[:-400]
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        (Path(data_dir) / SOURCE_HEALTH_FILE_NAME).write_text(
            json.dumps(doc, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[fetch][warn] source-health write: {e}", flush=True)


def _source_blocked_today(data_dir: Path, feed: str, source: str) -> bool:
    rec = _source_health_load(data_dir).get(feed, {}).get(source) or {}
    return (rec.get("last_attempt_date") == datetime.now().strftime("%Y-%m-%d")
            and rec.get("outcome") not in (None, "ok"))


def _fetch_success_dates(data_dir: Path, feed: str) -> list[str]:
    doc = _source_health_load(data_dir)
    return list((doc.get("_fetch_success_dates") or {}).get(feed) or [])


# --------- guarded CSV read -------------------------------------------------
def _read_csv_guarded(path: Path, label: str) -> pd.DataFrame | None:
    """Read a cache CSV, or return None when there is effectively no cache.

    A missing file, a zero-byte file, or an unparseable one (pandas raises
    EmptyDataError for a truly empty CSV) must all mean "no cache" and let the
    run continue with fresh data only. This exact failure mode has halted the
    pipeline once already, so pd.read_csv is never allowed to raise out of here.
    """
    try:
        p = Path(path)
        if not p.exists() or p.stat().st_size == 0:
            return None
        df = pd.read_csv(p)
        return None if df.empty else df
    except Exception as e:
        _log(f"{label}: existing cache unreadable ({type(e).__name__}) — "
             f"treating as no cache, continuing with fresh data only")
        return None


def missing_date_ranges(target: Path, days: int) -> list[tuple[str, str]]:
    """Contiguous business-day ranges absent from a dated cache CSV.

    Returned as [(from, to), ...] with inclusive 'YYYY-MM-DD' bounds, oldest
    first, covering the trailing `days` calendar days. An unreadable or missing
    cache yields the whole window as one range.
    """
    end = pd.Timestamp.now().normalize()
    start = end - pd.Timedelta(days=int(days))
    expected = pd.bdate_range(start, end)
    have: set = set()
    df = _read_csv_guarded(Path(target), "date-range scan")
    if df is not None and "Date" in df.columns:
        have = set(pd.to_datetime(df["Date"], errors="coerce")
                   .dropna().dt.normalize().dt.date)
    ranges: list[tuple[str, str]] = []
    run_start = None
    prev = None
    for d in expected:
        if d.date() in have:
            if run_start is not None:
                ranges.append((run_start.strftime("%Y-%m-%d"), prev.strftime("%Y-%m-%d")))
                run_start = None
            continue
        if run_start is None:
            run_start = d
        prev = d
    if run_start is not None and prev is not None:
        ranges.append((run_start.strftime("%Y-%m-%d"), prev.strftime("%Y-%m-%d")))
    return ranges


# --------- optional nselib adapter ------------------------------------------
_NSELIB_ENV_LOGGED = False


def _nselib_env_note() -> None:
    """Log which interpreter bound nselib — exactly once.

    The target machine has both Python 3.10 and 3.13 installed; a package
    visible to only one of them has already produced two false diagnoses.
    """
    global _NSELIB_ENV_LOGGED
    if _NSELIB_ENV_LOGGED:
        return
    _NSELIB_ENV_LOGGED = True
    try:
        import nselib  # noqa: F401
        ver = getattr(nselib, "__version__", "unknown")
    except Exception:
        ver = "not importable"
    _log(f"nselib version {ver} · python {sys.version.split()[0]} · {sys.executable}")


def _resolve_nselib_fn(candidates: Iterable[str]):
    """Bind the first matching nselib callable, case/underscore-insensitively."""
    import nselib  # ImportError propagates — classified as a 'library' error
    _nselib_env_note()
    modules = []
    for mod_name in ("capital_market", "derivatives"):
        try:
            modules.append(getattr(__import__(f"nselib.{mod_name}",
                                              fromlist=[mod_name]), "__dict__"))
        except Exception:
            continue
    modules.append(vars(nselib))
    wanted = [_norm_header(c) for c in candidates]
    available: list[str] = []
    for ns in modules:
        names = [n for n in ns if not n.startswith("_")]
        available.extend(names)
        lookup = {_norm_header(n): n for n in names}
        for w in wanted:
            if w in lookup and callable(ns[lookup[w]]):
                _log(f"nselib: bound '{lookup[w]}'")
                return ns[lookup[w]]
    raise AttributeError(
        f"nselib has none of {list(candidates)} — available: "
        f"{sorted(set(available))[:60]}")


def _fii_dii_from_nselib(sess=None) -> pd.DataFrame:
    fn = _resolve_nselib_fn(["fii_dii_trading_activity", "fii_dii_activity",
                             "fii_dii_trade_react"])
    try:
        params = list(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        params = []
    rows = None
    if any(p.lower() in ("from_date", "start_date", "period") for p in params):
        for frm, to in (missing_date_ranges(Path("data/fii_dii_daily.csv"), 30) or [])[-1:]:
            try:
                rows = fn(from_date=pd.Timestamp(frm).strftime("%d-%m-%Y"),
                          to_date=pd.Timestamp(to).strftime("%d-%m-%Y"))
                break
            except Exception:
                rows = None
    if rows is None:
        _log("nselib fii_dii: latest-day only, cannot backfill")
        rows = fn()
    if isinstance(rows, pd.DataFrame):
        rows = rows.to_dict("records")
    out = _normalize_nse_fiidii_rows(list(rows or []))
    if out is None or out.empty:
        raise RuntimeError("nselib returned empty FII/DII result")
    return out


def _bulk_from_nselib(sess=None, days: int = 30) -> pd.DataFrame:
    fn = _resolve_nselib_fn(["bulk_deal_data", "bulk_deals_data", "bulk_deals"])
    end = datetime.now()
    start = end - timedelta(days=int(days))
    try:
        raw = fn(from_date=start.strftime("%d-%m-%Y"),
                 to_date=end.strftime("%d-%m-%Y"))
    except TypeError:
        raw = fn()
    df = raw if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw or [])
    if df.empty:
        raise RuntimeError("nselib returned empty bulk-deals result")
    cmap = {_norm_header(c): c for c in df.columns}

    def pick(*names):
        for n in names:
            for k, v in cmap.items():
                if _norm_header(n) in k:
                    return v
        return None

    out = pd.DataFrame({
        "Date":     pd.to_datetime(df[pick("date")], errors="coerce", dayfirst=True),
        "Symbol":   df[pick("symbol")].astype(str).str.strip(),
        "Client":   df[pick("client")].astype(str).str.strip(),
        "Buy_Sell": df[pick("buysell", "buy")].astype(str).str.strip(),
        "Qty":      pd.to_numeric(df[pick("quantity", "qty")].astype(str).str.replace(",", ""), errors="coerce"),
        "Price":    pd.to_numeric(df[pick("price", "watp")].astype(str).str.replace(",", ""), errors="coerce"),
    }).dropna(subset=["Date", "Symbol"])
    if out.empty:
        raise RuntimeError("nselib bulk deals parsed but empty")
    return out



# =========================================================================
# 1) FII / DII normalizers retained for compatibility with local imports
# =========================================================================
# ---------------------------------------------------------------------------
# HTML parser helper — pandas.read_html needs a flavor. Try lxml first (fast),
# then html5lib (tolerant of Moneycontrol's malformed markup), then bs4.
# ---------------------------------------------------------------------------
def _try_read_html(text: str) -> list[pd.DataFrame]:
    """Parse HTML tables, trying each installed flavor in turn.

    The failure message distinguishes two very different situations that used to
    look identical:

      * NO PARSER INSTALLED — an environment problem, fixed by
        `pip install lxml html5lib beautifulsoup4`.
      * PARSED BUT NO TABLES — the page really has no tables, which usually means
        a block/consent page or JS-rendered content and needs a different
        approach entirely.

    Reporting only the last exception made a missing dependency read as "the site
    changed", sending the reader after the wrong problem.
    """
    missing: list[str] = []
    parsed_no_tables: list[str] = []
    other: list[str] = []
    for flavor in ("lxml", "html5lib", "bs4"):
        try:
            return pd.read_html(io.StringIO(text), flavor=flavor)
        except ImportError:
            missing.append(flavor)
        except ValueError as e:
            if "No tables found" in str(e):
                parsed_no_tables.append(flavor)
            else:
                other.append(f"{flavor}: {e}")
        except Exception as e:
            other.append(f"{flavor}: {type(e).__name__}: {e}")

    if parsed_no_tables:
        raise RuntimeError(
            f"page parsed but contains no tables (tried {', '.join(parsed_no_tables)}) "
            f"— likely a block/consent page or JS-rendered content, not a parser issue")
    if missing and not other:
        raise RuntimeError(
            f"no HTML parser installed (missing: {', '.join(missing)}) — "
            f"run: pip install lxml html5lib beautifulsoup4")
    raise RuntimeError(
        f"no HTML parser succeeded; missing={missing or 'none'}; errors={other}")


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
# 1) FII / DII daily flow — NSE official latest-only endpoint
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
    """Historical NSE FII/DII endpoint; one probe per day because it is blocked."""
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
    r = sess.get(url, timeout=25, headers=headers)
    if r.status_code >= 500:
        raise RuntimeError(f"HTTP {r.status_code} — historical NSE endpoint is blocked")
    r.raise_for_status()
    payload = r.json()
    rows = payload if isinstance(payload, list) else (payload.get("data") or [])
    out = _normalize_nse_fiidii_rows(rows)
    if out.empty:
        raise RuntimeError("NSE historical API returned empty payload")
    return out


def _report_flow_coverage(feed: str, target: Path, backfill_ok: bool) -> None:
    """Report honest coverage for institutional flows and bulk deals.

    Bulk-deal archives describe one day only. A missing row is therefore not a
    no-deal day unless that day was successfully fetched and returned zero rows.
    """
    try:
        df = _read_csv_guarded(target, f"{feed} coverage")
        if df is None or "Date" not in df.columns:
            return
        dates = pd.to_datetime(df["Date"], errors="coerce").dropna().dt.normalize()
        if dates.empty:
            return
        first, last = dates.min(), dates.max()
        expected = pd.bdate_range(first, last)
        have = set(dates.dt.date)
        missing = [d for d in expected if d.date() not in have]
        stale_days = (pd.Timestamp.now().normalize() - last).days
        if feed == "bulk_deals":
            success_dates = set(_fetch_success_dates(target.parent, feed))
            if missing:
                known_empty = [d for d in missing if d.strftime("%Y-%m-%d") in success_dates]
                unknown = len(missing) - len(known_empty)
                if known_empty:
                    _log(f"[gap] {feed}: {len(known_empty)} fetched day(s) had no deals")
                if unknown:
                    _log(f"[gap] {feed}: {unknown} day(s) unfetched — not labelled no-deal")
            if stale_days >= 3:
                _log(f"[gap] {feed}: newest row is {last:%Y-%m-%d} ({stale_days} days old)")
            return
        if missing and len(missing) > max(2, 0.15 * len(expected)):
            _log(f"[gap] {feed}: {len(missing)} of {len(expected)} business days "
                 f"missing between {first:%Y-%m-%d} and {last:%Y-%m-%d}")
            if not backfill_ok:
                _log(f"      no range/backfill source was available; only latest-day "
                     f"data can be added. Treat {feed} as incomplete context, not a series.")
        if stale_days >= 3:
            _log(f"[gap] {feed}: newest row is {last:%Y-%m-%d} ({stale_days} days old)")
    except Exception as exc:
        _log(f"{feed}: coverage check skipped ({type(exc).__name__})")


def fetch_fii_dii(data_dir: Path, keep_days: int = 90, force: bool = False) -> bool:
    target = data_dir / "fii_dii_daily.csv"
    if not force and _is_fresh(target, FRESH_FLOW_HOURS):
        _log(f"fii_dii_daily.csv fresh (<{FRESH_FLOW_HOURS}h) — skipping fetch")
        return True
    sess = _requests_session()
    # The official latest-only endpoint is the only source measured working.
    # Historical NSE is retained as a once-per-day re-probe, not as a retry loop.
    sources = _filter_known_unavailable("fii_dii", [
        ("nse-api", _fii_dii_from_nse_api),
        ("nse-archive", lambda s: _fii_dii_from_nse_archive(s, days=keep_days)),
        ("nselib", _fii_dii_from_nselib),
        ("moneycontrol", _fii_dii_from_moneycontrol),
        ("groww", _fii_dii_from_groww),
    ])
    collected: list[pd.DataFrame] = []
    used: list[str] = []
    skipped: list[str] = []
    for name, fn in sources:
        if name == "nse-archive" and _source_blocked_today(data_dir, "fii_dii", name):
            _log("[try] fii_dii: 'nse-archive' skipped — blocked earlier today (503 block page)")
            skipped.append(name)
            continue
        try:
            out = fn(sess)
            if out is None or out.empty:
                raise RuntimeError("empty result")
            collected.append(out)
            used.append(name)
            _source_health_record(data_dir, "fii_dii", name, "ok")
            _log(f"fii_dii source '{name}' ok ({len(out)} rows)")
            if name == "nse-archive":
                break
        except Exception as e:
            _source_health_record(data_dir, "fii_dii", name, "failed")
            _log_source_attempt("fii_dii", name, e)
            skipped.append(name)
    _log_source_summary("fii_dii", used, skipped)
    if not collected:
        _warn("fii_dii (all sources)", RuntimeError("no reachable FII/DII source succeeded"))
        return target.exists()
    merged = _merge_dated(target, pd.concat(collected, ignore_index=True), "Date", keep_days)
    data_dir.mkdir(parents=True, exist_ok=True)
    merged.to_csv(target, index=False)
    _log(f"fii_dii_daily.csv refreshed via {'+'.join(used)} ({len(merged)} rows in cache)")
    _report_flow_coverage("fii_dii", target, backfill_ok="nse-archive" in used)
    latest = pd.to_datetime(merged["Date"], errors="coerce").dropna().max()
    last_business = pd.Timestamp.now().normalize()
    while last_business.weekday() >= 5:
        last_business -= pd.Timedelta(days=1)
    if pd.notna(latest) and latest.normalize() < last_business:
        _log("[fetch][warn] fii_dii: latest published flow is older than the last completed business day.")
        _log("[fetch][warn] NSE publishes FII/DII after market processing; today's flow may not be available yet.")
        _log("[fetch][warn] Run the workflow after 19:00 IST to capture the latest published day.")
        _log("[fetch][warn] A missed day cannot be recovered from the latest-only endpoint; treat the series as incomplete.")
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


def fetch_bulk_deals(data_dir: Path, days: int = 30, keep_days: int = 60,
                     force: bool = False) -> bool:
    target = data_dir / "bulk_deals.csv"
    if not force and _is_fresh(target, FRESH_FLOW_HOURS):
        _log(f"bulk_deals.csv fresh (<{FRESH_FLOW_HOURS}h) — skipping fetch")
        return True
    sess = _requests_session()
    sources = _filter_known_unavailable("bulk_deals", [
        ("nse-archives", lambda: _bulk_from_nse_archive(sess)),
        ("nse-api", lambda: _bulk_from_nse_api(sess, days)),
        ("nselib", lambda: _bulk_from_nselib(sess, days)),
        ("bse", lambda: _bulk_from_bse(sess)),
    ])
    for name, fn in sources:
        try:
            out = fn()
            if out is None or out.empty:
                raise RuntimeError("empty result")
            merged = _merge_dated(target, out, "Date", keep_days)
            data_dir.mkdir(parents=True, exist_ok=True)
            merged.to_csv(target, index=False)
            _source_health_record(data_dir, "bulk_deals", name, "ok")
            _log(f"bulk_deals.csv refreshed via {name} ({len(out)} new rows, {len(merged)} in cache)")
            _report_flow_coverage("bulk_deals", target, backfill_ok=False)
            return True
        except Exception as e:
            _source_health_record(data_dir, "bulk_deals", name, "failed")
            _log_source_attempt("bulk_deals", name, e)
    _warn("bulk_deals (all sources)", RuntimeError("all configured bulk-deals sources failed"))
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


def _universe_symbols(base: Path) -> list[str]:
    """Return the full configured universe; never the capped fetch shortlist."""
    p = Path(base) / "config.csv"
    try:
        df = pd.read_csv(p)
        if "Symbol" not in df.columns:
            return []
        values = df["Symbol"].astype(str).str.strip()
        return list(dict.fromkeys(v for v in values if v and v.lower() != "nan"))
    except Exception:
        return []


def _fundamental_output_from_raw(raw: pd.DataFrame, build_quality_score) -> pd.DataFrame:
    scored = build_quality_score(raw)
    merged_cols = scored.merge(
        raw[[c for c in ("Symbol", "PE", "ROE", "DebtToEquity",
                         "EarningsGrowth", "ProfitMargin") if c in raw.columns]],
        on="Symbol", how="left", suffixes=("", "_raw"))
    def numeric(name: str) -> pd.Series:
        value = merged_cols.get(name)
        if value is None:
            return pd.Series(pd.NA, index=merged_cols.index, dtype="object")
        return pd.to_numeric(value, errors="coerce")
    return pd.DataFrame({
        "Symbol":            merged_cols["Symbol"].astype(str).str.strip(),
        "Fundamental_Score": numeric("Fundamental_Score"),
        "Fundamental_Coverage": numeric("Fundamental_Coverage"),
        "ROE_TTM":           numeric("ROE"),
        "DebtToEquity":      numeric("DebtToEquity"),
        "EPS_Growth_YoY":    numeric("EarningsGrowth"),
        "PE_TTM":            numeric("PE"),
        "PEG":               pd.NA,
        "ProfitMargin":      numeric("ProfitMargin"),
        "PromoterPledgePct": pd.NA,
        "PE_Self_Median_3Y": pd.NA,
    }).drop_duplicates(subset=["Symbol"], keep="last")


def fetch_fundamentals(data_dir: Path, base: Path, cap: int = 120,
                       force: bool = False) -> bool:
    target = data_dir / "fundamentals_latest.csv"
    if not force and _is_fresh(target, FRESH_FUND_HOURS):
        _log(f"fundamentals_latest.csv fresh (<{FRESH_FUND_HOURS}h) — skipping fetch")
        _write_health_row(data_dir, "fundamentals", _health_status_from_age(
            _cache_last_date(target, "As_Of") or datetime.now().strftime("%Y-%m-%d"),
            warn_days=8, fail_days=30), _cache_row_count(target), None,
            "cache fresh, skipped fetch")
        return True
    shortlist = _shortlist_symbols(base, cap=cap)
    universe = _universe_symbols(base)
    if not shortlist:
        _log("fundamentals: no shortlist yet — will populate on next run after scoring")
        _write_health_row(data_dir, "fundamentals", "amber", 0, None,
                          "no shortlist yet — run scoring first")
        return target.exists()
    cached = _read_csv_guarded(target, "fundamentals cache")
    if cached is not None and "Symbol" in cached.columns:
        cached["Symbol"] = cached["Symbol"].astype(str).str.strip()
    else:
        cached = pd.DataFrame()
    try:
        from core.fundamental_factor import fetch_fundamentals as _yf_fetch, build_quality_score
        _log(f"fundamentals: yfinance fetch for {len(shortlist)} symbols (~1s each, be patient)")
        raw = _yf_fetch(shortlist, sleep=0.15)
        if raw is None or raw.empty:
            fresh = pd.DataFrame({
                "Symbol": [str(s) for s in shortlist],
                "Fundamental_Score": [pd.NA] * len(shortlist),
                "Fundamental_Coverage": [0.0] * len(shortlist),
            })
            fetched_count = 0
        else:
            fresh = _fundamental_output_from_raw(raw, build_quality_score)
            fetched_count = int(fresh["Fundamental_Score"].notna().sum())
        today = datetime.now().strftime("%Y-%m-%d")
        if not cached.empty:
            # Fresh non-null cells win; null/failed lookups keep the prior value.
            all_cols = list(dict.fromkeys([*cached.columns, *fresh.columns]))
            combined = cached.reindex(columns=all_cols).set_index("Symbol")
            incoming = fresh.reindex(columns=all_cols).set_index("Symbol")
            # combine_first adds newly fetched symbols; update then replaces only
            # non-null incoming cells, so transient misses cannot erase history.
            out_index = combined.index.union(incoming.index)
            combined = combined.reindex(index=out_index).combine_first(incoming.reindex(index=out_index))
            combined.update(incoming.reindex(index=out_index))
            out = combined.reset_index()
        else:
            out = fresh.copy()
        # The cache is bounded by the full config universe, not today's 120-symbol shortlist.
        allowed = set(universe) if universe else set(shortlist)
        out = out[out["Symbol"].isin(allowed)].copy()
        if "As_Of" not in out.columns:
            out["As_Of"] = pd.NA
        if "Fundamentals_Stale_Days" not in out.columns:
            out["Fundamentals_Stale_Days"] = pd.NA
        fresh_symbols = set(fresh.loc[fresh["Fundamental_Score"].notna(), "Symbol"])
        out.loc[out["Symbol"].isin(fresh_symbols), "As_Of"] = today
        parsed = pd.to_datetime(out["As_Of"], errors="coerce")
        out["Fundamentals_Stale_Days"] = (pd.Timestamp(today) - parsed).dt.days
        out.loc[out["As_Of"].isna(), "Fundamentals_Stale_Days"] = pd.NA
        out = out.drop_duplicates("Symbol", keep="last").sort_values("Symbol").reset_index(drop=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        out.to_csv(target, index=False)
        reused = int((out["Fundamental_Score"].notna()).sum()) - fetched_count
        reused = max(0, reused)
        never = int(out["Fundamental_Score"].isna().sum())
        _log(f"fundamentals: {fetched_count} fetched, {reused} reused from cache, {never} never populated")
        status = "green" if int(out["Fundamental_Score"].notna().sum()) >= max(5, len(out) // 4) else "amber"
        _write_health_row(data_dir, "fundamentals", status, len(out), today,
                          f"{int(out['Fundamental_Score'].notna().sum())}/{len(out)} symbols scored")
        return True
    except Exception as e:
        _warn("fundamentals (yfinance)", e)
        _write_health_row(data_dir, "fundamentals",
                          "red" if cached.empty else "amber",
                          _cache_row_count(target), None,
                          f"fetch failed: {type(e).__name__}")
        return target.exists() or not cached.empty


# =========================================================================
# 4) Earnings calendar via yfinance Ticker.calendar
# =========================================================================
def fetch_earnings_calendar(data_dir: Path, base: Path, cap: int = 120,
                            horizon_days: int = 90, force: bool = False) -> bool:
    target = data_dir / "earnings_calendar.csv"
    if not force and _is_fresh(target, FRESH_EVENT_HOURS):
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


def fetch_delivery_pct(data_dir: Path, days: int = 5, keep_days: int = 365,
                       force: bool = False) -> bool:
    """Append-only cache. A failed fetch NEVER wipes the existing CSV."""
    target = data_dir / "delivery_pct_daily.csv"
    if not force and _is_fresh(target, FRESH_FLOW_HOURS):
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
                  keep_days: int = 400, force: bool = False) -> bool:
    """Fetch today's ATM IV per shortlisted F&O name; append to cache.
    A failed run NEVER wipes cached data."""
    target = data_dir / "iv_rank_daily.csv"
    if not force and _is_fresh(target, FRESH_FLOW_HOURS):
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
def refresh_all(base: Path | None = None, only: Iterable[str] | None = None,
                force: bool = False) -> dict:
    """Refresh whichever feeds are stale/missing.

    only: optional subset of {'fii_dii', 'bulk_deals', 'fundamentals',
                              'earnings', 'delivery_pct', 'iv_rank'}
    force: ignore cache freshness and re-fetch every requested feed.

    The default (force=False) is for the scheduled pipeline: a feed whose cache
    is still inside its freshness window is skipped, so a full run does not hammer
    NSE for data it already has.

    force=True is for an explicit human request ("Refresh optional feeds now").
    Pressing that button means the person wants current data regardless of when
    the last run happened, so honouring the cache would be ignoring the
    instruction. Freshness windows exist to avoid redundant automatic fetches,
    not to override a deliberate one.

    Returns a small status dict, never raises.
    """
    base = Path(base) if base else Path(__file__).resolve().parent.parent
    data_dir = base / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    if force:
        _log(f"FORCED refresh of optional overlay feeds into {data_dir} "
             f"— ignoring cache freshness (manual request)")
    else:
        _log(f"refreshing optional overlay feeds into {data_dir} "
             f"(fresh caches will be skipped)")
    wanted = set(only) if only else {"fii_dii", "bulk_deals", "fundamentals",
                                     "earnings", "delivery_pct", "iv_rank"}
    status: dict[str, bool] = {}

    # Every feed is wrapped. This function is documented as never raising, and
    # the orchestrator step relies on that to stay non-fatal — but only
    # delivery_pct and iv_rank used to be guarded, so a network error inside any
    # of the other four propagated out and could abort the run. On failure the
    # status falls back to "do we still have a usable cache on disk?", which is
    # the honest answer: a failed refresh is not the same as missing data.
    _feeds: list[tuple[str, str, callable]] = [
        ("fii_dii", "fii_dii_daily.csv",
         lambda: fetch_fii_dii(data_dir, force=force)),
        ("bulk_deals", "bulk_deals.csv",
         lambda: fetch_bulk_deals(data_dir, force=force)),
        ("fundamentals", "fundamentals_latest.csv",
         lambda: fetch_fundamentals(data_dir, base, force=force)),
        ("earnings", "earnings_calendar.csv",
         lambda: fetch_earnings_calendar(data_dir, base, force=force)),
        ("delivery_pct", "delivery_pct_daily.csv",
         lambda: fetch_delivery_pct(data_dir, force=force)),
        ("iv_rank", "iv_rank_daily.csv",
         lambda: fetch_iv_rank(data_dir, base, force=force)),
    ]
    for _name, _cache, _call in _feeds:
        if _name not in wanted:
            continue
        try:
            status[_name] = _call()
        except Exception as e:
            _warn(_name, e)
            status[_name] = (data_dir / _cache).exists()
    # Health rows for feeds that fetch above but don't self-report; and for
    # non-optional feeds that live outside this module (price cache, AMFI, news).
    try:
        if "fii_dii" in wanted:
            p = data_dir / "fii_dii_daily.csv"
            _write_health_row(data_dir, "fii_dii",
                              _health_status_from_age(_cache_last_date(p)),
                              _cache_row_count(p), _cache_last_date(p),
                              "" if status.get("fii_dii") else "fetch failed — cache reused if present")
        if "bulk_deals" in wanted:
            p = data_dir / "bulk_deals.csv"
            _write_health_row(data_dir, "bulk_deals",
                              _health_status_from_age(_cache_last_date(p)),
                              _cache_row_count(p), _cache_last_date(p),
                              "" if status.get("bulk_deals") else "fetch failed — cache reused if present")
        if "earnings" in wanted:
            p = data_dir / "earnings_calendar.csv"
            _write_health_row(data_dir, "earnings",
                              _health_status_from_age(_cache_last_date(p, "Event_Date"),
                                                       warn_days=14, fail_days=60),
                              _cache_row_count(p),
                              _cache_last_date(p, "Event_Date"),
                              "" if status.get("earnings") else "fetch failed — cache reused if present")
        # ── seed feeds this module does not fetch ──
        # Price cache
        price_meta = data_dir / "price_cache_meta.json"
        raw_prices = data_dir / "raw_prices_latest.csv"
        if price_meta.exists() or raw_prices.exists():
            src = price_meta if price_meta.exists() else raw_prices
            last = datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d")
            _write_health_row(data_dir, "price",
                              _health_status_from_age(last),
                              _cache_row_count(raw_prices), last,
                              f"from {src.name}")
        # AMFI standardized imports
        for feed, fname in (
            ("amfi_nav", "amfi_aum_source_standardized.csv"),
            ("amfi_ter", "amfi_ter_tracking_source_standardized.csv"),
        ):
            p = data_dir / fname
            if p.exists():
                last = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
                _write_health_row(data_dir, feed,
                                  _health_status_from_age(last, warn_days=7, fail_days=30),
                                  _cache_row_count(p), last, f"from {fname}")
        # News
        news = base / "output" / "news_market_context.md"
        if news.exists():
            last = datetime.fromtimestamp(news.stat().st_mtime).strftime("%Y-%m-%d")
            _write_health_row(data_dir, "news",
                              _health_status_from_age(last),
                              None, last, "news_market_context.md mtime")
    except Exception as e:
        _warn("data_health seeding", e)

    ok = sum(1 for v in status.values() if v)
    # Surface a DNS problem ONCE with an actionable hint. Buried inside a
    # per-source urllib3 traceback it reads like the provider is down, when the
    # fix is actually on this machine.
    if _DNS_FAILURES:
        _log(f"NOTE: {len(_DNS_FAILURES)} source(s) unreachable because a hostname "
             f"would not resolve: {', '.join(sorted(_DNS_FAILURES))}")
        _log("      That is DNS on this machine/network, not the source being down. "
             "Other feeds succeeded, so this is selective resolution failure.")
        _log("      Try: ipconfig /flushdns, a different network (phone hotspot), or "
             "check whether a proxy/VPN is required.")
        _DNS_FAILURES.clear()
    _log(f"done — {ok}/{len(status)} feeds available (missing feeds keep the pipeline running quiet)")
    return status


if __name__ == "__main__":
    refresh_all()
