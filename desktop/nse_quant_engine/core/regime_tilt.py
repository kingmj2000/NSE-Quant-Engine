"""
Step 15 — Regime-Conditional Alpha Tilt (report-only by default).

Reweights the alpha-zoo survivors based on macro regime. Does NOT introduce
new alpha families; only re-weights the ones that already passed IC gates.

Applied only when REGIME_TILT_APPLY=1 (default 0 = report-only).
"""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd


# Rough classification of alpha families by behaviour (matches core/alpha_zoo).
_MOMENTUM = {"mom_5d", "mom_21d", "mom_63d", "breakout_20d", "breakout_55d",
             "rsi_14", "high_252d"}
_MEANREV  = {"reversal_5d", "reversal_21d", "bollinger_z"}
_LOWVOL   = {"low_vol_20d", "low_vol_60d", "inv_vol_20d"}
_QUALITY  = {"quality_z", "value_z"}


def _family(alpha: str) -> str:
    a = str(alpha).lower()
    if a in _MOMENTUM: return "momentum"
    if a in _MEANREV:  return "mean_reversion"
    if a in _LOWVOL:   return "low_vol"
    if a in _QUALITY:  return "quality"
    return "other"


_REGIME_WEIGHTS = {
    "RISK_OFF": {"momentum": 0.5, "mean_reversion": 2.0,
                 "low_vol": 2.0, "quality": 1.5, "other": 1.0},
    "RISK_ON":  {"momentum": 2.0, "mean_reversion": 0.75,
                 "low_vol": 0.5, "quality": 1.0, "other": 1.0},
    "NEUTRAL":  {"momentum": 1.0, "mean_reversion": 1.0,
                 "low_vol": 1.0, "quality": 1.0, "other": 1.0},
}


def compute_tilt(survivors: list | dict, regime: str) -> dict:
    """Return {'regime','weights':{alpha:mult},'family_mults':{...},
    'n_survivors','notes'}. Never raises."""
    reg = (str(regime) or "NEUTRAL").upper()
    if reg not in _REGIME_WEIGHTS:
        reg = "NEUTRAL"
    fam_mult = _REGIME_WEIGHTS[reg]
    surv_list = survivors if isinstance(survivors, list) else survivors.get("survivors", []) if isinstance(survivors, dict) else []
    weights: dict[str, float] = {}
    for s in surv_list:
        name = s.get("alpha") if isinstance(s, dict) else str(s)
        if not name:
            continue
        fam = _family(name)
        weights[str(name)] = round(fam_mult.get(fam, 1.0), 3)
    notes = f"Regime={reg}. Multipliers applied by family; survivors unchanged."
    return {
        "regime": reg,
        "family_multipliers": fam_mult,
        "alpha_multipliers": weights,
        "n_survivors": len(weights),
        "notes": notes,
    }


def build_report(macro_ctx_path: Path, survivors_path: Path,
                 out_path: Path, apply: bool = False) -> dict:
    """Write regime_tilt_report.json. Returns the report dict. Never raises."""
    macro = {}
    survivors = []
    try:
        if Path(macro_ctx_path).exists():
            macro = json.loads(Path(macro_ctx_path).read_text(encoding="utf-8"))
    except Exception:
        macro = {}
    try:
        if Path(survivors_path).exists():
            survivors = json.loads(Path(survivors_path).read_text(encoding="utf-8"))
    except Exception:
        survivors = []
    regime = str(macro.get("regime", "NEUTRAL"))
    tilt = compute_tilt(survivors, regime)
    tilt["applied_to_scoring"] = bool(apply)
    tilt["mode"] = "APPLIED" if apply else "REPORT_ONLY"
    try:
        Path(out_path).write_text(json.dumps(tilt, indent=2), encoding="utf-8")
    except Exception:
        pass
    return tilt
