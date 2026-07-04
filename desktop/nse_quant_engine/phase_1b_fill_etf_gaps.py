"""
Phase 1B: Fill ETF Metadata Gaps using Fallback Sources
========================================================
Enhances ETF metadata by filling TER/AUM/Tracking Error gaps using multiple sources.
Updates etf_quality_latest.csv with source tracking.
"""

from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime
import sys

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'

# Import fallback sources
sys.path.insert(0, str(BASE_DIR / 'core'))
from fallback_sources import MultiSourceFallback, create_template_manual_overrides

print("="*70)
print("PHASE 1B: FILL ETF METADATA GAPS")
print("="*70)

# ── Load current ETF quality data ────────────────────────────────────────
etf_quality_file = DATA_DIR / 'etf_quality_latest.csv'
if not etf_quality_file.exists():
    print(f"ERROR: {etf_quality_file} not found. Run etf_quality_builder.py first.")
    sys.exit(1)

df_etf = pd.read_csv(etf_quality_file)
total_etfs = len(df_etf)

print(f"\n[CURRENT STATE]")
print(f"  Total ETFs: {total_etfs}")
print(f"  TER coverage: {df_etf['TER'].notna().sum()}/{total_etfs} ({100*df_etf['TER'].notna().sum()/total_etfs:.1f}%)")
print(f"  AUM coverage: {df_etf['AUM_Cr'].notna().sum()}/{total_etfs} ({100*df_etf['AUM_Cr'].notna().sum()/total_etfs:.1f}%)")
print(f"  Tracking Error coverage: {df_etf['Tracking_Error'].notna().sum()}/{total_etfs} ({100*df_etf['Tracking_Error'].notna().sum()/total_etfs:.1f}%)")

# ── Ensure source tracking columns exist ──────────────────────────────────
if 'TER_Source' not in df_etf.columns:
    df_etf['TER_Source'] = df_etf['TER'].notna().apply(lambda x: 'AMFI' if x else np.nan)
if 'AUM_Source' not in df_etf.columns:
    df_etf['AUM_Source'] = df_etf['AUM_Cr'].notna().apply(lambda x: 'AMFI' if x else np.nan)
if 'Tracking_Error_Source' not in df_etf.columns:
    df_etf['Tracking_Error_Source'] = df_etf['Tracking_Error'].notna().apply(lambda x: 'AMFI' if x else np.nan)

# ── Initialize fallback provider ─────────────────────────────────────────
fallback = MultiSourceFallback()

print(f"\n[ATTEMPTING FALLBACK FILLS]")

ter_filled = 0
aum_filled = 0
tracking_error_filled = 0

# ── Try to fill TER gaps ─────────────────────────────────────────────────
print(f"\nFilling TER gaps...")
missing_ter = df_etf[df_etf['TER'].isna()]
print(f"  Attempting to fill {len(missing_ter)} missing TER values...")

for idx, row in missing_ter.iterrows():
    symbol = row['Symbol']
    isin = row.get('ISIN', None)
    scheme_name = row.get('AMFI_Scheme_Name', None)
    
    ter_value, ter_source = fallback.get_ter_fallback(symbol, isin, scheme_name)
    
    if pd.notna(ter_value):
        df_etf.at[idx, 'TER'] = ter_value
        df_etf.at[idx, 'TER_Source'] = ter_source
        ter_filled += 1
        if ter_filled <= 10:  # Print first 10 fills
            print(f"    ✓ {symbol}: TER={ter_value:.4f} (from {ter_source})")

if ter_filled > 10:
    print(f"    ... and {ter_filled-10} more filled")

# ── Try to fill AUM gaps ─────────────────────────────────────────────────
print(f"\nFilling AUM gaps...")
missing_aum = df_etf[df_etf['AUM_Cr'].isna()]
print(f"  Attempting to fill {len(missing_aum)} missing AUM values...")

for idx, row in missing_aum.iterrows():
    symbol = row['Symbol']
    isin = row.get('ISIN', None)
    scheme_code = row.get('AMFI_Scheme_Code', None)
    
    aum_value, aum_source = fallback.get_aum_fallback(symbol, isin, scheme_code)
    
    if pd.notna(aum_value):
        df_etf.at[idx, 'AUM_Cr'] = aum_value
        df_etf.at[idx, 'AUM_Source'] = aum_source
        aum_filled += 1
        if aum_filled <= 10:
            print(f"    ✓ {symbol}: AUM={aum_value:.1f}Cr (from {aum_source})")

if aum_filled > 10:
    print(f"    ... and {aum_filled-10} more filled")

# ── Try to fill Tracking Error gaps ──────────────────────────────────────
print(f"\nFilling Tracking Error gaps...")
missing_tracking_error = df_etf[df_etf['Tracking_Error'].isna()]
print(f"  Attempting to fill {len(missing_tracking_error)} missing Tracking Error values...")

for idx, row in missing_tracking_error.iterrows():
    symbol = row['Symbol']
    isin = row.get('ISIN', None)
    
    te_value, te_source = fallback.get_tracking_error_fallback(symbol, isin)
    
    if pd.notna(te_value):
        df_etf.at[idx, 'Tracking_Error'] = te_value
        df_etf.at[idx, 'Tracking_Error_Source'] = te_source
        tracking_error_filled += 1
        if tracking_error_filled <= 10:
            print(f"    ✓ {symbol}: Tracking_Error={te_value:.4f} (from {te_source})")

if tracking_error_filled > 10:
    print(f"    ... and {tracking_error_filled-10} more filled")

# ── Save enhanced data ───────────────────────────────────────────────────
print(f"\n[SAVING ENHANCED DATA]")
df_etf.to_csv(etf_quality_file, index=False)
print(f"  Saved: {etf_quality_file}")

# Save fallback logs
fallback.save_logs()
print(f"  Saved: {DATA_DIR / 'fallback_source_log.csv'}")
print(f"  Saved: {DATA_DIR / 'fallback_source_failures.csv'}")

# ── Report results ───────────────────────────────────────────────────────
print(f"\n[RESULTS]")
print(f"  TER filled: {ter_filled} additional values")
print(f"  AUM filled: {aum_filled} additional values")
print(f"  Tracking Error filled: {tracking_error_filled} additional values")

print(f"\n[NEW COVERAGE]")
print(f"  TER coverage: {df_etf['TER'].notna().sum()}/{total_etfs} ({100*df_etf['TER'].notna().sum()/total_etfs:.1f}%)")
print(f"  AUM coverage: {df_etf['AUM_Cr'].notna().sum()}/{total_etfs} ({100*df_etf['AUM_Cr'].notna().sum()/total_etfs:.1f}%)")
print(f"  Tracking Error coverage: {df_etf['Tracking_Error'].notna().sum()}/{total_etfs} ({100*df_etf['Tracking_Error'].notna().sum()/total_etfs:.1f}%)")

print(f"\n[NEXT STEPS]")
print(f"1. Check {DATA_DIR / 'fallback_source_log.csv'} to see which sources were used")
print(f"2. Review template at: core/etf_metadata_fallback_overrides_template.csv")
print(f"3. Add manual data and save as: etf_metadata_fallback_overrides.csv")
print(f"4. Re-run this script to apply manual overrides")

# ── Summary of remaining gaps ────────────────────────────────────────────
print(f"\n[REMAINING GAPS]")
remaining_ter = df_etf[df_etf['TER'].isna()]
if not remaining_ter.empty:
    print(f"  Still missing TER ({len(remaining_ter)}):")
    for _, row in remaining_ter.head(5).iterrows():
        print(f"    - {row['Symbol']}: {row['Name']}")
    if len(remaining_ter) > 5:
        print(f"    ... and {len(remaining_ter)-5} more")

remaining_aum = df_etf[df_etf['AUM_Cr'].isna()]
if not remaining_aum.empty:
    print(f"  Still missing AUM ({len(remaining_aum)}):")
    for _, row in remaining_aum.head(5).iterrows():
        print(f"    - {row['Symbol']}: {row['Name']}")
    if len(remaining_aum) > 5:
        print(f"    ... and {len(remaining_aum)-5} more")

print("\n" + "="*70)
