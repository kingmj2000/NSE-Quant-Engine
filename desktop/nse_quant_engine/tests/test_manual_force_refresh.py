"""Manual refresh must override cache freshness; the pipeline must not.

`refresh_all()` skips any feed whose CSV is still inside its freshness window.
That is right for the scheduled pipeline — repeated runs should not hammer public
endpoints for data already on disk. It is wrong for the "Refresh optional feeds
now" button: pressing that means the person wants current data regardless of when
the last run happened, so honouring the cache ignores the instruction.

These tests pin both halves of that contract, and pin that the two UI buttons pass
force=True while the orchestrator step does not.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from core import optional_data_fetchers as odf

FEEDS = ["fii_dii", "bulk_deals", "fundamentals", "earnings", "delivery_pct", "iv_rank"]

TARGETS = {
    "fii_dii": "fii_dii_daily.csv",
    "bulk_deals": "bulk_deals.csv",
    "fundamentals": "fundamentals_latest.csv",
    "earnings": "earnings_calendar.csv",
    "delivery_pct": "delivery_pct_daily.csv",
    "iv_rank": "iv_rank_daily.csv",
}


def _seed_fresh_caches(base: Path) -> Path:
    """Create every optional CSV with a just-now mtime, i.e. maximally fresh."""
    data = base / "data"
    data.mkdir(parents=True, exist_ok=True)
    for name in TARGETS.values():
        p = data / name
        p.write_text("Date,Symbol\n2026-08-17,AAA\n", encoding="utf-8")
        now = time.time()
        import os
        os.utime(p, (now, now))
    return data


def test_is_fresh_reports_a_just_written_file_as_fresh(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a\n", encoding="utf-8")
    assert odf._is_fresh(p, 24) is True
    assert odf._is_fresh(tmp_path / "missing.csv", 24) is False


@pytest.mark.parametrize("feed", FEEDS)
def test_fresh_cache_is_skipped_without_force(tmp_path, monkeypatch, feed):
    """Default behaviour: a fresh cache means no network call at all."""
    _seed_fresh_caches(tmp_path)

    def _boom(*a, **k):
        raise AssertionError("network session opened despite a fresh cache")

    monkeypatch.setattr(odf, "_requests_session", _boom)
    status = odf.refresh_all(tmp_path, only=[feed])
    assert status.get(feed) is True


@pytest.mark.parametrize("feed", FEEDS)
def test_force_bypasses_a_fresh_cache(tmp_path, monkeypatch, feed):
    """force=True must attempt a real fetch even when the cache is brand new."""
    _seed_fresh_caches(tmp_path)
    attempted: list[str] = []

    def _tracking_session(*a, **k):
        attempted.append("session")
        raise RuntimeError("no network in test")

    monkeypatch.setattr(odf, "_requests_session", _tracking_session)
    # Fundamentals/earnings/iv_rank go through yfinance or a shortlist rather than
    # a requests session, so also track the shortlist lookup they depend on.
    shortlisted: list[str] = []
    if hasattr(odf, "_shortlist_symbols"):
        real = odf._shortlist_symbols

        def _tracking_shortlist(*a, **k):
            shortlisted.append("shortlist")
            return real(*a, **k)

        monkeypatch.setattr(odf, "_shortlist_symbols", _tracking_shortlist)

    odf.refresh_all(tmp_path, only=[feed], force=True)

    assert attempted or shortlisted, (
        f"{feed}: force=True did not get past the freshness short-circuit"
    )


def test_force_does_not_delete_cached_data_when_the_fetch_fails(tmp_path, monkeypatch):
    """A forced refresh that cannot reach the network must not wipe the cache."""
    data = _seed_fresh_caches(tmp_path)
    before = {n: (data / n).read_text(encoding="utf-8") for n in TARGETS.values()}

    monkeypatch.setattr(odf, "_requests_session",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    odf.refresh_all(tmp_path, force=True)

    for name, text in before.items():
        assert (data / name).exists(), f"{name} was deleted by a failed forced refresh"
        assert (data / name).read_text(encoding="utf-8") == text


def test_every_fetcher_accepts_force():
    import inspect

    for fn_name in ("fetch_fii_dii", "fetch_bulk_deals", "fetch_fundamentals",
                    "fetch_earnings_calendar", "fetch_delivery_pct", "fetch_iv_rank"):
        fn = getattr(odf, fn_name)
        assert "force" in inspect.signature(fn).parameters, fn_name


def test_manual_buttons_force_and_pipeline_does_not():
    """The distinction is the whole point — pin it against future edits."""
    eng = Path(__file__).resolve().parents[1]

    for rel in ("ui/decision_center.py", "run_app.py"):
        src = (eng / rel).read_text(encoding="utf-8")
        assert "force=True" in src, f"{rel}: manual refresh no longer forces"

    # Inspect the actual call expression via AST. A text search would also match
    # the word inside a docstring or comment explaining the behaviour, which is
    # exactly the false positive that makes grep-based invariants brittle.
    import ast

    orch = ast.parse((eng / "orchestrator.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(orch)
              if isinstance(n, ast.FunctionDef) and n.name == "_run_optional_feeds")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "refresh_all"]
    assert calls, "orchestrator no longer calls refresh_all"
    for call in calls:
        forced = [k for k in call.keywords
                  if k.arg == "force" and getattr(k.value, "value", False) is True]
        assert not forced, (
            "the scheduled pipeline step must stay cache-aware, otherwise every "
            "full run re-fetches every feed"
        )
