"""Sector mapping + sector relative-strength helpers."""
from __future__ import annotations
import pandas as pd
import numpy as np

# yfinance sector -> NSE sector index symbol
SECTOR_INDEX = {
    "Technology": "^CNXIT",
    "Information Technology": "^CNXIT",
    "Financial Services": "^CNXFIN",
    "Financial": "^CNXFIN",
    "Banks": "^NSEBANK",
    "Energy": "^CNXENERGY",
    "Utilities": "^CNXENERGY",
    "Healthcare": "^CNXPHARMA",
    "Consumer Defensive": "^CNXFMCG",
    "Consumer Cyclical": "^CNXAUTO",
    "Basic Materials": "^CNXMETAL",
    "Industrials": "^CNXMETAL",
    "Real Estate": "^CNXREALTY",
    "Communication Services": "^CNXMEDIA",
}


def map_symbol_to_sector_index(sector: str | None, override_map: dict | None = None) -> str | None:
    if override_map and sector and sector in override_map:
        return override_map[sector]
    if not sector:
        return None
    return SECTOR_INDEX.get(sector)


def sector_rs_multiplier(stock_ret_21d: float, sector_ret_21d: float,
                         confirm: float = 1.0, fail: float = 0.92) -> float:
    if pd.isna(stock_ret_21d) or pd.isna(sector_ret_21d):
        return fail
    return confirm if stock_ret_21d >= sector_ret_21d else fail


def combined_rs(market_mult: float, sector_mult: float) -> float:
    """Geometric mean of market and sector RS multipliers."""
    return float(np.sqrt(max(market_mult, 0.0) * max(sector_mult, 0.0)))
