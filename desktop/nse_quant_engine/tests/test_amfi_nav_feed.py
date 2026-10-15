"""AMFI NAVAll: the feed behind the whole Data Health page.

Measured 2026-10-15: both AMFI URLs returned HTTP 200 and all three private
parsers produced 0 rows; amfi_navall_latest.csv had been 2 bytes since
2026-08-20; every ETF had NAV blank / Mapping_Status Missing since at least
2026-09-30. Nothing recorded what the server actually sent.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from core import amfi_nav

ROOT = Path(__file__).resolve().parents[1]

GOOD = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;"
    "Net Asset Value;Date\r\n"
    "\r\n"
    "Open Ended Schemes(Other Scheme - Other  ETFs)\r\n"
    "\r\n"
    "Aditya Birla Sun Life Mutual Fund\r\n"
    "152732;INF209KC1134;-;ADITYA BIRLA SUN LIFE CRISIL BROAD BASED GILT ETF;12.3456;14-Oct-2026\r\n"
    "154224;INF209KC1670;-;Aditya Birla Sun Life BSE Top 10 Banks ETF;1,045.10;14-Oct-2026\r\n"
    "100001;INF000000000;-;Some Fund with no NAV;N.A.;14-Oct-2026\r\n"
)


def test_parses_real_navall_shape_and_skips_banners():
    df = amfi_nav.parse_navall_text(GOOD)
    assert list(df["Scheme_Code"]) == ["152732", "154224"]
    assert df["NAV"].tolist() == [12.3456, 1045.10]      # comma thousands handled


def test_bom_and_pipe_delimiter_do_not_yield_zero_rows():
    piped = "\ufeff" + GOOD.replace(";", "|")
    assert len(amfi_nav.parse_navall_text(piped.lstrip("\ufeff"))) == 2


def test_html_block_page_never_parses_as_data():
    html = ("<!DOCTYPE html><html><head><title>Access Denied</title></head>"
            "<body>a;b;c;d;e;f</body></html>")
    assert amfi_nav.parse_navall_text(html).empty


class _Resp:
    def __init__(self, url, body: str, status=200, ctype="text/plain"):
        self.url, self.status_code = url, status
        self.content = body.encode("utf-8")
        self.headers = {"Content-Type": ctype}


class _Sess:
    def __init__(self, bodies): self.bodies = bodies
    def get(self, url, **kw): return self.bodies[url]


def test_fetch_falls_through_html_to_good_url_and_diagnoses(tmp_path):
    u1, u2 = amfi_nav.NAVALL_URLS
    sess = _Sess({u1: _Resp(u1, "<html>Please enable JavaScript</html>", ctype="text/html"),
                  u2: _Resp(u2, GOOD)})
    df, diags = amfi_nav.fetch_navall(session=sess, data_dir=tmp_path)
    assert len(df) == 2
    assert diags[0]["rows"] == 0 and "JavaScript" in diags[0]["head"]
    assert diags[0]["content_type"] == "text/html"


def test_total_failure_saves_the_body_it_could_not_parse(tmp_path):
    u1, u2 = amfi_nav.NAVALL_URLS
    sess = _Sess({u: _Resp(u, "<html>blocked</html>", ctype="text/html") for u in (u1, u2)})
    df, diags = amfi_nav.fetch_navall(session=sess, data_dir=tmp_path)
    assert df.empty and all(d["error"] for d in diags)
    saved = tmp_path / amfi_nav.FAILED_BODY_NAME
    assert saved.exists() and "blocked" in saved.read_text(encoding="utf-8")


def test_all_three_etf_modules_use_the_shared_parser():
    for rel in ("etf_quality_builder.py", "etf_metadata_enricher.py", "etf_aum_auto_fetcher.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "amfi_nav.fetch_navall(" in src, f"{rel} still has a private NAVAll parser"


def test_data_health_tiles_read_dq_column_names():
    """dq_summary keys are column names; the tiles asked for lowercase aliases."""
    src = (ROOT / "run_app.py").read_text(encoding="utf-8")
    for key in ('"NAV"', '"AUM_Cr"', '"TER"', '"Benchmark_Index"'):
        assert f"_cov(cov_act, {key}" in src
