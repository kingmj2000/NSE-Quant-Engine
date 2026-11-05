"""Data Health tiles: a fresh file with no data rows is not a healthy feed.

refresh_all() seeds the price / amfi_nav / amfi_ter tiles from the file's mtime.
On 2026-10-29 the TER/TE importer found no source rows and rewrote
data/amfi_ter_tracking_source_standardized.csv as a header-only file. The next
data_health.json (2026-11-05) showed amfi_ter GREEN with rows 0: "the file was
touched recently" recorded as "the feed is healthy".

These tests build the files in tmp_path and assert on the tile JSON that
refresh_all() writes. No network: every optional CSV is seeded fresh, so all
fetchers take the cached path, and _requests_session raises if called.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from core import optional_data_fetchers as odf

TER_FILE = "amfi_ter_tracking_source_standardized.csv"
AUM_FILE = "amfi_aum_source_standardized.csv"
TER_HEADER = ("Scheme_Code,ISIN,Scheme_Name,Benchmark_Index,TER,Tracking_Error,"
              "Tracking_Difference,Source,Symbol\n")


def _fresh(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")
    now = time.time()
    os.utime(p, (now, now))


def _tiles(tmp_path: Path, monkeypatch, files: dict[str, str]) -> dict:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    # Fresh fii_dii cache -> its fetcher is skipped; nothing touches the network.
    _fresh(data / "fii_dii_daily.csv", "Date,FII_Net\n2026-11-03,1.0\n")
    for name, text in files.items():
        _fresh(data / name, text)

    def _no_network(*_a, **_k):
        raise AssertionError("network session opened in an offline test")

    monkeypatch.setattr(odf, "_requests_session", _no_network)
    odf.refresh_all(tmp_path, only=["fii_dii"])
    return json.loads((data / odf.HEALTH_FILE_NAME).read_text(encoding="utf-8"))["feeds"]


def test_header_only_ter_import_is_red_not_green(tmp_path, monkeypatch):
    feeds = _tiles(tmp_path, monkeypatch, {TER_FILE: TER_HEADER})
    tile = feeds["amfi_ter"]
    assert tile["rows"] == 0
    assert tile["status"] == "red", f"0-row TER import shown as {tile['status']}"
    assert "0 data rows" in tile["note"]


def test_populated_fresh_import_stays_green(tmp_path, monkeypatch):
    """The fix must not turn a working feed red."""
    row = "101234,INF204KB14I2,Nippon ETF,Nifty 50,0.04,,0.01,AMFI,NIFTYBEES.NS\n"
    feeds = _tiles(tmp_path, monkeypatch, {TER_FILE: TER_HEADER + row,
                                           AUM_FILE: "Scheme_Code,AUM_Cr\n101234,123.4\n"})
    assert feeds["amfi_ter"]["status"] == "green"
    assert feeds["amfi_ter"]["rows"] == 1
    assert feeds["amfi_nav"]["status"] == "green"
    assert feeds["amfi_nav"]["rows"] == 1


def test_zero_byte_file_reports_zero_rows_not_minus_one(tmp_path, monkeypatch):
    feeds = _tiles(tmp_path, monkeypatch, {AUM_FILE: ""})
    assert feeds["amfi_nav"]["rows"] == 0
    assert feeds["amfi_nav"]["status"] == "red"


def test_price_meta_without_price_rows_is_red(tmp_path, monkeypatch):
    """price_cache_meta.json alone (fresh mtime) used to make the price tile green."""
    feeds = _tiles(tmp_path, monkeypatch, {
        "price_cache_meta.json": "{}",
        "raw_prices_latest.csv": "Date,Symbol,Close\n",
    })
    assert feeds["price"]["rows"] == 0
    assert feeds["price"]["status"] == "red"


@pytest.mark.parametrize("text, expected", [("", 0), ("A,B\n", 0), ("A,B\n1,2\n3,4\n", 2)])
def test_cache_row_count_never_negative(tmp_path, text, expected):
    p = tmp_path / "x.csv"
    p.write_text(text, encoding="utf-8")
    assert odf._cache_row_count(p) == expected
