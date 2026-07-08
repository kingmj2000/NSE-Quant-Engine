"""
Step 14 — Institutional-Flow Overlay (offline, optional inputs).

Reads two optional CSVs the user drops into `data/`:
  * fii_dii_daily.csv  columns: Date, FII_Net_INR_Cr, DII_Net_INR_Cr
  * bulk_deals.csv     columns: Date, Symbol, Client, Buy_Sell, Qty, Price

Absent → all outputs are 'Unknown'. Never raises.
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd


def load_fii_dii(path: Path, lookback_days: int = 5) -> dict:
    """Return {'fii_regime', 'fii_net_5d_cr', 'dii_net_5d_cr', 'as_of'}."""
    out = {"fii_regime": "Unknown", "fii_net_5d_cr": None,
           "dii_net_5d_cr": None, "as_of": None}
    p = Path(path)
    if not p.exists():
        return out
    try:
        df = pd.read_csv(p)
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).sort_values("Date").tail(lookback_days)
        if df.empty:
            return out
        fii = float(pd.to_numeric(df.get("FII_Net_INR_Cr"), errors="coerce").sum())
        dii = float(pd.to_numeric(df.get("DII_Net_INR_Cr"), errors="coerce").sum())
        out["fii_net_5d_cr"] = round(fii, 1)
        out["dii_net_5d_cr"] = round(dii, 1)
        out["as_of"] = df["Date"].max().strftime("%Y-%m-%d")
        if fii > 500:
            reg = "Net_Buying"
        elif fii < -500:
            reg = "Net_Selling"
        else:
            reg = "Mixed"
        out["fii_regime"] = reg
    except Exception:
        pass
    return out


def load_bulk_deals(path: Path, lookback_days: int = 30) -> pd.DataFrame:
    """Returns per-symbol aggregate: Symbol, Buy_Value_Cr, Sell_Value_Cr, Net_Flag."""
    cols = ["Symbol", "Buy_Value_Cr", "Sell_Value_Cr", "N_Deals", "Net_Flag"]
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(p)
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=lookback_days)
        df = df.dropna(subset=["Date", "Symbol"])
        df = df[df["Date"] >= cutoff]
        if df.empty:
            return pd.DataFrame(columns=cols)
        df["Value"] = (pd.to_numeric(df.get("Qty"), errors="coerce") *
                       pd.to_numeric(df.get("Price"), errors="coerce")) / 1e7
        df["Side"] = df.get("Buy_Sell", "").astype(str).str.upper().str[0]
        rows = []
        for sym, g in df.groupby("Symbol"):
            buy = float(g.loc[g["Side"] == "B", "Value"].sum())
            sell = float(g.loc[g["Side"] == "S", "Value"].sum())
            if buy - sell > 0.5:
                flag = "Buy"
            elif sell - buy > 0.5:
                flag = "Sell"
            else:
                flag = "None"
            rows.append({"Symbol": str(sym), "Buy_Value_Cr": round(buy, 2),
                         "Sell_Value_Cr": round(sell, 2), "N_Deals": int(len(g)),
                         "Net_Flag": flag})
        return pd.DataFrame(rows, columns=cols)
    except Exception:
        return pd.DataFrame(columns=cols)


def enrich(top5: pd.DataFrame,
           data_dir: Path,
           fii_lookback: int = 5,
           bulk_lookback: int = 30) -> tuple[pd.DataFrame, dict]:
    """Return (per_pick_df, macro_fii_dict). Never raises."""
    cols = ["Symbol", "Bulk_Deal_Flag", "Bulk_Buy_Cr", "Bulk_Sell_Cr",
            "N_Bulk_Deals", "FII_Regime", "Institutional_Confirmation"]
    fii = load_fii_dii(Path(data_dir) / "fii_dii_daily.csv", fii_lookback)
    bulk = load_bulk_deals(Path(data_dir) / "bulk_deals.csv", bulk_lookback)
    if top5 is None or top5.empty:
        return pd.DataFrame(columns=cols), fii

    bmap = {str(r["Symbol"]): r for _, r in bulk.iterrows()} if not bulk.empty else {}
    rows = []
    for _, r in top5.iterrows():
        sym = str(r.get("Symbol", ""))
        if not sym:
            continue
        b = bmap.get(sym)
        bflag = b["Net_Flag"] if b is not None else "Unknown"
        buy_cr = float(b["Buy_Value_Cr"]) if b is not None else np.nan
        sell_cr = float(b["Sell_Value_Cr"]) if b is not None else np.nan
        n = int(b["N_Deals"]) if b is not None else 0
        if bflag == "Buy" and fii["fii_regime"] == "Net_Buying":
            conf = "Yes"
        elif bflag == "Sell" or fii["fii_regime"] == "Net_Selling":
            conf = "No"
        elif bflag == "Unknown" and fii["fii_regime"] == "Unknown":
            conf = "Unknown"
        else:
            conf = "Mixed"
        rows.append({
            "Symbol": sym, "Bulk_Deal_Flag": bflag,
            "Bulk_Buy_Cr": buy_cr if pd.notna(buy_cr) else np.nan,
            "Bulk_Sell_Cr": sell_cr if pd.notna(sell_cr) else np.nan,
            "N_Bulk_Deals": n, "FII_Regime": fii["fii_regime"],
            "Institutional_Confirmation": conf,
        })
    return pd.DataFrame(rows, columns=cols), fii
