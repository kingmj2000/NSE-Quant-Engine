"""Compares the official engine against the v4.1 shadow engine — diagnostically.

This module NEVER recommends a champion or a model switch. The shadow engine has
no independent validation history (nothing writes validation_status_shadow.json),
so it is CURRENT-RANKING DIAGNOSTIC ONLY and is not eligible for promotion.

Score identity is exact and has no fallback:
    Official = Confidence_Adjusted_Score
    Shadow   = V4_Confidence_Adjusted_Score
"""
from __future__ import annotations
from pathlib import Path
import json
import numpy as np
import pandas as pd

from core import validation_status as vs
from core import expected_value as ev
from core import config as C

BASE = Path(__file__).resolve().parent
OUT = BASE / "output"
OUT.mkdir(exist_ok=True)

OFFICIAL = OUT / "latest_scores.csv"
SHADOW = OUT / "latest_scores_v4_shadow.csv"
STATUS_OFF = OUT / "validation_status.json"
STATUS_SHA = OUT / "validation_status_shadow.json"
FWD = OUT / "forward_return_history.csv"
REPORT = OUT / "shadow_vs_official.md"
CSV = OUT / "shadow_vs_official.csv"


def _read(p: Path) -> pd.DataFrame:
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


def _spearman(a: pd.Series, b: pd.Series) -> float:
    try:
        return float(a.rank().corr(b.rank()))
    except Exception:
        return float("nan")


def build() -> dict:
    off = _read(OFFICIAL)
    sha = _read(SHADOW)
    if off.empty or sha.empty:
        REPORT.write_text("# Shadow vs Official\n\nMissing one of the score files — run the pipeline first.\n")
        return {"recommendation": "INSUFFICIENT_DATA"}

    # Official ranking authority is Confidence_Adjusted_Score, full stop.
    # There is NO Final_Score fallback: without CAS the comparison is a
    # data-quality failure, not a raw-score comparison.
    if "Confidence_Adjusted_Score" not in off.columns:
        REPORT.write_text(
            "# Shadow vs Official\n\nOfficial scores are missing "
            "`Confidence_Adjusted_Score` — comparison skipped (data quality).\n")
        return {"recommendation": "INSUFFICIENT_DATA",
                "reason": "official_missing_Confidence_Adjusted_Score"}

    # Shadow score identity is EXACT: V4_Confidence_Adjusted_Score, no fallback.
    #
    # The shadow frame is built as `out = old.copy()`, so it can inherit official
    # score columns. Any fallback chain here (CAS -> Shadow_Score -> Final_Score
    # -> Opportunity_Score) can therefore select the OFFICIAL score sitting in the
    # shadow file and compare official-against-itself, reporting Spearman 1.00 and
    # Jaccard 1.00 as if the two engines agreed perfectly. They are not the same
    # number and must never be resolved by name-guessing.
    score_col_off = "Confidence_Adjusted_Score"
    score_col_sha = "V4_Confidence_Adjusted_Score"
    if score_col_sha not in sha.columns:
        REPORT.write_text(
            "# Shadow vs Official\n\nShadow scores are missing "
            "`V4_Confidence_Adjusted_Score` — comparison skipped (data quality). "
            "No fallback score column is permitted: falling back would risk "
            "comparing the official score against a copy of itself.\n")
        return {"recommendation": "INSUFFICIENT_DATA",
                "reason": "shadow_missing_V4_Confidence_Adjusted_Score"}
    for _leaked in ("Confidence_Adjusted_Score", "Final_Score", "Opportunity_Score"):
        if _leaked in sha.columns:
            REPORT.write_text(
                "# Shadow vs Official\n\nShadow score file still contains the bare "
                f"official column `{_leaked}` — comparison skipped (data quality). "
                "Shadow outputs must rename inherited official scores to "
                "`Official_*`.\n")
            return {"recommendation": "INSUFFICIENT_DATA",
                    "reason": f"shadow_contains_official_column_{_leaked}"}

    merged = off[["Symbol", score_col_off]].rename(columns={score_col_off: "score_off"}).merge(
        sha[["Symbol", score_col_sha]].rename(columns={score_col_sha: "score_sha"}),
        on="Symbol", how="inner",
    )

    top_n = 25
    top_off = set(off.sort_values([score_col_off, "Symbol"], ascending=[False, True]).head(top_n)["Symbol"])
    top_sha = set(sha.sort_values([score_col_sha, "Symbol"], ascending=[False, True]).head(top_n)["Symbol"])
    jaccard = len(top_off & top_sha) / max(len(top_off | top_sha), 1)
    rho = _spearman(merged["score_off"], merged["score_sha"])

    # Mean |rank delta| across the common universe. Ranks are built the same way
    # on both sides (score desc, Symbol asc) so only the score differs.
    if len(merged) >= 2:
        r_off = merged["score_off"].rank(ascending=False, method="min")
        r_sha = merged["score_sha"].rank(ascending=False, method="min")
        avg_abs_delta_rank = float((r_off - r_sha).abs().mean())
    else:
        avg_abs_delta_rank = float("nan")

    # validation verdicts
    v_off = vs.read_status(STATUS_OFF)
    v_sha = vs.read_status(STATUS_SHA)

    # Filtered EV. The bucket column on forward-return history is Signal_Bucket
    # (see validation_builder.FORWARD_COLUMNS) and its values come from
    # assign_bucket. The previous filter named a column and a bucket value that
    # this engine never writes, so it was silently dropped and an UNFILTERED EV
    # was published under a filtered label. expected_value now fails closed on
    # any filter column it cannot find; this filter must stay in sync with
    # FORWARD_COLUMNS.
    EV_FILTER = {"Signal_Bucket": "Top Candidate"}
    fwd = _read(FWD)
    try:
        ev_off = ev.expected_value_per_day(fwd, v_off, horizon=10, filters=EV_FILTER)
    except Exception as exc:
        ev_off = {"ev_per_day": float("nan"),
                  "status": f"EV unavailable — {type(exc).__name__}"}

    # Shadow EV has no independent evidence base: there is no production writer
    # for validation_status_shadow.json, so no shadow forward-return cohort
    # exists. Report that fact rather than reusing the official cohort.
    ev_sha = {
        "ev_per_day": float("nan"),
        "status": "INSUFFICIENT_SHADOW_HISTORY",
        "missing_filter_columns": [],
    }

    # Recommendation. Only two outcomes are permitted while the shadow engine has
    # no independent validation history: keep running both, or insufficient data.
    # No champion, no promotion, no "better edge" — those require evidence that
    # does not exist yet.
    rec = "REVIEW: continue running both"
    notes = [
        "SHADOW STATUS: CURRENT-RANKING DIAGNOSTIC ONLY — no independent shadow "
        "validation history exists, so the shadow engine is not a validated "
        "challenger and cannot be promoted.",
    ]
    if str(ev_off.get("missing_filter_columns") or []):
        notes.append(
            f"Official EV filter unavailable: {ev_off.get('status')}"
        )

    summary = {
        "jaccard_top25": round(jaccard, 3),
        "overlap_top_n": top_n,
        "spearman_full": None if pd.isna(rho) else round(rho, 3),
        "avg_abs_delta_rank": None if pd.isna(avg_abs_delta_rank) else round(float(avg_abs_delta_rank), 2),
        "official_score_column": score_col_off,
        "shadow_score_column": score_col_sha,
        "verdict_official": v_off.get("verdict"),
        "verdict_shadow": v_sha.get("verdict"),
        "ev_filter": EV_FILTER,
        "ev_per_day_official": ev_off.get("ev_per_day"),
        "ev_status_official": ev_off.get("status"),
        "ev_per_day_shadow": ev_sha.get("ev_per_day"),
        "ev_status_shadow": ev_sha.get("status"),
        "shadow_governance": "CURRENT-RANKING DIAGNOSTIC ONLY",
        "recommendation": rec,
    }

    lines = [
        "# Shadow (v4.1) vs Official Engine",
        "",
        "> **SHADOW STATUS: CURRENT-RANKING DIAGNOSTIC ONLY.** No independent "
        "shadow validation history. Not a validated challenger. Not promotable.",
        "",
        f"- Compared columns — Official: `{score_col_off}` / Shadow: `{score_col_sha}`",
        f"- Top-{top_n} overlap (Jaccard): **{jaccard:.2f}**",
        f"- Full-rank Spearman ρ: **{summary['spearman_full']}**",
        f"- Validation — Official: **{v_off['verdict']}** / Shadow: **{v_sha['verdict']}**",
        f"- EV/day (filter {EV_FILTER}) — Official: **{ev_off.get('ev_per_day')}** "
        f"({ev_off.get('status')}) / Shadow: **{ev_sha.get('status')}**",
        "",
        f"## Recommendation\n\n> {rec}\n",
    ]
    if notes:
        lines += ["## Notes", *[f"- {n}" for n in notes]]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    pd.DataFrame([summary]).to_csv(CSV, index=False)
    (OUT / "shadow_vs_official.json").write_text(json.dumps(summary, indent=2))

    # ── append per-run ledger (fail-soft, idempotent by calendar date) ──
    # Read by the dashboard's shadow streak strip. Never raise — a broken
    # ledger append must not block the main report.
    try:
        from datetime import date as _date
        HIST = OUT / "shadow_vs_official_history.csv"
        beats = bool(
            (not pd.isna(ev_sha.get("ev_per_day"))) and
            (not pd.isna(ev_off.get("ev_per_day"))) and
            (ev_sha["ev_per_day"] > ev_off["ev_per_day"])
        )
        # "green" would mean the shadow engine had demonstrated an edge. It has
        # no independent validation history, so that state is unreachable by
        # construction rather than by luck.
        shadow_state = (
            "red" if v_off.get("verdict") == "Validation Negative" else "amber"
        )
        matured_obs = None
        try:
            stats_sha = v_sha.get("stats") or {}
            matured_obs = stats_sha.get("effective_validation_dates") \
                          or stats_sha.get("validation_dates")
        except Exception:
            matured_obs = None
        overlap_ct = len(top_off & top_sha)
        today_str = _date.today().isoformat()
        row = {
            "date": today_str,
            "verdict": v_off.get("verdict"),
            "shadow_state": shadow_state,
            "shadow_beats_official_net": beats,
            "shadow_matured_obs": matured_obs,
            "overlap": overlap_ct,
        }
        if HIST.exists():
            hist = pd.read_csv(HIST)
            hist = hist[hist["date"].astype(str) != today_str]
            hist = pd.concat([hist, pd.DataFrame([row])], ignore_index=True)
        else:
            hist = pd.DataFrame([row])
        hist.to_csv(HIST, index=False)
    except Exception:
        pass

    return summary


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, default=str))
