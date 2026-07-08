"""
Step 7 — Evidence Bundle assembler (no in-script AI).

Zips the top-5 evidence (CSVs, JSONs, prompt spec) into a single, portable
archive the user hands to Claude / any external LLM. Pure stdlib. Never
raises: any missing input is skipped, and a manifest lists what was included
vs missing so the AI knows.
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
import pandas as pd


# Files we will include if present. Order matters only for the manifest.
_CANDIDATE_FILES = [
    "trade_plan_latest.csv",
    "top5_horizon.csv",
    "top5_sentiment.csv",
    "top5_benchmark_stats.csv",
    "top5_corr_matrix.csv",
    "top5_fundamentals.csv",
    "top5_position_sizing.csv",
    "top5_sector_context.csv",
    "top5_events.csv",
    "top5_expected_value.csv",
    "portfolio_validation.json",
    "top5_institutional_flow.csv",
    "regime_tilt_report.json",
    "rebalance_diff.json",
    "alpha_zoo_ic_report.csv",
    "alpha_zoo_survivors.json",
    "macro_context.json",
    "backtest_scorecard.csv",
    "backtest_equity_curve.csv",
    "cross_sectional_validation_report.md",
    "validation_status.json",
    "news_market_latest.csv",
]


def _read_top5(output_dir: Path) -> pd.DataFrame:
    tp = output_dir / "trade_plan_latest.csv"
    if not tp.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(tp)
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df
    reviewable = df[~df.get("Trade_Status", pd.Series(dtype=str)).astype(str)
                    .str.contains("Avoid", case=False, na=False)].copy()
    sort_cols = [c for c in ["Confidence_Adjusted_Score", "Final_Score"]
                 if c in reviewable.columns]
    if sort_cols:
        reviewable = reviewable.sort_values(sort_cols, ascending=False)
    return reviewable.head(5)


def _row_lookup(df: pd.DataFrame, sym: str) -> dict:
    if df is None or df.empty or "Symbol" not in df.columns:
        return {}
    sub = df[df["Symbol"].astype(str) == str(sym)]
    if sub.empty:
        return {}
    rec = sub.iloc[0].to_dict()
    # JSON-safe
    return {k: (None if pd.isna(v) else v) for k, v in rec.items()}


def build_evidence_json(output_dir: Path, top5: pd.DataFrame) -> dict:
    """Aggregate per-symbol evidence for the LLM. Reads whatever exists."""
    def _read(name: str) -> pd.DataFrame:
        p = output_dir / name
        if not p.exists():
            return pd.DataFrame()
        try:
            return pd.read_csv(p)
        except Exception:
            return pd.DataFrame()

    horizon = _read("top5_horizon.csv")
    sent = _read("top5_sentiment.csv")
    bench = _read("top5_benchmark_stats.csv")
    fund = _read("top5_fundamentals.csv")
    sizing = _read("top5_position_sizing.csv")
    sector = _read("top5_sector_context.csv")
    events = _read("top5_events.csv")
    ev_report = _read("top5_expected_value.csv")
    instflow = _read("top5_institutional_flow.csv")

    macro = {}
    mp = output_dir / "macro_context.json"
    if mp.exists():
        try:
            macro = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            macro = {}

    survivors = {}
    sp = output_dir / "alpha_zoo_survivors.json"
    if sp.exists():
        try:
            survivors = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            survivors = {}

    portfolio_val = {}
    pv = output_dir / "portfolio_validation.json"
    if pv.exists():
        try:
            portfolio_val = json.loads(pv.read_text(encoding="utf-8"))
        except Exception:
            portfolio_val = {}

    regime_tilt = {}
    rt = output_dir / "regime_tilt_report.json"
    if rt.exists():
        try:
            regime_tilt = json.loads(rt.read_text(encoding="utf-8"))
        except Exception:
            regime_tilt = {}

    rebalance = {}
    rb = output_dir / "rebalance_diff.json"
    if rb.exists():
        try:
            rebalance = json.loads(rb.read_text(encoding="utf-8"))
        except Exception:
            rebalance = {}

    picks = []
    for _, r in top5.iterrows():
        sym = str(r.get("Symbol", ""))
        picks.append({
            "symbol": sym,
            "name": r.get("Name") if not pd.isna(r.get("Name", None)) else None,
            "trade_status": r.get("Trade_Status"),
            "final_score": None if pd.isna(r.get("Final_Score", None)) else float(r.get("Final_Score")),
            "confidence_adjusted_score": None if pd.isna(r.get("Confidence_Adjusted_Score", None)) else float(r.get("Confidence_Adjusted_Score")),
            "price": None if pd.isna(r.get("Price", None)) else float(r.get("Price")),
            "buy_zone": [
                None if pd.isna(r.get("Buy_Zone_Low", None)) else float(r.get("Buy_Zone_Low")),
                None if pd.isna(r.get("Buy_Zone_High", None)) else float(r.get("Buy_Zone_High")),
            ],
            "stop_loss": None if pd.isna(r.get("Stop_Loss", None)) else float(r.get("Stop_Loss")),
            "target_1": None if pd.isna(r.get("Target_1", None)) else float(r.get("Target_1")),
            "target_2": None if pd.isna(r.get("Target_2", None)) else float(r.get("Target_2")),
            "horizon": _row_lookup(horizon, sym),
            "sentiment": _row_lookup(sent, sym),
            "benchmark_stats": _row_lookup(bench, sym),
            "fundamentals": _row_lookup(fund, sym),
            "sizing": _row_lookup(sizing, sym),
            "sector_context": _row_lookup(sector, sym),
            "event_calendar": _row_lookup(events, sym),
            "expected_value": _row_lookup(ev_report, sym),
            "key_risk": r.get("Key_Risk"),
        })

    return {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "macro_context": macro,
        "alpha_zoo_survivors": survivors,
        "portfolio_validation": portfolio_val,
        "picks": picks,
    }


def build_manifest(output_dir: Path, included: list[str],
                   config_snapshot: Optional[dict]) -> dict:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "engine_version": "insight-engine v4 + steps 1-13",
        "included_files": included,
        "missing_files": [f for f in _CANDIDATE_FILES if f not in included],
        "config_snapshot": config_snapshot or {},
        "instructions_for_ai": "See README_for_AI.md",
    }


def _config_snapshot() -> dict:
    try:
        from core import config as C
    except Exception:
        return {}
    snap = {}
    for k in dir(C):
        if k.startswith("_"):
            continue
        v = getattr(C, k, None)
        if isinstance(v, (bool, int, float, str, list, tuple)):
            snap[k] = v
    return snap


def build_bundle(output_dir: Path,
                 prompts_dir: Path,
                 bundle_max_mb: float = 5.0,
                 keep_last_n: int = 10) -> Optional[Path]:
    """Assemble output/insight_bundle_<ts>.zip. Returns the zip path, or None
    if there's nothing to bundle. Never raises."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        top5 = _read_top5(output_dir)
    except Exception:
        top5 = pd.DataFrame()
    if top5.empty:
        return None

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    zip_path = output_dir / f"insight_bundle_{ts}.zip"

    included: list[str] = []
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # only-top-5 slice of trade_plan_latest.csv for size
            slim = zip_path.with_suffix(".top5.csv")
            try:
                top5.to_csv(slim, index=False)
                zf.write(slim, arcname="top5.csv")
                slim.unlink(missing_ok=True)
                included.append("top5.csv")
            except Exception:
                pass

            for name in _CANDIDATE_FILES:
                p = output_dir / name
                if p.exists() and p.stat().st_size < bundle_max_mb * 1024 * 1024:
                    zf.write(p, arcname=name)
                    included.append(name)

            # evidence.json (aggregated) + manifest + prompt
            try:
                ev = build_evidence_json(output_dir, top5)
                zf.writestr("evidence.json", json.dumps(ev, default=str, indent=2))
                included.append("evidence.json")
            except Exception:
                pass

            try:
                man = build_manifest(output_dir, included, _config_snapshot())
                zf.writestr("run_manifest.json", json.dumps(man, default=str, indent=2))
                included.append("run_manifest.json")
            except Exception:
                pass

            # prompt spec
            prompt_src = Path(prompts_dir) / "rationale_prompt.md"
            if prompt_src.exists():
                zf.write(prompt_src, arcname="README_for_AI.md")
                included.append("README_for_AI.md")
    except Exception:
        return None

    # prune old bundles
    try:
        old = sorted(output_dir.glob("insight_bundle_*.zip"))
        for p in old[:-keep_last_n]:
            try:
                p.unlink()
            except Exception:
                pass
    except Exception:
        pass

    # size check
    try:
        if zip_path.stat().st_size > bundle_max_mb * 1024 * 1024:
            # too large — leave it, caller can act on the size
            pass
    except Exception:
        pass

    return zip_path
