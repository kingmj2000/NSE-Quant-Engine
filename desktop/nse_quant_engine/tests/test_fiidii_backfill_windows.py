"""FII/DII archive backfill: windowed requests, partial success, convergence.

MEASURED CAVEAT (2026-08-31 probe): NSE returns an 18 KB HTML block page with
HTTP 503 for the historical endpoint at BOTH 3-day and 20-day ranges,
byte-identical. Narrow windows are not currently a workaround — the endpoint is
blocked outright for this client.

The windowing is kept and tested because it is the right shape when access
returns: only missing days are requested, each window retries with a cookie
re-warm, and failure is partial rather than total so windows that succeed fill
their gaps while the rest retry next run.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from core import optional_data_fetchers as odf


class _NoSleep:
    """Stand-in for the module's `time` so backoffs do not slow the suite."""

    @staticmethod
    def sleep(_seconds):
        return None

    @staticmethod
    def time():
        import time as _t
        return _t.time()


class _Resp:
    def __init__(self, code, payload):
        self.status_code = code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    """Records the windows requested and answers per a scripted plan."""

    def __init__(self, plan):
        self.plan = plan
        self.calls: list[tuple[str, str]] = []
        self.warmups = 0

    def get(self, url, timeout=None, headers=None):
        if "api/historical" not in url:
            self.warmups += 1
            return _Resp(200, [])
        params = dict(kv.split("=") for kv in url.split("?", 1)[1].split("&"))
        self.calls.append((params["from"], params["to"]))
        result = self.plan(params["from"], params["to"])
        if isinstance(result, Exception):
            raise result
        return _Resp(200, result)


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
        assert span <= 31, f"window {frm}..{to} is {span} days - too wide"
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

    out = odf._fii_dii_from_nse_archive(FakeSession(plan), days=90)
    assert not out.empty, "later windows succeeded; their rows must survive"


def test_total_failure_still_raises(monkeypatch):
    """Partial tolerance must not swallow a completely dead source."""
    monkeypatch.setattr(odf, "time", _NoSleep())
    with pytest.raises(RuntimeError) as exc:
        odf._fii_dii_from_nse_archive(
            FakeSession(lambda f, t: RuntimeError("HTTP 503")), days=90)
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
    odf._fii_dii_from_nse_archive(sess, days=30)
    assert sess.warmups >= 2, "expected a re-warm after the failed attempt"


def test_each_window_retries_three_times(monkeypatch):
    monkeypatch.setattr(odf, "time", _NoSleep())
    sess = FakeSession(lambda f, t: RuntimeError("HTTP 503"))
    with pytest.raises(RuntimeError):
        odf._fii_dii_archive_window(sess, datetime(2026, 7, 1), datetime(2026, 7, 30))
    assert len(sess.calls) == 3, f"expected 3 attempts, got {len(sess.calls)}"


def test_window_returns_the_normalised_schema(monkeypatch):
    """Raw payload here would break the union with every other source."""
    monkeypatch.setattr(odf, "time", _NoSleep())
    out = odf._fii_dii_archive_window(
        FakeSession(lambda f, t: _rows(f)),
        datetime(2026, 7, 1), datetime(2026, 7, 30))
    assert list(out.columns) == ["Date", "FII_Net_INR_Cr", "DII_Net_INR_Cr"]


def test_windows_are_deduplicated_on_date(monkeypatch):
    monkeypatch.setattr(odf, "time", _NoSleep())
    fixed = _rows("01-07-2026")
    out = odf._fii_dii_from_nse_archive(FakeSession(lambda f, t: fixed), days=90)
    assert len(out) == len(out.drop_duplicates(subset=["Date"]))


# --- gap-driven targeting ---------------------------------------------------

def test_only_missing_ranges_are_requested(tmp_path):
    cache = tmp_path / "fii_dii_daily.csv"
    today = pd.Timestamp.now().normalize()
    span = list(pd.bdate_range(today - pd.Timedelta(days=14), today))
    gone = [span[2], span[5], span[7], span[8]]        # one isolated + one pair
    keep = [d.strftime("%Y-%m-%d") for d in span if d not in gone]
    pd.DataFrame({"Date": keep, "FII_Net_INR_Cr": [1.0] * len(keep),
                  "DII_Net_INR_Cr": [2.0] * len(keep)}).to_csv(cache, index=False)

    ranges = odf.missing_date_ranges(cache, days=14)
    covered = set()
    for a, b in ranges:
        covered |= {d.date() for d in pd.bdate_range(a, b)}

    assert all(g.date() in covered for g in gone), "a missing day was not requested"
    assert any(len(pd.bdate_range(a, b)) == 2 for a, b in ranges), (
        "the contiguous pair must be ONE range, not two requests")


def test_no_gaps_means_no_requests(tmp_path):
    cache = tmp_path / "fii_dii_daily.csv"
    today = pd.Timestamp.now().normalize()
    days = [d.strftime("%Y-%m-%d")
            for d in pd.bdate_range(today - pd.Timedelta(days=9), today)]
    pd.DataFrame({"Date": days, "FII_Net_INR_Cr": [1.0] * len(days)}).to_csv(
        cache, index=False)
    assert odf.missing_date_ranges(cache, days=9) == []


def test_request_count_is_bounded(tmp_path):
    """One run must not fire dozens of requests at a rate-limiting endpoint."""
    cache = tmp_path / "fii_dii_daily.csv"
    today = pd.Timestamp.now().normalize()
    days = [d.strftime("%Y-%m-%d")
            for d in pd.bdate_range(today - pd.Timedelta(days=230), today)][::3]
    pd.DataFrame({"Date": days, "FII_Net_INR_Cr": [1.0] * len(days)}).to_csv(
        cache, index=False)
    assert len(odf.missing_date_ranges(cache, days=240)) <= 6


def test_missing_or_corrupt_cache_yields_no_crash(tmp_path):
    odf.missing_date_ranges(tmp_path / "nope.csv", days=30)
    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"\x00 not csv")
    odf.missing_date_ranges(bad, days=30)
