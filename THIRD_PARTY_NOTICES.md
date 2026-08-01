# Third-party notices

This project bundles **no third-party source code**. All dependencies are
installed from their official package registries at install time. Their
licenses apply to those packages, not to this repository's code (MIT — see
[`LICENSE`](LICENSE)).

## Python dependencies (`desktop/nse_quant_engine/requirements.txt`)

| Package | License | Purpose |
|---|---|---|
| pandas | BSD-3-Clause | Data frames, CSV/Excel I/O |
| numpy | BSD-3-Clause | Numerics |
| yfinance | Apache-2.0 | Public price/fundamental data access |
| openpyxl | MIT | Excel output |
| tabulate | MIT | Text tables in reports |
| requests | Apache-2.0 | HTTP client |
| beautifulsoup4 | MIT | HTML parsing |
| lxml | BSD-3-Clause | XML/HTML parser backend |
| html5lib | MIT | HTML parser backend for `pandas.read_html` |
| PySide6 / PySide6-Addons | LGPL-3.0 (with Qt commercial alternative) | Desktop UI, optional QtWebEngine |

**Qt / PySide6 note:** PySide6 is distributed under the LGPLv3. Using it as an
installed dependency of this application, as documented, is compatible with
that license. If you redistribute a bundled binary of this application you must
satisfy LGPL obligations yourself (dynamic linking, relinking ability, license
notices). QtWebEngine additionally embeds Chromium, which carries BSD-style and
other notices from the Chromium project.

## Optional Python extras (`requirements_optional_etf_metadata.txt`)

| Package | License | Purpose |
|---|---|---|
| amfipy | MIT | AMFI mutual-fund/ETF metadata |
| httpx | BSD-3-Clause | Async HTTP for optional fetchers |
| polars | MIT | Fast columnar processing |
| xlrd | BSD-3-Clause | Legacy Excel reading |

## Web shell (`package.json`)

The optional TanStack Start landing page uses React (MIT), TanStack Router /
Start / Query (MIT), Vite (MIT), Tailwind CSS (MIT), Radix UI primitives (MIT),
lucide-react (ISC), and related MIT-licensed tooling. Full details are in
`package.json` and `bun.lock`.

## Conceptual inspiration (no code bundled)

Professional-desk features were inspired by, but contain no code from:

- [Fincept Terminal](https://github.com/Fincept-Corporation/FinceptTerminal) — MIT
- [Vibe Trading](https://github.com/HKUDS/Vibe-Trading) — see upstream repository

Terminal log lines for concept-borrowed steps are prefixed `[fincept]` or
`[vibe]` so provenance is visible at run time.

## Data

Market data fetched at run time belongs to its respective providers (NSE India,
Yahoo Finance, Google News, AMFI) and is **not** redistributed here. See
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).
