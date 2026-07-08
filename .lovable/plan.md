
## What the two repos actually offer us

**Fincept Terminal** — Bloomberg-style desktop (Qt6/C++ + Python), ~100 data connectors, alt-data (Adanos: Reddit/X/news/Polymarket sentiment), buy-side toolkit (equity screening, portfolio, derivatives), AI-agent screens. Useful inspiration: *breadth of data sources + sentiment overlay + screener UX*, not code we can lift (AGPL + commercial license, C++/Qt architecture is orthogonal to our PyQt/pandas engine).

**Vibe-Trading** — Python agentic quant research stack. Directly relevant primitives:
- **Alpha Zoo** — 452 pre-built formulaic alphas (qlib158, alpha101, gtja191, academic FF/Carhart) with lookahead-guard + AST purity tests.
- **Hypothesis Registry** — create / link / invalidate hypotheses tied to backtest run cards → forces every signal change to leave an audit trail.
- **Run cards** (`run_card.json`/`.md`) for reproducible research runs.
- **Correlation heatmap** for portfolio/symbol return correlations.
- **Benchmark comparison panel** (excess return, information ratio).
- **Trust Layer / security boundary** patterns for sandboxed tool exec.
- **PIT-safe fundamental fields** contract (Tushare) — mirrors our need for a PIT guard on yfinance fundamentals.

Both are heavy platforms; we should port *concepts*, not code, and only what tightens the top-5 pick.

## Scope of this plan

Purely additive changes to `desktop/nse_quant_engine/` — no UI framework swap, no new heavy deps. Everything below is guarded so a missing data source degrades gracefully (matches existing fallback pattern).

## Proposed additions, in priority order

### 1. Expand the alpha bench (biggest expected lift on precision of top-5)
Right now `scoring.py` uses one composite (blended momentum × trend × RS − penalties). Vibe-Trading's Alpha Zoo shows the value of scoring many orthogonal alphas and blending only the ones that survive validation.

- New module `core/alpha_zoo.py` implementing ~15 low-cost, price/volume-only alphas: 12-1 momentum, 52w-high proximity, Amihud illiquidity (inverse), Chaikin money flow, OBV slope, mean-reversion (5D z), volatility-adjusted breakout, gap-fill, term-structure of vol (20D/60D), residual momentum vs sector, low-vol anomaly, drawdown recovery, earnings-window avoidance flag, up-day ratio, and a "quiet uptrend" (low vol × positive slope) — all pure pandas.
- New CLI `alpha bench` in `orchestrator.py` producing per-alpha spread stats via the existing `cross_sectional_validation.py`.
- `scoring.compute_opportunity_scores` gains a `blend_validated_alphas(...)` step that includes only alphas whose validation verdict is positive (reuses existing gating in `expected_value.py`).

### 2. Hypothesis registry + run cards (reproducibility → better iteration)
- `core/hypothesis_registry.py` — JSON store under `output/hypotheses/`: `create`, `link_run`, `invalidate(note)`, `list`. Every scoring/weight change writes a hypothesis id into the run card.
- `run_app.py` and `orchestrator.py` emit `output/run_cards/<run_id>.json` + `.md` with: git-like hash of scoring config, universe size, top-5 IDs, validation verdict, EV stats, and links to artifacts.
- Dashboard: new "Run History" panel listing last 20 run cards.

### 3. Sentiment / news overlay (Fincept inspiration, optional connector)
`news_market_builder.py` already exists but isn't a per-symbol signal.
- Add `core/sentiment_overlay.py`: pulls per-symbol recent headline sentiment (VADER on titles from an RSS aggregator; degrades to neutral if offline).
- Applied as a small ±5% multiplier on `Final_Score`, capped, and only for the top-N candidates (cheap).
- Weight configurable in `config.py`, default 0.05.

### 4. Correlation-aware top-5 selection (materially improves "low-risk" claim)
Today top-5 can be 5 IT stocks. Port Vibe-Trading's correlation heatmap idea into selection:
- `core/portfolio_selection.py`: from the top-N (e.g. 25) by `Final_Score`, greedily pick 5 that maximise `score − λ · max_pairwise_corr_60d`.
- Show the correlation heatmap of the final 5 on the dashboard (Chart.js matrix — no new deps if we use canvas cells).

### 5. Benchmark & information-ratio panel on dashboard
Add per-candidate: 21D excess return vs Nifty50, rolling IR (63D), tracking-error vs benchmark. This is what Vibe-Trading's benchmark panel exposes and it directly answers "is this actually adding alpha over just buying the index?".

### 6. Short-hold optimisation
User goal is *shortest hold duration*. Add:
- `core/hold_horizon_optimizer.py`: for each candidate, scan matured `forward_return_history` slices at horizons {3, 5, 10, 15, 21} and pick the horizon that maximises EV-per-day subject to `p_win ≥ 0.55`.
- Report the chosen horizon per candidate in the trade plan card instead of a fixed 10-day default.

### 7. Lookahead / PIT guards (Vibe-Trading's discipline)
- Add a unit test in `tests/test_new_modules.py` that fails if any alpha in `alpha_zoo.py` references a future-indexed value (pattern check on `.shift(-` and negative rolling windows).
- Wrap fundamental fetch in a "PIT-safe" flag: fundamentals used in scoring must be tagged with `as_of` ≤ signal date.

### 8. Small UX polish (Fincept inspiration)
- Screener tab: a lightweight filter bar on the Trade Plan / Validation tabs (min score, max vol, universe, sector) — pure client-side JS filtering, no pipeline rerun.
- "Data sources health" strip at the top of the dashboard (last-fetch age per source), reusing existing `fallback_source_log.csv`.

## What we deliberately do NOT copy

- Fincept's C++/Qt6 rewrite — our stack stays PyQt/pandas.
- Vibe-Trading's LLM-agent orchestration / MCP server / swarm — outside our scope and cost profile.
- Fincept's paid data connectors / broker integrations — we stay yfinance + NSE public + AMFI.
- Any AGPL-licensed code copied verbatim.

## Delivery order (each is a separate build turn)

1. **Alpha Zoo v0 + validation wiring** (items 1, 7) — highest expected lift.
2. **Correlation-aware top-5 + benchmark panel** (items 4, 5) — sharpens "low risk" claim.
3. **Hold-horizon optimizer** (item 6) — answers "shortest hold".
4. **Hypothesis registry + run cards** (item 2) — reproducibility.
5. **Sentiment overlay + screener/health UX** (items 3, 8) — nice-to-have.

## Files that will change (indicative)

```text
core/alpha_zoo.py                 (new)
core/portfolio_selection.py       (new)
core/hold_horizon_optimizer.py    (new)
core/hypothesis_registry.py       (new)
core/sentiment_overlay.py         (new, guarded)
core/scoring.py                   (blend validated alphas)
core/config.py                    (weights + flags)
orchestrator.py                   (bench + run card emit)
dashboard_html_builder.py         (corr heatmap, benchmark panel, run history, health strip, screener)
tests/test_new_modules.py         (PIT/lookahead guards, alpha zoo unit tests)
```

## Confirm before I build

Please confirm:
1. Green-light **all 5 delivery steps**, or a subset (which ones)?
2. OK to add ~15 pure-pandas alphas without adding new pip dependencies?
3. For sentiment overlay: add VADER (`vaderSentiment`, small pure-Python dep) — yes/no?
