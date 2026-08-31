"""ETF metadata resilience: an unreachable AMFI must not break the pipeline.

Three compounding defects, all instances of this codebase's recurring shape —
"could not be done" recorded as "was done":

1. `etf_metadata_enricher.fetch_amfi_navall()` treated a parse yielding zero rows
   as SUCCESS and wrote the empty frame over `amfi_navall_latest.csv`, destroying
   a working 14,280-row cache during a transient DNS outage.
2. `etf_quality_builder.fetch_amfi_nav()` then read that zero-byte cache with a
   bare `pd.read_csv`, raising `EmptyDataError` in the RECOVERY path.
3. `make_mapping_suggestions()` reached `candidates.iloc[0]` on the empty NAV
   table and raised IndexError, HALTING the whole pipeline — an optional metadata
   source taking down scoring, validation and everything downstream.

Together they turned a temporary network failure into a permanently broken run:
even after DNS recovered, the poisoned cache kept the crash alive.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

ENG = Path(__file__).resolve().parents[1]
if str(ENG) not in sys.path:
    sys.path.insert(0, str(ENG))

from core.safe_io import is_usable_csv, read_cached_csv, read_required_csv


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ENG / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


def _etfs() -> pd.DataFrame:
    return pd.DataFrame({
        "Name": ["Nippon India ETF Nifty BeES", "SBI Gold ETF"],
        "Symbol": ["NIFTYBEES.NS", "SETFGOLD.NS"],
        "ISIN": ["INF204KB14I2", ""],
        "Raw_Symbol": ["NIFTYBEES", "SETFGOLD"],
        "Category": ["ETF", "ETF"],
    })


NAV_COLS = ["AMFI_Scheme_Code", "AMFI_Scheme_Name",
            "AMFI_ISIN_Growth", "AMFI_ISIN_Div", "NAV", "NAV_Date"]


# ── safe_io ─────────────────────────────────────────────────────────────────

def test_zero_byte_cache_reads_as_empty_not_error(tmp_path):
    p = tmp_path / "amfi_navall_latest.csv"
    p.write_text("", encoding="utf-8")
    assert is_usable_csv(p) is False
    assert read_cached_csv(p).empty          # must not raise EmptyDataError


def test_corrupt_cache_reads_as_empty(tmp_path):
    p = tmp_path / "cache.csv"
    p.write_bytes(b"\x00\xff\xfe not a csv at all")
    assert read_cached_csv(p).empty


def test_missing_cache_reads_as_empty(tmp_path):
    assert read_cached_csv(tmp_path / "nope.csv").empty


def test_expected_columns_are_always_present(tmp_path):
    cols = ["AMFI_Scheme_Code", "NAV"]
    p = tmp_path / "empty.csv"
    p.write_text("", encoding="utf-8")
    assert list(read_cached_csv(p, expected_columns=cols).columns) == cols


def test_good_cache_still_reads_normally(tmp_path):
    p = tmp_path / "cache.csv"
    pd.DataFrame({"a": [1, 2, 3]}).to_csv(p, index=False)
    assert len(read_cached_csv(p)) == 3


def test_required_input_still_raises_but_explains(tmp_path):
    p = tmp_path / "config.csv"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        read_required_csv(p, produced_by="python universe_builder.py")
    msg = str(exc.value)
    assert "config.csv" in msg and "universe_builder" in msg


def test_a_corrupt_cache_is_never_deleted(tmp_path):
    """Self-healing comes from overwriting, not from destroying evidence."""
    p = tmp_path / "cache.csv"
    p.write_bytes(b"\x00 broken")
    read_cached_csv(p)
    assert p.exists()


# ── the pipeline path that actually broke ───────────────────────────────────

def test_amfi_fetch_survives_dns_failure_and_empty_cache(tmp_path, monkeypatch):
    """The exact observed failure: DNS down AND a poisoned cache."""
    eqb = _load("eqb_safe", "etf_quality_builder.py")
    data = tmp_path / "data"
    data.mkdir()
    (data / "amfi_navall_latest.csv").write_text("", encoding="utf-8")

    monkeypatch.setattr(eqb, "DATA_DIR", data)
    monkeypatch.setattr(eqb, "AMFI_NAV_URL",
                        "https://portal.amfiindia.invalid/spages/NAVAll.txt")

    nav = eqb.fetch_amfi_nav()          # must not raise

    assert nav.empty
    for col in ("AMFI_Scheme_Code", "AMFI_Scheme_Name", "NAV", "NAV_Date"):
        assert col in nav.columns, "callers index these unconditionally"


def test_amfi_fetch_uses_a_good_cache_when_the_network_is_down(tmp_path, monkeypatch):
    eqb = _load("eqb_cache", "etf_quality_builder.py")
    data = tmp_path / "data"
    data.mkdir()
    pd.DataFrame({
        "Scheme_Code": ["101234"], "ISIN_1": ["INF204KB14I2"], "ISIN_2": [""],
        "Scheme_Name": ["Nippon India ETF Nifty BeES"],
        "NAV": [250.5], "NAV_Date": ["20-Aug-2026"],
    }).to_csv(data / "amfi_navall_latest.csv", index=False)

    monkeypatch.setattr(eqb, "DATA_DIR", data)
    monkeypatch.setattr(eqb, "AMFI_NAV_URL",
                        "https://portal.amfiindia.invalid/spages/NAVAll.txt")

    nav = eqb.fetch_amfi_nav()
    assert len(nav) == 1
    # Identifier columns must be TEXT, matching the live parser. Otherwise ETF
    # mapping silently behaves differently depending on AMFI reachability.
    assert nav.iloc[0]["AMFI_Scheme_Code"] == "101234"
    assert nav.iloc[0]["AMFI_ISIN_Growth"] == "INF204KB14I2"


@pytest.mark.parametrize("nav", [
    pd.DataFrame(),                       # AMFI never answered
    pd.DataFrame(columns=NAV_COLS),       # answered with no rows
])
def test_empty_nav_table_does_not_halt_the_pipeline(nav):
    eqb = _load("eqb_empty", "etf_quality_builder.py")
    out = eqb.make_mapping_suggestions(_etfs(), nav)

    assert len(out) == 2, "every ETF must still get a row"
    assert set(out["Mapping_Status"]) == {"Missing"}


def test_no_match_is_reported_as_missing_not_verified():
    eqb = _load("eqb_status", "etf_quality_builder.py")
    assert eqb.mapping_status("Unavailable", 0.0) == "Missing"
    assert eqb.mapping_status("ISIN", 1.0) == "Verified"


def test_populated_nav_table_still_matches():
    """Degrading gracefully must not degrade the working path."""
    eqb = _load("eqb_ok", "etf_quality_builder.py")
    nav = pd.DataFrame([{
        "AMFI_Scheme_Code": "101234",
        "AMFI_Scheme_Name": "Nippon India ETF Nifty BeES",
        "AMFI_ISIN_Growth": "INF204KB14I2",
        "AMFI_ISIN_Div": "",
        "NAV": 250.5,
        "NAV_Date": "20-Aug-2026",
    }])
    row = eqb.make_mapping_suggestions(_etfs(), nav)
    row = row[row["Symbol"] == "NIFTYBEES.NS"].iloc[0]
    assert row["Mapping_Status"] == "Verified"
    assert row["Suggested_AMFI_Scheme_Code"] == "101234"


# ── the destroyed NAV cache ─────────────────────────────────────────────────

def test_degraded_fetch_never_overwrites_a_good_cache(tmp_path, monkeypatch):
    enr = _load("enr_cache", "etf_metadata_enricher.py")
    cache = tmp_path / "amfi_navall_latest.csv"
    pd.DataFrame({"Scheme_Code": range(14280), "NAV": [1.0] * 14280}).to_csv(
        cache, index=False)
    monkeypatch.setattr(enr, "AMFI_NAV_OUT", cache)

    enr._write_nav_cache_if_better(pd.DataFrame({"Scheme_Code": [1], "NAV": [1.0]}), [])

    assert len(pd.read_csv(cache)) == 14280, "the good cache was overwritten"


def test_a_full_fetch_does_replace_the_cache(tmp_path, monkeypatch):
    enr = _load("enr_write", "etf_metadata_enricher.py")
    cache = tmp_path / "amfi_navall_latest.csv"
    pd.DataFrame({"Scheme_Code": range(100), "NAV": [1.0] * 100}).to_csv(
        cache, index=False)
    monkeypatch.setattr(enr, "AMFI_NAV_OUT", cache)

    enr._write_nav_cache_if_better(
        pd.DataFrame({"Scheme_Code": range(14280), "NAV": [2.0] * 14280}), [])

    assert len(pd.read_csv(cache)) == 14280


def test_cache_is_written_when_none_exists(tmp_path, monkeypatch):
    enr = _load("enr_new", "etf_metadata_enricher.py")
    cache = tmp_path / "amfi_navall_latest.csv"
    monkeypatch.setattr(enr, "AMFI_NAV_OUT", cache)
    enr._write_nav_cache_if_better(pd.DataFrame({"Scheme_Code": [1], "NAV": [1.0]}), [])
    assert cache.exists() and len(pd.read_csv(cache)) == 1


def test_cache_loader_tolerates_a_corrupt_file(tmp_path, monkeypatch):
    enr = _load("enr_bad", "etf_metadata_enricher.py")
    cache = tmp_path / "amfi_navall_latest.csv"
    cache.write_bytes(b"\x00\xff not a csv")
    monkeypatch.setattr(enr, "AMFI_NAV_OUT", cache)
    assert enr._load_nav_cache().empty


def test_zero_parsed_rows_is_not_treated_as_success():
    src = (ENG / "etf_metadata_enricher.py").read_text(encoding="utf-8")
    i = src.index("def fetch_amfi_navall")
    body = src[i:src.index("def _load_nav_cache")]
    assert "if df.empty:" in body, "an empty parse must not be recorded as OK"
    assert "_write_nav_cache_if_better" in body, "cache write must be guarded"
    assert "df.to_csv(AMFI_NAV_OUT" not in body, "unconditional cache write is back"


# ── HTML parser diagnostics ─────────────────────────────────────────────────

def test_missing_parser_and_no_tables_are_distinguished():
    """These need opposite responses: pip install vs the site is blocking us."""
    from core.optional_data_fetchers import _try_read_html

    with pytest.raises(RuntimeError) as exc:
        _try_read_html("<html><body><p>Access denied</p></body></html>")
    assert "no tables" in str(exc.value).lower()

    assert len(_try_read_html("<table><tr><th>A</th></tr><tr><td>1</td></tr></table>")) == 1


def test_no_unguarded_cache_reads_remain_in_the_etf_chain():
    """Every fallback read in these modules must go through safe_io."""
    import ast

    for rel in ("etf_quality_builder.py", "etf_metadata_enricher.py",
                "etf_ter_tracking_auto_fetcher.py", "etf_aum_auto_fetcher.py"):
        tree = ast.parse((ENG / rel).read_text(encoding="utf-8"))
        protected = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Try):
                protected.update(id(c) for c in ast.walk(n))
        unguarded = [
            n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in ("read_csv", "read_excel")
            and isinstance(n.func.value, ast.Name) and n.func.value.id in ("pd", "pandas")
            and id(n) not in protected
        ]
        assert not unguarded, f"{rel}: unguarded pandas read at line(s) {unguarded}"
