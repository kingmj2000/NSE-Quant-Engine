"""
Step 10 — Sector & Peer Context (Fincept-inspired, offline).

Existing sector_rs_multiplier / combined_rs helpers preserved.
New: enrich(top5, prices_long, fund_df=None) → per-symbol frame with
  * Sector (from fundamentals cache if present, else 'Unknown')
  * Sector_RS_21D_%, Sector_RS_63D_%   (pick vs Nifty)
  * Peer_1..Peer_3                     (nearest-correlated NSE names from
                                        prices, excluding self, 60D window)
  * Peer_Median_3M_Return_%            (context stat for the LLM)

Pure derivations from data already on disk — no network. NaN-safe.
"""
from __future__ import annotations
import pandas as pd
import numpy as np

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


def sector_rs_multiplier(sec_ret: float, mkt_ret: float,
                         boost: float = 1.05, hair: float = 0.95) -> float:
    """1.05 if sector beats market by ≥3pp, 0.95 if it lags by ≥3pp, else 1.0."""
    try:
        d = float(sec_ret) - float(mkt_ret)
    except Exception:
        return 1.0
    if d >= 0.03:
        return boost
    if d <= -0.03:
        return hair
    return 1.0


def combined_rs(stock_mult: float, sector_mult: float,
                w_stock: float = 0.7, w_sector: float = 0.3) -> float:
    try:
        return round(w_stock * float(stock_mult) + w_sector * float(sector_mult), 4)
    except Exception:
        return 1.0


def _wide_closes(prices_long: pd.DataFrame) -> pd.DataFrame:
    if prices_long is None or prices_long.empty:
        return pd.DataFrame()
    df = prices_long.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Symbol", "Close"])
    return df.pivot_table(index="Date", columns="Symbol",
                          values="Close", aggfunc="last").sort_index()


def _pct_ret(wide: pd.DataFrame, sym: str, n: int) -> float:
    if sym not in wide.columns or len(wide) < n + 1:
        return np.nan
    s = wide[sym].dropna()
    if len(s) < n + 1:
        return np.nan
    return float(s.iloc[-1] / s.iloc[-(n + 1)] - 1.0) * 100.0


def _peers(wide: pd.DataFrame, sym: str, window: int = 60, top_k: int = 3) -> list[str]:
    if sym not in wide.columns or wide.shape[0] < window + 5:
        return []
    tail = wide.iloc[-window:].pct_change().dropna(how="all")
    if sym not in tail.columns:
        return []
    others = [c for c in tail.columns if c != sym and not str(c).startswith("^")]
    if not others:
        return []
    corr = tail[others].corrwith(tail[sym]).dropna()
    return corr.sort_values(ascending=False).head(top_k).index.astype(str).tolist()


def enrich(top5: pd.DataFrame,
           prices_long: pd.DataFrame | None,
           fund_df: pd.DataFrame | None = None,
           benchmark: str = "^NSEI") -> pd.DataFrame:
    """Return per-symbol sector + peer context frame. Never raises."""
    cols = ["Symbol", "Sector", "Sector_Index",
            "Ret_21D_%", "Ret_63D_%",
            "Bench_Ret_21D_%", "Bench_Ret_63D_%",
            "Sector_RS_21D_%", "Sector_RS_63D_%",
            "Peer_1", "Peer_2", "Peer_3",
            "Peer_Median_3M_Return_%"]
    if top5 is None or top5.empty:
        return pd.DataFrame(columns=cols)

    sec_map: dict[str, str] = {}
    if fund_df is not None and not fund_df.empty and "Sector" in fund_df.columns:
        sec_map = {str(r["Symbol"]): str(r.get("Sector") or "")
                   for _, r in fund_df.iterrows() if pd.notna(r.get("Symbol"))}

    wide = _wide_closes(prices_long) if prices_long is not None else pd.DataFrame()
    bench21 = _pct_ret(wide, benchmark, 21) if not wide.empty else np.nan
    bench63 = _pct_ret(wide, benchmark, 63) if not wide.empty else np.nan

    rows = []
    for _, r in top5.iterrows():
        sym = str(r.get("Symbol", ""))
        if not sym:
            continue
        sector = sec_map.get(sym, "Unknown")
        r21 = _pct_ret(wide, sym, 21) if not wide.empty else np.nan
        r63 = _pct_ret(wide, sym, 63) if not wide.empty else np.nan
        rs21 = (r21 - bench21) if (pd.notna(r21) and pd.notna(bench21)) else np.nan
        rs63 = (r63 - bench63) if (pd.notna(r63) and pd.notna(bench63)) else np.nan
        peers = _peers(wide, sym) if not wide.empty else []
        peer_rets = [_pct_ret(wide, p, 63) for p in peers] if peers else []
        peer_med = float(np.nanmedian(peer_rets)) if peer_rets and any(pd.notna(x) for x in peer_rets) else np.nan
        rows.append({
            "Symbol": sym,
            "Sector": sector,
            "Sector_Index": map_symbol_to_sector_index(sector) or "",
            "Ret_21D_%": round(r21, 2) if pd.notna(r21) else np.nan,
            "Ret_63D_%": round(r63, 2) if pd.notna(r63) else np.nan,
            "Bench_Ret_21D_%": round(bench21, 2) if pd.notna(bench21) else np.nan,
            "Bench_Ret_63D_%": round(bench63, 2) if pd.notna(bench63) else np.nan,
            "Sector_RS_21D_%": round(rs21, 2) if pd.notna(rs21) else np.nan,
            "Sector_RS_63D_%": round(rs63, 2) if pd.notna(rs63) else np.nan,
            "Peer_1": peers[0] if len(peers) > 0 else "",
            "Peer_2": peers[1] if len(peers) > 1 else "",
            "Peer_3": peers[2] if len(peers) > 2 else "",
            "Peer_Median_3M_Return_%": round(peer_med, 2) if pd.notna(peer_med) else np.nan,
        })
    return pd.DataFrame(rows, columns=cols)
