# Security Policy

## Supported versions

This is a research project without formal release branches. Security fixes are
applied to the default branch only.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's private reporting route:

1. Go to the repository's **Security** tab.
2. Choose **Report a vulnerability** (GitHub Security Advisories).
3. Include reproduction steps, affected files and impact.

We aim to acknowledge reports within 7 days. No personal email address is
published for security contact.

## Scope

In scope:

- Code execution or injection through parsed data files, HTML dashboards or
  downloaded market data.
- Unsafe deserialization, path traversal in cache/output handling.
- Accidental disclosure of local credentials or runtime data by the tooling.

Out of scope:

- Financial accuracy, model quality, or profitability of any signal. This
  software is research tooling and provides **no investment advice**.
- Upstream outages, blocking or rate limits from public market-data sources.

## Handling of local data

The engine writes market caches, validation history, portfolio files and
evidence bundles to `desktop/nse_quant_engine/data/` and
`desktop/nse_quant_engine/output/`. These paths are git-ignored and must never
be attached to an issue, pull request or advisory. Redact symbols and holdings
before sharing any log.

Never commit `.env`, API tokens, brokerage credentials or private keys. If a
credential is committed by accident, rotate it immediately and report it
privately — do not attempt to hide it with a force-push.
