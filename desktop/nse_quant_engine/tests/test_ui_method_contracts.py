"""Static guard: no class may call a private method it never defines.

Why this exists
---------------
`CandidatesWorkbench.refresh()` called `self._reload_combo(...)` at four sites,
but the method was never defined. Every pipeline step reported `ok` and then the
sidebar printed:

    Candidates tab refresh failed: 'CandidatesWorkbench' object has no
    attribute '_reload_combo'

Nothing caught it because the failure lives in a Qt widget method: importing the
module succeeds, `compileall` succeeds, and the UI tests that do run either skip
without PySide6 or never reach that branch. An AttributeError on a missing method
is only raised at call time.

This test needs no Qt and no running application — it parses the AST, so it
covers every class in the engine on every platform, including headless CI.

Scope: only `self._name(...)` calls (single leading underscore) are checked.
Public and dunder calls are skipped because they may legitimately be inherited
from a Qt base class this test cannot introspect. Private members are by
convention defined on the class itself, so their absence is a real defect.
"""
from __future__ import annotations

import ast
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {"__pycache__", "vendor", "node_modules", ".venv"}


def _python_files() -> list[Path]:
    return [p for p in sorted(ENGINE_ROOT.rglob("*.py"))
            if not SKIP_PARTS & set(p.parts)]


def _self_attr_names(node: ast.AST) -> set[str]:
    """Names bound onto `self` by assignment (callable attributes count)."""
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            for target in n.targets:
                if isinstance(target, ast.Attribute) \
                        and isinstance(target.value, ast.Name) \
                        and target.value.id == "self":
                    names.add(target.attr)
        elif isinstance(n, ast.AnnAssign) \
                and isinstance(n.target, ast.Attribute) \
                and isinstance(n.target.value, ast.Name) \
                and n.target.value.id == "self":
            names.add(n.target.attr)
    return names


def _undefined_private_calls(cls: ast.ClassDef) -> list[str]:
    defined = {n.name for n in ast.walk(cls)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    defined |= _self_attr_names(cls)

    called: dict[str, int] = {}
    for n in ast.walk(cls):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and isinstance(n.func.value, ast.Name) \
                and n.func.value.id == "self":
            name = n.func.attr
            if name.startswith("_") and not name.startswith("__"):
                called.setdefault(name, n.lineno)

    return [f"self.{name}() called at line {lineno} but never defined"
            for name, lineno in sorted(called.items()) if name not in defined]


def test_no_class_calls_an_undefined_private_method():
    problems: list[str] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover — compileall covers this
            problems.append(f"{path.name}: unparseable ({exc})")
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for msg in _undefined_private_calls(cls):
                problems.append(
                    f"{path.relative_to(ENGINE_ROOT)} :: {cls.name} :: {msg}")

    assert not problems, (
        "Class(es) call private methods that do not exist — these raise "
        "AttributeError at runtime, not import time:\n  "
        + "\n  ".join(problems)
    )


def test_candidates_workbench_defines_reload_combo():
    """Pin the specific regression, so the cause stays legible in the suite."""
    src = (ENGINE_ROOT / "ui" / "candidates_workbench.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "CandidatesWorkbench")
    methods = {n.name for n in ast.walk(cls)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_reload_combo" in methods
    assert "_reload_combo" in src  # still called; the fix is the definition


def test_reload_combo_keeps_placeholder_first_and_preserves_choice():
    """Behavioural check with a stand-in widget — no Qt required.

    `_apply_filters` treats `currentIndex() > 0` as "a real filter is selected",
    so index 0 must always be the placeholder. Signals must be blocked during the
    rebuild because `clear()` emits `currentIndexChanged`, which is wired to
    `_apply_filters`.
    """
    # The module imports PySide6 at top level; parse out just the function
    # instead of importing it, so this runs headless and without Qt installed.
    src = (ENGINE_ROOT / "ui" / "candidates_workbench.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "CandidatesWorkbench")
    fn = next(n for n in ast.walk(cls)
              if isinstance(n, (ast.FunctionDef,)) and n.name == "_reload_combo")
    fn.decorator_list = []  # drop @staticmethod so we can exec it standalone
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    ns: dict = {"QComboBox": object}
    exec(compile(module, "<reload_combo>", "exec"), ns)
    reload_combo = ns["_reload_combo"]

    class FakeCombo:
        def __init__(self, items, current=0):
            self.items = list(items)
            self.index = current
            self.blocked = False
            self.emissions = 0

        def currentText(self):
            return self.items[self.index] if 0 <= self.index < len(self.items) else ""

        def blockSignals(self, flag):
            was, self.blocked = self.blocked, bool(flag)
            return was

        def clear(self):
            self.items = []
            self.index = -1
            if not self.blocked:
                self.emissions += 1

        def addItems(self, items):
            self.items.extend(items)
            if not self.blocked:
                self.emissions += 1

        def setCurrentIndex(self, i):
            self.index = i
            if not self.blocked:
                self.emissions += 1

    # 1. Placeholder is always index 0.
    combo = FakeCombo(["All universes"])
    reload_combo(combo, "All universes", ["ETF", "STOCK"])
    assert combo.items == ["All universes", "ETF", "STOCK"]
    assert combo.index == 0
    assert combo.currentText() == "All universes"

    # 2. A surviving selection is preserved across a refresh.
    combo.setCurrentIndex(2)          # user picks "STOCK"
    combo.emissions = 0
    reload_combo(combo, "All universes", ["ETF", "STOCK"])
    assert combo.currentText() == "STOCK"

    # 3. No signal is emitted during the rebuild (would re-enter _apply_filters).
    assert combo.emissions == 0

    # 4. A selection that no longer exists falls back to the placeholder rather
    #    than filtering on a stale value and emptying the table.
    reload_combo(combo, "All universes", ["ETF"])
    assert combo.index == 0
    assert combo.currentText() == "All universes"

    # 5. Empty data still leaves a usable placeholder.
    reload_combo(combo, "All raw score buckets", [])
    assert combo.items == ["All raw score buckets"]
    assert combo.index == 0

    # 6. Signal blocking is restored, not forced off.
    combo.blockSignals(True)
    reload_combo(combo, "All universes", ["ETF"])
    assert combo.blocked is True
