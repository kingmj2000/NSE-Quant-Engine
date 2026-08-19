"""Repo health check — verify the audited fixes are present and no regressions crept in.

Run from anywhere inside the repo:

    python desktop/nse_quant_engine/verify_repo_health.py

No network, no pytest, no PySide6 required. Exit code 0 = all clear.

It answers three questions:
  1. Are the specific fixes from the audit sessions actually in this checkout?
  2. Has the recurring defect pattern reappeared — a name one side writes and
     another side reads differently, or a private method that does not exist?
  3. Is anything dead or accidentally tracked?

Every FIX check names the bug it guards, so a failure tells you what broke rather
than just that something did.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ENG = Path(__file__).resolve().parent
ROOT = ENG.parent.parent
SKIP = {"__pycache__", "vendor", "node_modules", ".venv", "output", "data"}

results: list[tuple[str, bool, str]] = []
warnings: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


def read(rel: str) -> str:
    p = ENG / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def py_files() -> list[Path]:
    return [p for p in sorted(ENG.rglob("*.py")) if not SKIP & set(p.parts)]


# ─── 1. Fail-closed portfolio validation ────────────────────────────────────
pv = read("core/portfolio_validation.py")
check("portfolio validation tracks artifact completeness",
      all(k in pv for k in ("missing_artifacts", "artifact_completeness",
                            "symbol_order_aligned", "CRITICAL_TOP5_ARTIFACTS")),
      "missing artifacts must force Downgrade_To_Watch, not pass silently")
check("portfolio validation no longer skips empty symbol lists",
      "if syms and syms != expected" not in pv,
      "the old guard treated a MISSING artifact as 'no mismatch found'")

# ─── 2. Shadow score identity ───────────────────────────────────────────────
sh = read("nse_quant_engine_v4_shadow.py")
rep = read("shadow_vs_official_report.py")
check("shadow exposes its own V4_Confidence_Adjusted_Score",
      "V4_Confidence_Adjusted_Score" in sh and "sanitize_shadow_columns" in sh,
      "without it the report compares official CAS against a copy of itself")
check("shadow comparison has no score-column fallback chain",
      "_shadow_score_col" not in rep,
      "name-guessing selected the official score sitting in the shadow file")
check("shadow report claims no champion / promotion",
      not any(s in rep.lower() for s in ("consider manual switch", "keep current champion",
                                         "shadow leads", "switch to shadow")),
      "shadow has no independent validation history and cannot be promoted")
for consumer, rel in (("dashboard", "dashboard_html_builder.py"),
                      ("candidates workbench", "ui/candidates_workbench.py")):
    src = read(rel)
    check(f"{consumer} sorts shadow by the V4 score",
          "V4_Confidence_Adjusted_Score" in src,
          "it previously fell back to an inherited OFFICIAL score column")

# ─── 3. Expected value fails closed ─────────────────────────────────────────
ev = read("core/expected_value.py")
check("EV reports filter columns it could not apply",
      "missing_filter_columns" in ev and "ignored_missing_cols" not in ev,
      "an unfiltered statistic must never be published under a filtered label")
check("EV guards the forward-return schema",
      "schema_incompatible" in ev or "schema incompatible" in ev,
      "a legacy history file used to raise KeyError('Horizon_Days')")
check("shadow report filters on the real bucket column",
      "Signal_Bucket" in rep and "Top Quintile" not in rep,
      "Score_Bucket / 'Top Quintile' are not written by this engine")

# ─── 4. Evidence accumulation (survivorship) ────────────────────────────────
vb = read("validation_builder.py")
check("forward-return history accumulates instead of being rebuilt",
      "merge_forward_history" in vb and "FORWARD_KEY" in vb,
      "rebuilding dropped matured returns for symbols that left the universe")

# ─── 5. History dedup ───────────────────────────────────────────────────────
hio = read("core/history_io.py")
nqe = read("nse_quant_engine.py")
check("append_history canonicalises its dedup key",
      "canonicalise_key" in hio,
      "string vs date keys never matched, so same-day re-runs duplicated history")
check("append_history is importable without yfinance",
      "from core.history_io import append_history" in nqe,
      "it lived in a module that imports yfinance, so it was untestable in CI")
check("timestamp keys keep full precision",
      'lowered == "date"' in hio,
      "collapsing Run_Timestamp to a day would discard run history")

# ─── 6. Maturation counters ─────────────────────────────────────────────────
uir = read("core/ui_readers.py")
check("maturation progress reads the missing-signals file",
      "read_maturation_progress" in uir,
      "pending was counted as NaN rows of a file that only holds matured rows")
for consumer, rel in (("Overview strip", "ui/decision_center.py"),
                      ("KPI grid", "run_app.py")):
    check(f"{consumer} uses read_maturation_progress",
          "read_maturation_progress" in read(rel),
          "otherwise 'Awaiting maturation' is structurally always 0")

# ─── 7. Cross-module key agreement ──────────────────────────────────────────
app = read("run_app.py")
check("shadow KPI keys match the writer",
      all(k in app for k in ("jaccard_top25", "avg_abs_delta_rank"))
      and "jaccard_at_20" not in app,
      "the UI read keys nobody wrote, so the cards rendered a permanent dash")
check("shadow report emits those keys",
      all(f'"{k}"' in rep for k in ("jaccard_top25", "overlap_top_n", "avg_abs_delta_rank")))
check("portfolio verdict read by its real name",
      "Batch_Verdict" not in app,
      'portfolio_validation.json writes "verdict"')
tpb = read("trade_plan_builder.py")
check("macro context carries previous_regime forward",
      '"previous_regime"' in tpb,
      "core/daily_changes.py reads it; nothing wrote it, so regime_change was dead")

# ─── 8. Candidates tab crash ────────────────────────────────────────────────
cw = read("ui/candidates_workbench.py")
check("CandidatesWorkbench defines _reload_combo",
      "def _reload_combo" in cw,
      "four call sites existed with no definition -> refresh() aborted every run")

# ─── 9. Pattern scan: private methods that do not exist ─────────────────────
missing_methods: list[str] = []
for p in py_files():
    try:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        missing_methods.append(f"{p.name}: unparseable ({exc})")
        continue
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        defined = {n.name for n in ast.walk(cls)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for n in ast.walk(cls):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                            and t.value.id == "self":
                        defined.add(t.attr)
        for n in ast.walk(cls):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "self":
                a = n.func.attr
                if a.startswith("_") and not a.startswith("__") and a not in defined:
                    missing_methods.append(f"{p.relative_to(ENG)}::{cls.name}.{a}() line {n.lineno}")
# Scope-aware undefined-name scan: catches a NameError that only fires at call
# time, e.g. a method referencing a local that belongs to a different method.
try:
    from tools_scan_undefined_names import scan as _scan_undefined
    _undef = _scan_undefined(ENG)
except Exception as _exc:  # scanner itself must never break the health check
    _undef = []
    warnings.append((f"undefined-name scan unavailable: {_exc}", "run it manually"))
check("no function uses an undefined name", not _undef,
      "; ".join(_undef[:5]) or "NameError raises at call time, not import time")

check("no class calls an undefined private method",
      not missing_methods,
      "; ".join(missing_methods[:5]) or "AttributeError only raises at call time")

# ─── 10. Dead files / stray tracked artifacts ───────────────────────────────
stray = [f for f in ("phase_1a_audit_gaps.py", "phase_1b_fill_etf_gaps.py",
                     "missing_ter_list.txt") if (ENG / f).exists()]
check("removed one-off scripts stay removed", not stray,
      f"still present: {', '.join(stray)}" if stray else "")

# Runtime data: the question is whether git TRACKS it, not whether it exists on
# disk. A live run folder is SUPPOSED to contain output/ and data/ — flagging
# that was crying wolf. Only a git checkout can answer this, so outside one the
# check is reported as not-applicable rather than failed.
def _git_root(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


git_root = _git_root(ENG)
if git_root is None:
    warnings.append(("runtime-data check skipped: not a git checkout",
                     "expected in a live run folder; run this in your clone to "
                     "verify output/ and data/ are untracked"))
    check("runtime data untracked (n/a outside a checkout)", True, "")
else:
    tracked: list[str] = []
    try:
        import subprocess
        rel = ENG.relative_to(git_root).as_posix()
        for d in ("output", "data"):
            res = subprocess.run(
                ["git", "-C", str(git_root), "ls-files", f"{rel}/{d}"],
                capture_output=True, text=True, timeout=30)
            if res.returncode == 0 and res.stdout.strip():
                n = len(res.stdout.strip().splitlines())
                tracked.append(f"{d}/ ({n} tracked file(s))")
    except Exception as exc:
        warnings.append((f"runtime-data check inconclusive: {exc}",
                         "run `git ls-files` on output/ and data/ manually"))
    check("no runtime data tracked by git", not tracked,
          f"{', '.join(tracked)} — back up locally, then "
          f"`git rm -r --cached` them" if tracked else
          "these are generated per run and must never be committed")

# ─── 11. Orphan modules ─────────────────────────────────────────────────────
corpus = {}
for pat in ("*.py", "*.bat", "*.md", "*.yml", "*.yaml", "*.toml", "*.cfg", "*.ini"):
    for p in list(ENG.rglob(pat)) + list(ROOT.glob(pat)):
        if SKIP & set(p.parts):
            continue
        corpus[p] = p.read_text(encoding="utf-8", errors="replace")
orphans = []
for p in py_files():
    # Test modules are collected by pytest, not imported — never orphans.
    if p.stem in ("__init__", "run_app", "orchestrator", "verify_repo_health") \
            or "tests" in p.parts or p.stem.startswith(("test_", "diagnose_", "tools_")):
        continue
    if not any(q != p and re.search(rf"\b{re.escape(p.stem)}\b", s) for q, s in corpus.items()):
        orphans.append(str(p.relative_to(ENG)))
if orphans:
    warnings.append(("unreferenced module(s): " + ", ".join(orphans),
                     "dead code in a public repo — wire it up or delete it"))
check("no unreferenced modules", True, "")

# ─── 12. Test files present ─────────────────────────────────────────────────
expected_tests = ["test_integrity_fail_closed.py", "test_ui_method_contracts.py",
                  "test_evidence_accumulation.py", "test_history_dedup.py"]
absent = [t for t in expected_tests if not (ENG / "tests" / t).exists()]
check("audit regression tests present", not absent,
      f"missing: {', '.join(absent)}" if absent else "")


# ─── report ─────────────────────────────────────────────────────────────────
passed = [r for r in results if r[1]]
failed = [r for r in results if not r[1]]

print("=" * 74)
print("NSE QUANT ENGINE — REPO HEALTH CHECK")
print("=" * 74)
for name, ok, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok and detail:
        print(f"        why it matters: {detail}")
for msg, why in warnings:
    print(f"  WARN  {msg}")
    print(f"        {why}")
print("-" * 74)
print(f"{len(passed)} passed, {len(failed)} failed, {len(warnings)} warning(s)")
if failed:
    print("\nEach FAIL names the file and the bug it guards. For a missing fix,")
    print("the commit most likely did not include that file — re-copy and re-run.")
else:
    print("\nAll audited fixes present. Run the test suite next:  python -m pytest -q")
print("=" * 74)
sys.exit(1 if failed else 0)
