#!/usr/bin/env python3
"""CLI entry: ``python3 run.py NVDA.US`` → prints raw_payload JSON + a summary.

Also writes ``<TICKER>_raw_payload.json`` in the same directory.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

from fetch import CLIError, NoOptionsError, fetch


def _summarize(payload: dict) -> str:
    contracts = payload.get("contracts", [])
    buckets = Counter(c["bucket"] for c in contracts)
    types = Counter(c["type"] for c in contracts)
    strikes_per_bucket = {
        b: len({c["strike"] for c in contracts if c["bucket"] == b})
        for b in ("short", "mid", "long")
    }
    expiries_per_bucket = {
        b: sorted({c["expiry"] for c in contracts if c["bucket"] == b})
        for b in ("short", "mid", "long")
    }
    lines = [
        f"symbol           : {payload['symbol']}",
        f"current_price    : {payload['current_price']}",
        f"fetched_at       : {payload['fetched_at']}",
        f"contracts total  : {len(contracts)}",
        f"  by bucket      : {dict(buckets)}",
        f"  by type        : {dict(types)}",
        f"  strikes/bucket : {strikes_per_bucket}",
        f"  expiries/bucket:",
    ]
    for b in ("short", "mid", "long"):
        if expiries_per_bucket[b]:
            lines.append(f"      {b:5s} : {expiries_per_bucket[b]}")
    lines.extend([
        f"pcr_history      : {len(payload.get('pcr_history', []))} rows",
        f"stock_closes     : {len(payload.get('stock_closes', []))} rows",
    ])
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python3 run.py <SYMBOL>   (e.g. NVDA.US)", file=sys.stderr)
        return 2
    symbol = argv[1].upper()

    t0 = time.time()
    try:
        payload = fetch(symbol)
    except NoOptionsError as e:
        print(f"NoOptionsError: {e}", file=sys.stderr)
        return 3
    except CLIError as e:
        print(f"CLIError: {e}", file=sys.stderr)
        return 4
    elapsed = time.time() - t0

    out_dir = Path(__file__).parent / "_dev_payloads"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{symbol.split('.')[0]}_raw_payload.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    print(_summarize(payload))
    print(f"\nelapsed          : {elapsed:.2f}s")
    print(f"written          : {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
