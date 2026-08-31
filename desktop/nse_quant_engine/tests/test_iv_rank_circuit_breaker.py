"""iv_rank must stop probing once NSE has clearly blocked the session.

MEASURED COST OF NOT DOING THIS (run log 2026-08-31):

    [fetch][warn] iv_rank (option-chain): RuntimeError: no symbols returned IV
                  (60 misses) — NSE likely blocked the session

Sixty misses is sixty symbols x three attempts with 1+3+7s backoff each, plus
0.6s pacing: roughly 180 rejected requests and ~11 minutes of wall clock, every
run, against the same host (www.nseindia.com) whose historical FII/DII endpoint
is already answering with 503 block pages.

The engine had warm-up, a 3-attempt backoff, a one-shot re-warm and polite
pacing — everything except a reason to stop. Once symbol 8 has failed with zero
hits, symbol 9 carries no new information.

These tests assert BEHAVIOUR (how many symbols were attempted, and that a
partial success still completes the full list), not log phrasing.
"""
from __future__ import annotations

import pandas as pd
import pytest

from core import optional_data_fetchers as odf


@pytest.fixture
def universe(tmp_path, monkeypatch):
    """60 shortlist symbols, no network, no session warm-up."""
    symbols = [f"SYM{i:03d}" for i in range(60)]
    monkeypatch.setattr(odf, "_shortlist_symbols", lambda base, cap=60: symbols[:cap])
    monkeypatch.setattr(odf, "_nse_browser_session", lambda: object())
    monkeypatch.setattr(odf, "_nse_option_chain_warmup", lambda sess: None)
    monkeypatch.setattr(odf.time, "sleep", lambda *_a, **_k: None)
    return symbols


def _run(tmp_path, attempted: list[str]):
    return odf.fetch_iv_rank(tmp_path, tmp_path, cap=60, force=True)


def test_blocked_session_stops_well_before_the_whole_shortlist(universe, tmp_path,
                                                               monkeypatch):
    attempted: list[str] = []

    def always_blocked(sess, symbol):
        attempted.append(symbol)
        raise RuntimeError(f"option-chain {symbol} failed after 3 tries: blocked HTTP 401")

    monkeypatch.setattr(odf, "_iv_rank_from_option_chain", always_blocked)
    _run(tmp_path, attempted)

    assert attempted, "the breaker must still probe — silence is not a diagnosis"
    assert len(attempted) <= 12, (
        f"probed {len(attempted)} symbols against a session NSE had already "
        f"blocked; each one costs ~11s and three rejected requests")


def test_a_working_session_still_fetches_every_symbol(universe, tmp_path,
                                                      monkeypatch):
    """The breaker must never truncate a healthy run."""
    attempted: list[str] = []

    def always_ok(sess, symbol):
        attempted.append(symbol)
        return 24.5

    monkeypatch.setattr(odf, "_iv_rank_from_option_chain", always_ok)
    assert _run(tmp_path, attempted) is True
    assert len(attempted) == 60, "a healthy session must cover the full shortlist"
    out = pd.read_csv(tmp_path / "iv_rank_daily.csv")
    assert len(out) == 60


def test_one_late_hit_keeps_the_run_alive(universe, tmp_path, monkeypatch):
    """The breaker is armed only while hit == 0.

    A session that recovers on the 6th symbol must go on to try all 60 — the
    give-up condition is 'nothing has ever worked', not 'something failed'.
    """
    attempted: list[str] = []

    def recovers(sess, symbol):
        attempted.append(symbol)
        if len(attempted) < 6:
            raise RuntimeError("blocked HTTP 401")
        return 18.0

    monkeypatch.setattr(odf, "_iv_rank_from_option_chain", recovers)
    _run(tmp_path, attempted)
    assert len(attempted) == 60, (
        "one success disarms the breaker; it must not stop a recovering session")


def test_failure_never_wipes_the_existing_cache(universe, tmp_path, monkeypatch):
    target = tmp_path / "iv_rank_daily.csv"
    pd.DataFrame({"Date": ["2026-08-20"], "Symbol": ["SYM000"],
                  "IV": [20.0], "IV_Rank": [50.0]}).to_csv(target, index=False)

    monkeypatch.setattr(odf, "_iv_rank_from_option_chain",
                        lambda sess, symbol: (_ for _ in ()).throw(
                            RuntimeError("blocked HTTP 401")))
    _run(tmp_path, [])

    kept = pd.read_csv(target)
    assert len(kept) == 1 and kept.loc[0, "Symbol"] == "SYM000", (
        "a blocked run must reuse cached IV, never overwrite it")
