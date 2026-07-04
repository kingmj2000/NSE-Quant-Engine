"""Build a self-contained black + crimson glassmorphic HTML evidence-review dashboard.

Reads existing pipeline artifacts (latest_scores, trade_plan_latest,
score_bucket_performance, validation_status, shadow_vs_official, shadow_mode_summary,
dq_summary, etf_quality_latest, forward_return_history) and renders ONE HTML file
under output/dashboard_latest.html plus output/dashboard_<YYYY-MM-DD>.html.

No Python chart libraries. Chart.js is embedded into the generated HTML so the
dashboard works offline inside PySide6 QWebEngineView as well as in a browser.
"""
from __future__ import annotations
import json
import math
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
OUT = BASE / "output"
DATA = BASE / "data"
VENDOR = BASE / "vendor"

# Hard-coded governance veto list (kept here so dashboard is fully driven from
# this module; downstream engines can also import GOVERNANCE_VETO).
GOVERNANCE_VETO = {
    "ADANIENT", "ADANIGREEN", "ADANIPORTS", "ADANIPOWER", "ADANITRANS",
    "ADANIENSOL", "ADANITOTAL", "ATGL", "AWL", "ACC", "AMBUJACEM",
}


def _embedded_chart_js() -> str:
    """Return bundled Chart.js so QWebEngine dashboards work without CDN/network.

    QWebEngineView often blocks or races external CDN scripts for local file://
    dashboards. Embedding the runtime removes the `Chart is not defined` failure
    and keeps the same HTML portable to a browser.
    """
    local = VENDOR / "chart.umd.min.js"
    if local.exists():
        return local.read_text(encoding="utf-8", errors="replace")
    return "console.error('Bundled Chart.js missing: vendor/chart.umd.min.js');"


# ---------------------------------------------------------------- helpers ----
def _safe_read_csv(p: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(p) if p.exists() else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _safe_read_json(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        # tolerate the NaN literal that earlier reports wrote
        txt = p.read_text(encoding="utf-8").replace(": NaN", ": null")
        return json.loads(txt)
    except Exception:
        return {}


def _num(x, nd=2):
    try:
        if x is None:
            return None
        f = float(x)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, nd)
    except Exception:
        return None


def _veto_symbol(sym: str) -> bool:
    if not isinstance(sym, str):
        return False
    base = sym.replace(".NS", "").upper()
    return base in GOVERNANCE_VETO


# ---------------------------------------------------------------- payload ----
def _payload() -> dict:
    val = _safe_read_json(OUT / "validation_status.json")
    cmp_ = _safe_read_json(OUT / "shadow_vs_official.json")
    shadow_summary = _safe_read_json(OUT / "shadow_mode_summary.json")
    dq = _safe_read_json(DATA / "dq_summary.json")

    tp = _safe_read_csv(OUT / "trade_plan_latest.csv")
    scores = _safe_read_csv(OUT / "latest_scores.csv")
    shadow_scores = _safe_read_csv(OUT / "latest_scores_v4_shadow.csv")
    bucket = _safe_read_csv(OUT / "score_bucket_performance.csv")
    forward = _safe_read_csv(OUT / "forward_return_history.csv")
    etfq = _safe_read_csv(OUT / "etf_quality_latest.csv")
    if etfq.empty:
        etfq = _safe_read_csv(DATA / "etf_quality_latest.csv")

    # --- verdict / banner ---
    verdict = (val.get("verdict") or "Insufficient History")
    grade = (val.get("evidence_grade") or "Insufficient Evidence")
    stats = val.get("stats", {}) or {}

    rec = (cmp_.get("recommendation") or "REVIEW: continue running both")
    rec_low = rec.lower()
    shadow_state = ("green" if "shadow leads" in rec_low or "switch to shadow" in rec_low
                    else "red"   if "official still leads" in rec_low or "do not switch" in rec_low or verdict != "Validation Positive"
                    else "amber")

    decision_use = "LIVE" if verdict == "Validation Positive" else "WATCHLIST ONLY"
    bottom_line = (
        f"<b>{decision_use}.</b> Validation is <b>{verdict}</b>. "
        f"All entry / stop / target levels below are mechanical reference levels, "
        f"not recommendations. Shadow stays "
        f"{'🟢 GREEN' if shadow_state=='green' else '🔴 RED' if shadow_state=='red' else '🟡 AMBER'}."
    )

    # --- maturity metric cards (matured vs maturing) — filtered to 10-day slice ---
    matured = maturing = 0
    total_signals = 0
    if not forward.empty:
        fwd = forward
        if "Horizon_Days" in fwd.columns:
            try:
                fwd10 = fwd[fwd["Horizon_Days"] == 10]
                if not fwd10.empty:
                    fwd = fwd10
            except Exception:
                pass
        total_signals = int(len(fwd))
        if "Net_Forward_Return" in fwd.columns:
            matured = int(fwd["Net_Forward_Return"].notna().sum())
            maturing = int(fwd["Net_Forward_Return"].isna().sum())
        else:
            maturing = total_signals
    maturation_rate = round(100.0 * matured / total_signals, 1) if total_signals else 0.0


    # --- evidence tiles for 5D + 10D ---
    def _evidence(horizon: int) -> dict:
        # try the structured json first, fall back to detail csv aggregation
        s = stats
        if s.get("validation_dates") is not None and val.get("horizon_days") == horizon:
            return {
                "validation_dates": _num(s.get("validation_dates"), 0),
                "effective_validation_dates": _num(s.get("effective_validation_dates"), 1),
                "spread": _num(s.get("spread"), 4),
                "adj_tstat": _num(s.get("adj_tstat"), 2),
                "bootstrap_prob": _num(s.get("bootstrap_prob"), 2),
            }
        # fallback from cross_sectional_validation_detail by quintile median
        det = _safe_read_csv(OUT / "cross_sectional_validation_detail.csv")
        if det.empty or "Horizon_Days" not in det.columns:
            return {k: None for k in
                    ("validation_dates","effective_validation_dates","spread","adj_tstat","bootstrap_prob")}
        d = det[det["Horizon_Days"] == horizon]
        if d.empty:
            return {k: None for k in
                    ("validation_dates","effective_validation_dates","spread","adj_tstat","bootstrap_prob")}
        dates = d["Signal_Date"].nunique() if "Signal_Date" in d.columns else None
        q1 = d[d.get("Score_Quintile", "") == "Q1_Highest"]["Net_Forward_Return"].median()
        q5 = d[d.get("Score_Quintile", "") == "Q5_Lowest"]["Net_Forward_Return"].median()
        spread = (q1 - q5) if pd.notna(q1) and pd.notna(q5) else None
        return {
            "validation_dates": dates,
            "effective_validation_dates": None,
            "spread": _num(spread, 4),
            "adj_tstat": None,
            "bootstrap_prob": None,
        }

    evidence_10 = _evidence(10)
    evidence_5 = _evidence(5)

    # --- quintile chart (5D + 10D medians) ---
    quintile = {"5": [], "10": []}
    if not bucket.empty and "Bucket_Type" in bucket.columns:
        bq = bucket[bucket["Bucket_Type"] == "Score_Quintile"].copy()
        for h in (5, 10):
            sub = bq[bq["Horizon_Days"] == h]
            ordered = []
            for label in ("Q5_Lowest", "Q4", "Q3", "Q2", "Q1_Highest"):
                row = sub[sub["Bucket"] == label]
                ordered.append(_num(row["Median_Net_Return"].iloc[0] * 100, 3)
                               if not row.empty else None)
            quintile[str(h)] = ordered

    # --- shadow chip ---
    overlap = shadow_summary.get("top20_overlap_count")
    added = shadow_summary.get("v4_added_to_top20", []) or []
    dropped = shadow_summary.get("v4_dropped_from_top20", []) or []
    shadow_warnings = shadow_summary.get("warnings", []) or []
    shadow_reason_bits = []
    if verdict != "Validation Positive":
        shadow_reason_bits.append("official validation is not positive (Green requires it)")
    veto_in_shadow = [s for s in added if _veto_symbol(s)]
    if veto_in_shadow:
        shadow_reason_bits.append(
            f"shadow Top-20 pulls in governance-vetoed name(s): {', '.join(veto_in_shadow)}")
    if not shadow_reason_bits:
        shadow_reason_bits.append(rec)

    shadow = {
        "state": shadow_state,
        "chip": ("🟢 GREEN" if shadow_state == "green"
                 else "🔴 RED" if shadow_state == "red" else "🟡 AMBER"),
        "reason": "<b>Do not switch.</b> " + "; ".join(shadow_reason_bits) + "."
                  if shadow_state == "red"
                  else ("Shadow leads matured EV/day. " + rec) if shadow_state == "green"
                  else rec,
        "overlap": overlap,
        "added": len(added),
        "dropped": len(dropped),
        "added_symbols": added,
        "dropped_symbols": dropped,
        "warnings": shadow_warnings,
    }

    # --- candidate cards (top 5 post-veto) ---
    cards = []
    if not tp.empty:
        tp_clean = tp[~tp["Symbol"].apply(_veto_symbol)].copy()
        if "Final_Score" in tp_clean.columns:
            tp_clean = tp_clean.sort_values("Final_Score", ascending=False)
        for _, r in tp_clean.head(5).iterrows():
            rsi = _num(r.get("RSI_14"), 1)
            vol = _num((r.get("Volatility_20D") or 0) * 100, 1)
            flags = []
            if rsi is not None:
                if rsi >= 75:
                    flags.append(["red", "Technical", f"RSI {rsi} — strongly overbought"])
                elif rsi >= 70:
                    flags.append(["amber", "Technical", f"RSI {rsi} — overbought; pullback entry preferred"])
                elif vol is not None and vol >= 30:
                    flags.append(["amber", "Technical", f"Vol {vol}% — elevated"])
                else:
                    flags.append(["green", "Technical", f"RSI {rsi}, vol {vol}% — clean"])
            risk = str(r.get("Key_Risk", "") or "").strip()
            if risk and risk.lower() != "no major technical risk flagged":
                flags.append(["amber", "Risk", risk])
            reason = str(r.get("Reason", "") or "").strip()
            if reason:
                flags.append(["dim", "Why", reason])

            cards.append({
                "sym": r.get("Symbol"),
                "nm": r.get("Name"),
                "px": _num(r.get("Price")),
                "bzl": _num(r.get("Buy_Zone_Low")),
                "bzh": _num(r.get("Buy_Zone_High")),
                "stop": _num(r.get("Stop_Loss")),
                "t1": _num(r.get("Target_1")),
                "t2": _num(r.get("Target_2")),
                "nt1": _num(r.get("Net_Target_1_%")),
                "nt2": _num(r.get("Net_Target_2_%")),
                "pd1": _num(r.get("Net_Target_1_%_Per_Day_MinHold"), 3),
                "pd2": _num(r.get("Net_Target_2_%_Per_Day_MinHold"), 3),
                "hold": f"{int(r.get('Hold_Days_Min',5))}\u2013{int(r.get('Hold_Days_Max',15))}d",
                "edge": _num(r.get("Model_Edge_%_Per_Day"), 3),
                "label": "Watch only" if decision_use == "WATCHLIST ONLY" else "Live candidate",
                "clean": (rsi is not None and rsi < 70 and (vol or 0) < 30),
                "flags": flags,
            })

    # --- RSI / vol scatter ---
    scatter = []
    for c in cards:
        if c.get("flags"):
            # pull RSI / vol straight from the trade plan row (we just used it above)
            pass
    if not tp.empty:
        for _, r in tp[~tp["Symbol"].apply(_veto_symbol)].head(20).iterrows():
            rsi = _num(r.get("RSI_14"), 1); vol = _num((r.get("Volatility_20D") or 0) * 100, 1)
            if rsi is None or vol is None:
                continue
            scatter.append({"x": rsi, "y": vol, "s": str(r.get("Symbol","")).replace(".NS","")})

    # --- avoid list ---
    avoid = []
    if not tp.empty:
        # vetoed names that were in the universe
        for _, r in tp[tp["Symbol"].apply(_veto_symbol)].iterrows():
            avoid.append([r.get("Symbol"), r.get("Name") or "", "veto",
                          "Adani / governance veto — categorical exclude"])
        # overbought / hi-vol from top-30 post-veto
        for _, r in tp[~tp["Symbol"].apply(_veto_symbol)].head(30).iterrows():
            rsi = _num(r.get("RSI_14"), 1); vol = _num((r.get("Volatility_20D") or 0) * 100, 1)
            if rsi is not None and rsi >= 75:
                avoid.append([r.get("Symbol"), r.get("Name") or "", "rsi",
                              f"RSI {rsi} — strongly overbought"])
            elif vol is not None and vol >= 32:
                avoid.append([r.get("Symbol"), r.get("Name") or "", "vol",
                              f"Elevated volatility (~{vol}%)"])
    # dedupe, cap
    seen = set(); avoid_dedup = []
    for a in avoid:
        if a[0] in seen: continue
        seen.add(a[0]); avoid_dedup.append(a)
    avoid = avoid_dedup[:10]

    # --- shadow-only names (in shadow Top-20, not in official Top-20) ---
    shadow_only = []
    for sym in added:
        note = "governance veto — disqualifies shadow for switch" if _veto_symbol(sym) \
               else ("ETF — monitor only" if (".NS" in sym and sym.upper() != sym.upper().replace("BANK","BANK")) else "monitor only")
        kind = "veto" if _veto_symbol(sym) else "etf"
        shadow_only.append([sym, kind, note])

    # --- ETF / DQ notes ---
    dq_notes = {
        "rows": dq.get("rows"),
        "health": dq.get("health_score"),
        "actionable": dq.get("coverage_actionable", {}),
        "structural": dq.get("coverage_structural", {}),
        "flags": dq.get("flag_counts", {}),
        "maturing": maturing,
        "matured": matured,
    }

    # --- Excel-ready summary line ---
    date_str = datetime.now().strftime("%Y-%m-%d")
    top5_syms = [c["sym"] for c in cards]
    vetoed_present = sorted({r.get("Symbol") for _, r in tp.iterrows() if _veto_symbol(r.get("Symbol",""))}) if not tp.empty else []
    excel = (f"{date_str} | {verdict} / {grade} | {decision_use} | "
             f"{maturing} maturing | Top5 (post-veto): {', '.join(s.replace('.NS','') for s in top5_syms)} | "
             f"Vetoed: {', '.join(s.replace('.NS','') for s in vetoed_present) or 'none'} | "
             f"Shadow {shadow['chip']} | Recommendation: {rec}")


    # --- universe composition from config.csv (Nifty50 / Next50 / Midcap150 / ETF) ---
    universe_counts: dict = {}
    try:
        cfg = _safe_read_csv(BASE / "config.csv")
        if not cfg.empty and "Universe_Group" in cfg.columns:
            for k, v in cfg["Universe_Group"].value_counts().to_dict().items():
                universe_counts[str(k)] = int(v)
    except Exception:
        universe_counts = {}

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "date": date_str,
        "verdict": verdict,
        "grade": grade,
        "decision_use": decision_use,
        "bottom_line": bottom_line,
        "regime": (val.get("regime") or "Neutral"),
        "signal_count": maturing,
        "maturity": {"matured": matured, "maturing": maturing,
                     "total": total_signals, "rate": maturation_rate},

        "evidence_10": evidence_10,
        "evidence_5": evidence_5,
        "quintile": quintile,
        "shadow": shadow,
        "universe": universe_counts,
        "cards": cards,
        "scatter": scatter,
        "avoid": avoid,
        "shadow_only": shadow_only,
        "dq": dq_notes,
        "excel": excel,
    }


# ---------------------------------------------------------------- template ----
_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NSE Quant Evidence Review &mdash; __DATE__</title>
<script>__CHART_JS__</script>
<style>
:root{
  --bg:#09090D; --bg2:#111118;
  --panel:rgba(22,24,34,0.62); --panel2:rgba(28,30,42,0.45);
  --line:rgba(255,255,255,0.07); --line2:rgba(255,255,255,0.13);
  --txt:#ECEDEE; --muted:#8A92A6; --dim:#6B6F76;
  /* Accent system — crimson is reserved for the primary CTA / verdict header only.
     Category charts and per-panel glows use teal / blue / violet / amber / green. */
  --accent:#D8345F; --accent-soft:#FF6B8F; --accent-bg:rgba(216,52,95,0.15); --accent-deep:#8F1837;
  --accent2:#FF8A5C; --accent2-soft:#FFB193;
  --teal:#38BDB0; --teal-bg:rgba(56,189,176,0.14);
  --amber:#F2B13C; --amber-bg:rgba(242,177,60,0.12);
  --green:#3FB950; --green-bg:rgba(63,185,80,0.12);
  --red:#E5556A; --red-soft:#FF8597; --red-bg:rgba(229,85,106,0.14); --red-deep:#7A1A28;
  --blue:#58A6FF; --blue-soft:#9CC6FF; --blue-bg:rgba(88,166,255,0.14);
  --violet:#A371F7; --violet-soft:#C6A8FA; --violet-bg:rgba(163,113,247,0.14);
  /* Per-element glow — each glass panel inherits the glow that matches its accent. */
  --glow-primary:0 12px 44px -14px rgba(216,52,95,0.42);
  --glow-teal:   0 12px 44px -14px rgba(56,189,176,0.40);
  --glow-amber:  0 12px 44px -14px rgba(242,177,60,0.38);
  --glow-green:  0 12px 44px -14px rgba(63,185,80,0.36);
  --glow-blue:   0 12px 44px -14px rgba(88,166,255,0.40);
  --glow-violet: 0 12px 44px -14px rgba(163,113,247,0.40);
  --glow-neutral:0 10px 32px -14px rgba(0,0,0,0.55);
  --glow:var(--glow-neutral);
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:radial-gradient(1100px 600px at 85% -10%, rgba(88,166,255,0.14), transparent 60%),
  radial-gradient(900px 500px at -10% 10%, rgba(56,189,176,0.10), transparent 60%),
  linear-gradient(135deg,var(--bg) 0%,var(--bg2) 55%,#0C0F1A 100%);
  color:var(--txt);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;
  line-height:1.45;font-size:14px;min-height:100vh}
body{padding:22px;max-width:1220px;margin:0 auto}
h1{font-size:22px;font-weight:700;letter-spacing:.2px;
  background:linear-gradient(90deg,#fff 0%,var(--accent-soft) 58%,var(--accent2-soft) 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}

h2{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:1.4px;color:var(--muted);margin:28px 0 12px}
.sub{color:var(--dim);font-size:12px;margin-top:3px}

.glass{background:var(--panel);border:1px solid var(--line);border-radius:16px;
  backdrop-filter:blur(18px) saturate(140%);box-shadow:var(--glow)}
.glass.g-teal   {box-shadow:var(--glow-teal);   border-color:rgba(56,189,176,0.30)}
.glass.g-amber  {box-shadow:var(--glow-amber);  border-color:rgba(242,177,60,0.30)}
.glass.g-green  {box-shadow:var(--glow-green);  border-color:rgba(63,185,80,0.30)}
.glass.g-blue   {box-shadow:var(--glow-blue);   border-color:rgba(88,166,255,0.30)}
.glass.g-violet {box-shadow:var(--glow-violet); border-color:rgba(163,113,247,0.30)}
.glass.g-primary{box-shadow:var(--glow-primary);border-color:rgba(216,52,95,0.30)}
.panel{padding:16px}

.bottomline{background:linear-gradient(90deg,var(--accent-bg),transparent);
  border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:12px;
  padding:14px 16px;margin:14px 0 4px;font-size:15px;font-weight:500;
  backdrop-filter:blur(12px)}
.bottomline b{color:var(--accent-soft)}

.banner{display:flex;flex-wrap:wrap;gap:14px;align-items:stretch;margin-top:14px}
.verdict{flex:1;min-width:280px;
  background:linear-gradient(135deg,rgba(110,139,255,0.18),rgba(142,123,255,0.10));
  border:1px solid rgba(110,139,255,0.45);border-radius:14px;padding:16px 18px;
  backdrop-filter:blur(18px);box-shadow:var(--glow)}
.verdict.green{background:linear-gradient(135deg,rgba(63,185,80,0.18),rgba(20,80,40,0.10));border-color:rgba(63,185,80,0.45)}
.verdict.amber{background:linear-gradient(135deg,rgba(242,177,60,0.20),rgba(229,85,106,0.12));border-color:rgba(242,177,60,0.45)}
.verdict .v{font-size:20px;font-weight:700;color:var(--accent-soft);margin-top:2px}
.verdict.green .v{color:var(--green)} .verdict.amber .v{color:var(--amber)}

.pillrow{display:flex;gap:9px;flex-wrap:wrap;margin-top:10px}
.pill{background:var(--panel2);border:1px solid var(--line);border-radius:999px;
  padding:5px 12px;font-size:11.5px;color:var(--muted);backdrop-filter:blur(8px)}
.pill b{color:var(--txt)}

.grid{display:grid;gap:14px} .twocol{grid-template-columns:1fr 1fr}
@media(max-width:900px){.twocol{grid-template-columns:1fr}}

.evid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:8px}
@media(max-width:760px){.evid{grid-template-columns:repeat(2,1fr)}}
.tile{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px;backdrop-filter:blur(10px)}
.tile .k{font-size:10.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px;height:26px}
.tile .val{font-size:20px;font-weight:700;margin-top:4px}
.tag{display:inline-block;font-size:10px;font-weight:650;padding:2px 7px;border-radius:5px;margin-top:6px}
.t-thin{background:var(--amber-bg);color:var(--amber)} .t-build{background:var(--accent-bg);color:var(--accent-soft)} .t-ok{background:var(--green-bg);color:var(--green)}

.shadow-row{display:flex;flex-wrap:wrap;gap:14px;align-items:center;
  background:linear-gradient(135deg,rgba(110,139,255,0.18),rgba(142,123,255,0.10));
  border:1px solid rgba(110,139,255,0.45);border-radius:14px;padding:14px 16px;
  backdrop-filter:blur(18px);box-shadow:var(--glow)}
.shadow-row.green{background:linear-gradient(135deg,rgba(56,189,176,0.20),rgba(20,80,70,0.20));border-color:rgba(56,189,176,0.50)}
.shadow-row.amber{background:linear-gradient(135deg,rgba(242,177,60,0.18),rgba(80,60,20,0.30));border-color:rgba(242,177,60,0.50)}
.chip{font-size:13px;font-weight:700;padding:6px 14px;border-radius:999px;
  background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;white-space:nowrap;
  box-shadow:0 4px 18px -4px rgba(110,139,255,0.55)}
.shadow-row.green .chip{background:linear-gradient(135deg,#3FCBB8,#1B6A60)}
.shadow-row.amber .chip{background:linear-gradient(135deg,#E0A030,#7A5710)}
.shadow-row .reason{flex:1;min-width:240px;font-size:12.5px;color:var(--txt)}
.ovl{display:flex;gap:9px;flex-wrap:wrap}

.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px;
  border-top:3px solid var(--blue);backdrop-filter:blur(18px);box-shadow:var(--glow-blue);transition:transform .2s}
.card.clean{border-top-color:var(--teal);box-shadow:var(--glow-teal)}
.card.warn{border-top-color:var(--amber);box-shadow:var(--glow-amber)}
.card.risk{border-top-color:var(--red);box-shadow:0 12px 44px -14px rgba(229,85,106,0.32)}
.card:hover{transform:translateY(-2px)}
.card.clean{border-top-color:var(--teal)}

.card .top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.card .sym{font-size:16px;font-weight:700}
.card .nm{font-size:11px;color:var(--dim)}
.card .px{text-align:right} .card .px .lbl{font-size:10px;color:var(--dim)} .card .px .p{font-size:16px;font-weight:700}
.lblchip{font-size:10px;font-weight:700;padding:3px 9px;border-radius:6px;background:var(--red-bg);color:var(--red-soft);white-space:nowrap}
.lblchip.review{background:var(--green-bg);color:var(--green)}
.levels{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:11px}
.lv{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:7px 9px}
.lv .l{font-size:9.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.4px}
.lv .n{font-size:13px;font-weight:640;margin-top:1px}
.lv.stop .n{color:var(--red-soft)} .lv.t .n{color:var(--green)}
.perday{display:flex;gap:8px;margin-top:9px;flex-wrap:wrap}
.pd{flex:1;min-width:90px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:6px 8px;text-align:center}
.pd .l{font-size:9px;color:var(--dim)} .pd .n{font-size:13px;font-weight:650}
.pd.edge .n{color:var(--dim)}
.flags{margin-top:10px;display:flex;flex-direction:column;gap:5px}
.flag{font-size:11px;color:var(--muted)} .flag b{color:var(--txt)}
.fdot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:middle}
.d-red{background:var(--red)} .d-amber{background:var(--amber)} .d-green{background:var(--green)} .d-dim{background:var(--dim)}

table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:4px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-size:10.5px;text-transform:uppercase;letter-spacing:.5px;font-weight:600}
td .rsym{font-weight:640}
.rchip{font-size:10px;font-weight:650;padding:2px 7px;border-radius:5px}
.rc-veto{background:var(--red-bg);color:var(--red-soft)} .rc-rsi{background:var(--amber-bg);color:var(--amber)}
.rc-vol{background:rgba(163,113,247,.14);color:var(--violet)} .rc-etf{background:rgba(88,166,255,.12);color:var(--blue)}

.caption{background:var(--panel2);border:1px dashed var(--line2);border-radius:10px;padding:11px 14px;font-size:11.5px;color:var(--muted);margin-top:12px}
.caption b{color:var(--txt)}

.foot{margin-top:26px;padding:14px;border:1px solid var(--line);border-radius:12px;
  font-size:11px;color:var(--dim);background:var(--panel2);backdrop-filter:blur(10px)}
.foot code{color:var(--txt);background:transparent}
canvas{margin-top:4px}
.chart-error{margin-top:10px;padding:12px;border:1px dashed rgba(255,255,255,.14);border-radius:10px;color:var(--amber);background:rgba(242,177,60,.08);font-size:12px}
</style></head>
<body>

<h1>NSE Quant Evidence Review</h1>
<div class="sub">Daily run &middot; Generated __GENERATED__ &middot; expanded NSE stock universe / NSE ETFs &middot; cross-sectional validation report is the authority</div>

<div class="bottomline" id="bottomline"></div>

<div class="banner" id="banner"></div>

<h2>Signal maturation &amp; universe composition</h2>
<div class="grid twocol">
  <div class="glass g-teal panel">
    <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Signal maturation (10-day horizon)</div>
    <div class="evid" id="maturityCards" style="grid-template-columns:1fr 1fr"></div>
    <canvas id="maturityBar" height="80" style="margin-top:10px"></canvas>
    <div class="sub" id="maturityNote" style="margin-top:8px"></div>
  </div>
  <div class="glass g-violet panel">
    <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Universe composition &amp; evidence gauge</div>
    <canvas id="universeChart" height="150"></canvas>
    <div class="evid" id="evidence10" style="grid-template-columns:repeat(5,1fr);margin-top:10px"></div>
    <div class="sub" id="evidenceNote" style="margin-top:10px"></div>
  </div>
</div>

<h2>Shadow vs Official &mdash; ranking overlap</h2>
<div class="glass g-blue panel">
  <div class="evid" id="shadowCards" style="grid-template-columns:repeat(4,1fr);margin-bottom:10px"></div>
  <canvas id="shadowBar" height="70"></canvas>
  <div class="sub" id="shadowWarnings" style="margin-top:8px"></div>
</div>

<h2>Quintile median net return &mdash; 10-day (medians, not means)</h2>
<div class="glass panel">
  <canvas id="quintileChart" height="118"></canvas>
  <div class="sub" style="margin-top:10px">Read on <b>medians</b>: at low effective sample sizes any quintile inversion is <b>noise, not model failure</b>. Means are intentionally discarded — outlier records inflate them.</div>
</div>


<h2>Top 5 watchlist candidates &mdash; post-governance veto</h2>
<div class="cards" id="cards"></div>
<div class="caption">
  <b>Target-per-day</b> = best case <b>IF</b> the target is hit within the hold window; ignores the stop and hit rate — mechanical ceiling, not expected return.
  <b>Model edge/day</b> = measured expected edge after costs — blank ("&mdash;") until validation is positive. Adani group names are categorically vetoed before any scoring or watchlist inclusion.
</div>

<h2>Timing filter map &mdash; RSI vs 20-day volatility</h2>
<div class="glass panel">
  <canvas id="scatterChart" height="150"></canvas>
  <div class="sub" style="margin-top:10px">RSI is a timing filter, not a valuation filter. Right of the dashed line (RSI ~ 70-73) = overbought entry risk; above the upper band (vol ~ 30%) = elevated volatility.</div>
</div>

<h2>Avoid / downgrade for now</h2>
<div class="glass panel">
  <table><thead><tr><th>Symbol</th><th>Name</th><th>Reason</th></tr></thead>
  <tbody id="avoidBody"></tbody></table>
</div>

<h2>Shadow-only names to watch (in shadow Top-20, not official)</h2>
<div class="glass panel">
  <table><thead><tr><th>Symbol</th><th>Note</th></tr></thead>
  <tbody id="shadowOnlyBody"></tbody></table>
  <div class="sub" id="shadowDroppedNote" style="margin-top:8px"></div>
</div>

<h2>ETF data gaps &amp; data-quality notes</h2>
<div class="grid twocol">
  <div class="glass panel">
    <div style="font-weight:600;margin-bottom:6px">ETF segment</div>
    <div class="sub" id="etfNote"></div>
  </div>
  <div class="glass panel">
    <div style="font-weight:600;margin-bottom:6px">Data quality</div>
    <div class="sub" id="dqNote"></div>
  </div>
</div>

<div class="foot">
  Excel-ready summary: <code id="excel"></code><br><br>
  Personal screening &amp; validation tool &mdash; <b>not financial advice</b> and not a substitute for a SEBI-registered adviser.
  All numbers pulled directly from the engine output files; nothing estimated.
</div>

<script id="DATA-JSON" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("DATA-JSON").textContent);
const fmt = (v, suffix="", nd=2) => (v===null||v===undefined||Number.isNaN(v)) ? "&mdash;" : (Number(v).toFixed(nd)+suffix);
const num = v => (v===null||v===undefined) ? "&mdash;" : Number(v).toLocaleString('en-IN');

// bottom line + banner
document.getElementById("bottomline").innerHTML = DATA.bottom_line;
const vClass = DATA.verdict==="Validation Positive" ? "green"
              : DATA.verdict.startsWith("Insufficient") ? "" : "amber";
document.getElementById("banner").innerHTML = `
 <div class="verdict ${vClass}">
   <div style="font-size:10.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.6px">Validation Verdict</div>
   <div class="v">${DATA.verdict}</div>
   <div style="font-size:12px;color:var(--muted);margin-top:2px">Evidence grade: ${DATA.grade}</div>
   <div class="pillrow">
     <span class="pill"><b>${num(DATA.signal_count)}</b> signals maturing</span>
     <span class="pill">Market regime: <b>${DATA.regime}</b></span>
     <span class="pill">Mode: <b>${DATA.decision_use}</b></span>
   </div>
 </div>`;

// evidence tiles
function tagFor(field, val10){
  if (val10===null||val10===undefined) return ["t-thin","Thin"];
  if (field==="spread")          return val10>0.01 ? ["t-ok","Strong"] : val10>0 ? ["t-build","Building"] : ["t-thin","Inverted"];
  if (field==="validation_dates")return val10>=20 ? ["t-ok","Sufficient"] : val10>=10 ? ["t-build","Building"] : ["t-thin","Thin"];
  if (field==="effective_validation_dates") return val10>=5 ? ["t-ok","Sufficient"] : val10>=2 ? ["t-build","Building"] : ["t-thin","Thin"];
  if (field==="adj_tstat")       return val10>=2 ? ["t-ok","Strong"] : val10>=1 ? ["t-build","Building"] : ["t-thin","Thin"];
  if (field==="bootstrap_prob")  return val10>=0.9 ? ["t-ok","Strong"] : val10>=0.7 ? ["t-build","Building"] : ["t-thin","Thin"];
  return ["t-thin","Thin"];
}
const evLabels = [
  ["validation_dates","Validation Dates",0],
  ["effective_validation_dates","Effective Val. Dates",1],
  ["spread","Top&minus;Bottom Quintile",4],
  ["adj_tstat","Adj. t-stat",2],
  ["bootstrap_prob","Bootstrap P(+)",2],
];
document.getElementById("evidence10").innerHTML = evLabels.map(([k,label,nd])=>{
  const v = DATA.evidence_10[k];
  const [cls, tagTxt] = tagFor(k, v);
  const display = (v===null||v===undefined) ? "&mdash;" : Number(v).toFixed(nd);
  return `<div class="tile"><div class="k">${label}</div><div class="val">${display}</div><span class="tag ${cls}">${tagTxt}</span></div>`;
}).join("");

const e5 = DATA.evidence_5;
document.getElementById("evidenceNote").innerHTML =
  `5-day horizon: ${fmt(e5.validation_dates,'',0)} dates, eff. ${fmt(e5.effective_validation_dates,'',1)}, spread ${fmt(e5.spread,'',4)}, t-stat ${fmt(e5.adj_tstat,'',2)}, bootstrap ${fmt(e5.bootstrap_prob,'',2)}.`;
document.getElementById("maturityNote").innerHTML =
  `${num(DATA.maturity.matured)} signals matured, ${num(DATA.maturity.maturing)} still maturing. A huge maturing pool early in accumulation is normal — not a data fault.`;

// charts — guarded so one chart cannot break the whole dashboard.
function chartError(canvas, msg){
  const el = typeof canvas === 'string' ? document.getElementById(canvas) : canvas;
  if(!el) return;
  el.insertAdjacentHTML('afterend', `<div class="chart-error">Chart unavailable: ${msg}</div>`);
}
function safeChart(id, config){
  try{
    if(typeof Chart === 'undefined') throw new Error('embedded Chart.js did not initialize');
    const el = document.getElementById(id);
    if(!el) return null;
    return new Chart(el, config);
  }catch(err){
    console.error(`Chart render failed for ${id}:`, err);
    chartError(id, err && err.message ? err.message : String(err));
    return null;
  }
}
if(typeof Chart !== 'undefined'){
  Chart.defaults.color="#9BA1A6"; Chart.defaults.font.size=11;
  Chart.defaults.font.family="-apple-system,Segoe UI,Inter,sans-serif";
}
const grid={color:"rgba(255,255,255,0.06)"};


// Maturity KPI cards (replaces the crimson matured/maturing donut).
document.getElementById("maturityCards").innerHTML = `
  <div class="tile"><div class="k">Matured</div><div class="val" style="color:var(--teal)">${num(DATA.maturity.matured)}</div><span class="tag t-ok">forward return known</span></div>
  <div class="tile"><div class="k">Awaiting maturation</div><div class="val" style="color:var(--blue-soft)">${num(DATA.maturity.maturing)}</div><span class="tag t-build">horizon not yet elapsed</span></div>`;

safeChart("maturityBar",{
  type:"bar",
  data:{labels:["Signal pool"], datasets:[
    {label:"Matured", data:[DATA.maturity.matured||0], backgroundColor:"#38BDB0", stack:"a", borderRadius:4},
    {label:"Awaiting", data:[DATA.maturity.maturing||0], backgroundColor:"rgba(88,166,255,0.55)", stack:"a", borderRadius:4},
  ]},
  options:{indexAxis:"y", plugins:{legend:{position:"bottom",labels:{boxWidth:10,padding:10}}},
    scales:{x:{stacked:true,grid:{color:"rgba(255,255,255,0.06)"}},y:{stacked:true,grid:{display:false}}}}
});

// Universe composition donut — uses teal/blue/violet/amber, never crimson.
const uni = DATA.universe || {};
const uniLabels = Object.keys(uni);
const uniVals   = uniLabels.map(k => uni[k]);
safeChart("universeChart",{
  type:"doughnut",
  data:{labels:uniLabels, datasets:[{data:uniVals.length?uniVals:[1],
    backgroundColor:["#38BDB0","#58A6FF","#A371F7","#F2B13C","#7FE0C6","#9CC6FF"].slice(0,Math.max(uniLabels.length,1)),
    borderColor:"#0B0B0F", borderWidth:2}]},
  options:{cutout:"62%", plugins:{legend:{position:"bottom",labels:{boxWidth:11,padding:12}}}}
});

// Shadow vs official — KPI cards + horizontal stacked bar (replaces crimson donut).
const sh = DATA.shadow || {};
document.getElementById("shadowCards").innerHTML = `
  <div class="tile"><div class="k">Top-20 overlap</div><div class="val" style="color:var(--teal)">${sh.overlap ?? '—'} / 20</div><span class="tag t-ok">common names</span></div>
  <div class="tile"><div class="k">Shadow added</div><div class="val" style="color:var(--blue-soft)">${sh.added ?? 0}</div><span class="tag t-build">new to shadow Top-20</span></div>
  <div class="tile"><div class="k">Shadow dropped</div><div class="val" style="color:var(--violet-soft)">${sh.dropped ?? 0}</div><span class="tag t-build">absent in shadow Top-20</span></div>
  <div class="tile"><div class="k">Regime</div><div class="val" style="color:var(--amber)">${sh.chip ?? '—'}</div><span class="tag t-thin">${DATA.decision_use}</span></div>`;

const _ov = sh.overlap ?? 0, _ad = sh.added ?? 0, _dr = sh.dropped ?? 0;
safeChart("shadowBar",{
  type:"bar",
  data:{labels:["Ranking overlap"], datasets:[
    {label:"Common", data:[_ov], backgroundColor:"#38BDB0", stack:"s", borderRadius:4},
    {label:"Shadow only", data:[_ad], backgroundColor:"#58A6FF", stack:"s", borderRadius:4},
    {label:"Official only", data:[_dr], backgroundColor:"#A371F7", stack:"s", borderRadius:4},
  ]},
  options:{indexAxis:"y", plugins:{legend:{position:"bottom",labels:{boxWidth:10,padding:10}}},
    scales:{x:{stacked:true,grid:{color:"rgba(255,255,255,0.06)"}},y:{stacked:true,grid:{display:false}}}}
});
document.getElementById("shadowWarnings").innerHTML =
  (sh.warnings && sh.warnings.length) ? "Shadow neutralizations: " + sh.warnings.map(w=>`<span class="pill">${w}</span>`).join(" ")
                                      : "";

const q10 = DATA.quintile["10"];
safeChart("quintileChart",{
  type:"bar",
  data:{labels:["Q5 Lowest","Q4","Q3","Q2","Q1 Highest"],
    datasets:[{label:"Median net return 10D (%)",data:q10,
      backgroundColor:q10.map(v => v===null ? "rgba(255,255,255,0.08)" : v>=0.5 ? "#3FB950" : v>=0 ? "#38BDB0" : "#F2B13C"),
      borderRadius:6,barPercentage:.72}]},
  options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>(c.parsed.y??'—')+"% median"}}},
    scales:{y:{grid,title:{display:true,text:"% median net return"}},x:{grid:{display:false}}}}
});

// scatter
const band={id:"band",beforeDraw(ch){const{ctx,chartArea:a,scales:{x,y}}=ch;if(!a)return;ctx.save();
  const rsi=x.getPixelForValue(71.5);ctx.fillStyle="rgba(242,177,60,.07)";ctx.fillRect(rsi,a.top,a.right-rsi,a.bottom-a.top);
  const vol=y.getPixelForValue(30);ctx.fillStyle="rgba(163,113,247,.07)";ctx.fillRect(a.left,a.top,a.right-a.left,vol-a.top);
  ctx.setLineDash([5,4]);ctx.strokeStyle="#F2B13C";ctx.beginPath();ctx.moveTo(rsi,a.top);ctx.lineTo(rsi,a.bottom);ctx.stroke();
  ctx.strokeStyle="#A371F7";ctx.beginPath();ctx.moveTo(a.left,vol);ctx.lineTo(a.right,vol);ctx.stroke();ctx.restore();}};
safeChart("scatterChart",{
  type:"scatter",plugins:[band],
  data:{datasets:[{label:"Top-20 candidates",data:DATA.scatter,pointRadius:7,pointHoverRadius:9,
    backgroundColor:c=>{const d=c.raw;return (d&&(d.x>=71||d.y>=30))?"#F2B13C":"#3FB950";},
    borderColor:"#0B0B0F",borderWidth:1.5}]},
  options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>`${c.raw.s}: RSI ${c.raw.x}, vol ${c.raw.y}%`}}},
    scales:{x:{title:{display:true,text:"RSI(14)"},grid,min:30,max:90},
            y:{title:{display:true,text:"20D vol (%)"},grid,min:0,suggestedMax:45}}}
});

// candidate cards
const dotc={red:"d-red",amber:"d-amber",green:"d-green",dim:"d-dim"};
document.getElementById("cards").innerHTML = (DATA.cards||[]).map(c=>`
 <div class="card ${c.clean?'clean':''}">
   <div class="top">
     <div><div class="sym">${c.sym}</div><div class="nm">${c.nm||''}</div></div>
     <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">
       <div class="px"><div class="lbl">Price</div><div class="p">&#8377;${num(c.px)}</div></div>
       <span class="lblchip ${c.clean?'review':''}">${c.label}</span>
     </div>
   </div>
   <div class="levels">
     <div class="lv"><div class="l">Buy zone</div><div class="n">${num(c.bzl)}&ndash;${num(c.bzh)}</div></div>
     <div class="lv stop"><div class="l">Stop</div><div class="n">${num(c.stop)}</div></div>
     <div class="lv"><div class="l">Hold</div><div class="n">${c.hold}</div></div>
     <div class="lv t"><div class="l">Target 1</div><div class="n">${num(c.t1)}</div></div>
     <div class="lv t"><div class="l">Target 2</div><div class="n">${num(c.t2)}</div></div>
     <div class="lv"><div class="l">Net T1 / T2</div><div class="n">${fmt(c.nt1,'%',2)} / ${fmt(c.nt2,'%',2)}</div></div>
   </div>
   <div class="perday">
     <div class="pd"><div class="l">T1 %/day</div><div class="n">${fmt(c.pd1,'',3)}</div></div>
     <div class="pd"><div class="l">T2 %/day</div><div class="n">${fmt(c.pd2,'',3)}</div></div>
     <div class="pd edge"><div class="l">Model edge/day</div><div class="n">${c.edge==null?'&mdash;':fmt(c.edge,'',3)}</div></div>
   </div>
   <div class="flags">${(c.flags||[]).map(f=>`<div class="flag"><span class="fdot ${dotc[f[0]]||'d-dim'}"></span><b>${f[1]}:</b> ${f[2]}</div>`).join('')}</div>
 </div>`).join("") || `<div class="glass panel"><div class="sub">No trade-plan output yet — run the pipeline.</div></div>`;

// avoid
const rcls={veto:"rc-veto",rsi:"rc-rsi",vol:"rc-vol",etf:"rc-etf"};
const rtxt={veto:"GOVERNANCE VETO",rsi:"OVERBOUGHT RSI",vol:"ELEVATED VOL",etf:"ETF GAP"};
document.getElementById("avoidBody").innerHTML = (DATA.avoid||[]).map(a=>`
 <tr><td class="rsym">${a[0]}</td><td style="color:var(--muted)">${a[1]}</td>
 <td><span class="rchip ${rcls[a[2]]||'rc-vol'}">${rtxt[a[2]]||a[2].toUpperCase()}</span> <span style="color:var(--muted);font-size:11.5px">${a[3]}</span></td></tr>`).join("") || `<tr><td colspan="3" class="sub">No avoids flagged.</td></tr>`;

// shadow-only
document.getElementById("shadowOnlyBody").innerHTML = (DATA.shadow_only||[]).map(a=>`
 <tr><td class="rsym">${a[0]}</td><td><span class="rchip ${rcls[a[1]]||'rc-etf'}">${(rtxt[a[1]]||a[1]).toUpperCase()}</span> <span style="color:var(--muted);font-size:11.5px">${a[2]}</span></td></tr>`).join("")
 || `<tr><td colspan="2" class="sub">Shadow Top-20 matches official.</td></tr>`;
document.getElementById("shadowDroppedNote").innerHTML = DATA.shadow.dropped_symbols && DATA.shadow.dropped_symbols.length
  ? `Shadow dropped from official Top-20: ${DATA.shadow.dropped_symbols.join(", ")}. Comparison only — shadow never overrides the official ranking.`
  : "";

// DQ
const dq = DATA.dq || {};
const actionable = Object.entries(dq.actionable||{}).map(([k,v])=>`${k} ${(v*100).toFixed(0)}%`).join(" &middot; ");
const structural = Object.entries(dq.structural||{}).map(([k,v])=>`${k} ${(v*100).toFixed(0)}% <i>(source-limited)</i>`).join(" &middot; ");
document.getElementById("etfNote").innerHTML =
  `${dq.rows ?? '—'} ETF rows analysed. Actionable coverage: ${actionable||'—'}. ${structural? "Structural / source-limited: "+structural+"." : ""}`;
document.getElementById("dqNote").innerHTML = `
  &bull; <b>${num(dq.maturing||0)}</b> signals "forward horizon not matured yet" &mdash; expected accumulation, not corruption.<br>
  &bull; Medians used as the authoritative read &mdash; bucket means can be contaminated by outliers.<br>
  &bull; Health score: <b>${dq.health ?? '—'} / 100</b>.`;

// excel summary
document.getElementById("excel").textContent = DATA.excel;
</script>
</body></html>
"""


# ---------------------------------------------------------------- public ----
def build() -> Path:
    OUT.mkdir(exist_ok=True)
    payload = _payload()
    html = (_TEMPLATE
            .replace("__DATE__", payload["date"])
            .replace("__GENERATED__", payload["generated_at"])
            .replace("__CHART_JS__", _embedded_chart_js())
            .replace("__DATA__", json.dumps(payload, default=str)))
    latest = OUT / "dashboard_latest.html"
    dated = OUT / f"dashboard_{payload['date']}.html"
    latest.write_text(html, encoding="utf-8")
    dated.write_text(html, encoding="utf-8")
    print(f"dashboard written: {latest}  +  {dated.name}")
    return latest


if __name__ == "__main__":
    build()
