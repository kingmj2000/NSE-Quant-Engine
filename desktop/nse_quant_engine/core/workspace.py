"""Which folder is this engine actually running in — and does it have history?

WHY THIS EXISTS
---------------
The engine keeps everything relative to its own file location (BASE), so the
folder you launch from IS the dataset. There are two copies on MJ's machine:

    run folder : ...\\OneDrive - DESRI Project Management\\Projects\\nse_quant_engine_v4_6_patched
                 (all validation history, ETF import files, caches)
    git clone  : C:\\Personal\\Github NSE\\NSE-Quant-Engine\\desktop\\nse_quant_engine
                 (code only; data/ and output/ are gitignored)

On 2026-10-15 17:34 the app was launched from the clone. Every step reported
"ok", and the result looked like a catastrophic regression: Data Health 0/100,
AUM/TER/benchmark 0%, validation dates gone, 1,626 signals "not matured".
Nothing was broken — the clone simply has no score history, no forward-return
history and no data/etf_metadata_imports/. The run folder was untouched.

All-green steps on an empty workspace is the same failure shape as every other
defect in this repo: a check that could not be performed (no history) reported
as a result. This module makes it loud instead.
"""
from __future__ import annotations

from pathlib import Path

MIN_HISTORY_ROWS = 1000     # a single run writes ~550; real history is tens of thousands


def _row_count(p: Path, cap: int = MIN_HISTORY_ROWS + 1) -> int:
    try:
        with p.open("rb") as fh:
            n = 0
            for n, _ in enumerate(fh, start=0):
                if n >= cap:
                    break
            return n             # header excluded by starting at 0
    except Exception:
        return 0


def is_git_checkout(base: Path) -> bool:
    base = Path(base).resolve()
    return any((p / ".git").exists() for p in (base, *base.parents))


def inspect(base: str | Path) -> dict:
    base = Path(base).resolve()
    out = base / "output"
    data = base / "data"
    imports = data / "etf_metadata_imports"
    score_rows = _row_count(out / "score_history.csv")
    fwd_rows = _row_count(out / "forward_return_history.csv")
    has_imports = imports.is_dir() and any(imports.glob("*.csv"))
    return {
        "base": str(base),
        "git_checkout": is_git_checkout(base),
        "score_history_rows": score_rows,
        "forward_history_rows": fwd_rows,
        "has_history": score_rows >= MIN_HISTORY_ROWS and fwd_rows >= MIN_HISTORY_ROWS,
        "has_etf_imports": has_imports,
    }


def warning(base: str | Path) -> str | None:
    """A human-readable warning, or None when the workspace looks real."""
    info = inspect(base)
    problems = []
    if not info["has_history"]:
        problems.append(f"no validation history here (score_history: "
                        f"{info['score_history_rows']} rows, forward returns: "
                        f"{info['forward_history_rows']} rows)")
    if not info["has_etf_imports"]:
        problems.append("no data\\etf_metadata_imports\\ (ETF AUM/TER/benchmark will read 0%)")
    if not problems:
        return None
    where = ("This folder is a GIT CLONE. " if info["git_checkout"] else "")
    return (f"{where}Running from: {info['base']}\n"
            + "".join(f"  • {p}\n" for p in problems)
            + "Validation and Data Health will look empty or broken. If your history "
              "lives in another folder (the run folder), close this and launch "
              "run_app.bat from THAT folder.")
