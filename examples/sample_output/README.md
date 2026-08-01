# Synthetic sample output

**Every file in this folder is fabricated.** Symbols (`AAAA`, `BBBB`, `CCCC`,
`DDDD`, `EEEE`), prices, scores, ranks, dates and headlines are invented to
document the *shape* of each artifact. They are **not** market data, not real
NSE instruments, not model output, and **not investment data or advice**.

No accumulated production market data, validation evidence or user portfolio
history from a real run is published in this repository.

| File | Real counterpart | Shape it documents |
|---|---|---|
| `latest_scores.csv` | `output/latest_scores.csv` | Per-symbol score / rank row |
| `validation_status.json` | `output/validation_status.json` | Canonical verdict + schema version |
| `news_digest.json` | `output/news_digest.json` | Context-only news digest |
| `daily_changes.json` | `output/daily_changes.json` | Entrants/exits, gainers, risk flags |

Real artifacts are produced locally by `python orchestrator.py --all` and stay
on your machine — see [`docs/DATA_SOURCES.md`](../../docs/DATA_SOURCES.md).
