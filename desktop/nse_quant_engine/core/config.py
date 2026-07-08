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
