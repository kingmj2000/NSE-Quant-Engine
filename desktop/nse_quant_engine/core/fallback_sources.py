"""
Phase 1B: Multi-Source Fallback Data Provider
===============================================
Provides fallback data sources for ETF metadata (TER, AUM, Tracking Error).
Priority: AMFI → NSE → MorningStar → SEBI → Manual

This module is designed to be imported by etf_metadata_enricher.py to fill gaps.
"""

from __future__ import annotations
from pathlib import Path
from datetime import datetime, timedelta
import json
import re
import pandas as pd
import numpy as np
import requests
import time

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
DATA_DIR.mkdir(exist_ok=True)
BASE_DIR = DATA_DIR.parent

# ── Fallback Data Source Registry ──────────────────────────────────────────
# Each source has priority, URL/endpoint, and parsing logic
FALLBACK_SOURCES = {
    'NSE_OFFICIAL': {
        'priority': 1,
        'name': 'NSE Official ETF Metadata',
        'description': 'NSE official ETF information endpoint',
        'timeout': 30,
    },
    'MORNINGSTAR': {
        'priority': 2,
        'name': 'MorningStar India',
        'description': 'MorningStar ETF data API',
        'timeout': 30,
    },
    'SEBI': {
        'priority': 3,
        'name': 'SEBI Registrar',
        'description': 'SEBI fund information database',
        'timeout': 60,
    },
    'ZERODHA': {
        'priority': 4,
        'name': 'Zerodha Kite',
        'description': 'Zerodha instrument data',
        'timeout': 30,
    },
    'MANUAL_OVERRIDE': {
        'priority': 5,
        'name': 'Manual Override File',
        'description': 'User-provided manual fixes',
        'timeout': 0,
    },
}

class MultiSourceFallback:
    """
    Manages multi-source fallback for ETF metadata.
    Tries each source in priority order until data found or all exhausted.
    """
    
    def __init__(self):
        self.cache = {}
        self.source_log = []
        self.failures = []
        self.nse_cache_file = DATA_DIR / 'nse_etf_metadata_cache.json'
        self.morningstar_cache_file = DATA_DIR / 'morningstar_etf_cache.json'
        self.zerodha_cache_file = DATA_DIR / 'zerodha_instrument_cache.json'
        self.manual_overrides_file = BASE_DIR / 'etf_metadata_fallback_overrides.csv'
        
        self._load_caches()
    
    def _load_caches(self):
        """Load cached data from previous fetches."""
        if self.nse_cache_file.exists():
            try:
                with open(self.nse_cache_file, 'r') as f:
                    self.cache['nse'] = json.load(f)
            except Exception as e:
                pass
        
        if self.morningstar_cache_file.exists():
            try:
                with open(self.morningstar_cache_file, 'r') as f:
                    self.cache['morningstar'] = json.load(f)
            except Exception as e:
                pass
        
        if self.zerodha_cache_file.exists():
            try:
                with open(self.zerodha_cache_file, 'r') as f:
                    self.cache['zerodha'] = json.load(f)
            except Exception as e:
                pass
    
    def get_ter_fallback(self, symbol: str, isin: str = None, scheme_name: str = None) -> tuple[float, str]:
        """
        Try to fetch TER from fallback sources.
        Returns: (ter_value, source) or (np.nan, 'not_found')
        """
        sources_to_try = [
            ('manual_override', self._try_manual_override_ter),
            ('nse', self._try_nse_ter),
            ('morningstar', self._try_morningstar_ter),
            ('zerodha', self._try_zerodha_ter),
        ]
        
        for source_name, fetch_func in sources_to_try:
            try:
                value = fetch_func(symbol, isin, scheme_name)
                if pd.notna(value):
                    self.source_log.append({
                        'Timestamp': datetime.now().isoformat(),
                        'Symbol': symbol,
                        'Metric': 'TER',
                        'Source': source_name,
                        'Value': value,
                        'Status': 'Success',
                    })
                    return value, source_name
            except Exception as e:
                self.failures.append({
                    'Symbol': symbol,
                    'Metric': 'TER',
                    'Source': source_name,
                    'Error': str(e)[:100],
                    'Timestamp': datetime.now().isoformat(),
                })
                continue
        
        self.source_log.append({
            'Timestamp': datetime.now().isoformat(),
            'Symbol': symbol,
            'Metric': 'TER',
            'Source': 'all_exhausted',
            'Value': np.nan,
            'Status': 'NotFound',
        })
        return np.nan, 'not_found'
    
    def get_aum_fallback(self, symbol: str, isin: str = None, scheme_code: str = None) -> tuple[float, str]:
        """
        Try to fetch AUM from fallback sources.
        Returns: (aum_in_crores, source) or (np.nan, 'not_found')
        """
        sources_to_try = [
            ('manual_override', self._try_manual_override_aum),
            ('nse', self._try_nse_aum),
            ('morningstar', self._try_morningstar_aum),
            ('sebi', self._try_sebi_aum),
            ('zerodha', self._try_zerodha_aum),
        ]
        
        for source_name, fetch_func in sources_to_try:
            try:
                value = fetch_func(symbol, isin, scheme_code)
                if pd.notna(value):
                    self.source_log.append({
                        'Timestamp': datetime.now().isoformat(),
                        'Symbol': symbol,
                        'Metric': 'AUM_Cr',
                        'Source': source_name,
                        'Value': value,
                        'Status': 'Success',
                    })
                    return value, source_name
            except Exception as e:
                self.failures.append({
                    'Symbol': symbol,
                    'Metric': 'AUM_Cr',
                    'Source': source_name,
                    'Error': str(e)[:100],
                    'Timestamp': datetime.now().isoformat(),
                })
                continue
        
        self.source_log.append({
            'Timestamp': datetime.now().isoformat(),
            'Symbol': symbol,
            'Metric': 'AUM_Cr',
            'Source': 'all_exhausted',
            'Value': np.nan,
            'Status': 'NotFound',
        })
        return np.nan, 'not_found'
    
    def get_tracking_error_fallback(self, symbol: str, isin: str = None) -> tuple[float, str]:
        """Try to fetch tracking error from fallback sources."""
        sources_to_try = [
            ('manual_override', self._try_manual_override_tracking_error),
            ('morningstar', self._try_morningstar_tracking_error),
            ('zerodha', self._try_zerodha_tracking_error),
        ]
        
        for source_name, fetch_func in sources_to_try:
            try:
                value = fetch_func(symbol, isin)
                if pd.notna(value):
                    self.source_log.append({
                        'Timestamp': datetime.now().isoformat(),
                        'Symbol': symbol,
                        'Metric': 'Tracking_Error',
                        'Source': source_name,
                        'Value': value,
                        'Status': 'Success',
                    })
                    return value, source_name
            except Exception as e:
                self.failures.append({
                    'Symbol': symbol,
                    'Metric': 'Tracking_Error',
                    'Source': source_name,
                    'Error': str(e)[:100],
                    'Timestamp': datetime.now().isoformat(),
                })
                continue
        
        return np.nan, 'not_found'
    
    # ── Individual source fetchers (implementations) ────────────────────────
    
    def _try_manual_override_ter(self, symbol: str, isin: str = None, scheme_name: str = None) -> float:
        """Check manual overrides file for TER."""
        if not self.manual_overrides_file.exists():
            return np.nan
        
        try:
            df = pd.read_csv(self.manual_overrides_file)
            # Match by symbol, case-insensitive
            match = df[df['Symbol'].astype(str).str.strip().str.upper() == symbol.upper()]
            if not match.empty and 'TER' in df.columns:
                ter_val = match.iloc[0]['TER']
                if pd.notna(ter_val):
                    return float(ter_val)
        except Exception as e:
            pass
        return np.nan
    
    def _try_manual_override_aum(self, symbol: str, isin: str = None, scheme_code: str = None) -> float:
        """Check manual overrides file for AUM."""
        if not self.manual_overrides_file.exists():
            return np.nan
        
        try:
            df = pd.read_csv(self.manual_overrides_file)
            match = df[df['Symbol'].astype(str).str.strip().str.upper() == symbol.upper()]
            if not match.empty and 'AUM_Cr' in df.columns:
                aum_val = match.iloc[0]['AUM_Cr']
                if pd.notna(aum_val):
                    return float(aum_val)
        except Exception as e:
            pass
        return np.nan
    
    def _try_manual_override_tracking_error(self, symbol: str, isin: str = None) -> float:
        """Check manual overrides file for tracking error."""
        if not self.manual_overrides_file.exists():
            return np.nan
        
        try:
            df = pd.read_csv(self.manual_overrides_file)
            match = df[df['Symbol'].astype(str).str.strip().str.upper() == symbol.upper()]
            if not match.empty and 'Tracking_Error' in df.columns:
                te_val = match.iloc[0]['Tracking_Error']
                if pd.notna(te_val):
                    return float(te_val)
        except Exception as e:
            pass
        return np.nan
    
    def _try_nse_ter(self, symbol: str, isin: str = None, scheme_name: str = None) -> float:
        """Try NSE official metadata for TER."""
        # NSE doesn't directly publish TER in a simple endpoint
        # This would require scraping NSE ETF pages or accessing a paid API
        # For now, return cached data or np.nan
        if 'nse_ter' in self.cache and symbol in self.cache['nse_ter']:
            return self.cache['nse_ter'][symbol]
        return np.nan
    
    def _try_nse_aum(self, symbol: str, isin: str = None, scheme_code: str = None) -> float:
        """Try NSE official metadata for AUM."""
        if 'nse_aum' in self.cache and symbol in self.cache['nse_aum']:
            return self.cache['nse_aum'][symbol]
        return np.nan
    
    def _try_morningstar_ter(self, symbol: str, isin: str = None, scheme_name: str = None) -> float:
        """Try MorningStar for TER (would require API access)."""
        # Placeholder: MorningStar API access would require subscription
        if 'morningstar_ter' in self.cache and symbol in self.cache['morningstar_ter']:
            return self.cache['morningstar_ter'][symbol]
        return np.nan
    
    def _try_morningstar_aum(self, symbol: str, isin: str = None, scheme_code: str = None) -> float:
        """Try MorningStar for AUM."""
        if 'morningstar_aum' in self.cache and symbol in self.cache['morningstar_aum']:
            return self.cache['morningstar_aum'][symbol]
        return np.nan
    
    def _try_morningstar_tracking_error(self, symbol: str, isin: str = None) -> float:
        """Try MorningStar for tracking error."""
        if 'morningstar_tracking_error' in self.cache and symbol in self.cache['morningstar_tracking_error']:
            return self.cache['morningstar_tracking_error'][symbol]
        return np.nan
    
    def _try_zerodha_ter(self, symbol: str, isin: str = None, scheme_name: str = None) -> float:
        """Try Zerodha Kite data for TER."""
        # Zerodha doesn't publish TER directly, but could scrape or use API
        if 'zerodha_ter' in self.cache and symbol in self.cache['zerodha_ter']:
            return self.cache['zerodha_ter'][symbol]
        return np.nan
    
    def _try_zerodha_aum(self, symbol: str, isin: str = None, scheme_code: str = None) -> float:
        """Try Zerodha Kite data for AUM."""
        if 'zerodha_aum' in self.cache and symbol in self.cache['zerodha_aum']:
            return self.cache['zerodha_aum'][symbol]
        return np.nan
    
    def _try_zerodha_tracking_error(self, symbol: str, isin: str = None) -> float:
        """Try Zerodha for tracking error."""
        return np.nan  # Zerodha doesn't publish tracking error
    
    def _try_sebi_aum(self, symbol: str, isin: str = None, scheme_code: str = None) -> float:
        """Try SEBI registrar for AUM."""
        # SEBI publishes AUM monthly but requires parsing official documents
        if 'sebi_aum' in self.cache and symbol in self.cache['sebi_aum']:
            return self.cache['sebi_aum'][symbol]
        return np.nan
    
    def save_logs(self):
        """Save fallback attempt logs."""
        if self.source_log:
            df_log = pd.DataFrame(self.source_log)
            df_log.to_csv(DATA_DIR / 'fallback_source_log.csv', index=False)
        
        if self.failures:
            df_failures = pd.DataFrame(self.failures)
            df_failures.to_csv(DATA_DIR / 'fallback_source_failures.csv', index=False)


def create_template_manual_overrides():
    """Create template file for manual TER/AUM overrides."""
    template_file = Path(__file__).resolve().parent / 'etf_metadata_fallback_overrides_template.csv'
    if not template_file.exists():
        template_df = pd.DataFrame({
            'Symbol': ['ABSLLIQUID.NS', 'AONELIQUID.NS', 'AXISTECETF.NS'],
            'ISIN': ['INF109K01FS0', 'INF090I01HM6', 'INF093K01HT5'],
            'TER': [0.0015, 0.0030, 0.0050],  # As decimal
            'AUM_Cr': [250.0, 150.0, 500.0],
            'Tracking_Error': [0.002, 0.003, 0.005],
            'Source': ['Manual', 'Manual', 'Manual'],
            'Notes': ['AMC Website', 'Fund Fact Sheet', 'Recent Report'],
        })
        template_df.to_csv(template_file, index=False)
        print(f"Created template: {template_file}")
        print("Edit this file to add manual TER/AUM/Tracking_Error for ETFs with gaps")
        print("Then copy to etf_metadata_fallback_overrides.csv for use")


if __name__ == '__main__':
    create_template_manual_overrides()
    print("Template created. Edit and save as etf_metadata_fallback_overrides.csv to use.")
