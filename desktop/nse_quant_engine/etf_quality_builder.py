"""
ETF Quality Builder - Stage 3.5.10 Merged Metadata ETF Quality
===============================

Creates ETF quality files used by nse_quant_engine.py.

Outputs:
    data/amfi_nav_latest.csv
    data/etf_mapping_suggestions.csv
    data/etf_quality_latest.csv
    manual_etf_quality_template.csv

Mapping_Status:
    Verified  = ISIN match or manual override
    Suggested = strong fuzzy match
    Review    = weak fuzzy match
    Missing   = no reliable match

Run:
    python etf_quality_builder.py
"""

from __future__ import annotations

from pathlib import Path
from difflib import SequenceMatcher
import re
import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

CONFIG_CSV = BASE_DIR / "config.csv"
MANUAL_QUALITY_CSV = BASE_DIR / "manual_etf_quality.csv"
MANUAL_TEMPLATE_CSV = BASE_DIR / "manual_etf_quality_template.csv"

ETF_QUALITY_OUT = DATA_DIR / "etf_quality_latest.csv"
MAPPING_SUGGESTIONS_OUT = DATA_DIR / "etf_mapping_suggestions.csv"
AMFI_NAV_OUT = DATA_DIR / "amfi_nav_latest.csv"

AMFI_NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/plain,*/*"}


def normalize_text(text: str) -> str:
    text = str(text).upper()
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    stopwords = [
        "ETF", "EXCHANGE", "TRADED", "FUND", "DIRECT", "GROWTH", "PLAN",
        "REGULAR", "NSE", "AMC", "ASSET", "MANAGEMENT", "MUTUAL", "SCHEME",
        "ICICIPRAMC", "MIRAEAMC", "MOTILALAMC", "NIPPONAMC", "SBIAMC",
        "HDFC", "KOTAKMAMC", "ZERODHAAMC", "UTIAMC", "AXISAMC", "LICNAMC",
        "ADITYA", "BIRLA", "SUN", "LIFE", "TATA", "MAHINDRA", "MIRAE",
        "NIPPON", "INDIA", "SBI", "ICICI", "PRUDENTIAL",
    ]
    return " ".join([p for p in text.split() if p not in stopwords])


def score_match(a: str, b: str) -> float:
    aa = normalize_text(a)
    bb = normalize_text(b)
    if not aa or not bb:
        return 0.0
    return SequenceMatcher(None, aa, bb).ratio()


def load_config_etfs() -> pd.DataFrame:
    if not CONFIG_CSV.exists():
        raise FileNotFoundError("config.csv not found. Run python universe_builder.py first.")
    cfg = pd.read_csv(CONFIG_CSV)
    for col in ["Universe", "Symbol", "Raw_Symbol", "Name", "Category", "ISIN", "Opportunity_Type", "Opportunity_Eligible"]:
        if col not in cfg.columns:
            cfg[col] = ""
    etfs = cfg[cfg["Universe"].astype(str).str.upper().eq("ETF")].copy()
    if etfs.empty:
        raise ValueError("No ETFs found in config.csv.")
    return etfs


def fetch_amfi_nav() -> pd.DataFrame:
    print("Downloading AMFI NAVAll.txt...")
    try:
        response = requests.get(AMFI_NAV_URL, headers=HEADERS, timeout=60)
        response.raise_for_status()
        text = response.text
    except Exception as exc:
        # Keep the desktop app resilient: AMFI occasionally times out. Reuse the
        # most recent cached NAVAll file created by etf_metadata_enricher instead
        # of crashing the GUI at the end of the run.
        cached = DATA_DIR / "amfi_navall_latest.csv"
        if cached.exists():
            print(f"WARNING: AMFI NAV download failed ({exc}). Reusing cached {cached.name}.")
            cached_df = pd.read_csv(cached)
            rename = {
                "Scheme_Code": "AMFI_Scheme_Code",
                "ISIN_1": "AMFI_ISIN_Growth",
                "ISIN_2": "AMFI_ISIN_Div",
                "Scheme_Name": "AMFI_Scheme_Name",
                "NAV_Date": "NAV_Date",
                "NAV": "NAV",
            }
            cached_df = cached_df.rename(columns=rename)
            for col in ["AMFI_Scheme_Code", "AMFI_ISIN_Growth", "AMFI_ISIN_Div", "AMFI_Scheme_Name", "NAV", "NAV_Date"]:
                if col not in cached_df.columns:
                    cached_df[col] = ""
            return cached_df[["AMFI_Scheme_Code", "AMFI_ISIN_Growth", "AMFI_ISIN_Div", "AMFI_Scheme_Name", "NAV", "NAV_Date"]].copy()
        raise

    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Scheme Code"):
            continue
        parts = line.split(";")
        if len(parts) < 6:
            continue
        scheme_code, isin_growth, isin_div, scheme_name, nav, nav_date = parts[:6]
        try:
            nav_value = float(nav)
        except Exception:
            nav_value = pd.NA
        rows.append({
            "AMFI_Scheme_Code": str(scheme_code).strip(),
            "AMFI_ISIN_Growth": str(isin_growth).strip(),
            "AMFI_ISIN_Div": str(isin_div).strip(),
            "AMFI_Scheme_Name": str(scheme_name).strip(),
            "NAV": nav_value,
            "NAV_Date": str(nav_date).strip(),
        })
    df = pd.DataFrame(rows).dropna(subset=["NAV"])
    df.to_csv(AMFI_NAV_OUT, index=False)
    return df


def mapping_status(match_type: str, match_score: float) -> str:
    if match_type == "ISIN":
        return "Verified"
    if match_score >= 0.82:
        return "Suggested"
    if match_score >= 0.65:
        return "Review"
    return "Missing"


def make_mapping_suggestions(etfs: pd.DataFrame, nav_df: pd.DataFrame) -> pd.DataFrame:
    suggestions = []
    for _, etf in etfs.iterrows():
        etf_name = str(etf.get("Name", ""))
        etf_symbol = str(etf.get("Symbol", ""))
        etf_isin = str(etf.get("ISIN", "")).strip()

        candidates = nav_df.copy()
        isin_matches = pd.DataFrame()
        if etf_isin and etf_isin.lower() != "nan":
            isin_matches = candidates[
                candidates["AMFI_ISIN_Growth"].astype(str).str.upper().eq(etf_isin.upper())
                | candidates["AMFI_ISIN_Div"].astype(str).str.upper().eq(etf_isin.upper())
            ].copy()

        if not isin_matches.empty:
            best = isin_matches.iloc[0]
            match_score = 1.0
            match_type = "ISIN"
        else:
            candidates["Match_Score"] = candidates["AMFI_Scheme_Name"].map(lambda x: score_match(etf_name, x))
            candidates = candidates.sort_values("Match_Score", ascending=False)
            best = candidates.iloc[0]
            match_score = float(best["Match_Score"])
            match_type = "Fuzzy"

        status = mapping_status(match_type, match_score)
        suggestions.append({
            "Symbol": etf_symbol,
            "Raw_Symbol": etf.get("Raw_Symbol", ""),
            "ETF_Name": etf_name,
            "ETF_Category": etf.get("Category", ""),
            "ETF_ISIN": etf_isin,
            "Suggested_AMFI_Scheme_Code": best["AMFI_Scheme_Code"],
            "Suggested_AMFI_Scheme_Name": best["AMFI_Scheme_Name"],
            "Suggested_NAV": best["NAV"],
            "Suggested_NAV_Date": best["NAV_Date"],
            "Match_Score": match_score,
            "Match_Type": match_type,
            "Mapping_Status": status,
        })

    out = pd.DataFrame(suggestions)
    out.to_csv(MAPPING_SUGGESTIONS_OUT, index=False)
    return out


def create_manual_template(etfs: pd.DataFrame, suggestions: pd.DataFrame) -> None:
    template = etfs[["Symbol", "Raw_Symbol", "Name", "Category", "ISIN"]].copy()
    template = template.merge(suggestions, on=["Symbol", "Raw_Symbol"], how="left")
    template["Manual_AMFI_Scheme_Code"] = ""
    template["Manual_AMFI_Scheme_Name"] = ""
    template["Manual_NAV"] = ""
    template["Manual_NAV_Date"] = ""
    template["AUM_Cr"] = ""
    template["TER"] = ""
    template["Tracking_Error"] = ""
    template["Tracking_Difference"] = ""
    template["Benchmark_Index"] = ""
    template["Quality_Override_Notes"] = ""
    template.to_csv(MANUAL_TEMPLATE_CSV, index=False)


def load_manual_quality() -> pd.DataFrame:
    if not MANUAL_QUALITY_CSV.exists():
        return pd.DataFrame()
    manual = pd.read_csv(MANUAL_QUALITY_CSV)
    if "Symbol" not in manual.columns:
        raise ValueError("manual_etf_quality.csv must contain Symbol column.")
    return manual


def build_quality(etfs: pd.DataFrame, suggestions: pd.DataFrame, manual: pd.DataFrame) -> pd.DataFrame:
    q = etfs[[
        "Symbol", "Raw_Symbol", "Name", "Category", "ISIN",
        "Opportunity_Type", "Opportunity_Eligible"
    ]].copy()

    q = q.merge(suggestions, on=["Symbol", "Raw_Symbol"], how="left")
    trusted_map = q["Mapping_Status"].isin(["Verified", "Suggested"])
    q["AMFI_Scheme_Code"] = q["Suggested_AMFI_Scheme_Code"].where(trusted_map, "")
    q["AMFI_Scheme_Name"] = q["Suggested_AMFI_Scheme_Name"].where(trusted_map, "")
    q["NAV"] = q["Suggested_NAV"].where(trusted_map, pd.NA)
    q["NAV_Date"] = q["Suggested_NAV_Date"].where(trusted_map, "")

    q["AUM_Cr"] = pd.NA
    q["TER"] = pd.NA
    q["Tracking_Error"] = pd.NA
    q["Tracking_Difference"] = pd.NA
    q["Benchmark_Index"] = ""
    q["ETF_Quality_Source"] = "AMFI NAV suggestion / missing manual fields"

    if not manual.empty:
        for col in [
            "Manual_AMFI_Scheme_Code", "Manual_AMFI_Scheme_Name", "Manual_NAV",
            "Manual_NAV_Date", "AUM_Cr", "TER", "Tracking_Error",
            "Tracking_Difference", "Benchmark_Index", "Quality_Override_Notes"
        ]:
            if col not in manual.columns:
                manual[col] = ""

        q = q.merge(manual, on="Symbol", how="left", suffixes=("", "_ManualFile"))

        def use_manual(row, manual_col, current_col):
            val = row.get(manual_col)
            if pd.notna(val) and str(val).strip() != "":
                return val
            return row.get(current_col)

        q["AMFI_Scheme_Code"] = q.apply(lambda r: use_manual(r, "Manual_AMFI_Scheme_Code", "AMFI_Scheme_Code"), axis=1)
        q["AMFI_Scheme_Name"] = q.apply(lambda r: use_manual(r, "Manual_AMFI_Scheme_Name", "AMFI_Scheme_Name"), axis=1)
        q["NAV"] = q.apply(lambda r: use_manual(r, "Manual_NAV", "NAV"), axis=1)
        q["NAV_Date"] = q.apply(lambda r: use_manual(r, "Manual_NAV_Date", "NAV_Date"), axis=1)

        for col in ["AUM_Cr", "TER", "Tracking_Error", "Tracking_Difference", "Benchmark_Index"]:
            manual_col = col + "_ManualFile"
            if manual_col in q.columns:
                q[col] = q.apply(lambda r: use_manual(r, manual_col, col), axis=1)

        q["ETF_Quality_Source"] = "AMFI NAV + manual overrides where available"
        q.loc[q["Manual_AMFI_Scheme_Code"].fillna("").astype(str).str.strip().ne(""), "Mapping_Status"] = "Verified"

    for col in ["NAV", "AUM_Cr", "TER", "Tracking_Error", "Tracking_Difference", "Match_Score"]:
        if col in q.columns:
            q[col] = pd.to_numeric(q[col], errors="coerce")

    flags = []
    for _, row in q.iterrows():
        f = []
        if row.get("Mapping_Status") in ["Review", "Missing"]:
            f.append(f"AMFI mapping {row.get('Mapping_Status')}")
        if row.get("Mapping_Status") == "Suggested":
            f.append("AMFI mapping suggested, not verified")
        if pd.isna(row.get("NAV")):
            f.append("NAV missing")
        if pd.isna(row.get("AUM_Cr")):
            f.append("AUM missing")
        if pd.isna(row.get("TER")):
            f.append("TER missing")
        tracking_error_missing = pd.isna(row.get("Tracking_Error"))
        tracking_difference_missing = pd.isna(row.get("Tracking_Difference"))
        if tracking_error_missing and tracking_difference_missing:
            # If core ETF metadata is present, treat missing tracking disclosure as source-unavailable,
            # not a broken ETF-quality record. AMFI may not disclose tracking for every new/special ETF.
            has_core = pd.notna(row.get("NAV")) and pd.notna(row.get("AUM_Cr")) and pd.notna(row.get("TER"))
            has_benchmark = pd.notna(row.get("Benchmark_Index")) and str(row.get("Benchmark_Index")).strip() != ""
            if has_core and has_benchmark:
                pass
            else:
                f.append("Tracking quality metric missing")
        elif tracking_error_missing and not tracking_difference_missing:
            # Tracking difference is accepted as the fallback tracking-quality
            # metric, so this is not an incomplete quality record.
            pass
        elif not tracking_error_missing and tracking_difference_missing:
            pass
        flags.append("; ".join(f) if f else "Complete")
    q["ETF_Quality_Data_Flag"] = flags

    cols = [
        "Symbol", "Raw_Symbol", "Name", "Category", "Opportunity_Type", "Opportunity_Eligible",
        "ISIN", "AMFI_Scheme_Code", "AMFI_Scheme_Name", "NAV", "NAV_Date",
        "AUM_Cr", "TER", "Tracking_Error", "Tracking_Difference", "Benchmark_Index",
        "Match_Score", "Mapping_Status", "ETF_Quality_Data_Flag", "ETF_Quality_Source"
    ]
    for col in cols:
        if col not in q.columns:
            q[col] = ""
    q = q[cols].copy()
    q.to_csv(ETF_QUALITY_OUT, index=False)
    return q


def main() -> None:
    print("ETF Quality Builder - Stage 3.5.10 Merged Metadata ETF Quality")
    print("===============================")

    etfs = load_config_etfs()
    print(f"ETF rows from config: {len(etfs)}")

    nav_df = fetch_amfi_nav()
    print(f"AMFI NAV rows: {len(nav_df)}")

    suggestions = make_mapping_suggestions(etfs, nav_df)
    print(f"Mapping suggestions written: {MAPPING_SUGGESTIONS_OUT}")

    create_manual_template(etfs, suggestions)
    print(f"Manual quality template written: {MANUAL_TEMPLATE_CSV}")

    manual = load_manual_quality()
    if manual.empty:
        print("No manual_etf_quality.csv found. Using AMFI suggestions only.")
    else:
        print(f"Manual quality rows loaded: {len(manual)}")

    quality = build_quality(etfs, suggestions, manual)
    print(f"ETF quality output written: {ETF_QUALITY_OUT}")
    tracking_quality = quality["Tracking_Error"].notna() | quality["Tracking_Difference"].notna()
    source_limited_ok = quality["ETF_Quality_Data_Flag"].astype(str).eq("Complete") & ~tracking_quality
    print(f"Tracking quality metric disclosed (error or difference): {int(tracking_quality.sum())} / {len(quality)}")
    print(f"Tracking disclosure source-limited but accepted: {int(source_limited_ok.sum())} / {len(quality)}")
    print("\nQuality data flags:")
    print(quality["ETF_Quality_Data_Flag"].value_counts(dropna=False).head(10).to_string())
    print("\nDone. Next run: python nse_quant_engine.py")


if __name__ == "__main__":
    main()
