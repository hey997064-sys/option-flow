#!/usr/bin/env python3
"""Production entry: fetch → compute → ai_payload JSON to stdout. ZERO disk writes.

Plugin-runtime entrypoint. Invoked by the option-flow skill as:
    python3 "${CLAUDE_PLUGIN_ROOT}/option_flow.py" <SYMBOL.US>

For local dev with on-disk payload inspection, use run.py instead (dev-only).
"""
from __future__ import annotations

import json
import sys

from fetch import CLIError, NoOptionsError, fetch
from compute import compute


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python3 option_flow.py <SYMBOL.US>   (e.g. NVDA.US)", file=sys.stderr)
        return 2
    symbol = argv[1].upper()
    try:
        raw = fetch(symbol)
    except NoOptionsError as e:
        print(f"NoOptionsError: {e}", file=sys.stderr)
        return 3
    except CLIError as e:
        print(f"CLIError: {e}", file=sys.stderr)
        return 4
    except ValueError as e:
        # fetch() raises ValueError only for non-US symbols (see fetch docstring).
        print(f"NonUSError: option-flow 当前仅支持美股（.US 后缀）：{e}", file=sys.stderr)
        return 5
    ai = compute(raw)
    print(json.dumps(ai, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
