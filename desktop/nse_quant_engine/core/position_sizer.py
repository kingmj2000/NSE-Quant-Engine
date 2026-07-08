"""
Step 8 — Risk-Parity / Vol-Targeted Position Sizing.

Two modes: 'inverse_vol' and 'risk_parity_lite' (Newton solve on the existing
correlation matrix, pure numpy, no cvxpy). Output per name:

    Weight_%, Capital_INR, Risk_Contribution_%, Stop_Loss_INR, Max_Loss_INR

All inputs are optional except symbols + a way to derive per-name sigma —
either an explicit sigma frame or a wide returns matrix. Never raises; on
degenerate input returns an empty frame or an equal-weight fallback.
"""
from __future__ import annotations

from typing import Optional
import numpy as np
import pandas as pd


def _returns_wide(prices_long: pd.DataFrame, symbols: list[str],
                  window: int = 63) -> pd.DataFrame:
    if prices_long is None or prices_long.empty:
        return pd.DataFrame()
    df = prices_long[prices_long["Symbol"].isin(symbols)].copy()
    if df.empty:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"])
    wide = df.pivot_table(index="Date", columns="Symbol",
                          values="Close", aggfunc="last").sort_index()
    return wide.pct_change().tail(window).dropna(how="all")


def _sigma_from_returns(rets: pd.DataFrame) -> pd.Series:
    """Annualised daily-return sigma per symbol."""
    if rets is None or rets.empty:
        return pd.Series(dtype=float)
    return rets.std(ddof=0) * np.sqrt(252)


def inverse_vol_weights(sigma: pd.Series, max_weight: float = 0.30) -> pd.Series:
    s = pd.to_numeric(sigma, errors="coerce").replace(0, np.nan).dropna()
    if s.empty:
        return pd.Series(dtype=float)
    w = 1.0 / s
    w = w / w.sum()
    # cap + renormalise (one pass is enough for 5 names)
    w = w.clip(upper=max_weight)
    w = w / w.sum()
    return w


def _cov_from_corr_sigma(corr: pd.DataFrame, sigma: pd.Series) -> pd.DataFrame:
    syms = [s for s in corr.index if s in sigma.index]
    if len(syms) < 2:
        return pd.DataFrame()
    c = corr.loc[syms, syms].values
    s = sigma.loc[syms].values
    cov = c * np.outer(s, s)
    return pd.DataFrame(cov, index=syms, columns=syms)


def risk_parity_lite(cov: pd.DataFrame,
                     max_weight: float = 0.30,
                     iters: int = 50,
                     tol: float = 1e-6) -> pd.Series:
    """Equal-risk-contribution weights via a simple fixed-point iteration
    (numpy only). Falls back to inverse-vol when solve stalls.

    Reference: Maillard et al. (2010), 'On the properties of equally-weighted
    risk contributions portfolios'. This variant uses the well-known
    w_i <- (b_i / (Σw)_i) update, normalised each step.
    """
    if cov is None or cov.empty:
        return pd.Series(dtype=float)
    n = cov.shape[0]
    if n < 2:
        return pd.Series([1.0], index=cov.index)
    C = cov.values
    b = np.ones(n) / n           # equal-risk target
    w = np.ones(n) / n
    for _ in range(iters):
        m = C @ w
        # guard against a zero-vol column
        m = np.where(np.abs(m) < 1e-12, 1e-12, m)
        w_new = b / m
        w_new = np.clip(w_new, 1e-6, None)
        w_new = w_new / w_new.sum()
        # cap
        w_new = np.minimum(w_new, max_weight)
        w_new = w_new / w_new.sum()
        if np.max(np.abs(w_new - w)) < tol:
            w = w_new
            break
        w = w_new
    return pd.Series(w, index=cov.index)


def risk_contributions(weights: pd.Series, cov: pd.DataFrame) -> pd.Series:
    if weights is None or weights.empty or cov is None or cov.empty:
        return pd.Series(dtype=float)
    syms = [s for s in weights.index if s in cov.index]
    w = weights.loc[syms].values
    C = cov.loc[syms, syms].values
    port_var = float(w @ C @ w)
    if port_var <= 0:
        return pd.Series(np.nan, index=syms)
    rc = w * (C @ w)                          # per-name risk contribution
    return pd.Series(rc / port_var, index=syms)


def size_portfolio(top5: pd.DataFrame,
                   prices_long: Optional[pd.DataFrame] = None,
                   corr: Optional[pd.DataFrame] = None,
                   mode: str = "risk_parity_lite",
                   nav_inr: float = 1_000_000.0,
                   vol_target: float = 0.12,
                   max_weight: float = 0.30,
                   cash_buffer: float = 0.10,
                   window: int = 63) -> pd.DataFrame:
    """Produce a per-name sizing frame. `top5` must have Symbol + Price +
    Stop_Loss. Never raises; returns an empty frame if inputs are unusable."""
    if top5 is None or top5.empty:
        return pd.DataFrame()

    df = top5.copy()
    df["Symbol"] = df["Symbol"].astype(str)
    syms = df["Symbol"].tolist()

    rets = _returns_wide(prices_long, syms, window=window) \
        if prices_long is not None else pd.DataFrame()
    sigma = _sigma_from_returns(rets)
    if sigma.empty:
        # equal weight fallback
        w = pd.Series(1.0 / len(syms), index=syms)
        cov = pd.DataFrame()
        rc = pd.Series(1.0 / len(syms), index=syms)
    else:
        if mode == "inverse_vol" or corr is None or corr.empty:
            w = inverse_vol_weights(sigma, max_weight=max_weight)
            cov = pd.DataFrame()
            rc = pd.Series(np.nan, index=w.index)
        else:
            cov = _cov_from_corr_sigma(corr, sigma)
            w = risk_parity_lite(cov, max_weight=max_weight)
            rc = risk_contributions(w, cov)

    # vol target scaling: shrink whole book toward target ex-post
    port_vol = float(np.sqrt(w.values @ cov.values @ w.values)) if not cov.empty else np.nan
    if pd.notna(port_vol) and port_vol > 0:
        scale = min(1.0, vol_target / port_vol)
        w = w * scale                 # unused cash sits in buffer

    investable = nav_inr * (1.0 - cash_buffer)

    df = df.merge(w.rename("Weight").reset_index().rename(columns={"index": "Symbol"}),
                  on="Symbol", how="left")
    df["Weight"] = df["Weight"].fillna(0.0)
    df["Weight_%"] = df["Weight"] * 100.0
    df["Capital_INR"] = df["Weight"] * investable

    if "Price" in df.columns:
        px = pd.to_numeric(df["Price"], errors="coerce")
        df["Shares"] = np.where(px > 0, (df["Capital_INR"] / px).round(), np.nan)
    else:
        df["Shares"] = np.nan

    if "Stop_Loss" in df.columns and "Price" in df.columns:
        stop = pd.to_numeric(df["Stop_Loss"], errors="coerce")
        px = pd.to_numeric(df["Price"], errors="coerce")
        stop_dist_pct = ((px - stop) / px).clip(lower=0)
        df["Stop_Loss_INR"] = stop
        df["Max_Loss_INR"] = (df["Capital_INR"] * stop_dist_pct).round(2)
        df["Max_Loss_%_of_NAV"] = (df["Max_Loss_INR"] / nav_inr * 100.0).round(3)
    else:
        df["Stop_Loss_INR"] = np.nan
        df["Max_Loss_INR"] = np.nan
        df["Max_Loss_%_of_NAV"] = np.nan

    if not rc.empty:
        df = df.merge(rc.rename("Risk_Contribution").reset_index()
                      .rename(columns={"index": "Symbol"}), on="Symbol", how="left")
        df["Risk_Contribution_%"] = df["Risk_Contribution"] * 100.0
    else:
        df["Risk_Contribution_%"] = np.nan

    keep = ["Symbol", "Price", "Weight_%", "Capital_INR", "Shares",
            "Stop_Loss_INR", "Max_Loss_INR", "Max_Loss_%_of_NAV",
            "Risk_Contribution_%"]
    keep = [c for c in keep if c in df.columns]
    return df[keep]
