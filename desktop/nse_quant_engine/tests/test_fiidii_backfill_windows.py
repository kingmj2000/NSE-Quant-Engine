"""FII/DII archive backfill: windowed requests, partial success, convergence.

The backfill asked NSE for the whole ~90-day range in one request. NSE answers a
wide range with HTTP 503 far more often than a narrow one, and because any 503
failed the whole source, the backfill never ran at all. `nse-api` only returns
the latest row or two, so the cache accrued permanent holes — 12 of 30 business
days missing in the observed run.

Windowing makes failure partial rather than total: whatever succeeds fills its
gaps now, whatever fails is retried next run, and the cache converges.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from core import optional_data_fetchers as odf


class FakeSession:
    """Records the windows requested and answers per a scripted plan."""

    def __init__(self, plan):
        self.plan = plan            # callable(from_str, to_str) -> rows | Exception
        self.calls: list[tuple[str, str]] = []
        self.warmups = 0

    def get(self, url, timeout=None, headers=None):
        if "api/historical" not in url:
            self.warmups += 1
            return _Resp(200, [])
        q = url.split("?", 1)[1]
        params = dict(kv.split("=") for kv in q.split("&"))
        self.calls.append((params["from"], params["to"]))
        result = self.plan(params["from"], params["to"])
        if isinstance(result, Exception):
            raise result
        return _Resp(200, result)


class _Resp:
    def __init__(self, code, payload):
        self.status_code = code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _rows(from_str: str) -> list[dict]:
    d = datetime.strptime(from_str, "%d-%m-%Y")
    return [
        {"category": "FII/FPI **", "date": d.strftime("%d-%b-%Y"),
         "buyValue": "100", "sellValue": "40", "netValue": "60"},
        {"category": "DII **", "date": d.strftime("%d-%b-%Y"),
         "buyValue": "80", "sellValue": "30", "netValue": "50"},
    ]


def test_backfill_is_split_into_windows(monkeypatch):
    monkeypatch.setattr(odf, "time", _NoSleep())
    sess = FakeSession(lambda f, t: _rows(f))

    out = odf._fii_dii_from_nse_archive(sess, days=90)

    assert len(sess.calls) >= 3, (
        f"90 days must be requested in windows, not one shot (got {len(sess.calls)})")
    for frm, to in sess.calls:
        span = (datetime.strptime(to, "%d-%m-%Y")
                - datetime.strptime(frm, "%d-%m-%Y")).days
        assert span <= 31, f"window {frm}..{to} is {span} days — too wide"
    assert not out.empty


def test_partial_failure_still_returns_what_worked(monkeypatch):
    """One 503 used to kill the entire backfill."""
    monkeypatch.setattr(odf, "time", _NoSleep())
    state = {"n": 0}

    def plan(f, t):
        state["n"] += 1
        if state["n"] <= 3:          # first window fails all three attempts
            return RuntimeError("HTTP 503")
        return _rows(f)

    sess = FakeSession(plan)
    out = odf._fii_dii_from_nse_archive(sess, days=90)

    assert not out.empty, "later windows succeeded; their rows must survive"


def test_total_failure_still_raises(monkeypatch):
    """Partial tolerance must not swallow a completely dead source."""
    monkeypatch.setattr(odf, "time", _NoSleep())
    sess = FakeSession(lambda f, t: RuntimeError("HTTP 503"))

    with pytest.raises(RuntimeError) as exc:
        odf._fii_dii_from_nse_archive(sess, days=90)
    assert "window" in str(exc.value)


def test_cookies_are_reprimed_between_retries(monkeypatch):
    """A 503 usually means the session was rejected, not that NSE is down."""
    monkeypatch.setattr(odf, "time", _NoSleep())
    state = {"n": 0}

    def plan(f, t):
        state["n"] += 1
        if state["n"] == 1:
            return RuntimeError("HTTP 503")
        return _rows(f)

    sess = FakeSession(plan)
    before = sess.warmups
    odf._fii_dii_from_nse_archive(sess, days=30)
    assert sess.warmups > before + 3, "expected a re-warm after the failed attempt"


def test_windows_are_deduplicated_on_date(monkeypatch):
    monkeypatch.setattr(odf, "time", _NoSleep())
    fixed = _rows("01-07-2026")
    sess = FakeSession(lambda f, t: fixed)

    out = odf._fii_dii_from_nse_archive(sess, days=90)
    assert len(out) == len(out.drop_duplicates(subset=["Date"]))


def test_each_window_retries_three_times(monkeypatch):
    monkeypatch.setattr(odf, "time", _NoSleep())
    sess = FakeSession(lambda f, t: RuntimeError("HTTP 503"))
    with pytest.raises(RuntimeError):
        odf._fii_dii_archive_window(sess, datetime(2026, 7, 1), datetime(2026, 7, 30))
    assert len(sess.calls) == 3, f"expected 3 attempts, got {len(sess.calls)}"


class _NoSleep:
    """Stand-in for the module's `time` so backoffs don't slow the suite."""

    @staticmethod
    def sleep(_seconds):
        return None

    @staticmethod
    def time():
        import time as _t
        return _t.time()
