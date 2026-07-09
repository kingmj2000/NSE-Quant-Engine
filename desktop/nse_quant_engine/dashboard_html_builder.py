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


def _norm_sym(sym: str) -> str:
    return str(sym or "").replace(".NS", "").upper().strip()


def _cfg(name: str, default):
    """Look up a core.config constant with a safe default. Never raises."""
    try:
        from core import config as _C
        return getattr(_C, name, default)
    except Exception:
        return default


# ─── verdict fallback + progress ──────────────────────────────────────────────
_VALID_VERDICTS = (
    "Validation Positive", "Validation Negative", "No Proven Edge Yet",
    "Insufficient Statistical Evidence", "Insufficient Breadth",
    "Insufficient Independent History", "Insufficient History",
)
_VERDICT_GLOSS = {
    "Validation Positive": "Edge confirmed — live mode.",
    "Validation Negative": "Edge is negative after costs — do not act on picks.",
    "No Proven Edge Yet":  "No measurable edge after costs yet — watchlist only.",
    "Insufficient History":              "Not enough evidence yet — watchlist only.",
    "Insufficient Independent History":  "Not enough evidence yet — watchlist only.",
    "Insufficient Statistical Evidence": "Not enough evidence yet — watchlist only.",
    "Insufficient Breadth":              "Not enough evidence yet — watchlist only.",
}


def _verdict_state(v: str | None) -> str:
    if v == "Validation Positive":
        return "green"
    if v == "Validation Negative":
        return "red"
    if v in _VERDICT_GLOSS:
        return "amber"
    return "neutral"


# ─── Plain-English layer (deterministic; no LLM, no network) ────────────────
# One-sentence glosses for finance / stats terms actually shown on the dashboard.
PLAIN_GLOSSARY: dict[str, str] = {
    "IC": "How well the tool's ranking of stocks lined up with what actually happened next. 0 = no relationship; positive is better; realistic edges are small.",
    "residual IC": "The extra ranking value a signal adds on top of signals already in use — not just correlation with existing survivors.",
    "t-stat": "A rough measure of how unlikely the result is to be luck. Bigger numbers mean more convincing — not necessarily more profitable.",
    "bootstrap probability": "Chance the measured edge is above zero after re-shuffling the data many times.",
    "spread": "Difference between the average return of the top-ranked group of stocks and the bottom-ranked group.",
    "hit rate": "Fraction of picks that ended up positive after costs.",
    "NAV": "Net Asset Value — the per-unit market value of a fund or ETF.",
    "TER": "Total Expense Ratio — the annual fee a fund charges; it comes out of your returns.",
    "tracking error": "How closely an ETF follows its benchmark index. Smaller is better.",
    "AUM": "Assets Under Management — how much money the fund holds. Larger usually means more stable.",
    "momentum": "How strongly a stock's price has been trending up (or down) over recent weeks/months.",
    "quintile": "One of five equal-sized groups. Q1 is the top-ranked fifth, Q5 the bottom fifth.",
    "medians": "The middle value of a sorted list — less swayed by a few outliers than the average.",
    "drawdown": "How far a stock or portfolio has fallen from its recent peak.",
    "RSI": "Relative Strength Index — a 0–100 gauge of recent price momentum. Above ~70 is often overbought; below ~30 often oversold.",
    "volatility": "How much a stock's price swings around. Higher = wilder ride, not automatically worse returns.",
    "IV rank": "Where today's implied volatility sits inside its 1-year range (0% = 1-year low, 100% = 1-year high).",
    "delivery %": "Fraction of a day's traded shares that were actually delivered to buyers (not intraday flips). Higher usually means real conviction.",
    "effective validation dates": "Independent days of evidence the tool has, after removing overlapping days that would double-count.",
    "raw validation dates": "Total calendar days with a validation observation, before removing overlaps.",
    "matured signals": "Past picks where enough time has passed to measure how they actually did.",
    "maturing signals": "Recent picks whose holding window has not finished — outcome unknown.",
    "shadow": "A parallel run of the tool with experimental rules. Only used for comparison; never overrides the official picks.",
    "overlap": "How many names the official Top-20 and the shadow Top-20 have in common.",
    "veto": "A hard block on certain names (e.g. governance concerns) regardless of score.",
    "regime": "The current market mood — risk-on (calm, rising), risk-off (nervous, falling), or neutral.",
    "Model edge/day": "The tool's measured expected return per day after costs. Blank until validation is positive.",
    "Target-per-day": "Best-case return per day IF the target is reached — a ceiling, not an expectation.",
}

PLAIN_DISCLAIMER_BULLETS: tuple[str, ...] = (
    "This is a personal research tool, not financial advice.",
    "It has never been proven to make money; it may never be.",
    'Even a "positive" verdict means "worth a closer look," not "will profit."',
    "Consult a SEBI-registered adviser before investing real money.",
    "Never invest money you can't afford to lose.",
)


def _html_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _gloss(term: str, label: str | None = None) -> str:
    """Return an inline span that reveals a plain-English definition on hover /
    focus / tap. Unknown terms fail soft — return the label/term unchanged."""
    definition = PLAIN_GLOSSARY.get(term)
    text = label or term
    if not definition:
        return text
    safe_def = _html_escape(definition)
    return (f'<span class="gloss" tabindex="0" role="button" '
            f'aria-label="Definition: {safe_def}">{text}'
            f'<span class="tt" role="tooltip">{safe_def}</span></span>')


def _plain_summary_html(progress: dict | None) -> str:
    """Top-of-page plain-English card. Copy is state-keyed; day counts come
    from the payload, never hand-typed. Missing numbers → 'not yet available'."""
    p = progress or {}
    verdict = p.get("verdict") or "Verdict not yet available"
    state = p.get("state") or "neutral"
    now = p.get("effective_now")
    tgt = p.get("effective_target")
    if now is not None and tgt:
        try:
            now_txt = ("%g" % float(now))
            frag = f"({now_txt} of ~{int(tgt)} independent {_gloss('effective validation dates', 'days')} needed)"
        except Exception:
            frag = "(day count not yet available)"
    else:
        frag = "(day count not yet available)"

    if verdict == "Validation Positive":
        body = (
            f"Today's result: the tool's rankings have finally cleared their evidence bar "
            f"{frag}, plus the statistical checks. This still means \u201cworth a closer look,\u201d not "
            f"\u201cwill make money.\u201d Read the \u201cBefore you ever act on this\u201d panel at the bottom "
            f"before doing anything with real money."
        )
    elif verdict == "Validation Negative":
        body = (
            "Today's result: do not act on the picks below. Over the days measured so far, "
            f"the tool's rankings have not beaten costs \u2014 the {_gloss('spread', 'edge')} is negative. "
            "Treat the lists below as a record of what the model <em>would</em> have picked, not as ideas to buy."
        )
    elif verdict == "Verdict not yet available":
        body = (
            "Today's result: verdict not yet available. The validation step hasn't run in this "
            "output folder, so the tool has no opinion to share yet. Run the workflow end-to-end, "
            "then reopen this page."
        )
    else:
        # amber / neutral family — insufficient history / no proven edge yet
        body = (
            "Today's result: no action. The tool is still learning whether its stock rankings "
            f"actually work, and it doesn't have enough history yet {frag}. Everything below is "
            "practice data for watching only \u2014 not advice to buy anything. Keep running it daily; "
            "it'll tell you when it has real evidence."
        )

    return (
        '<div class="glass g-violet panel plain-summary">'
        '<div class="ps-head">Plain English summary</div>'
        f'<p class="ps-body">{body}</p>'
        '<div class="ps-sub">Auto-generated from today\'s validation output. '
        'See the technical panels below for the numbers behind it.</div>'
        '</div>'
    )


def _plain_card_line(row: dict, verdict_state: str) -> str:
    """One deterministic 'in plain words' sentence per Top-5 card, composed only
    from fields already on the row. Missing fragments are dropped."""
    frags: list[str] = []

    # Trend / calm fragment inferred from the existing Technical flag we already
    # attached in _payload() (which encodes RSI + vol into a color + short text).
    tech = None
    risk_text = None
    for f in (row.get("flags") or []):
        if not isinstance(f, (list, tuple)) or len(f) < 3:
            continue
        cat = f[1]
        if cat == "Technical" and tech is None:
            tech = (f[0], str(f[2] or "").lower())
        elif cat == "Risk" and risk_text is None:
            risk_text = str(f[2] or "").strip()

    if tech:
        _color, text = tech
        if "clean" in text:
            frags.append("recent price action looks calm")
        elif "strongly overbought" in text:
            frags.append("recent price trend is strong but stretched (overbought)")
        elif "overbought" in text:
            frags.append("recent price trend is strong; a pullback entry is preferred")
        elif "elevated" in text or "vol" in text:
            frags.append("recent price swings are on the wide side")

    sent = row.get("sent") or {}
    if sent:
        try:
            neg = float(sent.get("neg") or 0)
            pos = float(sent.get("pos") or 0)
            if neg >= 40:
                frags.append("some negative news flagged in recent headlines")
            elif pos >= 40:
                frags.append("news tone is broadly positive")
            else:
                frags.append("no strong news signal either way")
        except Exception:
            pass

    if risk_text and risk_text.lower() != "no major technical risk flagged":
        frags.append(f"risk note: {risk_text.rstrip('.').lower()}")

    if not frags:
        body = ("Not enough plain-language signals to summarise this candidate yet "
                "\u2014 see the numeric fields above")
    else:
        body = "In plain words: " + "; ".join(frags)

    if verdict_state == "green":
        suffix = (" \u2014 the tool's rankings have cleared their evidence bar, but this is "
                  "still \u201cworth a closer look,\u201d not a buy signal.")
    else:
        suffix = (" \u2014 but this is a watch-only note, not a recommendation, because the "
                  "tool hasn't proven its picks work yet.")
    return body + suffix


def _plain_disclaimer_html() -> str:
    bullets = "".join(f"<li>{_html_escape(b)}</li>" for b in PLAIN_DISCLAIMER_BULLETS)
    return (
        '<div class="glass g-amber panel plain-disclaimer">'
        '<div class="pd-head">Before you ever act on this</div>'
        f'<ul>{bullets}</ul>'
        '<div class="pd-sub">Always visible. Read it every time \u2014 '
        'including the days the tool looks confident.</div>'
        '</div>'
    )





def _verdict_from_markdown(md_path: Path) -> str | None:
    """Fallback verdict extractor. Looks for a line naming one of VALID_VERDICTS."""
    if not md_path.exists():
        return None
    try:
        txt = md_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    for line in txt.splitlines():
        low = line.strip()
        if not low:
            continue
        # Prefer lines that look like a verdict heading, but accept any exact match.
        for v in _VALID_VERDICTS:
            if v in line:
                return v
    return None


def _delta_matured_from_history(history_csv: Path, matured_today: int) -> int | None:
    """Diff matured count vs the most recent DISTINCT prior date in score_history.
    Returns None when there is no strictly-prior distinct date (hides the chip)."""
    if not history_csv.exists():
        return None
    try:
        h = pd.read_csv(history_csv, usecols=lambda c: c in ("Date", "Net_Forward_Return", "Horizon_Days"))
    except Exception:
        try:
            h = pd.read_csv(history_csv)
        except Exception:
            return None
    if h.empty or "Date" not in h.columns:
        return None
    today_str = datetime.now().date().isoformat()
    try:
        dates = sorted({str(d) for d in h["Date"].dropna().astype(str).tolist()})
    except Exception:
        return None
    prior = [d for d in dates if d < today_str]
    if not prior:
        return None
    prior_date = prior[-1]
    sub = h[h["Date"].astype(str) == prior_date]
    if "Horizon_Days" in sub.columns:
        try:
            s10 = sub[sub["Horizon_Days"] == 10]
            if not s10.empty:
                sub = s10
        except Exception:
            pass
    if "Net_Forward_Return" in sub.columns:
        prior_matured = int(sub["Net_Forward_Return"].notna().sum())
    else:
        prior_matured = 0
    return int(matured_today) - prior_matured


def _trailing_run(seq, predicate) -> int:
    n = 0
    for x in reversed(list(seq)):
        if predicate(x):
            n += 1
        else:
            break
    return n


def _shadow_history_payload(hist_csv: Path) -> dict:
    """Read shadow_vs_official_history.csv (written by shadow_vs_official_report)
    and compute trailing streaks. Returns zeros when file is missing."""
    empty = {
        "available": False,
        "consecutive_shadow_leads": 0,
        "consecutive_verdict_positive": 0,
        "latest_shadow_matured_obs": None,
        "rows": 0,
    }
    if not hist_csv.exists():
        return empty
    try:
        df = pd.read_csv(hist_csv)
    except Exception:
        return empty
    if df.empty or "date" not in df.columns:
        return empty
    df = df.sort_values("date")
    def _truthy(v):
        s = str(v).strip().lower()
        return s in ("true", "1", "yes", "y", "t")
    leads = _trailing_run(df.get("shadow_beats_official_net", pd.Series(dtype=object)).tolist(),
                          _truthy)
    vpos = _trailing_run(df.get("verdict", pd.Series(dtype=str)).astype(str).tolist(),
                         lambda v: v == "Validation Positive")
    latest_obs = None
    try:
        latest_obs = df.iloc[-1].get("shadow_matured_obs")
        if pd.isna(latest_obs):
            latest_obs = None
        else:
            latest_obs = float(latest_obs)
    except Exception:
        latest_obs = None
    return {
        "available": True,
        "consecutive_shadow_leads": int(leads),
        "consecutive_verdict_positive": int(vpos),
        "latest_shadow_matured_obs": latest_obs,
        "rows": int(len(df)),
    }


def _alpha_evidence_payload(out_dir: Path) -> dict | None:
    """Merge alpha_promotion_log.json + alpha_zoo_ic_report.csv + survivors.json
    into a table-ready payload for the Alpha Zoo evidence panel.
    Returns None when NONE of the three sources exist."""
    plog_p = out_dir / "alpha_promotion_log.json"
    ic_p = out_dir / "alpha_zoo_ic_report.csv"
    surv_p = out_dir / "alpha_zoo_survivors.json"
    if not (plog_p.exists() or ic_p.exists() or surv_p.exists()):
        return None
    plog = _safe_read_json(plog_p) or {}
    ic_df = _safe_read_csv(ic_p)
    surv = _safe_read_json(surv_p) or {}

    min_ic = surv.get("threshold_ic") or plog.get("threshold_ic")
    min_t  = surv.get("threshold_tstat") or plog.get("threshold_tstat")
    inc_ic = _cfg("ALPHA_INCREMENTAL_IC_MIN", 0.015)

    survivor_names = set()
    for s in (surv.get("survivors") or []):
        nm = s.get("alpha") if isinstance(s, dict) else str(s)
        if nm:
            survivor_names.add(str(nm))

    # index IC report by alpha name (highest |mean_IC| row wins if duplicated by horizon)
    ic_by_name: dict[str, dict] = {}
    if not ic_df.empty and "alpha" in ic_df.columns:
        try:
            ic_df = ic_df.copy()
            ic_df["_abs"] = pd.to_numeric(ic_df.get("mean_IC"), errors="coerce").abs()
            ic_df = ic_df.sort_values("_abs", ascending=False)
            for _, r in ic_df.iterrows():
                nm = str(r.get("alpha"))
                if nm and nm not in ic_by_name:
                    ic_by_name[nm] = r.to_dict()
        except Exception:
            pass

    # candidate records from promotion log
    candidates = []
    plog_entries = plog.get("candidates") or plog.get("entries") or []
    if isinstance(plog_entries, dict):
        plog_entries = [dict(v, alpha=k) for k, v in plog_entries.items()]
    plog_names = set()
    for e in plog_entries:
        if not isinstance(e, dict):
            continue
        nm = str(e.get("alpha") or e.get("name") or "").strip()
        if not nm:
            continue
        plog_names.add(nm)
        promoted = e.get("promote")
        if promoted is None:
            promoted = e.get("promoted")
        reason = str(e.get("reason") or e.get("verdict") or "").strip()
        candidates.append({
            "alpha": nm,
            "standalone_ic": _num(e.get("standalone_ic") or e.get("mean_IC") or e.get("ic"), 4),
            "residual_ic": _num(e.get("residual_ic"), 4),
            "tstat": _num(e.get("t_stat") or e.get("tstat"), 2),
            "windows": _num(e.get("windows") or e.get("n_windows"), 0),
            "promote": promoted,
            "reason": reason,
            "in_zoo": nm in survivor_names,
        })

    # add zoo-only rows (already surviving, but not in this run's promotion log)
    for nm, rec in ic_by_name.items():
        if nm in plog_names:
            continue
        candidates.append({
            "alpha": nm,
            "standalone_ic": _num(rec.get("mean_IC"), 4),
            "residual_ic": None,
            "tstat": _num(rec.get("t_stat") or rec.get("tstat"), 2),
            "windows": _num(rec.get("n_windows") or rec.get("windows"), 0),
            "promote": True if nm in survivor_names else None,
            "reason": "In zoo — baseline survivor" if nm in survivor_names else "Baseline (no eval this run)",
            "in_zoo": nm in survivor_names,
        })

    # sort: promoted first, then by |standalone_ic| desc
    def _rank(c):
        p = 0 if c.get("promote") is True else (1 if c.get("promote") is None else 2)
        ic = abs(c.get("standalone_ic") or 0.0)
        return (p, -ic)
    candidates.sort(key=_rank)

    return {
        "min_ic": min_ic,
        "min_tstat": min_t,
        "min_residual_ic": inc_ic,
        "rows": candidates[:20],
        "sources": {
            "promotion_log": plog_p.exists(),
            "ic_report": ic_p.exists(),
            "survivors": surv_p.exists(),
        },
    }





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
    top5_bench_df = _safe_read_csv(OUT / "top5_benchmark_stats.csv")
    top5_corr_df = _safe_read_csv(OUT / "top5_corr_matrix.csv")
    top5_horizon_df = _safe_read_csv(OUT / "top5_horizon.csv")
    top5_sent_df = _safe_read_csv(OUT / "top5_sentiment.csv")
    macro_ctx = _safe_read_json(OUT / "macro_context.json")
    alpha_ic_df = _safe_read_csv(OUT / "alpha_zoo_ic_report.csv")
    alpha_survivors = _safe_read_json(OUT / "alpha_zoo_survivors.json")

    # --- verdict / banner ---
    # Verdict source order: structured JSON → markdown fallback → neutral chip.
    # Missing sources must NEVER default to a positive verdict.
    verdict_source = "unavailable"
    verdict = None
    grade = None
    if (OUT / "validation_status.json").exists() and val:
        verdict = val.get("verdict")
        grade = val.get("evidence_grade")
        if verdict:
            verdict_source = "validation_status.json"
    if not verdict:
        md_verdict = _verdict_from_markdown(OUT / "cross_sectional_validation_report.md")
        if md_verdict:
            verdict = md_verdict
            verdict_source = "cross_sectional_validation_report.md"
    if not verdict:
        verdict = "Verdict not yet available"
        grade = grade or "Insufficient Evidence"

    grade = grade or "Insufficient Evidence"
    stats = val.get("stats", {}) or {}

    rec = (cmp_.get("recommendation") or "REVIEW: continue running both")
    rec_low = rec.lower()

    decision_use = "LIVE" if verdict == "Validation Positive" else "WATCHLIST ONLY"
    # shadow_state is finalized below after the streak/history is read.
    shadow_state = "amber"
    bottom_line = (
        f"<b>{decision_use}.</b> Validation is <b>{verdict}</b>. "
        f"All entry / stop / target levels below are mechanical reference levels, "
        f"not recommendations."
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

    # --- quintile chart (dynamic highest usable horizon) ---
    quintile: dict[str, list] = {}
    quintile_horizon = None
    if not bucket.empty and "Bucket_Type" in bucket.columns:
        bq = bucket[bucket["Bucket_Type"] == "Score_Quintile"].copy()
        if "Horizon_Days" in bq.columns and "Median_Net_Return" in bq.columns:
            bq["_h"] = pd.to_numeric(bq["Horizon_Days"], errors="coerce")
            horizons = sorted([int(h) for h in bq["_h"].dropna().unique()])
            for h in horizons:
                sub = bq[bq["_h"] == h]
                ordered = []
                for label in ("Q5_Lowest", "Q4", "Q3", "Q2", "Q1_Highest"):
                    row = sub[sub["Bucket"] == label]
                    q_val = None
                    if not row.empty:
                        raw = pd.to_numeric(row["Median_Net_Return"], errors="coerce").dropna()
                        if not raw.empty:
                            q_val = _num(raw.iloc[0] * 100, 3)
                    ordered.append(q_val)
                quintile[str(h)] = ordered
            usable = [h for h in horizons if any(v is not None for v in quintile.get(str(h), []))]
            if usable:
                quintile_horizon = max(usable)

    # --- shadow chip (tightened four-gate) ---
    overlap = shadow_summary.get("top20_overlap_count")
    added = shadow_summary.get("v4_added_to_top20", []) or []
    dropped = shadow_summary.get("v4_dropped_from_top20", []) or []
    shadow_warnings = shadow_summary.get("warnings", []) or []
    veto_in_shadow = [s for s in added if _veto_symbol(s)]

    shadow_history = _shadow_history_payload(OUT / "shadow_vs_official_history.csv")
    min_streak = int(_cfg("SHADOW_GREEN_MIN_STREAK", 8))
    min_matured_obs = int(_cfg("SHADOW_GREEN_MIN_MATURED_OBS",
                               _cfg("CROSSVAL_MIN_EFFECTIVE_DATES", 6)))
    lead_streak = int(shadow_history.get("consecutive_shadow_leads") or 0)
    vpos_streak = int(shadow_history.get("consecutive_verdict_positive") or 0)
    latest_obs = shadow_history.get("latest_shadow_matured_obs")
    latest_obs_val = float(latest_obs) if latest_obs is not None else 0.0

    failed_checks: list[str] = []
    if verdict != "Validation Positive":
        failed_checks.append("official verdict is not Validation Positive")
    if lead_streak < min_streak:
        failed_checks.append(f"only {lead_streak} consecutive shadow-lead run(s) — {min_streak} required")
    if vpos_streak < min_streak:
        failed_checks.append(f"only {vpos_streak} consecutive verdict-positive run(s) — {min_streak} required")
    if latest_obs_val < min_matured_obs:
        failed_checks.append(f"shadow matured-independent obs {latest_obs_val:.0f} — {min_matured_obs} required")
    if veto_in_shadow:
        failed_checks.append(
            f"shadow Top-20 pulls in governance-vetoed name(s): {', '.join(veto_in_shadow)}")

    if not failed_checks:
        shadow_state = "green"
    elif ("official still leads" in rec_low or "do not switch" in rec_low
          or verdict == "Validation Negative"):
        shadow_state = "red"
    else:
        shadow_state = "amber"

    if shadow_state == "green":
        shadow_reason = "Shadow leads matured EV/day and clears every green-gate check."
    elif shadow_state == "red":
        shadow_reason = "<b>Do not switch.</b> " + "; ".join(failed_checks or [rec]) + "."
    else:
        shadow_reason = "Green-gate checks not met: " + "; ".join(failed_checks) + "."

    shadow = {
        "state": shadow_state,
        "chip": ("🟢 GREEN" if shadow_state == "green"
                 else "🔴 RED" if shadow_state == "red" else "🟡 AMBER"),
        "reason": shadow_reason,

        "overlap": overlap,
        "added": len(added),
        "dropped": len(dropped),
        "added_symbols": added,
        "dropped_symbols": dropped,
        "warnings": shadow_warnings,
        "history": {
            "available": shadow_history.get("available", False),
            "lead_streak": lead_streak,
            "vpos_streak": vpos_streak,
            "latest_matured_obs": latest_obs,
            "min_streak": min_streak,
            "min_matured_obs": min_matured_obs,
            "failed_checks": failed_checks,
        },
    }
    bottom_line = bottom_line + f" Shadow stays {shadow['chip']}."


    shadow_top5_symbols: set[str] = set()
    shadow_unique_top5 = []
    if not shadow_scores.empty and "Symbol" in shadow_scores.columns:
        sh_clean = shadow_scores[~shadow_scores["Symbol"].apply(_veto_symbol)].copy()
        score_col = "Final_Score" if "Final_Score" in sh_clean.columns else "Opportunity_Score" if "Opportunity_Score" in sh_clean.columns else None
        if score_col:
            sh_clean = sh_clean.sort_values(score_col, ascending=False)
        shadow_top5 = sh_clean.head(5)
        shadow_top5_symbols = {_norm_sym(s) for s in shadow_top5["Symbol"].tolist()}

    # --- candidate cards (official top 5 post-veto) ---
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
                "in_shadow_top5": _norm_sym(r.get("Symbol")) in shadow_top5_symbols,
                "bench": None,
                "flags": flags,
            })

    # attach benchmark stats to each card by symbol
    if not top5_bench_df.empty and "Symbol" in top5_bench_df.columns:
        bmap = {str(row["Symbol"]): row for _, row in top5_bench_df.iterrows()}
        for c in cards:
            row = bmap.get(str(c["sym"]))
            if row is not None:
                c["bench"] = {
                    "ex21": _num(row.get("Excess_21D"), 4),
                    "ir63": _num(row.get("InformationRatio_63D"), 2),
                    "te63": _num(row.get("TrackingError_63D"), 3),
                    "beta": _num(row.get("BetaVsBenchmark_63D"), 2),
                }

    # attach horizon-optimizer recommendation per card
    if not top5_horizon_df.empty and "Symbol" in top5_horizon_df.columns:
        hmap = {str(row["Symbol"]): row for _, row in top5_horizon_df.iterrows()}
        for c in cards:
            row = hmap.get(str(c["sym"]))
            if row is None:
                continue
            try:
                curve = row.get("Exp_Ret_Curve")
                if isinstance(curve, str):
                    import ast as _ast
                    curve = _ast.literal_eval(curve) if curve.strip().startswith("[") else None
                hor = row.get("Horizons")
                if isinstance(hor, str):
                    import ast as _ast
                    hor = _ast.literal_eval(hor) if hor.strip().startswith("[") else None
            except Exception:
                curve, hor = None, None
            c["horizon"] = {
                "rec_days": _num(row.get("Rec_Horizon_Days"), 0),
                "exp_ret": _num(row.get("Exp_Ret_%"), 2),
                "down_vol": _num(row.get("Downside_Vol_%"), 2),
                "sharpe": _num(row.get("Sharpe_like"), 2),
                "grid": hor if isinstance(hor, list) else None,
                "curve": curve if isinstance(curve, list) else None,
            }

    # attach sentiment chip per card
    if not top5_sent_df.empty and "Symbol" in top5_sent_df.columns:
        smap = {str(row["Symbol"]): row for _, row in top5_sent_df.iterrows()}
        for c in cards:
            row = smap.get(str(c["sym"]))
            if row is None:
                continue
            c["sent"] = {
                "n": int(row.get("Headlines_7D") or 0),
                "pos": _num((row.get("PosPct") or 0) * 100, 0),
                "neg": _num((row.get("NegPct") or 0) * 100, 0),
                "net": _num(row.get("Net_Sent"), 2),
            }

    # correlation matrix payload for the top-5 (or fewer)
    corr_payload = None
    if not top5_corr_df.empty:
        try:
            first_col = top5_corr_df.columns[0]
            cdf = top5_corr_df.set_index(first_col)
            cdf.index = cdf.index.astype(str)
            cdf.columns = cdf.columns.astype(str)
            common = [s for s in cdf.index if s in cdf.columns]
            if len(common) >= 2:
                cdf = cdf.loc[common, common]
                vals = cdf.round(2).values.tolist()
                labels = [s.replace(".NS", "") for s in common]
                # avg |off-diagonal|
                import numpy as _np
                arr = cdf.abs().values.astype(float).copy()
                _np.fill_diagonal(arr, _np.nan)
                avg_abs = float(_np.nanmean(arr)) if arr.size else None
                corr_payload = {"labels": labels, "values": vals, "avg_abs": avg_abs}
        except Exception:
            corr_payload = None

    official_top5_symbols = {_norm_sym(c.get("sym")) for c in cards}
    if not shadow_scores.empty and "Symbol" in shadow_scores.columns:
        sh_clean = shadow_scores[~shadow_scores["Symbol"].apply(_veto_symbol)].copy()
        score_col = "Final_Score" if "Final_Score" in sh_clean.columns else "Opportunity_Score" if "Opportunity_Score" in sh_clean.columns else None
        if score_col:
            sh_clean = sh_clean.sort_values(score_col, ascending=False)
        for _, r in sh_clean.head(5).iterrows():
            if _norm_sym(r.get("Symbol")) in official_top5_symbols:
                continue
            shadow_unique_top5.append({
                "sym": r.get("Symbol"),
                "nm": r.get("Name") or r.get("Company") or "",
                "score": _num(r.get(score_col), 2) if score_col else None,
                "bucket": r.get("Bucket") or r.get("Opportunity_Bucket") or r.get("Opportunity_Type") or "Shadow Top 5",
                "risk": r.get("Key_Risk") or r.get("Reason") or "Unique to shadow Top 5",
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

    # ── alpha-zoo survivors + IC snapshot (step 5) ──
    zoo_payload = None
    try:
        surv = (alpha_survivors or {}).get("survivors") or []
        if surv:
            zoo_payload = {
                "survivors": surv[:10],
                "count": len(surv),
                "min_ic": alpha_survivors.get("threshold_ic"),
                "min_tstat": alpha_survivors.get("threshold_tstat"),
                "min_for_tilt": alpha_survivors.get("min_for_tilt"),
            }
        elif not alpha_ic_df.empty:
            zoo_payload = {"survivors": [], "count": 0,
                            "top_by_ic": alpha_ic_df.sort_values("mean_IC", ascending=False,
                                                                 key=lambda s: s.abs())
                                                 .head(6)
                                                 .to_dict(orient="records")}
    except Exception:
        zoo_payload = None

    # ── macro context (step 4) ──
    macro_payload = None
    try:
        if macro_ctx:
            macro_payload = {
                "regime": macro_ctx.get("regime") or "neutral",
                "vix": _num(macro_ctx.get("vix_level"), 2),
                "vix_pct": _num(macro_ctx.get("vix_pctile_252d"), 1),
                "nifty_trend": _num(macro_ctx.get("nifty_50d_trend"), 2),
                "above_50dma": macro_ctx.get("nifty_above_50dma"),
            }
    except Exception:
        macro_payload = None


    # ── progress-to-a-verdict payload ──
    effective_now = stats.get("effective_validation_dates") if stats else None
    raw_now = stats.get("validation_dates") if stats else None
    effective_target = int(_cfg("CROSSVAL_MIN_EFFECTIVE_DATES", 6))
    raw_target = int(_cfg("CROSSVAL_MIN_DATES", 10))
    adaptive_tick = int(_cfg("ADAPTIVE_MIN_DATES", 60))
    delta_matured = _delta_matured_from_history(OUT / "score_history.csv", matured)
    progress_payload = {
        "verdict": verdict,
        "gloss": _VERDICT_GLOSS.get(verdict, "Verdict not yet available."),
        "state": _verdict_state(verdict),
        "source": verdict_source,
        "effective_now": _num(effective_now, 1) if effective_now is not None else None,
        "effective_target": effective_target,
        "adaptive_tick": adaptive_tick,
        "raw_now": _num(raw_now, 0) if raw_now is not None else None,
        "raw_target": raw_target,
        "matured": matured,
        "maturing": maturing,
        "total": total_signals,
        "delta_matured": delta_matured,
    }

    alpha_evidence = _alpha_evidence_payload(OUT)

    # Plain-English per-card "in plain words" line (deterministic, no new I/O)
    _vstate = _verdict_state(verdict)
    for _c in cards:
        _c["plain"] = _plain_card_line(_c, _vstate)
    for _c in shadow_unique_top5:
        _c["plain"] = _plain_card_line(_c, _vstate)

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

        "progress": progress_payload,
        "alpha_evidence": alpha_evidence,
        "evidence_10": evidence_10,
        "evidence_5": evidence_5,
        "quintile": quintile,
        "quintile_horizon": quintile_horizon,
        "shadow": shadow,
        "universe": universe_counts,
        "cards": cards,
        "shadow_unique_top5": shadow_unique_top5,
        "scatter": scatter,
        "avoid": avoid,
        "shadow_only": shadow_only,
        "dq": dq_notes,
        "excel": excel,
        "corr_matrix": corr_payload,
        "macro": macro_payload,
        "alpha_zoo": zoo_payload,
        "plain_summary_html": _plain_summary_html(progress_payload),
        "plain_disclaimer_html": _plain_disclaimer_html(),
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
@media(max-width:640px){.twocol{grid-template-columns:1fr}}

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
.card.risk{border-top-color:var(--amber);box-shadow:var(--glow-amber)}
.card:hover{transform:translateY(-2px)}


.card .top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.card .sym{font-size:16px;font-weight:700}
.card .nm{font-size:11px;color:var(--dim)}
.card .px{text-align:right} .card .px .lbl{font-size:10px;color:var(--dim)} .card .px .p{font-size:16px;font-weight:700}
.lblchip{font-size:10px;font-weight:700;padding:3px 9px;border-radius:6px;background:var(--amber-bg);color:var(--amber);white-space:nowrap}
.lblchip.review{background:var(--teal-bg);color:var(--teal)}
.lblchip.shadow{background:var(--blue-bg);color:var(--blue-soft);border:1px solid rgba(88,166,255,.22)}
.levels{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:11px}
.lv{background:rgba(255,255,255,0.03);border:1px solid var(--line);border-radius:8px;padding:7px 9px}
.lv .l{font-size:9.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.4px}
.lv .n{font-size:13px;font-weight:640;margin-top:1px}
.lv.stop .n{color:var(--amber)} .lv.t .n{color:var(--teal)}
.perday{display:flex;gap:8px;margin-top:9px;flex-wrap:wrap}
.pd{flex:1;min-width:90px;background:rgba(255,255,255,0.03);border:1px solid var(--line);border-radius:8px;padding:6px 8px;text-align:center}
.pd .l{font-size:9px;color:var(--dim)} .pd .n{font-size:13px;font-weight:650}
.pd.edge .n{color:var(--dim)}
.flags{margin-top:10px;display:flex;flex-direction:column;gap:5px}
.flag{font-size:11px;color:var(--muted)} .flag b{color:var(--txt)}
.fdot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:middle}
.d-red{background:var(--amber)} .d-amber{background:var(--amber)} .d-green{background:var(--teal)} .d-dim{background:var(--dim)}

table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:4px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-size:10.5px;text-transform:uppercase;letter-spacing:.5px;font-weight:600}
td .rsym{font-weight:640}
.rchip{font-size:10px;font-weight:650;padding:2px 7px;border-radius:5px}
.rc-veto{background:var(--amber-bg);color:var(--amber)} .rc-rsi{background:var(--amber-bg);color:var(--amber)}
.rc-vol{background:rgba(163,113,247,.14);color:var(--violet)} .rc-etf{background:rgba(88,166,255,.12);color:var(--blue)}

/* Readiness visual (replaces 5D vs 10D evidence tile row) */
.rmeter{display:flex;flex-direction:column;gap:9px;margin-top:8px}
.rrow{display:grid;grid-template-columns:130px 1fr 68px;gap:10px;align-items:center;font-size:11.5px}
.rrow .rl{color:var(--muted);text-transform:uppercase;letter-spacing:.5px;font-size:10.5px}
.rrow .rv{color:var(--txt);text-align:right;font-weight:650}
.rbar{position:relative;height:8px;border-radius:6px;background:rgba(255,255,255,0.05);overflow:hidden}
.rbar > span{position:absolute;left:0;top:0;bottom:0;border-radius:6px}
.rbar.ok  > span{background:linear-gradient(90deg,var(--teal),#7FE0C6)}
.rbar.mid > span{background:linear-gradient(90deg,var(--blue),var(--violet))}
.rbar.low > span{background:linear-gradient(90deg,var(--amber),#FFD07A)}


.caption{background:var(--panel2);border:1px dashed var(--line2);border-radius:10px;padding:11px 14px;font-size:11.5px;color:var(--muted);margin-top:12px}
.caption b{color:var(--txt)}

.foot{margin-top:26px;padding:14px;border:1px solid var(--line);border-radius:12px;
  font-size:11px;color:var(--dim);background:var(--panel2);backdrop-filter:blur(10px)}
.foot code{color:var(--txt);background:transparent}
canvas{margin-top:4px}
.chart-error{margin-top:10px;padding:12px;border:1px dashed rgba(255,255,255,.14);border-radius:10px;color:var(--amber);background:rgba(242,177,60,.08);font-size:12px}

/* Progress-to-a-verdict section */
.progress-row{display:grid;grid-template-columns:minmax(220px,1fr) minmax(280px,2fr) minmax(200px,1fr);gap:14px;align-items:stretch}
@media(max-width:900px){.progress-row{grid-template-columns:1fr}}
.vchip{display:flex;flex-direction:column;justify-content:center;gap:6px;padding:12px 14px;border-radius:12px;border:1px solid var(--line);backdrop-filter:blur(10px)}
.vchip .vhead{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.6px}
.vchip .vtitle{font-size:17px;font-weight:700}
.vchip .vgloss{font-size:12px;color:var(--muted)}
.vchip .vsrc{font-size:10px;color:var(--dim);margin-top:2px}
.vchip.green {background:linear-gradient(135deg,rgba(63,185,80,0.20),rgba(20,80,40,0.10));border-color:rgba(63,185,80,0.45)}
.vchip.green .vtitle{color:var(--green)}
.vchip.amber {background:linear-gradient(135deg,rgba(242,177,60,0.20),rgba(80,60,20,0.10));border-color:rgba(242,177,60,0.45)}
.vchip.amber .vtitle{color:var(--amber)}
.vchip.red   {background:linear-gradient(135deg,rgba(229,85,106,0.20),rgba(80,20,30,0.10));border-color:rgba(229,85,106,0.45)}
.vchip.red   .vtitle{color:var(--red-soft)}
.vchip.neutral{background:var(--panel2);border-color:var(--line2)}
.vchip.neutral .vtitle{color:var(--muted)}

.pbwrap{padding:12px 14px}
.pblab{font-size:10.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px}
.pbcount{font-size:18px;font-weight:700;margin-top:2px}
.pbbar{position:relative;height:10px;border-radius:6px;background:rgba(255,255,255,0.06);overflow:visible;margin-top:8px}
.pbbar > .pbfill{position:absolute;left:0;top:0;bottom:0;border-radius:6px;background:linear-gradient(90deg,var(--teal),#7FE0C6)}
.pbbar > .pbfill.low{background:linear-gradient(90deg,var(--amber),#FFD07A)}
.pbbar > .pbfill.mid{background:linear-gradient(90deg,var(--blue),var(--violet))}
.pbtick{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--violet-soft);opacity:.85}
.pbtick::after{content:attr(data-label);position:absolute;top:-16px;left:50%;transform:translateX(-50%);white-space:nowrap;font-size:9.5px;color:var(--violet-soft);letter-spacing:.3px}
.pbraw{font-size:11px;color:var(--dim);margin-top:6px}
.pbfoot{font-size:11px;color:var(--muted);margin-top:8px}

.matbox{padding:12px 14px;display:flex;flex-direction:column;gap:4px;justify-content:center}
.matline{font-size:12px;color:var(--muted)}
.matline b{color:var(--txt)}
.matdelta{display:inline-block;font-size:11px;font-weight:650;padding:2px 8px;border-radius:6px;margin-top:6px;width:fit-content}
.matdelta.up{background:var(--teal-bg);color:var(--teal)}
.matdelta.down{background:var(--amber-bg);color:var(--amber)}
.matdelta.flat{background:rgba(255,255,255,0.05);color:var(--dim)}

/* Universe pill strip in header */
.upills{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.upill{font-size:11px;color:var(--muted);background:var(--panel2);border:1px solid var(--line);border-radius:999px;padding:3px 10px;backdrop-filter:blur(6px)}
.upill b{color:var(--txt)}

/* Shadow streak strip */
.streakstrip{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}
.streak{font-size:11px;color:var(--muted);background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:5px 10px}
.streak b{color:var(--txt)}
.streak.warn{border-color:rgba(242,177,60,0.35);color:var(--amber)}

/* Watchlist banner + per-card ribbon */
.watchbanner{margin:10px 0 14px;padding:12px 16px;border-radius:12px;font-size:13px;font-weight:600;
  background:linear-gradient(90deg,rgba(242,177,60,0.18),rgba(242,177,60,0.06));
  border:1px solid rgba(242,177,60,0.45);border-left:4px solid var(--amber);color:var(--txt);backdrop-filter:blur(10px)}
.card{position:relative;overflow:hidden}
.ribbon{position:absolute;top:12px;right:-38px;transform:rotate(35deg);padding:3px 44px;font-size:10px;font-weight:800;letter-spacing:1.1px;color:#0B0B10;box-shadow:0 4px 12px -4px rgba(0,0,0,0.55);z-index:2}
.ribbon.watch{background:linear-gradient(90deg,#E0A030,#F2B13C)}
.ribbon.live {background:linear-gradient(90deg,#3FCBB8,#38BDB0)}

/* Alpha zoo evidence table */
.aztable{width:100%;border-collapse:collapse;font-size:12.5px}
.aztable th{padding:6px 8px;text-align:left;color:var(--muted);border-bottom:1px solid var(--line);font-weight:600;font-size:10.5px;text-transform:uppercase;letter-spacing:.5px}
.aztable td{padding:7px 8px;border-bottom:1px solid var(--line)}
.aztable td.num{font-variant-numeric:tabular-nums;text-align:right}
.azchip{display:inline-block;font-size:10px;font-weight:700;padding:2px 8px;border-radius:5px}
.azchip.green{background:var(--green-bg);color:var(--green)}
.azchip.amber{background:var(--amber-bg);color:var(--amber)}
.azchip.red  {background:var(--red-bg);color:var(--red-soft)}
.azchip.dim  {background:rgba(255,255,255,0.05);color:var(--dim)}
.aznew{display:inline-block;font-size:9.5px;font-weight:700;padding:1px 7px;border-radius:5px;background:var(--teal-bg);color:var(--teal);margin-left:6px;letter-spacing:.3px}



/* Plain-English layer — top summary card, per-card sub-line, disclaimer, glossary tooltips */
.plain-summary{margin:6px 0 14px}
.plain-summary .ps-head{font-size:10.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px}
.plain-summary .ps-body{font-size:14.5px;line-height:1.55;color:var(--txt)}
.plain-summary .ps-sub{font-size:11px;color:var(--muted);margin-top:8px}
.plain{font-size:12px;color:var(--muted);margin-top:10px;padding:8px 10px;border-radius:8px;
  background:rgba(255,255,255,0.03);border:1px dashed var(--line2);line-height:1.5}
.plain-disclaimer{margin:22px 0 6px}
.plain-disclaimer .pd-head{font-size:14px;font-weight:700;color:var(--amber);margin-bottom:8px}
.plain-disclaimer ul{margin:0 0 6px 20px;padding:0;color:var(--txt);font-size:13px;line-height:1.6}
.plain-disclaimer ul li{margin:3px 0}
.plain-disclaimer .pd-sub{font-size:11px;color:var(--muted);margin-top:8px}
.gloss{position:relative;border-bottom:1px dotted var(--muted);cursor:help;outline:none}
.gloss:focus,.gloss:focus-within{border-bottom-color:var(--teal)}
.gloss .tt{position:absolute;left:50%;bottom:calc(100% + 6px);transform:translateX(-50%);
  min-width:200px;max-width:280px;background:rgba(14,16,26,0.97);color:var(--txt);
  border:1px solid var(--line2);border-radius:8px;padding:8px 10px;font-size:11.5px;
  line-height:1.45;text-transform:none;letter-spacing:normal;font-weight:400;
  box-shadow:0 8px 24px -8px rgba(0,0,0,0.7);opacity:0;pointer-events:none;
  transition:opacity .12s ease;z-index:50;white-space:normal}
.gloss:hover .tt,.gloss:focus .tt,.gloss:focus-within .tt,.gloss:active .tt{opacity:1;pointer-events:auto}
</style></head>

<body>

<h1>NSE Quant Evidence Review</h1>
<div class="sub">Daily run &middot; Generated __GENERATED__ &middot; expanded NSE stock universe / NSE ETFs &middot; cross-sectional validation report is the authority</div>
<div class="upills" id="universePills"></div>

<div id="plainSummary"></div>


<h2>Progress to a verdict</h2>
<div class="glass g-violet panel">
  <div class="progress-row">
    <div class="vchip neutral" id="verdictChip">
      <div class="vhead">Validation Verdict</div>
      <div class="vtitle" id="vTitle">&mdash;</div>
      <div class="vgloss" id="vGloss">&mdash;</div>
      <div class="vsrc" id="vSource"></div>
    </div>
    <div class="pbwrap" id="progressBar"></div>
    <div class="matbox" id="maturationBox"></div>
  </div>
</div>

<div class="bottomline" id="bottomline"></div>

<div class="banner" id="banner"></div>


<div id="marketCtxWrap" style="display:none">
  <h2>Market context</h2>
  <div class="glass panel" id="marketCtxPanel">
    <div id="marketCtx" style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px"></div>
  </div>
</div>

<h2>Signal maturation &amp; validation readiness</h2>
<div class="grid twocol">
  <div class="glass g-teal panel">
    <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Signal maturation (10-day horizon)</div>
    <div class="evid" id="maturityCards" style="grid-template-columns:repeat(4,1fr)"></div>
    <div class="sub" id="maturityNote" style="margin-top:10px"></div>
  </div>
  <div class="glass g-violet panel">
    <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Validation readiness (10-day)</div>
    <div class="rmeter" id="readiness"></div>
    <div class="sub" id="evidenceNote" style="margin-top:10px"></div>
  </div>
</div>

<h2>Shadow vs Official &mdash; running record</h2>
<div class="glass g-blue panel">
  <div class="streakstrip" id="streakStrip"></div>
  <div class="evid" id="shadowCards" style="grid-template-columns:repeat(4,1fr);margin-bottom:10px"></div>
  <canvas id="shadowBar" height="86"></canvas>
  <div class="sub" id="shadowWarnings" style="margin-top:8px"></div>
</div>

<h2 id="quintileTitle">Quintile median net return</h2>
<div class="glass panel">
  <canvas id="quintileChart" height="118"></canvas>
  <div id="quintileEmpty" class="chart-error" style="display:none">No usable quintile median-return data is available yet. The chart will appear automatically once a forward-return horizon matures.</div>
  <div class="sub" id="quintileNote" style="margin-top:10px">Read on <b>medians</b>: at low effective sample sizes any quintile inversion is <b>noise, not model failure</b>. Means are intentionally discarded — outlier records inflate them.</div>
</div>


<h2>Top 5 watchlist candidates &mdash; post-governance veto</h2>
<div class="watchbanner" id="watchBanner" style="display:none">Reference levels only &mdash; not validated. Buy zones, stops, and targets below are mechanical outputs, not recommendations.</div>
<div class="cards" id="cards"></div>


<h2 id="corrTitle">Top-5 correlation &mdash; diversification check</h2>
<div class="glass panel" id="corrPanel" style="display:none">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
    <div class="sub">60-session daily-return correlation across the picked top-5. Lower off-diagonal magnitude = more diversified basket.</div>
    <div id="corrAvg" class="lblchip"></div>
  </div>
  <div id="corrTable"></div>
</div>

<div id="alphaZooWrap" style="display:none">
  <h2>Alpha Zoo &mdash; signal evidence</h2>
  <div class="glass panel">
    <div class="sub" id="alphaZooCaption" style="margin-bottom:10px"></div>
    <div id="alphaZooBody"></div>
    <div class="sub" id="alphaZooFoot" style="margin-top:10px;font-size:11px"></div>
  </div>
</div>


<h2 id="shadowUniqueTitle">Shadow Top 5 unique candidates</h2>
<div class="cards" id="shadowUniqueCards"></div>
<div class="caption">
  <b>Target-per-day</b> = best case <b>IF</b> the target is hit within the hold window; ignores the stop and hit rate — mechanical ceiling, not expected return.
  <b>Model edge/day</b> = measured expected edge after costs — blank ("&mdash;") until validation is positive. Adani group names are categorically vetoed before any scoring or watchlist inclusion.
</div>

<details id="scatterDetails" style="margin-top:22px">
  <summary style="cursor:pointer;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:1.4px;color:var(--muted);padding:6px 0">Show RSI &times; volatility map</summary>
  <div class="glass panel" style="margin-top:10px">
    <canvas id="scatterChart" height="150"></canvas>
    <div class="sub" style="margin-top:10px">RSI is a timing filter, not a valuation filter. Right of the dashed line (RSI ~ 70-73) = overbought entry risk; above the upper band (vol ~ 30%) = elevated volatility.</div>
  </div>
</details>


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

<div id="plainDisclaimer"></div>

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
              : DATA.verdict==="Validation Negative" ? "amber"
              : (DATA.verdict||"").startsWith("Insufficient") ? "" : "amber";
document.getElementById("banner").innerHTML = `
 <div class="verdict ${vClass}">
   <div style="font-size:10.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.6px">Validation Verdict</div>
   <div class="v">${DATA.verdict}</div>
   <div style="font-size:12px;color:var(--muted);margin-top:2px">Evidence grade: ${DATA.grade}</div>
   <div class="pillrow">
     <span class="pill">Market regime: <b>${DATA.regime}</b></span>
     <span class="pill">Mode: <b>${DATA.decision_use}</b></span>
   </div>
 </div>`;

// ─── Progress to a verdict ────────────────────────────────────────────────
(function renderProgress(){
  const p = DATA.progress || {};
  const state = p.state || "neutral";
  const chip = document.getElementById("verdictChip");
  chip.className = "vchip " + state;
  document.getElementById("vTitle").textContent = p.verdict || "Verdict not yet available";
  document.getElementById("vGloss").textContent = p.gloss || "";
  const src = p.source && p.source !== "unavailable"
    ? "Source: " + p.source
    : "Source: not yet available";
  document.getElementById("vSource").textContent = src;

  // Progress bar (effective → CROSSVAL_MIN_EFFECTIVE_DATES, with adaptive tick)
  const now = (p.effective_now==null) ? 0 : Number(p.effective_now);
  const tgt = Math.max(1, Number(p.effective_target || 1));
  const pct = Math.max(2, Math.min(100, Math.round(now / tgt * 100)));
  const cls = now >= tgt ? "" : (now >= tgt * 0.5 ? "mid" : "low");
  const tickPct = Math.min(100, Math.round(Number(p.adaptive_tick || 0) / tgt * 100));
  const tickHtml = tickPct > 0 && tickPct <= 100
    ? `<span class="pbtick" style="left:${tickPct}%" data-label="adaptive-weighting (${p.adaptive_tick})"></span>`
    : "";
  const rawTxt = (p.raw_now==null)
    ? `raw dates: not yet available`
    : `${p.raw_now} / ${p.raw_target} raw dates${Number(p.raw_now) >= p.raw_target ? " — clears breadth floor" : ""}`;
  document.getElementById("progressBar").innerHTML = `
    <div class="pblab">Effective validation dates</div>
    <div class="pbcount">${p.effective_now==null ? '&mdash;' : Number(p.effective_now).toFixed(1)} / ${p.effective_target}</div>
    <div class="pbbar"><div class="pbfill ${cls}" style="width:${pct}%"></div>${tickHtml}</div>
    <div class="pbraw">${rawTxt}</div>
    <div class="pbfoot">Verdict requires &ge; ${p.effective_target} effective dates plus spread, t-stat, and bootstrap gates. Adaptive weighting is a separate downstream gate — see tick.</div>`;

  // Maturation summary + delta chip
  let deltaHtml = "";
  if (p.delta_matured !== null && p.delta_matured !== undefined) {
    const d = Number(p.delta_matured);
    if (d > 0) deltaHtml = `<span class="matdelta up">&#9650; +${d} since prior run</span>`;
    else if (d < 0) deltaHtml = `<span class="matdelta down">&#9660; ${d} since prior run</span>`;
    else deltaHtml = `<span class="matdelta flat">flat since prior run</span>`;
  }
  document.getElementById("maturationBox").innerHTML = `
    <div class="pblab">Signal maturation (10-day)</div>
    <div class="matline">Matured <b>${num(p.matured)}</b> &middot; Awaiting <b>${num(p.maturing)}</b></div>
    <div class="matline">Total <b>${num(p.total)}</b></div>
    ${deltaHtml}`;
})();

// Universe pill strip in the header (donut removed).
(function renderUniversePills(){
  const uni = DATA.universe || {};
  const keys = Object.keys(uni);
  if (!keys.length) return;
  const el = document.getElementById("universePills");
  el.innerHTML = keys.map(k => `<span class="upill">${k} &middot; <b>${uni[k]}</b></span>`).join("");
})();


// Readiness meter — replaces the 5D-vs-10D evidence tile row with a
// horizontal progress-bar visual driven by the 10-day validation stats.
function readinessRow(label, val, cfg){
  // cfg = {ok, mid, nd, suffix} — thresholds and display precision
  const nd = cfg.nd ?? 2, suf = cfg.suffix ?? "";
  const v = (val===null||val===undefined) ? null : Number(val);
  let pct = 0, cls = "low";
  if (v !== null){
    pct = Math.max(2, Math.min(100, Math.round((v / cfg.ok) * 100)));
    if (v >= cfg.ok)      cls = "ok";
    else if (v >= cfg.mid) cls = "mid";
    else                   cls = "low";
  }
  const shown = v === null ? "—" : v.toFixed(nd) + suf;
  return `<div class="rrow"><div class="rl">${label}</div>
    <div class="rbar ${cls}"><span style="width:${pct}%"></span></div>
    <div class="rv">${shown}</div></div>`;
}
const e10 = DATA.evidence_10 || {};
document.getElementById("readiness").innerHTML = [
  readinessRow("Validation dates", e10.validation_dates, {ok:20, mid:10, nd:0}),
  readinessRow("Effective dates",  e10.effective_validation_dates, {ok:5, mid:2, nd:1}),
  readinessRow("Q1&minus;Q5 spread", e10.spread, {ok:0.01, mid:0.003, nd:4}),
  readinessRow("Adj. t-stat",      e10.adj_tstat, {ok:2, mid:1, nd:2}),
  readinessRow("Bootstrap P(+)",   e10.bootstrap_prob, {ok:0.9, mid:0.7, nd:2}),
].join("");

const e5 = DATA.evidence_5 || {};
document.getElementById("evidenceNote").innerHTML =
  `5-day companion: ${fmt(e5.validation_dates,'',0)} dates, spread ${fmt(e5.spread,'',4)}, t-stat ${fmt(e5.adj_tstat,'',2)}.`;
document.getElementById("maturityNote").innerHTML =
  `${num(DATA.maturity.matured)} matured / ${num(DATA.maturity.total)} total &middot; ${DATA.maturity.rate}% maturation rate. A large awaiting pool early in accumulation is normal — not a data fault.`;

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


// Maturity metric cards (matured / awaiting / total / rate).
document.getElementById("maturityCards").innerHTML = `
  <div class="tile"><div class="k">Matured</div><div class="val" style="color:var(--teal)">${num(DATA.maturity.matured)}</div><span class="tag t-ok">forward return known</span></div>
  <div class="tile"><div class="k">Awaiting maturation</div><div class="val" style="color:var(--blue-soft)">${num(DATA.maturity.maturing)}</div><span class="tag t-build">horizon not elapsed</span></div>
  <div class="tile"><div class="k">Total signals</div><div class="val" style="color:var(--violet-soft)">${num(DATA.maturity.total)}</div><span class="tag t-build">10-day slice</span></div>
  <div class="tile"><div class="k">Maturation rate</div><div class="val" style="color:var(--amber)">${DATA.maturity.rate}%</div><span class="tag t-thin">matured / total</span></div>`;



// Universe donut removed — counts now live in the header pill strip above.

// Shadow streak strip — running record of consecutive lead / verdict-positive runs.
(function renderStreakStrip(){
  const sh = DATA.shadow || {};
  const h  = sh.history || {};
  const strip = document.getElementById("streakStrip");
  const leadCls = (h.lead_streak||0) >= (h.min_streak||8) ? "" : "warn";
  const vposCls = (h.vpos_streak||0) >= (h.min_streak||8) ? "" : "warn";
  const rows = [
    `<span class="streak ${leadCls}">Shadow lead streak: <b>${h.lead_streak||0} run${(h.lead_streak||0)===1?'':'s'}</b></span>`,
    `<span class="streak ${vposCls}">Verdict-positive streak: <b>${h.vpos_streak||0} run${(h.vpos_streak||0)===1?'':'s'}</b></span>`,
    `<span class="streak">Green requires: &ge; <b>${h.min_streak||8}</b> consecutive leads <b>AND</b> verdict = Validation Positive <b>AND</b> shadow matured-independent obs &ge; <b>${h.min_matured_obs||6}</b></span>`,
  ];
  if (!h.available) {
    rows.unshift(`<span class="streak warn">History not yet available &mdash; ledger will populate after next shadow_vs_official run.</span>`);
  }
  strip.innerHTML = rows.join("");
})();

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
const _shadowReasonHtml = sh.reason ? `<div style="margin-bottom:6px">${sh.reason}</div>` : "";
document.getElementById("shadowWarnings").innerHTML =
  _shadowReasonHtml +
  ((sh.warnings && sh.warnings.length) ? "Shadow neutralizations: " + sh.warnings.map(w=>`<span class="pill">${w}</span>`).join(" ")
                                       : "");


const qh = DATA.quintile_horizon;
const qvals = qh ? (DATA.quintile[String(qh)] || []) : [];
document.getElementById("quintileTitle").innerHTML = qh
  ? `Quintile median net return &mdash; ${qh}-day (medians, not means)`
  : `Quintile median net return &mdash; awaiting matured horizon`;
document.getElementById("quintileNote").innerHTML = qh
  ? `Current usable horizon: <b>${qh} days</b>. Read on <b>medians</b>: at low effective sample sizes any quintile inversion is <b>noise, not model failure</b>. Means are intentionally discarded — outlier records inflate them.`
  : `No matured quintile horizon is available yet.`;
if(!qvals.length || qvals.every(v => v===null||v===undefined||Math.abs(Number(v))<1e-3)){
  document.getElementById("quintileChart").style.display = "none";
  document.getElementById("quintileEmpty").style.display = "block";
}else{
  safeChart("quintileChart",{
    type:"bar",
    data:{labels:["Q5 Lowest","Q4","Q3","Q2","Q1 Highest"],
      datasets:[{label:`Median net return ${qh}D (%)`,data:qvals,
        backgroundColor:qvals.map(v => v===null ? "rgba(255,255,255,0.08)" : v>=0.5 ? "#3FB950" : v>=0 ? "#38BDB0" : "#F2B13C"),
        borderRadius:6,barPercentage:.72}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>(c.parsed.y??'—')+"% median"}}},
      scales:{y:{grid,title:{display:true,text:"% median net return"}},x:{grid:{display:false}}}}
  });
}

// scatter — minimal: no grid, no ticks, subtle threshold shading, rich tooltip.
const band={id:"band",beforeDraw(ch){const{ctx,chartArea:a,scales:{x,y}}=ch;if(!a)return;ctx.save();
  const rsi=x.getPixelForValue(71.5);ctx.fillStyle="rgba(242,177,60,.06)";ctx.fillRect(rsi,a.top,a.right-rsi,a.bottom-a.top);
  const vol=y.getPixelForValue(30);ctx.fillStyle="rgba(163,113,247,.05)";ctx.fillRect(a.left,a.top,a.right-a.left,vol-a.top);
  ctx.setLineDash([4,4]);ctx.strokeStyle="rgba(242,177,60,0.55)";ctx.lineWidth=1;
  ctx.beginPath();ctx.moveTo(rsi,a.top);ctx.lineTo(rsi,a.bottom);ctx.stroke();
  ctx.strokeStyle="rgba(163,113,247,0.45)";ctx.beginPath();ctx.moveTo(a.left,vol);ctx.lineTo(a.right,vol);ctx.stroke();ctx.restore();}};
safeChart("scatterChart",{
  type:"scatter",plugins:[band],
  data:{datasets:[{label:"Top-20 candidates",data:DATA.scatter,pointRadius:7,pointHoverRadius:10,
    backgroundColor:c=>{const d=c.raw;return (d&&(d.x>=71||d.y>=30))?"#F2B13C":"#38BDB0";},
    borderColor:"rgba(255,255,255,0.85)",borderWidth:1.2}]},
  options:{plugins:{legend:{display:false},
      tooltip:{backgroundColor:"rgba(14,16,26,0.94)",borderColor:"rgba(255,255,255,0.10)",borderWidth:1,
        padding:10,titleColor:"#ECEDEE",bodyColor:"#DEE0E5",
        callbacks:{title:c=>c[0].raw.s, label:c=>[`RSI(14): ${c.raw.x}`,`20D volatility: ${c.raw.y}%`]}}},
    scales:{x:{min:30,max:90,grid:{display:false,drawBorder:false},ticks:{display:false},title:{display:true,text:"RSI(14)",color:"#8A92A6",font:{size:11,weight:"600"}}},
            y:{min:0,suggestedMax:45,grid:{display:false,drawBorder:false},ticks:{display:false},title:{display:true,text:"20-day volatility (%)",color:"#8A92A6",font:{size:11,weight:"600"}}}}}
});


// Watchlist banner + auto-open scatter based on verdict.
const _isLive = DATA.verdict === "Validation Positive";
{
  const wb = document.getElementById("watchBanner");
  if (wb) wb.style.display = _isLive ? "none" : "block";
  const sd = document.getElementById("scatterDetails");
  if (sd && _isLive) sd.open = true;
}

// candidate cards
const dotc={red:"d-red",amber:"d-amber",green:"d-green",dim:"d-dim"};
const _ribbon = _isLive
  ? '<div class="ribbon live">LIVE</div>'
  : '<div class="ribbon watch">WATCHLIST</div>';
document.getElementById("cards").innerHTML = (DATA.cards||[]).map(c=>`
 <div class="card ${c.clean?'clean':''}">
   ${_ribbon}
   <div class="top">
     <div><div class="sym">${c.sym}</div><div class="nm">${c.nm||''}</div></div>
     <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">
       <div class="px"><div class="lbl">Price</div><div class="p">&#8377;${num(c.px)}</div></div>
        ${c.in_shadow_top5 ? '<span class="lblchip shadow">Also in shadow Top 5</span>' : ''}
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
    ${c.bench ? `<div class="perday" style="margin-top:6px;border-top:1px dashed var(--line);padding-top:8px">
      <div class="pd"><div class="l">Excess 21D</div><div class="n">${c.bench.ex21==null?'&mdash;':fmt(c.bench.ex21*100,'%',2)}</div></div>
      <div class="pd"><div class="l">IR 63D</div><div class="n">${fmt(c.bench.ir63,'',2)}</div></div>
      <div class="pd"><div class="l">TE 63D</div><div class="n">${c.bench.te63==null?'&mdash;':fmt(c.bench.te63*100,'%',2)}</div></div>
      <div class="pd"><div class="l">β vs Nifty</div><div class="n">${fmt(c.bench.beta,'',2)}</div></div>
    </div>` : ''}
    ${c.horizon && c.horizon.rec_days ? `<div class="perday" style="margin-top:6px;border-top:1px dashed var(--line);padding-top:8px">
      <div class="pd"><div class="l">Rec hold</div><div class="n">≈${c.horizon.rec_days}d</div></div>
      <div class="pd"><div class="l">Exp return</div><div class="n">${fmt(c.horizon.exp_ret,'%',2)}</div></div>
      <div class="pd"><div class="l">Downside vol</div><div class="n">${fmt(c.horizon.down_vol,'%',2)}</div></div>
      <div class="pd"><div class="l">Sharpe-like</div><div class="n">${fmt(c.horizon.sharpe,'',2)}</div></div>
    </div>${c.horizon.curve ? `<div class="sub" style="margin-top:4px;font-size:11px">Curve %: ${c.horizon.grid.map((h,i)=>`${h}d=${c.horizon.curve[i]==null?'—':c.horizon.curve[i]}`).join(' · ')}</div>` : ''}` : ''}
    ${c.sent ? `<div class="sub" style="margin-top:6px;font-size:11.5px">📰 ${c.sent.n} headlines · 🟢 ${c.sent.pos}% / 🔴 ${c.sent.neg}% · net=${fmt(c.sent.net,'',2)}</div>` : ''}
    <div class="flags">${(c.flags||[]).map(f=>`<div class="flag"><span class="fdot ${dotc[f[0]]||'d-dim'}"></span><b>${f[1]}:</b> ${f[2]}</div>`).join('')}</div>
    ${c.plain ? `<div class="plain">${c.plain}</div>` : ''}
 </div>`).join("") || `<div class="glass panel"><div class="sub">No trade-plan output yet — run the pipeline.</div></div>`;

// Plain-English summary card + permanent disclaimer panel — deterministic HTML built server-side.
(function injectPlainLayer(){
  const s = document.getElementById("plainSummary");
  if (s && DATA.plain_summary_html) s.innerHTML = DATA.plain_summary_html;
  const d = document.getElementById("plainDisclaimer");
  if (d && DATA.plain_disclaimer_html) d.innerHTML = DATA.plain_disclaimer_html;
})();

// Also attach the plain-words line inside shadow-only cards.
(function patchShadowUniquePlain(){
  const host = document.getElementById("shadowUniqueCards");
  if (!host) return;
  const rows = DATA.shadow_unique_top5 || [];
  const els = host.querySelectorAll(".card");
  rows.forEach((c, i) => {
    if (!c.plain) return;
    const el = els[i]; if (!el) return;
    const p = document.createElement("div");
    p.className = "plain";
    p.innerHTML = c.plain;
    el.appendChild(p);
  });
})();


// Correlation matrix tile
(function renderCorr(){
  const cm = DATA.corr_matrix;
  const panel = document.getElementById("corrPanel");
  const title = document.getElementById("corrTitle");
  if(!cm || !cm.labels || cm.labels.length<2){
    if(title) title.style.display="none";
    return;
  }
  panel.style.display="block";
  const badge = document.getElementById("corrAvg");
  if(badge && cm.avg_abs!=null){
    const v = cm.avg_abs;
    const cls = v<0.35 ? "review" : (v<0.6 ? "" : "shadow");
    badge.className = "lblchip " + cls;
    badge.textContent = "avg |corr| = " + v.toFixed(2);
  }
  const cellBg = v => {
    const a = Math.min(Math.abs(v), 1);
    // green (low) → amber → red (high)
    const hue = 130 - a*130; // 130=green, 0=red
    return `hsla(${hue.toFixed(0)},70%,45%,${(0.15+a*0.55).toFixed(2)})`;
  };
  let html = '<table style="width:100%;border-collapse:collapse;font-size:12.5px"><thead><tr><th></th>';
  cm.labels.forEach(l => { html += `<th style="padding:4px 6px;color:var(--muted);font-weight:500">${l}</th>`; });
  html += '</tr></thead><tbody>';
  cm.values.forEach((row,i) => {
    html += `<tr><th style="padding:4px 6px;color:var(--muted);text-align:right;font-weight:500">${cm.labels[i]}</th>`;
    row.forEach((v,j) => {
      const bg = i===j ? "transparent" : cellBg(v);
      const txt = i===j ? "&mdash;" : Number(v).toFixed(2);
      html += `<td style="padding:6px 8px;text-align:center;background:${bg};border:1px solid var(--line)">${txt}</td>`;
    });
    html += '</tr>';
  });
  html += '</tbody></table>';
  document.getElementById("corrTable").innerHTML = html;
})();

const shadowUnique = DATA.shadow_unique_top5 || [];
document.getElementById("shadowUniqueTitle").style.display = shadowUnique.length ? "block" : "none";
document.getElementById("shadowUniqueCards").style.display = shadowUnique.length ? "grid" : "none";
document.getElementById("shadowUniqueCards").innerHTML = shadowUnique.map(c=>`
 <div class="card warn">
   <div class="top">
     <div><div class="sym">${c.sym}</div><div class="nm">${c.nm||''}</div></div>
     <span class="lblchip shadow">Shadow only</span>
   </div>
   <div class="levels" style="grid-template-columns:repeat(2,1fr)">
     <div class="lv"><div class="l">Shadow score</div><div class="n">${c.score==null?'&mdash;':num(c.score)}</div></div>
     <div class="lv"><div class="l">Bucket</div><div class="n">${c.bucket||'Shadow Top 5'}</div></div>
   </div>
   <div class="flags"><div class="flag"><span class="fdot d-amber"></span><b>Shadow:</b> ${c.risk||'Unique to shadow Top 5'}</div></div>
 </div>`).join("");

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

// ── Market context strip (step 4) ──
(function renderMacro(){
  const m = DATA.macro;
  if(!m) return;
  const wrap = document.getElementById("marketCtxWrap");
  const body = document.getElementById("marketCtx");
  const regimeColor = m.regime==="risk-on" ? "#3FB950" : (m.regime==="risk-off" ? "#F2B13C" : "#8A92A6");
  body.innerHTML = `
    <div class="glass panel"><div class="sub">Regime</div><div style="font-size:20px;font-weight:600;color:${regimeColor}">${(m.regime||'neutral').toUpperCase()}</div></div>
    <div class="glass panel"><div class="sub">India VIX</div><div style="font-size:20px;font-weight:600">${fmt(m.vix,'',2)}</div><div class="sub">${m.vix_pct==null?'':`${m.vix_pct}% percentile (252d)`}</div></div>
    <div class="glass panel"><div class="sub">Nifty 50D trend</div><div style="font-size:20px;font-weight:600">${fmt(m.nifty_trend,'%',2)}</div><div class="sub">${m.above_50dma===true?'Above 50-DMA':(m.above_50dma===false?'Below 50-DMA':'—')}</div></div>
    <div class="glass panel"><div class="sub">Read</div><div class="sub" style="margin-top:6px">Sentiment veto and horizon optimizer act only on the top-5 candidates; this strip is the whole-market backdrop.</div></div>`;
  wrap.style.display="block";
})();

// ── Alpha-Zoo evidence panel: standalone IC, residual IC, t-stat, verdict ──
(function renderAlphaEvidence(){
  const ev = DATA.alpha_evidence;
  const legacy = DATA.alpha_zoo;
  const wrap = document.getElementById("alphaZooWrap");
  const cap  = document.getElementById("alphaZooCaption");
  const body = document.getElementById("alphaZooBody");
  const foot = document.getElementById("alphaZooFoot");
  if(!wrap) return;

  // If neither the new evidence payload nor the legacy zoo payload exists,
  // render an explicit "not yet available" panel — never hide the story.
  if(!ev && !legacy){
    cap.innerHTML = `<b>Not yet available.</b> Run the alpha_evaluator step to populate <code>alpha_promotion_log.json</code>, <code>alpha_zoo_ic_report.csv</code>, and <code>alpha_zoo_survivors.json</code>.`;
    body.innerHTML = "";
    foot.innerHTML = "";
    wrap.style.display = "block";
    return;
  }

  if(ev && ev.rows && ev.rows.length){
    const mIC = ev.min_ic==null ? "—" : Number(ev.min_ic).toFixed(3);
    const mT  = ev.min_tstat==null ? "—" : Number(ev.min_tstat).toFixed(2);
    const mR  = ev.min_residual_ic==null ? "—" : Number(ev.min_residual_ic).toFixed(3);
    cap.innerHTML = `Survivor gate: IC &ge; <b>${mIC}</b>, t-stat &ge; <b>${mT}</b>, residual IC &ge; <b>${mR}</b>. New candidates enter live scoring only after clearing all three.`;
    const newSet = new Set(["delivery_momentum","iv_rank"]);
    const chipFor = (r) => {
      if(r.promote === true) return '<span class="azchip green">Promoted</span>';
      if(r.promote === false){
        const belowResidual = r.residual_ic != null && ev.min_residual_ic != null && Math.abs(r.residual_ic) < ev.min_residual_ic;
        if(belowResidual) return '<span class="azchip amber">Watch — below residual gate</span>';
        return '<span class="azchip red">Rejected</span>';
      }
      return '<span class="azchip dim">Baseline (no eval)</span>';
    };
    const fmtIC = v => (v==null) ? '&mdash;' : (v>=0?'+':'') + Number(v).toFixed(3);
    const fmtT  = v => (v==null) ? '&mdash;' : Number(v).toFixed(2);
    let html = '<table class="aztable"><thead><tr>'
      + ['Alpha','Standalone IC','Residual IC','t-stat','Windows','Verdict']
        .map(h=>`<th>${h}</th>`).join('')
      + '</tr></thead><tbody>';
    ev.rows.forEach(r => {
      const nameCell = `${r.alpha}${newSet.has(r.alpha) ? ' <span class="aznew">NEW CANDIDATE</span>' : ''}`;
      const reason = r.reason ? `<div class="sub" style="font-size:10.5px;margin-top:2px">${r.reason}</div>` : '';
      html += `<tr>
        <td>${nameCell}${reason}</td>
        <td class="num">${fmtIC(r.standalone_ic)}</td>
        <td class="num">${fmtIC(r.residual_ic)}</td>
        <td class="num">${fmtT(r.tstat)}</td>
        <td class="num">${r.windows==null?'&mdash;':r.windows}</td>
        <td>${chipFor(r)}</td>
      </tr>`;
    });
    html += '</tbody></table>';
    body.innerHTML = html;
    const src = ev.sources || {};
    const bits = [];
    if(!src.promotion_log) bits.push("alpha_promotion_log.json not yet available");
    if(!src.ic_report)     bits.push("alpha_zoo_ic_report.csv not yet available");
    if(!src.survivors)     bits.push("alpha_zoo_survivors.json not yet available");
    foot.innerHTML = bits.length ? "Partial sources: " + bits.join(" &middot; ") + "." : "";
    wrap.style.display = "block";
    return;
  }

  // Fall back to the legacy zoo payload when only IC/survivor data exists.
  if(legacy){
    if(legacy.survivors && legacy.survivors.length){
      cap.innerHTML = `<b>${legacy.count}</b> signal${legacy.count===1?'':'s'} cleared IC/t-stat thresholds (IC&ge;${legacy.min_ic}, |t|&ge;${legacy.min_tstat}). Residual-IC evidence not yet available.`;
      let html = '<table class="aztable"><thead><tr>'
        + ['Alpha','Horizon (d)','Mean IC','t-stat','Hit rate'].map(h=>`<th>${h}</th>`).join('')
        + '</tr></thead><tbody>';
      legacy.survivors.forEach(s=>{
        html += `<tr><td>${s.alpha}</td><td class="num">${s.horizon}</td><td class="num">${(s.mean_IC>0?'+':'')+Number(s.mean_IC).toFixed(3)}</td><td class="num">${s.t_stat==null?'—':Number(s.t_stat).toFixed(2)}</td><td class="num">${s.hit_rate==null?'—':(s.hit_rate*100).toFixed(0)+'%'}</td></tr>`;
      });
      body.innerHTML = html + '</tbody></table>';
    } else if(legacy.top_by_ic && legacy.top_by_ic.length){
      cap.innerHTML = `No alpha cleared IC/t-stat thresholds yet — top by |mean IC| for review. Scoring blend stays disabled.`;
      let html = '<table class="aztable"><thead><tr>'
        + ['Alpha','Horizon (d)','Mean IC','t-stat'].map(h=>`<th>${h}</th>`).join('')
        + '</tr></thead><tbody>';
      legacy.top_by_ic.forEach(s=>{
        html += `<tr><td>${s.alpha}</td><td class="num">${s.horizon}</td><td class="num">${Number(s.mean_IC||0).toFixed(3)}</td><td class="num">${s.t_stat==null?'—':Number(s.t_stat).toFixed(2)}</td></tr>`;
      });
      body.innerHTML = html + '</tbody></table>';
    } else {
      cap.innerHTML = `<b>Not yet available.</b> No survivors or IC report to display.`;
      body.innerHTML = "";
    }
    foot.innerHTML = "";
    wrap.style.display = "block";
  }
})();

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
