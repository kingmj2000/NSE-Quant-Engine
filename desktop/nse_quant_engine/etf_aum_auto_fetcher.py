"""
ETF AUM Auto Fetcher - Stage 3.5.6 AUM Validation Fix
=================================================================

Purpose
-------
Fetch ETF/scheme AUM on a monthly schedule and write an import file for
etf_metadata_enricher.py:

    data/etf_metadata_imports/auto_amfi_aum_latest.csv

Why this version exists
-----------------------
Earlier versions either:
- Tried static AMFI files like aum202606.xlsx, which do not exist, or
- Used AMFI JSON methods but could return zero standardized rows depending on
  the response shape / installed package behavior.

This version is more practical:
1. Keep smart monthly scheduling.
2. Prefer AMFI Excel-returning scheme-wise methods when amfipy is available.
3. Also try JSON/as_df methods for coverage.
4. Use NAV-enriched mapping from manual_etf_quality.csv / etf_metadata_enriched.csv
   so ETF Symbol -> AMFI Scheme Code works even if config.csv has no ISIN.
5. Manual fallback remains supported.
6. Never blocks the main workflow.

Recommended workflow order
--------------------------
    universe_builder.py
    etf_quality_builder.py
    etf_metadata_enricher.py          # builds NAV mapping first
    etf_aum_auto_fetcher.py           # uses NAV mapping to match AUM
    etf_metadata_enricher.py          # consumes AUM import and rebuilds manual quality
    etf_quality_builder.py            # rebuilds ETF quality
    nse_quant_engine.py
    validation_builder.py
    cross_sectional_validation.py
    trade_plan_builder.py
    news_market_builder.py

Run standalone
--------------
    python etf_aum_auto_fetcher.py

Force a refresh
---------------
    set FORCE_AUM_REFRESH=1
    python etf_aum_auto_fetcher.py
"""

from __future__ import annotations

import csv
import importlib.metadata
import json
import os
import re
import sys
import traceback
from datetime import date, datetime
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
IMPORT_DIR = DATA_DIR / "etf_metadata_imports"
DEBUG_DIR = DATA_DIR / "aum_debug"
OUTPUT_DIR = BASE_DIR / "output"
for p in [DATA_DIR, IMPORT_DIR, DEBUG_DIR, OUTPUT_DIR]:
    p.mkdir(exist_ok=True)

CONFIG_CSV = BASE_DIR / "config.csv"
MANUAL_QUALITY = BASE_DIR / "manual_etf_quality.csv"
ETF_METADATA_ENRICHED = DATA_DIR / "etf_metadata_enriched.csv"
NAVALL_CSV = DATA_DIR / "amfi_navall_latest.csv"

AUTO_IMPORT_LATEST = IMPORT_DIR / "auto_amfi_aum_latest.csv"
SOURCE_STD_OUT = DATA_DIR / "amfi_aum_source_standardized.csv"
MATCH_DIAG_OUT = DATA_DIR / "etf_aum_auto_match_diagnostics.csv"
LOG_OUT = DATA_DIR / "etf_aum_auto_fetch_log.csv"
STATUS_OUT = DATA_DIR / "etf_aum_auto_status.json"
DEBUG_REPORT_OUT = DATA_DIR / "etf_aum_auto_debug_report.md"

REFRESH_START_DAY = int(os.environ.get("AUM_REFRESH_START_DAY", "10"))
FORCE_REFRESH = os.environ.get("FORCE_AUM_REFRESH", "").strip().lower() in {"1", "yes", "true", "y"}

# Stage 3.5.6 guardrail:
# AMFI scheme codes are typically six-digit values around 100000-200000.
# If those get mistaken as AUM, the engine falsely thinks every ETF has huge AUM.
# Keep this conservative. If an Indian ETF ever exceeds 100000 crore AUM, we can raise it.
MAX_REASONABLE_ETF_AUM_CR = float(os.environ.get("MAX_REASONABLE_ETF_AUM_CR", "100000"))
SCHEME_CODE_MIN = 100000
SCHEME_CODE_MAX = 200000

NAVALL_URLS = [
    "https://www.amfiindia.com/spages/NAVAll.txt",
    "https://portal.amfiindia.com/spages/NAVAll.txt",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NSE-Quant-Engine/3.5.6",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.amfiindia.com/",
}

CODE_KEYS = [
    "scheme code", "schemecode", "scheme_code", "amfi scheme code", "amfischemecode",
    "scheme id", "schemeid", "code"
]
NAME_KEYS = [
    "scheme name", "schemename", "scheme_name", "scheme", "fund name", "fundname",
    "mutual fund scheme", "name", "nav name"
]
AUM_KEYS = [
    "average aum", "averageaum", "average_aum", "aaum", "aum", "aum cr", "aum_cr",
    "aum in crore", "aum rs cr", "aumincr", "average assets", "average net assets",
    "avg aum", "closing aum", "net aum", "average corpus", "corpus"
]
ISIN_KEYS = [
    "isin", "isin growth", "isin_1", "isin1", "isin div payout", "isin div reinvestment",
    "isin div payout/isin growth"
]

STOPWORDS = {
    "etf", "exchange", "traded", "fund", "funds", "scheme", "schemes", "regular", "direct",
    "growth", "dividend", "payout", "reinvestment", "plan", "index", "the", "and", "of",
    "mutual", "amc", "asset", "management", "company", "limited", "ltd", "india", "nse",
    "bse", "benchmark", "total", "return", "tri", "option", "open", "ended"
}
AMC_TOKENS = {
    "icici", "prudential", "icicipramc", "hdfc", "hdfcamc", "motilal", "oswal", "motilaloswal",
    "mirae", "axis", "sbi", "kotak", "nippon", "uti", "aditya", "birla", "sun", "life",
    "zerodha", "edelweiss", "bandhan", "invesco", "tata", "lic", "canara", "robeco", "dsp",
    "quantum", "mahindra", "manulife", "groww", "bajaj", "baroda", "bnp", "paribas"
}


def log(rows: list[dict], source: str, status: str, detail: str, count: int = 0) -> None:
    rows.append({
        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Source": source,
        "Status": status,
        "Detail": str(detail)[:2000],
        "Rows": count,
    })


def safe_version(pkg: str) -> str:
    try:
        return importlib.metadata.version(pkg)
    except Exception:
        return "not installed"


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
        log(log_rows, "Schedule", "FORCE", "FORCE_AUM_REFRESH=1; fetching regardless of date")
        return True

    if today.day < REFRESH_START_DAY:
        log(log_rows, "Schedule", "SKIP", f"Day {today.day} < refresh start day {REFRESH_START_DAY}; reusing last good file")
        return False

    last_success = str(status.get("last_success_run_date", "")).strip()
    if last_success and last_success[:7] == today.strftime("%Y-%m") and AUTO_IMPORT_LATEST.exists():
        if auto_import_file_has_valid_aum(AUTO_IMPORT_LATEST):
            log(log_rows, "Schedule", "SKIP", f"Already refreshed this month: {last_success}; reusing valid file")
            return False
        log(log_rows, "Schedule", "RETRY", "Existing monthly auto AUM file failed sanity checks; retrying refresh")
        return True

    log(log_rows, "Schedule", "ATTEMPT", f"Day {today.day} >= {REFRESH_START_DAY}; attempting AUM refresh")
    return True


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def norm_key(value: Any) -> str:
    return clean_text(value).replace(" ", "")


def tokens(value: Any, drop_amc: bool = False) -> list[str]:
    words = clean_text(value).split()
    out = []
    for w in words:
        if w in STOPWORDS:
            continue
        if drop_amc and w in AMC_TOKENS:
            continue
        out.append(w)
    return out


def combined_score(a: Any, b: Any) -> float:
    ta = set(tokens(a, drop_amc=True))
    tb = set(tokens(b, drop_amc=True))
    token_part = 0.0 if not ta or not tb else len(ta & tb) / max(len(ta), len(tb))
    aa = " ".join(tokens(a, drop_amc=True))
    bb = " ".join(tokens(b, drop_amc=True))
    seq_part = 0.0 if not aa or not bb else SequenceMatcher(None, aa, bb).ratio()
    return round(0.65 * token_part + 0.35 * seq_part, 4)


def parse_float(value: Any) -> float:
    if value is None:
        return np.nan
    try:
        if pd.isna(value):
            return np.nan
    except Exception:
        pass
    text = str(value).replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").replace("INR", "").strip()
    if not text or text.lower() in {"nan", "none", "-", "na", "n/a", "nil"}:
        return np.nan
    match = re.search(r"-?\d+(\.\d+)?", text)
    if not match:
        return np.nan
    try:
        return float(match.group(0))
    except Exception:
        return np.nan


def parse_aum_cr(value: Any) -> float:
    num = parse_float(value)
    if pd.isna(num):
        return np.nan
    text = str(value).lower() if value is not None else ""
    if "lakh crore" in text:
        return round(num * 100000, 2)
    if "crore" in text or " cr" in f" {text} " or text.strip().endswith("cr"):
        return round(num, 2)
    if "lakh" in text:
        return round(num / 100, 2)
    # AMFI AUM files generally report Rs crore. If a huge rupee value appears, convert it.
    if num > 1_000_000:
        return round(num / 10_000_000, 2)
    return round(num, 2)


def is_integer_like(value: Any, tolerance: float = 0.001) -> bool:
    num = parse_float(value)
    if pd.isna(num):
        return False
    return abs(num - round(num)) <= tolerance


def is_scheme_code_like_value(value: Any, scheme_code: Any = None) -> bool:
    """Detect the common failure where AMFI Scheme_Code is mistaken as AUM."""
    num = parse_float(value)
    if pd.isna(num):
        return False

    code = parse_float(scheme_code)
    if pd.notna(code) and abs(num - code) <= 0.01:
        return True

    # AMFI scheme codes are usually six-digit integer-ish values.
    if SCHEME_CODE_MIN <= num <= SCHEME_CODE_MAX and is_integer_like(num):
        return True

    return False


def validate_etf_aum_cr(value: Any, scheme_code: Any = None) -> tuple[bool, str, float]:
    aum = parse_aum_cr(value)
    if pd.isna(aum) or aum <= 0:
        return False, "blank_or_non_positive", np.nan
    if is_scheme_code_like_value(aum, scheme_code):
        return False, "looks_like_amfi_scheme_code", np.nan
    if aum > MAX_REASONABLE_ETF_AUM_CR:
        return False, f"above_reasonable_etf_aum_limit_{MAX_REASONABLE_ETF_AUM_CR:g}_cr", np.nan
    return True, "valid", round(float(aum), 2)


def series_scheme_code_like_ratio(values: pd.Series, code_values: pd.Series | None = None) -> float:
    nums = values.apply(parse_float)
    mask = nums.notna() & (nums > 0)
    if mask.sum() == 0:
        return 1.0
    scheme_like = nums[mask].apply(lambda x: is_scheme_code_like_value(x))
    if code_values is not None:
        codes = code_values.reindex(values.index).apply(parse_float)
        eq_code = (nums[mask] - codes[mask]).abs() <= 0.01
        scheme_like = scheme_like | eq_code.fillna(False)
    return float(scheme_like.mean())


def choose_aum_column(df: pd.DataFrame, code_col: str | None) -> str | None:
    """Choose the most plausible AUM column and reject scheme-code-like columns."""
    if df is None or df.empty:
        return None

    cols = list(df.columns)
    excluded = {c for c in [code_col] if c}
    candidates: list[tuple[float, str, str]] = []
    code_series = df[code_col] if code_col and code_col in df.columns else None

    named_cols = []
    for c in cols:
        n = norm_key(c)
        if c in excluded:
            continue
        if any(norm_key(k) in n for k in AUM_KEYS):
            if "source" in n or "method" in n or "date" in n:
                continue
            named_cols.append(c)

    # If header names are poor, also inspect numeric columns. Some AMFI Excel files
    # have multi-row headers and the real AUM column can have an unhelpful name.
    candidate_pool = list(dict.fromkeys(named_cols + [c for c in cols if c not in excluded]))

    for c in candidate_pool:
        nums = df[c].apply(parse_aum_cr)
        valid_num = nums.notna() & (nums > 0)
        if valid_num.sum() < max(5, min(25, len(df) * 0.01)):
            continue
        scheme_ratio = series_scheme_code_like_ratio(df[c], code_series)
        if scheme_ratio > 0.50:
            # This is almost certainly a scheme-code column, not AUM.
            continue
        name_bonus = 1000 if c in named_cols else 0
        realistic = nums[valid_num].between(0.01, MAX_REASONABLE_ETF_AUM_CR).mean()
        score = name_bonus + float(valid_num.sum()) + 100 * realistic - 500 * scheme_ratio
        candidates.append((score, c, f"valid={int(valid_num.sum())}; scheme_like={scheme_ratio:.2f}; realistic={realistic:.2f}"))

    if not candidates:
        return None
    candidates.sort(reverse=True, key=lambda x: x[0])
    return candidates[0][1]


def auto_import_file_has_valid_aum(path: Path) -> bool:
    try:
        df = pd.read_csv(path)
    except Exception:
        return False
    if "AUM Cr" not in df.columns:
        return False
    scheme_col = "Scheme_Code" if "Scheme_Code" in df.columns else None
    valid = []
    for _, r in df.iterrows():
        ok, _, _ = validate_etf_aum_cr(r.get("AUM Cr"), r.get(scheme_col) if scheme_col else None)
        valid.append(ok)
    return sum(valid) >= max(5, len(df) * 0.10)


def find_col(cols: list[str], keys: list[str]) -> str | None:
    norm_cols = {c: norm_key(c) for c in cols}
    key_norms = [norm_key(k) for k in keys]
    # exact/strong first
    for c, n in norm_cols.items():
        if n in key_norms:
            return c
    # containment next
    for c, n in norm_cols.items():
        if any(k in n for k in key_norms):
            return c
    return None


def flatten_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [" ".join([str(x) for x in tup if str(x) != "nan"]).strip() for tup in out.columns]
    else:
        out.columns = [str(c).strip() for c in out.columns]
    return out


def maybe_make_header_from_row(raw: pd.DataFrame, row_idx: int) -> pd.DataFrame:
    header = [str(x).strip() for x in raw.iloc[row_idx].tolist()]
    df = raw.iloc[row_idx + 1:].copy()
    df.columns = header
    df = df.dropna(how="all")
    return df


def dataframe_to_standardized(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    if df is None or df.empty:
        return empty_source_df()
    df = flatten_cols(df).dropna(how="all")
    cols = list(df.columns)
    code_col = find_col(cols, CODE_KEYS)
    name_col = find_col(cols, NAME_KEYS)
    isin_col = find_col(cols, ISIN_KEYS)
    aum_col = choose_aum_column(df, code_col)

    if aum_col is None or (name_col is None and code_col is None and isin_col is None):
        return empty_source_df()

    rows = []
    rejected = 0
    for _, r in df.iterrows():
        code = str(r.get(code_col, "")).strip() if code_col else ""
        name = str(r.get(name_col, "")).strip() if name_col else ""
        isin = str(r.get(isin_col, "")).strip().upper() if isin_col else ""
        ok, reason, aum = validate_etf_aum_cr(r.get(aum_col, np.nan), code)
        if not ok:
            rejected += 1
            continue
        if not any([code, name, isin]):
            continue
        if not code and not isin:
            low_name = clean_text(name)
            if low_name in {"total", "grand total", "open ended", "close ended", "interval fund"}:
                continue
        rows.append({
            "Scheme_Code": code,
            "Scheme_Name": name,
            "ISIN": isin,
            "AUM_Cr": aum,
            "AUM_Source": source_name,
        })
    if not rows:
        return empty_source_df()
    out = pd.DataFrame(rows)
    out = out.drop_duplicates(subset=["Scheme_Code", "Scheme_Name", "ISIN", "AUM_Cr"], keep="first")
    return out.reset_index(drop=True)

def excel_bytes_to_standardized(raw: bytes, source_name: str, log_rows: list[dict]) -> pd.DataFrame:
    try:
        xls = pd.ExcelFile(BytesIO(raw))
    except Exception as exc:
        log(log_rows, source_name, "EXCEL_OPEN_ERROR", exc, 0)
        return empty_source_df()

    pieces = []
    for sheet in xls.sheet_names:
        # Try normal headers first.
        for header in list(range(0, 16)) + [None]:
            try:
                if header is None:
                    raw_df = pd.read_excel(BytesIO(raw), sheet_name=sheet, header=None)
                    # Build headers from likely rows.
                    candidate_rows = []
                    for i, rr in raw_df.head(30).iterrows():
                        row_text = " ".join(clean_text(v) for v in rr.tolist())
                        if any(k in row_text for k in ["scheme", "average", "aum", "aaum", "isin"]):
                            candidate_rows.append(i)
                    for ridx in candidate_rows[:10]:
                        std = dataframe_to_standardized(maybe_make_header_from_row(raw_df, ridx), f"{source_name}::{sheet}::headerrow{ridx}")
                        if len(std) > 0:
                            pieces.append(std)
                    break
                else:
                    df = pd.read_excel(BytesIO(raw), sheet_name=sheet, header=header)
                    std = dataframe_to_standardized(df, f"{source_name}::{sheet}::header{header}")
                    if len(std) > 0:
                        pieces.append(std)
                        # Continue scanning: some sheets have multiple possible blocks.
            except Exception:
                continue
    if not pieces:
        return empty_source_df()
    out = pd.concat(pieces, ignore_index=True).drop_duplicates().reset_index(drop=True)
    return out


def empty_source_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["Scheme_Code", "Scheme_Name", "ISIN", "AUM_Cr", "AUM_Source"])


def object_to_standardized(obj: Any, source_name: str, log_rows: list[dict]) -> pd.DataFrame:
    if obj is None:
        return empty_source_df()
    # bytes Excel
    if isinstance(obj, (bytes, bytearray)):
        return excel_bytes_to_standardized(bytes(obj), source_name, log_rows)
    # Polars DataFrame
    if hasattr(obj, "to_pandas"):
        try:
            return dataframe_to_standardized(obj.to_pandas(), source_name)
        except Exception:
            pass
    if isinstance(obj, pd.DataFrame):
        return dataframe_to_standardized(obj, source_name)
    # list/dict JSON-like
    rows = []

    def find_value(row: dict, keys: list[str]) -> Any:
        norm_map = {norm_key(k): v for k, v in row.items()}
        for k in keys:
            nk = norm_key(k)
            if nk in norm_map and str(norm_map[nk]).strip() not in {"", "nan", "None"}:
                return norm_map[nk]
        for k, v in row.items():
            nk = norm_key(k)
            if any(norm_key(candidate) in nk for candidate in keys):
                if str(v).strip() not in {"", "nan", "None"}:
                    return v
        return ""

    def has_identity(row: dict) -> bool:
        return any(str(find_value(row, keys)).strip() for keys in [CODE_KEYS, NAME_KEYS, ISIN_KEYS])

    def has_aum(row: dict) -> bool:
        return str(find_value(row, AUM_KEYS)).strip() not in {"", "nan", "None"}

    def flatten(item: Any, parent: dict | None = None) -> None:
        parent = parent or {}
        if isinstance(item, dict):
            flat = dict(parent)
            nested = []
            for k, v in item.items():
                if isinstance(v, (dict, list)):
                    nested.append((k, v))
                else:
                    flat[str(k)] = v
            if has_identity(flat) and has_aum(flat):
                code = str(find_value(flat, CODE_KEYS)).strip()
                name = str(find_value(flat, NAME_KEYS)).strip()
                isin = str(find_value(flat, ISIN_KEYS)).strip().upper()
                aum = parse_aum_cr(find_value(flat, AUM_KEYS))
                if pd.notna(aum) and aum > 0:
                    rows.append({
                        "Scheme_Code": code,
                        "Scheme_Name": name,
                        "ISIN": isin,
                        "AUM_Cr": aum,
                        "AUM_Source": source_name,
                    })
            for _, v in nested:
                flatten(v, flat)
        elif isinstance(item, list):
            for x in item:
                flatten(x, parent)

    flatten(obj)
    if not rows:
        return empty_source_df()
    return pd.DataFrame(rows).drop_duplicates().reset_index(drop=True)


def save_debug_bytes(raw: bytes, filename: str) -> None:
    try:
        (DEBUG_DIR / filename).write_bytes(raw)
    except Exception:
        pass


def save_debug_text(text: str, filename: str) -> None:
    try:
        (DEBUG_DIR / filename).write_text(text[:2_000_000], encoding="utf-8")
    except Exception:
        pass


def get_latest_fy_periods(aum: Any, log_rows: list[dict]) -> list[tuple[Any, Any, str]]:
    """Return list of (fy_id, period_id, label), latest-first as best effort."""
    combos: list[tuple[Any, Any, str]] = []
    try:
        fys = aum.financial_years()
        log(log_rows, "amfipy.financial_years", "OK", str(fys)[:500], len(fys) if hasattr(fys, "__len__") else 0)
    except Exception as exc:
        log(log_rows, "amfipy.financial_years", "ERROR", repr(exc), 0)
        return combos

    fy_items = []
    if isinstance(fys, list):
        for f in fys:
            if isinstance(f, dict):
                fid = f.get("id") or f.get("fy_id") or f.get("financialYearId")
                label = f.get("financial_year") or f.get("title") or f.get("name") or str(f)
                if fid is not None:
                    fy_items.append((fid, str(label)))
            else:
                fy_items.append((f, str(f)))

    for fy_id, fy_label in fy_items[:5]:
        try:
            periods_obj = aum.periods(fy_id)
            log(log_rows, "amfipy.periods", "OK", f"fy={fy_id}; {str(periods_obj)[:500]}", 0)
        except Exception as exc:
            log(log_rows, "amfipy.periods", "ERROR", f"fy={fy_id}; {repr(exc)}", 0)
            continue
        periods = periods_obj.get("periods", periods_obj) if isinstance(periods_obj, dict) else periods_obj
        if not isinstance(periods, list):
            continue
        for p in periods[:8]:
            if isinstance(p, dict):
                pid = p.get("id") or p.get("period_id") or p.get("periodId")
                plabel = p.get("period") or p.get("title") or p.get("name") or str(p)
            else:
                pid = p
                plabel = str(p)
            if pid is not None:
                combos.append((fy_id, pid, f"{fy_label} | {plabel}"))
    return combos


def try_amfipy_excel_first(log_rows: list[dict]) -> pd.DataFrame:
    log(log_rows, "Environment", "INFO", f"python={sys.version.split()[0]}; amfipy={safe_version('amfipy')}; httpx={safe_version('httpx')}; pandas={safe_version('pandas')}", 0)
    try:
        from amfipy import AMFIClient  # type: ignore
    except Exception as exc:
        log(log_rows, "amfipy", "IMPORT_ERROR", repr(exc), 0)
        return empty_source_df()
    try:
        client = AMFIClient()
        aum = client.aum
    except Exception as exc:
        log(log_rows, "amfipy", "CLIENT_ERROR", repr(exc), 0)
        return empty_source_df()

    pieces = []

    # A. Quarterly scheme-wise average AUM Excel. This is the highest-value path for ETF quality.
    combos = get_latest_fy_periods(aum, log_rows)
    for fy_id, period_id, label in combos[:16]:
        for str_type in ["Categorywise", "Typewise"]:
            try:
                raw = aum.average_aum_schemewise_excel(fy_id, period_id, str_type=str_type, mf_id=0)
                if isinstance(raw, (bytes, bytearray)) and len(raw) > 500:
                    fname = f"average_aum_schemewise_{fy_id}_{period_id}_{str_type}.xlsx".replace("/", "_").replace(" ", "_")
                    save_debug_bytes(bytes(raw), fname)
                    df = object_to_standardized(raw, f"amfipy.average_aum_schemewise_excel:{label}:{str_type}", log_rows)
                    log(log_rows, "amfipy.average_aum_schemewise_excel", "OK", f"fy={fy_id}, period={period_id}, type={str_type}; rows={len(df)}", len(df))
                    if len(df) > 0:
                        pieces.append(df)
                        # One good quarterly source is enough.
                        return finalize_source_pieces(pieces)
                else:
                    log(log_rows, "amfipy.average_aum_schemewise_excel", "EMPTY", f"fy={fy_id}, period={period_id}, type={str_type}; returned {type(raw)} len={len(raw) if hasattr(raw, '__len__') else 'NA'}", 0)
            except Exception as exc:
                log(log_rows, "amfipy.average_aum_schemewise_excel", "ERROR", f"fy={fy_id}, period={period_id}, type={str_type}; {repr(exc)}", 0)

            # JSON/as_df fallback for the same combo.
            for as_df in [True, False]:
                try:
                    obj = aum.average_aum_schemewise(fy_id, period_id, str_type=str_type, mf_id=0, as_df=as_df)
                    save_debug_text(json.dumps(obj, default=str, indent=2), f"average_aum_schemewise_{fy_id}_{period_id}_{str_type}_asdf_{as_df}.json")
                    df = object_to_standardized(obj, f"amfipy.average_aum_schemewise:{label}:{str_type}:as_df={as_df}", log_rows)
                    log(log_rows, "amfipy.average_aum_schemewise", "OK", f"fy={fy_id}, period={period_id}, type={str_type}, as_df={as_df}; rows={len(df)}", len(df))
                    if len(df) > 0:
                        pieces.append(df)
                        return finalize_source_pieces(pieces)
                except Exception as exc:
                    log(log_rows, "amfipy.average_aum_schemewise", "ERROR", f"fy={fy_id}, period={period_id}, type={str_type}, as_df={as_df}; {repr(exc)}", 0)

    # B. Monthly classified scheme-category-wise Excel fallback.
    try:
        dates = aum.classified_dates(mf_id=0)
        log(log_rows, "amfipy.classified_dates", "OK", str(dates)[:500], len(dates) if hasattr(dates, "__len__") else 0)
        date_values = []
        if isinstance(dates, list):
            for d in dates:
                if isinstance(d, dict):
                    v = d.get("date") or d.get("monthYear") or d.get("MonthYear") or d.get("month")
                else:
                    v = str(d)
                if v:
                    date_values.append(str(v))
        else:
            date_values = [str(dates)]
    except Exception as exc:
        log(log_rows, "amfipy.classified_dates", "ERROR", repr(exc), 0)
        date_values = []

    def norm_month_date(v: str) -> str:
        v = v.strip()
        m = re.match(r"([A-Za-z]+)[-\s]+(\d{4})", v)
        if m:
            return f"01-{m.group(1)[:3].lower()}-{m.group(2)}"
        return v.lower()

    for d in date_values[:8]:
        nd = norm_month_date(d)
        try:
            raw = aum.scheme_catwise_excel(nd, mf_id=0)
            if isinstance(raw, (bytes, bytearray)) and len(raw) > 500:
                fname = f"scheme_catwise_{nd}.xlsx".replace("/", "_").replace(" ", "_")
                save_debug_bytes(bytes(raw), fname)
                df = object_to_standardized(raw, f"amfipy.scheme_catwise_excel:{nd}", log_rows)
                log(log_rows, "amfipy.scheme_catwise_excel", "OK", f"date={nd}; rows={len(df)}", len(df))
                if len(df) > 0:
                    pieces.append(df)
                    return finalize_source_pieces(pieces)
            else:
                log(log_rows, "amfipy.scheme_catwise_excel", "EMPTY", f"date={nd}; returned {type(raw)} len={len(raw) if hasattr(raw, '__len__') else 'NA'}", 0)
        except Exception as exc:
            log(log_rows, "amfipy.scheme_catwise_excel", "ERROR", f"date={nd}; {repr(exc)}", 0)

        for as_df in [True, False]:
            try:
                obj = aum.scheme_catwise(nd, mf_id=0, as_df=as_df)
                save_debug_text(json.dumps(obj, default=str, indent=2), f"scheme_catwise_{nd}_as_df_{as_df}.json")
                df = object_to_standardized(obj, f"amfipy.scheme_catwise:{nd}:as_df={as_df}", log_rows)
                log(log_rows, "amfipy.scheme_catwise", "OK", f"date={nd}, as_df={as_df}; rows={len(df)}", len(df))
                if len(df) > 0:
                    pieces.append(df)
                    return finalize_source_pieces(pieces)
            except Exception as exc:
                log(log_rows, "amfipy.scheme_catwise", "ERROR", f"date={nd}, as_df={as_df}; {repr(exc)}", 0)

    return finalize_source_pieces(pieces)


def finalize_source_pieces(pieces: list[pd.DataFrame]) -> pd.DataFrame:
    if not pieces:
        return empty_source_df()
    out = pd.concat(pieces, ignore_index=True).drop_duplicates().reset_index(drop=True)
    out.to_csv(SOURCE_STD_OUT, index=False)
    return out


def load_manual_aum(log_rows: list[dict]) -> pd.DataFrame:
    files = sorted(
        list(IMPORT_DIR.glob("amfi_aum_manual*.csv")) + list(IMPORT_DIR.glob("amfi_aum_manual*.xls*")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        log(log_rows, "ManualFallback", "NO_FILE", str(IMPORT_DIR), 0)
        return empty_source_df()
    path = files[0]
    try:
        if path.suffix.lower() == ".csv":
            raw = pd.read_csv(path)
            std = dataframe_to_standardized(raw, f"manual:{path.name}")
        else:
            std = excel_bytes_to_standardized(path.read_bytes(), f"manual:{path.name}", log_rows)
        log(log_rows, "ManualFallback", "OK", f"{path.name}; rows={len(std)}", len(std))
        if len(std) > 0:
            std.to_csv(SOURCE_STD_OUT, index=False)
        return std
    except Exception as exc:
        log(log_rows, "ManualFallback", "ERROR", f"{path.name}: {repr(exc)}", 0)
        return empty_source_df()


def fetch_navall(log_rows: list[dict]) -> pd.DataFrame:
    if NAVALL_CSV.exists():
        try:
            df = pd.read_csv(NAVALL_CSV, dtype=str)
            log(log_rows, "NAVAll", "CACHE_OK", str(NAVALL_CSV), len(df))
            return df
        except Exception as exc:
            log(log_rows, "NAVAll", "CACHE_ERROR", repr(exc), 0)
    for url in NAVALL_URLS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=45)
            r.raise_for_status()
            rows = []
            for line in r.text.splitlines():
                if ";" not in line:
                    continue
                parts = [p.strip() for p in line.split(";")]
                if len(parts) < 6 or parts[0].lower().startswith("scheme"):
                    continue
                code, isin1, isin2, scheme, nav, nav_date = parts[:6]
                rows.append({
                    "Scheme_Code": code,
                    "ISIN_1": isin1,
                    "ISIN_2": isin2,
                    "Scheme_Name": scheme,
                    "NAV": nav,
                    "NAV_Date": nav_date,
                })
            df = pd.DataFrame(rows)
            df.to_csv(NAVALL_CSV, index=False)
            log(log_rows, "NAVAll", "FETCH_OK", url, len(df))
            return df
        except Exception as exc:
            log(log_rows, "NAVAll", "FETCH_ERROR", f"{url}: {repr(exc)}", 0)
    return pd.DataFrame()


def build_nav_mappings(nav: pd.DataFrame) -> tuple[dict[str, str], dict[str, str], pd.DataFrame]:
    """Return ISIN->code, scheme_code->scheme_name, names df."""
    if nav.empty:
        return {}, {}, pd.DataFrame(columns=["Scheme_Code", "Scheme_Name"])
    nav = nav.copy()
    nav.columns = [str(c).strip() for c in nav.columns]
    code_col = find_col(list(nav.columns), CODE_KEYS) or nav.columns[0]
    name_col = find_col(list(nav.columns), NAME_KEYS)
    isin_cols = [c for c in nav.columns if "isin" in str(c).lower()]
    isin_to_code = {}
    code_to_name = {}
    for _, row in nav.iterrows():
        code = str(row.get(code_col, "")).strip()
        if not code:
            continue
        name = str(row.get(name_col, "")).strip() if name_col else ""
        if name:
            code_to_name[code] = name
        for col in isin_cols:
            isin = str(row.get(col, "")).strip().upper()
            if isin and isin.lower() not in {"nan", "none", "-"}:
                isin_to_code[isin] = code
    name_df = pd.DataFrame({"Scheme_Code": list(code_to_name.keys()), "Scheme_Name": list(code_to_name.values())})
    return isin_to_code, code_to_name, name_df


def load_etf_universe() -> pd.DataFrame:
    if not CONFIG_CSV.exists():
        raise FileNotFoundError("config.csv not found. Run universe_builder.py first.")
    cfg = pd.read_csv(CONFIG_CSV, dtype=str)
    universe_col = "Universe_Group" if "Universe_Group" in cfg.columns else "Universe" if "Universe" in cfg.columns else None
    if universe_col is None:
        raise ValueError("config.csv needs Universe_Group or Universe column.")
    etfs = cfg[cfg[universe_col].astype(str).str.upper().eq("ETF")].copy()
    if "Raw_Symbol" not in etfs.columns:
        etfs["Raw_Symbol"] = etfs.get("Symbol", "").astype(str).str.replace(".NS", "", regex=False)
    if "ISIN" not in etfs.columns:
        etfs["ISIN"] = ""
    if "Name" not in etfs.columns:
        etfs["Name"] = etfs.get("Raw_Symbol", etfs.get("Symbol", ""))
    return etfs.reset_index(drop=True)


def load_nav_enriched_symbol_map(log_rows: list[dict]) -> pd.DataFrame:
    pieces = []
    for path in [ETF_METADATA_ENRICHED, MANUAL_QUALITY]:
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, dtype=str)
            cols = set(df.columns)
            if "Symbol" not in cols:
                continue
            keep_cols = [c for c in ["Symbol", "AMFI_Scheme_Code", "AMFI_Scheme_Name", "Manual_NAV", "Manual_NAV_Date", "NAV_Match_Score", "NAV_Match_Method"] if c in cols]
            tmp = df[keep_cols].copy()
            tmp["Map_Source"] = path.name
            pieces.append(tmp)
            log(log_rows, "NAVEnrichedMap", "OK", f"{path.name}; rows={len(tmp)}", len(tmp))
        except Exception as exc:
            log(log_rows, "NAVEnrichedMap", "ERROR", f"{path.name}: {repr(exc)}", 0)
    if not pieces:
        return pd.DataFrame()
    out = pd.concat(pieces, ignore_index=True).drop_duplicates(subset=["Symbol"], keep="first")
    return out


def merge_aum_to_etfs(etfs: pd.DataFrame, aum_df: pd.DataFrame, log_rows: list[dict]) -> pd.DataFrame:
    nav = fetch_navall(log_rows)
    isin_to_code, code_to_name, nav_names = build_nav_mappings(nav)
    nav_enriched = load_nav_enriched_symbol_map(log_rows)
    enriched_by_symbol = {}
    if not nav_enriched.empty:
        enriched_by_symbol = {str(r["Symbol"]).strip().upper(): r for _, r in nav_enriched.iterrows()}

    aum = aum_df.copy()
    for col in ["Scheme_Code", "Scheme_Name", "ISIN", "AUM_Cr", "AUM_Source"]:
        if col not in aum.columns:
            aum[col] = ""
    aum["Scheme_Code"] = aum["Scheme_Code"].astype(str).str.strip()
    aum["Scheme_Name"] = aum["Scheme_Name"].astype(str).str.strip()
    aum["ISIN"] = aum["ISIN"].astype(str).str.upper().str.strip()

    aum_by_code = {str(r["Scheme_Code"]).strip(): r for _, r in aum.iterrows() if str(r.get("Scheme_Code", "")).strip()}
    aum_by_isin = {str(r["ISIN"]).strip(): r for _, r in aum.iterrows() if str(r.get("ISIN", "")).strip() and str(r.get("ISIN", "")).lower() not in {"nan", "none"}}

    diag_rows = []
    results = []
    for _, e in etfs.iterrows():
        symbol = str(e.get("Symbol", "")).strip()
        sym_key = symbol.upper()
        raw_symbol = str(e.get("Raw_Symbol", symbol)).replace(".NS", "").strip()
        name = str(e.get("Name", raw_symbol)).strip()
        isin = str(e.get("ISIN", "")).strip().upper()

        match = None
        method = "No match"
        score = 0.0
        scheme_code = ""
        scheme_name = ""

        # 1. Existing NAV-enriched mapping from etf_metadata_enricher.
        enr = enriched_by_symbol.get(sym_key)
        if enr is not None:
            sc = str(enr.get("AMFI_Scheme_Code", "")).strip()
            sn = str(enr.get("AMFI_Scheme_Name", "")).strip()
            if sc:
                scheme_code = sc
                scheme_name = sn
                if sc in aum_by_code:
                    match = aum_by_code[sc]
                    method = "NAV-enriched Symbol -> AMFI_Scheme_Code"
                    score = 1.0

        # 2. Direct ISIN in AUM source.
        if match is None and isin and isin.lower() not in {"nan", "none", "-"}:
            if isin in aum_by_isin:
                match = aum_by_isin[isin]
                method = "AUM ISIN"
                score = 1.0
                scheme_code = str(match.get("Scheme_Code", "")).strip()
            elif isin in isin_to_code and isin_to_code[isin] in aum_by_code:
                scheme_code = isin_to_code[isin]
                match = aum_by_code[scheme_code]
                method = "NAVAll ISIN -> Scheme_Code"
                score = 1.0

        # 3. Fuzzy fallback against AUM names plus NAV names.
        if match is None:
            candidates = []
            for idx, r in aum.iterrows():
                candidate_name = str(r.get("Scheme_Name", "")).strip()
                if not candidate_name:
                    code = str(r.get("Scheme_Code", "")).strip()
                    candidate_name = code_to_name.get(code, "")
                if not candidate_name:
                    continue
                s = max(combined_score(name, candidate_name), combined_score(raw_symbol, candidate_name), combined_score(scheme_name, candidate_name))
                candidates.append((s, idx, candidate_name))
            if candidates:
                candidates.sort(reverse=True, key=lambda x: x[0])
                best_score, best_idx, best_name = candidates[0]
                if best_score >= 0.72:
                    match = aum.loc[best_idx]
                    method = "Fuzzy Scheme_Name"
                    score = float(best_score)
                    scheme_code = str(match.get("Scheme_Code", "")).strip()
                    scheme_name = best_name
                else:
                    diag_rows.append({
                        "Symbol": symbol,
                        "ISIN": isin,
                        "Name": name,
                        "Known_AMFI_Scheme_Code": scheme_code,
                        "Known_AMFI_Scheme_Name": scheme_name,
                        "Best_Candidate": best_name,
                        "Best_Score": best_score,
                        "Decision": "Below fuzzy threshold",
                    })

        aum_cr = ""
        source = ""
        aum_validation = "no_match"
        if match is not None:
            candidate_aum = match.get("AUM_Cr", "")
            source = match.get("AUM_Source", "")
            if not scheme_code:
                scheme_code = str(match.get("Scheme_Code", "")).strip()
            if not scheme_name:
                scheme_name = str(match.get("Scheme_Name", "")).strip() or code_to_name.get(scheme_code, "")
            ok, reason, clean_aum = validate_etf_aum_cr(candidate_aum, scheme_code)
            aum_validation = reason
            if ok:
                aum_cr = clean_aum
            else:
                # Keep the row for diagnostics but do not write fake AUM into the import file.
                method = f"{method}; AUM rejected: {reason}"
                source = source or "Rejected source"

        results.append({
            "Symbol": symbol,
            "NSE Symbol": raw_symbol,
            "ISIN": isin,
            "Scheme Name": scheme_name or name,
            "AUM Cr": aum_cr if pd.notna(aum_cr) else "",
            "AUM_Source": source,
            "Match_Method": method,
            "Scheme_Code": scheme_code,
            "AUM_Match_Score": score,
            "AUM_Validation": aum_validation,
            "Fetch_Date": datetime.now().strftime("%Y-%m-%d"),
        })

    out = pd.DataFrame(results)
    filled = out["AUM Cr"].apply(lambda x: pd.notna(x) and str(x).strip() not in {"", "nan", "None"}).sum()
    log(log_rows, "Merge", "OK", f"Matched AUM {filled}/{len(out)} ETF rows", int(filled))
    pd.DataFrame(diag_rows).to_csv(MATCH_DIAG_OUT, index=False)
    return out


def write_debug_report(log_rows: list[dict], aum_df: pd.DataFrame | None = None, output: pd.DataFrame | None = None) -> None:
    lines = [
        "# ETF AUM Auto Fetch Debug Report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Environment",
        "",
        f"- Python: {sys.version.split()[0]}",
        f"- amfipy: {safe_version('amfipy')}",
        f"- httpx: {safe_version('httpx')}",
        f"- pandas: {safe_version('pandas')}",
        f"- openpyxl: {safe_version('openpyxl')}",
        f"- xlrd: {safe_version('xlrd')}",
        "",
        "## Coverage",
        "",
    ]
    if aum_df is not None:
        lines.append(f"- Standardized AUM source rows: {len(aum_df)}")
    if output is not None and not output.empty and "AUM Cr" in output.columns:
        filled = output["AUM Cr"].apply(lambda x: pd.notna(x) and str(x).strip() not in {"", "nan", "None"}).sum()
        lines.append(f"- ETF AUM matched rows: {filled} / {len(output)}")
    lines += ["", "## Log tail", "", "| Source | Status | Rows | Detail |", "|---|---:|---:|---|"]
    for r in log_rows[-40:]:
        detail = str(r.get("Detail", "")).replace("|", "/")[:300]
        lines.append(f"| {r.get('Source','')} | {r.get('Status','')} | {r.get('Rows','')} | {detail} |")
    lines += ["", "## Debug files", "", f"Raw/debug files, if any: `{DEBUG_DIR}`"]
    DEBUG_REPORT_OUT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    print("ETF AUM Auto Fetcher - Stage 3.5.6 AUM Validation Fix")
    print("================================================================")
    log_rows: list[dict] = []
    aum_df = empty_source_df()
    output = pd.DataFrame()

    try:
        etfs = load_etf_universe()
        print(f"ETF universe: {len(etfs)} rows")
    except Exception as exc:
        print(f"WARNING: {exc}. Skipping AUM fetch.")
        log(log_rows, "Universe", "ERROR", repr(exc), 0)
        pd.DataFrame(log_rows).to_csv(LOG_OUT, index=False)
        write_debug_report(log_rows)
        return 0

    if not should_attempt_refresh(log_rows):
        if AUTO_IMPORT_LATEST.exists():
            print(f"Skipped AUM fetch. Reusing: {AUTO_IMPORT_LATEST}")
        else:
            print("Skipped AUM fetch and no previous auto AUM file exists.")
        pd.DataFrame(log_rows).to_csv(LOG_OUT, index=False)
        write_debug_report(log_rows)
        return 0

    print("Fetching scheme-wise AUM from AMFI using Excel-first method...")
    aum_df = try_amfipy_excel_first(log_rows)

    if aum_df.empty:
        print("AMFI automatic fetch produced 0 standardized rows. Trying manual fallback file...")
        aum_df = load_manual_aum(log_rows)

    if aum_df.empty:
        print("No AUM source rows available. Workflow will continue with last good/manual data if present.")
        write_status({**read_status(), "last_attempt_run_date": date.today().isoformat(), "last_attempt_status": "no_source_rows"})
        pd.DataFrame(log_rows).to_csv(LOG_OUT, index=False)
        write_debug_report(log_rows, aum_df)
        return 0

    aum_df.to_csv(SOURCE_STD_OUT, index=False)
    print(f"AUM source rows standardized: {len(aum_df)}")

    output = merge_aum_to_etfs(etfs, aum_df, log_rows)
    filled = output["AUM Cr"].apply(lambda x: pd.notna(x) and str(x).strip() not in {"", "nan", "None"}).sum()

    period_str = datetime.now().strftime("%Y_%m")
    period_out = IMPORT_DIR / f"auto_amfi_aum_{period_str}.csv"
    output.to_csv(period_out, index=False)
    output.to_csv(AUTO_IMPORT_LATEST, index=False)

    print(f"AUM import saved: {AUTO_IMPORT_LATEST}")
    print(f"Period file: {period_out}")
    print(f"AUM filled: {filled} / {len(output)}")

    status = {**read_status(), "last_attempt_run_date": date.today().isoformat(), "last_attempt_status": "success" if filled > 0 else "matched_zero"}
    if filled > 0:
        status.update({"last_success_run_date": date.today().isoformat(), "last_success_period": period_str, "last_success_rows": int(filled)})
    write_status(status)
    pd.DataFrame(log_rows).to_csv(LOG_OUT, index=False)
    write_debug_report(log_rows, aum_df, output)

    if filled == 0:
        print("No ETF AUM matched. Check data/etf_aum_auto_debug_report.md and data/aum_debug.")
    elif filled < len(output) * 0.50:
        print("Partial AUM coverage. Check data/etf_aum_auto_match_diagnostics.csv.")
    else:
        print("Good AUM coverage. Continue full workflow to rebuild ETF quality.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("WARNING: ETF AUM auto-fetcher hit an unexpected error. Workflow continues.")
        traceback.print_exc()
        raise SystemExit(0)
