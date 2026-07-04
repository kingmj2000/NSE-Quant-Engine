"""
ETF TER / Tracking Auto Fetcher - Stage 3.5.9
=============================================

Fetches ETF TER and tracking error / tracking difference disclosures and writes a
normal import file into data/etf_metadata_imports/ for etf_metadata_enricher.py.

Why separate from AUM:
    AUM, TER, and tracking data are different AMFI disclosure streams. Keeping
    them separate makes failures easier to debug and prevents one bad source from
    contaminating all ETF metadata.

Primary automatic sources:
    - AMFI TER of MF Schemes via amfipy TER client / Excel download methods.
    - AMFI Tracking Error / Tracking Difference via amfipy tracking client.

Manual fallback:
    Place .csv/.xlsx/.xls files in data/etf_metadata_imports with a filename that
    includes one of:
        ter_tracking_manual
        amfi_ter_tracking_manual
        tracking_manual
        ter_manual

Output consumed by etf_metadata_enricher.py:
    data/etf_metadata_imports/auto_amfi_ter_tracking_latest.csv

Other outputs:
    data/amfi_ter_tracking_source_standardized.csv
    data/etf_ter_tracking_match_diagnostics.csv
    data/etf_ter_tracking_auto_fetch_log.csv
    data/etf_ter_tracking_auto_debug_report.md
    data/ter_tracking_debug/

Run standalone:
    python etf_ter_tracking_auto_fetcher.py

Force a run regardless of date/status:
    set FORCE_TER_TRACKING_REFRESH=1
    python etf_ter_tracking_auto_fetcher.py

This script is intentionally non-blocking. If TER/tracking fetch fails, it logs
and exits 0 so the main workflow can continue with existing/manual data.
"""

from __future__ import annotations

import calendar
import dataclasses
import json
import os
import re
import sys
import traceback
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
IMPORT_DIR = DATA_DIR / "etf_metadata_imports"
DEBUG_DIR = DATA_DIR / "ter_tracking_debug"
DATA_DIR.mkdir(exist_ok=True)
IMPORT_DIR.mkdir(exist_ok=True)
DEBUG_DIR.mkdir(exist_ok=True)

CONFIG_CSV = BASE_DIR / "config.csv"
MANUAL_QUALITY = BASE_DIR / "manual_etf_quality.csv"
ENRICHED_CSV = DATA_DIR / "etf_metadata_enriched.csv"

AUTO_IMPORT_LATEST = IMPORT_DIR / "auto_amfi_ter_tracking_latest.csv"
SOURCE_STANDARDIZED_OUT = DATA_DIR / "amfi_ter_tracking_source_standardized.csv"
MATCH_DIAG_OUT = DATA_DIR / "etf_ter_tracking_match_diagnostics.csv"
LOG_OUT = DATA_DIR / "etf_ter_tracking_auto_fetch_log.csv"
STATUS_OUT = DATA_DIR / "etf_ter_tracking_auto_status.json"
DEBUG_REPORT_OUT = DATA_DIR / "etf_ter_tracking_auto_debug_report.md"

REFRESH_START_DAY = int(os.environ.get("TER_TRACKING_REFRESH_START_DAY", "10"))
FORCE_REFRESH = os.environ.get("FORCE_TER_TRACKING_REFRESH", "").strip().lower() in {"1", "yes", "true", "y"}
# Convenience: when user forces all metadata/AUM refresh, refresh TER/tracking too.
FORCE_REFRESH = FORCE_REFRESH or os.environ.get("FORCE_METADATA_REFRESH", "").strip().lower() in {"1", "yes", "true", "y"}

MAX_REASONABLE_TER_DECIMAL = float(os.environ.get("MAX_REASONABLE_TER_DECIMAL", "0.05"))
MAX_REASONABLE_TRACKING_ERROR_DECIMAL = float(os.environ.get("MAX_REASONABLE_TRACKING_ERROR_DECIMAL", "0.20"))
MAX_ABS_TRACKING_DIFFERENCE_DECIMAL = float(os.environ.get("MAX_ABS_TRACKING_DIFFERENCE_DECIMAL", "0.20"))

STOPWORDS = {
    "etf", "exchange", "traded", "fund", "funds", "scheme", "schemes", "regular", "direct",
    "growth", "dividend", "payout", "reinvestment", "plan", "index", "the", "and", "of",
    "mutual", "amc", "asset", "management", "company", "limited", "ltd", "india", "nse",
    "bse", "benchmark", "total", "return", "tri"
}
AMC_TOKENS = {
    "icici", "prudential", "icicipramc", "hdfc", "hdfcamc", "motilal", "oswal", "mirae", "axis", "sbi",
    "kotak", "nippon", "uti", "aditya", "birla", "sun", "life", "zerodha", "edelweiss", "bandhan",
    "invesco", "tata", "lic", "canara", "robeco", "dsp", "quantum", "mahindra", "manulife", "groww",
    "bajaj", "baroda", "bnp", "paribas"
}


def log(rows: list[dict], source: str, status: str, detail: str, count: int = 0) -> None:
    rows.append({
        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Source": source,
        "Status": status,
        "Detail": detail,
        "Rows": count,
    })


def clean_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(value: Any, drop_amc: bool = False) -> list[str]:
    out = []
    for w in clean_text(value).split():
        if w in STOPWORDS:
            continue
        if drop_amc and w in AMC_TOKENS:
            continue
        out.append(w)
    return out


def token_score(a: Any, b: Any) -> float:
    ta = set(tokens(a, drop_amc=True))
    tb = set(tokens(b, drop_amc=True))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def parse_float(value: Any) -> float:
    if pd.isna(value):
        return np.nan
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "-", "na", "n/a", "nil", ""}:
        return np.nan
    text = text.replace(",", "").replace("₹", "").replace("rs.", "").replace("rs", "").strip()
    m = re.search(r"-?\d+(\.\d+)?", text)
    if not m:
        return np.nan
    try:
        return float(m.group(0))
    except Exception:
        return np.nan


def parse_percent_decimal(value: Any) -> float:
    """Return decimal form. 0.42% -> 0.0042, 0.42 -> 0.0042, 0.0042 -> 0.0042."""
    if pd.isna(value):
        return np.nan
    text = str(value).strip()
    num = parse_float(text)
    if pd.isna(num):
        return np.nan
    if "%" in text:
        return num / 100.0
    # TER / tracking disclosures are often shown as percent values like 0.42.
    if abs(num) > 0.05:
        return num / 100.0
    return num


def valid_ter(value: Any) -> tuple[bool, float, str]:
    x = parse_percent_decimal(value)
    if pd.isna(x) or x <= 0:
        return False, np.nan, "blank_or_non_positive"
    if x > MAX_REASONABLE_TER_DECIMAL:
        return False, np.nan, f"above_reasonable_ter_limit_{MAX_REASONABLE_TER_DECIMAL:g}"
    return True, round(float(x), 6), "valid"


def valid_tracking_error(value: Any) -> tuple[bool, float, str]:
    x = parse_percent_decimal(value)
    if pd.isna(x) or x < 0:
        return False, np.nan, "blank_or_negative"
    if x > MAX_REASONABLE_TRACKING_ERROR_DECIMAL:
        return False, np.nan, f"above_reasonable_tracking_error_limit_{MAX_REASONABLE_TRACKING_ERROR_DECIMAL:g}"
    return True, round(float(x), 6), "valid"


def valid_tracking_difference(value: Any) -> tuple[bool, float, str]:
    x = parse_percent_decimal(value)
    if pd.isna(x):
        return False, np.nan, "blank"
    if abs(x) > MAX_ABS_TRACKING_DIFFERENCE_DECIMAL:
        return False, np.nan, f"outside_reasonable_tracking_difference_limit_{MAX_ABS_TRACKING_DIFFERENCE_DECIMAL:g}"
    return True, round(float(x), 6), "valid"


def read_status() -> dict:
    if not STATUS_OUT.exists():
        return {}
    try:
        return json.loads(STATUS_OUT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_status(status: dict) -> None:
    STATUS_OUT.write_text(json.dumps(status, indent=2), encoding="utf-8")


def should_attempt_refresh(log_rows: list[dict]) -> bool:
    today = date.today()
    status = read_status()
    if FORCE_REFRESH:
        log(log_rows, "Schedule", "FORCE", "FORCE_TER_TRACKING_REFRESH/FORCE_METADATA_REFRESH active")
        return True
    if today.day < REFRESH_START_DAY:
        log(log_rows, "Schedule", "SKIP", f"Day {today.day} < refresh start day {REFRESH_START_DAY}. Reusing last file.")
        return False
    last_success = str(status.get("last_success_run_date", "")).strip()
    if last_success and last_success[:7] == today.strftime("%Y-%m") and AUTO_IMPORT_LATEST.exists():
        log(log_rows, "Schedule", "SKIP", f"Already refreshed this month: {last_success}. Reusing file.")
        return False
    log(log_rows, "Schedule", "ATTEMPT", f"Day {today.day} >= {REFRESH_START_DAY}. Attempting TER/tracking refresh.")
    return True


def fiscal_year_for_date(d: date | None = None) -> str:
    d = d or date.today()
    if d.month >= 4:
        return f"{d.year}-{d.year + 1}"
    return f"{d.year - 1}-{d.year}"


def month_candidates(max_months: int = 8) -> list[str]:
    """Return MM-YYYY candidates, newest first."""
    today = date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(max_months):
        out.append(f"{m:02d}-{y}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


def tracking_error_date_candidates(max_months: int = 8) -> list[str]:
    """Return DD-mon-YYYY month-end candidates, newest first."""
    today = date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(max_months):
        last_day = calendar.monthrange(y, m)[1]
        mon = datetime(y, m, 1).strftime("%b").lower()
        out.append(f"{last_day:02d}-{mon}-{y}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


def tracking_difference_month_candidates(max_months: int = 8) -> list[str]:
    """Return 01-Mon-YYYY candidates, newest first."""
    today = date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(max_months):
        mon = datetime(y, m, 1).strftime("%b")
        out.append(f"01-{mon}-{y}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


def _plain_object(obj: Any) -> Any:
    """Convert pydantic/dataclass/custom objects into plain dict/list scalars."""
    try:
        if hasattr(obj, "model_dump"):
            return _plain_object(obj.model_dump())
        if hasattr(obj, "dict") and callable(getattr(obj, "dict")) and not isinstance(obj, dict):
            return _plain_object(obj.dict())
        if dataclasses.is_dataclass(obj):
            return _plain_object(dataclasses.asdict(obj))
        if isinstance(obj, dict):
            return {str(k): _plain_object(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [_plain_object(v) for v in obj]
        if hasattr(obj, "__dict__") and not isinstance(obj, (str, bytes)):
            return {str(k): _plain_object(v) for k, v in vars(obj).items() if not str(k).startswith("_")}
    except Exception:
        pass
    return obj


def _flatten_records_from_plain(obj: Any) -> list[dict]:
    """Pull tabular row dictionaries out of nested AMFI/amfipy payloads."""
    obj = _plain_object(obj)
    if obj is None:
        return []
    if isinstance(obj, list):
        rows: list[dict] = []
        for item in obj:
            rows.extend(_flatten_records_from_plain(item))
        return rows
    if isinstance(obj, dict):
        # Prefer list-valued keys that look like the actual payload.
        rows: list[dict] = []
        scalar_ctx = {k: v for k, v in obj.items() if not isinstance(v, (list, dict))}
        for key, val in obj.items():
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        rec = {**scalar_ctx, **item, "__PayloadKey": key}
                        rows.append(_plain_object(rec))
                    elif isinstance(item, (list, tuple)):
                        rows.append({"__PayloadKey": key, "__RowValues": list(item)})
            elif isinstance(val, dict):
                nested = _flatten_records_from_plain(val)
                for rec in nested:
                    if isinstance(rec, dict):
                        rows.append({**scalar_ctx, **rec, "__PayloadKey": key})
        if rows:
            return rows
        return [obj]
    if isinstance(obj, (tuple, set)):
        return _flatten_records_from_plain(list(obj))
    return [{"value": obj}]


def _dedupe_columns(cols: list[Any]) -> list[str]:
    out: list[str] = []
    seen: dict[str, int] = {}
    for c in cols:
        name = str(c).strip()
        if not name or name.lower().startswith("nan"):
            name = "Unnamed"
        base = name
        if base in seen:
            seen[base] += 1
            name = f"{base}_{seen[base]}"
        else:
            seen[base] = 0
        out.append(name)
    return out


def _promote_header_row_if_needed(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    current_cols = [str(c) for c in df.columns]
    cols_look_generic = all(c.isdigit() or c.startswith("_") or c.lower().startswith("unnamed") for c in current_cols)
    if not cols_look_generic:
        return df
    keywords = ["scheme", "isin", "tracking", "benchmark", "expense", "ter", "difference", "error"]
    for i in range(min(12, len(df))):
        row = df.iloc[i]
        text = " ".join(str(v).lower() for v in row.values)
        if sum(k in text for k in keywords) >= 2:
            out = df.iloc[i + 1:].copy()
            out.columns = _dedupe_columns(list(row.values))
            return out.reset_index(drop=True)
    return df


def _expand_rowvalues_column(df: pd.DataFrame) -> pd.DataFrame:
    if "__RowValues" not in df.columns:
        return df
    values = df["__RowValues"].dropna()
    if values.empty:
        return df.drop(columns=["__RowValues"], errors="ignore")
    expanded = pd.DataFrame(values.tolist(), index=values.index)
    expanded = _promote_header_row_if_needed(expanded)
    # Keep context columns beside expanded row values.
    ctx = df.drop(columns=["__RowValues"], errors="ignore")
    ctx = ctx.loc[expanded.index] if len(expanded.index) else ctx.iloc[:0]
    out = pd.concat([ctx.reset_index(drop=True), expanded.reset_index(drop=True)], axis=1)
    return out


def to_pandas(obj: Any, log_rows: list[dict], label: str) -> pd.DataFrame:
    if obj is None:
        return pd.DataFrame()
    try:
        if isinstance(obj, bytes):
            return parse_excel_bytes(obj, log_rows, label)
        if isinstance(obj, pd.DataFrame):
            df = obj.copy()
        elif hasattr(obj, "to_pandas"):
            df = obj.to_pandas()
        elif hasattr(obj, "to_dicts"):
            df = pd.DataFrame(obj.to_dicts())
        else:
            plain = _plain_object(obj)
            # List-of-lists sometimes comes from web payloads; build then promote header.
            if isinstance(plain, list) and plain and all(isinstance(x, (list, tuple)) for x in plain):
                df = pd.DataFrame(plain)
            else:
                rows = _flatten_records_from_plain(plain)
                df = pd.json_normalize(rows) if rows else pd.DataFrame()
        if df is None or df.empty:
            return pd.DataFrame()
        df = _expand_rowvalues_column(df)
        df = _promote_header_row_if_needed(df)
        df.columns = _dedupe_columns([str(c).strip() for c in df.columns])
        log(log_rows, label, "CONVERT_OK", f"columns={list(df.columns)[:30]}", len(df))
        return df
    except Exception as exc:
        log(log_rows, label, "CONVERT_ERROR", str(exc), 0)
    return pd.DataFrame()


def parse_excel_bytes(raw: bytes, log_rows: list[dict], label: str) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame()
    debug_file = DEBUG_DIR / f"{label}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.xlsx"
    debug_file.write_bytes(raw)
    try:
        xls = pd.ExcelFile(BytesIO(raw))
    except Exception as exc:
        log(log_rows, label, "EXCEL_OPEN_ERROR", str(exc), 0)
        return pd.DataFrame()
    pieces = []
    for sheet in xls.sheet_names:
        # Try normal header first, then dynamic header rows.
        candidates = []
        try:
            candidates.append(pd.read_excel(BytesIO(raw), sheet_name=sheet))
        except Exception:
            pass
        try:
            raw_df = pd.read_excel(BytesIO(raw), sheet_name=sheet, header=None)
            for i, row in raw_df.iterrows():
                row_text = " ".join(str(v).lower() for v in row.values)
                if any(k in row_text for k in ["scheme", "isin", "expense", "tracking", "benchmark", "ter"]):
                    try:
                        candidates.append(pd.read_excel(BytesIO(raw), sheet_name=sheet, header=i))
                    except Exception:
                        pass
                    break
        except Exception:
            pass
        for df in candidates:
            if df is not None and len(df) > 0:
                df["__Sheet"] = sheet
                pieces.append(df)
    if not pieces:
        return pd.DataFrame()
    out = pd.concat(pieces, ignore_index=True)
    log(log_rows, label, "EXCEL_PARSED", str(debug_file.name), len(out))
    return out


def get_clients(log_rows: list[dict]) -> dict[str, list[Any]]:
    clients = {"ter": [], "tracking": []}
    try:
        import amfipy  # type: ignore
        log(log_rows, "amfipy", "OK", f"version={getattr(amfipy, '__version__', 'unknown')}")
    except Exception as exc:
        log(log_rows, "amfipy", "IMPORT_ERROR", str(exc))
        return clients

    # Path 1: AMFIClient facade.
    try:
        from amfipy import AMFIClient  # type: ignore
        c = AMFIClient()
        if hasattr(c, "ter"):
            clients["ter"].append(c.ter)
        if hasattr(c, "tracking"):
            clients["tracking"].append(c.tracking)
        log(log_rows, "AMFIClient", "OK", "Facade client created")
    except Exception as exc:
        log(log_rows, "AMFIClient", "ERROR", str(exc))

    # Path 2: direct clients.
    for module_name, class_name, bucket in [
        ("amfipy.ter", "TERClient", "ter"),
        ("amfipy.tracking", "TrackingClient", "tracking"),
    ]:
        try:
            module = __import__(module_name, fromlist=[class_name])
            cls = getattr(module, class_name)
            clients[bucket].append(cls())
            log(log_rows, class_name, "OK", f"{module_name}.{class_name} created")
        except Exception as exc:
            log(log_rows, class_name, "ERROR", str(exc))
    return clients


def call_method(obj: Any, method_name: str, attempts: list[dict], log_rows: list[dict], label: str) -> pd.DataFrame:
    if obj is None or not hasattr(obj, method_name):
        return pd.DataFrame()
    fn = getattr(obj, method_name)
    for kwargs in attempts:
        try:
            res = fn(**kwargs)
            df = to_pandas(res, log_rows, f"{label}_{method_name}")
            log(log_rows, label, "CALL_OK", f"{method_name}({kwargs}) -> rows={len(df)}", len(df))
            if len(df) > 0:
                return df
        except TypeError as exc:
            log(log_rows, label, "TYPE_ERROR", f"{method_name}({kwargs}): {exc}", 0)
        except Exception as exc:
            log(log_rows, label, "CALL_ERROR", f"{method_name}({kwargs}): {exc}", 0)
    return pd.DataFrame()


def fetch_ter_sources(log_rows: list[dict], clients: list[Any]) -> pd.DataFrame:
    fy = fiscal_year_for_date()
    months = []
    for c in clients:
        if hasattr(c, "months"):
            try:
                raw_months = c.months(year=fy)
                if isinstance(raw_months, list):
                    for item in raw_months:
                        if isinstance(item, dict):
                            val = item.get("MonthNumber") or item.get("month") or item.get("Month")
                            if val:
                                months.append(str(val))
                        else:
                            months.append(str(item))
                log(log_rows, "TER", "MONTHS_OK", f"{fy}: {months[:5]}", len(months))
            except Exception as exc:
                log(log_rows, "TER", "MONTHS_ERROR", str(exc), 0)
    if not months:
        months = month_candidates(8)
    months = list(dict.fromkeys(months))[:8]

    for c in clients:
        for month in months:
            # Excel first, because it preserves columns closest to AMFI disclosure.
            attempts_excel = [
                {"month": month, "year": fy, "mf_id": "All"},
                {"month": month, "year": fy, "mf_id": "all"},
                {"month": month, "year": fy},
                {"month": month},
            ]
            df = call_method(c, "download_excel", attempts_excel, log_rows, "TER")
            if len(df) > 0:
                out = standardize_ter_df(df, f"AMFI_TER_{month}")
                if len(out) > 0:
                    return out
            attempts_fetch = [
                {"month": month, "year": fy, "mf_id": "All", "as_df": False},
                {"month": month, "year": fy, "mf_id": "all", "as_df": False},
                {"month": month, "year": fy, "as_df": False},
                {"month": month, "as_df": False},
            ]
            df = call_method(c, "fetch", attempts_fetch, log_rows, "TER")
            if len(df) > 0:
                out = standardize_ter_df(df, f"AMFI_TER_{month}")
                if len(out) > 0:
                    return out
    return pd.DataFrame()


def fetch_tracking_sources(log_rows: list[dict], clients: list[Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch tracking data using as_df=True first, then raw payload fallback.

    The previous script proved the AMFI calls were returning rows but the raw
    payload standardizer discarded them. This version tries the documented
    dataframe path first and writes richer debug previews for any non-empty raw
    call so parser failures are inspectable.
    """
    tracking_error = pd.DataFrame()
    tracking_diff = pd.DataFrame()

    for c in clients:
        for d in tracking_error_date_candidates(10):
            attempts = [
                {"date": d, "mf_id": "all", "as_df": True},
                {"date": d, "mf_id": "All", "as_df": True},
                {"date": d, "as_df": True},
                {"date": d, "mf_id": "all", "as_df": False},
                {"date": d, "mf_id": "All", "as_df": False},
                {"date": d, "as_df": False},
            ]
            df = call_method(c, "error", attempts, log_rows, "TrackingError")
            if len(df) > 0:
                debug_preview(df, f"tracking_error_raw_{d}", log_rows)
                out = standardize_tracking_error_df(df, f"AMFI_Tracking_Error_{d}")
                if len(out) > 0:
                    tracking_error = out
                    break
                log(log_rows, "TrackingError", "STANDARDIZE_EMPTY", f"date={d}, columns={list(df.columns)[:40]}", len(df))
        if len(tracking_error) > 0:
            break

    for c in clients:
        for m in tracking_difference_month_candidates(10):
            attempts = [
                {"month": m, "mf_id": "all", "as_df": True},
                {"month": m, "mf_id": "All", "as_df": True},
                {"month": m, "as_df": True},
                {"month": m, "mf_id": "all", "as_df": False},
                {"month": m, "mf_id": "All", "as_df": False},
                {"month": m, "as_df": False},
            ]
            df = call_method(c, "difference", attempts, log_rows, "TrackingDifference")
            if len(df) > 0:
                debug_preview(df, f"tracking_difference_raw_{m}", log_rows)
                out = standardize_tracking_difference_df(df, f"AMFI_Tracking_Difference_{m}")
                if len(out) > 0:
                    tracking_diff = out
                    break
                log(log_rows, "TrackingDifference", "STANDARDIZE_EMPTY", f"month={m}, columns={list(df.columns)[:40]}", len(df))
        if len(tracking_diff) > 0:
            break
    return tracking_error, tracking_diff


def debug_preview(df: pd.DataFrame, label: str, log_rows: list[dict]) -> None:
    try:
        safe = re.sub(r"[^A-Za-z0-9_]+", "_", label)[:80]
        path = DEBUG_DIR / f"{safe}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.csv"
        df.head(50).to_csv(path, index=False)
        log(log_rows, "DebugPreview", "SAVED", f"{path.name}; columns={list(df.columns)[:30]}", min(len(df), 50))
    except Exception as exc:
        log(log_rows, "DebugPreview", "ERROR", f"{label}: {exc}", 0)


def find_col(df: pd.DataFrame, patterns: list[str], must_not_include: list[str] | None = None) -> str | None:
    must_not_include = must_not_include or []
    norms = {c: clean_text(c) for c in df.columns}
    for pat in patterns:
        p_tokens = clean_text(pat).split()
        for col, n in norms.items():
            if any(bad in n for bad in must_not_include):
                continue
            if all(t in n for t in p_tokens):
                return col
    # Compact aliases sometimes appear as TE, TD, TrackingError, etc.
    compact = {c: re.sub(r"[^a-z0-9]+", "", clean_text(c)) for c in df.columns}
    aliases = {
        "trackingerror": ["trackingerror", "annualisedtrackingerror", "annualizedtrackingerror", "te"],
        "trackingdifference": ["trackingdifference", "trackingdiff", "td"],
        "benchmark": ["benchmark", "benchmarkindex", "underlyingindex"],
        "scheme": ["schemename", "fundname", "nameofscheme"],
        "isin": ["isin"],
        "scheme code": ["schemecode", "amficode", "code"],
    }
    for pat in patterns:
        key = clean_text(pat)
        for alias in aliases.get(key, []):
            for col, n in compact.items():
                if any(bad in clean_text(col) for bad in must_not_include):
                    continue
                if alias == n or alias in n:
                    return col
    return None


def _numeric_valid_count(series: pd.Series, validator) -> tuple[int, float]:
    vals = series.apply(lambda x: validator(x)[1])
    good = vals.dropna()
    if good.empty:
        return 0, np.nan
    return int(len(good)), float(good.abs().median())


def best_percent_column(df: pd.DataFrame, kind: str) -> str | None:
    """Last-resort numeric-column detector when AMFI headers are unusual."""
    banned = ["scheme", "code", "isin", "date", "month", "year", "aum", "nav", "sr", "serial", "folio", "assets"]
    candidates = []
    validator = valid_tracking_error if kind == "tracking_error" else valid_tracking_difference
    for col in df.columns:
        n = clean_text(col)
        if any(b in n for b in banned):
            continue
        count, med = _numeric_valid_count(df[col], validator)
        if count <= 0:
            continue
        # Prefer explicit headers. Otherwise prefer high coverage and small magnitude.
        explicit = 0
        if kind == "tracking_error" and ("tracking" in n and "error" in n):
            explicit = 2
        if kind == "tracking_difference" and ("tracking" in n and "difference" in n):
            explicit = 2
        if kind == "tracking_difference" and "difference" in n:
            explicit = max(explicit, 1)
        if kind == "tracking_error" and (n == "te" or "trackingerror" in re.sub(r"[^a-z0-9]+", "", n)):
            explicit = max(explicit, 1)
        candidates.append((explicit, count, -abs(med if not np.isnan(med) else 999), col))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][3]




def percent_candidate_columns(df: pd.DataFrame, kind: str) -> list[str]:
    """Return plausible numeric disclosure columns, ordered by usefulness.

    AMFI tracking payloads often expose multiple columns by tenure, such as
    1-year, 3-year, 5-year, and since-allotment. Selecting just one table-level
    column can produce terrible coverage. This returns a priority list and the
    standardizers then pick first valid value row-by-row.
    """
    hard_banned = [
        "scheme code", "isin", "date", "month", "year", "aum", "nav", "sr",
        "serial", "folio", "assets", "scheme name", "fund name", "name of scheme",
        "benchmark", "category", "type", "amc", "mutual fund"
    ]
    validator = valid_tracking_error if kind == "tracking_error" else valid_tracking_difference
    candidates: list[tuple[int, int, int, float, str]] = []
    for col in df.columns:
        n = clean_text(col)
        compact = re.sub(r"[^a-z0-9]+", "", n)
        explicit_tracking = ("tracking" in n and ("error" in n or "difference" in n)) or compact in {"te", "td"}
        if any(b in n for b in hard_banned) and not explicit_tracking:
            continue
        valid_vals = df[col].apply(lambda x: validator(x)[1]).dropna()
        count = int(len(valid_vals))
        if count <= 0:
            continue

        explicit = 0
        if kind == "tracking_error":
            if "tracking" in n and "error" in n:
                explicit += 50
            if compact in {"te", "trackingerror", "annualisedtrackingerror", "annualizedtrackingerror"}:
                explicit += 40
            if "difference" in n:
                explicit -= 60
        else:
            if "tracking" in n and "difference" in n:
                explicit += 50
            if compact in {"td", "trackingdifference", "trackingdiff"}:
                explicit += 40
            if "error" in n:
                explicit -= 60

        tenure = 0
        if any(s in n for s in ["1 year", "one year", "1 yr", "1y", "year 1"]):
            tenure += 30
        elif any(s in n for s in ["3 year", "three year", "3 yr", "3y"]):
            tenure += 20
        elif any(s in n for s in ["5 year", "five year", "5 yr", "5y"]):
            tenure += 10
        elif any(s in n for s in ["since", "inception", "allotment", "launch"]):
            tenure += 5

        med = float(valid_vals.abs().median()) if not valid_vals.empty else 999.0
        candidates.append((explicit, tenure, count, -med, col))

    candidates.sort(reverse=True)
    return [c[-1] for c in candidates]


def first_valid_percent_by_row(df: pd.DataFrame, columns: list[str], validator) -> pd.Series:
    """Pick first valid value across priority-ordered columns for each row."""
    if not columns:
        return pd.Series(np.nan, index=df.index)
    out = pd.Series(np.nan, index=df.index, dtype="float64")
    for col in columns:
        vals = df[col].apply(lambda x: validator(x)[1])
        mask = out.isna() & vals.notna()
        out.loc[mask] = vals.loc[mask]
    return out


def detected_column_note(columns: list[str]) -> str:
    if not columns:
        return "no columns"
    shown = ", ".join(str(c) for c in columns[:8])
    if len(columns) > 8:
        shown += f", ... +{len(columns)-8} more"
    return shown

def _best_text_column(df: pd.DataFrame, prefer: list[str], avoid: list[str] | None = None) -> str | None:
    avoid = avoid or []
    scores = []
    for col in df.columns:
        n = clean_text(col)
        if any(a in n for a in avoid):
            continue
        s = df[col].dropna().astype(str)
        if s.empty:
            continue
        joined = " ".join(s.head(50).str.lower().tolist())
        keyword_score = sum(k in n or k in joined for k in prefer)
        avg_len = s.head(100).str.len().mean()
        alpha_ratio = s.head(100).str.contains(r"[A-Za-z]", regex=True).mean()
        if alpha_ratio < 0.5:
            continue
        scores.append((keyword_score, avg_len, col))
    if not scores:
        return None
    scores.sort(reverse=True)
    return scores[0][2]


def standard_base_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    code_col = find_col(df, ["scheme code", "amfi code", "code", "schemecode"], must_not_include=["category", "type"])
    isin_col = find_col(df, ["isin"])
    name_col = find_col(df, ["scheme name", "fund name", "name of scheme", "scheme", "fund"])
    bench_col = find_col(df, ["benchmark index", "benchmark", "underlying index", "index"], must_not_include=["tracking", "scheme"])

    if not name_col:
        name_col = _best_text_column(df, ["scheme", "fund", "etf", "index"], avoid=["benchmark", "amc", "mutual fund"])
    if not bench_col:
        bench_col = _best_text_column(df, ["benchmark", "nifty", "sensex", "bse", "crisil", "tri", "index"], avoid=["scheme name", "fund name"])

    out["Scheme_Code"] = df[code_col].astype(str).str.strip() if code_col else ""
    out["ISIN"] = df[isin_col].astype(str).str.strip() if isin_col else ""
    out["Scheme_Name"] = df[name_col].astype(str).str.strip() if name_col else ""
    out["Benchmark_Index"] = df[bench_col].astype(str).str.strip() if bench_col else ""
    return out


def standardize_ter_df(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    base = standard_base_columns(df)
    ter_col = find_col(df, ["total expense ratio", "expense ratio", "base expense ratio", "ter"], must_not_include=["change", "date"])
    if not ter_col:
        return pd.DataFrame()
    ter_vals = df[ter_col].apply(lambda x: valid_ter(x)[1])
    out = base.copy()
    out["TER"] = ter_vals
    out["Tracking_Error"] = np.nan
    out["Tracking_Difference"] = np.nan
    out["Source"] = source
    out = out.dropna(subset=["TER"])
    return out


def standardize_tracking_error_df(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    base = standard_base_columns(df)

    candidate_cols = percent_candidate_columns(df, "tracking_error")
    if not candidate_cols:
        fallback = best_percent_column(df, "tracking_error")
        candidate_cols = [fallback] if fallback else []
    if not candidate_cols:
        return pd.DataFrame()

    vals = first_valid_percent_by_row(df, candidate_cols, valid_tracking_error)
    out = base.copy()
    out["TER"] = np.nan
    out["Tracking_Error"] = vals
    out["Tracking_Difference"] = np.nan
    out["Source"] = f"{source}; cols={detected_column_note(candidate_cols)}"
    out = out.dropna(subset=["Tracking_Error"])
    id_ok = out[["Scheme_Code", "ISIN", "Scheme_Name"]].astype(str).apply(lambda s: s.str.strip().replace("nan", "").ne(""), axis=0).any(axis=1)
    return out[id_ok].copy()


def standardize_tracking_difference_df(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    base = standard_base_columns(df)

    candidate_cols = percent_candidate_columns(df, "tracking_difference")
    if not candidate_cols:
        fallback = best_percent_column(df, "tracking_difference")
        candidate_cols = [fallback] if fallback else []
    if not candidate_cols:
        return pd.DataFrame()

    vals = first_valid_percent_by_row(df, candidate_cols, valid_tracking_difference)
    out = base.copy()
    out["TER"] = np.nan
    out["Tracking_Error"] = np.nan
    out["Tracking_Difference"] = vals
    out["Source"] = f"{source}; cols={detected_column_note(candidate_cols)}"
    out = out.dropna(subset=["Tracking_Difference"])
    id_ok = out[["Scheme_Code", "ISIN", "Scheme_Name"]].astype(str).apply(lambda s: s.str.strip().replace("nan", "").ne(""), axis=0).any(axis=1)
    return out[id_ok].copy()


def load_manual_fallback_sources(log_rows: list[dict]) -> pd.DataFrame:
    patterns = ["*ter_tracking_manual*.csv", "*ter_tracking_manual*.xlsx", "*ter_tracking_manual*.xls",
                "*amfi_ter_tracking_manual*.csv", "*amfi_ter_tracking_manual*.xlsx", "*amfi_ter_tracking_manual*.xls",
                "*tracking_manual*.csv", "*tracking_manual*.xlsx", "*tracking_manual*.xls",
                "*ter_manual*.csv", "*ter_manual*.xlsx", "*ter_manual*.xls"]
    files = []
    for pat in patterns:
        files.extend(IMPORT_DIR.glob(pat))
    files = sorted(set(files), key=lambda p: p.stat().st_mtime, reverse=True)
    pieces = []
    for path in files:
        try:
            if path.suffix.lower() == ".csv":
                df = pd.read_csv(path)
                df["__Source_File"] = path.name
                pieces.append(standardize_manual_import_df(df, path.name))
            else:
                xls = pd.ExcelFile(path)
                for sheet in xls.sheet_names:
                    df = pd.read_excel(path, sheet_name=sheet)
                    df["__Source_File"] = f"{path.name}::{sheet}"
                    pieces.append(standardize_manual_import_df(df, f"{path.name}::{sheet}"))
            log(log_rows, "ManualFallback", "OK", str(path), len(pieces[-1]) if pieces else 0)
        except Exception as exc:
            log(log_rows, "ManualFallback", "ERROR", f"{path}: {exc}", 0)
    if not pieces:
        return pd.DataFrame()
    out = pd.concat(pieces, ignore_index=True)
    return out.dropna(how="all")


def standardize_manual_import_df(df: pd.DataFrame, source: str) -> pd.DataFrame:
    base = standard_base_columns(df)
    symbol_col = find_col(df, ["symbol", "ticker", "nse symbol"])
    if symbol_col:
        base["Symbol"] = df[symbol_col].astype(str).str.strip()
    else:
        base["Symbol"] = ""
    ter_col = find_col(df, ["ter", "total expense ratio", "expense ratio"])
    te_col = find_col(df, ["tracking error"], must_not_include=["difference"])
    td_col = find_col(df, ["tracking difference"])
    base["TER"] = df[ter_col].apply(lambda x: valid_ter(x)[1]) if ter_col else np.nan
    base["Tracking_Error"] = df[te_col].apply(lambda x: valid_tracking_error(x)[1]) if te_col else np.nan
    base["Tracking_Difference"] = df[td_col].apply(lambda x: valid_tracking_difference(x)[1]) if td_col else np.nan
    base["Source"] = source
    keep = base[["TER", "Tracking_Error", "Tracking_Difference"]].notna().any(axis=1)
    return base[keep].copy()


def combine_source_rows(dfs: list[pd.DataFrame]) -> pd.DataFrame:
    clean = []
    for df in dfs:
        if df is not None and not df.empty:
            for col in ["Scheme_Code", "ISIN", "Scheme_Name", "Benchmark_Index", "TER", "Tracking_Error", "Tracking_Difference", "Source", "Symbol"]:
                if col not in df.columns:
                    df[col] = np.nan
            clean.append(df[["Scheme_Code", "ISIN", "Scheme_Name", "Benchmark_Index", "TER", "Tracking_Error", "Tracking_Difference", "Source", "Symbol"]].copy())
    if not clean:
        return pd.DataFrame(columns=["Scheme_Code", "ISIN", "Scheme_Name", "Benchmark_Index", "TER", "Tracking_Error", "Tracking_Difference", "Source", "Symbol"])
    raw = pd.concat(clean, ignore_index=True)
    raw = raw.replace({"": np.nan, "nan": np.nan, "None": np.nan})
    # Group by best key and take first non-null for each field.
    raw["Scheme_Code"] = raw["Scheme_Code"].astype(str).str.strip().replace({"nan": np.nan})
    raw["ISIN"] = raw["ISIN"].astype(str).str.strip().replace({"nan": np.nan})
    raw["Scheme_Name"] = raw["Scheme_Name"].astype(str).str.strip().replace({"nan": np.nan})
    return raw


def load_etf_universe() -> pd.DataFrame:
    if not CONFIG_CSV.exists():
        raise FileNotFoundError("config.csv not found. Run universe_builder.py first.")
    cfg = pd.read_csv(CONFIG_CSV)
    group_col = "Universe" if "Universe" in cfg.columns else "Universe_Group"
    etfs = cfg[cfg[group_col].astype(str).str.upper().eq("ETF")].copy().reset_index(drop=True)
    if "Raw_Symbol" not in etfs.columns:
        etfs["Raw_Symbol"] = etfs["Symbol"].astype(str).str.replace(".NS", "", regex=False)
    return etfs


def load_mapping_context() -> pd.DataFrame:
    pieces = []
    if ENRICHED_CSV.exists():
        pieces.append(pd.read_csv(ENRICHED_CSV))
    if MANUAL_QUALITY.exists():
        pieces.append(pd.read_csv(MANUAL_QUALITY))
    if not pieces:
        return pd.DataFrame()
    ctx = pd.concat(pieces, ignore_index=True, sort=False)
    if "Symbol" not in ctx.columns:
        return pd.DataFrame()
    # Preserve most recent/last non-null mapping for each symbol.
    return ctx.drop_duplicates(subset=["Symbol"], keep="last")


def first_nonnull(series: pd.Series) -> Any:
    for v in series:
        if pd.notna(v) and str(v).strip() not in {"", "nan", "None"}:
            return v
    return np.nan


def best_source_row_for_etf(etf: pd.Series, ctx_row: pd.Series | None, sources: pd.DataFrame) -> tuple[pd.Series | None, str, float]:
    if sources.empty:
        return None, "No source", 0.0
    symbol = str(etf.get("Symbol", "")).strip()
    raw = str(etf.get("Raw_Symbol", symbol)).replace(".NS", "").strip()
    isin = str(etf.get("ISIN", "")).strip()
    name = str(etf.get("Name", "")).strip()
    code = ""
    scheme_name = ""
    if ctx_row is not None:
        code = str(ctx_row.get("AMFI_Scheme_Code", "")).strip()
        scheme_name = str(ctx_row.get("AMFI_Scheme_Name", "")).strip()
        if not isin:
            isin = str(ctx_row.get("ISIN", "")).strip()

    # 1. Direct Symbol manual fallback match.
    if "Symbol" in sources.columns:
        hit = sources[sources["Symbol"].astype(str).str.replace(".NS", "", regex=False).str.upper().eq(raw.upper())]
        if not hit.empty:
            return hit.iloc[0], "Symbol", 1.0

    # 2. Scheme code.
    if code and code.lower() not in {"nan", "none"}:
        hit = sources[sources["Scheme_Code"].astype(str).str.strip().eq(code)]
        if not hit.empty:
            return merge_source_hits(hit), "AMFI_Scheme_Code", 1.0

    # 3. ISIN.
    if isin and isin.lower() not in {"nan", "none"}:
        hit = sources[sources["ISIN"].astype(str).str.upper().eq(isin.upper())]
        if not hit.empty:
            return merge_source_hits(hit), "ISIN", 1.0

    # 4. Conservative fuzzy matching on AMFI scheme name and ETF name.
    candidates = []
    for idx, r in sources.iterrows():
        src_name = str(r.get("Scheme_Name", ""))
        if not src_name or src_name.lower() == "nan":
            continue
        score = max(token_score(scheme_name, src_name), token_score(name, src_name), token_score(raw, src_name))
        candidates.append((score, idx))
    if not candidates:
        return None, "No match", 0.0
    candidates.sort(reverse=True, key=lambda x: x[0])
    score, idx = candidates[0]
    if score < 0.70:
        return None, "No reliable fuzzy match", score
    return sources.loc[idx], "Fuzzy", score


def merge_source_hits(hit: pd.DataFrame) -> pd.Series:
    cols = ["Scheme_Code", "ISIN", "Scheme_Name", "Benchmark_Index", "TER", "Tracking_Error", "Tracking_Difference", "Source", "Symbol"]
    data = {col: first_nonnull(hit[col]) if col in hit.columns else np.nan for col in cols}
    return pd.Series(data)


def build_output_import(etfs: pd.DataFrame, sources: pd.DataFrame, log_rows: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    ctx = load_mapping_context()
    ctx_by_symbol = {}
    if not ctx.empty and "Symbol" in ctx.columns:
        ctx_by_symbol = {str(r["Symbol"]).upper(): r for _, r in ctx.iterrows()}
    rows = []
    diags = []
    for _, etf in etfs.iterrows():
        symbol = str(etf.get("Symbol", "")).strip()
        raw = str(etf.get("Raw_Symbol", symbol)).replace(".NS", "").strip()
        ctx_row = ctx_by_symbol.get(symbol.upper())
        source_row, method, score = best_source_row_for_etf(etf, ctx_row, sources)
        if source_row is None:
            source_row = pd.Series(dtype=object)
        ter = source_row.get("TER", np.nan)
        te = source_row.get("Tracking_Error", np.nan)
        td = source_row.get("Tracking_Difference", np.nan)
        bench = source_row.get("Benchmark_Index", "")
        amfi_code = str(ctx_row.get("AMFI_Scheme_Code", "")).strip() if ctx_row is not None else str(source_row.get("Scheme_Code", "")).strip()
        scheme_name = str(ctx_row.get("AMFI_Scheme_Name", "")).strip() if ctx_row is not None else str(source_row.get("Scheme_Name", "")).strip()
        isin = str(etf.get("ISIN", "")).strip() or (str(ctx_row.get("ISIN", "")).strip() if ctx_row is not None else str(source_row.get("ISIN", "")).strip())
        rows.append({
            "Symbol": symbol,
            "NSE Symbol": raw,
            "ISIN": isin,
            "AMFI_Scheme_Code": amfi_code,
            "Scheme Name": scheme_name or str(etf.get("Name", "")),
            "TER": ter if pd.notna(ter) else "",
            "Tracking Error": te if pd.notna(te) else "",
            "Tracking Difference": td if pd.notna(td) else "",
            "Benchmark Index": bench if pd.notna(bench) else "",
            "Match_Method": method,
            "Match_Score": score,
            "Source": str(source_row.get("Source", "")),
            "Fetch_Date": datetime.now().strftime("%Y-%m-%d"),
        })
        diags.append({
            "Symbol": symbol,
            "Match_Method": method,
            "Match_Score": score,
            "TER_Filled": pd.notna(ter),
            "Tracking_Error_Filled": pd.notna(te),
            "Tracking_Difference_Filled": pd.notna(td),
            "Benchmark_Filled": bool(pd.notna(bench) and str(bench).strip()),
            "Matched_Source": str(source_row.get("Source", "")),
            "Matched_Scheme_Name": str(source_row.get("Scheme_Name", "")),
        })
    out = pd.DataFrame(rows)
    diag = pd.DataFrame(diags)
    log(log_rows, "Merge", "OK", f"TER={out['TER'].astype(str).str.strip().ne('').sum()}, Tracking_Error={out['Tracking Error'].astype(str).str.strip().ne('').sum()}", len(out))
    return out, diag


def write_debug_report(log_rows: list[dict], sources: pd.DataFrame, out: pd.DataFrame | None = None) -> None:
    lines = [
        "# ETF TER / Tracking Auto Fetch Debug Report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Coverage",
    ]
    if out is not None and not out.empty:
        lines += [
            f"- Output ETF rows: {len(out)}",
            f"- TER filled: {out['TER'].astype(str).str.strip().ne('').sum()}",
            f"- Tracking Error filled: {out['Tracking Error'].astype(str).str.strip().ne('').sum()}",
            f"- Tracking Difference filled: {out['Tracking Difference'].astype(str).str.strip().ne('').sum()}",
            f"- Benchmark filled: {out['Benchmark Index'].astype(str).str.strip().ne('').sum()}",
        ]
    lines += [
        f"- Source rows standardized: {len(sources) if sources is not None else 0}",
        "",
        "## Log",
    ]
    for r in log_rows[-80:]:
        lines.append(f"- {r.get('Timestamp')} | {r.get('Source')} | {r.get('Status')} | rows={r.get('Rows')} | {r.get('Detail')}")
    DEBUG_REPORT_OUT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    print("ETF TER / Tracking Auto Fetcher - Stage 3.5.9")
    print("================================================")
    log_rows: list[dict] = []

    try:
        etfs = load_etf_universe()
        print(f"ETF universe: {len(etfs)} rows")
    except Exception as exc:
        print(f"WARNING: {exc}. Skipping TER/tracking fetch.")
        log(log_rows, "Universe", "ERROR", str(exc), 0)
        pd.DataFrame(log_rows).to_csv(LOG_OUT, index=False)
        write_debug_report(log_rows, pd.DataFrame())
        return 0

    if not should_attempt_refresh(log_rows):
        if AUTO_IMPORT_LATEST.exists():
            print(f"Skipped TER/tracking fetch. Reusing: {AUTO_IMPORT_LATEST.name}")
        else:
            print("Skipped TER/tracking fetch and no prior file exists.")
        pd.DataFrame(log_rows).to_csv(LOG_OUT, index=False)
        write_debug_report(log_rows, pd.DataFrame())
        return 0

    sources = []
    clients = get_clients(log_rows)

    print("Fetching AMFI TER...")
    ter_df = fetch_ter_sources(log_rows, clients.get("ter", []))
    if not ter_df.empty:
        print(f"TER source rows standardized: {len(ter_df)}")
        sources.append(ter_df)
    else:
        print("TER automatic source returned 0 usable rows.")

    print("Fetching AMFI tracking error / tracking difference...")
    te_df, td_df = fetch_tracking_sources(log_rows, clients.get("tracking", []))
    if not te_df.empty:
        print(f"Tracking error source rows standardized: {len(te_df)}")
        sources.append(te_df)
    else:
        print("Tracking error automatic source returned 0 usable rows.")
    if not td_df.empty:
        print(f"Tracking difference source rows standardized: {len(td_df)}")
        sources.append(td_df)
    else:
        print("Tracking difference automatic source returned 0 usable rows.")

    print("Checking manual TER/tracking fallback files...")
    manual_df = load_manual_fallback_sources(log_rows)
    if not manual_df.empty:
        print(f"Manual TER/tracking rows standardized: {len(manual_df)}")
        sources.append(manual_df)

    source_df = combine_source_rows(sources)
    source_df.to_csv(SOURCE_STANDARDIZED_OUT, index=False)
    print(f"Combined TER/tracking source rows: {len(source_df)}")

    if source_df.empty:
        print("No TER/tracking source rows available. Workflow will continue with existing/manual data if present.")
        write_status({**read_status(), "last_attempt_run_date": date.today().isoformat(), "last_attempt_status": "no_source_rows"})
        pd.DataFrame(log_rows).to_csv(LOG_OUT, index=False)
        write_debug_report(log_rows, source_df)
        return 0

    out, diag = build_output_import(etfs, source_df, log_rows)
    period_str = datetime.now().strftime("%Y_%m")
    period_out = IMPORT_DIR / f"auto_amfi_ter_tracking_{period_str}.csv"
    out.to_csv(period_out, index=False)
    out.to_csv(AUTO_IMPORT_LATEST, index=False)
    diag.to_csv(MATCH_DIAG_OUT, index=False)
    pd.DataFrame(log_rows).to_csv(LOG_OUT, index=False)
    write_debug_report(log_rows, source_df, out)

    ter_count = out["TER"].astype(str).str.strip().ne("").sum()
    te_count = out["Tracking Error"].astype(str).str.strip().ne("").sum()
    td_count = out["Tracking Difference"].astype(str).str.strip().ne("").sum()
    bench_count = out["Benchmark Index"].astype(str).str.strip().ne("").sum()

    print(f"TER/tracking import saved: {AUTO_IMPORT_LATEST}")
    print(f"Period file: {period_out}")
    print(f"TER filled: {ter_count} / {len(out)}")
    print(f"Tracking error filled: {te_count} / {len(out)}")
    print(f"Tracking difference filled: {td_count} / {len(out)}")
    print(f"Benchmark filled: {bench_count} / {len(out)}")

    success = ter_count > 0 or te_count > 0 or td_count > 0
    write_status({
        **read_status(),
        "last_success_run_date": date.today().isoformat() if success else read_status().get("last_success_run_date", ""),
        "last_success_period": period_str if success else read_status().get("last_success_period", ""),
        "last_success_ter_rows": int(ter_count),
        "last_success_tracking_error_rows": int(te_count),
        "last_success_tracking_difference_rows": int(td_count),
        "last_attempt_status": "success" if success else "no_matches",
    })

    if success:
        print("Good. Run/continue full workflow so etf_metadata_enricher.py and etf_quality_builder.py pick this up.")
    else:
        print("No ETF TER/tracking rows matched. Check data\\etf_ter_tracking_auto_debug_report.md.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("WARNING: ETF TER/tracking auto-fetcher hit an unexpected error. Workflow continues.")
        traceback.print_exc()
        raise SystemExit(0)
