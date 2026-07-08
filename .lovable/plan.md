# Remaining Roadmap — Steps 10–13

Steps 1–9 are done. The current engine already covers: correlation-aware top-5, benchmark/IR, horizon optimizer, sentiment + macro regime, alpha-zoo IC, fundamentals overlay, position sizing, walk-forward backtest, and the AI-handoff evidence zip. The gaps below are the last meaningful pieces from the two reference repos that still map to the "PM-grade NSE opportunity finder" goal — everything stays **100% local, no runtime AI, no new pip deps**.

## Step 10 — Sector & Peer Context Pack (Fincept-inspired)
**New:** `core/sector_context.py`
- Per pick: sector membership, sector 1M/3M return vs Nifty, sector breadth (% of sector above 50-DMA), peer table (top 3 peers by market cap with PE / ROE / 3M return).
- Purely derived from data already cached (yfinance + universe file). No new fetches.
- Output: `output/top5_sector_context.csv` + peer rows folded into `evidence.json`.
- Dashboard: "Sector Snapshot" strip on each top-5 card.
- Why it matters: PMs never recommend a name without peer context; this closes that gap for the LLM handoff.

## Step 11 — Event & Catalyst Calendar (Fincept-inspired, offline)
**New:** `core/event_calendar.py`
- Pulls next earnings date, ex-dividend date, and any corporate-action flags already present in yfinance's cached `.calendar` / `.actions` (no extra network — reuses fundamentals cache).
- Flags picks where earnings fall **inside** the recommended hold horizon → new column `Event_Risk_Flag` (`Pre-Earnings` / `Post-Earnings` / `Clear`).
- Feeds into `Trade_Status`: earnings inside horizon downgrades confidence, not the pick.
- Output: `output/top5_events.csv`, merged into evidence bundle + rationale prompt.

## Step 12 — Expected-Value & Kelly-Lite Sizing Cross-Check (Vibe-Trading-inspired)
**New:** `core/expected_value.py`
- Combines: backtest hit rate (Step 9) + avg win/loss (Step 9) + downside vol (Step 3) → per-pick **EV_%** and **Kelly_Fraction_Capped** (hard-capped at 25% of the risk-parity weight).
- Report-only column next to Step 8 sizing — never overrides risk-parity by default (`KELLY_OVERRIDE=0`).
- Output: `output/top5_expected_value.csv` + surfaced in rationale prompt as a sanity check ("does the sizing agree with the edge?").

## Step 13 — Portfolio-Level Validation Gate (both repos)
**New:** `core/portfolio_validation.py`
- One consolidated pre-flight check that runs after Steps 6–12 and writes `output/portfolio_validation.json`:
  - avg pairwise |corr| ≤ threshold
  - sum of Max_Loss_%_of_NAV ≤ NAV risk cap
  - sector concentration ≤ cap (e.g. no >40% single sector)
  - backtest hit rate ≥ floor OR macro regime = RISK_OFF → downgrades entire batch to "Watch"
  - # of alpha survivors ≥ floor
- Emits `Batch_Verdict`: `Ship` / `Ship_With_Caveats` / `Downgrade_To_Watch`, echoed on the dashboard header and in the evidence README.
- This is the piece Fincept and Vibe both do that the current engine doesn't: an explicit portfolio-level go/no-go gate rather than only per-stock scoring.

## Cross-cutting
- All 4 modules guarded in `trade_plan_builder.py` (`try/except` + `INSIGHT_SAFE_MODE=1` disables). No pip changes. No network.
- Extend `evidence_bundle.py` to include the 4 new CSVs + JSON, and update `prompts/rationale_prompt.md` so the LLM references sector context, event risk, EV cross-check, and the batch verdict.
- Add ~8 unit tests under `tests/test_new_modules.py`.

## Explicitly out of scope (won't do)
- Options/derivatives overlay, live paper-trading, multi-agent LLM debate, any runtime AI call, any new pip dep or API key.

---

# Running the workflow — unchanged

You are still good with the existing entry points. Nothing about the run command has changed after Steps 6–9, and Steps 10–13 will also plug into the same flow with no new commands.

- Windows: double-click **`run_app.bat`** (or `run_full_workflow.bat` for the end-to-end batch).
- Mac: **`run_app.command`**.
- Manual: `python run_app.py` from `desktop/nse_quant_engine/`.

New outputs to look for after each run (already produced by Steps 6–9, more added by 10–13):
- `output/top5_fundamentals.csv`, `top5_position_sizing.csv`, `backtest_scorecard.csv`
- `output/insight_bundle_<timestamp>.zip` ← this is the file to hand to Claude/any LLM; the prompt file is baked inside as `README_for_AI.md` + `prompts/rationale_prompt.md`.

Kill switches if anything misbehaves:
- `INSIGHT_SAFE_MODE=1` → disables Steps 6–13, engine reverts to Steps 1–5 output.
- `QUALITY_WEIGHT=0.0` (default) keeps fundamentals report-only until you've reviewed IC.

Approve this and I'll implement Steps 10 → 11 → 13 → 12 in that order (validation gate before EV so EV can feed into it).
