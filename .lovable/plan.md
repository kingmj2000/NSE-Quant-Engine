# Final Roadmap — Steps 14–16 + docs refresh

Steps 1–13 are shipped. Comparing the current engine against the two reference repos one more time, three genuinely PM-relevant gaps remain — all local, all offline, no new pip deps, no runtime AI. Everything else in Fincept / Vibe Trading is either a terminal-UI feature, a duplicate of what we already have, or an LLM/agent behaviour the external Claude handoff already covers.

## Step 14 — Institutional-Flow Overlay (Fincept-inspired)
**New:** `core/institutional_flow.py`
- Ingests two optional local CSVs the user can drop into `data/`:
  - `fii_dii_daily.csv` (Date, FII_Net_INR_Cr, DII_Net_INR_Cr) — from NSE/BSE daily bhav report
  - `bulk_deals.csv` (Date, Symbol, Client, Buy_Sell, Qty, Price) — NSE bulk/block deals CSV
- Derives per-pick and per-batch flags:
  - `FII_Regime` (net-buying / net-selling / mixed, 5-day rolling)
  - `Bulk_Deal_Flag` (Buy / Sell / None, last 30 days, aggregated by direction)
  - `Institutional_Confirmation` (Yes / No / Unknown) — feeds the confidence label
- Output: `output/top5_institutional_flow.csv` + `macro_context.json` gets `fii_regime`.
- Files are optional: absent → columns are `Unknown`, no failure. Every module gracefully degrades if the user never downloads the CSVs.

## Step 15 — Regime-Conditional Scoring Tilt (Vibe-Trading-inspired)
**New:** `core/regime_tilt.py`
- Reads the existing `macro_context.json` (regime + India VIX) and reweights the alpha-zoo tilt (Step 5) at runtime:
  - `RISK_OFF` → double-weight the low-vol / mean-reversion survivors, halve the momentum survivors
  - `RISK_ON` → double-weight momentum / breakout survivors
  - `NEUTRAL` → equal weight (current behaviour)
- **Report-only by default** (`REGIME_TILT_APPLY=0`) so ranking is unchanged; the recommended tilt is written to `output/regime_tilt_report.json` for the LLM handoff and for a human to promote later once it's been eyeballed for a few weeks.
- New alpha families are NOT introduced — this only re-weights the survivors we already trust.

## Step 16 — Turnover / Rebalance Report vs Previous Top-5
**New:** `core/rebalance_diff.py`
- On every run, compares today's top-5 to the last run's `trade_plan_latest.csv` snapshot (kept as `output/history/top5_prev.csv`).
- Emits `output/rebalance_diff.json`:
  - `holds` (still in top-5), `exits` (dropped out — with reason: score decay / sentiment veto / horizon breach / event risk), `entries` (newly promoted)
  - `estimated_turnover_%` (share of NAV that would rotate) + `estimated_round_trip_cost_%`
  - `net_edge_after_cost_%` = expected excess return of the new basket − estimated round-trip cost
- Prevents the classic "engine churns every week and gives up its edge to friction" failure mode Fincept flags in its terminal.
- Small file, folded into the evidence bundle so the LLM can explicitly recommend "hold" vs "rotate".

## Cross-cutting
- All 3 modules guarded by `try/except` + a `_SAFE_MODE` env flag; new config knobs in `core/config.py`.
- Extend `evidence_bundle.py` to include the 3 new artifacts; extend `prompts/rationale_prompt.md` so the LLM output must comment on institutional flow, regime tilt agreement, and rotate-vs-hold.
- Add ~4 unit tests.
- **Docs refresh:** update `WORKFLOW.md`, `README.md`, `INTEGRATION_GUIDE.md`, `CHANGES_v4_2.md` to cover Steps 6–16 in one pass (currently they stop at older versions).

## Explicitly out of scope — will not build
Called out to close the loop on what's *not* worth porting from the two repos:

- Options overlay / derivatives greeks (Fincept) — F&O is a different risk product; the user's brief is cash-market NSE stocks + ETFs.
- Live paper-trading loop (both) — the engine is a research pipeline, not a broker adapter.
- Multi-agent LLM debate / self-critique (Vibe) — the external Claude handoff already gives the user one strong analyst voice; adding a second agent locally would re-introduce runtime AI, which the user explicitly excluded.
- Terminal UI (Fincept) — outside project scope (this project already ships an HTML dashboard).
- Alt-data (satellite, credit card) — none of it is freely available for NSE.

## Running the workflow
No change. Still `run_app.bat` / `run_app.command` / `python run_app.py`. Steps 14–16 will slot into the same guarded post-processing block as 10–13, and their outputs will land in `output/` and inside `insight_bundle_<ts>.zip` automatically.

If FII/DII and bulk-deal CSVs are not dropped into `data/`, Step 14 quietly emits `Unknown` and the pipeline is otherwise unchanged — you can adopt it incrementally.

---

Approve this and I'll implement in order **14 → 16 → 15 → docs**, then this integration track is done.
