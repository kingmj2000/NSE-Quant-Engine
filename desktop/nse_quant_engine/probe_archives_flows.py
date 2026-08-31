"""Probe: is any NSE ARCHIVES-host path a usable institutional-flow source?

WHY THIS EXISTS
---------------
The engine's own results show a clean split by host, not by feed:

    WORKS   archives.nseindia.com/content/equities/bulk.csv          (static CSV)
    WORKS   nsearchives.nseindia.com/products/content/sec_bhavdata_* (static CSV)
    BLOCKED www.nseindia.com/api/historical/fiidiiTradeReact         (503 block page)
    BLOCKED www.nseindia.com/api/option-chain-equities               (60/60 misses)
    OK      www.nseindia.com/api/fiidiiTradeReact                    (latest day only)

Static files on the archives hosts are served; JSON APIs on www are gated. Note
that the fii_dii source NAMED "nse-archive" in optional_data_fetchers.py is not
on an archives host at all — it is the www historical API. So the archives hosts
have never actually been tried for flows.

This script tries them. It writes nothing and touches no cache: it prints a
table and exits.

READ THE RESULT HONESTLY
-----------------------
The fao_participant_* files, if they exist, are PARTICIPANT-WISE F&O ACTIVITY
(FII / DII / Pro / Client open interest and volume). That is derivatives
positioning, NOT cash-market FII/DII rupee flows. A hit here does not backfill
FII_Net_INR_Cr and is not a drop-in fix for the 14 missing days. It would be a
new feed, which needs its own decision — see handoff section 9.

The two known-good controls are included on purpose. If the controls fail too,
this machine had a network or DNS problem and the whole run is uninformative.

Usage:
    python probe_archives_flows.py            # last completed business day
    python probe_archives_flows.py 28-08-2026 # a specific DD-MM-YYYY
"""
from __future__ import annotations

import io
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Reuse the engine's own session so a PASS here transfers to the engine. Probing
# with a hand-rolled session proves nothing about what the engine can reach.
from core.optional_data_fetchers import _requests_session, _nse_warmup  # noqa: E402

BLOCK_MARKERS = ("noindex", "Access Denied", "Request unsuccessful",
                 "<html", "Incapsula", "captcha")


def _last_business_day() -> datetime:
    d = datetime.now() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def candidates(d: datetime) -> list[tuple[str, str, str]]:
    """(label, url, kind) — kind is 'control' or 'candidate'."""
    dd = d.strftime("%d%m%Y")
    return [
        ("bulk deals (CONTROL, known good)",
         "https://archives.nseindia.com/content/equities/bulk.csv", "control"),
        ("bhavcopy full (CONTROL, known good)",
         f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{dd}.csv",
         "control"),
        ("participant-wise F&O OI (nsearchives)",
         f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{dd}.csv",
         "candidate"),
        ("participant-wise F&O OI (archives)",
         f"https://archives.nseindia.com/content/nsccl/fao_participant_oi_{dd}.csv",
         "candidate"),
        ("participant-wise F&O volume (nsearchives)",
         f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_vol_{dd}.csv",
         "candidate"),
        ("cash-market FII/DII latest (CONTROL, engine's live source)",
         "https://www.nseindia.com/api/fiidiiTradeReact", "control"),
    ]


def probe(sess, label: str, url: str, kind: str) -> dict:
    row = {"feed": label, "kind": kind, "status": "", "bytes": 0,
           "shape": "", "verdict": ""}
    try:
        r = sess.get(url, timeout=20,
                     headers={"Referer": "https://www.nseindia.com/"})
    except Exception as exc:
        row["status"] = type(exc).__name__
        row["verdict"] = f"unreachable: {exc}"
        return row

    body = r.text or ""
    row["status"] = str(r.status_code)
    row["bytes"] = len(r.content or b"")

    if r.status_code != 200:
        hit = next((m for m in BLOCK_MARKERS if m.lower() in body.lower()), None)
        row["verdict"] = (f"HTTP {r.status_code}" +
                          (f", block page ({hit})" if hit else ""))
        return row

    if body.lstrip().startswith(("{", "[")):
        try:
            payload = r.json()
            n = len(payload) if isinstance(payload, list) else len(payload.get("data") or [])
            row["shape"] = f"json, {n} entries"
            row["verdict"] = "USABLE (json)" if n else "200 but empty payload"
        except Exception as exc:
            row["verdict"] = f"200 but unparseable json: {exc}"
        return row

    if any(m.lower() in body[:4000].lower() for m in BLOCK_MARKERS):
        row["verdict"] = "200 but HTML — block/interstitial page, not a file"
        return row

    try:
        df = pd.read_csv(io.StringIO(body))
    except Exception as exc:
        row["verdict"] = f"200 but not parseable as CSV: {type(exc).__name__}"
        return row

    row["shape"] = f"{len(df)} rows x {len(df.columns)} cols"
    cols = " ".join(str(c).lower() for c in df.columns)
    flavour = [k for k in ("fii", "fpi", "dii", "client", "pro", "buy", "sell",
                           "net", "oi") if k in cols]
    row["verdict"] = ("USABLE (csv)" if len(df) else "200, CSV, zero rows")
    if flavour:
        row["verdict"] += f" | columns mention: {', '.join(flavour)}"
    return row


def main() -> int:
    d = (datetime.strptime(sys.argv[1], "%d-%m-%Y")
         if len(sys.argv) > 1 else _last_business_day())
    print(f"probing for trade date {d:%Y-%m-%d} "
          f"(pass DD-MM-YYYY to change)\n")

    sess = _requests_session()
    try:
        _nse_warmup(sess)
    except Exception as exc:
        print(f"warn: cookie warm-up failed ({exc}) — www results will be "
              f"pessimistic, archives results are still valid\n")

    rows = [probe(sess, *c) for c in candidates(d)]
    out = pd.DataFrame(rows)[["kind", "feed", "status", "bytes", "shape", "verdict"]]
    with pd.option_context("display.max_colwidth", 60, "display.width", 200):
        print(out.to_string(index=False))

    controls = [r for r in rows if r["kind"] == "control"]
    good_controls = [r for r in controls if "USABLE" in r["verdict"]]
    print()
    if not good_controls:
        print("INCONCLUSIVE: every control failed too. This looks like a local "
              "network/DNS problem, not an NSE block. Re-run before concluding "
              "anything.")
        return 2
    hits = [r for r in rows if r["kind"] == "candidate" and "USABLE" in r["verdict"]]
    if hits:
        print(f"{len(hits)} archives candidate(s) reachable. Reminder: "
              f"participant-wise F&O activity is NOT cash-market FII/DII rupee "
              f"flows — it does not backfill FII_Net_INR_Cr. Decide whether it "
              f"is worth adding as a separate context feed before wiring "
              f"anything.")
    else:
        print("No archives candidate is reachable. The archives-host theory is "
              "dead: NSE serves cash-market FII/DII only through the www "
              "endpoints, and only the latest published day is ungated. Running "
              "after 19:00 IST remains the only lever.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
