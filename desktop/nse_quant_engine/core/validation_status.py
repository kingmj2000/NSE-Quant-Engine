"""
Structured validation status (clean core v4).

Root-causes the bug you hit in Stage 3.4: the trade-plan builder PARSED the
human-readable markdown validation report, matched the phrase "Validation
Positive" inside an explanatory sentence, and stamped a false green light.

The fix is a principle: ONE structured source of truth per fact. The validation
step writes validation_status.json (machine-readable); every downstream consumer
reads THAT, never the prose report. A sentence in a report can never again be
mistaken for a verdict.

write_status() is called by your cross-sectional validation step.
read_status()  is called by trade_plan / EV / the AI-review export.
"""

from __future__ import annotations
from pathlib import Path
import json

from . import config as C

VALID_VERDICTS = (
    "Validation Positive",
    "Validation Negative",
    "No Proven Edge Yet",
    "Insufficient Statistical Evidence",
    "Insufficient Breadth",
    "Insufficient Independent History",
    "Insufficient History",
)


# ─── Bayesian shrinkage on validation stats (protects the ship/hold gate) ───

def shrink_hit_rate(observed: float, n_obs: int,
                    prior_alpha: float | None = None,
                    prior_beta: float | None = None) -> float:
    """Beta prior posterior mean on hit-rate. Small-sample-safe."""
    if observed is None or (isinstance(observed, float) and (observed != observed)):
        return float("nan")
    a0 = float(prior_alpha if prior_alpha is not None else getattr(C, "VALIDATION_HITRATE_PRIOR_ALPHA", 10.0))
    b0 = float(prior_beta  if prior_beta  is not None else getattr(C, "VALIDATION_HITRATE_PRIOR_BETA",  10.0))
    n  = max(int(n_obs or 0), 0)
    successes = float(observed) * n
    return (a0 + successes) / (a0 + b0 + n)


def shrink_ic(observed: float, n_obs: int, prior_n: int | None = None) -> float:
    """Shrink IC toward 0 with weight = prior_n / (prior_n + n)."""
    if observed is None or (isinstance(observed, float) and (observed != observed)):
        return float("nan")
    pn = int(prior_n if prior_n is not None else getattr(C, "VALIDATION_IC_PRIOR_N", 20))
    n  = max(int(n_obs or 0), 0)
    w_prior = pn / (pn + n) if (pn + n) > 0 else 1.0
    return (1.0 - w_prior) * float(observed) + w_prior * 0.0


def apply_bayes_shrink(stats: dict) -> dict:
    """Return a new stats dict with `hit_rate` and `adj_tstat` shrunk toward
    prior. Raw values are preserved under `*_raw` keys so the artifact stays
    transparent. Safe on missing keys."""
    if not getattr(C, "VALIDATION_BAYES_SHRINK", True):
        return dict(stats)
    out = dict(stats or {})
    n_dates = int(out.get("effective_validation_dates") or out.get("validation_dates") or 0)
    if "hit_rate" in out and out.get("hit_rate") is not None:
        out["hit_rate_raw"] = out.get("hit_rate")
        out["hit_rate"] = round(shrink_hit_rate(out["hit_rate"], n_dates), 6)
    # Treat adjusted t-stat like an IC-scale quantity: shrink toward 0.
    if "adj_tstat" in out and out.get("adj_tstat") is not None:
        out["adj_tstat_raw"] = out.get("adj_tstat")
        out["adj_tstat"] = round(shrink_ic(out["adj_tstat"], n_dates), 6)
    if "spread" in out and out.get("spread") is not None:
        out["spread_raw"] = out.get("spread")
        out["spread"] = round(shrink_ic(out["spread"], n_dates), 6)
    return out


def decide_verdict(stats: dict) -> tuple[str, str]:
    """
    Apply the validation gates from config to the computed cross-sectional stats.
    `stats` keys: validation_dates, effective_validation_dates, avg_obs,
    spread, hit_rate, adj_tstat, bootstrap_prob.
    Returns (verdict, evidence_grade).
    """
    g = stats.get
    if g("validation_dates", 0) < C.CROSSVAL_MIN_DATES:
        return "Insufficient History", "Insufficient Evidence"
    if g("effective_validation_dates", 0) < C.CROSSVAL_MIN_EFFECTIVE_DATES:
        return "Insufficient Independent History", "Insufficient Evidence"
    if g("avg_obs", 0) < C.CROSSVAL_MIN_OBS:
        return "Insufficient Breadth", "Insufficient Evidence"

    passes = (
        g("spread", -1) >= C.CROSSVAL_MIN_SPREAD
        and g("hit_rate", 0) >= C.CROSSVAL_MIN_HITRATE
        and g("adj_tstat", 0) >= C.CROSSVAL_MIN_TSTAT
        and g("bootstrap_prob", 0) >= C.CROSSVAL_MIN_BOOTSTRAP_PROB
    )
    if passes:
        return "Validation Positive", "Sufficient Evidence"

    # Enough data, but the edge isn't there after costs.
    if g("spread", 0) < 0:
        return "Validation Negative", "Sufficient Evidence"
    return "No Proven Edge Yet", "Sufficient Evidence"


def write_status(path: str | Path, verdict: str, grade: str, stats: dict,
                 horizon: int = 10) -> dict:
    status = {
        "verdict": verdict,
        "evidence_grade": grade,
        "horizon_days": horizon,
        "stats": {k: (None if v is None else float(v) if isinstance(v, (int, float)) else v)
                  for k, v in stats.items()},
        "schema": "nse_validation_status_v1",
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status


def read_status(path: str | Path) -> dict:
    """Read structured status. If the file is missing/corrupt, fail SAFE —
    return Insufficient History so downstream defaults to watchlist-only."""
    p = Path(path)
    safe = {"verdict": "Insufficient History",
            "evidence_grade": "Insufficient Evidence",
            "stats": {}, "schema": "nse_validation_status_v1",
            "note": "status file missing or unreadable; defaulting to watchlist-only"}
    if not p.exists():
        return safe
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if str(data.get("verdict", "")) not in VALID_VERDICTS:
            data["verdict"] = "Insufficient History"
        return data
    except Exception:
        return safe
