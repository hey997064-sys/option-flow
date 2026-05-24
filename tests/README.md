# option-flow tests

Unit + mutation tests for the `option-flow` skill (compute layer + fetch layer).

## What's tested

### `test_compute.py` (31 tests)
Derived from `compute.py` at the repo root:

- **TestIVPeak** (6): the v3 Patch 1 invariants — DTE ≤ 14 window, IV_SANITY_CEILING 200% filter, trigger ratio iv_peak ≥ iv_far × 1.3, AAPL-flat-surface regression.
- **TestIVWindowMedian** (3): iv_near / iv_far medians and precision tags, IV_SANITY_CEILING filtering.
- **TestWallDirection** (2): call_wall is strictly above current price; put_wall strictly below.
- **TestPassthroughDates** (1): data_as_of / snapshot_date / pcr_latest_date all flow through unchanged.
- **TestPcrLagDays** (4): aligned / lagged / defensive negative-clamp / missing-input.
- **TestIsIntradayPassthrough** (3): true / false / key missing → False default.
- **TestHV30Trading** (2): strict 31-closes-required rule, last-31-window correctness.
- **TestNoDeletedFields** (4): pcr_label, iv_hv_label, pcr_history_n, legacy term_structure keys must stay deleted.
- **TestMutationsAreDetected** (6): every algorithmic invariant has a paired mutation that breaks it.

### `test_fetch.py` (14 tests)
Derived from `fetch.py` at the repo root:

- **TestIsIntradayHelper** (7): weekday-session / pre-market / after-close / Sat / Sun / 09:30 inclusive / 16:00 exclusive (uses the `now=` override parameter — no monkey-patching needed).
- **TestOCCEncoding** (4): NVDA 220 call, 222.5 half-dollar, AAPL put, sub-dollar strike raises CLIError.
- **TestBucketForDte** (1, parameterized 9 cases): 0/14 → short, 15 gap, 30/60 → mid, 61 gap, 90/180 → long, 181 gap.
- **TestSymbolGuard** (2): `.HK` and `.SH` symbols rejected immediately with no `_run_cli` invocation.

## How to run

```bash
cd <repo_root>
python3 -m unittest discover tests -v
```

The test files insert the repo root onto `sys.path` at import time (so `import fetch` / `import compute` resolves to the modules at the repo root). The relative `tests/` → repo-root layout must be preserved.

## Test coverage summary

| Class | Tests | What it pins |
|---|---|---|
| TestIVPeak | 6 | iv_peak: DTE ceiling, trigger ratio, sanity filter, AAPL-flat regression |
| TestIVWindowMedian | 3 | median (not mean) over iv_near / iv_far windows |
| TestWallDirection | 2 | call/put wall direction invariants |
| TestPassthroughDates | 1 | date fields unchanged |
| TestPcrLagDays | 4 | (data_as_of − pcr_latest_date) clamped to ≥ 0 |
| TestIsIntradayPassthrough | 3 | is_intraday → data_quality.is_intraday |
| TestHV30Trading | 2 | strict 31 closes; uses last 31 only |
| TestNoDeletedFields | 4 | deleted fields don't resurface |
| TestMutationsAreDetected | 6 | invariants are load-bearing |
| TestIsIntradayHelper | 7 | session boundary correctness |
| TestOCCEncoding | 4 | OCC string encoding rules |
| TestBucketForDte | 1 (9 subtests) | DTE → bucket mapping |
| TestSymbolGuard | 2 | US-market-only guard |
| **TOTAL** | **45** | — |

## Mutation testing philosophy

Real-data baselines tell you the system isn't broken **right now**. Mutation tests tell you the **invariants** are load-bearing — that if someone later silently breaks them (e.g. lifts IV_SANITY_CEILING during a debug session, swaps median→mean, removes the trigger ratio), the test suite catches it.

Pattern: for each algorithmic invariant, write a unit test that asserts the post-condition, then a paired mutation test that monkey-patches away the invariant and asserts the post-condition fails. The two together prove (a) the invariant currently holds and (b) the test would actually fire under a regression.

This is the harness's "pattern E" (mutation testing validates the validator).

## Future work

- **Real-broker integration tests**: fetch.py's full path (subprocess → longbridge CLI → real broker quote endpoint) is not exercised here. A pre-release manual run of `python3 fetch.py NVDA.US > /tmp/payload.json && python3 compute.py /tmp/payload.json` against a live session is still needed.
- **Live PCR timezone diagnostic**: `_debug_pcr_timezone` is not unit-tested (requires real broker stats); the production code path is well-documented and changes rarely.
- **Coverage threshold guards**: `QUOTE_COVERAGE_MIN_RATIO` and `CHAIN_EXPIRY_FAIL_RATIO` raise CLIError — could be unit-tested by mocking `_run_cli` to return partial results.
