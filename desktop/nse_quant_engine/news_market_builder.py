"""News & Market Context Builder (context-only).

Guardrails (enforced by tests):
- Never modifies latest_scores.csv, latest_scores_validated.xlsx,
  validation_status.json, trade_plan_latest.xlsx, rank_changes.csv,
  score_history.csv, rebalance_diff.json, or any shadow output.
- News never influences scoring, ranking, adaptive weights, portfolio
  selection, trade levels or rebalance decisions.
- Unknown publication dates stay unknown (never replaced with `now`).
- Uses the SAME finalized official source Trade Plan / Overview use, via
  core.candidate_selection.top_official_candidates.
- Any fetch failure preserves the last-good outputs; refresh_status is
  recorded and a cache fallback is used.
- All file writes are atomic.
"""
from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Iterable
import pandas as pd

from core.candidate_selection import top_official_candidates
from news import SCHEMA_VERSION
from news.news_cache import (
    CACHE_COLUMNS, atomic_write_df, atomic_write_text,
    read_cache, upsert, prune,
)
from news.news_dedup import cluster_key, dedup
from news.news_relevance import (
    build_aliases, classify_event, classify_relevance, load_alias_overrides,
)
from news.sources import google_news_rss, nse_announcements

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

LATEST_SCORES = OUTPUT_DIR / "latest_scores.csv"
RANK_CHANGES = OUTPUT_DIR / "rank_changes.csv"
DAILY_CHANGES = OUTPUT_DIR / "daily_changes.json"
ALIAS_OVERRIDES = DATA_DIR / "news_alias_overrides.csv"
NEWS_CACHE = DATA_DIR / "news_cache.csv"
SOURCE_HEALTH = DATA_DIR / "news_source_health.json"
TOP_CAND_OUT = DATA_DIR / "top_candidate_news.csv"
MARKET_OUT = DATA_DIR / "news_latest.csv"
DIGEST_OUT = OUTPUT_DIR / "news_digest.json"
CONTEXT_MD = OUTPUT_DIR / "news_market_context.md"

MARKET_QUERIES = [
    "Nifty 50 market outlook India",
    "Nifty Next 50 market outlook India",
    "India stock market FII flows RBI inflation crude rupee",
    "RBI policy India stock market",
    "Nasdaq US Fed India market impact",
    "crude oil rupee India equity market",
]

# Caps (explicit constants — enforced by _select_candidates priority)
MAX_CANDIDATES = 30                 # max symbols per run
MAX_QUERIES_PER_SYMBOL = 2          # media queries per symbol
MAX_STORIES_PER_SYMBOL = 15         # retained stories per symbol per run
RANK_GAINER_MIN = 5                 # min rank improvement to qualify a "gainer"
RECENT_WINDOW_DAYS = 30
NSE_WINDOW_DAYS = 14

# Risk-flag values that must NEVER trigger candidate expansion.
NEUTRAL_RISK_FLAGS = {"", "clean", "none", "nan", "n/a", "na", "-", "ok"}

DISCLAIMER = (
    "News and filings are human-review context only. "
    "They do not change any score, rank, validation result, adaptive weight, "
    "trade level, portfolio decision or rebalance output."
)


# ---------- recency ----------
def add_recency(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        for c in ("Published_Date", "Age_Days", "Recency_Bucket"):
            if c not in df.columns:
                df[c] = pd.Series(dtype="object")
        return df
    now = pd.Timestamp.now()
    pub = pd.to_datetime(df.get("Published_Date"), errors="coerce")
    df["Published_Date"] = pub  # keep NaT as NaT — never overwrite with now
    df["Age_Days"] = (now - pub).dt.days
    bins = [-1e9, 7, 30, 90, 1e9]
    labels = ["Recent_0_7D", "Current_8_30D", "Older_31_90D", "Stale_90DPlus"]
    df["Recency_Bucket"] = pd.cut(df["Age_Days"], bins=bins, labels=labels).astype(str)
    df.loc[df["Published_Date"].isna(), "Recency_Bucket"] = "Unknown_Date"
    return df


# ---------- candidate coverage ----------
def select_candidates(latest: pd.DataFrame, rank_changes: pd.DataFrame | None,
                      pins: Iterable[str] | None = None) -> pd.DataFrame:
    """One deduplicated candidate set with deterministic priority.

    Priority (fills the MAX_CANDIDATES cap in this order):
      1. Official ranks 1–15  (top_official_candidates → canonical)
      2. New Top-20 entrants  (Previous_Rank NaN & current Rank <= 20)
      3. Rank gainers         (Rank_Change >= RANK_GAINER_MIN, ranked <= 30)
      4. High-ranked with newly introduced review flags
      5. User pins
    """
    ordered_syms: list[str] = []
    priority_map: dict[str, str] = {}
    seen: set[str] = set()

    def add(sym: str, why: str) -> None:
        sym = str(sym).strip().upper()
        if not sym or sym in seen:
            return
        seen.add(sym); ordered_syms.append(sym); priority_map[sym] = why

    official = top_official_candidates(latest, n=15)
    for _, r in official.iterrows():
        add(r.get("Symbol", ""), "official_top15")

    if rank_changes is not None and not rank_changes.empty:
        rc = rank_changes.copy()
        rc["Rank"] = pd.to_numeric(rc.get("Rank"), errors="coerce")
        rc["Previous_Rank"] = pd.to_numeric(rc.get("Previous_Rank"), errors="coerce")
        rc["Rank_Change"] = pd.to_numeric(rc.get("Rank_Change"), errors="coerce")
        entrants = rc[(rc["Previous_Rank"].isna()) & (rc["Rank"] <= 20)]
        for _, r in entrants.sort_values("Rank").iterrows():
            add(r.get("Symbol", ""), "new_top20_entrant")
        gainers = rc[(rc["Rank_Change"] >= RANK_GAINER_MIN) & (rc["Rank"] <= 30)]
        for _, r in gainers.sort_values("Rank").iterrows():
            add(r.get("Symbol", ""), "rank_gainer")

    # newly flagged high-ranked
    if "Risk_Flag" in latest.columns and "Opportunity_Rank" in latest.columns:
        flagged = latest.copy()
        flagged["Opportunity_Rank"] = pd.to_numeric(flagged["Opportunity_Rank"], errors="coerce")
        flagged = flagged[(flagged["Risk_Flag"].astype(str).str.len() > 0)
                          & (flagged["Opportunity_Rank"] <= 20)]
        for _, r in flagged.sort_values("Opportunity_Rank").iterrows():
            add(r.get("Symbol", ""), "new_risk_flag")

    for p in (pins or []):
        add(p, "user_pin")

    ordered_syms = ordered_syms[:MAX_CANDIDATES]
    latest_lut = latest.set_index(latest["Symbol"].astype(str).str.upper()) if "Symbol" in latest.columns else pd.DataFrame()
    rows = []
    for i, sym in enumerate(ordered_syms):
        row = latest_lut.loc[sym] if sym in latest_lut.index else pd.Series()
        rows.append({
            "Symbol": sym,
            "Name": row.get("Name", ""),
            "Rank": row.get("Opportunity_Rank", row.get("Rank")),
            "Coverage_Reason": priority_map[sym],
            "Priority_Order": i,
        })
    return pd.DataFrame(rows)


# ---------- source health ----------
class SourceHealth:
    def __init__(self) -> None:
        self.entries: dict[str, dict] = {}

    def record(self, source: str, *, status: str, items_received: int = 0,
               items_retained: int = 0, error: str = "",
               cache_fallback: bool = False) -> None:
        now = pd.Timestamp.now().isoformat(timespec="seconds")
        e = self.entries.setdefault(source, {
            "Source": source, "Last_Attempt": None, "Last_Success": None,
            "Fetch_Status": "unknown", "Items_Received": 0, "Items_Retained": 0,
            "Unknown_Date_Count": 0, "Duplicate_Count": 0,
            "Cache_Fallback_Used": False, "Error": "",
        })
        e["Last_Attempt"] = now
        e["Fetch_Status"] = status
        e["Items_Received"] = int(items_received)
        e["Items_Retained"] = int(items_retained)
        e["Error"] = error or ""
        if status == "success":
            e["Last_Success"] = now
        if cache_fallback:
            e["Cache_Fallback_Used"] = True

    def to_list(self) -> list[dict]:
        return list(self.entries.values())


# ---------- building rows ----------
def _row_from_item(item: dict, symbol: str, name: str, rank, reason: str,
                   event: str) -> dict:
    now_iso = pd.Timestamp.now().isoformat(timespec="seconds")
    return {
        "Cluster_Key": "",  # filled after
        "Symbol": symbol,
        "Rank": rank,
        "Name": name,
        "Canonical_Title": item.get("Canonical_Title", ""),
        "Source": item.get("Source", ""),
        "All_Sources": item.get("Source", ""),
        "Source_Type": item.get("Source_Type", "media"),
        "Published_Date": item.get("Published_Date", pd.NaT),
        "Age_Days": pd.NA,
        "Recency_Bucket": "",
        "Event_Category": event,
        "URL": item.get("URL", ""),
        "Is_Official_Filing": bool(item.get("Is_Official_Filing", False)),
        "Relevance_Reason": reason,
        "Duplicate_Count": 1,
        "First_Seen": now_iso,
        "Last_Seen": now_iso,
        "Fetched_At": now_iso,
    }


# ---------- main ----------
def build(pins: Iterable[str] | None = None) -> dict:
    """Run the news refresh. Never raises. Returns the digest envelope."""
    health = SourceHealth()
    refresh_status = "success"
    cache_fallback_used = False
    prev_digest = _safe_read_json(DIGEST_OUT)

    if not LATEST_SCORES.exists():
        return _write_failed(prev_digest, health, "latest_scores_missing")

    try:
        latest = pd.read_csv(LATEST_SCORES)
    except Exception as exc:
        return _write_failed(prev_digest, health, f"latest_scores_read_error: {exc}")

    rank_changes = _safe_read_csv(RANK_CHANGES)
    overrides = load_alias_overrides(ALIAS_OVERRIDES)
    candidates = select_candidates(latest, rank_changes, pins=pins)

    cache = read_cache(NEWS_CACHE)

    # --- NSE announcements: one warmed session, one feed per run ---
    nse_items: list[dict] = []
    session = nse_announcements.warm_session()
    nse_items, nse_health = nse_announcements.fetch_feed(session, window_days=NSE_WINDOW_DAYS)
    health.record("nse_announcements", status=nse_health["fetch_status"],
                  items_received=nse_health["items_received"], error=nse_health.get("error", ""))
    nse_by_sym = nse_announcements.filter_for_symbols(nse_items, candidates["Symbol"].tolist()) if candidates.shape[0] else {}
    if nse_health["fetch_status"] != "success":
        cache_fallback_used = True
        refresh_status = "partial"

    # --- Candidate-specific media via Google News RSS ---
    candidate_rows: list[dict] = []
    gnews_received = 0
    gnews_retained = 0
    gnews_failed = 0
    for _, cand in candidates.iterrows():
        sym = cand["Symbol"]; name = cand.get("Name", "") or ""; rnk = cand.get("Rank")
        aliases = build_aliases(name, sym, overrides)

        # Official filings first (mapped by exchange symbol, not text)
        for it in nse_by_sym.get(sym, [])[:MAX_STORIES_PER_SYMBOL]:
            reason = classify_relevance(it["Canonical_Title"], sym, aliases,
                                        is_official_filing=True,
                                        filing_symbol=it.get("Filing_Symbol"))
            if not reason:
                continue
            candidate_rows.append(_row_from_item(
                it, sym, name, rnk, reason,
                classify_event(it["Canonical_Title"])))

        # Media queries (capped)
        queries = _candidate_queries(name, sym)[:MAX_QUERIES_PER_SYMBOL]
        media_kept_for_sym = 0
        for q in queries:
            items, gh = google_news_rss.fetch(q, recent_days=RECENT_WINDOW_DAYS, limit=10)
            if gh["fetch_status"] != "success":
                gnews_failed += 1
                continue
            gnews_received += gh["items_received"]
            for it in items:
                if media_kept_for_sym >= MAX_STORIES_PER_SYMBOL:
                    break
                reason = classify_relevance(it["Canonical_Title"], sym, aliases, is_official_filing=False)
                if not reason:
                    continue
                candidate_rows.append(_row_from_item(
                    it, sym, name, rnk, reason,
                    classify_event(it["Canonical_Title"])))
                media_kept_for_sym += 1
                gnews_retained += 1

    gnews_status = "success" if gnews_failed == 0 else ("partial" if gnews_retained else "failed")
    health.record("google_news_rss", status=gnews_status,
                  items_received=gnews_received, items_retained=gnews_retained,
                  error=f"{gnews_failed} query failures" if gnews_failed else "")
    if gnews_status == "failed":
        cache_fallback_used = True
        refresh_status = "partial" if refresh_status == "success" else refresh_status

    # --- Market context (never assigned to candidates) ---
    market_rows: list[dict] = []
    for q in MARKET_QUERIES:
        items, gh = google_news_rss.fetch(q, recent_days=RECENT_WINDOW_DAYS, limit=8)
        if gh["fetch_status"] != "success":
            continue
        for it in items:
            market_rows.append({
                "Query": q,
                "Canonical_Title": it["Canonical_Title"],
                "URL": it["URL"],
                "Source": it["Source"],
                "Published_Date": it["Published_Date"],
                "Source_Type": "media",
                "Is_Official_Filing": False,
            })

    cand_df = pd.DataFrame(candidate_rows, columns=CACHE_COLUMNS) if candidate_rows else pd.DataFrame(columns=CACHE_COLUMNS)
    cand_df = add_recency(cand_df)
    if not cand_df.empty:
        cand_df["Cluster_Key"] = cand_df.apply(cluster_key, axis=1)
        cand_df = dedup(cand_df)

    # --- Merge with cache (cache fills gaps if fresh fetch weak) ---
    merged = upsert(cache, cand_df)

    # If everything failed and we have zero fresh rows, fall back to cache
    if cand_df.empty and not cache.empty:
        cache_fallback_used = True
        if refresh_status == "success":
            refresh_status = "cached"
        merged = cache.copy()

    if cand_df.empty and cache.empty and nse_health["fetch_status"] != "success" and gnews_status != "success":
        refresh_status = "failed"

    # --- Market news ---
    market_df = pd.DataFrame(market_rows)
    market_df = add_recency(market_df) if not market_df.empty else market_df

    # --- Sort candidate feed per spec ---
    display = merged.copy() if not merged.empty else pd.DataFrame(columns=CACHE_COLUMNS + ["Age_Days"])
    if not display.empty:
        display = add_recency(display)
        display["_rank"] = pd.to_numeric(display["Rank"], errors="coerce").fillna(1e9)
        display["_filing"] = display["Is_Official_Filing"].astype(bool).astype(int)
        display["_pd"] = pd.to_datetime(display["Published_Date"], errors="coerce")
        display = display.sort_values(
            by=["_rank", "_filing", "_pd", "Symbol"],
            ascending=[True, False, False, True],
            na_position="last",
        ).drop(columns=["_rank", "_filing", "_pd"])

    # --- Atomic writes: only overwrite valid outputs if we have real data ---
    if refresh_status != "failed":
        atomic_write_df(TOP_CAND_OUT, display)
        atomic_write_df(MARKET_OUT, market_df if not market_df.empty else pd.DataFrame(
            columns=["Query", "Canonical_Title", "URL", "Source", "Published_Date",
                     "Source_Type", "Is_Official_Filing", "Age_Days", "Recency_Bucket"]))
        atomic_write_df(NEWS_CACHE, prune(merged))
    else:
        # Do NOT overwrite valid outputs with empties.
        pass

    # --- Digest envelope ---
    digest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "refresh_status": refresh_status,
        "ranking_source": "output/latest_scores.csv (finalized official; via core.candidate_selection.top_official_candidates)",
        "ranking_column": "Confidence_Adjusted_Score",
        "disclaimer": DISCLAIMER,
        "source_health": health.to_list(),
        "cache_fallback_used": cache_fallback_used,
        "candidate_coverage": candidates.to_dict(orient="records") if not candidates.empty else [],
        "counts": {
            "candidates": int(len(candidates)),
            "candidate_stories": int(len(display)),
            "official_filings": int(display["Is_Official_Filing"].sum()) if not display.empty else 0,
            "unknown_date": int((display.get("Recency_Bucket", pd.Series(dtype=str)) == "Unknown_Date").sum()) if not display.empty else 0,
            "market_items": int(len(market_df)),
        },
        "stories": display.head(300).to_dict(orient="records") if not display.empty else [],
        "market_items": market_df.head(60).to_dict(orient="records") if not market_df.empty else [],
    }
    _write_digest(digest, prev_digest)
    _write_source_health(health)
    _write_context_md(digest, display, market_df)
    return digest


def _candidate_queries(name: str, symbol: str) -> list[str]:
    sym = (symbol or "").strip().upper()
    nm = (name or "").strip()
    strict_bits = []
    if sym:
        strict_bits.append(f'"{sym}"')
    if nm:
        strict_bits.append(f'"{nm}"')
    strict = " OR ".join(strict_bits) if strict_bits else sym or nm
    broad = f'{strict} results earnings order acquisition regulatory'
    return [q for q in (strict, broad) if q]


def _safe_read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path) if path.exists() else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _safe_read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:
        return None


def _write_digest(digest: dict, prev_digest: dict | None) -> None:
    # Preserve last-good digest if this run failed
    if digest.get("refresh_status") == "failed" and prev_digest is not None:
        prev_digest = dict(prev_digest)
        prev_digest.setdefault("previous_refresh_status", prev_digest.get("refresh_status"))
        prev_digest["refresh_status"] = "failed"
        prev_digest["last_failed_attempt"] = digest["generated_at"]
        prev_digest["source_health"] = digest["source_health"]
        atomic_write_text(DIGEST_OUT, json.dumps(prev_digest, indent=2, default=str))
        return
    atomic_write_text(DIGEST_OUT, json.dumps(digest, indent=2, default=str))


def _write_source_health(health: SourceHealth) -> None:
    atomic_write_text(SOURCE_HEALTH, json.dumps(health.to_list(), indent=2, default=str))


def _write_failed(prev_digest: dict | None, health: SourceHealth, reason: str) -> dict:
    health.record("pipeline", status="failed", error=reason)
    digest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "refresh_status": "failed",
        "ranking_source": "unavailable",
        "ranking_column": "Confidence_Adjusted_Score",
        "disclaimer": DISCLAIMER,
        "source_health": health.to_list(),
        "cache_fallback_used": True,
        "candidate_coverage": [],
        "counts": {"candidates": 0, "candidate_stories": 0, "official_filings": 0,
                   "unknown_date": 0, "market_items": 0},
        "stories": [], "market_items": [],
        "error": reason,
    }
    _write_digest(digest, prev_digest)
    _write_source_health(health)
    return digest


def _write_context_md(digest: dict, display: pd.DataFrame, market_df: pd.DataFrame) -> None:
    lines: list[str] = []
    lines.append("# News & Market Context Pack")
    lines.append("")
    lines.append(f"_{DISCLAIMER}_")
    lines.append("")
    lines.append(f"- Schema: `{digest['schema_version']}`")
    lines.append(f"- Refresh status: **{digest['refresh_status']}**")
    lines.append(f"- Ranking source: {digest['ranking_source']}")
    lines.append(f"- Ranking column: `{digest['ranking_column']}`")
    lines.append(f"- Generated: {digest['generated_at']}")
    lines.append(f"- Cache fallback used: {digest['cache_fallback_used']}")
    lines.append("")

    lines.append("## Source health")
    for h in digest["source_health"]:
        lines.append(f"- **{h['Source']}** — {h['Fetch_Status']} "
                     f"(received {h['Items_Received']}, retained {h.get('Items_Retained', 0)})"
                     f"{' · error: ' + h['Error'] if h.get('Error') else ''}")
    lines.append("")

    def _section(title: str, subset: pd.DataFrame, cols: list[str]) -> None:
        lines.append(f"## {title}")
        if subset is None or subset.empty:
            lines.append("_No rows._"); lines.append(""); return
        cols = [c for c in cols if c in subset.columns]
        lines.append(subset[cols].head(40).to_markdown(index=False))
        lines.append("")

    if not display.empty:
        filings = display[display["Is_Official_Filing"].astype(bool)]
        recent = display[(display["Recency_Bucket"].isin(["Recent_0_7D", "Current_8_30D"]))
                         & (~display["Is_Official_Filing"].astype(bool))]
        stale = display[display["Recency_Bucket"].isin(["Older_31_90D", "Stale_90DPlus"])]
        unknown = display[display["Recency_Bucket"] == "Unknown_Date"]
    else:
        filings = recent = stale = unknown = pd.DataFrame()

    _section("Recent official filings", filings,
             ["Symbol", "Rank", "Canonical_Title", "Source", "Published_Date", "URL", "Event_Category"])
    _section("Recent candidate-specific media", recent,
             ["Symbol", "Rank", "Canonical_Title", "Source", "Published_Date", "Event_Category", "Relevance_Reason"])
    _section("Market context", market_df,
             ["Query", "Canonical_Title", "Source", "Published_Date", "Recency_Bucket"])
    _section("Stale candidate context", stale,
             ["Symbol", "Rank", "Canonical_Title", "Source", "Published_Date", "Event_Category"])
    _section("Unknown-date items", unknown,
             ["Symbol", "Rank", "Canonical_Title", "Source", "Event_Category", "Relevance_Reason"])

    lines.append("## Candidate coverage")
    if digest["candidate_coverage"]:
        cov = pd.DataFrame(digest["candidate_coverage"])
        lines.append(cov.to_markdown(index=False))
    else:
        lines.append("_No candidates selected for coverage._")
    lines.append("")
    atomic_write_text(CONTEXT_MD, "\n".join(lines))


def main() -> None:
    try:
        digest = build()
        print(f"[news] refresh_status={digest['refresh_status']} "
              f"stories={digest['counts']['candidate_stories']} "
              f"filings={digest['counts']['official_filings']}")
    except Exception:
        # Absolute guarantee: builder never fails the outer pipeline.
        print("[news] non-fatal error:")
        traceback.print_exc()


if __name__ == "__main__":
    main()
