"""
Phase 1A: Audit Current Data Gaps
==================================
Analyzes current data quality for ETFs and stocks.
Produces: data_gap_audit.csv
"""

from pathlib import Path
import pandas as pd
import sys

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'

print("="*70)
print("PHASE 1A: AUDIT CURRENT DATA GAPS")
print("="*70)

# ── Analyze ETF Quality ──────────────────────────────────────────────────
etf_quality_file = DATA_DIR / 'etf_quality_latest.csv'
if etf_quality_file.exists():
    df_etf = pd.read_csv(etf_quality_file)
    print(f"\n[ETF QUALITY] Total ETFs: {len(df_etf)}")
    print(f"  TER coverage: {df_etf['TER'].notna().sum()}/{len(df_etf)} ({100*df_etf['TER'].notna().sum()/len(df_etf):.1f}%)")
    print(f"  AUM coverage: {df_etf['AUM_Cr'].notna().sum()}/{len(df_etf)} ({100*df_etf['AUM_Cr'].notna().sum()/len(df_etf):.1f}%)")
    print(f"  NAV coverage: {df_etf['NAV'].notna().sum()}/{len(df_etf)} ({100*df_etf['NAV'].notna().sum()/len(df_etf):.1f}%)")
    
    print(f"\n  Missing TER (first 15):")
    missing_ter = df_etf[df_etf['TER'].isna()][['Symbol', 'Name']].head(15)
    for _, row in missing_ter.iterrows():
        print(f"    - {row['Symbol']}: {row['Name']}")
    
    print(f"\n  Missing AUM (first 15):")
    missing_aum = df_etf[df_etf['AUM_Cr'].isna()][['Symbol', 'Name']].head(15)
    for _, row in missing_aum.iterrows():
        print(f"    - {row['Symbol']}: {row['Name']}")
else:
    print(f"[ETF QUALITY] File not found: {etf_quality_file}")

# ── Analyze ETF Unresolved Mappings ──────────────────────────────────────
unresolved_file = DATA_DIR / 'etf_metadata_unresolved_review.csv'
if unresolved_file.exists():
    df_unresolved = pd.read_csv(unresolved_file)
    print(f"\n[ETF MAPPING] Unresolved ETFs: {len(df_unresolved)}")
    print(f"  Mapping issues (first 10):")
    for _, row in df_unresolved.head(10).iterrows():
        print(f"    - {row['Symbol']}: {row['Name']} (confidence: {row.get('Match_Score', 'N/A')})")
else:
    print(f"[ETF MAPPING] File not found: {unresolved_file}")

# ── Analyze Price Download Failures ──────────────────────────────────────
price_diag_file = DATA_DIR / 'price_download_diagnostics.csv'
if price_diag_file.exists():
    df_price = pd.read_csv(price_diag_file)
    print(f"\n[PRICE DATA] Total symbols checked: {len(df_price)}")
    if 'Status' in df_price.columns:
        status_counts = df_price['Status'].value_counts()
        print(f"  Status breakdown:")
        for status, count in status_counts.items():
            print(f"    - {status}: {count}")
        failures = df_price[df_price['Status'] == 'Failed']
        if len(failures) > 0:
            print(f"  Failed symbols (first 10):")
            for _, row in failures.head(10).iterrows():
                print(f"    - {row['Symbol']}: {row.get('Reason', 'Unknown error')}")
    else:
        print(f"  Status column not found. Columns: {df_price.columns.tolist()}")
else:
    print(f"[PRICE DATA] File not found: {price_diag_file}")

# ── Analyze Fundamentals ────────────────────────────────────────────────
config_file = BASE_DIR / 'config.csv'
if config_file.exists():
    df_config = pd.read_csv(config_file)
    stocks = df_config[df_config['Universe'].astype(str).str.upper() != 'ETF']
    print(f"\n[FUNDAMENTALS] Total stocks in universe: {len(stocks)}")
    
    fundamentals_file = DATA_DIR / 'fundamentals_latest.csv'
    if fundamentals_file.exists():
        df_fund = pd.read_csv(fundamentals_file)
        print(f"  Fundamentals fetched: {len(df_fund)}")
        print(f"  PE coverage: {df_fund['PE'].notna().sum()}/{len(df_fund)}")
        print(f"  ROE coverage: {df_fund['ROE'].notna().sum()}/{len(df_fund)}")
        print(f"  DebtToEquity coverage: {df_fund['DebtToEquity'].notna().sum()}/{len(df_fund)}")
        print(f"  EarningsGrowth coverage: {df_fund['EarningsGrowth'].notna().sum()}/{len(df_fund)}")
        print(f"  ProfitMargin coverage: {df_fund['ProfitMargin'].notna().sum()}/{len(df_fund)}")
    else:
        print(f"  Fundamentals file not found: {fundamentals_file}")
else:
    print(f"[FUNDAMENTALS] Config file not found: {config_file}")

# ── Summary Statistics ──────────────────────────────────────────────────
print("\n" + "="*70)
print("SUMMARY: Key Data Quality Gaps")
print("="*70)
print("""
Priority Issues to Fix (Phase 1):
1. ETF TER coverage gaps → Add fallback sources (NSE, MorningStar, SEBI)
2. ETF AUM coverage gaps → Add fallback sources
3. ETF fuzzy matching failures → Improve matching algorithm
4. Price download failures → Add alternate data source fallback
5. Fundamentals coverage → Add BSE/NSE API, Zerodha, Moneycontrol
""")

print("\nNext Steps:")
print("1. Run phase_1b_implement_fallbacks.py  → Add multi-source fallback logic")
print("2. Run phase_1c_improve_fuzzy_matching.py → Enhance ETF mapping")
print("3. Run phase_1d_enhance_fundamentals.py → Expand fundamentals coverage")
print("\n" + "="*70)
