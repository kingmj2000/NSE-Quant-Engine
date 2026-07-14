# Fix adaptive fit, shadow inputs, unified Top‑5 ranking (v2)

Approved with two adjustments folded in: non-redundant alpha panel + raw `Final_Score` visible alongside `Confidence_Adjusted_Score`. Adaptive remains dormant by default.

## Issue 1 — Adaptive panel is missing alpha columns

**Root cause.** `_run_adaptive_shadow()` only keeps `Date` + `Fwd_Return`, so `fit_adaptive_weights` sees `alpha_cols = []` and always returns baseline. Per-alpha component scores were never persisted per (Date, Symbol).

**Fix.**
1. **Baseline alpha keys — non-overlapping only.** In `core/config.py`:
   ```
   ALPHA_WEIGHTS = {"momentum": …, "trend": …, "safety": …}
   ```
   Explicitly **exclude** `Opportunity_Score` (composite of the components — would collinearize the ridge, reintroducing the momentum triple-count) and `Final_Score` (target-adjacent). Add a code comment stating this rule.
2. **Persist per-alpha scores per (Date, Symbol).** In `nse_quant_engine.py`, append a new `output/alpha_score_history.csv` next to `signal_history.csv`, columns `Date, Symbol, momentum, trend, safety` (renamed from `Momentum_Score`, `Trend_Score`, `Safety_Score`). Uses existing `append_history`.
3. **Build the real training panel in `_run_adaptive_shadow()`.** Inner‑join `alpha_score_history.csv` with `forward_return_history.csv` on `(Date, Symbol)`; drop `Symbol` before passing (guardrail #5); pass `Date + alphas + Fwd_Return`.
4. **Collinearity guardrail.** Before fitting, compute the pairwise Pearson correlation matrix of the alpha columns on the training panel. Log it to `adaptive_weights_log.json` as `alpha_corr_matrix`. If `max(|corr_ij|)` for i≠j exceeds `ADAPTIVE_MAX_ALPHA_CORR` (default 0.8), force dormant with `dormant_reason="alpha collinearity <pair>=<r>"`. This makes the anti-triple-count rule mechanical, not just documented.
5. **Fail-soft.** Missing file / empty join → dormant with specific `dormant_reason`.

**Tests.**
- Panel builder produces DataFrame with every baseline alpha key + `Fwd_Return`, no `Symbol`.
- With sufficient uncorrelated synthetic rows, verdict positive, `ADAPTIVE_ENABLED=True` → fit returns `dormant=False` and `shrunk_final != baseline`.
- With deliberately collinear alphas → fit stays dormant with `alpha collinearity` reason and corr matrix logged.

## Issue 2 — Shadow running on missing inputs

**Fix.**
1. Re-verify `nse_quant_engine.py` writes the full column set to `latest_scores.csv` every run (MA_20/50/200, Above_*, Benchmark_Return_5/10/21/63D, Relative_Strength_5/10/21/63D, Price). Add a write-time assertion listing any missing shadow-required cols; fail loudly if any missing.
2. `nse_quant_engine_v4_shadow.py`: record every input it had to neutralize as `neutralized_inputs: [{name, reason}]` on `shadow_mode_summary.json` and print a summary line. After the fix, list must be empty.
3. Confirm `fundamentals_latest.csv` scaffold path runs every time so shadow always sees `Symbol + Fundamental_Score` (all-NaN → AMBER health, never silent).

**Verification artefact.** Show shadow run log with `neutralized_inputs: []`.

## Issue 3 — One ranking column for Top‑5 everywhere

**Fix.**
1. `core/config.py`: `RANKING_COLUMN = "Confidence_Adjusted_Score"` with a **code comment**:
   > This embeds data-completeness and regime tilt, so a candidate can rank lower for missing metadata rather than worse prospects. Raw `Final_Score` is shown alongside in Top-5 for transparency.
   Fallback to `Final_Score` only if column is entirely NaN, logged.
2. Route `trade_plan_builder.py`, `cross_sectional_validation.py`, `dashboard_html_builder.py` Top‑5 selection through this constant.
3. **Dashboard Top-5 table shows both columns** — a `Rank_Score` column (labeled `Confidence_Adjusted_Score`) and a `Raw_Score` column (labeled `Final_Score`) side by side. Header reads `Top 5 (ranked by Confidence_Adjusted_Score; raw Final_Score shown for transparency)`.
4. `_assert_top5_alignment(cards, trade_plan_df)` compares the ordered 5 symbols; on mismatch render a RED honesty chip listing both lists. Never silently paper over.

**Tests.**
- Dashboard Top‑5 selection and `trade_plan_builder` Top‑5 return identical ordered symbol lists on a shared synthetic frame.
- Alignment assertion returns a mismatch payload when lists differ.

## Verification checklist

- (a) Print panel columns handed to `fit_adaptive_weights` (contains every baseline alpha key + `Fwd_Return`, no `Symbol`) **plus** the logged `alpha_corr_matrix` with all off-diagonal |r| < 0.8.
- (b) `shadow_mode_summary.json` with `neutralized_inputs: []`.
- (c) Programmatic check: dashboard Top‑5 symbols == trade plan Top‑5 symbols, same order.

## Files touched

- `core/config.py` (add `ALPHA_WEIGHTS` (non-overlapping), `RANKING_COLUMN`, `ADAPTIVE_MAX_ALPHA_CORR`; comments)
- `nse_quant_engine.py` (write `alpha_score_history.csv`; assert shadow columns present)
- `nse_quant_engine_v4_shadow.py` (build real panel; corr-matrix guardrail; `neutralized_inputs`)
- `core/adaptive_weights.py` (compute + log `alpha_corr_matrix`; enforce `max_alpha_corr`)
- `trade_plan_builder.py`, `cross_sectional_validation.py` (route sort through `RANKING_COLUMN`)
- `dashboard_html_builder.py` (Top‑5 header label, dual-column table, alignment assertion + RED chip)
- `tests/test_new_modules.py` (panel builder, collinearity guardrail, Top‑5 alignment)

Adaptive stays dormant by default — the plumbing being *capable* of non-baseline output is proof of correctness, not a signal to enable.
