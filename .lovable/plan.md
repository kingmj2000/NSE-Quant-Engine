# Confirming Fincept Terminal + Vibe Trading integrations

## What the terminal output already proves ran

Your `trade_plan_builder` log shows every inspired module firing and writing artifacts:

| Inspiration source | Concept borrowed | Module that ran | Artifact in your log |
|---|---|---|---|
| Fincept Terminal — research desk layout | Sector / peer context per pick | `core/sector_context.py` | `top5_sector_context.csv` |
| Fincept — earnings/event calendar tab | Event-risk window flag | `core/event_calendar.py` | `top5_events.csv` |
| Fincept — institutional flow panel (FII/DII, bulk deals) | FII regime + bulk-deal confirmation | `core/institutional_flow.py` | `top5_institutional_flow.csv (fii_regime=Unknown)` |
| Fincept — macro regime dashboard | Regime detection + report | `core/regime.py` | `macro_context.json (regime=neutral)` |
| Vibe Trading — alpha zoo / IC gating | Multi-alpha IC survivorship | `core/alpha_zoo.py` + `alpha_evaluator.py` | `alpha_zoo_ic_report.csv + alpha_zoo_survivors.json (7 survivors)` |
| Vibe Trading — regime-conditional alpha tilt | Family reweighting by regime | `core/regime_tilt.py` | `regime_tilt_report.json (mode=REPORT_ONLY)` |
| Vibe Trading — expected-value / Kelly sizing | EV + fractional Kelly | `core/expected_value.py`, `core/position_sizer.py` | `top5_expected_value.csv`, `top5_position_sizing.csv (sum weight=83.9%)` |
| Vibe Trading — walk-forward style backtest | Style backtest + scorecard | `core/backtest_engine.py` | (writes when enough history; part of insight bundle) |
| Vibe Trading — portfolio-level gate | Batch ship/hold verdict | `core/portfolio_validation.py` | `portfolio_validation.json — Batch_Verdict=Ship` |
| Vibe Trading — turnover-vs-cost check | Rebalance diff + net edge | `core/rebalance_diff.py` | `rebalance_diff.json (turnover=20.0%, rec=Hold_Minor_Adjustment)` |
| Both — portable evidence handoff to an LLM | Zip bundle + baked prompt | `core/evidence_bundle.py` + `prompts/rationale_prompt.md` | `insight_bundle_20260708_1238.zip (170 KB)` + `[step7] Upload ... to Claude` |

So the integrations are wired end-to-end. What's missing is that nothing in the run *labels* these steps as Fincept- / Vibe-inspired, so the run looks generic.

## Why it feels invisible

- Log lines are neutral ("Saved: top5_sector_context.csv"). No provenance tag.
- `WORKFLOW.md` describes steps but doesn't credit the source repos per step.
- The UI tabs added last turn (Portfolio, Macro & Rotation) exist but have no "Inspired by" badges.
- Some outputs read as no-ops on this run because inputs are missing (FII=Unknown → no `data/fii_dii_daily.csv`; 0 fundamentals scored → no `data/fundamentals_latest.csv`; 0 events → empty calendar). They ran; they had nothing to chew on.

## Plan (docs + light UI only, no analytics changes)

1. **Add `desktop/nse_quant_engine/INSPIRATION_MAP.md`** — one-page table mapping every borrowed concept to the file, the artifact, and the enabling input file (so you know what to drop into `data/` to light each one up). Same table as above, expanded with "how to activate".

2. **Tag pipeline log lines with provenance.** In `trade_plan_builder.py`, prefix the existing `Saved: ...` lines for the borrowed steps with a short tag, e.g. `[fincept] Saved: top5_sector_context.csv`, `[vibe] Saved: alpha_zoo_ic_report.csv ...`. Print-only change; no logic touched.

3. **Add an "Inspiration" section to `WORKFLOW.md`** cross-referencing the 16 steps to Fincept/Vibe features.

4. **UI provenance badges in `run_app.py`.** On the Portfolio and Macro & Rotation tab headers, add a small subtitle like `Sector & Peers · inspired by Fincept research desk`, `Alpha Zoo · inspired by Vibe Trading`, `Rebalance Diff · inspired by Vibe Trading turnover gate`. Text-only.

5. **Add an "Activation checklist" panel to the Dashboard** listing the optional input files (`data/fii_dii_daily.csv`, `data/bulk_deals.csv`, `data/fundamentals_latest.csv`, earnings calendar) with ✅ / ⚠️ present-or-missing status, so it's obvious *why* a borrowed step returned Unknown/empty on this run and how to feed it.

6. **README bump.** Add a "Credits & inspiration" section to `desktop/nse_quant_engine/README.md` linking both repos and pointing to `INSPIRATION_MAP.md`.

## Not in scope

- No changes to scoring, sizing, validation, or bundle contents.
- No new dependencies.
- `run_app.bat` / `run_app.command` unchanged — same one-click run.

## Files to touch

- New: `desktop/nse_quant_engine/INSPIRATION_MAP.md`
- Edit (print tags only): `desktop/nse_quant_engine/trade_plan_builder.py`
- Edit (docs): `desktop/nse_quant_engine/WORKFLOW.md`, `desktop/nse_quant_engine/README.md`
- Edit (UI labels + activation panel): `desktop/nse_quant_engine/run_app.py`

After this, one look at the terminal, the UI, or `INSPIRATION_MAP.md` will make it unambiguous which Fincept/Vibe ideas are live and which are dormant waiting for optional input files.
