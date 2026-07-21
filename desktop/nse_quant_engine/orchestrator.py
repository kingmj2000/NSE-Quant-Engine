"""Single-button orchestrator: runs full normal pipeline + shadow + comparison.

Usable headlessly:
    python orchestrator.py --all
    python orchestrator.py --all --skip-fetch        # use cached AMFI/yfinance data
    python orchestrator.py --steps engine,shadow,compare

Each step is a Python function so the PySide6 GUI can call it directly and
stream logs without spawning subprocesses where avoidable.

v4.3 fixes:
- Catches SystemExit raised by sub-scripts (was killing the worker thread
  silently after etf_aum_auto_fetcher and leaving the UI on "Running...").
- Gates the shadow engine on a non-empty `output/latest_scores.csv` so a
  half-complete normal run can never feed the shadow.
- Emits structured per-step events for the GUI dashboard.
"""
from __future__ import annotations
import argparse
import json
import runpy
import sys
import time
import traceback

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

BASE = Path(__file__).resolve().parent
OUT = BASE / "output"


@dataclass
class Step:
    name: str
    runner: Callable[[], None]
    skippable: bool = False
    network: bool = False
    gate: Optional[Callable[[], tuple[bool, str]]] = None  # (ok, reason)
    status: str = "pending"
    duration_s: float = 0.0
    error: str = ""


def _runpy(script: str):
    def _run():
        path = BASE / script
        if not path.exists():
            raise FileNotFoundError(script)
        try:
            runpy.run_path(str(path), run_name="__main__")
        except SystemExit as se:
            code = se.code
            if code in (None, 0, "0"):
                return  # normal exit
            raise RuntimeError(f"script exited with code {code}")
    return _run


def _module(modname: str, func: str = "build"):
    def _run():
        if str(BASE) not in sys.path:
            sys.path.insert(0, str(BASE))
        mod = __import__(modname)
        getattr(mod, func)()
    return _run


def _gate_normal_scores_ready() -> tuple[bool, str]:
    p = OUT / "latest_scores.csv"
    if not p.exists():
        return False, "output/latest_scores.csv missing — normal run did not finish"
    try:
        if p.stat().st_size < 200:
            return False, "latest_scores.csv looks empty/truncated"
    except Exception as e:
        return False, f"stat failed: {e}"
    return True, ""


def _run_optional_feeds():
    """Step 0.5 — refresh the 4 optional overlay CSVs from free public sources.
    Always non-fatal: refresh_all() catches every exception internally."""
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))
    from core.optional_data_fetchers import refresh_all
    refresh_all(BASE)


def build_steps(include_shadow: bool = True, include_fetch: bool = True) -> list[Step]:
    steps: list[Step] = []
    if include_fetch:
        steps += [
            Step("optional_data_fetchers",         _run_optional_feeds,                         network=True, skippable=True),
            Step("universe_builder",              _runpy("universe_builder.py"),              network=True),
            Step("etf_quality_builder (pass 1)",  _runpy("etf_quality_builder.py")),
            Step("etf_metadata_enricher (pass 1)", _runpy("etf_metadata_enricher.py"),         network=True),
            Step("etf_aum_auto_fetcher",          _runpy("etf_aum_auto_fetcher.py"),           network=True, skippable=True),
            Step("etf_ter_tracking_auto_fetcher", _runpy("etf_ter_tracking_auto_fetcher.py"),  network=True, skippable=True),
            Step("etf_metadata_enricher (pass 2)", _runpy("etf_metadata_enricher.py"),         network=True),
            Step("etf_quality_builder (pass 2)",  _runpy("etf_quality_builder.py")),
        ]
    steps.append(Step("dq_report_builder",  _module("dq_report_builder")))
    steps.append(Step("nse_quant_engine",  _runpy("nse_quant_engine.py"), network=True))
    def _run_daily_changes():
        if str(BASE) not in sys.path:
            sys.path.insert(0, str(BASE))
        from core.daily_changes import build_daily_changes
        build_daily_changes(BASE)

    steps += [
        Step("validation_builder",          _runpy("validation_builder.py")),
        Step("cross_sectional_validation",  _runpy("cross_sectional_validation.py")),
        Step("trade_plan_builder",          _runpy("trade_plan_builder.py")),
        # Post-ranking structured daily diff for the UI — read-only over
        # latest_scores.csv + score_history.csv + macro_context.json.
        Step("daily_changes_builder",       _run_daily_changes, skippable=True),
        Step("news_market_builder",         _runpy("news_market_builder.py"), network=True, skippable=True),
    ]
    if include_shadow:
        # Shadow runs only AFTER normal scoring + validation succeeded — never in parallel.
        steps.append(Step(
            "nse_quant_engine_v4_shadow",
            _runpy("nse_quant_engine_v4_shadow.py"),
            skippable=True,
            gate=_gate_normal_scores_ready,
        ))
        steps.append(Step("shadow_vs_official_report", _module("shadow_vs_official_report"),
                          gate=_gate_normal_scores_ready))
    # Always last — consumes every artifact produced above.
    steps.append(Step("dashboard_html_builder", _module("dashboard_html_builder")))

    # Output retention: prune dated artifacts to config.RETENTION_KEEP_N.
    # skippable=True so a cleanup failure can never fail a pipeline run.
    def _run_cleanup():
        if str(BASE) not in sys.path:
            sys.path.insert(0, str(BASE))
        from core.cleanup_outputs import run_cleanup
        run_cleanup(BASE)
    steps.append(Step("cleanup_outputs", _run_cleanup, skippable=True))
    return steps


def run_all(steps: list[Step], on_log=print, on_step=None) -> dict:
    summary = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": []}
    overall_t0 = time.time()
    for s in steps:
        on_log(f"→ {s.name}")
        # Pre-flight gate
        if s.gate is not None:
            ok, reason = s.gate()
            if not ok:
                s.status = "skipped"
                s.error = f"gate failed: {reason}"
                on_log(f"  SKIPPED — {reason}")
                summary["steps"].append({"name": s.name, "status": s.status,
                                         "duration_s": 0.0, "error": s.error})
                if on_step:
                    on_step(s)
                continue
        t0 = time.time()
        try:
            s.runner()
            s.status = "ok"
        except SystemExit as se:
            if se.code in (None, 0, "0"):
                s.status = "ok"
            else:
                s.status = "error" if not s.skippable else "skipped"
                s.error = f"SystemExit: {se.code}"
                on_log(f"  {s.status.upper()} — {s.error}")
        except BaseException as e:
            s.status = "error" if not s.skippable else "skipped"
            s.error = f"{type(e).__name__}: {e}"
            on_log(f"  {s.status.upper()} — {s.error}")
            if not s.skippable:
                traceback.print_exc()
        finally:
            s.duration_s = round(time.time() - t0, 2)
            summary["steps"].append({"name": s.name, "status": s.status,
                                     "duration_s": s.duration_s, "error": s.error})
            on_log(f"  done in {s.duration_s}s [{s.status}]")
            if on_step:
                on_step(s)
            if s.status == "error" and not s.skippable:
                on_log("  PIPELINE HALTED — required step failed.")
                break
    summary["duration_s"] = round(time.time() - overall_t0, 2)
    summary["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_manifest(summary)
    return summary


def write_manifest(summary: dict) -> Path:
    """Persist a small manifest the GUI reads at launch to show the last run."""
    OUT.mkdir(parents=True, exist_ok=True)

    def _status_of(name_contains: str) -> str:
        hits = [s for s in summary["steps"] if name_contains in s["name"]]
        if not hits:
            return "missing"
        if any(s["status"] == "error" for s in hits):
            return "failed"
        if all(s["status"] == "skipped" for s in hits):
            return "skipped"
        if any(s["status"] == "skipped" for s in hits):
            return "partial"
        return "ok"

    champion = "official"
    cmp_path = OUT / "shadow_vs_official.json"
    if cmp_path.exists():
        try:
            rec = json.loads(cmp_path.read_text()).get("recommendation", "").lower()
            if "shadow leads" in rec or "switch" in rec:
                champion = "shadow"
        except Exception:
            pass

    def _exists(p: str) -> str:
        fp = OUT / p
        return str(fp) if fp.exists() else ""

    manifest = {
        "completed_at": summary.get("finished"),
        "started_at": summary.get("started"),
        "duration_s": summary.get("duration_s"),
        "official_status": _status_of("nse_quant_engine"),
        "shadow_status": _status_of("shadow"),
        "champion": champion,
        "steps": summary["steps"],
        "artifacts": {
            "dashboard_html": _exists("dashboard_latest.html"),
            "scores_csv":     _exists("latest_scores.csv"),
            "shadow_csv":     _exists("latest_scores_v4_shadow.csv"),
            "compare_csv":    _exists("shadow_vs_official.csv"),
            "validation":     _exists("validation_status.json"),
            "trade_plan":     _exists("trade_plan_report.md"),
            "dq_report":      _exists("dq_report.md"),
        },
    }
    (OUT / "run_manifest.json").write_text(json.dumps(manifest, indent=2))
    return OUT / "run_manifest.json"



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-shadow", action="store_true")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="Skip network steps (use cached data on disk).")
    ap.add_argument("--steps", default="",
                    help="Comma-separated subset of step names to run.")
    args = ap.parse_args()

    all_steps = build_steps(include_shadow=not args.no_shadow,
                            include_fetch=not args.skip_fetch)
    if args.steps:
        wanted = {n.strip() for n in args.steps.split(",")}
        all_steps = [s for s in all_steps if s.name in wanted]

    if not (args.all or args.steps):
        ap.print_help()
        return

    summary = run_all(all_steps)
    print("\n=== SUMMARY ===")
    for s in summary["steps"]:
        print(f"  {s['status']:8s} {s['duration_s']:>6.2f}s  {s['name']}  {s['error']}")
    print(f"  total: {summary['duration_s']}s")


if __name__ == "__main__":
    main()
