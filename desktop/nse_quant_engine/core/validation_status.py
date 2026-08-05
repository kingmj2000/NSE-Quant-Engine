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


def active_rules(rules: dict | None = None) -> dict:
    """Resolve the ONE active threshold set.

    `rules` is the parsed scoring_rules.csv mapping (authoritative when
    supplied). Anything absent falls back to core.config. This guarantees the
    structured verdict and scoring_rules.csv can never diverge.
    """
    r = rules or {}

    def pick(key: str, cfg_default):
        v = r.get(key)
        try:
            return type(cfg_default)(v) if v is not None else cfg_default
        except Exception:
            return cfg_default

    return {
        "CrossVal_Min_Dates": pick("CrossVal_Min_Dates", int(C.CROSSVAL_MIN_DATES)),
        "CrossVal_Min_Effective_Dates": pick("CrossVal_Min_Effective_Dates", int(C.CROSSVAL_MIN_EFFECTIVE_DATES)),
        "CrossVal_Min_Obs": pick("CrossVal_Min_Obs", int(C.CROSSVAL_MIN_OBS)),
        "CrossVal_Min_Spread": pick("CrossVal_Min_Spread", float(C.CROSSVAL_MIN_SPREAD)),
        "CrossVal_Min_HitRate": pick("CrossVal_Min_HitRate", float(C.CROSSVAL_MIN_HITRATE)),
        "CrossVal_Min_TStat": pick("CrossVal_Min_TStat", float(C.CROSSVAL_MIN_TSTAT)),
        "CrossVal_Min_Bootstrap_Prob": pick("CrossVal_Min_Bootstrap_Prob", float(C.CROSSVAL_MIN_BOOTSTRAP_PROB)),
    }


def _isnan(v) -> bool:
    return v is None or (isinstance(v, float) and v != v)


def decide_verdict(stats: dict, rules: dict | None = None) -> tuple[str, str]:
    """
    Apply the active validation gates to cross-sectional stats.
    `stats` keys: validation_dates, effective_validation_dates, avg_obs,
    spread, hit_rate, adj_tstat, bootstrap_prob.

    NOTE: callers must pass ALREADY-SHRUNK stats when Bayesian shrinkage is on
    (see resolve_validation). Returns (verdict, evidence_grade).
    """
    t = active_rules(rules)
    g = stats.get

    if (g("validation_dates") or 0) < t["CrossVal_Min_Dates"]:
        return "Insufficient History", "Insufficient Evidence"
    if (g("effective_validation_dates") or 0) < t["CrossVal_Min_Effective_Dates"]:
        return "Insufficient Independent History", "Insufficient Evidence"
    if (g("avg_obs") or 0) < t["CrossVal_Min_Obs"]:
        return "Insufficient Breadth", "Insufficient Evidence"

    spread, hit, adj_t, boot = (g("spread"), g("hit_rate"),
                                g("adj_tstat"), g("bootstrap_prob"))
    if any(_isnan(x) for x in (spread, hit, adj_t, boot)):
        return "Insufficient Statistical Evidence", "Insufficient Evidence"

    if (spread >= t["CrossVal_Min_Spread"]
            and hit >= t["CrossVal_Min_HitRate"]
            and adj_t >= t["CrossVal_Min_TStat"]
            and boot >= t["CrossVal_Min_Bootstrap_Prob"]):
        strong = ((g("effective_validation_dates") or 0) >= 20 and adj_t >= 2.0)
        return "Validation Positive", "Strong Evidence" if strong else "Moderate Evidence"

    # Materially negative spread with a non-positive t-stat: the ranking is
    # actively wrong, not merely unproven.
    if spread <= -t["CrossVal_Min_Spread"] and adj_t <= 0:
        return "Validation Negative", "Weak or Negative Evidence"

    return "No Proven Edge Yet", "Weak or Negative Evidence"


def resolve_validation(raw_stats: dict, rules: dict | None = None) -> tuple[str, str, dict]:
    """THE authoritative validation path.

    1. take raw cross-sectional statistics
    2. apply Bayesian shrinkage
    3. decide the verdict from the SHRUNK statistics

    Returns (verdict, evidence_grade, stats) where `stats` keeps both shrunk
    values and their `*_raw` counterparts.
    """
    raw_stats = dict(raw_stats or {})
    shrunk = apply_bayes_shrink(raw_stats)
    verdict, grade = decide_verdict(shrunk, rules)
    return verdict, grade, shrunk


def write_status(path: str | Path, verdict: str, grade: str, stats: dict,
                 horizon: int = 10,
                 ranking_column: str = "Confidence_Adjusted_Score",
                 ranking_schema_version: int = 2) -> dict:
    shrink_on = bool(getattr(C, "VALIDATION_BAYES_SHRINK", True))
    status = {
        "verdict": verdict,
        "evidence_grade": grade,
        "verdict_basis": "bayesian_adjusted" if shrink_on else "raw",
        "bayesian_shrinkage_applied": shrink_on,
        "horizon_days": horizon,
        "ranking_column": ranking_column,
        "ranking_schema_version": int(ranking_schema_version),
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
