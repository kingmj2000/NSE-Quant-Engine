# NSE Quant Engine — Clean Core v4

A clean, unit-tested core that fixes the real analytical flaws of the patched
engine and adds two new analysis pieces (fundamental/quality factor + cost-aware
expected value), designed to bolt onto your existing proven data-fetching code.

## What it fixes
- Momentum triple-counting -> momentum is primary; trend & relative strength
  are soft confirmation gates, not additive thirds.
- "Falling knife" promotion -> absolute filters cap negative-momentum names.
- yfinance adjustment drift + slow re-downloads -> incremental raw-price cache.
- False "Validation Positive" from report-scraping -> structured status JSON.
- Tracking-error treated as a quality demerit -> neutral when unavailable.
- Magic numbers scattered everywhere -> one config.py.

## What it adds
- Fundamental/quality factor (ROE, PE, debt, growth, margins), low default weight.
- Cost-aware expected value per holding day — the honest "profit %/day", blank
  until validation is positive.

## Start here
1. `python tests/test_core.py`   (12 tests, should all pass)
2. Read `INTEGRATION_GUIDE.md`   (what to swap, keep, and wire — in order)

## Honest note
This is a screener that, once validated, can tell you whether your ranking beats
a benchmark after costs. It is not — and cannot be — a low-risk/high-profit/
short-hold oracle. The validation layer's most valuable possible answer is
sometimes "No Proven Edge Yet." Believe it when it says so.
