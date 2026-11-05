"""A daily-flow cache is fresh only if it holds the latest day NSE has published.

FII/DII and bulk deals come from latest-only endpoints, and NSE posts the day's
figures in the evening (the fetcher itself says: run after 19:00 IST). A plain
24-hour mtime window was the freshness test:

- 2026-10-26 17:16 run fetched the 10-23 figures (10-26 not out yet);
- 2026-10-27 16:01 run: file 23h old, "fresh", fetch skipped;
- 10-26 was never fetched, and a latest-only source cannot backfill it.
  (2026-11-05 15:43 skipped the same way; 18:08 happened to pass the 24h mark.)

Rule now: a cache inside the 24h window is skipped only if its latest Date is
at least the latest weekday published by now (today after 19:00 IST, else the
previous weekday). Otherwise the feed is refetched.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import optional_data_fetchers as odf  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))


def _ist(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=IST)


# ---------------------------------------------------- the expected day ----

@pytest.mark.parametrize("now, expected", [
    (_ist(2026, 11, 5, 15, 46), date(2026, 11, 4)),   # Thu afternoon -> Wed
    (_ist(2026, 11, 5, 18, 59), date(2026, 11, 4)),
    (_ist(2026, 11, 5, 19, 0), date(2026, 11, 5)),    # from 19:00 -> today
    (_ist(2026, 11, 7, 10, 0), date(2026, 11, 6)),    # Sat -> Fri
    (_ist(2026, 11, 8, 20, 0), date(2026, 11, 6)),    # Sun evening -> Fri
    (_ist(2026, 11, 9, 10, 0), date(2026, 11, 6)),    # Mon morning -> Fri
    (_ist(2026, 11, 9, 19, 30), date(2026, 11, 9)),   # Mon evening -> Mon
])
def test_expected_flow_day(now, expected):
    assert odf._expected_flow_day(now) == expected


def test_now_ist_is_a_fixed_offset_aware_clock():
    # Fixed +05:30 offset: no tzdata dependency on Windows, and the cutoff does
    # not move with the machine's local timezone.
    n = odf._now_ist()
    assert n.tzinfo is not None
    assert n.utcoffset() == timedelta(hours=5, minutes=30)


# --------------------------------------------------- the content check ----

def _write(path: Path, days, written_at: datetime) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["Date,FII_Net_INR_Cr,DII_Net_INR_Cr"] + [f"{d},1.0,2.0" for d in days]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ts = written_at.timestamp()
    os.utime(path, (ts, ts))
    return path


@pytest.mark.parametrize("days, now, expected", [
    # The 10-27 / 11-05 afternoon case: cache ends a day short.
    (["2026-11-02", "2026-11-03"], _ist(2026, 11, 5, 15, 46), False),
    # Holds Wednesday; Thursday is not out before 19:00.
    (["2026-11-03", "2026-11-04"], _ist(2026, 11, 5, 18, 25), True),
    # Same cache after 19:00: Thursday is due.
    (["2026-11-03", "2026-11-04"], _ist(2026, 11, 5, 19, 30), False),
    # Holds Thursday, next morning.
    (["2026-11-04", "2026-11-05"], _ist(2026, 11, 6, 10, 0), True),
    # Friday's figures cover the whole weekend.
    (["2026-11-06"], _ist(2026, 11, 8, 21, 0), True),
    # Unsorted rows: the max date counts, not the last line.
    (["2026-11-05", "2026-10-28"], _ist(2026, 11, 6, 10, 0), True),
])
def test_cache_has_latest_day(tmp_path, days, now, expected):
    p = _write(tmp_path / "fii_dii_daily.csv", days, now)
    assert odf._flow_cache_has_latest_day(p, now=now) is expected


@pytest.mark.parametrize("text", ["", "Date,FII_Net_INR_Cr\n", "Symbol,X\nA,1\n",
                                  "Date,FII\nnot-a-date,1\n"])
def test_unreadable_or_dateless_cache_is_not_current(tmp_path, text):
    p = tmp_path / "fii_dii_daily.csv"
    p.write_text(text, encoding="utf-8")
    assert odf._flow_cache_has_latest_day(p, now=_ist(2026, 11, 5, 10)) is False
    assert odf._flow_cache_has_latest_day(tmp_path / "absent.csv",
                                          now=_ist(2026, 11, 5, 10)) is False


# --------------------------------------------------- the two fetchers ----

FEEDS = {
    "fii_dii": ("fii_dii_daily.csv", lambda d: odf.fetch_fii_dii(d)),
    "bulk_deals": ("bulk_deals.csv", lambda d: odf.fetch_bulk_deals(d)),
}


class _FetchAttempted(Exception):
    pass


def _freeze_clock(monkeypatch, now: datetime):
    """One frozen clock for both checks: the 24h window and the expected day."""
    monkeypatch.setattr(odf, "_now_ist", lambda: now)
    real_is_fresh = odf._is_fresh

    def _is_fresh_at_now(path, max_age_hours):
        if not Path(path).exists() or Path(path).stat().st_size == 0:
            return real_is_fresh(path, max_age_hours)
        age_h = (now.timestamp() - Path(path).stat().st_mtime) / 3600.0
        return age_h < max_age_hours

    monkeypatch.setattr(odf, "_is_fresh", _is_fresh_at_now)

    def _session(*a, **k):
        raise _FetchAttempted()

    monkeypatch.setattr(odf, "_requests_session", _session)


@pytest.mark.parametrize("feed", sorted(FEEDS))
def test_recent_cache_without_the_due_day_is_refetched(tmp_path, monkeypatch, feed):
    """10-27 replica: written 17:16 the day before, holding the day before that."""
    name, fetch = FEEDS[feed]
    data = tmp_path / "data"
    _write(data / name, ["2026-10-22", "2026-10-23"], _ist(2026, 10, 26, 17, 16))
    _freeze_clock(monkeypatch, _ist(2026, 10, 27, 16, 1))
    assert odf._is_fresh(data / name, odf.FRESH_FLOW_HOURS)      # inside 24h ...
    with pytest.raises(_FetchAttempted):                            # ... yet refetched
        fetch(data)


@pytest.mark.parametrize("feed", sorted(FEEDS))
def test_evening_rerun_refetches_an_afternoon_cache(tmp_path, monkeypatch, feed):
    name, fetch = FEEDS[feed]
    data = tmp_path / "data"
    _write(data / name, ["2026-11-04"], _ist(2026, 11, 5, 18, 8))
    _freeze_clock(monkeypatch, _ist(2026, 11, 5, 19, 30))
    with pytest.raises(_FetchAttempted):
        fetch(data)


@pytest.mark.parametrize("feed", sorted(FEEDS))
def test_late_publication_is_retried_by_the_next_run(tmp_path, monkeypatch, feed):
    """Fetched at 19:05 but NSE had not posted yet: the cache must not be trusted."""
    name, fetch = FEEDS[feed]
    data = tmp_path / "data"
    _write(data / name, ["2026-11-04"], _ist(2026, 11, 5, 19, 5))
    _freeze_clock(monkeypatch, _ist(2026, 11, 5, 21, 0))
    with pytest.raises(_FetchAttempted):
        fetch(data)


@pytest.mark.parametrize("feed", sorted(FEEDS))
def test_cache_holding_the_due_day_is_still_skipped(tmp_path, monkeypatch, feed):
    name, fetch = FEEDS[feed]
    data = tmp_path / "data"
    p = _write(data / name, ["2026-11-04", "2026-11-05"], _ist(2026, 11, 5, 19, 40))
    before = p.read_text(encoding="utf-8")
    _freeze_clock(monkeypatch, _ist(2026, 11, 6, 10, 0))
    assert fetch(data) is True                  # no session opened
    assert p.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("feed", sorted(FEEDS))
def test_same_afternoon_rerun_is_still_skipped(tmp_path, monkeypatch, feed):
    """Before 19:00 nothing newer exists; repeated runs must not hammer NSE."""
    name, fetch = FEEDS[feed]
    data = tmp_path / "data"
    _write(data / name, ["2026-11-04"], _ist(2026, 11, 5, 18, 8))
    _freeze_clock(monkeypatch, _ist(2026, 11, 5, 18, 25))
    assert fetch(data) is True
