"""
Universe Builder - Stage 3.3 Final
============================

Builds config.csv from:
1. Nifty 50 constituents
2. Nifty Next 50 constituents
3. Nifty Midcap 150 constituents
4. NSE ETF security list

Stage 3.3 Final:
    - Strong cash/liquid/debt ETF classification
    - DUMMY/TEST symbol filtering
    - Opportunity_Eligible flag
    - Full universe retained, but parking/debt ETFs excluded from opportunity rankings

Run:
    python universe_builder.py
"""

from __future__ import annotations

from pathlib import Path
from io import StringIO
import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

CONFIG_CSV = BASE_DIR / "config.csv"

NIFTY50_URL = "https://niftyindices.com/IndexConstituent/ind_nifty50list.csv"
NIFTY_NEXT50_URL = "https://niftyindices.com/IndexConstituent/ind_niftynext50list.csv"
NIFTY_MIDCAP150_URL = "https://niftyindices.com/IndexConstituent/ind_niftymidcap150list.csv"
NSE_ETF_URL = "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/csv,application/csv,text/plain,*/*",
}

PARKING_KEYWORDS = [
    "LIQUID", "LIQ", "CASH", "MONEY", "OVERNIGHT", "TREPS", "1D", "1D RATE",
    "GILT", "GSEC", "G-SEC", "SDL", "BOND", "DEBT", "T-BILL", "TBILL",
    "COLLATERAL", "LOW DURATION", "ULTRA SHORT", "MONEY MARKET",
]


def download_csv(url: str, label: str) -> pd.DataFrame:
    print(f"Downloading {label}...")
    response = requests.get(url, headers=HEADERS, timeout=45)
    response.raise_for_status()
    text = response.text.strip()
    if not text:
        raise ValueError(f"Empty response for {label}: {url}")
    df = pd.read_csv(StringIO(text))
    df.columns = [str(c).strip() for c in df.columns]
    return df


def download_csv_cached(url: str, label: str, cache_name: str) -> pd.DataFrame:
    """Download a CSV, falling back to the last cached copy if NSE blocks/fails."""
    cache = DATA_DIR / cache_name
    try:
        df = download_csv(url, label)
        df.to_csv(cache, index=False)
        return df
    except Exception as exc:
        if cache.exists():
            print(f"WARNING: {label} download failed ({exc}). Reusing cached {cache.name}.")
            df = pd.read_csv(cache)
            df.columns = [str(c).strip() for c in df.columns]
            return df
        raise


def clean_symbol_to_yahoo(symbol: str) -> str:
    symbol = str(symbol).strip().upper()
    if not symbol:
        return ""
    if symbol.endswith(".NS"):
        return symbol
    return f"{symbol}.NS"


def is_bad_symbol(raw_symbol: str) -> bool:
    s = str(raw_symbol).strip().upper()
    if not s:
        return True
    return s.startswith("DUMMY") or s.startswith("TEST")


def contains_any(text: str, keywords: list[str]) -> bool:
    t = str(text).upper()
    return any(k in t for k in keywords)


def infer_etf_category(row: pd.Series) -> str:
    text = " ".join([str(x) for x in row.values]).upper()

    if contains_any(text, PARKING_KEYWORDS):
        return "Parking/Debt ETF"
    if "GOLD" in text:
        return "Gold ETF"
    if "SILVER" in text:
        return "Silver ETF"
    if any(k in text for k in ["NASDAQ", "S&P", "FANG", "HANG", "GLOBAL", "NYSE", "US ", "USA"]):
        return "International ETF"
    if any(k in text for k in ["BANK", "FINANCIAL", "PSU BANK"]):
        return "Bank/Financial ETF"
    if "IT" in text or "TECH" in text:
        return "IT ETF"
    if "PHARMA" in text or "HEALTH" in text:
        return "Pharma/Healthcare ETF"
    if "AUTO" in text:
        return "Auto ETF"
    if "CONSUM" in text or "FMCG" in text:
        return "Consumption/FMCG ETF"
    if "PSU" in text or "CPSE" in text:
        return "PSU/CPSE ETF"
    if any(k in text for k in ["MOMENTUM", "QUALITY", "VALUE", "LOW VOL", "ALPHA"]):
        return "Factor ETF"
    if any(k in text for k in ["MIDCAP", "SMALLCAP", "NEXT 50", "NIFTY", "SENSEX"]):
        return "Broad Market ETF"
    return "ETF"


def infer_opportunity_type(universe: str, category: str) -> str:
    if universe == "Stock":
        return "Stock"
    c = str(category).upper()
    if "PARKING" in c or "DEBT" in c:
        return "Parking/Debt ETF"
    if "GOLD" in c or "SILVER" in c:
        return "Commodity ETF"
    if "INTERNATIONAL" in c:
        return "International ETF"
    return "Equity ETF"


def is_opportunity_eligible(universe: str, category: str, name: str = "", raw_symbol: str = "") -> str:
    if universe != "ETF":
        return "Yes"
    text = f"{category} {name} {raw_symbol}".upper()
    if contains_any(text, PARKING_KEYWORDS):
        return "No"
    return "Yes"


def normalize_index_df(df: pd.DataFrame, universe_group: str) -> pd.DataFrame:
    col_map = {c.lower().strip(): c for c in df.columns}
    symbol_col = col_map.get("symbol")
    name_col = col_map.get("company name")
    industry_col = col_map.get("industry")
    isin_col = col_map.get("isin code")

    if not symbol_col:
        raise ValueError(f"No Symbol column found for {universe_group}. Columns: {df.columns.tolist()}")

    working = df[~df[symbol_col].map(is_bad_symbol)].copy()
    out = pd.DataFrame(index=working.index)
    out["Universe"] = "Stock"
    out["Universe_Group"] = universe_group
    out["Symbol"] = working[symbol_col].map(clean_symbol_to_yahoo)
    out["Raw_Symbol"] = working[symbol_col].astype(str).str.strip().str.upper()
    out["Name"] = working[name_col].astype(str).str.strip() if name_col else out["Raw_Symbol"]
    out["Category"] = working[industry_col].astype(str).str.strip() if industry_col else universe_group
    out["ISIN"] = working[isin_col].astype(str).str.strip() if isin_col else ""
    out["Include"] = "Yes"
    out["Source"] = universe_group
    out["Opportunity_Type"] = "Stock"
    out["Opportunity_Eligible"] = "Yes"
    return out.reset_index(drop=True)


def normalize_etf_df(df: pd.DataFrame) -> pd.DataFrame:
    cols = {c.lower().strip(): c for c in df.columns}
    symbol_col = None
    name_col = None
    isin_col = None

    for k, v in cols.items():
        if k in ["symbol", "sym"]:
            symbol_col = v
        if ("name" in k or "security" in k) and name_col is None:
            name_col = v
        if "isin" in k:
            isin_col = v

    if symbol_col is None:
        symbol_col = df.columns[0]

    working = df[~df[symbol_col].map(is_bad_symbol)].copy()
    out = pd.DataFrame(index=working.index)
    out["Universe"] = "ETF"
    out["Universe_Group"] = "ETF"
    out["Symbol"] = working[symbol_col].map(clean_symbol_to_yahoo)
    out["Raw_Symbol"] = working[symbol_col].astype(str).str.strip().str.upper()
    out["Name"] = working[name_col].astype(str).str.strip() if name_col else out["Raw_Symbol"]
    out["Category"] = working.apply(infer_etf_category, axis=1)
    out["ISIN"] = working[isin_col].astype(str).str.strip() if isin_col else ""
    out["Include"] = "Yes"
    out["Source"] = "NSE ETF"
    out["Opportunity_Type"] = out.apply(lambda r: infer_opportunity_type(r["Universe"], r["Category"]), axis=1)
    out["Opportunity_Eligible"] = out.apply(
        lambda r: is_opportunity_eligible(r["Universe"], r["Category"], r["Name"], r["Raw_Symbol"]),
        axis=1
    )
    return out.reset_index(drop=True)


def build_universe() -> pd.DataFrame:
    logs = []

    n50 = download_csv_cached(NIFTY50_URL, "Nifty 50", "universe_nifty50_raw.csv")
    n50_norm = normalize_index_df(n50, "Nifty50")
    n50_norm.to_csv(DATA_DIR / "universe_nifty50.csv", index=False)
    logs.append(f"Nifty50 rows: {len(n50_norm)}")

    nn50 = download_csv_cached(NIFTY_NEXT50_URL, "Nifty Next 50", "universe_niftynext50_raw.csv")
    nn50_norm = normalize_index_df(nn50, "NiftyNext50")
    nn50_norm.to_csv(DATA_DIR / "universe_niftynext50.csv", index=False)
    logs.append(f"NiftyNext50 rows: {len(nn50_norm)}")

    mid150 = download_csv_cached(NIFTY_MIDCAP150_URL, "Nifty Midcap 150", "universe_niftymidcap150_raw.csv")
    mid150_norm = normalize_index_df(mid150, "NiftyMidcap150")
    mid150_norm.to_csv(DATA_DIR / "universe_niftymidcap150.csv", index=False)
    logs.append(f"NiftyMidcap150 rows: {len(mid150_norm)}")

    etf = download_csv_cached(NSE_ETF_URL, "NSE ETF list", "universe_etfs_raw.csv")
    etf_norm = normalize_etf_df(etf)
    etf_norm.to_csv(DATA_DIR / "universe_etfs.csv", index=False)
    logs.append(f"ETF rows after dummy-filter: {len(etf_norm)}")

    combined = pd.concat([n50_norm, nn50_norm, mid150_norm, etf_norm], ignore_index=True)
    priority = {"Nifty50": 1, "NiftyNext50": 2, "NiftyMidcap150": 3, "ETF": 4}
    combined["Priority"] = combined["Universe_Group"].map(priority).fillna(9)
    combined = combined.sort_values(["Symbol", "Priority"]).drop_duplicates(subset=["Symbol"], keep="first")
    combined = combined.drop(columns=["Priority"])

    ordered_cols = [
        "Universe", "Universe_Group", "Opportunity_Type", "Opportunity_Eligible",
        "Symbol", "Raw_Symbol", "Name", "Category", "ISIN", "Include", "Source"
    ]
    combined = combined[ordered_cols].sort_values(["Universe", "Universe_Group", "Symbol"]).reset_index(drop=True)
    combined.to_csv(CONFIG_CSV, index=False)

    logs.append(f"Combined config rows after dedupe: {len(combined)}")
    logs.append("")
    logs.append("Universe_Group counts:")
    logs.append(combined["Universe_Group"].value_counts(dropna=False).to_string())
    logs.append("")
    logs.append("Opportunity eligibility counts:")
    logs.append(combined["Opportunity_Eligible"].value_counts(dropna=False).to_string())

    (DATA_DIR / "universe_build_log.txt").write_text("\n".join(logs), encoding="utf-8")
    return combined


def main() -> None:
    print("Universe Builder - Stage 3.3 Final")
    print("============================")
    universe = build_universe()
    print(f"\nSaved config: {CONFIG_CSV}")
    print(f"Total rows: {len(universe)}")
    print("\nUniverse counts:")
    print(universe["Universe_Group"].value_counts(dropna=False).to_string())
    print("\nOpportunity eligibility:")
    print(universe["Opportunity_Eligible"].value_counts(dropna=False).to_string())
    print("\nDone. Next run: python etf_quality_builder.py")


if __name__ == "__main__":
    main()
