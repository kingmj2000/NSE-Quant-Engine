"""Cached artifacts must degrade, not crash the pipeline.

`Path.exists()` is not "usable". A zero-byte CSV passes the exists check and then
makes `pd.read_csv` raise `EmptyDataError: No columns to parse from file` — and
because these reads sit in FALLBACK paths, the crash lands in the recovery code,
which is the worst place for it.

Observed sequence: a failed AMFI fetch wrote an empty `amfi_navall_latest.csv`;
every later run then died reading it, network up or down, halting the whole
pipeline at `etf_quality_builder`.

The distinction these tests pin:
  * cached/derived artifact  -> empty frame, warn once, continue
  * genuine pipeline input   -> still raise, but name the file and the fix
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


# ── the reader itself ───────────────────────────────────────────────────────

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
    """Callers index these columns straight after reading."""
    cols = ["AMFI_Scheme_Code", "NAV"]
    p = tmp_path / "empty.csv"
    p.write_text("", encoding="utf-8")
    out = read_cached_csv(p, expected_columns=cols)
    assert list(out.columns) == cols

    p2 = tmp_path / "partial.csv"
    pd.DataFrame({"AMFI_Scheme_Code": ["1"]}).to_csv(p2, index=False)
    out2 = read_cached_csv(p2, expected_columns=cols)
    assert "NAV" in out2.columns


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
    assert "config.csv" in msg
    assert "universe_builder" in msg, "the message must say how to regenerate it"

    with pytest.raises(FileNotFoundError):
        read_required_csv(tmp_path / "absent.csv")


def test_a_corrupt_cache_is_never_deleted(tmp_path):
    """Self-healing must come from overwriting, not from destroying evidence."""
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
    # Identifier columns must be TEXT here, matching what the live parser
    # produces. Without that, ETF mapping silently behaves differently depending
    # on whether AMFI happened to be reachable.
    assert nav.iloc[0]["AMFI_Scheme_Code"] == "101234"
    assert nav.iloc[0]["AMFI_ISIN_Growth"] == "INF204KB14I2"


def test_manual_quality_cache_can_be_empty(tmp_path, monkeypatch):
    eqb = _load("eqb_manual", "etf_quality_builder.py")
    p = tmp_path / "manual_etf_quality.csv"
    p.write_text("", encoding="utf-8")
    monkeypatch.setattr(eqb, "MANUAL_QUALITY_CSV", p)
    assert eqb.load_manual_quality().empty      # must not raise


def test_no_unguarded_cache_reads_remain_in_the_etf_chain():
    """Every fallback read in these modules must go through safe_io."""
    import ast

    for rel in ("etf_quality_builder.py", "etf_metadata_enricher.py",
                "etf_aum_auto_fetcher.py", "etf_ter_tracking_auto_fetcher.py"):
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
