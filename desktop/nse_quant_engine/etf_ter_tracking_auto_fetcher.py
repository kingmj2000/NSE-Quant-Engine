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

Matching (parser v3):
    TER, tracking error and tracking difference are matched to each ETF one
    stream at a time: symbol, scheme code, ISIN, then the exact scheme name
    (with and without plan words). A looser name match needs the same fund
    house and the same distinctive words ("Zerodha CASE Gold ETF" = "Zerodha
    Gold ETF"; Nifty Bank is not Nifty Private Bank), and index fund / FoF rows
    never match by name. Unmatched stays blank, and so does a total TER above
    1.00% ("Rejected (TER above limit)"; AMFI figure and its split in the Note).
    Every match is listed per stream in
    data/etf_ter_tracking_match_diagnostics.csv.

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

from core.safe_io import read_cached_csv, read_required_csv
from core.etf_source_rules import (
    AMFI_PARSER_VERSION,
    ETF_TER_CAP_DECIMAL,
    amc_of,
    amc_of_mf_id,
    amc_of_scheme_code,
    benchmark_or_blank,
    is_amfi_category_label,
    scheme_kind,
)

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

# Total TER above 1.00% is left blank for ETFs (rule 2 in core/etf_source_rules.py);
# the old 5% ceiling let unit errors (0.04% stored as 4%) and index-fund
# regular-plan TERs through.
MAX_REASONABLE_TER_DECIMAL = float(os.environ.get("MAX_REASONABLE_TER_DECIMAL", str(ETF_TER_CAP_DECIMAL)))
MAX_REASONABLE_TRACKING_ERROR_DECIMAL = float(os.environ.get("MAX_REASONABLE_TRACKING_ERROR_DECIMAL", "0.20"))
MAX_ABS_TRACKING_DIFFERENCE_DECIMAL = float(os.environ.get("MAX_ABS_TRACKING_DIFFERENCE_DECIMAL", "0.20"))

# AMFI fills each month / period in as the AMCs file, so the newest set is
# partial for the first weeks (5 Nov 2026: November TER without Nippon India or
# HDFC; 31-Oct tracking error for SBI only). Combine the newest sets; for each
# scheme the most recent disclosure wins.
TER_MONTHS_TO_COMBINE = 2
TRACKING_PERIODS_TO_COMBINE = 2

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


def parse_percent_decimal(value: Any, unit: str = "auto") -> float:
    """Return decimal form. 0.42% -> 0.0042, 0.42 -> 0.0042, 0.0042 -> 0.0042.

    unit="percent" is for AMFI disclosures, which are always percent figures:
    there "0.04" is 0.04% (0.0004), not 4%. The "auto" guess below is kept only
    for hand-made fallback files whose unit is unknown.
    """
    if pd.isna(value):
        return np.nan
    text = str(value).strip()
    num = parse_float(text)
    if pd.isna(num):
        return np.nan
    if unit == "percent" or "%" in text:
        return num / 100.0
    # TER / tracking disclosures are often shown as percent values like 0.42.
    if abs(num) > 0.05:
        return num / 100.0
    return num


def valid_ter(value: Any, unit: str = "auto") -> tuple[bool, float, str]:
    x = parse_percent_decimal(value, unit)
    if pd.isna(x) or x <= 0:
        return False, np.nan, "blank_or_non_positive"
    if x > MAX_REASONABLE_TER_DECIMAL:
        return False, np.nan, f"above_reasonable_ter_limit_{MAX_REASONABLE_TER_DECIMAL:g}"
    return True, round(float(x), 6), "valid"


def valid_tracking_error(value: Any, unit: str = "auto") -> tuple[bool, float, str]:
    x = parse_percent_decimal(value, unit)
    if pd.isna(x) or x < 0:
        return False, np.nan, "blank_or_negative"
    if x > MAX_REASONABLE_TRACKING_ERROR_DECIMAL:
        return False, np.nan, f"above_reasonable_tracking_error_limit_{MAX_REASONABLE_TRACKING_ERROR_DECIMAL:g}"
    return True, round(float(x), 6), "valid"


def valid_tracking_difference(value: Any, unit: str = "auto") -> tuple[bool, float, str]:
    x = parse_percent_decimal(value, unit)
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


def _day_of_month(iso_text: str) -> int:
    try:
        return int(str(iso_text)[8:10])
    except (TypeError, ValueError):
        return 99


def should_attempt_refresh(log_rows: list[dict]) -> bool:
    today = date.today()
    status = read_status()
    if FORCE_REFRESH:
        log(log_rows, "Schedule", "FORCE", "FORCE_TER_TRACKING_REFRESH/FORCE_METADATA_REFRESH active")
        return True
    if str(status.get("parser_version", "")) != str(AMFI_PARSER_VERSION):
        # The last file was read by an older parser (percent/category bugs), so
        # waiting for the monthly window would keep known-wrong values in use.
        log(log_rows, "Schedule", "ATTEMPT", f"Last import used parser v{status.get('parser_version', '1')}; current v{AMFI_PARSER_VERSION}. Re-fetching now.")
        return True
    if today.day < REFRESH_START_DAY:
        log(log_rows, "Schedule", "SKIP", f"Day {today.day} < refresh start day {REFRESH_START_DAY}. Reusing last file.")
        return False
    last_success = str(status.get("last_success_run_date", "")).strip()
    if last_success and last_success[:7] == today.strftime("%Y-%m") and AUTO_IMPORT_LATEST.exists():
        if _day_of_month(last_success) >= REFRESH_START_DAY:
            log(log_rows, "Schedule", "SKIP", f"Already refreshed this month: {last_success}. Reusing file.")
            return False
        # A forced / parser-bump run early in the month only sees the AMCs that
        # had filed by then (5 Nov 2026: October tracking error for SBI alone).
        # Refresh once inside the normal window so the fuller month lands.
        log(log_rows, "Schedule", "ATTEMPT", f"Last refresh {last_success} ran before day {REFRESH_START_DAY}; refreshing once with this month's fuller AMFI data.")
        return True
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


_EXCEL_HEADER_KEYWORDS = ["scheme", "isin", "expense", "tracking", "benchmark", "ter"]


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
        # Read each sheet once and promote the first row that looks like a
        # header. The old code read the sheet with the default header AND again
        # with the detected header row, even when that row was row 0, so every
        # AMFI TER row came in twice (7,997 -> 15,994) and the merge took 5 min.
        try:
            grid = pd.read_excel(xls, sheet_name=sheet, header=None)
        except Exception as exc:
            log(log_rows, label, "EXCEL_SHEET_ERROR", f"{sheet}: {exc}", 0)
            continue
        if grid is None or grid.empty:
            continue
        header_at = 0
        for i in range(min(len(grid), 30)):
            row_text = " ".join(str(v).lower() for v in grid.iloc[i].values)
            if any(k in row_text for k in _EXCEL_HEADER_KEYWORDS):
                header_at = i
                break
        df = grid.iloc[header_at + 1:].copy()
        df.columns = _dedupe_columns(list(grid.iloc[header_at].values))
        df = df.dropna(how="all").reset_index(drop=True)
        if len(df) > 0:
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


def _empty_payload(res: Any) -> bool:
    """True when a call worked but AMFI simply has no rows for that period."""
    if res is None:
        return True
    if isinstance(res, pd.DataFrame):
        return res.empty
    height = getattr(res, "height", None)          # polars.DataFrame
    if isinstance(height, int) and hasattr(res, "columns"):
        return height == 0
    plain = _plain_object(res)
    if plain is None:
        return True
    if isinstance(plain, (bytes, bytearray, str, list, tuple, set)):
        return len(plain) == 0
    if isinstance(plain, dict):
        if not plain:
            return True
        if "data" in plain:
            data = plain.get("data")
            return data is None or (isinstance(data, (list, tuple, dict, str)) and len(data) == 0)
    return False


def call_method(obj: Any, method_name: str, attempts: list[dict], log_rows: list[dict], label: str) -> pd.DataFrame:
    if obj is None or not hasattr(obj, method_name):
        return pd.DataFrame()
    fn = getattr(obj, method_name)
    for kwargs in attempts:
        try:
            res = fn(**kwargs)
        except TypeError as exc:
            log(log_rows, label, "TYPE_ERROR", f"{method_name}({kwargs}): {exc}", 0)
            continue
        except Exception as exc:
            log(log_rows, label, "CALL_ERROR", f"{method_name}({kwargs}): {exc}", 0)
            continue
        if _empty_payload(res):
            # The call worked; AMFI has nothing for this period yet. The other
            # argument spellings ask the same question (5 Nov 2026: six empty
            # calls per unpublished month).
            log(log_rows, label, "CALL_EMPTY", f"{method_name}({kwargs}) -> no rows published; other argument forms skipped", 0)
            return pd.DataFrame()
        df = to_pandas(res, log_rows, f"{label}_{method_name}")
        log(log_rows, label, "CALL_OK", f"{method_name}({kwargs}) -> rows={len(df)}", len(df))
        if len(df) > 0:
            return df
    return pd.DataFrame()


def _fiscal_year_for_month(month: str) -> str:
    m = re.fullmatch(r"\s*(\d{1,2})-(\d{4})\s*", str(month))
    if not m:
        return fiscal_year_for_date()
    return fiscal_year_for_date(date(int(m.group(2)), int(m.group(1)), 1))


def _parse_amfi_day(text: str) -> date | None:
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(text).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _ter_months(log_rows: list[dict], clients: list[Any]) -> list[str]:
    fy = fiscal_year_for_date()
    months: list[str] = []
    for c in clients:
        if not hasattr(c, "months"):
            continue
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
            if months:
                break                      # both clients wrap the same AMFI list
        except Exception as exc:
            log(log_rows, "TER", "MONTHS_ERROR", str(exc), 0)
    # AMFI's list covers the current financial year only; the calendar
    # candidates add last year's months (April) and cover a failed list call.
    return list(dict.fromkeys(months + month_candidates(8)))[:8]


def fetch_ter_sources(log_rows: list[dict], clients: list[Any]) -> pd.DataFrame:
    """Latest TER per scheme from the newest TER_MONTHS_TO_COMBINE months.

    AMFI's current month fills in as AMCs file: on 5 Nov 2026 the November
    sheet had no Nippon India or HDFC rows, so NIFTYBEES, BANKBEES, GOLDBEES,
    JUNIORBEES, CPSEETF and LIQUIDBEES got no TER. The previous month covers
    them; the matcher keeps the most recent TER date of each scheme.
    """
    months = _ter_months(log_rows, clients)
    for c in clients:
        got: list[pd.DataFrame] = []
        for month in months:
            fy = _fiscal_year_for_month(month)
            out = pd.DataFrame()
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
            if out.empty:
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
                amcs = out["AMC"].replace("", np.nan).nunique() if "AMC" in out.columns else 0
                usable = int(out["TER"].notna().sum())
                log(log_rows, "TER", "MONTH_OK", f"{month}: {usable} TER rows, {amcs} AMCs, "
                    f"{len(out) - usable} ETF rows above the TER limit", usable)
                got.append(out)
                if len(got) >= TER_MONTHS_TO_COMBINE:
                    break
        if got:
            return pd.concat(got, ignore_index=True)
    return pd.DataFrame()


def fetch_tracking_sources(log_rows: list[dict], clients: list[Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Tracking error / difference from the newest TRACKING_PERIODS_TO_COMBINE periods.

    AMFI publishes these per AMC as each one files. On 5 Nov 2026 the
    31-Oct tracking-error set held SBI's 34 rows only, while 30-Sep held every
    AMC; stopping at the first non-empty date left 357 of 359 ETFs blank.
    Month-ends still in the future are not queried at all.
    """
    today = date.today()
    te_parts: list[pd.DataFrame] = []
    td_parts: list[pd.DataFrame] = []

    for c in clients:
        for d in tracking_error_date_candidates(10):
            when = _parse_amfi_day(d)
            if when is not None and when > today:
                continue
            attempts = [
                {"date": d, "mf_id": "all", "as_df": True},
                {"date": d, "mf_id": "All", "as_df": True},
                {"date": d, "as_df": True},
                {"date": d, "mf_id": "all", "as_df": False},
                {"date": d, "mf_id": "All", "as_df": False},
                {"date": d, "as_df": False},
            ]
            df = call_method(c, "error", attempts, log_rows, "TrackingError")
            if len(df) == 0:
                continue
            debug_preview(df, f"tracking_error_raw_{d}", log_rows)
            out = standardize_tracking_error_df(df, f"AMFI_Tracking_Error_{d}", as_of=when)
            if len(out) == 0:
                log(log_rows, "TrackingError", "STANDARDIZE_EMPTY", f"date={d}, columns={list(df.columns)[:40]}", len(df))
                continue
            te_parts.append(out)
            if len(te_parts) >= TRACKING_PERIODS_TO_COMBINE:
                break
        if te_parts:
            break

    for c in clients:
        for m in tracking_difference_month_candidates(10):
            when = _parse_amfi_day(m)
            if when is not None and when > today:
                continue
            attempts = [
                {"month": m, "mf_id": "all", "as_df": True},
                {"month": m, "mf_id": "All", "as_df": True},
                {"month": m, "as_df": True},
                {"month": m, "mf_id": "all", "as_df": False},
                {"month": m, "mf_id": "All", "as_df": False},
                {"month": m, "as_df": False},
            ]
            df = call_method(c, "difference", attempts, log_rows, "TrackingDifference")
            if len(df) == 0:
                continue
            debug_preview(df, f"tracking_difference_raw_{m}", log_rows)
            out = standardize_tracking_difference_df(df, f"AMFI_Tracking_Difference_{m}", as_of=when)
            if len(out) == 0:
                log(log_rows, "TrackingDifference", "STANDARDIZE_EMPTY", f"month={m}, columns={list(df.columns)[:40]}", len(df))
                continue
            td_parts.append(out)
            if len(td_parts) >= TRACKING_PERIODS_TO_COMBINE:
                break
        if td_parts:
            break

    tracking_error = pd.concat(te_parts, ignore_index=True) if te_parts else pd.DataFrame()
    tracking_diff = pd.concat(td_parts, ignore_index=True) if td_parts else pd.DataFrame()
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
        if any(b in n for b in banned) or _is_id_column(col):
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




_ID_COLUMNS = {"id", "mfid", "amcid", "schemeid", "fundid", "planid", "srno", "slno"}
_TENURES = (
    (30, "1y", re.compile(r"\b(?:y1|1y|1yr|1yrs)\b|\b(?:1|one)\s*(?:years?|yrs?)\b|\byears?\s*1\b")),
    (20, "3y", re.compile(r"\b(?:y3|3y|3yr|3yrs)\b|\b(?:3|three)\s*(?:years?|yrs?)\b|\byears?\s*3\b")),
    (10, "5y", re.compile(r"\b(?:y5|5y|5yr|5yrs)\b|\b(?:5|five)\s*(?:years?|yrs?)\b|\byears?\s*5\b")),
    (7, "10y", re.compile(r"\b(?:y10|10y|10yr|10yrs)\b|\b(?:10|ten)\s*(?:years?|yrs?)\b|\byears?\s*10\b")),
)


def _is_id_column(col: Any) -> bool:
    """AMFI's mfId (3 = ABSL, 9 = HDFC, 20 = ICICI) reads as a valid 0.03-0.20
    "percent", so it must never be a tracking-value candidate."""
    compact = re.sub(r"[^a-z0-9]+", "", str(col).lower())
    return compact in _ID_COLUMNS or "code" in compact


def _column_tenure(col: Any) -> tuple[int, str]:
    """(priority, tenure group) of a tracking column. 1-year comes first; the
    since-launch figure is only a fallback (AMFI names: Y1_R, ReturnLaunch_D)."""
    n = clean_text(col)
    for rank, group, pattern in _TENURES:
        if pattern.search(n):
            return rank, group
    compact = n.replace(" ", "")
    if any(s in compact for s in ["since", "inception", "allotment", "launch"]):
        return 5, "launch"
    return 0, ""


def _is_regular_plan_column(col: Any) -> bool:
    n = clean_text(col)
    return "regular" in n or "r" in n.split()


def percent_candidate_columns(df: pd.DataFrame, kind: str) -> list[str]:
    """Return plausible numeric disclosure columns, ordered by usefulness.

    AMFI tracking payloads expose one column per tenure and plan (Y1_R, Y1_D,
    Y3_R, ..., ReturnLaunch_D; RegularPercent / DirectPercent). The order is
    tenure (1Y first, since-launch last), then regular before direct plan.
    Identifier columns such as mfId are never candidates.
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
        if _is_id_column(col):
            continue
        explicit_tracking = ("tracking" in n and ("error" in n or "difference" in n)) or compact in {"te", "td"}
        tenure, _group = _column_tenure(col)
        if any(b in n for b in hard_banned) and not explicit_tracking and tenure == 0:
            continue
        valid_vals = df[col].apply(lambda x: validator(x, "percent")[1]).dropna()
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

        plan = 1 if _is_regular_plan_column(col) else 0
        med = float(valid_vals.abs().median()) if not valid_vals.empty else 999.0
        candidates.append((explicit, tenure * 2 + plan, count, -med, col))

    candidates.sort(reverse=True)
    return [c[-1] for c in candidates]


def first_valid_percent_by_row(df: pd.DataFrame, columns: list[str], validator) -> pd.Series:
    """Pick one value per row from priority-ordered columns.

    Columns of one tenure (regular and direct plan) form a group. Some AMCs
    fill the plan an ETF does not have with 0 instead of blank (Bajaj Finserv
    BSE Top 10 Banks ETF, 30 Sep 2026: regular 0, direct 0.06), so a non-zero
    value of the same group wins over a 0. The next tenure is only used when
    the whole group is blank.
    """
    if not columns:
        return pd.Series(np.nan, index=df.index)
    order: list[str] = []
    members: dict[str, list[str]] = {}
    for col in columns:
        group = _column_tenure(col)[1]
        if group not in members:
            order.append(group)
            members[group] = []
        members[group].append(col)
    out = pd.Series(np.nan, index=df.index, dtype="float64")
    for group in order:
        if not out.isna().any():
            break
        nonzero = pd.Series(np.nan, index=df.index, dtype="float64")
        zero = pd.Series(np.nan, index=df.index, dtype="float64")
        for col in members[group]:
            vals = pd.to_numeric(df[col].apply(lambda x: validator(x)[1]), errors="coerce")
            nonzero = nonzero.where(nonzero.notna(), vals.where(vals.ne(0)))
            zero = zero.where(zero.notna(), vals.where(vals.eq(0)))
        pick = nonzero.where(nonzero.notna(), zero)
        mask = out.isna() & pick.notna()
        out.loc[mask] = pick.loc[mask]
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


def _naive_datetimes(values: Any, index: pd.Index) -> pd.Series:
    when = pd.to_datetime(pd.Series(values, index=index), errors="coerce", utc=True)
    return when.dt.tz_convert(None)


def _source_amc(df: pd.DataFrame, base: pd.DataFrame) -> pd.Series:
    """Fund house of each AMFI row: scheme name, else NSDL code, else mfId."""
    amc = base["Scheme_Name"].apply(amc_of)
    amc = amc.where(amc.ne(""), base["Scheme_Code"].apply(amc_of_scheme_code))
    mf_col = next((c for c in df.columns if re.sub(r"[^a-z0-9]+", "", str(c).lower()) == "mfid"), None)
    if mf_col is not None:
        amc = amc.where(amc.ne(""), df[mf_col].apply(amc_of_mf_id))
    return amc


def standard_base_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    code_col = find_col(df, ["scheme code", "amfi code", "code", "schemecode"], must_not_include=["category", "type"])
    isin_col = find_col(df, ["isin"])
    name_col = find_col(df, ["scheme name", "fund name", "name of scheme", "scheme", "fund"])
    bench_col = find_col(df, ["benchmark index", "benchmark", "underlying index", "index"], must_not_include=["tracking", "scheme"])

    if not name_col:
        name_col = _best_text_column(df, ["scheme", "fund", "etf", "index"], avoid=["benchmark", "amc", "mutual fund"])

    out["Scheme_Code"] = df[code_col].astype(str).str.strip() if code_col else ""
    out["ISIN"] = df[isin_col].astype(str).str.strip() if isin_col else ""
    out["Scheme_Name"] = df[name_col].astype(str).str.strip() if name_col else ""
    # Only a real benchmark column is a benchmark. AMFI TER sheets have none; the
    # old best-text-column fallback landed on "Scheme Category" and 174 ETFs
    # stored new SEBI category labels as their benchmark.
    bench_raw = df[bench_col].astype(str).str.strip() if bench_col else pd.Series("", index=df.index)
    cat_col = find_col(df, ["scheme category", "schemecat desc", "category", "sub category"])
    if cat_col:
        cat = df[cat_col].astype(str).str.strip()
        cat = cat.where(~cat.str.lower().isin(["nan", "none"]), "")
    else:
        cat = pd.Series("", index=df.index)
    # Pre-v2 files wrote the category into the benchmark column.
    cat = cat.where(cat.ne(""), bench_raw.where(bench_raw.apply(is_amfi_category_label), ""))
    out["Scheme_Category"] = cat
    out["Benchmark_Index"] = bench_raw.apply(benchmark_or_blank)
    out["AMC"] = _source_amc(df, out)
    return out


def _plan_part_column(df: pd.DataFrame, plan: str, *words: str) -> Any:
    """A per-plan TER component column, e.g. ("regular", "statutory", "levies")."""
    for col in df.columns:
        n = clean_text(col).split()
        if plan in n and all(w in n for w in words):
            return col
    return None


def _plan_total_ter_columns(df: pd.DataFrame) -> tuple[Any, Any]:
    """Regular- and direct-plan total TER columns (JSON R_TER/D_TER or the
    Excel "Regular Plan - Total TER (%)" / "Direct Plan - Total TER (%)")."""
    regular = direct = None
    for col in df.columns:
        n = clean_text(col)
        words = n.split()
        compact = n.replace(" ", "")
        if regular is None and (compact == "rter" or ("regular" in words and "total" in words and "ter" in words)):
            regular = col
        elif direct is None and (compact == "dter" or ("direct" in words and "total" in words and "ter" in words)):
            direct = col
    return regular, direct


def standardize_ter_df(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    base = standard_base_columns(df)
    ter_col = find_col(
        df,
        ["total expense ratio", "ter total", "total ter", "expense ratio", "ter"],
        must_not_include=["change", "date", "year", "addl", "additional", "base"],
    )
    if not ter_col:
        return pd.DataFrame()
    def as_percent(col: Any) -> pd.Series:
        return pd.to_numeric(df[col].apply(lambda x: parse_percent_decimal(x, "percent")), errors="coerce")

    ter_vals = df[ter_col].apply(lambda x: valid_ter(x, "percent")[1])
    disclosed = as_percent(ter_col)
    # AMFI files an ETF's single plan under either the regular or the direct
    # plan and puts 0.00 in the other. Use the regular figure, else the direct
    # one: reading only the regular column dropped 92 of 326 ETFs in the
    # November 2026 sheet (all Zerodha, Groww, DSP, Tata, Angel One ETFs).
    reg_col, dir_col = _plan_total_ter_columns(df)
    on_regular = pd.Series(True, index=df.index)
    if reg_col is not None and dir_col is not None:
        reg = df[reg_col].apply(lambda x: valid_ter(x, "percent")[1])
        dirp = df[dir_col].apply(lambda x: valid_ter(x, "percent")[1])
        ter_vals = reg.where(reg.notna(), dirp)
        reg_disclosed = as_percent(reg_col)
        on_regular = reg_disclosed.gt(0)
        disclosed = reg_disclosed.where(on_regular, as_percent(dir_col))
    out = base.copy()
    out["TER"] = pd.to_numeric(ter_vals, errors="coerce")
    # A total TER above MAX_REASONABLE_TER_DECIMAL is not used. ETF rows keep
    # the disclosed figure and its split so the diagnostics can say why the ETF
    # has no TER (13 ETFs in the November 2026 sheet, e.g. SBI BSE India Defence
    # ETF 7.77% = base 0.30% + statutory levies 7.20%) instead of "no same-name
    # scheme". Since April 2026 levies are charged at actuals above the capped
    # base expense ratio, so such a figure can be real: the split shows which.
    not_other_type = pd.Series([scheme_kind(n, c) != "non_etf"
                                for n, c in zip(out["Scheme_Name"], out["Scheme_Category"])], index=out.index)
    out["TER_Rejected"] = disclosed.where(out["TER"].isna() & disclosed.gt(MAX_REASONABLE_TER_DECIMAL) & not_other_type)
    parts = []
    for label, words in (("base", ("base", "expense")), ("statutory levies", ("statutory", "levies"))):
        cols = [_plan_part_column(df, plan, *words) for plan in ("regular", "direct")]
        if cols[0] is not None and cols[1] is not None:
            value = as_percent(cols[0]).where(on_regular, as_percent(cols[1]))
            parts.append(value.map(lambda v, lab=label: f"{lab} {v:.2%}" if pd.notna(v) else ""))
    split = pd.Series("", index=out.index)
    for part in parts:
        split = split.str.cat(part, sep=" + ").str.strip(" +")
    out["TER_Detail"] = split.where(out["TER_Rejected"].notna(), "")
    out["Tracking_Error"] = np.nan
    out["Tracking_Difference"] = np.nan
    out["Source"] = source
    date_col = find_col(df, ["ter date"]) or find_col(df, ["date"])
    out["As_Of"] = _naive_datetimes(df[date_col], df.index) if date_col else pd.NaT
    out = out[out["TER"].notna() | out["TER_Rejected"].notna()]
    # AMFI's TER feed is daily (one row per scheme per TER_Date): latest first.
    if len(out):
        out = out.sort_values("As_Of", ascending=False, kind="stable", na_position="last")
    return out


def standardize_tracking_error_df(df: pd.DataFrame, source: str, as_of: Any = None) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    base = standard_base_columns(df)

    candidate_cols = percent_candidate_columns(df, "tracking_error")
    if not candidate_cols:
        fallback = best_percent_column(df, "tracking_error")
        candidate_cols = [fallback] if fallback else []
    if not candidate_cols:
        return pd.DataFrame()

    vals = first_valid_percent_by_row(df, candidate_cols, lambda v: valid_tracking_error(v, "percent"))
    out = base.copy()
    out["TER"] = np.nan
    out["Tracking_Error"] = vals
    out["Tracking_Difference"] = np.nan
    out["Source"] = f"{source}; cols={detected_column_note(candidate_cols)}"
    out["As_Of"] = pd.Timestamp(as_of) if as_of is not None else pd.NaT
    out = out.dropna(subset=["Tracking_Error"])
    id_ok = out[["Scheme_Code", "ISIN", "Scheme_Name"]].astype(str).apply(lambda s: s.str.strip().replace("nan", "").ne(""), axis=0).any(axis=1)
    return out[id_ok].copy()


def standardize_tracking_difference_df(df: pd.DataFrame, source: str, as_of: Any = None) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    base = standard_base_columns(df)

    candidate_cols = percent_candidate_columns(df, "tracking_difference")
    if not candidate_cols:
        fallback = best_percent_column(df, "tracking_difference")
        candidate_cols = [fallback] if fallback else []
    if not candidate_cols:
        return pd.DataFrame()

    vals = first_valid_percent_by_row(df, candidate_cols, lambda v: valid_tracking_difference(v, "percent"))
    out = base.copy()
    out["TER"] = np.nan
    out["Tracking_Error"] = np.nan
    out["Tracking_Difference"] = vals
    out["Source"] = f"{source}; cols={detected_column_note(candidate_cols)}"
    out["As_Of"] = pd.Timestamp(as_of) if as_of is not None else pd.NaT
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
    base["As_Of"] = pd.NaT
    keep = base[["TER", "Tracking_Error", "Tracking_Difference"]].notna().any(axis=1)
    return base[keep].copy()


SOURCE_COLUMNS = ["Scheme_Code", "ISIN", "Scheme_Name", "Scheme_Category", "Benchmark_Index", "TER",
                  "Tracking_Error", "Tracking_Difference", "Source", "Symbol", "As_Of", "AMC",
                  "TER_Rejected", "TER_Detail"]


def combine_source_rows(dfs: list[pd.DataFrame]) -> pd.DataFrame:
    clean = []
    for df in dfs:
        if df is not None and not df.empty:
            df = df.copy()
            for col in SOURCE_COLUMNS:
                if col not in df.columns:
                    df[col] = pd.NaT if col == "As_Of" else np.nan
            clean.append(df[SOURCE_COLUMNS].copy())
    if not clean:
        return pd.DataFrame(columns=SOURCE_COLUMNS)
    raw = pd.concat(clean, ignore_index=True)
    for col in ["Scheme_Code", "ISIN", "Scheme_Name", "Scheme_Category", "Benchmark_Index", "Source", "Symbol", "AMC",
                "TER_Detail"]:
        s = raw[col].astype(object)
        s = s.where(s.isna(), s.astype(str).str.strip())
        raw[col] = s.mask(s.isin(["", "nan", "None"]))
    for col in ["TER", "Tracking_Error", "Tracking_Difference", "TER_Rejected"]:
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    raw["As_Of"] = pd.to_datetime(raw["As_Of"], errors="coerce")
    return raw


def load_etf_universe() -> pd.DataFrame:
    if not CONFIG_CSV.exists():
        raise FileNotFoundError("config.csv not found. Run universe_builder.py first.")
    cfg = read_required_csv(CONFIG_CSV, produced_by="python universe_builder.py")
    group_col = "Universe" if "Universe" in cfg.columns else "Universe_Group"
    etfs = cfg[cfg[group_col].astype(str).str.upper().eq("ETF")].copy().reset_index(drop=True)
    if "Raw_Symbol" not in etfs.columns:
        etfs["Raw_Symbol"] = etfs["Symbol"].astype(str).str.replace(".NS", "", regex=False)
    return etfs


def load_mapping_context() -> pd.DataFrame:
    pieces = []
    # exists() is not usable — an empty/corrupt cache must be skipped, not raise.
    for _path, _label in ((ENRICHED_CSV, "etf_metadata_enriched.csv"),
                          (MANUAL_QUALITY, "manual_etf_quality.csv")):
        _df = read_cached_csv(_path, label=_label)
        if not _df.empty:
            pieces.append(_df)
    if not pieces:
        return pd.DataFrame()
    ctx = pd.concat(pieces, ignore_index=True, sort=False)
    if "Symbol" not in ctx.columns:
        return pd.DataFrame()
    # Preserve most recent/last non-null mapping for each symbol.
    return ctx.drop_duplicates(subset=["Symbol"], keep="last")


# Each disclosure stream is matched on its own: one ETF can take its TER from
# the TER sheet, its tracking error from the 30-Sep set and its tracking
# difference from the 01-Oct set. Picking one "best" row across the mixed
# streams (TER rows sort first) left TE on 2 ETFs and TD on 30 in Nov 2026.
STREAMS = (("TER", "TER"), ("Tracking_Error", "TE"), ("Tracking_Difference", "TD"))
TRUSTED_NAV_METHODS = {"ISIN", "AMFI_Scheme_Code", "Scheme_Code"}
_PLAN_ONLY_WORDS = {"direct", "regular", "plan", "plans", "option", "options"}
_OPTION_WORDS = {"growth", "idcw", "dividend", "payout", "reinvestment", "bonus"}
_PLAN_WORDS = _PLAN_ONLY_WORDS | _OPTION_WORDS
# Fund-house and brand words. The AMC itself is compared separately, so these
# only blur name similarity ("Angel One", "360 ONE", "Zerodha CASE", "BeES").
_BRAND_WORDS = {"angel", "one", "360", "jio", "blackrock", "jioblackrock", "finserv", "wealth",
                "company", "mf", "sun", "life", "prudential", "oswal", "manulife", "robeco",
                "templeton", "case", "asset", "bees"}
_FUZZY_DROP = STOPWORDS | AMC_TOKENS | _BRAND_WORDS | _PLAN_WORDS
_FORMERLY = re.compile(r"\((?:formerly|erstwhile)[^)]*\)", re.IGNORECASE)


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "nat"} else text


def _code_text(value: Any) -> str:
    text = _text(value)
    return text[:-2] if re.fullmatch(r"\d+\.0", text) else text


def name_key(value: Any) -> str:
    """Normalised scheme name: "Nifty50" == "Nifty 50", "Exchange Traded Fund" == "ETF"."""
    text = _text(value)
    if not text:
        return ""
    text = clean_text(_FORMERLY.sub(" ", text))
    text = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", text)
    text = re.sub(r"\bexchange traded funds?\b", "etf", text)
    text = re.sub(r"\betfs\b", "etf", text)
    text = re.sub(r"\bg sec\b", "gsec", text)
    text = re.sub(r"\bs and p\b", " ", text)
    return " ".join(text.split())


def option_key(key: str) -> str:
    """Name without plan words, keeping the option once: AMFI NAV names read
    "X - Growth - Regular Plan - Growth" where the TER sheet has "X - Growth",
    and the IDCW and Growth options of one liquid ETF carry different TERs."""
    out: list[str] = []
    for w in str(key).split():
        if w in _PLAN_ONLY_WORDS or (w in _OPTION_WORDS and w in out):
            continue
        out.append(w)
    return " ".join(out)


def plan_free_key(key: str) -> str:
    return " ".join(w for w in str(key).split() if w not in _PLAN_WORDS)


def _fold_plural(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def fuzzy_tokens(key: str) -> frozenset:
    """Distinctive words of a name: no AMC, brand, plan or filler words."""
    return frozenset(_fold_plural(w) for w in str(key).split()
                     if w not in _FUZZY_DROP and not (len(w) == 1 and w.isalpha()))


def _empty_stream_index() -> dict:
    return {"rows": [], "symbol": {}, "code": {}, "isin": {}, "key": {}, "okey": {}, "pkey": {}, "amc": {}}


def build_stream_index(sources: pd.DataFrame, value_col: str) -> dict:
    """Rows that carry `value_col`, newest disclosure first.

    One scheme can appear under several names and dates (daily TER rows, two
    months combined, a rename mid-month: "Zerodha Nifty 50 ETF" became
    "Zerodha CASE Nifty 50 ETF" on 5 Nov 2026). Every name of a scheme code
    resolves to that code's newest value.
    """
    index = _empty_stream_index()
    if sources is None or sources.empty or value_col not in sources.columns:
        return index
    df = sources[pd.to_numeric(sources[value_col], errors="coerce").notna()].copy()
    if df.empty:
        return index
    df["_as_of"] = pd.to_datetime(df["As_Of"], errors="coerce") if "As_Of" in df.columns else pd.NaT
    df["_name"] = df["Scheme_Name"].map(_text) if "Scheme_Name" in df.columns else ""
    df["_code"] = df["Scheme_Code"].map(_code_text) if "Scheme_Code" in df.columns else ""
    keys = {n: name_key(n) for n in df["_name"].unique()}
    df["_key"] = df["_name"].map(keys)
    df["_symbol"] = (df["Symbol"].map(_text).str.upper().str.replace(".NS", "", regex=False)
                     if "Symbol" in df.columns else "")
    df = df.sort_values("_as_of", ascending=False, kind="stable", na_position="last")
    keep_all = df["_symbol"].ne("") | df["_key"].eq("")
    df = pd.concat([df[keep_all], df[~keep_all].drop_duplicates(["_code", "_key"], keep="first")])
    newest: dict[str, Any] = {}
    for _, r in df.iterrows():
        if r["_code"] and r["_code"] not in newest:
            newest[r["_code"]] = r
    for _, r in df.iterrows():
        name, key = r["_name"], r["_key"]
        latest = newest.get(r["_code"], r) if r["_code"] else r
        rec = {
            "value": float(latest[value_col]),
            "name": name,
            "key": key,
            "okey": option_key(key),
            "pkey": plan_free_key(key),
            "tokens": fuzzy_tokens(key),
            "amc": _text(r.get("AMC")) or amc_of(name) or amc_of_scheme_code(r.get("Scheme_Code")),
            "kind": scheme_kind(name, r.get("Scheme_Category")),
            "category": _text(latest.get("Scheme_Category")),
            "benchmark": benchmark_or_blank(_text(latest.get("Benchmark_Index"))),
            "source": _text(latest.get("Source")),
            "detail": _text(latest.get("TER_Detail")),
            "as_of": latest["_as_of"],
            "code": r["_code"],
            "isin": _text(r.get("ISIN")).upper(),
            "symbol": r["_symbol"],
        }
        index["rows"].append(rec)
        if rec["symbol"]:
            index["symbol"].setdefault(rec["symbol"], rec)
        if rec["code"]:
            index["code"].setdefault(rec["code"], rec)
        if rec["isin"]:
            index["isin"].setdefault(rec["isin"], rec)
        if rec["kind"] == "non_etf" or not key:
            continue
        index["key"].setdefault(key, rec)
        index["okey"].setdefault(rec["okey"], []).append(rec)
        index["pkey"].setdefault(rec["pkey"], []).append(rec)
        if rec["amc"]:
            index["amc"].setdefault(rec["amc"], []).append(rec)
    return index


def etf_identity(etf: pd.Series, ctx_row: pd.Series | None) -> dict:
    """What identifies one ETF for matching. The AMFI scheme name/code from the
    NAV mapping is used only when that mapping came from an identifier: on
    2026-11-05 HDFCNIMEG's "No reliable match" name was Mirae Asset BSE India
    Defence ETF."""
    symbol = _text(etf.get("Symbol"))
    raw = (_text(etf.get("Raw_Symbol")) or symbol).replace(".NS", "")
    nse_name = _text(etf.get("Name"))
    isin = _text(etf.get("ISIN"))
    code = amfi_name = ""
    if ctx_row is not None:
        if not isin:
            isin = _text(ctx_row.get("ISIN"))
        method = _text(ctx_row.get("NAV_Match_Method"))
        if method in TRUSTED_NAV_METHODS:
            code = _code_text(ctx_row.get("AMFI_Scheme_Code"))
            amfi_name = _text(ctx_row.get("AMFI_Scheme_Name"))
    keys = list(dict.fromkeys(k for k in (name_key(amfi_name), name_key(nse_name)) if k))
    return {
        "symbol": symbol,
        "raw": raw.upper(),
        "isin": isin.upper(),
        "isin_out": isin,
        "code": code,
        "amfi_name": amfi_name,
        "nse_name": nse_name,
        "amc": amc_of(amfi_name) or amc_of(nse_name),
        "keys": keys,
        "okeys": list(dict.fromkeys(option_key(k) for k in keys if option_key(k))),
        "pkeys": list(dict.fromkeys(plan_free_key(k) for k in keys if plan_free_key(k))),
        "tokens": [fuzzy_tokens(k) for k in keys if fuzzy_tokens(k)],
    }


def _one_scheme(hits: list[dict]) -> dict | None:
    """The newest hit when all hits describe one scheme (one code, or one value);
    None when they are different schemes with different values."""
    if len({h["code"] for h in hits if h["code"]}) <= 1 and all(h["code"] for h in hits):
        return hits[0]
    if len({round(h["value"], 6) for h in hits}) == 1:
        return hits[0]
    return None


def match_stream(ident: dict, index: dict) -> tuple[dict | None, str, float]:
    """Row of one stream for one ETF, or none.

    Identifiers first, then the exact name, the name without plan words
    (keeping Growth vs IDCW), the name without plan and option words, and last
    a name made of the same distinctive words from the ETF's own fund house
    ("Zerodha CASE Gold ETF" = "Zerodha Gold ETF"). One differing word is a
    different index: SBI Nifty200 Value 30 ETF is not SBI Nifty 200 Quality 30
    ETF, and Nifty Bank is not Nifty Private Bank. Index-fund and FoF rows
    never match by name.
    """
    if not index["rows"]:
        return None, "No source", 0.0
    if ident["raw"] and ident["raw"] in index["symbol"]:
        return index["symbol"][ident["raw"]], "Symbol", 1.0
    if ident["code"] and ident["code"] in index["code"]:
        return index["code"][ident["code"]], "AMFI_Scheme_Code", 1.0
    if ident["isin"] and ident["isin"] in index["isin"]:
        return index["isin"][ident["isin"]], "ISIN", 1.0
    for key in ident["keys"]:
        hit = index["key"].get(key)
        if hit is not None:
            return hit, "Exact_Name", 1.0
    for tier, field in (("Exact_Name_Option", "okeys"), ("Exact_Name_PlanFree", "pkeys")):
        idx = index["okey"] if field == "okeys" else index["pkey"]
        for k in ident[field]:
            hits = idx.get(k, [])
            if hits:
                one = _one_scheme(hits)
                return (one, tier, 1.0) if one is not None else (None, "Ambiguous_Name", 0.0)
    amc = ident["amc"]
    if not amc:
        return None, "No match (ETF AMC unknown)", 0.0
    pool = index["amc"].get(amc, [])
    if not pool:
        return None, "No match (AMC not in source)", 0.0
    hits = [rec for rec in pool if rec["tokens"] and rec["tokens"] in ident["tokens"]]
    if not hits:
        return None, "No match (no same-name scheme)", 0.0
    one = _one_scheme(hits)
    if one is None:
        return None, "Ambiguous_Name", 0.0
    return one, "Name_Tokens_Same_AMC", 1.0


def _as_of_text(rec: dict | None) -> str:
    if not rec or rec.get("as_of") is None or pd.isna(rec.get("as_of")):
        return ""
    return pd.Timestamp(rec["as_of"]).strftime("%Y-%m-%d")


def build_output_import(etfs: pd.DataFrame, sources: pd.DataFrame, log_rows: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    ctx = load_mapping_context()
    ctx_by_symbol = {}
    if not ctx.empty and "Symbol" in ctx.columns:
        ctx_by_symbol = {str(r["Symbol"]).upper(): r for _, r in ctx.iterrows()}
    indexes = {col: build_stream_index(sources, col) for col, _ in STREAMS}
    over_cap = build_stream_index(sources, "TER_Rejected")
    fetch_date = datetime.now().strftime("%Y-%m-%d")
    rows = []
    diags = []
    for _, etf in etfs.iterrows():
        symbol = _text(etf.get("Symbol"))
        ctx_row = ctx_by_symbol.get(symbol.upper())
        ident = etf_identity(etf, ctx_row)
        hits = {col: match_stream(ident, indexes[col]) for col, _ in STREAMS}
        # No usable TER: say so when the ETF's own row is there but above the limit.
        no_ter = hits["TER"][0] is None and hits["TER"][1].startswith("No match")
        rejected = match_stream(ident, over_cap)[0] if no_ter and over_cap["rows"] else None
        if rejected is not None:
            hits["TER"] = (None, "Rejected (TER above limit)", 0.0)
        ter_rec, te_rec, td_rec = (hits[col][0] for col, _ in STREAMS)
        bench = next((r["benchmark"] for r in (te_rec, td_rec, ter_rec) if r and r["benchmark"]), "")
        category = next((r["category"] for r in (ter_rec, te_rec, td_rec) if r and r["category"]), "")
        lead_rec, lead_method, lead_score = next(
            ((r, m, s) for (r, m, s) in (hits[col] for col, _ in STREAMS) if r is not None), hits["TER"])
        source_text = " | ".join(f"{short}: {hits[col][0]['source']} [{hits[col][0]['name']}]"
                                 for col, short in STREAMS if hits[col][0] is not None)
        rows.append({
            "Symbol": symbol,
            "NSE Symbol": ident["raw"],
            "ISIN": ident["isin_out"],
            "AMFI_Scheme_Code": ident["code"],
            "Scheme Name": ident["amfi_name"] or (lead_rec["name"] if lead_rec else "") or ident["nse_name"],
            "TER": ter_rec["value"] if ter_rec else "",
            "Tracking Error": te_rec["value"] if te_rec else "",
            "Tracking Difference": td_rec["value"] if td_rec else "",
            "Benchmark Index": bench,
            "Scheme Category": category,
            "Parser_Version": AMFI_PARSER_VERSION,
            "Match_Method": lead_method,
            "Match_Score": lead_score,
            "Source": source_text,
            "Fetch_Date": fetch_date,
        })
        notes = []
        if rejected is not None:
            split = f"; {rejected['detail']}" if rejected.get("detail") else ""
            notes.append(f"TER: AMFI total {rejected['value']:.2%} for {rejected['name']}"
                         f" ({_as_of_text(rejected) or 'undated'}{split}) is above the"
                         f" {MAX_REASONABLE_TER_DECIMAL:.2%} limit used for ETFs; left blank")
        for col, short in STREAMS:
            if ident["amc"] and indexes[col]["rows"] and ident["amc"] not in indexes[col]["amc"]:
                notes.append(f"{short}: no {ident['amc']} rows in the fetched AMFI period(s)")
        diag = {
            "Symbol": symbol,
            "ETF_AMC": ident["amc"],
            "AMFI_Scheme_Name": ident["amfi_name"],
            "Match_Method": lead_method,
            "Match_Score": lead_score,
        }
        for col, short in STREAMS:
            rec, method, score = hits[col]
            shown = rejected if col == "TER" and rejected is not None else rec
            diag[f"{short}_Method"] = method
            diag[f"{short}_Score"] = score
            diag[f"{short}_Scheme"] = shown["name"] if shown else ""
            diag[f"{short}_As_Of"] = _as_of_text(shown)
        diag.update({
            "TER_Filled": ter_rec is not None,
            "Tracking_Error_Filled": te_rec is not None,
            "Tracking_Difference_Filled": td_rec is not None,
            "Benchmark_Filled": bool(bench),
            "Matched_Source": source_text,
            "Matched_Scheme_Name": lead_rec["name"] if lead_rec else "",
            "Note": "; ".join(notes),
        })
        diags.append(diag)
    out = pd.DataFrame(rows)
    diag = pd.DataFrame(diags)
    by_method = {short: diag[f"{short}_Method"].value_counts().to_dict() for _, short in STREAMS} if len(diag) else {}
    log(log_rows, "Merge", "OK",
        f"TER={int(diag['TER_Filled'].sum()) if len(diag) else 0}, "
        f"Tracking_Error={int(diag['Tracking_Error_Filled'].sum()) if len(diag) else 0}, "
        f"Tracking_Difference={int(diag['Tracking_Difference_Filled'].sum()) if len(diag) else 0}, "
        f"Benchmark={int(diag['Benchmark_Filled'].sum()) if len(diag) else 0}; methods={by_method}", len(out))
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
        rejected = int(ter_df["TER_Rejected"].notna().sum()) if "TER_Rejected" in ter_df.columns else 0
        print(f"TER source rows standardized: {len(ter_df) - rejected}"
              + (f" (+{rejected} ETF rows above the {MAX_REASONABLE_TER_DECIMAL:.2%} TER limit, not used)" if rejected else ""))
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
    for _, short in STREAMS:
        if f"{short}_Method" in diag.columns:
            print(f"{short} match methods: {diag[f'{short}_Method'].value_counts().to_dict()}")
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
        "parser_version": AMFI_PARSER_VERSION if success else read_status().get("parser_version", ""),
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
