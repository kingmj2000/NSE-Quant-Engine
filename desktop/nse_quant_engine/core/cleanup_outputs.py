"""Output retention cleanup — additive, fail-soft, never raises.

Prunes accumulating dated artifacts down to the most recent
`config.RETENTION_KEEP_N`. Explicit PROTECTED_FILES set guarantees the
cumulative evidence base (rolling history CSVs, *_latest.* files) is never
touched, even if a future edit widens a glob.

Every run appends one row per pattern to `output/cleanup_log.csv` so the
pruning is auditable and never silent.

NOTE: `output/insight_bundle_*.zip` is NOT pruned here — it is already
owned by `core.evidence_bundle.build_bundle` (keep_last_n=10). Do not
double-implement.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable

# Exact filenames that must NEVER be removed regardless of pattern matches.
PROTECTED_FILES: set[str] = {
    # Rolling history / cumulative evidence base
    "score_history.csv",
    "signal_history.csv",
    "forward_return_history.csv",
    "alpha_score_history.csv",
    "cross_sectional_spread_by_date.csv",
    "shadow_vs_official_history.csv",
    # Self-overwriting "current" files
    "latest_scores.csv",
    "latest_scores_v4_shadow.csv",
    "dashboard_latest.html",
    "trade_plan_latest.csv",
    "trade_plan_latest.xlsx",
    "latest_scores_validated.xlsx",
    "shadow_vs_official.csv",
    "shadow_vs_official.json",
    "run_manifest.json",
    "validation_status.json",
    "cleanup_log.csv",
}


def _prune(root: Path, pattern: str, keep_n: int,
           protected: set[str]) -> tuple[int, list[str]]:
    """Return (removed_count, removed_basenames). Fail-soft per file."""
    if not root.exists():
        return 0, []
    try:
        candidates = [p for p in root.glob(pattern)
                      if p.is_file() and p.name not in protected]
    except Exception:
        return 0, []
    try:
        candidates.sort(key=lambda p: p.stat().st_mtime)
    except Exception:
        return 0, []
    if len(candidates) <= keep_n:
        return 0, []
    to_remove = candidates[:-keep_n] if keep_n > 0 else []
    removed: list[str] = []
    for p in to_remove:
        try:
            p.unlink()
            removed.append(p.name)
        except Exception:
            pass
    return len(removed), removed


def _log(output_dir: Path, rows: Iterable[dict]) -> None:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path = output_dir / "cleanup_log.csv"
        write_header = not log_path.exists()
        with log_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["timestamp", "root", "pattern", "kept",
                            "removed", "removed_files"],
            )
            if write_header:
                w.writeheader()
            for r in rows:
                w.writerow(r)
    except Exception:
        pass


def run_cleanup(base_dir: Path | str | None = None) -> dict:
    """Prune dated artifacts. Never raises. Returns a small summary dict."""
    try:
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent.parent
        base = Path(base_dir)
        output_dir = base / "output"

        try:
            from core import config as C
            keep_n = int(getattr(C, "RETENTION_KEEP_N", 10))
        except Exception:
            keep_n = 10

        ts = datetime.now().isoformat(timespec="seconds")

        if keep_n <= 0:
            _log(output_dir, [{
                "timestamp": ts, "root": "*", "pattern": "*",
                "kept": "all", "removed": 0,
                "removed_files": "disabled (RETENTION_KEEP_N=0)",
            }])
            return {"status": "disabled", "removed_total": 0}

        # (root, pattern) pairs. insight_bundle_*.zip intentionally omitted
        # (owned by core.evidence_bundle.build_bundle keep_last_n prune).
        prune_specs: list[tuple[Path, str]] = [
            (output_dir, "latest_scores_*.xlsx"),
            (output_dir, "latest_scores_v4_shadow_*.xlsx"),
            (output_dir, "dashboard_*.html"),
            (base, "manual_etf_quality_backup_*.csv"),
            (base / "data" / "ter_tracking_debug", "*.xlsx"),
        ]

        rows: list[dict] = []
        removed_total = 0
        for root, pattern in prune_specs:
            n_removed, names = _prune(root, pattern, keep_n, PROTECTED_FILES)
            removed_total += n_removed
            rows.append({
                "timestamp": ts,
                "root": str(root),
                "pattern": pattern,
                "kept": keep_n,
                "removed": n_removed,
                "removed_files": ";".join(names)[:800],
            })
        _log(output_dir, rows)
        return {"status": "ok", "removed_total": removed_total, "keep_n": keep_n}
    except Exception as e:
        try:
            _log(Path(base_dir or ".") / "output", [{
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "root": "*", "pattern": "*", "kept": "?",
                "removed": 0, "removed_files": f"ERROR: {type(e).__name__}: {e}",
            }])
        except Exception:
            pass
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    print(run_cleanup())
