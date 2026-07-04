# NSE Quant Engine — Clean Core v4: Integration Guide

This is **not** a from-scratch replacement. It's a clean, unit-tested *core*
that fixes the real analytical flaws and adds your two new analysis pieces,
designed to bolt onto your existing, proven data-fetching modules.

## Why it's structured this way (read first)

Your live data layer (yfinance downloads, AMFI NAV/AUM/TER fetchers, the
metadata enricher) is **battle-tested across dozens of real runs**. It is also
network-dependent, which means it cannot be verified in a sandbox. Rewriting it
blind would trade working code for unproven code. So this package rewrites only:

- the parts where the **real flaws live** (scoring, validation plumbing), and
- the parts that are **pure logic I could fully unit-test** (12/12 passing).

Everything network-dependent stays as your current proven version.

## What's in this package

```
core/
  config.py              All weights, costs, thresholds, gates — one place.
  price_cache.py         Incremental raw-price cache (speed + adjustment-drift fix).
  scoring.py             De-correlated scoring: momentum primary, trend/RS as gates.
  fundamental_factor.py  NEW: quality/value factor (low default weight, optional).
  expected_value.py      NEW: cost-aware EV per holding day (gated on validation).
  validation_status.py   Structured verdict JSON — kills the report-scraping bug.
tests/
  test_core.py           12 unit tests, all passing.
```

## Swap / keep / add — per file

### REPLACE the logic inside these existing scripts

| Your current file | Replace its … with … |
|---|---|
| `nse_quant_engine.py` | scoring math → import and call `core.scoring.compute_opportunity_scores` then `apply_fundamental_factor`. Keep its data-loading and output-writing. |
| `cross_sectional_validation.py` | verdict logic → call `core.validation_status.decide_verdict` and `write_status(...)`. Keep the spread/t-stat/bootstrap math you already have; just feed its outputs into `decide_verdict` as the `stats` dict. |
| `trade_plan_builder.py` | the markdown-parsing verdict reader → `core.validation_status.read_status(...)`. Add an EV column via `core.expected_value.expected_value_per_day`. |

### KEEP exactly as-is (proven, network-dependent)

- `universe_builder.py`
- `etf_quality_builder.py`
- `etf_metadata_enricher.py`  *(with the coalesce fix already applied)*
- `etf_aum_auto_fetcher.py`, `etf_ter_tracking_auto_fetcher.py`
- `news_market_builder.py`
- `validation_builder.py`  *(but see price-cache note below)*

### ADD (new capability)

- The whole `core/` folder — drop it into your project root.
- A fundamentals step: once a week, call `core.fundamental_factor.fetch_fundamentals`
  on your stock symbols, `build_quality_score`, and save to
  `data/fundamentals_latest.csv`. The engine reads it if present.

## Wiring: minimal changes to your engine

In `nse_quant_engine.py`, after you've built the per-symbol indicator table
(returns, vol, RSI, MAs, benchmark return), replace the score block with:

```python
from core import scoring, config as C
import pandas as pd, os

# optional fundamentals (skip silently if file absent)
fpath = "data/fundamentals_latest.csv"
if os.path.exists(fpath):
    df = df.merge(pd.read_csv(fpath)[["Symbol","Fundamental_Score"]], on="Symbol", how="left")

df = scoring.compute_opportunity_scores(df)   # de-correlated momentum core
df = scoring.apply_fundamental_factor(df)      # folds in quality at C.FUNDAMENTAL_WEIGHT
# df now has Opportunity_Score and Final_Score
```

In `cross_sectional_validation.py`, after computing your spread/tstat/bootstrap:

```python
from core import validation_status as vs

stats = {
    "validation_dates": n_dates,
    "effective_validation_dates": eff_dates,
    "avg_obs": avg_obs,
    "spread": avg_top_minus_bottom,
    "hit_rate": hit_rate,
    "adj_tstat": adjusted_tstat,
    "bootstrap_prob": bootstrap_prob_positive,
}
verdict, grade = vs.decide_verdict(stats)
vs.write_status("output/validation_status.json", verdict, grade, stats, horizon=10)
# keep writing your .md report too — but it's now DECORATIVE, not authoritative
```

In `trade_plan_builder.py`, replace report-scraping with:

```python
from core import validation_status as vs, expected_value as ev
import pandas as pd

status = vs.read_status("output/validation_status.json")   # structured, not parsed prose
fwd = pd.read_csv("output/forward_return_history.csv")      # (handle empty-file as you do now)
ev_row = ev.expected_value_per_day(fwd, status, horizon=10)
# ev_row["ev_per_day"] is the honest "profit %/day" — NaN until validation positive
```

## Price-cache adoption (highest-value change — do this one first)

This is the single biggest win for both speed and correctness. In your yfinance
download wrapper, after you melt the download to long form (`Date, Symbol, Price`):

```python
from core import price_cache as pc
cache = pc.load_cache("data/price_history_cache.csv")
need = pc.symbols_needing_download(cache, universe_symbols, pd.Timestamp.today())
fresh = your_yfinance_download(need)          # only download what's missing/stale
cache = pc.update_cache(cache, fresh, freeze_history=True)   # never re-adjust history
pc.save_cache(cache, "data/price_history_cache.csv")
prices_wide = pc.wide_window(cache, lookback_days=400)       # feed indicators from here
```

`freeze_history=True` is what stops yfinance silently re-basing past prices and
corrupting your forward-return validation. Only use `freeze_history=False` for a
deliberate full rebuild after a known split.

## Recommended adoption order (lowest risk first)

1. **Drop in `core/` and run the tests** (`python tests/test_core.py`) — zero risk, confirms the logic.
2. **Wire `validation_status.json`** into validation + trade-plan. Kills the false-positive bug. Low risk.
3. **Adopt the price cache.** Biggest speed + correctness gain. Test one run against your old output.
4. **Swap in the new scoring.** This changes your rankings (that's the point — de-correlated). Compare a few runs side-by-side before trusting.
5. **Turn on fundamentals at low weight (0.15)** and EV column. Let validation tell you over the coming weeks whether the fundamental factor helps.

## Honest boundaries

- `fundamental_factor.fetch_fundamentals` is the **one piece not verified against
  your live feed** (no network here). Run it on ~5 symbols and eyeball the numbers
  before a full-universe run. The *scoring* logic on top of it is tested.
- New scoring will reorder your candidates. It is more defensible, not magic.
- The EV column is honest precisely because it returns blank until validation
  clears — don't "fix" that blank; it's the correct answer until the evidence exists.
- None of this makes "low-risk, highest-profit, shortest-hold" achievable. It
  makes the screener cleaner, faster, more honest, and able to *tell you whether
  it works.* That remains the real goal.
