"""
Central configuration for NSE Quant Engine (clean core v4).

EVERY tunable number lives here — score weights, cost haircuts, thresholds,
validation gates, refresh cadence. No magic numbers scattered across modules.

Override any value with an environment variable of the same name, e.g.:
    set FUNDAMENTAL_WEIGHT=0.20
"""

from __future__ import annotations
import os


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return default


# ── Scoring weights (technical core) ────────────────────────────────────────
# Momentum is the PRIMARY signal. Trend and relative-strength are applied as
# soft confirmation multipliers, NOT additive thirds — this removes the
# triple-counting of "recent return" that plagued earlier versions.
RISK_ADJ_MOMENTUM_IS_PRIMARY = True

# Soft confirmation multipliers (applied to the momentum percentile).
TREND_CONFIRM_MULT      = _f("TREND_CONFIRM_MULT", 1.00)   # price above key MAs
TREND_FAIL_MULT         = _f("TREND_FAIL_MULT", 0.85)      # below MAs -> haircut
RS_CONFIRM_MULT         = _f("RS_CONFIRM_MULT", 1.00)      # beating benchmark
RS_FAIL_MULT            = _f("RS_FAIL_MULT", 0.90)         # lagging benchmark

# Risk penalties (subtracted from the 0-100 opportunity score).
VOL_PENALTY_MAX         = _f("VOL_PENALTY_MAX", 15.0)
DRAWDOWN_PENALTY_MAX    = _f("DRAWDOWN_PENALTY_MAX", 15.0)
OVERBOUGHT_RSI          = _f("OVERBOUGHT_RSI", 75.0)
OVERBOUGHT_PENALTY      = _f("OVERBOUGHT_PENALTY", 12.0)

# Momentum horizon blend (must sum to 1.0). Multi-horizon reduces single-window noise.
MOM_W_5D                = _f("MOM_W_5D", 0.20)
MOM_W_21D               = _f("MOM_W_21D", 0.50)
MOM_W_63D               = _f("MOM_W_63D", 0.30)

# Absolute filters — stop "least-bad falling knife" promotion in down markets.
MIN_ABS_RETURN_21D      = _f("MIN_ABS_RETURN_21D", 0.0)    # must be > 0
MAX_ABS_DROP_5D         = _f("MAX_ABS_DROP_5D", -0.08)     # not crashing this week

# ── Fundamental / quality factor (NEW, optional) ────────────────────────────
# Folded into the final score at this weight. Starts LOW on purpose: it relies
# on a data source (yfinance fundamentals) that is patchy for NSE and unverified
# against your live feed. Raise only after the validation layer shows it helps.
FUNDAMENTAL_WEIGHT      = _f("FUNDAMENTAL_WEIGHT", 0.15)   # 0.0 disables it
FUNDAMENTAL_APPLIES_TO_ETF = False                        # ETFs get neutral
# Only apply fundamental score when enough fields are populated. A score built
# from one accidental metric should not move ranking like it discovered alpha.
FUNDAMENTAL_MIN_COVERAGE = _f("FUNDAMENTAL_MIN_COVERAGE", 0.60)

# ── Transaction costs (round-trip, fraction) ────────────────────────────────
STOCK_COST              = _f("STOCK_COST", 0.0035)
ETF_COST_HIGH_LIQ       = _f("ETF_COST_HIGH_LIQ", 0.0025)
ETF_COST_MID_LIQ        = _f("ETF_COST_MID_LIQ", 0.0050)
ETF_COST_LOW_LIQ        = _f("ETF_COST_LOW_LIQ", 0.0100)
ETF_MID_LIQ_VALUE       = _f("ETF_MID_LIQ_VALUE", 100_000_000.0)
ETF_LOW_LIQ_VALUE       = _f("ETF_LOW_LIQ_VALUE", 20_000_000.0)

# ── Validation gates (cross-sectional) ──────────────────────────────────────
CROSSVAL_MIN_DATES          = _i("CROSSVAL_MIN_DATES", 10)
CROSSVAL_MIN_EFFECTIVE_DATES= _i("CROSSVAL_MIN_EFFECTIVE_DATES", 6)
CROSSVAL_MIN_OBS            = _i("CROSSVAL_MIN_OBS", 50)
CROSSVAL_MIN_SPREAD         = _f("CROSSVAL_MIN_SPREAD", 0.005)   # 0.5% net
CROSSVAL_MIN_HITRATE        = _f("CROSSVAL_MIN_HITRATE", 0.55)
CROSSVAL_MIN_TSTAT          = _f("CROSSVAL_MIN_TSTAT", 1.5)
CROSSVAL_MIN_BOOTSTRAP_PROB = _f("CROSSVAL_MIN_BOOTSTRAP_PROB", 0.90)

# ── ATR / trade-plan ────────────────────────────────────────────────────────
ATR_STOP_MULT           = _f("ATR_STOP_MULT", 1.5)
ATR_T1_MULT             = _f("ATR_T1_MULT", 1.5)
ATR_T2_MULT             = _f("ATR_T2_MULT", 2.5)
HOLD_DAYS_MIN           = _i("HOLD_DAYS_MIN", 5)
HOLD_DAYS_MAX           = _i("HOLD_DAYS_MAX", 15)

# ── ETF data-quality flags ──────────────────────────────────────────────────
# Tracking error is NOT published by AMFI for most ETFs. Treat its absence as a
# neutral informational note, not a quality demerit, when everything else is present.
TRACKING_UNAVAILABLE_IS_NEUTRAL = True

# ── Expected value gates ─────────────────────────────────────────────────────
# EV is only meaningful when computed for a relevant candidate bucket/type, not
# blindly from all historical signals. Keep a minimum observation floor.
EV_MIN_OBS = _i("EV_MIN_OBS", 50)

# ── Forward-return horizons ─────────────────────────────────────────────────
HORIZONS = (5, 10, 21)

# ── Correlation-aware top-5 (step 2) ────────────────────────────────────────
# Feature-flagged: any exception during selection silently falls back to
# score-only ranking so the pipeline can never regress on this change.
CORR_AWARE_TOP5    = os.environ.get("CORR_AWARE_TOP5", "1") not in ("0", "false", "False")
CORR_AWARE_POOL_N  = _i("CORR_AWARE_POOL_N", 25)
CORR_AWARE_ALPHA   = _f("CORR_AWARE_ALPHA", 0.65)   # score vs diversification tradeoff
CORR_WINDOW_DAYS   = _i("CORR_WINDOW_DAYS", 60)

# ── Safe-mode kill switch (steps 3-5) ───────────────────────────────────────
# One env var to disable ALL new post-pipeline enrichment in a single shot,
# e.g. INSIGHT_SAFE_MODE=1 when debugging.
_SAFE_MODE         = os.environ.get("INSIGHT_SAFE_MODE", "0") in ("1", "true", "True")

# ── Step 3: Hold-Horizon Optimizer ──────────────────────────────────────────
HORIZON_OPTIMIZER_ON = (not _SAFE_MODE) and (
    os.environ.get("HORIZON_OPTIMIZER_ON", "1") not in ("0", "false", "False"))
HORIZON_GRID       = [3, 5, 10, 21, 42, 63]
HORIZON_HIST_DAYS  = _i("HORIZON_HIST_DAYS", 250)
HORIZON_RISK_CAP_PCT = _f("HORIZON_RISK_CAP_PCT", 6.0)

# ── Step 4: Sentiment / macro overlay ───────────────────────────────────────
SENTIMENT_OVERLAY_ON = (not _SAFE_MODE) and (
    os.environ.get("SENTIMENT_OVERLAY_ON", "1") not in ("0", "false", "False"))
SENTIMENT_VETO_ON    = (not _SAFE_MODE) and (
    os.environ.get("SENTIMENT_VETO_ON", "1") not in ("0", "false", "False"))
SENT_LOOKBACK_DAYS = _i("SENT_LOOKBACK_DAYS", 7)
SENT_NEG_VETO_PCT  = _f("SENT_NEG_VETO_PCT", 0.60)
SENT_MIN_HEADLINES = _i("SENT_MIN_HEADLINES", 3)

# ── Step 5: Alpha-Zoo evaluation + gated tilt ───────────────────────────────
ALPHA_ZOO_ON       = (not _SAFE_MODE) and (
    os.environ.get("ALPHA_ZOO_ON", "1") not in ("0", "false", "False"))
ALPHA_ZOO_WEIGHT   = _f("ALPHA_ZOO_WEIGHT", 0.10)
ALPHA_IC_MIN       = _f("ALPHA_IC_MIN", 0.03)
ALPHA_TSTAT_MIN    = _f("ALPHA_TSTAT_MIN", 2.0)
ALPHA_EVAL_DAYS    = _i("ALPHA_EVAL_DAYS", 250)
ALPHA_EVAL_FOLDS   = _i("ALPHA_EVAL_FOLDS", 4)
ALPHA_MIN_SURVIVORS_FOR_TILT = _i("ALPHA_MIN_SURVIVORS_FOR_TILT", 3)

# ── Step 6: Fundamentals & Quality Overlay ──────────────────────────────────
FUNDAMENTALS_OVERLAY_ON = (not _SAFE_MODE) and (
    os.environ.get("FUNDAMENTALS_OVERLAY_ON", "1") not in ("0", "false", "False"))
QUALITY_WEIGHT           = _f("QUALITY_WEIGHT", 0.0)  # report-only until IC reviewed
VALUATION_LOOKBACK_YEARS = _i("VALUATION_LOOKBACK_YEARS", 3)

# ── Step 7: Evidence bundle (offline AI handoff) ────────────────────────────
EVIDENCE_BUNDLE_ON   = (not _SAFE_MODE) and (
    os.environ.get("EVIDENCE_BUNDLE_ON", "1") not in ("0", "false", "False"))
BUNDLE_MAX_MB        = _f("BUNDLE_MAX_MB", 5.0)
BUNDLE_KEEP_LAST_N   = _i("BUNDLE_KEEP_LAST_N", 10)

# ── Step 8: Position sizer ──────────────────────────────────────────────────
POSITION_SIZER_ON    = (not _SAFE_MODE) and (
    os.environ.get("POSITION_SIZER_ON", "1") not in ("0", "false", "False"))
SIZING_MODE          = os.environ.get("SIZING_MODE", "risk_parity_lite")
PORTFOLIO_VOL_TARGET = _f("PORTFOLIO_VOL_TARGET", 0.12)
PORTFOLIO_NAV_INR    = _f("PORTFOLIO_NAV_INR", 1_000_000.0)
MAX_WEIGHT           = _f("MAX_WEIGHT", 0.30)
CASH_BUFFER          = _f("CASH_BUFFER", 0.10)

# ── Step 9: Walk-forward backtest ───────────────────────────────────────────
BACKTEST_ON            = (not _SAFE_MODE) and (
    os.environ.get("BACKTEST_ON", "1") not in ("0", "false", "False"))
BACKTEST_LOOKBACK_DAYS = _i("BACKTEST_LOOKBACK_DAYS", 250)
BACKTEST_STALE_DAYS    = _i("BACKTEST_STALE_DAYS", 7)
BACKTEST_HOLD_DAYS     = _i("BACKTEST_HOLD_DAYS", 10)
BACKTEST_REBAL_EVERY   = _i("BACKTEST_REBAL_EVERY", 5)

# ── Step 10: Sector & Peer Context ──────────────────────────────────────────
SECTOR_CONTEXT_ON      = (not _SAFE_MODE) and (
    os.environ.get("SECTOR_CONTEXT_ON", "1") not in ("0", "false", "False"))

# ── Step 11: Event & Catalyst Calendar ──────────────────────────────────────
EVENT_CALENDAR_ON      = (not _SAFE_MODE) and (
    os.environ.get("EVENT_CALENDAR_ON", "1") not in ("0", "false", "False"))

# ── Step 12: Expected-Value / Kelly cross-check (report-only) ───────────────
EV_REPORT_ON           = (not _SAFE_MODE) and (
    os.environ.get("EV_REPORT_ON", "1") not in ("0", "false", "False"))
KELLY_CAP_OF_WEIGHT    = _f("KELLY_CAP_OF_WEIGHT", 0.25)
KELLY_OVERRIDE         = os.environ.get("KELLY_OVERRIDE", "0") in ("1", "true", "True")

# ── Step 13: Portfolio-level validation gate ────────────────────────────────
PORTFOLIO_VALIDATION_ON      = (not _SAFE_MODE) and (
    os.environ.get("PORTFOLIO_VALIDATION_ON", "1") not in ("0", "false", "False"))
PV_MAX_AVG_ABS_CORR          = _f("PV_MAX_AVG_ABS_CORR", 0.70)
PV_MAX_PORTFOLIO_LOSS_PCT    = _f("PV_MAX_PORTFOLIO_LOSS_PCT", 3.0)
PV_MAX_SINGLE_SECTOR_PCT     = _f("PV_MAX_SINGLE_SECTOR_PCT", 60.0)
PV_MIN_BACKTEST_HIT_RATE     = _f("PV_MIN_BACKTEST_HIT_RATE", 0.50)
PV_MIN_ALPHA_SURVIVORS       = _i("PV_MIN_ALPHA_SURVIVORS", 2)

# ── Step 14: Institutional Flow Overlay ─────────────────────────────────────
INSTITUTIONAL_FLOW_ON    = (not _SAFE_MODE) and (
    os.environ.get("INSTITUTIONAL_FLOW_ON", "1") not in ("0", "false", "False"))
FII_LOOKBACK_DAYS        = _i("FII_LOOKBACK_DAYS", 5)
BULK_DEALS_LOOKBACK_DAYS = _i("BULK_DEALS_LOOKBACK_DAYS", 30)

# ── Step 15: Regime-conditional alpha tilt (report-only default) ────────────
REGIME_TILT_ON     = (not _SAFE_MODE) and (
    os.environ.get("REGIME_TILT_ON", "1") not in ("0", "false", "False"))
REGIME_TILT_APPLY  = os.environ.get("REGIME_TILT_APPLY", "0") in ("1", "true", "True")

# ── Step 16: Rebalance diff / turnover report ───────────────────────────────
REBALANCE_DIFF_ON       = (not _SAFE_MODE) and (
    os.environ.get("REBALANCE_DIFF_ON", "1") not in ("0", "false", "False"))
REBAL_ROUND_TRIP_COST_PCT = _f("REBAL_ROUND_TRIP_COST_PCT", 0.35)


# ── Part A (tightened plan): sector-neutral scoring + turnover-aware weights ─
SECTOR_NEUTRAL              = os.environ.get("SECTOR_NEUTRAL", "1") not in ("0", "false", "False")
SECTOR_NEUTRAL_MIN_MEMBERS  = _i("SECTOR_NEUTRAL_MIN_MEMBERS", 5)
TURNOVER_LAMBDA             = _f("TURNOVER_LAMBDA", 0.25)
ALPHA_INCREMENTAL_IC_MIN    = _f("ALPHA_INCREMENTAL_IC_MIN", 0.015)

# ── Part B (dormant, shadow-only): adaptive alpha weighting ─────────────────
# Every guardrail here is mandatory. Keep ADAPTIVE_ENABLED=False in production
# until the shadow-vs-primary report gives you an unambiguous reason to flip.
ADAPTIVE_ENABLED            = os.environ.get("ADAPTIVE_ENABLED", "0") in ("1", "true", "True")
ADAPTIVE_MIN_DATES          = _i("ADAPTIVE_MIN_DATES", 60)      # effective (overlap-adjusted)
ADAPTIVE_SHRINKAGE_ALPHA    = _f("ADAPTIVE_SHRINKAGE_ALPHA", 0.20)
ADAPTIVE_MAX_STEP           = _f("ADAPTIVE_MAX_STEP", 0.05)
ADAPTIVE_MAX_TOTAL_DRIFT    = _f("ADAPTIVE_MAX_TOTAL_DRIFT", 0.30)
ADAPTIVE_RIDGE_ALPHA        = _f("ADAPTIVE_RIDGE_ALPHA", 1.0)
# Baseline alpha weights used by the shadow adaptive fit.
# NON-OVERLAPPING BY DESIGN: never mix component scores (momentum, trend, safety)
# with composite scores (Opportunity_Score = f(momentum, trend, ...) or Final_Score
# which is target-adjacent). Including both re-introduces the momentum triple-count
# we removed earlier and destabilizes the ridge fit via collinearity.
ALPHA_WEIGHTS               = {"momentum": 0.5, "trend": 0.3, "safety": 0.2}
# Hard collinearity guardrail on the alpha panel handed to fit_adaptive_weights.
ADAPTIVE_MAX_ALPHA_CORR     = _f("ADAPTIVE_MAX_ALPHA_CORR", 0.8)

# ── Single source of ranking truth for Top-5 across dashboard / trade plan / validated ─
# Confidence_Adjusted_Score embeds data-completeness and regime tilt, so a candidate
# can rank lower for missing metadata rather than worse raw prospects. Raw Final_Score
# is displayed alongside in the Top-5 UI for transparency. Falls back to Final_Score
# only when the primary column is entirely NaN (logged).
RANKING_COLUMN              = os.environ.get("RANKING_COLUMN", "Confidence_Adjusted_Score")
RANKING_COLUMN_FALLBACK     = "Final_Score"

# ── Dashboard shadow-green gate ─────────────────────────────────────────────
# Dashboard "GREEN" chip must not be easier to earn than the documented
# six shadow-switch criteria. Green requires ALL of:
#   1. official verdict = Validation Positive
#   2. consecutive shadow-lead runs      >= SHADOW_GREEN_MIN_STREAK
#   3. consecutive verdict-positive runs >= SHADOW_GREEN_MIN_STREAK
#   4. shadow matured-independent obs    >= SHADOW_GREEN_MIN_MATURED_OBS
SHADOW_GREEN_MIN_STREAK       = _i("SHADOW_GREEN_MIN_STREAK", 8)
SHADOW_GREEN_MIN_MATURED_OBS  = _i("SHADOW_GREEN_MIN_MATURED_OBS", CROSSVAL_MIN_EFFECTIVE_DATES)

# ── Validation-layer Bayesian shrinkage (protects ship/hold gate) ───────────
# Separate from adaptive-weight shrinkage; always on unless explicitly disabled.
VALIDATION_BAYES_SHRINK        = os.environ.get("VALIDATION_BAYES_SHRINK", "1") not in ("0", "false", "False")
VALIDATION_HITRATE_PRIOR_ALPHA = _f("VALIDATION_HITRATE_PRIOR_ALPHA", 10.0)
VALIDATION_HITRATE_PRIOR_BETA  = _f("VALIDATION_HITRATE_PRIOR_BETA", 10.0)
VALIDATION_IC_PRIOR_N          = _i("VALIDATION_IC_PRIOR_N", 20)

# ── Output retention ────────────────────────────────────────────────────────
# Retention for accumulating dated artifacts (dashboards, dated score snapshots,
# enricher backups, TER debug xlsx). Keeps the most recent N per pattern.
# Set to 0 to disable pruning entirely (safety escape hatch).
# Never applies to PROTECTED_FILES / rolling history CSVs.
RETENTION_KEEP_N = _i("RETENTION_KEEP_N", 10)
