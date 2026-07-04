"""Compares official engine vs v4.1 shadow and prints a champion recommendation."""
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

    score_col_off = "Final_Score" if "Final_Score" in off.columns else "Opportunity_Score"
    score_col_sha = "Final_Score" if "Final_Score" in sha.columns else "Opportunity_Score"
    merged = off[["Symbol", score_col_off]].rename(columns={score_col_off: "score_off"}).merge(
        sha[["Symbol", score_col_sha]].rename(columns={score_col_sha: "score_sha"}),
        on="Symbol", how="inner",
    )

    top_n = 25
    top_off = set(off.nlargest(top_n, score_col_off)["Symbol"])
    top_sha = set(sha.nlargest(top_n, score_col_sha)["Symbol"])
    jaccard = len(top_off & top_sha) / max(len(top_off | top_sha), 1)
    rho = _spearman(merged["score_off"], merged["score_sha"])

    # validation verdicts
    v_off = vs.read_status(STATUS_OFF)
    v_sha = vs.read_status(STATUS_SHA)

    # filtered EV — top quintile only when validation positive
    fwd = _read(FWD)
    ev_off = ev.expected_value_per_day(fwd, v_off, horizon=10, filters={"Score_Bucket": "Top Quintile"})
    ev_sha = ev.expected_value_per_day(fwd, v_sha, horizon=10, filters={"Score_Bucket": "Top Quintile"})

    # recommendation
    rec = "REVIEW: continue running both"
    notes = []
    if v_off["verdict"] == v_sha["verdict"] == "Validation Positive":
        if not pd.isna(ev_sha["ev_per_day"]) and not pd.isna(ev_off["ev_per_day"]):
            if ev_sha["ev_per_day"] > ev_off["ev_per_day"] * 1.05:
                rec = "RECOMMEND: shadow leads on filtered EV/day — consider manual switch"
            elif ev_off["ev_per_day"] > ev_sha["ev_per_day"] * 1.05:
                rec = "RECOMMEND: official still leads on EV/day — keep current champion"
    elif v_sha["verdict"] == "Validation Positive" and v_off["verdict"] != "Validation Positive":
        notes.append("Shadow validated, official did not — investigate before switching.")

    summary = {
        "jaccard_top25": round(jaccard, 3),
        "spearman_full": None if pd.isna(rho) else round(rho, 3),
        "verdict_official": v_off.get("verdict"),
        "verdict_shadow": v_sha.get("verdict"),
        "ev_per_day_official": ev_off.get("ev_per_day"),
        "ev_per_day_shadow": ev_sha.get("ev_per_day"),
        "recommendation": rec,
    }

    lines = [
        "# Shadow (v4.1) vs Official Engine",
        "",
        f"- Top-{top_n} overlap (Jaccard): **{jaccard:.2f}**",
        f"- Full-rank Spearman ρ: **{summary['spearman_full']}**",
        f"- Validation — Official: **{v_off['verdict']}** / Shadow: **{v_sha['verdict']}**",
        f"- EV/day (Top Quintile) — Official: **{ev_off.get('ev_per_day')}** / Shadow: **{ev_sha.get('ev_per_day')}**",
        "",
        f"## Recommendation\n\n> {rec}\n",
    ]
    if notes:
        lines += ["## Notes", *[f"- {n}" for n in notes]]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    pd.DataFrame([summary]).to_csv(CSV, index=False)
    (OUT / "shadow_vs_official.json").write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, default=str))
