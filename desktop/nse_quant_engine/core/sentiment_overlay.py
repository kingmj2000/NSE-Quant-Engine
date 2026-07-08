"""
Sentiment & Macro context overlay (Step 4).

Pure-Python keyword-polarity scorer using `core/data/lexicon_finance.csv`
(no VADER, no NLTK, no new pip deps). Reads whatever news frame the existing
`news_market_builder.py` step wrote, and computes per-symbol 7-day sentiment.

Also exposes a simple macro-tape read from cached benchmark + India VIX (both
optional — module degrades to `None` if the columns are missing).

Every public function is guarded: on any exception it returns an empty frame /
neutral dict so the pipeline is never blocked.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable
import re
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
LEXICON_CSV = BASE / "data" / "lexicon_finance.csv"

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z\-']+")


# ── lexicon ────────────────────────────────────────────────────────────────
def load_lexicon(path: Path = LEXICON_CSV) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except Exception:
        return {}
    if not {"term", "polarity"}.issubset(df.columns):
        return {}
    out: dict[str, float] = {}
    for _, r in df.iterrows():
        t = str(r["term"]).strip().lower()
        try:
            p = float(r["polarity"])
        except Exception:
            continue
        if t:
            out[t] = max(-1.0, min(1.0, p))
    return out


def polarity(text: str, lex: dict[str, float]) -> float:
    """Return a bounded polarity score in [-1, 1]. Empty text → 0."""
    if not text or not lex:
        return 0.0
    tokens = [t.lower() for t in _TOKEN_RE.findall(str(text))]
    if not tokens:
        return 0.0
    hits = [lex[t] for t in tokens if t in lex]
    if not hits:
        return 0.0
    s = sum(hits) / (len(hits) ** 0.5)   # dampen long-text bias
    return max(-1.0, min(1.0, s / 3.0))


# ── per-symbol news aggregation ────────────────────────────────────────────
def score_headlines(news_df: pd.DataFrame,
                    lookback_days: int = 7,
                    lex: dict[str, float] | None = None) -> pd.DataFrame:
    """Aggregate polarity per symbol from a news frame.

    Expected columns (best-effort): Symbol, Date/Published, Headline/Title.
    Missing columns are tolerated; the function returns an empty DataFrame
    with the canonical output schema when nothing usable is present.
    """
    cols = ["Symbol", "Headlines_7D", "PosPct", "NegPct", "Net_Sent"]
    if news_df is None or news_df.empty:
        return pd.DataFrame(columns=cols)

    lex = lex if lex is not None else load_lexicon()
    df = news_df.copy()

    sym_col = next((c for c in ("Symbol", "Ticker", "symbol", "ticker") if c in df.columns), None)
    date_col = next((c for c in ("Date", "Published", "PublishedAt", "published_at", "date") if c in df.columns), None)
    text_col = next((c for c in ("Headline", "Title", "Summary", "headline", "title") if c in df.columns), None)
    if not (sym_col and text_col):
        return pd.DataFrame(columns=cols)

    if date_col:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=lookback_days)
        df = df[df[date_col].isna() | (df[date_col] >= cutoff)]

    df = df.dropna(subset=[sym_col, text_col])
    if df.empty:
        return pd.DataFrame(columns=cols)

    df["_pol"] = df[text_col].astype(str).map(lambda t: polarity(t, lex))
    out = df.groupby(sym_col).agg(
        Headlines_7D=("_pol", "size"),
        PosPct=("_pol", lambda s: float((s > 0.05).mean())),
        NegPct=("_pol", lambda s: float((s < -0.05).mean())),
        Net_Sent=("_pol", "mean"),
    ).reset_index().rename(columns={sym_col: "Symbol"})
    return out[cols]


def sentiment_veto(sent_df: pd.DataFrame,
                   min_headlines: int = 3,
                   neg_pct_veto: float = 0.60) -> set[str]:
    """Return the set of symbols to remove from the top-5 because coverage is
    dominated by negative recent news."""
    if sent_df is None or sent_df.empty:
        return set()
    mask = (sent_df["Headlines_7D"] >= min_headlines) & (sent_df["NegPct"] >= neg_pct_veto)
    return set(sent_df.loc[mask, "Symbol"].astype(str).tolist())


# ── macro-tape ─────────────────────────────────────────────────────────────
def macro_tape_score(prices_long: pd.DataFrame,
                     benchmark: str = "^NSEI",
                     vix: str = "^INDIAVIX") -> dict:
    """Read benchmark + India VIX from the cached prices frame. Return a
    lightweight regime record; missing series → neutral defaults."""
    rec = {"regime": "neutral", "vix_level": None, "vix_pctile_252d": None,
           "nifty_50d_trend": None, "nifty_above_50dma": None}
    if prices_long is None or prices_long.empty:
        return rec
    df = prices_long.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    b = df[df["Symbol"] == benchmark].sort_values("Date")
    if not b.empty:
        c = pd.to_numeric(b["Close"], errors="coerce").dropna()
        if len(c) >= 55:
            ma50 = float(c.tail(50).mean())
            last = float(c.iloc[-1])
            rec["nifty_above_50dma"] = bool(last >= ma50)
            trend = (last / c.iloc[-50] - 1.0) * 100.0
            rec["nifty_50d_trend"] = round(float(trend), 2)

    v = df[df["Symbol"] == vix].sort_values("Date")
    if not v.empty:
        vc = pd.to_numeric(v["Close"], errors="coerce").dropna()
        if not vc.empty:
            rec["vix_level"] = round(float(vc.iloc[-1]), 2)
            if len(vc) >= 60:
                tail = vc.tail(252)
                pct = (tail <= vc.iloc[-1]).mean() * 100.0
                rec["vix_pctile_252d"] = round(float(pct), 1)

    # regime classification
    trend = rec["nifty_50d_trend"] or 0.0
    vix_pct = rec["vix_pctile_252d"]
    if vix_pct is not None:
        if trend > 2.0 and vix_pct < 40:
            rec["regime"] = "risk-on"
        elif trend < -3.0 or vix_pct > 70:
            rec["regime"] = "risk-off"
    return rec


def sector_rotation_table(sector_returns: pd.DataFrame,
                          bench_return_21d: float | None = None) -> pd.DataFrame:
    """Rank sectors by 21D return (vs benchmark if provided)."""
    cols = ["Sector", "Return_21D_%", "vs_Bench_%"]
    if sector_returns is None or sector_returns.empty:
        return pd.DataFrame(columns=cols)
    df = sector_returns.copy()
    ret_col = next((c for c in ("Return_21D", "Ret_21D", "R21", "ret_21d") if c in df.columns), None)
    sec_col = next((c for c in ("Sector", "sector", "Industry") if c in df.columns), None)
    if not (ret_col and sec_col):
        return pd.DataFrame(columns=cols)
    df["Return_21D_%"] = pd.to_numeric(df[ret_col], errors="coerce") * 100.0
    if bench_return_21d is not None:
        df["vs_Bench_%"] = df["Return_21D_%"] - (bench_return_21d * 100.0)
    else:
        df["vs_Bench_%"] = np.nan
    df = df.rename(columns={sec_col: "Sector"})
    return df[cols].sort_values("Return_21D_%", ascending=False).reset_index(drop=True)
