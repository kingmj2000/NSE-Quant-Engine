# NSE Quant Engine

A desktop screener and analytics engine for NSE (Indian equities & ETFs), plus a
minimal TanStack Start web shell used only so the Python source can live in a
normal Lovable / GitHub repository.

> **Honest note:** this is a screener that, once validated, can tell you whether
> its ranking beats a benchmark after costs. It is **not** a low-risk/
> high-profit/short-hold oracle. The validation layer's most valuable possible
> answer is sometimes _"No Proven Edge Yet."_ Believe it when it says so.

## Repository layout

```
desktop/nse_quant_engine/   # The actual app — Python + PySide6 desktop UI + engine
src/                        # TanStack Start web shell (placeholder preview only)
```

The web app is intentionally minimal. All analytical logic lives under
`desktop/nse_quant_engine/`.

## Quick start — desktop engine

Requires Python 3.11+.

```bash
cd desktop/nse_quant_engine
pip install -r requirements.txt
python run_app.py
```

Windows: double-click `run_app.bat`. macOS: `run_app.command`.
Full pipeline (headless): `run_full_workflow.bat` or `python orchestrator.py`.
Shadow-mode (dormant adaptive layer): `run_shadow_mode.bat`.

Runtime folders `data/` and `output/` are created on first run and are **not**
committed.

## Tests

```bash
cd desktop/nse_quant_engine
python -m pytest tests/ -q
```

## Documentation

Deeper docs live next to the code:

- [`desktop/nse_quant_engine/README.md`](desktop/nse_quant_engine/README.md) — engine overview & design
- [`desktop/nse_quant_engine/QUICKSTART_WINDOWS.md`](desktop/nse_quant_engine/QUICKSTART_WINDOWS.md) — first-run walkthrough
- [`desktop/nse_quant_engine/WORKFLOW.md`](desktop/nse_quant_engine/WORKFLOW.md) — the 16-step pipeline
- [`desktop/nse_quant_engine/INTEGRATION_GUIDE.md`](desktop/nse_quant_engine/INTEGRATION_GUIDE.md) — how modules bolt together
- [`desktop/nse_quant_engine/INSPIRATION_MAP.md`](desktop/nse_quant_engine/INSPIRATION_MAP.md) — concept → module → artifact table

## Web shell (optional)

```bash
bun install
bun run dev        # http://localhost:8080
bun run build
```

## Credits

Professional-desk features (macro regime, sector/peer context, event calendar,
FII/DII + bulk-deals institutional flow, multi-alpha IC survivorship,
walk-forward backtest, EV/Kelly, portfolio ship-gate, regime-conditional alpha
tilt, turnover-vs-cost rebalance diff, portable LLM evidence bundle) were
inspired by:

- [Fincept Terminal](https://github.com/Fincept-Corporation/FinceptTerminal)
- [Vibe Trading](https://github.com/HKUDS/Vibe-Trading)

No code from those projects is bundled — only concepts. Terminal log lines for
borrowed steps are prefixed `[fincept]` or `[vibe]` so each run makes the
provenance visible.

## License

MIT — see [`LICENSE`](LICENSE).

## Disclaimer

Provided for research and educational use. Not investment advice. Markets carry
risk; past performance does not guarantee future results. You are solely
responsible for any decisions made using this software.
