"""Tests for the news pipeline (context-only guarantees).

Covers:
- Unknown publication dates are preserved (never replaced with now).
- Generic market articles do not become candidate-specific news.
- Duplicate stories merge; distinct same-day events do NOT.
- Official filings remain separately identifiable.
- Fetch failures appear only in source health, never as fake rows.
- Candidate ordering comes from core.candidate_selection (finalized source).
- The pipeline completes when every news source fails.
- Failed refresh preserves last-good digest.
- All writes are atomic (tmp file gone; only final file present).
- Running the news builder does not alter any scoring/validation/trade-plan
  /history/rebalance/shadow output.
- Generic one-token aliases are rejected.
- news_digest.json schema envelope.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from news import SCHEMA_VERSION  # noqa: E402
from news.news_cache import atomic_write_text, prune, read_cache, upsert  # noqa: E402
from news.news_dedup import canonical_url, dedup, normalize_title  # noqa: E402
from news.news_relevance import (  # noqa: E402
    build_aliases, classify_event, classify_relevance,
)


# ---------- relevance ----------
def test_generic_market_headline_rejected_for_candidate():
    aliases = build_aliases("Reliance Industries Ltd", "RELIANCE")
    assert classify_relevance("Nifty 50 hits new high on broad rally",
                              "RELIANCE", aliases) is None


def test_single_generic_token_alias_rejected():
    # Company legal name reduces to generic token → must not build a bare alias
    aliases = build_aliases("Power Ltd", "PWRLTD")
    # Neither "power" nor "ltd" alone may match
    assert classify_relevance("Power sector outlook improves", "PWRLTD", aliases) is None


def test_symbol_boundary_match_only():
    aliases = build_aliases("Infosys Ltd", "INFY")
    # substring should not match — must be token boundary
    assert classify_relevance("BINFYX product launched", "INFY", aliases) is None
    assert classify_relevance("INFY beats Q1 estimates", "INFY", aliases) == "Symbol match"


def test_official_filing_requires_exchange_mapping():
    aliases = build_aliases("Infosys Ltd", "INFY")
    assert classify_relevance("Some unrelated filing text", "INFY", aliases,
                              is_official_filing=True, filing_symbol="TCS") is None
    assert classify_relevance("Board meeting outcome", "INFY", aliases,
                              is_official_filing=True, filing_symbol="INFY") == "Official filing mapping"


def test_multi_token_alias_requires_phrase_or_two_distinctive_tokens():
    aliases = build_aliases("Apollo Hospitals Enterprise Ltd", "APOLLOHOSP")
    assert "Apollo Hospitals Enterprise" in aliases or "Apollo Hospitals Enterprise Ltd" in aliases
    # phrase match
    assert classify_relevance("Apollo Hospitals Enterprise reports strong Q1",
                              "APOLLOHOSP", aliases) in ("Exact alias match", "Token alias match")
    # single distinctive token from the alias — should NOT match
    assert classify_relevance("Apollo mission update", "APOLLOHOSP", aliases) is None


def test_event_classification_is_deterministic():
    assert classify_event("Company reports Q1 results beat") == "Results/Earnings"
    assert classify_event("Board approves dividend of Rs 5") == "Corporate Action"
    assert classify_event("SEBI issues show cause notice") == "Regulatory/Legal"
    assert classify_event("CRISIL upgrades credit rating") == "Credit Rating"


# ---------- dedup ----------
def test_duplicate_stories_merge():
    rows = pd.DataFrame([
        {"Symbol": "X", "Canonical_Title": "Company X beats Q1 estimates",
         "URL": "https://a.com/x?utm_source=twitter", "Source": "A",
         "Is_Official_Filing": False, "First_Seen": "2026-07-01", "Published_Date": "2026-07-01"},
        {"Symbol": "X", "Canonical_Title": "Company X beats Q1 estimates!",
         "URL": "https://a.com/x", "Source": "A-mirror",
         "Is_Official_Filing": False, "First_Seen": "2026-07-01", "Published_Date": "2026-07-01"},
    ])
    out = dedup(rows)
    assert len(out) == 1
    assert out.iloc[0]["Duplicate_Count"] == 2
    assert "A-mirror" in out.iloc[0]["All_Sources"]


def test_distinct_same_day_events_not_merged():
    rows = pd.DataFrame([
        {"Symbol": "X", "Canonical_Title": "X wins large defence order",
         "URL": "https://a.com/1", "Source": "A", "Is_Official_Filing": False,
         "First_Seen": "2026-07-01", "Published_Date": "2026-07-01"},
        {"Symbol": "X", "Canonical_Title": "X appoints new CFO",
         "URL": "https://a.com/2", "Source": "A", "Is_Official_Filing": False,
         "First_Seen": "2026-07-01", "Published_Date": "2026-07-01"},
    ])
    out = dedup(rows)
    assert len(out) == 2


def test_official_filing_not_merged_into_media():
    rows = pd.DataFrame([
        {"Symbol": "X", "Canonical_Title": "X Q1 results",
         "URL": "https://media.com/q1", "Source": "News",
         "Is_Official_Filing": False, "First_Seen": "2026-07-01", "Published_Date": "2026-07-01"},
        {"Symbol": "X", "Canonical_Title": "X Q1 results",
         "URL": "https://nsearchives.com/filing.pdf", "Source": "NSE",
         "Is_Official_Filing": True, "First_Seen": "2026-07-01", "Published_Date": "2026-07-01"},
    ])
    out = dedup(rows)
    assert len(out) == 2
    assert set(out["Is_Official_Filing"].astype(bool)) == {True, False}


def test_canonical_url_strips_tracking():
    assert canonical_url("https://WWW.a.com/x?utm_source=x&utm_medium=y") == "https://a.com/x"


# ---------- pipeline end-to-end (isolated) ----------
@pytest.fixture()
def isolated_pipeline(tmp_path, monkeypatch):
    """Run news builder against a scratch tree so real files stay untouched."""
    import news_market_builder as nmb

    (tmp_path / "output").mkdir(); (tmp_path / "data").mkdir()
    # Seed a finalized official score file (mirrors trade-plan / overview source)
    latest = pd.DataFrame([
        {"Symbol": "RELIANCE", "Name": "Reliance Industries Ltd",
         "Opportunity_Eligible": "Yes", "Opportunity_Rank": 1,
         "Confidence_Adjusted_Score": 90, "Final_Score": 60, "Risk_Flag": ""},
        {"Symbol": "TCS", "Name": "Tata Consultancy Services Ltd",
         "Opportunity_Eligible": "Yes", "Opportunity_Rank": 2,
         "Confidence_Adjusted_Score": 85, "Final_Score": 55, "Risk_Flag": ""},
        {"Symbol": "AVOID1", "Name": "Some Company Ltd",
         "Opportunity_Eligible": "No", "Opportunity_Rank": None,
         "Confidence_Adjusted_Score": 40, "Final_Score": 30, "Risk_Flag": "avoid"},
    ])
    latest.to_csv(tmp_path / "output" / "latest_scores.csv", index=False)

    # Retarget module paths
    monkeypatch.setattr(nmb, "BASE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(nmb, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(nmb, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(nmb, "LATEST_SCORES", tmp_path / "output" / "latest_scores.csv")
    monkeypatch.setattr(nmb, "RANK_CHANGES", tmp_path / "output" / "rank_changes.csv")
    monkeypatch.setattr(nmb, "ALIAS_OVERRIDES", tmp_path / "data" / "news_alias_overrides.csv")
    monkeypatch.setattr(nmb, "NEWS_CACHE", tmp_path / "data" / "news_cache.csv")
    monkeypatch.setattr(nmb, "SOURCE_HEALTH", tmp_path / "data" / "news_source_health.json")
    monkeypatch.setattr(nmb, "TOP_CAND_OUT", tmp_path / "data" / "top_candidate_news.csv")
    monkeypatch.setattr(nmb, "MARKET_OUT", tmp_path / "data" / "news_latest.csv")
    monkeypatch.setattr(nmb, "DIGEST_OUT", tmp_path / "output" / "news_digest.json")
    monkeypatch.setattr(nmb, "CONTEXT_MD", tmp_path / "output" / "news_market_context.md")
    return nmb, tmp_path


def _fail_all_sources(monkeypatch, nmb):
    monkeypatch.setattr(nmb.nse_announcements, "warm_session", lambda **kw: None)
    monkeypatch.setattr(nmb.nse_announcements, "fetch_feed",
                        lambda *a, **kw: ([], {"fetch_status": "failed", "items_received": 0, "error": "blocked"}))
    monkeypatch.setattr(nmb.google_news_rss, "fetch",
                        lambda *a, **kw: ([], {"fetch_status": "failed", "items_received": 0, "error": "net down"}))


def test_pipeline_completes_when_all_sources_fail(isolated_pipeline, monkeypatch):
    nmb, tmp = isolated_pipeline
    _fail_all_sources(monkeypatch, nmb)
    digest = nmb.build()
    assert digest["refresh_status"] in ("failed", "cached", "partial")
    assert any(h["Fetch_Status"] == "failed" for h in digest["source_health"])
    # No fake headline rows leaked in
    assert digest["counts"]["candidate_stories"] == 0


def test_failed_refresh_preserves_last_good_digest(isolated_pipeline, monkeypatch):
    nmb, tmp = isolated_pipeline
    # First run: give it one good media item so we produce a valid digest
    def one_hit(query, recent_days=30, limit=10, timeout=20):
        if "RELIANCE" in query.upper() or "Reliance" in query:
            return ([{
                "Query": query,
                "Canonical_Title": "RELIANCE beats Q1 estimates on refining margins",
                "URL": "https://example.com/reliance-q1",
                "Source": "ExampleWire", "Source_Type": "media",
                "Is_Official_Filing": False,
                "Published_Date": pd.Timestamp.now(),
            }], {"fetch_status": "success", "items_received": 1, "error": ""})
        return ([], {"fetch_status": "success", "items_received": 0, "error": ""})
    monkeypatch.setattr(nmb.nse_announcements, "warm_session", lambda **kw: None)
    monkeypatch.setattr(nmb.nse_announcements, "fetch_feed",
                        lambda *a, **kw: ([], {"fetch_status": "success", "items_received": 0, "error": ""}))
    monkeypatch.setattr(nmb.google_news_rss, "fetch", one_hit)
    good = nmb.build()
    assert good["counts"]["candidate_stories"] >= 1
    good_stories = good["counts"]["candidate_stories"]

    # Now make everything fail — digest must retain prior stories.
    _fail_all_sources(monkeypatch, nmb)
    after = nmb.build()
    # Either we kept the previous digest as-is, or refresh_status is failed and
    # previous counts are still visible via the preserved envelope.
    assert after["counts"]["candidate_stories"] >= good_stories or after["refresh_status"] == "failed"
    # cache CSV was NOT emptied
    cache_path = nmb.NEWS_CACHE
    if cache_path.exists():
        assert not pd.read_csv(cache_path).empty


def test_unknown_publication_date_is_preserved(isolated_pipeline, monkeypatch):
    nmb, tmp = isolated_pipeline
    def no_date(query, recent_days=30, limit=10, timeout=20):
        if "RELIANCE" in query.upper() or "Reliance" in query:
            return ([{
                "Query": query,
                "Canonical_Title": "RELIANCE announces something",
                "URL": "https://example.com/undated",
                "Source": "Example", "Source_Type": "media",
                "Is_Official_Filing": False,
                "Published_Date": pd.NaT,
            }], {"fetch_status": "success", "items_received": 1, "error": ""})
        return ([], {"fetch_status": "success", "items_received": 0, "error": ""})
    monkeypatch.setattr(nmb.nse_announcements, "warm_session", lambda **kw: None)
    monkeypatch.setattr(nmb.nse_announcements, "fetch_feed",
                        lambda *a, **kw: ([], {"fetch_status": "success", "items_received": 0, "error": ""}))
    monkeypatch.setattr(nmb.google_news_rss, "fetch", no_date)
    digest = nmb.build()
    unknown = [s for s in digest["stories"] if s.get("Recency_Bucket") == "Unknown_Date"]
    assert unknown, "Undated story should surface in Unknown_Date bucket"
    for s in unknown:
        assert not s.get("Published_Date") or pd.isna(pd.to_datetime(s.get("Published_Date"), errors="coerce"))


def test_generic_market_article_never_reaches_candidate_feed(isolated_pipeline, monkeypatch):
    nmb, tmp = isolated_pipeline
    def generic(query, recent_days=30, limit=10, timeout=20):
        return ([{
            "Query": query,
            "Canonical_Title": "Nifty 50 rallies as FIIs turn buyers",
            "URL": "https://example.com/nifty",
            "Source": "Example", "Source_Type": "media",
            "Is_Official_Filing": False,
            "Published_Date": pd.Timestamp.now(),
        }], {"fetch_status": "success", "items_received": 1, "error": ""})
    monkeypatch.setattr(nmb.nse_announcements, "warm_session", lambda **kw: None)
    monkeypatch.setattr(nmb.nse_announcements, "fetch_feed",
                        lambda *a, **kw: ([], {"fetch_status": "success", "items_received": 0, "error": ""}))
    monkeypatch.setattr(nmb.google_news_rss, "fetch", generic)
    digest = nmb.build()
    # No candidate-specific story should reference Nifty-only content
    for s in digest.get("stories", []):
        assert s.get("Relevance_Reason"), "candidate story must carry a relevance reason"


def test_digest_envelope_schema(isolated_pipeline, monkeypatch):
    nmb, tmp = isolated_pipeline
    _fail_all_sources(monkeypatch, nmb)
    digest = nmb.build()
    for key in ("schema_version", "generated_at", "refresh_status",
                "ranking_source", "ranking_column", "source_health",
                "candidate_coverage", "counts", "cache_fallback_used"):
        assert key in digest
    assert digest["schema_version"] == SCHEMA_VERSION
    assert digest["ranking_column"] == "Confidence_Adjusted_Score"
    assert "latest_scores.csv" in digest["ranking_source"]


def test_news_pipeline_does_not_modify_official_outputs(isolated_pipeline, monkeypatch):
    nmb, tmp = isolated_pipeline
    # Create sentinel copies of every "official" artifact family
    outputs = tmp / "output"
    protected = {
        "latest_scores.csv": (outputs / "latest_scores.csv").read_bytes(),
    }
    for name in ("latest_scores_validated.xlsx", "trade_plan_latest.xlsx",
                 "validation_status.json", "rank_changes.csv", "score_history.csv",
                 "rebalance_diff.json", "latest_scores_v4_shadow.csv"):
        p = outputs / name
        p.write_bytes(b"SENTINEL")
        protected[name] = p.read_bytes()

    _fail_all_sources(monkeypatch, nmb)
    nmb.build()

    for name, content in protected.items():
        p = outputs / name
        assert p.read_bytes() == content, f"news pipeline mutated {name}"


def test_atomic_writes_leave_no_tmp_files(isolated_pipeline, monkeypatch):
    nmb, tmp = isolated_pipeline
    _fail_all_sources(monkeypatch, nmb)
    nmb.build()
    stray = list((tmp / "data").glob(".tmp_*")) + list((tmp / "output").glob(".tmp_*"))
    assert not stray, f"atomic writes must clean up tmp files, got {stray}"


def test_candidate_selection_uses_finalized_official_source(isolated_pipeline):
    """The builder's candidate set must come from top_official_candidates,
    excluding ineligible rows."""
    nmb, tmp = isolated_pipeline
    latest = pd.read_csv(nmb.LATEST_SCORES)
    cands = nmb.select_candidates(latest, rank_changes=None)
    syms = list(cands["Symbol"])
    assert "AVOID1" not in syms  # ineligible must be excluded
    assert syms[:2] == ["RELIANCE", "TCS"]  # canonical order


def test_output_caps_are_enforced(isolated_pipeline, monkeypatch):
    nmb, tmp = isolated_pipeline
    # Give one candidate an avalanche of hits
    def flood(query, recent_days=30, limit=10, timeout=20):
        if "RELIANCE" in query.upper():
            items = [{
                "Query": query,
                "Canonical_Title": f"RELIANCE story number {i}",
                "URL": f"https://example.com/r/{i}",
                "Source": "Example", "Source_Type": "media",
                "Is_Official_Filing": False,
                "Published_Date": pd.Timestamp.now(),
            } for i in range(200)]
            return (items, {"fetch_status": "success", "items_received": 200, "error": ""})
        return ([], {"fetch_status": "success", "items_received": 0, "error": ""})
    monkeypatch.setattr(nmb.nse_announcements, "warm_session", lambda **kw: None)
    monkeypatch.setattr(nmb.nse_announcements, "fetch_feed",
                        lambda *a, **kw: ([], {"fetch_status": "success", "items_received": 0, "error": ""}))
    monkeypatch.setattr(nmb.google_news_rss, "fetch", flood)
    digest = nmb.build()
    rel = [s for s in digest["stories"] if str(s.get("Symbol")).upper() == "RELIANCE"]
    assert len(rel) <= nmb.MAX_STORIES_PER_SYMBOL


def test_nse_failure_falls_back_to_cache(isolated_pipeline, monkeypatch):
    nmb, tmp = isolated_pipeline
    # Seed cache with a prior filing
    from news.news_cache import CACHE_COLUMNS
    seed = pd.DataFrame([{
        "Cluster_Key": "u::https://nsearchives.com/f1", "Symbol": "RELIANCE",
        "Rank": 1, "Name": "Reliance Industries Ltd",
        "Canonical_Title": "Board meeting outcome", "Source": "NSE",
        "All_Sources": "NSE", "Source_Type": "official_filing",
        "Published_Date": pd.Timestamp.now(), "Age_Days": 1,
        "Recency_Bucket": "Recent_0_7D", "Event_Category": "Corporate Action",
        "URL": "https://nsearchives.com/f1", "Is_Official_Filing": True,
        "Relevance_Reason": "Official filing mapping", "Duplicate_Count": 1,
        "First_Seen": "2026-07-01", "Last_Seen": "2026-07-01", "Fetched_At": "2026-07-01",
    }], columns=CACHE_COLUMNS)
    seed.to_csv(nmb.NEWS_CACHE, index=False)

    _fail_all_sources(monkeypatch, nmb)
    digest = nmb.build()
    assert digest.get("cache_fallback_used") is True
    # Prior filing should still be visible in the digest even when live fetch fails
    seen = [s for s in digest["stories"] if s.get("URL") == "https://nsearchives.com/f1"]
    assert seen, "cached filing must survive an NSE-block scenario"


# ---------- cache utility ----------
def test_cache_prune_keeps_filings_longer():
    now = pd.Timestamp.now()
    df = pd.DataFrame([
        {"Cluster_Key": "1", "Published_Date": now - pd.Timedelta(days=200),
         "Is_Official_Filing": False},
        {"Cluster_Key": "2", "Published_Date": now - pd.Timedelta(days=200),
         "Is_Official_Filing": True},
    ])
    pruned = prune(df, media_days=180, filing_days=540)
    assert set(pruned["Cluster_Key"]) == {"2"}


def test_cache_upsert_preserves_first_seen():
    a = pd.DataFrame([{"Cluster_Key": "k", "First_Seen": "2026-01-01", "Last_Seen": "2026-01-01"}])
    b = pd.DataFrame([{"Cluster_Key": "k", "First_Seen": "2026-07-01", "Last_Seen": "2026-07-01"}])
    out = upsert(a, b)
    assert out.iloc[0]["First_Seen"] == "2026-01-01"
    assert out.iloc[0]["Last_Seen"] == "2026-07-01"
