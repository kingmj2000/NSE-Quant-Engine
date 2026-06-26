
# NSE Quant Engine — Cleanup, New Factors, One-Button Runner

Scope: keep your proven Python data layer, fix the real flaws, add the four new factor families you picked, ship a single desktop button that runs the official pipeline and the v4.1 shadow side-by-side and writes a champion recommendation. No auto-switch — recommendation only.

Deliverable is a patched version of the zip. The TanStack sandbox here is unrelated; everything ships back as a downloadable archive plus a `presentation-artifact` link.

---

## 1. One-button local runner

**Choice: Python + PySide6 desktop app** (over Tauri/Rust).

Why: every existing module is Python, you already have a venv flow, and a Qt single-file app is the lowest-friction "double-click, hit Run" path. Rust/Tauri would mean re-implementing the orchestrator just to call Python. Streamlit was an option but needs a browser tab and a running server; PySide6 is one window with no server.

New file `run_app.py` + `run_app.bat` (and `run_app.command` for mac/linux):

- Single window. Top: **[ Run Full Pipeline ]** button. Toggles: *Include Shadow (v4.1)*, *Refresh Data*, *Verbose*.
- Live log pane (tails stdout/stderr from each step, color-coded pass/fail).
- Bottom tabs: **Scores** (latest_scores), **Shadow** (latest_scores_v4_shadow), **Compare** (rank-diff table), **DQ Report** (`dq_report.md`), **Validation** (verdict JSON), **Trade Plan**.
- Status bar shows last-run timestamp, champion recommendation, and the data-quality grade.
- Pipeline orchestrator is a plain Python module (`orchestrator.py`) the GUI calls — also runnable headless via `python orchestrator.py --all` so the .bat keeps working and CI/cron stays possible.

Logical step sequence (single button):

```text
universe_builder
  -> etf_quality_builder            (first pass)
  -> etf_metadata_enricher          (first pass: build NAV/AUM map)
  -> etf_aum_auto_fetcher
  -> etf_ter_tracking_auto_fetcher
  -> etf_metadata_enricher          (second pass: coalesce AUM/TER/tracking)
  -> etf_quality_builder            (second pass: enriched)
  -> dq_report_builder              (NEW — fill-rate, staleness, source mix)
  -> nse_quant_engine               (official)
  -> nse_quant_engine_v4_shadow     (always run; comparison only)
  -> validation_builder
  -> cross_sectional_validation     (writes validation_status.json)
  -> trade_plan_builder             (reads status JSON, no markdown scraping)
  -> news_market_builder
  -> shadow_vs_official_report      (NEW — champion recommendation)
```

Each step is a function the GUI runs in a `QThread`, so the window stays responsive and the user can cancel.

---

## 2. Champion / shadow comparison (manual switch only)

New `shadow_vs_official_report.py` writes `output/shadow_vs_official.md` + `.csv` containing:

- Top-25 rank delta (Jaccard overlap, Spearman ρ, mean rank shift).
- Per-bucket forward-return performance once `forward_return_history.csv` has ≥4 weeks of overlap.
- Validation verdict for each engine, side by side.
- Filtered EV/day for each engine (top-quintile only, gated on Validation Positive).
- A plain-English recommendation line, e.g. *"Shadow leads on spread (+0.42pp) and hit-rate (+6.1pp) over 6 weeks. Insufficient history to auto-promote — review manually before switching."*

No automatic promotion. The current `nse_quant_engine.py` remains the official output. A `config.csv` row (`Engine_Champion = official | shadow`) lets you flip manually later; if set to `shadow`, the GUI swaps which file is treated as `latest_scores.csv` downstream.

---

## 3. Data-quality flag cleanup (root-cause + structured taxonomy + dq_report)

**Root-cause fixes**

- `etf_metadata_enricher.py`: enforce ISIN-first join across AMFI sources before name fuzzy-matching. The current "Tracking disclosure unavailable" / "TER missing" mix is largely a join-order artefact — TER comes from one AMFI workbook, tracking from another, and only the last writer wins. Coalesce in a fixed precedence: manual override → AMFI TER workbook → AMFI scheme NAV → fallback. Same for AUM and tracking-error/tracking-difference.
- Stop down-weighting ETFs purely because AMFI hasn't published a tracking number this month. `etf_quality_builder.py` will treat *missing* differently from *bad*: missing = neutral, only an actually large tracking error or premium loses points. This mirrors the v4 core's stated principle.
- Stale NAV detection: any NAV older than 5 business days is flagged `STALE_NAV` instead of silently used.

**Structured flag taxonomy** (replaces free-text `ETF_Quality_Data_Flag`):

```text
OK
MISSING_TER
MISSING_TRACKING
STALE_NAV
LOW_AUM
UNRESOLVED_MAPPING
PRICE_GAP
```

Multiple flags allowed; stored as a pipe-joined column plus one boolean column per flag for easy filtering.

**Nightly delta refresh + dq_report.md**

- `dq_report_builder.py` (NEW): walks every enriched CSV and writes `output/dq_report.md` + `data/dq_metrics.csv` with fill-rate per field, source mix, staleness histogram, top 20 unresolved mappings, and a single 0–100 *Data Health Score*. The GUI tab renders this directly.
- Delta refresh: each fetcher gets a `--since YYYY-MM-DD` mode that only pulls AMFI workbooks newer than the last successful pull (tracked in `data/fetch_sla_log.csv`). Already-present rows are skipped. Faster, fewer rate-limit failures.

---

## 4. Analysis / logic-flaw fixes

Things in the current code that are quietly wrong, and the planned fix:

- **Momentum triple-counting**: the v4 `core/scoring.py` already fixes it but `nse_quant_engine.py` doesn't fully use it. Wire `core.scoring.compute_opportunity_scores` + `apply_fundamental_factor` into the official engine, gated by a config flag so behaviour is reproducible.
- **Validation by markdown scraping**: `trade_plan_builder.py` parses prose. Replace with `core.validation_status.read_status('output/validation_status.json')`. Make `cross_sectional_validation.py` write that JSON unconditionally.
- **Falling-knife promotion**: enforce `absolute_filter_cap` so negative-21D names cannot reach the top-N regardless of percentile, with the cap value in `scoring_rules.csv`.
- **Volatility floor**: cap `vol_20d` at 2% floor in `risk_adjusted_momentum` so dead instruments don't print Sharpe-like scores.
- **Tracking-error as universal demerit**: switch to *neutral when unavailable*; only penalise when present and large.
- **Forward-return history alignment**: `validation_builder.py` currently keys forward returns by trade-date row index; switch to a left-join on `(Symbol, Score_Date)` so renamed/removed symbols don't silently drop windows.
- **Bootstrap leak**: the bootstrap re-samples *dates*, not date+symbol pairs, which is correct; but the current code computes the t-stat on the same sample it picks the spread from. Add a HAC (Newey-West, 5-lag) adjustment to the t-stat as the README's `Adjusted_TStat` field implies but doesn't currently compute.
- **Cost model**: `expected_value.py` uses a flat round-trip cost; add per-universe costs from `config.csv` (ETF vs stock) so EV is honest per instrument type.

---

## 5. New factor families (all four you picked)

All are additive and behind config switches so they can be A/B'd in shadow before adoption.

### India VIX regime filter
- Pull `^INDIAVIX` via yfinance alongside `^NSEI`.
- Compute `VIX_Percentile_252D`. Regime = LOW (<30), MID (30–70), HIGH (>70).
- Scale `VOL_PENALTY_MAX` and tighten `MIN_ABS_RETURN_21D` in HIGH regime. Loosen in LOW. Values exposed in `scoring_rules.csv`.
- Add `Regime` column to outputs and to the comparison report.

### Sector / breadth context
- Fetch the 11 NSE sector indices (`^CNXAUTO`, `^CNXBANK`, `^CNXFMCG`, `^CNXIT`, `^CNXPHARMA`, `^CNXMETAL`, `^CNXENERGY`, `^CNXREALTY`, `^CNXMEDIA`, `^CNXPSUBANK`, `^CNXFIN`).
- Map each stock to a sector (yfinance `info['sector']` with manual override CSV for misses).
- New columns: `Sector_RS_21D`, `Sector_Rank_In_Universe`, `Breadth_AD_Pct` (advance-decline proxy = % of Nifty 500 above 50D MA, computed from existing cache).
- Sector RS becomes a soft multiplier alongside market RS (geometric mean of the two).

### ETF microstructure
- From `raw_prices_latest.csv` compute: 20D median traded value, intraday H-L spread % (proxy for bid-ask), `iNAV_Premium_Z` (z-score of `(Price-NAV)/NAV` over last 60 days).
- New hard filters: `Avg_Traded_Value_20D >= LIQUIDITY_MIN` (configurable, default ₹2 crore/day), `abs(iNAV_Premium_Z) <= 2.5`.
- ETF quality gets these as additional sub-scores; trade plan flags any pick that fails them.

### Fundamentals expansion (stocks only)
- Existing `core/fundamental_factor.py` already pulls ROE/PE/debt/growth/margins; add:
  - **FCF yield** = `freeCashflow / marketCap`.
  - **Earnings revisions** = sign of trailing-vs-forward EPS delta from yfinance `info`.
  - **Piotroski-lite (5 of 9)**: positive net income, positive CFO, CFO>NI, lower long-term debt YoY, higher current ratio YoY.
- Re-uses existing `Fundamental_Coverage` guard so a single missing field cannot move ranks. Coverage threshold stays at the configured minimum.

---

## 6. Files to add / change

```text
NEW
  run_app.py                       PySide6 GUI, single window, Run button + tabs
  run_app.bat / run_app.command    double-click entrypoints
  orchestrator.py                  headless pipeline runner, used by GUI + cron
  dq_report_builder.py             writes dq_report.md and dq_metrics.csv
  shadow_vs_official_report.py     champion comparison + recommendation
  core/regime.py                   VIX + breadth helpers
  core/sector_context.py           sector mapping + sector RS
  core/etf_microstructure.py       liquidity, spread proxy, iNAV z-score
  core/data_quality.py             flag taxonomy + validators
  tests/test_regime.py
  tests/test_microstructure.py
  tests/test_dq_flags.py

PATCH
  nse_quant_engine.py              use core.scoring; add regime/sector/microstructure columns
  cross_sectional_validation.py    write validation_status.json + HAC t-stat
  validation_builder.py            join forward returns on (Symbol, Score_Date)
  trade_plan_builder.py            read structured status; per-universe cost; EV gating
  etf_metadata_enricher.py         ISIN-first coalesce, fixed precedence
  etf_quality_builder.py           neutral-when-missing, structured flags, STALE_NAV
  core/expected_value.py           per-universe cost from config.csv
  core/config.py + scoring_rules.csv   new keys for VIX, liquidity, sector
  README.md / WORKFLOW.md          one-button instructions, new flag taxonomy
```

`core/__init__.py` exposes the new modules. Old `.bat` files still work; they now just call `orchestrator.py` so behaviour matches the GUI exactly.

---

## 7. Verification

- Extend `tests/test_core.py` (currently 12/12). Add unit tests for: structured flag taxonomy, ISIN-first coalesce precedence, regime scaling, sector RS multiplier composition, microstructure filters, HAC t-stat, EV gating with mixed universes. Target ≥25/25 passing without network.
- Headless smoke run of `orchestrator.py --all --offline` using only files already in `/tmp/nse/data/` so we can confirm the wiring end-to-end before you run it locally with live yfinance/AMFI.
- GUI smoke: launch headless via `pytest-qt` to verify the window builds, the Run thread starts, and the log pane receives output.

---

## 8. Out of scope (intentionally)

- Any auto-switch from official to shadow. Recommendation only, you flip the flag.
- Replacing yfinance with a paid feed.
- Rust/Tauri shell. Python + PySide6 stays in one language and the existing modules drop straight in. We can revisit Tauri later if you want a distributable installer.
- Deploying anything to the Lovable web sandbox — this is a local desktop tool.

After approval I'll patch the zip, run the offline smoke + unit tests, and hand back a downloadable `nse_quant_engine_patched.zip` plus a short changelog.
