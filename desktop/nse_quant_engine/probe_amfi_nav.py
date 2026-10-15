"""Probe: what does AMFI actually send for NAVAll.txt from THIS machine?

Read-only. Writes nothing except, on failure, the raw body to
data/amfi_navall_last_failed_response.txt. Run it, paste the output back.

    python probe_amfi_nav.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import amfi_nav  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"

df, diags = amfi_nav.fetch_navall(timeout=45, data_dir=DATA)
for d in diags:
    print(f"\n{d['url']}")
    for k in ("status", "final_url", "content_type", "bytes", "rows", "error"):
        print(f"  {k:<13}{d[k]}")
    print(f"  head         {d['head'][:200]!r}")
print()
if not df.empty:
    print(f"OK: {len(df)} NAV rows parsed. The feed works from here; the pipeline "
          f"will now fill NAV, scheme-code mapping and TER/TE joins.")
    sys.exit(0)
print("FAILED: no usable rows from any URL. Raw body saved to "
      f"data/{amfi_nav.FAILED_BODY_NAME}. A 'head' starting with '<' means AMFI "
      "served an HTML page (block/redirect), not the NAV file.")
sys.exit(1)
