"""Scope-aware undefined-name scan.

Catches the defect that zeroed the Overview counters: `_section_validation_progress`
referenced `OUT`, a local of a *different* method, so it raised NameError at call
time. `compileall` passes, imports pass, and a broad `except Exception` swallowed
it — the counters simply read 0 while the dashboard, reading the same files
directly, showed 11,647.

Only names loaded in a function's OWN scope are checked; nested functions,
lambdas and comprehensions are analysed separately with their own parameters in
scope, which is what makes the signal clean enough to act on.
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "_"}
SKIP = {"__pycache__", "vendor", "node_modules", ".venv", "output", "data"}

Scope = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda


def _arg_names(args: ast.arguments) -> set[str]:
    names = {a.arg for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


def _own_nodes(node: ast.AST):
    """Walk `node`, stopping at nested function/lambda/class scopes."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(n))


def _bound_in_scope(node: ast.AST) -> set[str]:
    """Names bound directly in this scope (excluding nested scopes)."""
    names: set[str] = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        names |= _arg_names(node.args)
    for n in _own_nodes(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            names.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
        elif isinstance(n, ast.comprehension):
            for x in ast.walk(n.target):
                if isinstance(x, ast.Name):
                    names.add(x.id)
    # comprehension targets can also sit in nested generator scopes
    for n in ast.walk(node):
        if isinstance(n, ast.comprehension):
            for x in ast.walk(n.target):
                if isinstance(x, ast.Name):
                    names.add(x.id)
    return names


def scan(root: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if SKIP & set(path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

        module_scope = _bound_in_scope(tree) | BUILTINS
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                module_scope.add(n.name)

        def visit(node: ast.AST, enclosing: set[str]) -> None:
            own = _bound_in_scope(node)
            known = enclosing | own
            for n in _own_nodes(node):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) \
                        and n.id not in known:
                    problems.append(
                        f"{path.relative_to(root)}:{n.lineno} uses undefined name '{n.id}'")
            for child in ast.iter_child_nodes(node):
                _descend(child, known)

        def _descend(node: ast.AST, enclosing: set[str]) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                visit(node, enclosing)
                return
            if isinstance(node, ast.ClassDef):
                for child in ast.iter_child_nodes(node):
                    _descend(child, enclosing | _bound_in_scope(node))
                return
            for child in ast.iter_child_nodes(node):
                _descend(child, enclosing)

        for child in ast.iter_child_nodes(tree):
            _descend(child, module_scope)

    return sorted(set(problems))


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    found = scan(root)
    for f in found:
        print(f)
    print(f"\n{len(found)} undefined name(s)")
    sys.exit(1 if found else 0)
