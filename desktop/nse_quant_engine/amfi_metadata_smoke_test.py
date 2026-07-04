"""
AMFI Metadata Smoke Test
========================
Run this after installing optional ETF metadata requirements:
    python amfi_metadata_smoke_test.py
"""
from __future__ import annotations

import sys

print("Python:", sys.version)
try:
    import amfipy
    print("amfipy:", getattr(amfipy, "__version__", "unknown"))
except Exception as exc:
    print("amfipy import failed:", exc)
    raise SystemExit(1)

try:
    import httpx
    print("httpx:", getattr(httpx, "__version__", "unknown"))
except Exception as exc:
    print("httpx import failed:", exc)

from amfipy import AMFIClient
client = AMFIClient()

def try_one(label, func, **kwargs):
    print(f"\nTesting {label}: {kwargs}")
    try:
        try:
            data = func(as_df=True, **kwargs)
        except TypeError:
            data = func(**kwargs)
        if hasattr(data, "shape"):
            print("OK shape:", data.shape)
        elif isinstance(data, list):
            print("OK list rows:", len(data))
        elif isinstance(data, dict):
            print("OK dict keys:", list(data.keys())[:10])
        else:
            print("OK type:", type(data))
    except Exception as exc:
        print("FAILED:", repr(exc))

try_one("TER", client.ter.fetch, month="05-2026")
try_one("Tracking Error", client.tracking.error, date="31-may-2026")
try_one("Tracking Difference", client.tracking.difference, month="01-May-2026")
try:
    fys = client.aum.financial_years()
    print("\nAUM financial years sample:", fys[:2] if isinstance(fys, list) else fys)
except Exception as exc:
    print("AUM financial years FAILED:", repr(exc))
