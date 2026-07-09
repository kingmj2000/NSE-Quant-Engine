"""
Turnover-aware alpha weighting (Part A3 of the tightened plan).

Given:
  * baseline weights (per alpha)
  * walk-forward survivor IC (per alpha, matured windows only — from
    alpha_evaluator; NEVER current-period / in-sample IC)
  * turnover proxy (per alpha, 0 = never changed rank list, 1 = churns every run)

produces final blended weights via   w_i ∝ IC_i / (1 + λ · turnover_i),
then normalizes to sum to 1.  Alphas without survivor IC are held at
baseline weight and excluded from the turnover adjustment.

Pure — reads no files, writes no files. Caller is responsible for I/O.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping
import numpy as np
import pandas as pd


def turnover_from_rebalance_history(history_paths: list[Path]) -> dict[str, float]:
    """Turnover proxy per alpha from a sequence of rebalance_diff.json files.

    Each file is expected to have `per_alpha_turnover: {alpha: 0..1}` or a
    generic `turnover: float`. If neither is present, contributes nothing.
    Missing history → empty dict, caller treats turnover as 0.
    """
    per_alpha: dict[str, list[float]] = {}
    for p in history_paths:
        try:
            rec = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        pa = rec.get("per_alpha_turnover") or {}
        if isinstance(pa, dict):
            for k, v in pa.items():
                try:
                    per_alpha.setdefault(str(k), []).append(float(v))
                except Exception:
                    continue
    return {k: float(np.mean(vs)) for k, vs in per_alpha.items() if vs}


def compute_weights(baseline: Mapping[str, float],
                    survivor_ic: Mapping[str, float],
                    turnover: Mapping[str, float] | None = None,
                    lam: float = 0.25) -> dict[str, dict]:
    """Return per-alpha record with baseline, survivor_ic, turnover, final.

    Alphas absent from `survivor_ic` (i.e. did not clear the walk-forward gate)
    stay at their baseline weight and are excluded from the turnover formula.
    """
    turnover = dict(turnover or {})
    baseline = {k: float(v) for k, v in baseline.items() if pd.notna(v)}
    if not baseline:
        return {}

    ic_gated = {k: float(v) for k, v in survivor_ic.items()
                if k in baseline and pd.notna(v) and abs(float(v)) > 0}

    # unnormalized turnover-adjusted weights for the gated set
    raw: dict[str, float] = {}
    for k, ic in ic_gated.items():
        t = max(0.0, float(turnover.get(k, 0.0)))
        raw[k] = abs(ic) / (1.0 + lam * t)

    # baseline weights outside the gated set are held (not scaled).
    held_mass = sum(baseline[k] for k in baseline if k not in ic_gated)

    # normalize gated slice to (1 - held_mass) so overall weights still sum to 1.
    gate_mass = 1.0 - held_mass
    denom = sum(raw.values())
    if denom > 0 and gate_mass > 0:
        scaled = {k: v / denom * gate_mass for k, v in raw.items()}
    else:
        # nothing to scale — fall back to pure baseline
        scaled = {k: baseline[k] for k in ic_gated}

    out: dict[str, dict] = {}
    for k in baseline:
        out[k] = {
            "baseline":     round(baseline[k], 6),
            "survivor_ic":  round(float(survivor_ic.get(k, np.nan)), 6)
                            if k in survivor_ic and pd.notna(survivor_ic[k]) else None,
            "turnover":     round(float(turnover.get(k, 0.0)), 4),
            "final_weight": round(scaled[k] if k in scaled else baseline[k], 6),
            "gated":        k in ic_gated,
        }
    # renormalize numerically to sum to 1 (kill rounding drift)
    total = sum(r["final_weight"] for r in out.values())
    if total > 0:
        for r in out.values():
            r["final_weight"] = round(r["final_weight"] / total, 6)
    return out
