"""Suite-wide guard: no test may modify the live dataset.

The engine resolves data/ and output/ relative to its own files, so a test that
calls a writer without redirecting the module's path constant writes into the
REAL run folder. Measured on the 2026-11-05 run folder: every full test run
rewrote data/etf_mapping_suggestions.csv (360 lines -> 3), because
make_mapping_suggestions() writes MAPPING_SUGGESTIONS_OUT and four tests called
it unredirected. Nothing failed; the damage was silent until the next pipeline
run regenerated the file.

This autouse fixture records (size, mtime_ns) of every file under data/ and
output/ before each test and fails that test if any file was created, changed or
deleted. It compares what is actually on disk, so a writer added later is covered
without maintaining a list of file names.

In a git clone data/ and output/ are absent or empty; a test that writes there
then shows up as "created", so the guard still bites in CI.

Second guard: no test may use the real network. Four AMFI "network is down"
tests monkeypatched a URL constant nothing read any more, so they fetched the
real AMFI: they passed offline (and while AMFI's 8-column format broke every
parse) and failed online. Same suite, different verdict depending on the
network. Every hostname lookup now gets a simulated DNS failure (what
fail-soft code is built to survive) and the attempt fails the test.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

ENGINE_ROOT = Path(__file__).resolve().parents[1]
LIVE_DIRS = ("data", "output")
# Written by the OS or an editor, not by the engine: Excel lock files from a CSV
# open while the suite runs, Explorer/OneDrive folder metadata.
_NOT_ENGINE_FILES = ("~$", ".~lock.", "desktop.ini", "Thumbs.db", ".DS_Store")


def _live_snapshot() -> dict[str, tuple[int, int]]:
    snap: dict[str, tuple[int, int]] = {}
    stack = [str(ENGINE_ROOT / d) for d in LIVE_DIRS]
    while stack:
        top = stack.pop()
        try:
            entries = list(os.scandir(top))
        except OSError:          # vanished / not a dir / no access
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name != "__pycache__":
                        stack.append(e.path)
                    continue
                if e.name.startswith(_NOT_ENGINE_FILES):
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            snap[os.path.relpath(e.path, ENGINE_ROOT)] = (st.st_size, st.st_mtime_ns)
    return snap


@pytest.fixture(autouse=True)
def _live_dataset_untouched():
    before = _live_snapshot()
    yield
    after = _live_snapshot()
    changed = sorted(p for p in before.keys() & after.keys() if before[p] != after[p])
    created = sorted(after.keys() - before.keys())
    deleted = sorted(before.keys() - after.keys())
    if changed or created or deleted:
        lines = ([f"changed: {p}" for p in changed] + [f"created: {p}" for p in created]
                 + [f"deleted: {p}" for p in deleted])
        pytest.fail(
            "this test modified the live dataset under " + str(ENGINE_ROOT) + ":\n  "
            + "\n  ".join(lines[:20])
            + "\nRedirect the writer's module-level path to tmp_path with "
              "monkeypatch.setattr(module, '<NAME>_OUT', tmp_path / ...). "
              "(If the pipeline or the app was writing at the same time, re-run.)")


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    attempts: list[str] = []
    real_getaddrinfo = socket.getaddrinfo

    def _offline(host, *args, **kwargs):
        h = host.decode() if isinstance(host, bytes) else str(host)
        if h in _LOCAL_HOSTS:
            return real_getaddrinfo(host, *args, **kwargs)
        attempts.append(h)
        raise socket.gaierror(getattr(socket, "EAI_NONAME", -2),
                             f"real network disabled in tests ({h})")

    monkeypatch.setattr(socket, "getaddrinfo", _offline)
    yield
    if attempts:
        pytest.fail("this test tried to reach the real network: "
                    + ", ".join(sorted(set(attempts)))
                    + ". Its result then depends on the machine it runs on. "
                      "Patch the network seam (e.g. requests.Session.get) instead.")
