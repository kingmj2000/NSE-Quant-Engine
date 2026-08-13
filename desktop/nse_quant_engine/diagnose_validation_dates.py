"""Explain why the validation date counters read 0 — against YOUR real data.

Read-only. Writes nothing, changes nothing. Run from the engine folder:

    python diagnose_validation_dates.py

It answers, in order:
  1. Does score_history.csv carry Ranking_Schema_Version=2 rows, and from when?
  2. Do those v2 dates have matured forward returns yet?
  3. Where exactly does each date get dropped: schema v1, no score-history match,
     missing Confidence_Adjusted_Score, or fewer than 10 instruments?
  4. What does raw_prices_latest.csv actually cover, in dates and symbols?

Point 3 is the one that matters: "Raw dates 0" is the CORRECT answer while
v2-stamped signal dates are still inside the forward horizon, and a BUG if v2
dates that should have matured are being dropped for another reason.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
OUT = BASE / "output"
DATA = BASE / "data"

MIN_PER_DATE = 10   # assign_buckets requires >= 10 instruments per (date, horizon)
HORIZON = 10


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        print(f"  MISSING: {path}")
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
        print(f"  {path.name}: {len(df):,} rows, {len(df.columns)} cols")
        return df
    except Exception as exc:
        print(f"  UNREADABLE {path.name}: {exc}")
        return pd.DataFrame()


def main() -> None:
    print("=" * 72)
    print("VALIDATION DATE DIAGNOSTIC")
    print("=" * 72)

    print("\n[1] Files")
    scores = _read(OUT / "score_history.csv")
    fwd = _read(OUT / "forward_return_history.csv")
    miss = _read(OUT / "forward_return_missing_signals.csv")
    px = _read(DATA / "raw_prices_latest.csv")

    # ── score history schema ────────────────────────────────────────────────
    print("\n[2] score_history.csv — ranking schema")
    if scores.empty:
        print("  no score history; nothing can validate yet")
        return
    scores["Date"] = pd.to_datetime(scores.get("Date"), errors="coerce")
    if "Ranking_Schema_Version" not in scores.columns:
        print("  !! Ranking_Schema_Version column ABSENT from score_history.csv")
        print("     -> every row reads as schema v1 -> v2 verdict can never have")
        print("        evidence. Expected if the stamp was added recently; the")
        print("        column only appears on rows written after that change.")
        scores["Ranking_Schema_Version"] = 1
    ver = pd.to_numeric(scores["Ranking_Schema_Version"], errors="coerce").fillna(1).astype(int)
    print(f"  rows by schema version: {ver.value_counts().to_dict()}")
    v2_dates = sorted(scores.loc[ver.eq(2), "Date"].dropna().dt.strftime("%Y-%m-%d").unique())
    v1_dates = sorted(scores.loc[ver.ne(2), "Date"].dropna().dt.strftime("%Y-%m-%d").unique())
    print(f"  schema-v1 dates: {len(v1_dates)}"
          + (f"  ({v1_dates[0]} .. {v1_dates[-1]})" if v1_dates else ""))
    print(f"  schema-v2 dates: {len(v2_dates)}"
          + (f"  ({v2_dates[0]} .. {v2_dates[-1]})" if v2_dates else ""))
    if not v2_dates:
        print("  VERDICT: no v2 evidence exists at all. Raw dates 0 is correct.")
    has_cas = "Confidence_Adjusted_Score" in scores.columns
    if has_cas:
        cas_na = scores.loc[ver.eq(2), "Confidence_Adjusted_Score"].isna().sum()
        print(f"  v2 rows missing Confidence_Adjusted_Score: {cas_na:,}")
    else:
        print("  !! Confidence_Adjusted_Score ABSENT — bucketing cannot run")

    # ── forward returns ─────────────────────────────────────────────────────
    print("\n[3] forward_return_history.csv — matured coverage")
    if fwd.empty:
        print("  no matured forward returns yet -> Raw dates 0 is correct")
    else:
        fwd["Signal_Date"] = pd.to_datetime(fwd.get("Signal_Date"), errors="coerce")
        f10 = fwd[pd.to_numeric(fwd.get("Horizon_Days"), errors="coerce") == HORIZON]
        use = f10 if not f10.empty else fwd
        fdates = sorted(use["Signal_Date"].dropna().dt.strftime("%Y-%m-%d").unique())
        print(f"  horizon-{HORIZON} rows: {len(use):,} across {len(fdates)} signal dates")
        if fdates:
            print(f"  date span: {fdates[0]} .. {fdates[-1]}")
        overlap = sorted(set(fdates) & set(v2_dates))
        print(f"  dates that are BOTH matured AND schema-v2: {len(overlap)}")
        if overlap:
            print(f"    {overlap[:10]}{' ...' if len(overlap) > 10 else ''}")
        else:
            print("    NONE -> this is exactly why Raw dates = 0.")
            if v2_dates and fdates:
                print(f"    newest matured signal date: {fdates[-1]}")
                print(f"    oldest schema-v2 date:      {v2_dates[0]}")
                if v2_dates[0] > fdates[-1]:
                    print("    => v2 dates are all NEWER than anything matured:")
                    print("       normal, they are still inside the forward window.")
                else:
                    print("    => v2 dates overlap the matured range but still do not")
                    print("       join. Check Date/Symbol formatting between the two")
                    print("       files (this would be a real bug).")

        # per-date instrument counts
        if overlap and has_cas:
            print(f"\n[4] Per-date instrument counts (need >= {MIN_PER_DATE})")
            s = scores.loc[ver.eq(2), ["Date", "Symbol", "Confidence_Adjusted_Score"]].rename(
                columns={"Date": "Signal_Date"})
            work = use.merge(s, on=["Signal_Date", "Symbol"], how="left")
            if "Opportunity_Eligible" in work.columns:
                work = work[work["Opportunity_Eligible"].astype(str).str.lower().eq("yes")]
            good = work.dropna(subset=["Confidence_Adjusted_Score", "Net_Forward_Return"])
            counts = good.groupby(good["Signal_Date"].dt.strftime("%Y-%m-%d")).size()
            usable = counts[counts >= MIN_PER_DATE]
            print(f"  joined+eligible+scored rows: {len(good):,}")
            print(f"  dates with >= {MIN_PER_DATE} instruments: {len(usable)} "
                  f"(these become Raw dates)")
            thin = counts[counts < MIN_PER_DATE]
            if len(thin):
                print(f"  dates dropped as too thin: {len(thin)} "
                      f"(sizes {sorted(thin.unique())[:8]})")

    # ── missing signals ─────────────────────────────────────────────────────
    print("\n[5] forward_return_missing_signals.csv — why signals have no return")
    if not miss.empty and "Reason" in miss.columns:
        for reason, n in miss["Reason"].value_counts().items():
            print(f"  {n:>8,}  {reason}")
    else:
        print("  none recorded")

    # ── price coverage ──────────────────────────────────────────────────────
    print("\n[6] raw_prices_latest.csv coverage")
    if px.empty:
        print("  missing/unreadable — forward returns cannot be computed at all")
    else:
        dcol = "Date" if "Date" in px.columns else px.columns[0]
        pd_dates = pd.to_datetime(px[dcol], errors="coerce").dropna()
        if not pd_dates.empty:
            print(f"  date span: {pd_dates.min():%Y-%m-%d} .. {pd_dates.max():%Y-%m-%d} "
                  f"({pd_dates.dt.strftime('%Y-%m-%d').nunique()} distinct dates)")
        if "Symbol" in px.columns:
            psyms = set(px["Symbol"].astype(str))
            print(f"  symbols: {len(psyms):,}")
            if not scores.empty and "Symbol" in scores.columns:
                hsyms = set(scores["Symbol"].astype(str))
                gone = hsyms - psyms
                print(f"  symbols in score history but NOT in the price file: {len(gone):,}")
                if gone:
                    print(f"    e.g. {sorted(gone)[:10]}")
                    print("    these are why matured counts fall when the universe changes;")
                    print("    validation_builder now RETAINS their prior forward returns")
                    print("    instead of dropping them (survivorship bias).")

    print("\n" + "=" * 72)
    print("READ THIS: 'Raw dates 0' is correct while schema-v2 signal dates are")
    print("still inside the 10-day forward window. It is a bug only if section [3]")
    print("shows v2 dates inside the matured range that still fail to join.")
    print("=" * 72)


if __name__ == "__main__":
    main()
