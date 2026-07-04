"""Builds output/dq_report.md and data/dq_metrics.csv from enriched ETF metadata."""
from __future__ import annotations
from pathlib import Path
import json
import pandas as pd

from core import data_quality as dq

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
OUT = BASE / "output"
OUT.mkdir(exist_ok=True)

ENRICHED = DATA / "etf_metadata_enriched.csv"
QUALITY = DATA / "etf_quality_latest.csv"
METRICS = DATA / "dq_metrics.csv"
REPORT = OUT / "dq_report.md"


def _load() -> pd.DataFrame:
    # QUALITY has the canonical columns (NAV, TER, Mapping_Status, …); enriched is fallback.
    if QUALITY.exists():
        return pd.read_csv(QUALITY)
    if ENRICHED.exists():
        return pd.read_csv(ENRICHED)
    return pd.DataFrame()


def build() -> dict:
    df = _load()
    if df.empty:
        REPORT.write_text("# DQ Report\n\nNo enriched ETF metadata yet — run the pipeline first.\n")
        return {"health_score": 0.0, "rows": 0}

    df = dq.annotate(df)
    breakdown = dq.coverage_breakdown(df)
    score = dq.health_score(df)

    flag_counts = {f: int(df[f"Flag_{f}"].sum()) for f in dq.FLAGS if f"Flag_{f}" in df.columns}
    unresolved = df[df.get("Flag_UNRESOLVED_MAPPING", False)].head(20) if "Flag_UNRESOLVED_MAPPING" in df.columns else df.head(0)

    # source mix
    src_cols = [c for c in df.columns if c.endswith("_Source")]
    src_mix = {c: df[c].fillna("(missing)").value_counts().head(5).to_dict() for c in src_cols}

    # write metrics
    rows = ([{"field": k, "fill_rate": round(v, 3), "bucket": "actionable"}
             for k, v in breakdown["actionable"].items()]
          + [{"field": k, "fill_rate": round(v, 3), "bucket": "structural"}
             for k, v in breakdown["structural"].items()]
          + [{"field": f"flag_{k}", "count": v} for k, v in flag_counts.items()])
    pd.DataFrame(rows).to_csv(METRICS, index=False)

    def _tbl(title, rates):
        out = [f"## {title}", "", "| Field | Fill rate |", "|---|---|"]
        for k, v in rates.items():
            out.append(f"| {k} | {v*100:.1f}% |")
        out.append("")
        return out

    lines = [
        "# Data Quality Report",
        "",
        f"- Rows: **{len(df)}**",
        f"- Data Health Score (actionable coverage only): **{score} / 100**",
        "",
        "> Tracking_Error / Tracking_Difference are excluded from the headline "
        "score because AMFI does not publish them for most NSE ETFs. They are "
        "reported separately under *Structural coverage*.",
        "",
    ]
    lines += _tbl("Actionable coverage", breakdown["actionable"])
    lines += _tbl("Structural coverage (source-limited)", breakdown["structural"])
    lines += ["## Flag distribution", "", "| Flag | Count | Type |", "|---|---|---|"]
    for k, v in flag_counts.items():
        bucket = ("structural" if k in dq.STRUCTURAL_FLAGS
                  else "actionable" if k in dq.ACTIONABLE_FLAGS else "info")
        lines.append(f"| {k} | {v} | {bucket} |")
    lines += ["", "## Source mix (top 5 per source column)", ""]
    for c, m in src_mix.items():
        lines.append(f"**{c}**")
        for k, v in m.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
    if not unresolved.empty:
        lines += ["## Top unresolved mappings", "", "| Symbol | Name | Mapping_Status |", "|---|---|---|"]
        for _, r in unresolved.iterrows():
            lines.append(f"| {r.get('Symbol','')} | {r.get('Name','')} | {r.get('Mapping_Status','')} |")

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    summary = {
        "health_score": score,
        "rows": int(len(df)),
        "flag_counts": flag_counts,
        "coverage_actionable": {k: round(v, 4) for k, v in breakdown["actionable"].items()},
        "coverage_structural": {k: round(v, 4) for k, v in breakdown["structural"].items()},
    }
    (DATA / "dq_summary.json").write_text(json.dumps(summary, indent=2))
    return summary



if __name__ == "__main__":
    s = build()
    print(json.dumps(s, indent=2))
