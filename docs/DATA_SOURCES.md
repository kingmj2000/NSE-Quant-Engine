# Data sources

All data is fetched from **public, free** endpoints at run time. Nothing is
redistributed in this repository.

## What is *not* in this repository

- No accumulated market data.
- No price, delivery or option caches.
- No validation history or evidence bundles.
- No user portfolio or watchlist files.

Those live only on your machine under:

```
desktop/nse_quant_engine/data/     # caches (git-ignored)
desktop/nse_quant_engine/output/   # results, history, evidence (git-ignored)
```

`examples/sample_output/` contains **synthetic** files that document artifact
shapes only.

## Sources used

| Source | Used for | Required? | Failure behaviour |
|---|---|---|---|
| Yahoo Finance (`yfinance`) | OHLCV price history, some fundamentals | Yes | Run aborts for the affected symbols; cache reused where possible |
| NSE India public endpoints / archives | FII–DII activity, bulk deals, bhavcopy delivery %, option-chain IV, announcements | No | Feed marked degraded in `data/data_health.json`; pipeline continues |
| Google News RSS | Context-only headlines | No | Cached digest reused; refresh status `cached` or `failed` |
| AMFI / fund-house pages (optional extras) | ETF AUM, TER, tracking error | No | Falls back to manual override CSVs |

## Known limitations

- **NSE endpoints block aggressively.** They require warm-up cookies, rate
  limiting and a browser-like User-Agent, and still return 503 under load. The
  engine cascades across live API → archive → cache, and records the outcome
  rather than pretending success.
- **Yahoo adjustment drift.** Adjusted series can change retroactively, so raw
  prices are cached incrementally and adjustments applied locally.
- **Delivery % and IV rank** are appended to daily CSV caches with backoff.
  Gaps are expected and are treated as missing, never as zero.
- **Fundamentals are sparse** for small caps and absent for ETFs. The
  fundamental factor carries a low default weight and is neutral when missing.
- **Missing is not zero.** Every optional feed is neutral when unavailable —
  a missing tracking error is not a quality demerit.

## Data health

Each run writes `data/data_health.json` with per-feed status, last success
timestamp and row counts. The desktop Data Health panel and the HTML dashboard
render it, so silent feed failures are visible instead of invisible.

## Terms of use and redistribution

You are responsible for complying with each provider's terms of service. Do not
commit or redistribute fetched market data. Fetch politely: the defaults are
conservative, and you should not raise request rates.

## Local data preservation

Runtime folders are git-ignored, not disposable. Back them up before upgrading:

```bash
cd desktop/nse_quant_engine
tar -czf ../nse_engine_backup_$(date +%Y%m%d).tgz data output
```

Windows PowerShell:

```powershell
cd desktop\nse_quant_engine
Compress-Archive -Path data,output -DestinationPath ..\nse_engine_backup.zip
```

Validation history is the only asset that cannot be re-created quickly — it
accumulates one date at a time. Losing it resets your evidence to zero.
