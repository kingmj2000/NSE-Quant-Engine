"""
Step 13 — Portfolio-Level Validation Gate.

Consolidated pre-flight check that runs after Steps 6–12 and produces a
single go/no-go verdict for the whole top-5 batch. Reads whatever exists in
output/. Never raises.

Batch_Verdict ∈ {Ship, Ship_With_Caveats, Downgrade_To_Watch}
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional
import numpy as np
import pandas as pd


DEFAULT_THRESHOLDS = {
    "max_avg_abs_corr": 0.70,          # concentration ceiling
    "max_portfolio_loss_pct_nav": 3.0, # sum of Max_Loss_%_of_NAV
    "max_single_sector_pct": 60.0,     # concentration by sector weight
    "min_backtest_hit_rate": 0.50,
    "min_alpha_survivors": 2,
}


def _read_csv(p: Path) -> pd.DataFrame:
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def _read_json(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _avg_abs_corr(corr: pd.DataFrame) -> float:
    if corr is None or corr.empty:
        return float("nan")
    try:
        m = corr.copy()
        m.columns = m.columns.astype(str)
        m.index = m.index.astype(str)
        vals = []
        cols = list(m.columns)
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                v = m.iloc[i, j]
                if pd.notna(v):
                    vals.append(abs(float(v)))
        return float(np.mean(vals)) if vals else float("nan")
    except Exception:
        return float("nan")


def _symbols(df: pd.DataFrame) -> list[str]:
    if df is None or df.empty or "Symbol" not in df.columns:
        return []
    return sorted({str(s) for s in df["Symbol"].dropna().tolist()})


def _corr_symbols(corr: pd.DataFrame) -> list[str]:
    if corr is None or corr.empty:
        return []
    try:
        return sorted({str(s) for s in corr.index.tolist()})
    except Exception:
        return []


def validate_batch(output_dir: Path,
                   thresholds: Optional[dict] = None,
                   validation_status: Optional[dict] = None) -> dict:
    """Return {"verdict","checks","reasons","thresholds"}. Never raises.

    `validation_status` is the parsed validation_status.json (the sole verdict
    authority). When it is not "Validation Positive" the batch can never be
    Ship / Ship_With_Caveats."""
    thr = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        thr.update({k: v for k, v in thresholds.items() if v is not None})

    output_dir = Path(output_dir)
    corr = _read_csv(output_dir / "top5_corr_matrix.csv")
    if not corr.empty and corr.columns[0].lower() in ("symbol", "unnamed: 0"):
        corr = corr.set_index(corr.columns[0])
    sizing = _read_csv(output_dir / "top5_position_sizing.csv")
    sector = _read_csv(output_dir / "top5_sector_context.csv")
    backtest = _read_csv(output_dir / "backtest_scorecard.csv")
    events = _read_csv(output_dir / "top5_events.csv")
    survivors = _read_json(output_dir / "alpha_zoo_survivors.json")
    macro = _read_json(output_dir / "macro_context.json")
    ev = _read_csv(output_dir / "top5_expected_value.csv")
    if validation_status is None:
        validation_status = _read_json(output_dir / "validation_status.json")

    checks: dict = {}
    reasons: list[str] = []
    caveats: list[str] = []

    # 1) Concentration by correlation
    aac = _avg_abs_corr(corr)
    checks["avg_abs_corr"] = None if pd.isna(aac) else round(aac, 3)
    if pd.notna(aac) and aac > thr["max_avg_abs_corr"]:
        reasons.append(f"avg |corr| {aac:.2f} > {thr['max_avg_abs_corr']:.2f}")

    # 2) Aggregate risk
    total_loss = float("nan")
    if not sizing.empty and "Max_Loss_%_of_NAV" in sizing.columns:
        try:
            total_loss = float(pd.to_numeric(sizing["Max_Loss_%_of_NAV"],
                                             errors="coerce").sum())
        except Exception:
            total_loss = float("nan")
    checks["sum_max_loss_pct_nav"] = None if pd.isna(total_loss) else round(total_loss, 2)
    if pd.notna(total_loss) and total_loss > thr["max_portfolio_loss_pct_nav"]:
        reasons.append(
            f"sum(Max_Loss_%_of_NAV)={total_loss:.2f} > "
            f"{thr['max_portfolio_loss_pct_nav']:.2f}"
        )

    # 3) Sector concentration
    top_sector = None
    top_sec_pct = float("nan")
    if not sizing.empty and not sector.empty and "Weight_%" in sizing.columns:
        try:
            merged = sizing[["Symbol", "Weight_%"]].merge(
                sector[["Symbol", "Sector"]], on="Symbol", how="left")
            g = merged.groupby(merged["Sector"].fillna("Unknown"))["Weight_%"].sum()
            if len(g):
                top_sector = str(g.idxmax())
                top_sec_pct = float(g.max())
        except Exception:
            pass
    checks["top_sector"] = top_sector
    checks["top_sector_weight_%"] = None if pd.isna(top_sec_pct) else round(top_sec_pct, 2)
    if pd.notna(top_sec_pct) and top_sec_pct > thr["max_single_sector_pct"] \
            and (top_sector or "").lower() not in ("", "unknown"):
        caveats.append(
            f"sector concentration: {top_sector} = {top_sec_pct:.1f}% "
            f"(> {thr['max_single_sector_pct']:.0f}%)"
        )

    # 4) Backtest floor
    hit = float("nan")
    if not backtest.empty and "Hit_Rate" in backtest.columns:
        try:
            hit = float(pd.to_numeric(
                backtest[backtest["Variant"].str.contains("Top5", na=False)]["Hit_Rate"],
                errors="coerce").dropna().iloc[0])
        except Exception:
            try:
                hit = float(pd.to_numeric(backtest["Hit_Rate"],
                                          errors="coerce").dropna().iloc[0])
            except Exception:
                hit = float("nan")
    checks["backtest_hit_rate_top5"] = None if pd.isna(hit) else round(hit, 3)
    if pd.notna(hit) and hit < thr["min_backtest_hit_rate"]:
        reasons.append(f"backtest hit rate {hit:.2f} < {thr['min_backtest_hit_rate']:.2f}")

    # 5) Alpha survivors
    n_surv = 0
    if isinstance(survivors, list):
        n_surv = len(survivors)
    elif isinstance(survivors, dict):
        n_surv = len(survivors.get("survivors", []))
    checks["n_alpha_survivors"] = n_surv
    if n_surv < thr["min_alpha_survivors"]:
        caveats.append(f"alpha survivors={n_surv} < {thr['min_alpha_survivors']}")

    # 6) Macro regime
    regime = str(macro.get("regime", "") or "").upper()
    checks["macro_regime"] = regime or None
    if regime == "RISK_OFF":
        reasons.append("macro regime = RISK_OFF")

    # 7) Event risk
    n_in_window = 0
    if not events.empty and "Event_Risk_Flag" in events.columns:
        n_in_window = int((events["Event_Risk_Flag"] == "In_Window").sum())
    checks["n_earnings_in_window"] = n_in_window
    if n_in_window >= 2:
        caveats.append(f"{n_in_window} names have earnings inside hold window")

    # 8) Official Top-5 symbol-set consistency across artifacts
    try:
        from .top5_contract import read_official_top5
        expected = _symbols(read_official_top5(output_dir))
    except Exception:
        expected = []
    corr_syms = _corr_symbols(corr)
    sizing_syms = _symbols(sizing)
    sector_syms = _symbols(sector)
    event_syms = _symbols(events)
    ev_syms = _symbols(ev)

    present = {"corr_symbols": corr_syms, "sizing_symbols": sizing_syms,
               "sector_symbols": sector_syms, "event_symbols": event_syms,
               "ev_symbols": ev_syms}
    aligned = True
    mismatched: list[str] = []
    if expected:
        for name, syms in present.items():
            if syms and syms != expected:
                aligned = False
                mismatched.append(name)

    checks["expected_symbols"] = expected
    checks.update(present)
    checks["symbol_set_aligned"] = aligned
    if not aligned:
        reasons.append(
            "symbol-set mismatch vs official Top-5 in: " + ", ".join(mismatched)
        )

    # 9) Official validation gate (validation_status.json is the authority)
    official_verdict = str((validation_status or {}).get("verdict", "") or "Unknown")
    checks["official_validation_verdict"] = official_verdict
    validation_ok = official_verdict.strip().lower() == "validation positive"
    if not validation_ok:
        reasons.append(f"official validation not positive ({official_verdict})")

    # Verdict
    if reasons:
        verdict = "Downgrade_To_Watch"
    elif caveats:
        verdict = "Ship_With_Caveats"
    else:
        verdict = "Ship"

    return {
        "verdict": verdict,
        "expected_symbols": expected,
        "corr_symbols": corr_syms,
        "sizing_symbols": sizing_syms,
        "sector_symbols": sector_syms,
        "event_symbols": event_syms,
        "ev_symbols": ev_syms,
        "symbol_set_aligned": aligned,
        "official_validation_verdict": official_verdict,
        "checks": checks,
        "reasons": reasons,
        "caveats": caveats,
        "thresholds": thr,
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
    }


def write_report(output_dir: Path, report: dict) -> Path:
    p = Path(output_dir) / "portfolio_validation.json"
    p.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return p
