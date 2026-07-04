"""
ETF Metadata Enricher - Stage 3.5.10.1 Consensus Import Coalesce + Quality Status
==========================================================

This script improves ETF quality evaluation by doing two practical things:
1. Fetches daily NAV directly from AMFI NAVAll.txt.
2. Reads AUM / TER / tracking metadata files from data/etf_metadata_imports.

Why this approach:
- AMFI NAVAll reliably provides NAV, scheme code, ISIN, scheme name, and NAV date.
- AUM is not present in NAVAll and is not cleanly mapped to NSE tickers in one stable free feed.
- So NAV is automatic; AUM/TER/tracking are imported or preserved only if they pass sanity checks.

Outputs:
- manual_etf_quality.csv
- data/amfi_navall_latest.csv
- data/etf_metadata_enriched.csv
- data/etf_metadata_match_diagnostics.csv
- data/etf_metadata_source_log.csv
- data/etf_metadata_import_standardized.csv
"""

from __future__ import annotations

from pathlib import Path
from difflib import SequenceMatcher
from datetime import datetime
import re
import os
import sys
import traceback
import requests
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
OUTPUT_DIR = BASE_DIR / 'output'
IMPORT_DIR = DATA_DIR / 'etf_metadata_imports'
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
IMPORT_DIR.mkdir(exist_ok=True)

CONFIG_CSV = BASE_DIR / 'config.csv'
MANUAL_QUALITY = BASE_DIR / 'manual_etf_quality.csv'
MAPPING_SUGGESTIONS = DATA_DIR / 'etf_mapping_suggestions.csv'

AMFI_NAVALL_URLS = [
    'https://www.amfiindia.com/spages/NAVAll.txt',
    'https://portal.amfiindia.com/spages/NAVAll.txt',
]

AMFI_NAV_OUT = DATA_DIR / 'amfi_navall_latest.csv'
ENRICHED_OUT = DATA_DIR / 'etf_metadata_enriched.csv'
DIAGNOSTICS_OUT = DATA_DIR / 'etf_metadata_match_diagnostics.csv'
SOURCE_LOG_OUT = DATA_DIR / 'etf_metadata_source_log.csv'
IMPORT_STANDARDIZED_OUT = DATA_DIR / 'etf_metadata_import_standardized.csv'
UNRESOLVED_REVIEW_OUT = DATA_DIR / 'etf_metadata_unresolved_review.csv'

OUTPUT_COLUMNS = [
    'Symbol', 'Raw_Symbol', 'Name', 'Universe_Group', 'Opportunity_Type', 'ISIN',
    'AMFI_Scheme_Code', 'AMFI_Scheme_Name', 'Manual_NAV', 'Manual_NAV_Date',
    'NAV_Source', 'NAV_Match_Method', 'NAV_Match_Score',
    'AUM_Cr', 'TER', 'Tracking_Error', 'Tracking_Difference', 'Benchmark_Index',
    'AUM_Source', 'TER_Source', 'Tracking_Error_Source', 'Tracking_Difference_Source', 'Benchmark_Source',
    'Metadata_Confidence', 'Metadata_Flags', 'Last_Updated'
]

QUALITY_FIELDS = ['AUM_Cr', 'TER', 'Tracking_Error', 'Tracking_Difference', 'Benchmark_Index']

STOPWORDS = {
    'etf','exchange','traded','fund','funds','scheme','schemes','regular','direct',
    'growth','dividend','payout','reinvestment','plan','index','the','and','of',
    'mutual','amc','asset','management','company','limited','ltd','india','nse',
    'bse','benchmark','total','return','tri'
}
AMC_TOKENS = {
    'icici','prudential','icicipramc','hdfc','hdfcamc','motilal','oswal','mirae','axis','sbi',
    'kotak','nippon','uti','aditya','birla','sun','life','zerodha','edelweiss','bandhan',
    'invesco','tata','lic','canara','robeco','dsp','quantum','mahindra','manulife','groww',
    'bajaj','baroda','bnp','paribas'
}


def log_event(rows, source, status, detail, rows_count=0):
    rows.append({
        'Timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'Source': source,
        'Status': status,
        'Rows': rows_count,
        'Detail': detail,
    })


def clean_text(value):
    if pd.isna(value):
        return ''
    text = str(value).lower().replace('&', ' and ')
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def tokens(value, drop_amc=False):
    words = clean_text(value).split()
    out = []
    for w in words:
        if w in STOPWORDS:
            continue
        if drop_amc and w in AMC_TOKENS:
            continue
        out.append(w)
    return out


def norm_for_match(value, drop_amc=False):
    return ' '.join(tokens(value, drop_amc=drop_amc))


def token_score(a, b):
    ta = set(tokens(a, drop_amc=True))
    tb = set(tokens(b, drop_amc=True))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def seq_score(a, b):
    aa = norm_for_match(a, drop_amc=True)
    bb = norm_for_match(b, drop_amc=True)
    if not aa or not bb:
        return 0.0
    return SequenceMatcher(None, aa, bb).ratio()


def combined_score(a, b):
    return 0.60 * token_score(a, b) + 0.40 * seq_score(a, b)


def first_existing(row, candidates):
    for c in candidates:
        if c in row.index:
            v = row.get(c)
            if pd.notna(v) and str(v).strip() != '':
                return v
    return ''


def find_col(df, patterns):
    norm_cols = {c: clean_text(c) for c in df.columns}
    for pat in patterns:
        p = clean_text(pat)
        for col, norm in norm_cols.items():
            if p == norm or p in norm:
                return col
    return None


def parse_float(value):
    if pd.isna(value):
        return np.nan
    text = str(value).strip()
    if not text or text.lower() in ['nan','none','-','na','n/a','nil']:
        return np.nan
    text = text.replace(',', '').replace('₹', '').replace('rs.', '').replace('rs', '').strip()
    m = re.search(r'-?\d+(\.\d+)?', text)
    if not m:
        return np.nan
    try:
        return float(m.group(0))
    except Exception:
        return np.nan


def parse_amount_to_cr(value):
    if pd.isna(value):
        return np.nan
    text = str(value).lower().replace(',', '').strip()
    num = parse_float(text)
    if pd.isna(num):
        return np.nan
    if 'lakh crore' in text:
        return num * 100000
    if 'crore' in text or ' cr' in f' {text} ' or text.endswith('cr'):
        return num
    if 'lakh' in text:
        return num / 100
    if num > 10000000:
        return num / 10000000
    return num


MAX_REASONABLE_ETF_AUM_CR = float(os.environ.get('MAX_REASONABLE_ETF_AUM_CR', '100000'))
SCHEME_CODE_MIN = 100000
SCHEME_CODE_MAX = 200000
MAX_REASONABLE_TER_DECIMAL = float(os.environ.get('MAX_REASONABLE_TER_DECIMAL', '0.05'))
MAX_REASONABLE_TRACKING_ERROR_DECIMAL = float(os.environ.get('MAX_REASONABLE_TRACKING_ERROR_DECIMAL', '0.20'))
MAX_ABS_TRACKING_DIFFERENCE_DECIMAL = float(os.environ.get('MAX_ABS_TRACKING_DIFFERENCE_DECIMAL', '0.20'))


def is_integer_like(value, tolerance=0.001):
    num = parse_float(value)
    if pd.isna(num):
        return False
    return abs(num - round(num)) <= tolerance


def is_scheme_code_like_value(value, scheme_code=None):
    num = parse_float(value)
    if pd.isna(num):
        return False
    code = parse_float(scheme_code)
    if pd.notna(code) and abs(num - code) <= 0.01:
        return True
    if SCHEME_CODE_MIN <= num <= SCHEME_CODE_MAX and is_integer_like(num):
        return True
    return False


def valid_etf_aum(value, scheme_code=None):
    aum = parse_amount_to_cr(value)
    if pd.isna(aum) or aum <= 0:
        return False, np.nan, 'blank_or_non_positive'
    if is_scheme_code_like_value(aum, scheme_code):
        return False, np.nan, 'looks_like_amfi_scheme_code'
    if aum > MAX_REASONABLE_ETF_AUM_CR:
        return False, np.nan, f'above_reasonable_etf_aum_limit_{MAX_REASONABLE_ETF_AUM_CR:g}_cr'
    return True, round(float(aum), 2), 'valid'


def parse_percent_decimal(value):
    if pd.isna(value):
        return np.nan
    text = str(value).strip()
    num = parse_float(text)
    if pd.isna(num):
        return np.nan
    if '%' in text:
        return num / 100
    if abs(num) > 0.05:
        return num / 100
    return num


def valid_ter(value):
    x = parse_percent_decimal(value)
    if pd.isna(x) or x <= 0:
        return False, np.nan, 'blank_or_non_positive'
    if x > MAX_REASONABLE_TER_DECIMAL:
        return False, np.nan, f'above_reasonable_ter_limit_{MAX_REASONABLE_TER_DECIMAL:g}'
    return True, round(float(x), 6), 'valid'


def valid_tracking_error(value):
    x = parse_percent_decimal(value)
    if pd.isna(x) or x < 0:
        return False, np.nan, 'blank_or_negative'
    if x > MAX_REASONABLE_TRACKING_ERROR_DECIMAL:
        return False, np.nan, f'above_reasonable_tracking_error_limit_{MAX_REASONABLE_TRACKING_ERROR_DECIMAL:g}'
    return True, round(float(x), 6), 'valid'


def valid_tracking_difference(value):
    x = parse_percent_decimal(value)
    if pd.isna(x):
        return False, np.nan, 'blank'
    if abs(x) > MAX_ABS_TRACKING_DIFFERENCE_DECIMAL:
        return False, np.nan, f'outside_reasonable_tracking_difference_limit_{MAX_ABS_TRACKING_DIFFERENCE_DECIMAL:g}'
    return True, round(float(x), 6), 'valid'


def load_config():
    if not CONFIG_CSV.exists():
        raise FileNotFoundError('config.csv not found. Run universe_builder.py first.')
    df = pd.read_csv(CONFIG_CSV)
    if 'Universe' in df.columns:
        df = df[df['Universe'].astype(str).str.lower().eq('etf')].copy()
    elif 'Universe_Group' in df.columns:
        df = df[df['Universe_Group'].astype(str).str.lower().eq('etf')].copy()
    else:
        raise ValueError('config.csv must include Universe or Universe_Group column.')
    return df.reset_index(drop=True)


def fetch_amfi_navall(log_rows):
    last_error = None
    for url in AMFI_NAVALL_URLS:
        try:
            r = requests.get(url, timeout=45, headers={'User-Agent': 'Mozilla/5.0'})
            r.raise_for_status()
            rows = []
            for line in r.text.splitlines():
                line = line.strip()
                if not line or ';' not in line:
                    continue
                parts = [p.strip() for p in line.split(';')]
                if len(parts) < 6 or parts[0].lower().startswith('scheme'):
                    continue
                code, isin1, isin2, scheme, nav, date = parts[:6]
                nav_val = parse_float(nav)
                if pd.isna(nav_val):
                    continue
                rows.append({
                    'Scheme_Code': code,
                    'ISIN_1': isin1,
                    'ISIN_2': isin2,
                    'Scheme_Name': scheme,
                    'NAV': nav_val,
                    'NAV_Date': date,
                    'Source_URL': url,
                })
            df = pd.DataFrame(rows)
            df.to_csv(AMFI_NAV_OUT, index=False)
            log_event(log_rows, 'AMFI_NAVAll', 'OK', url, len(df))
            return df
        except Exception as exc:
            last_error = exc
            log_event(log_rows, 'AMFI_NAVAll', 'ERROR', f'{url}: {exc}', 0)
    raise RuntimeError(f'Could not fetch AMFI NAVAll. Last error: {last_error}')


def load_existing_manual():
    if MANUAL_QUALITY.exists():
        return pd.read_csv(MANUAL_QUALITY)
    return pd.DataFrame()


def backup_manual_if_exists():
    if MANUAL_QUALITY.exists():
        backup = BASE_DIR / f"manual_etf_quality_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        backup.write_bytes(MANUAL_QUALITY.read_bytes())
        return backup
    return None



def _import_file_family(path):
    """Group auto-generated mirror files so _latest and dated copies do not double count.

    The fetchers intentionally save both:
      auto_amfi_aum_latest.csv and auto_amfi_aum_YYYY_MM.csv
      auto_amfi_ter_tracking_latest.csv and auto_amfi_ter_tracking_YYYY_MM.csv

    They normally contain the same period's rows. For enrichment, use the _latest
    file when it exists and keep dated files only as fallback. Manual files are
    always retained because they may contain hand-curated overrides.
    """
    name = path.name.lower()
    if name.startswith('auto_amfi_aum_'):
        return 'auto_amfi_aum'
    if name.startswith('auto_amfi_ter_tracking_'):
        return 'auto_amfi_ter_tracking'
    return None


def select_import_files(files, log_rows):
    """Prefer *_latest auto-import files over dated mirror files.

    This avoids reading duplicate AUM/TER rows twice, but does not affect manual
    fallback files or unmatched source files. It makes diagnostics cleaner and
    prevents accidental source-order noise.
    """
    by_family = {}
    keep = []
    for path in files:
        fam = _import_file_family(path)
        if not fam:
            keep.append(path)
        else:
            by_family.setdefault(fam, []).append(path)
    for fam, group in by_family.items():
        latest = [p for p in group if '_latest' in p.name.lower()]
        chosen = latest[:1] if latest else sorted(group, reverse=True)[:1]
        keep.extend(chosen)
        skipped = sorted(set(group) - set(chosen))
        for p in skipped:
            log_event(log_rows, 'Import_File', 'SKIPPED_MIRROR', f'{p.name}: latest/primary import kept for {fam}', 0)
    return sorted(keep)


def read_import_files(log_rows):
    pieces = []
    files = select_import_files(sorted([p for p in IMPORT_DIR.glob('*') if p.suffix.lower() in ['.csv','.xlsx','.xls']]), log_rows)
    if not files:
        log_event(log_rows, 'Import_Folder', 'NO_FILES', str(IMPORT_DIR), 0)
        return pd.DataFrame()
    for path in files:
        try:
            if path.suffix.lower() == '.csv':
                df = pd.read_csv(path)
                df['__Source_File'] = path.name
                pieces.append(df)
                log_event(log_rows, 'Import_File', 'OK', path.name, len(df))
            else:
                xls = pd.ExcelFile(path)
                total = 0
                for sheet in xls.sheet_names:
                    df = pd.read_excel(path, sheet_name=sheet)
                    df['__Source_File'] = f'{path.name}::{sheet}'
                    pieces.append(df)
                    total += len(df)
                log_event(log_rows, 'Import_File', 'OK', path.name, total)
        except Exception as exc:
            log_event(log_rows, 'Import_File', 'ERROR', f'{path.name}: {exc}', 0)
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()


def standardize_import_metadata(raw):
    cols = ['Import_Symbol','Import_ISIN','Import_Name','Import_Scheme_Code','AUM_Cr','AUM_Validation','TER','TER_Validation','Tracking_Error','Tracking_Error_Validation','Tracking_Difference','Tracking_Difference_Validation','Benchmark_Index','Source_File']
    if raw.empty:
        out = pd.DataFrame(columns=cols)
        out.to_csv(IMPORT_STANDARDIZED_OUT, index=False)
        return out
    symbol_col = find_col(raw, ['symbol','ticker','nse symbol','exchange symbol'])
    isin_col = find_col(raw, ['isin'])
    name_col = find_col(raw, ['scheme name','fund name','name','etf name'])
    scheme_code_col = find_col(raw, ['scheme_code','scheme code','amfi scheme code'])
    aum_col = find_col(raw, ['aum cr','aum crore','assets under management','aum'])
    ter_col = find_col(raw, ['ter','total expense ratio','expense ratio'])
    te_col = find_col(raw, ['tracking error', 'tracking error annualized', 'tracking error %'])
    td_col = find_col(raw, ['tracking difference', 'tracking deviation', 'tracking diff', 'tracking difference %'])
    bench_col = find_col(raw, ['benchmark index','benchmark','underlying index','index'])
    rows = []
    for _, r in raw.iterrows():
        scheme_code = str(r.get(scheme_code_col, '')).strip() if scheme_code_col else ''
        ok, clean_aum, aum_reason = valid_etf_aum(r.get(aum_col), scheme_code) if aum_col else (False, np.nan, 'missing_aum_column')
        rows.append({
            'Import_Symbol': str(r.get(symbol_col, '')).strip() if symbol_col else '',
            'Import_ISIN': str(r.get(isin_col, '')).strip() if isin_col else '',
            'Import_Name': str(r.get(name_col, '')).strip() if name_col else '',
            'Import_Scheme_Code': scheme_code,
            'AUM_Cr': clean_aum if ok else np.nan,
            'AUM_Validation': aum_reason,
            'TER': (valid_ter(r.get(ter_col))[1] if ter_col else np.nan),
            'TER_Validation': (valid_ter(r.get(ter_col))[2] if ter_col else 'missing_ter_column'),
            'Tracking_Error': (valid_tracking_error(r.get(te_col))[1] if te_col else np.nan),
            'Tracking_Error_Validation': (valid_tracking_error(r.get(te_col))[2] if te_col else 'missing_tracking_error_column'),
            'Tracking_Difference': (valid_tracking_difference(r.get(td_col))[1] if td_col else np.nan),
            'Tracking_Difference_Validation': (valid_tracking_difference(r.get(td_col))[2] if td_col else 'missing_tracking_difference_column'),
            'Benchmark_Index': str(r.get(bench_col, '')).strip() if bench_col else '',
            'Source_File': str(r.get('__Source_File', '')),
        })
    out = pd.DataFrame(rows).replace({'': np.nan})
    out.to_csv(IMPORT_STANDARDIZED_OUT, index=False)
    return out


def get_etf_identity(row):
    symbol = str(first_existing(row, ['Symbol','TradingSymbol','Ticker'])).strip()
    raw_symbol = str(first_existing(row, ['Raw_Symbol','RawSymbol','Ticker','Symbol'])).strip()
    name = str(first_existing(row, ['Name','Security_Name','Security Name','Underlying','Company Name'])).strip()
    universe_group = str(first_existing(row, ['Universe_Group','Universe'])).strip()
    opp_type = str(first_existing(row, ['Opportunity_Type','Category'])).strip()
    isin = ''
    for col in row.index:
        if 'isin' in str(col).lower():
            v = row.get(col)
            if pd.notna(v) and str(v).strip():
                isin = str(v).strip()
                break
    return {'Symbol': symbol, 'Raw_Symbol': raw_symbol, 'Name': name, 'Universe_Group': universe_group, 'Opportunity_Type': opp_type, 'ISIN': isin}


def match_nav(etf, nav_df):
    if nav_df.empty:
        return {}
    isin = str(etf.get('ISIN','')).strip()
    if isin and isin.lower() not in ['nan','none']:
        hit = nav_df[(nav_df['ISIN_1'].astype(str).str.upper().eq(isin.upper())) | (nav_df['ISIN_2'].astype(str).str.upper().eq(isin.upper()))]
        if not hit.empty:
            r = hit.iloc[0]
            return {'AMFI_Scheme_Code': r['Scheme_Code'], 'AMFI_Scheme_Name': r['Scheme_Name'], 'Manual_NAV': r['NAV'], 'Manual_NAV_Date': r['NAV_Date'], 'NAV_Source': 'AMFI_NAVAll', 'NAV_Match_Method': 'ISIN', 'NAV_Match_Score': 1.0}
    name = etf.get('Name','')
    raw = str(etf.get('Raw_Symbol','')).replace('.NS','')
    best = (0.0, None)
    for idx, r in nav_df.iterrows():
        scheme = str(r['Scheme_Name'])
        score = max(combined_score(name, scheme), combined_score(raw, scheme))
        if score > best[0]:
            best = (score, idx)
    score, idx = best
    if idx is None:
        return {}
    r = nav_df.loc[idx]
    if score < 0.48:
        return {'NAV_Source': 'AMFI_NAVAll', 'NAV_Match_Method': 'No reliable match', 'NAV_Match_Score': score, 'AMFI_Scheme_Name': r['Scheme_Name']}
    return {'AMFI_Scheme_Code': r['Scheme_Code'], 'AMFI_Scheme_Name': r['Scheme_Name'], 'Manual_NAV': r['NAV'], 'Manual_NAV_Date': r['NAV_Date'], 'NAV_Source': 'AMFI_NAVAll', 'NAV_Match_Method': 'Fuzzy', 'NAV_Match_Score': score}



def row_to_metadata(r, method):
    src = str(r.get('Source_File', 'import'))
    out = {}
    ok, clean_aum, aum_reason = valid_etf_aum(r.get('AUM_Cr', np.nan), r.get('Import_Scheme_Code', np.nan))
    if ok:
        out['AUM_Cr'] = clean_aum; out['AUM_Source'] = f'{src} ({method})'
    ok_ter, clean_ter, _ = valid_ter(r.get('TER', np.nan))
    if ok_ter:
        out['TER'] = clean_ter; out['TER_Source'] = f'{src} ({method})'
    ok_te, clean_te, _ = valid_tracking_error(r.get('Tracking_Error', np.nan))
    if ok_te:
        out['Tracking_Error'] = clean_te; out['Tracking_Error_Source'] = f'{src} ({method})'
    ok_td, clean_td, _ = valid_tracking_difference(r.get('Tracking_Difference', np.nan))
    if ok_td:
        out['Tracking_Difference'] = clean_td; out['Tracking_Difference_Source'] = f'{src} ({method})'
    bench = r.get('Benchmark_Index', np.nan)
    if pd.notna(bench) and str(bench).strip() and str(bench).strip().lower() not in ['nan', 'none']:
        out['Benchmark_Index'] = str(bench).strip(); out['Benchmark_Source'] = f'{src} ({method})'
    return out


def _merge_metadata_dict(base, incoming):
    """Fill missing metadata fields without overwriting already-valid values."""
    for field in ['AUM_Cr', 'TER', 'Tracking_Error', 'Tracking_Difference', 'Benchmark_Index']:
        if field in incoming:
            cur = base.get(field, np.nan)
            has_cur = pd.notna(cur) and str(cur).strip() not in ['', 'nan', 'None']
            if not has_cur:
                base[field] = incoming[field]
    for src_field in ['AUM_Source', 'TER_Source', 'Tracking_Error_Source', 'Tracking_Difference_Source', 'Benchmark_Source']:
        if src_field in incoming:
            cur = base.get(src_field, '')
            inc = str(incoming[src_field]).strip()
            if not cur:
                base[src_field] = inc
            elif inc and inc not in str(cur):
                base[src_field] = f"{cur}; {inc}"
    return base


def _dedupe_rows(df):
    if df is None or df.empty:
        return pd.DataFrame()
    return df.reset_index(drop=True).drop_duplicates().copy()


def _candidate_import_rows(etf, imports):
    """Return all plausible import rows for an ETF, not just the first one.

    This is the critical Stage 3.5.10 fix. Earlier versions returned the first
    matching import row. With separate AUM and TER/tracking import files, that
    often meant the AUM row was selected and the TER/tracking row was ignored.
    """
    candidates = []
    symbol = str(etf.get('Symbol', '')).strip()
    raw = str(etf.get('Raw_Symbol', symbol)).replace('.NS', '').strip()
    isin = str(etf.get('ISIN', '')).strip()
    code = str(etf.get('AMFI_Scheme_Code', '')).strip()
    name = str(etf.get('Name', '')).strip()
    scheme_name = str(etf.get('AMFI_Scheme_Name', '')).strip()

    if 'Import_Symbol' in imports.columns:
        sym_norm = imports['Import_Symbol'].astype(str).str.replace('.NS', '', regex=False).str.upper()
        hit = imports[sym_norm.eq(raw.upper()) | sym_norm.eq(symbol.replace('.NS', '').upper())]
        if not hit.empty:
            candidates.append(('Import Symbol', 1.0, hit))

    if code and code.lower() not in ['nan', 'none', ''] and 'Import_Scheme_Code' in imports.columns:
        hit = imports[imports['Import_Scheme_Code'].astype(str).str.strip().eq(code)]
        if not hit.empty:
            candidates.append(('AMFI Scheme Code', 1.0, hit))

    if isin and isin.lower() not in ['nan', 'none', ''] and 'Import_ISIN' in imports.columns:
        hit = imports[imports['Import_ISIN'].astype(str).str.upper().eq(isin.upper())]
        if not hit.empty:
            candidates.append(('Import ISIN', 1.0, hit))

    # Fuzzy fallback only when exact identifiers do not cover any source rows.
    if not candidates:
        scored = []
        for idx, r in imports.iterrows():
            imp_name = str(r.get('Import_Name', ''))
            if not imp_name or imp_name.lower() == 'nan':
                continue
            score = max(combined_score(name, imp_name), combined_score(scheme_name, imp_name), combined_score(raw, imp_name))
            if score >= 0.62:
                scored.append((score, idx))
        if scored:
            scored.sort(reverse=True, key=lambda x: x[0])
            best_score = scored[0][0]
            # Keep all rows close to the top score so separate source rows survive.
            keep_idx = [idx for score, idx in scored if score >= max(0.62, best_score - 0.06)][:20]
            candidates.append(('Import Fuzzy', best_score, imports.loc[keep_idx]))

    return candidates


def match_import_metadata(etf, imports):
    diags = []
    if imports.empty:
        return {}, diags

    candidates = _candidate_import_rows(etf, imports)
    if not candidates:
        return {}, [{'Symbol': etf.get('Symbol', ''), 'Match_Type': 'No import match', 'Match_Score': 0, 'Matched_Name': '', 'Source_File': ''}]

    merged = {}
    all_hits = []
    for method, score, hit in candidates:
        hit = _dedupe_rows(hit)
        all_hits.append(hit)
        for _, r in hit.iterrows():
            md = row_to_metadata(r, method)
            merged = _merge_metadata_dict(merged, md)
        # Diagnostics by source file so the user can see AUM and TER/tracking rows both matched.
        for src_file, grp in hit.groupby(hit.get('Source_File', pd.Series([''] * len(hit), index=hit.index)).fillna('')):
            diags.append({
                'Symbol': etf.get('Symbol', ''),
                'Match_Type': method,
                'Match_Score': score,
                'Matched_Name': '; '.join(grp.get('Import_Name', pd.Series('', index=grp.index)).dropna().astype(str).head(3).tolist()),
                'Source_File': src_file,
                'Rows_Used': len(grp),
                'AUM_Rows': int(grp.get('AUM_Cr', pd.Series(np.nan, index=grp.index)).notna().sum()),
                'TER_Rows': int(grp.get('TER', pd.Series(np.nan, index=grp.index)).notna().sum()),
                'Tracking_Error_Rows': int(grp.get('Tracking_Error', pd.Series(np.nan, index=grp.index)).notna().sum()),
                'Tracking_Difference_Rows': int(grp.get('Tracking_Difference', pd.Series(np.nan, index=grp.index)).notna().sum()),
            })
    return merged, diags


def find_existing_manual_row(existing, symbol):
    if existing.empty or 'Symbol' not in existing.columns:
        return None
    hit = existing[existing['Symbol'].astype(str).str.upper().eq(str(symbol).upper())]
    return None if hit.empty else hit.iloc[0]


def overlay_existing_manual(out, existing_row):
    if existing_row is None:
        return out
    for field in QUALITY_FIELDS:
        if field in existing_row.index:
            old = existing_row.get(field)
            if pd.notna(old) and str(old).strip() not in ['', 'nan', 'None']:
                if field == 'AUM_Cr':
                    ok, clean_aum, reason = valid_etf_aum(old, existing_row.get('AMFI_Scheme_Code', out.get('AMFI_Scheme_Code', np.nan)))
                    if not ok:
                        continue
                    out[field] = clean_aum
                elif field == 'TER':
                    ok, clean_ter, reason = valid_ter(old)
                    if not ok:
                        continue
                    out[field] = clean_ter
                elif field == 'Tracking_Error':
                    ok, clean_te, reason = valid_tracking_error(old)
                    if not ok:
                        continue
                    out[field] = clean_te
                elif field == 'Tracking_Difference':
                    ok, clean_td, reason = valid_tracking_difference(old)
                    if not ok:
                        continue
                    out[field] = clean_td
                else:
                    out[field] = old
                src_col = f'{field}_Source'
                if src_col not in out or not out.get(src_col):
                    out[src_col] = 'Existing manual_etf_quality.csv'
    return out


def metadata_confidence(row):
    score = 0.0
    if pd.notna(row.get('Manual_NAV', np.nan)): score += 0.30
    if valid_etf_aum(row.get('AUM_Cr', np.nan), row.get('AMFI_Scheme_Code', np.nan))[0]: score += 0.25
    if valid_ter(row.get('TER', np.nan))[0]: score += 0.15
    if valid_tracking_error(row.get('Tracking_Error', np.nan))[0]:
        score += 0.15
    elif valid_tracking_difference(row.get('Tracking_Difference', np.nan))[0]:
        score += 0.10
    if row.get('Benchmark_Index') and str(row.get('Benchmark_Index')).strip().lower() != 'nan': score += 0.15
    return round(score, 4)


def metadata_flags(row):
    flags = []
    if pd.isna(row.get('Manual_NAV', np.nan)): flags.append('NAV missing')
    ok_aum, _, aum_reason = valid_etf_aum(row.get('AUM_Cr', np.nan), row.get('AMFI_Scheme_Code', np.nan))
    if not ok_aum:
        flags.append('AUM missing' if aum_reason in ['blank_or_non_positive', 'missing_aum_column'] else f'AUM invalid: {aum_reason}')
    ok_ter, _, ter_reason = valid_ter(row.get('TER', np.nan))
    if not ok_ter: flags.append('TER missing' if ter_reason == 'blank_or_non_positive' else f'TER invalid: {ter_reason}')
    ok_te, _, te_reason = valid_tracking_error(row.get('Tracking_Error', np.nan))
    ok_td, _, td_reason = valid_tracking_difference(row.get('Tracking_Difference', np.nan))
    has_benchmark = bool(row.get('Benchmark_Index')) and str(row.get('Benchmark_Index')).strip().lower() != 'nan'
    source_limited_tracking = (not ok_te and not ok_td and pd.notna(row.get('Manual_NAV', np.nan)) and ok_aum and ok_ter and has_benchmark)
    if source_limited_tracking:
        # AMFI/import feeds often do not disclose TE/TD for every ETF. If NAV,
        # AUM, TER and benchmark are present, treat this as source-limited rather
        # than an actionable quality gap so the review file focuses on fixable rows.
        pass
    elif not ok_te and not ok_td:
        flags.append('Tracking quality metric missing')
    elif not ok_te and ok_td:
        # Tracking difference is a valid fallback quality metric. Do not mark the
        # row incomplete just because tracking error itself is unavailable.
        pass
    elif ok_te and not ok_td:
        pass
    if not has_benchmark: flags.append('Benchmark missing')
    if pd.notna(row.get('NAV_Match_Score', np.nan)) and row.get('NAV_Match_Method') == 'Fuzzy' and float(row.get('NAV_Match_Score')) < 0.70:
        flags.append('NAV fuzzy match review')
    return '; '.join(flags) if flags else 'Complete'


def main():
    print('ETF Metadata Enricher - Stage 3.5.10.1 Consensus Import Coalesce + Quality Status')
    print('===========================================================')
    log_rows = []
    diag_rows = []
    etfs = load_config()
    print(f'ETF rows loaded: {len(etfs)}')
    print('Fetching AMFI NAVAll.txt directly...')
    try:
        nav_df = fetch_amfi_navall(log_rows)
        print(f'AMFI NAV rows parsed: {len(nav_df)}')
    except Exception as exc:
        nav_df = pd.DataFrame()
        log_event(log_rows, 'AMFI_NAVAll', 'FATAL', str(exc), 0)
        print(f'WARNING: AMFI NAV fetch failed: {exc}')
    raw_imports = read_import_files(log_rows)
    imports = standardize_import_metadata(raw_imports)
    print(f'Import metadata rows standardized: {len(imports)}')
    existing = load_existing_manual()
    if not existing.empty:
        print(f'Existing manual_etf_quality rows loaded: {len(existing)}')
        backup = backup_manual_if_exists()
        if backup:
            print(f'Backup saved: {backup}')
    enriched = []
    for _, etf_row in etfs.iterrows():
        ident = get_etf_identity(etf_row)
        row = {**ident}
        row.update(match_nav(ident, nav_df))
        meta_match, meta_diags = match_import_metadata(row, imports)
        row.update(meta_match)
        diag_rows.extend(meta_diags)
        row = overlay_existing_manual(row, find_existing_manual_row(existing, ident['Symbol']))
        row['Metadata_Confidence'] = metadata_confidence(row)
        row['Metadata_Flags'] = metadata_flags(row)
        row['Last_Updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        enriched.append(row)
        diag_rows.append({'Symbol': ident['Symbol'], 'Match_Type': row.get('NAV_Match_Method',''), 'Match_Score': row.get('NAV_Match_Score', np.nan), 'Matched_Name': row.get('AMFI_Scheme_Name',''), 'Source_File': row.get('NAV_Source','')})
    out = pd.DataFrame(enriched)
    for col in OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    out = out[OUTPUT_COLUMNS]
    out.to_csv(ENRICHED_OUT, index=False)
    unresolved = out[out['Metadata_Flags'].astype(str).ne('Complete')].copy()
    unresolved.to_csv(UNRESOLVED_REVIEW_OUT, index=False)
    pd.DataFrame(diag_rows).to_csv(DIAGNOSTICS_OUT, index=False)
    pd.DataFrame(log_rows).to_csv(SOURCE_LOG_OUT, index=False)
    out.to_csv(MANUAL_QUALITY, index=False)
    print(f'Saved AMFI NAV data: {AMFI_NAV_OUT}')
    print(f'Saved enriched metadata: {ENRICHED_OUT}')
    print(f'Saved diagnostics: {DIAGNOSTICS_OUT}')
    print(f'Saved unresolved review: {UNRESOLVED_REVIEW_OUT}')
    print(f'Saved source log: {SOURCE_LOG_OUT}')
    print(f'Saved/updated manual quality file: {MANUAL_QUALITY}')
    print('')
    print('Coverage summary:')
    print(f"NAV filled: {int(out['Manual_NAV'].notna().sum())} / {len(out)}")
    print(f"AUM filled: {int(out['AUM_Cr'].notna().sum())} / {len(out)}")
    print(f"TER filled: {int(out['TER'].notna().sum())} / {len(out)}")
    print(f"Tracking error disclosed: {int(out['Tracking_Error'].notna().sum())} / {len(out)}")
    tracking_quality = out['Tracking_Error'].notna() | out['Tracking_Difference'].notna()
    source_limited_ok = out['Metadata_Flags'].astype(str).eq('Complete') & ~tracking_quality
    print(f"Tracking quality metric disclosed (error or difference): {int(tracking_quality.sum())} / {len(out)}")
    print(f"Tracking disclosure source-limited but accepted: {int(source_limited_ok.sum())} / {len(out)}")
    print(f"Benchmark filled: {int(out['Benchmark_Index'].notna().sum())} / {len(out)}")
    print('')
    print('Metadata flags:')
    print(out['Metadata_Flags'].value_counts(dropna=False).head(20).to_string())
    print('')
    print('Next run: python etf_quality_builder.py')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('ETF metadata enrichment failed.')
        traceback.print_exc()
        sys.exit(1)
