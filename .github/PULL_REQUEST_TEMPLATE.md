## Summary

<!-- What changes and why. Link the related issue. -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation / tooling
- [ ] Refactor (no behaviour change)

## Required confirmations

- [ ] **Ranking authority unchanged** — official ordering is still
      `Confidence_Adjusted_Score` descending with `Symbol` ascending as
      tie-breaker; `Raw_Score_Rank` remains diagnostic only.
- [ ] **No validation history deleted or rewritten** — `output/` history,
      schema versions and `validation_status.json` semantics are intact.
- [ ] **News remains context-only** — no news, filing or sentiment input
      affects scores, ranks, portfolio selection or the trade plan.
- [ ] **Adaptive weighting not enabled** — `ADAPTIVE_ENABLED` is still `False`.
- [ ] **Tests pass** — `QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q`
      and `python -m compileall -q .` succeed locally.
- [ ] **No runtime data committed** — nothing from `data/` or `output/`, no
      `.env`, credentials or personal portfolio files.

## Evidence (required for scoring, validation or alpha changes)

<!-- Out-of-sample results, residual IC vs existing survivors, cost assumptions.
     Write "N/A" if this PR touches none of those. -->

## Verification performed

<!-- Commands actually run and their outcome. -->
