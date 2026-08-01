# Contributing

Thanks for your interest. This project is a research screener, not a trading
product. Contributions are judged on **evidence and honesty** first.

## Ground rules

1. **Never commit runtime data.** `desktop/nse_quant_engine/data/` and
   `desktop/nse_quant_engine/output/` are git-ignored. They hold your local
   market caches, validation history and evidence bundles. Do not add them,
   and do not paste their contents into issues.
2. **News stays context-only.** News, filings and sentiment must never feed
   scores, ranks or portfolio selection.
3. **Ranking authority is fixed.** Official ordering is
   `Confidence_Adjusted_Score` descending, `Symbol` ascending as tie-breaker.
   `Raw_Score_Rank` is diagnostic only. Changing this requires an explicit
   discussion issue first.
4. **Adaptive weighting stays dormant.** `ADAPTIVE_ENABLED` must remain
   `False` on the default branch.
5. **`validation_status.json` is authoritative.** Never infer a verdict by
   scraping a Markdown report.
6. **No proven edge means no live trading.** Watchlist-only behaviour is a
   feature, not a bug to be worked around.

## Development setup

```bash
cd desktop/nse_quant_engine
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install pytest
```

## Running tests

```bash
cd desktop/nse_quant_engine
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
python -m compileall -q .
```

Tests must pass **without network access**. Anything touching a public data
source must be mocked or fed from `tests/fixtures/`.

For the web shell:

```bash
bun install
bun run lint
bun run build
```

## Adding a new alpha or signal

New alphas are welcome but must:

- Register in `core/alpha_zoo.py` with a deterministic, point-in-time
  computation (no look-ahead).
- Pass the **incremental / residual IC gate** in `core/alpha_evaluator.py` —
  measured on the residual after regressing out existing survivors.
- Survive walk-forward out-of-sample evaluation. In-sample IC alone is not
  evidence.
- **Not** bypass survivor gates, shrinkage, cost modelling or the validation
  verdict.
- Ship with unit tests, including a degenerate-input case.

A signal that only looks good in-sample will be rejected.

## Pull requests

- Keep diffs focused; no drive-by refactors of scoring or validation.
- Fill in the pull-request checklist honestly.
- Update `CHANGELOG.md` under "Unreleased".
