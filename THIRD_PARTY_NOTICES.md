# Third-party notices

This project bundles one third-party file (see **Bundled code** below). All
other dependencies are installed from their official package registries at
install time. Their licenses apply to those packages, not to this repository's
code (MIT — see [`LICENSE`](LICENSE)).

## Bundled code

`desktop/nse_quant_engine/vendor/chart.umd.min.js` is a vendored copy of
**Chart.js** (https://www.chartjs.org/), used so the generated dashboard renders
offline with no CDN call.

Chart.js is distributed under the MIT License and its notice must accompany
redistribution:

```
The MIT License (MIT)

Copyright (c) 2014-2025 Chart.js Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

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
