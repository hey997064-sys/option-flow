"""Data fetch layer for the /option-flow skill.

Pulls a raw_payload dict (see references/raw-payload-schema.md) using the
``longbridge`` CLI as the only IO boundary. No SDK imports — keeps the skill
free from the heavyweight Options Edge dependency stack.

Flow per symbol (single public entry: ``fetch(symbol)``):
  1. ``longbridge option chain <symbol>``          → all future expiries
  2. Pick 3 windows (≤14d / 30-60d / 90-180d), all expiries per window
  3. In parallel: ``option chain --date <d>`` for each chosen expiry
                  + ``option volume daily``
                  + ``kline``  (also gives current_price + stock_closes)
  4. ATM±10% filter on each window's strikes
  5. Build OCC symbols, one bulk ``option quote`` call → OI / IV / volume
  6. Assemble raw_payload dict matching references/raw-payload-schema.md contract

Rate-limit posture:
  - option_quote is server-side limited to ~200/60s (Options Edge code says
    so; CLAUDE.md confirms). We send ONE bulk call per symbol — ~60 symbols
    counts as ~60 lookups → well under 150/60s budget.
  - On error code 301607 → sleep 60s, retry once.
  - On transient errors (5xx / timeout / connection reset / network) →
    sleep 5s, retry once. Independent of the quota retry path.

Timezone posture (v3 hard rule, v4 extended to fetched_at):
  - All "today" boundaries use America/New_York. Intraday US data has NO
    analytical value for this report — IV, OI, and price all bounce around
    during the session and look misleading next to T+1 PCR. We FORCE the
    snapshot to anchor on the most recent fully-closed US trading day.
  - current_price = close of the latest prior-trading-day row in the daily
    kline (any in-progress "today" row in ET is filtered out).
  - PCR timestamps converted to ET when deriving date.
  - fetched_at is now ET-anchored (v4 cosmetic fix — was machine-local in
    v3, which was HK on user's laptop and inconsistent with snapshot_date /
    data_as_of / pcr_latest_date).

v5 addition: top-level ``is_intraday`` boolean flag.
  - True when the fetch happens during US equity regular session
    (Mon-Fri 09:30-16:00 ET). Downstream (SKILL.md) uses this flag to
    render a risk-warning banner rather than block the run.
  - Holidays not handled — would require broker calendar integration.

v6 cleanup: structural redundancy removal.
  - Removed outer transient-retry wrapper around the entry ``option chain``
    call in step 1. v4 pushed transient-retry logic INTO ``_run_cli`` itself
    (via ``retry_on_transient=True`` + ``TRANSIENT_RETRY_SLEEP``), so every
    call site — including the entry chain — already gets one-shot transient
    retry for free. The outer try/except + sleep + retry block at step 1 was
    a belt-and-suspenders relic from v3 and is now a no-op duplicate of the
    inner path: same marker table, same sleep, same one retry.
  - Removed the helper ``_looks_transient`` which existed only to support
    that outer wrapper (its sole call site). The inner equivalent
    ``_is_transient_stderr`` is the canonical transient detector and stays.
  - Net effect: one retry path, one marker table, one sleep constant —
    no behavioral change, just less surface area to keep in sync.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------- exceptions

class CLIError(RuntimeError):
    """``longbridge`` CLI returned a non-zero exit code or unparseable output."""


class NoOptionsError(RuntimeError):
    """Symbol has no listed options (e.g. small caps, recent IPO)."""


# ---------------------------------------------------------------- tunables

# ATM±10% strike window per (window) — matches Options Edge's ±15% but tighter
# because skill needs ~10 strikes/window, not the full surface.
STRIKE_WINDOW_PCT = 0.15
# Previously had STRIKES_PER_WINDOW_CAP = 10 which truncated remote strikes
# per-expiry (sorted by |strike-ATM|, kept closest 10). This made butterfly
# chart tails look thin because some remote strikes only appeared in 1-2
# expiries instead of all. Removed — let ATM±10% range be the sole filter.

# Window definitions in days-to-expiry
WINDOWS = [
    ("short", 0, 14),     # ≤14 days
    ("mid", 30, 60),      # 30-60 days
    ("long", 90, 180),    # 90-180 days
]

# Per-window expiry strategy: pull ALL expiries inside each window.
# Previously capped at 2 (sampled by sort+spread) — this missed the 4 mid-week
# weeklies in ≤14d (e.g. NVDA had 6 expiries in ≤14d but we only grabbed 5/22
# and 6/3, dropping 5/26 / 5/27 / 5/29 / 6/1). OI on those missing weeklies is
# why our OI numbers didn't match broker UIs.

# CLI defaults
CLI_TIMEOUT = 25  # seconds per call
QUOTA_RETRY_SLEEP = 60  # seconds after 301607
TRANSIENT_RETRY_SLEEP = 5  # seconds after transient failure (5xx / timeout / network)

# Parallelism for chain-by-date calls. Each is one HTTP request so concurrency
# is bounded by the quote-quota window; 6 in flight is safe.
CHAIN_PARALLEL = 6

# Bulk option_quote batch cap. Empirically the server returns 500 above ~100
# symbols (saw AAPL 120 batch fail). 50 keeps us inside any soft limit and
# still finishes ~60-symbol payload in 2-3 round trips.
QUOTE_BATCH_SIZE = 50

# If after assembling all quote chunks the contract coverage drops below
# this ratio of expected (OCC) symbols, treat the payload as untrustworthy
# and raise. Threshold lifted from the issue spec (70%).
QUOTE_COVERAGE_MIN_RATIO = 0.7

# Coverage floor for the parallel chain-by-date step. If more than this
# fraction of expiries fail their chain fetch we raise — silent drops of a
# few expiries can knock OI down 30% with no error signal downstream.
CHAIN_EXPIRY_FAIL_RATIO = 0.3

# PCR staleness threshold. PCR is broker T+1 (released noon-ish on day D+1).
# Past 5 calendar days = something is wrong (broker hiccup, broken pipeline,
# or symbol delisted from PCR feed). We WARN but do not block — downstream
# decides whether to use the field.
PCR_STALENESS_DAYS = 5


# ---------------------------------------------------------------- intraday flag

def _compute_is_intraday(now: datetime | None = None) -> bool:
    """True if current ET time is during US equity regular session.

    Regular session = 9:30 ET to 16:00 ET, Monday through Friday.
    Holidays not handled in this version (will need broker calendar to do properly).

    ``now`` is an optional override (used by tests). When None, queries
    ``datetime.now(ZoneInfo("America/New_York"))``.
    """
    et_zone = ZoneInfo("America/New_York")
    et_now = now if now is not None else datetime.now(et_zone)
    if et_now.weekday() >= 5:  # Sat/Sun
        return False
    market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = et_now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= et_now < market_close


# ---------------------------------------------------------------- CLI helpers

# Transient-failure stderr substring markers. Case-insensitive match.
# Distinct from the quota path (301607); orthogonal failure class meaning
# "broker / network flaked, try again once" — 5xx HTTP, timeouts, connection
# resets, generic network failures.
_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "500",
    "502",
    "503",
    "504",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "internal server error",
    "connection reset",
    "connection refused",
    "temporarily unavailable",
    "network",
)


def _is_transient_stderr(stderr: str) -> bool:
    """True if stderr looks like a transient broker / network failure.

    Case-insensitive substring match against ``_TRANSIENT_MARKERS``.
    """
    if not stderr:
        return False
    needle = stderr.lower()
    return any(marker in needle for marker in _TRANSIENT_MARKERS)


def _run_cli(
    args: list[str],
    *,
    retry_on_quota: bool = True,
    retry_on_transient: bool = True,
) -> Any:
    """Run a ``longbridge`` CLI command and return parsed JSON.

    Re-raises CLIError on non-zero exit. Two independent retry paths:

    1. **Quota retry** (``retry_on_quota``): stderr contains ``301607`` →
       sleep ``QUOTA_RETRY_SLEEP`` seconds, retry once. Quota takes
       precedence over the transient path.
    2. **Transient retry** (``retry_on_transient``): stderr matches one of
       ``_TRANSIENT_MARKERS`` (5xx / timeout / connection reset / network) →
       sleep ``TRANSIENT_RETRY_SLEEP`` seconds, retry once.

    Both retries are one-shot — the recursive call disables the matching
    retry flag, so a second consecutive failure of the same class raises
    CLIError as today. The two flags are independent: a quota retry that
    then sees a transient error on the second attempt still retries once
    on the transient path, and vice-versa. Total worst-case attempts = 3
    (initial + 1 quota retry + 1 transient retry), which is bounded.
    """
    cmd = ["longbridge", *args, "--format", "json"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        # subprocess-level timeout is itself a transient signal. Use the
        # same retry policy as stderr-detected timeouts.
        if retry_on_transient:
            time.sleep(TRANSIENT_RETRY_SLEEP)
            return _run_cli(
                args,
                retry_on_quota=retry_on_quota,
                retry_on_transient=False,
            )
        raise CLIError(f"timeout after {CLI_TIMEOUT}s: {' '.join(args)}") from e

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        # Quota first — precedence per spec.
        if "301607" in stderr and retry_on_quota:
            time.sleep(QUOTA_RETRY_SLEEP)
            return _run_cli(
                args,
                retry_on_quota=False,
                retry_on_transient=retry_on_transient,
            )
        # Transient second.
        if retry_on_transient and _is_transient_stderr(stderr):
            time.sleep(TRANSIENT_RETRY_SLEEP)
            return _run_cli(
                args,
                retry_on_quota=retry_on_quota,
                retry_on_transient=False,
            )
        raise CLIError(
            f"longbridge {' '.join(args)} failed (exit {proc.returncode}): {stderr[:300]}"
        )

    stdout = proc.stdout or ""
    if not stdout.strip():
        raise CLIError(f"empty stdout from: longbridge {' '.join(args)}")

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        raise CLIError(
            f"non-JSON output from longbridge {' '.join(args[:3])}: {stdout[:200]}"
        ) from e


# ---------------------------------------------------------------- OCC builder

def _ticker_of(symbol: str) -> str:
    """``NVDA.US`` → ``NVDA``."""
    return symbol.split(".")[0]


def _market_of(symbol: str) -> str:
    """``NVDA.US`` → ``US``."""
    return symbol.split(".")[1]


def _make_occ(ticker: str, market: str, expiry: str, opt_type: str, strike: float) -> str:
    """Build Longbridge OCC symbol.

    Example: NVDA + US + 2026-05-22 + call + 220.0 → ``NVDA260522C220000.US``

    Strike encoding: integer milli-dollars (no leading-zero padding). Verified
    against live CLI output (see staging/A_fetch/README.md).

    Raises CLIError when strike < 0.001 — the integer-milli-dollar encoding
    would round to 0 and silently collide with other sub-cent strikes.
    """
    if strike < 0.001:
        raise CLIError(
            f"strike {strike!r} too small to encode as integer milli-dollars "
            f"({ticker} {expiry} {opt_type})"
        )
    y, m, d = expiry.split("-")
    yymmdd = f"{y[2:]}{m}{d}"
    cp = "C" if opt_type.lower().startswith("c") else "P"
    # 220.0 → 220000;  217.5 → 217500;  4.5 → 4500
    strike_milli = int(round(strike * 1000))
    return f"{ticker}{yymmdd}{cp}{strike_milli}.{market}"


# ---------------------------------------------------------------- expiry picking

def _pick_expiries(expiries: list[str], today: date) -> list[tuple[str, str, int]]:
    """Return [(bucket, expiry_iso, dte)] for ALL expiries inside the 3 windows.

    No sampling/capping — every weekly inside ≤14d / 30-60d / 90-180d is pulled.
    OI on the missing mid-week weeklies (≤14d had 5/22, 5/26, 5/27, 5/29, 6/1,
    6/3 for NVDA) was the source of broker UI mismatch.
    """
    picks: list[tuple[str, str, int]] = []
    used: set[str] = set()
    for bucket, lo, hi in WINDOWS:
        for e in expiries:
            try:
                d = date.fromisoformat(e)
            except ValueError:
                continue
            dte = (d - today).days
            if lo <= dte <= hi and e not in used:
                picks.append((bucket, e, dte))
                used.add(e)
    picks.sort(key=lambda x: x[2])
    return picks


# ---------------------------------------------------------------- chain → strikes

def _strikes_for_expiry(symbol: str, expiry: str, current_price: float) -> list[float]:
    """Pull chain for one expiry, return all strikes inside ATM±STRIKE_WINDOW_PCT.

    Returns strikes sorted ascending. No cap — every strike whose value falls
    in [current_price*(1-STRIKE_WINDOW_PCT), current_price*(1+STRIKE_WINDOW_PCT)]
    is included. The earlier "cap at 10 closest" behaviour was removed in
    Phase 3 because it made the butterfly chart tails look artificially thin
    on the longer-dated expiries.
    """
    raw = _run_cli(["option", "chain", symbol, "--date", expiry])
    if not raw:
        return []

    # Coerce strikes to float
    parsed: list[tuple[float, dict]] = []
    for row in raw:
        try:
            s = float(row["strike"])
        except (KeyError, TypeError, ValueError):
            continue
        parsed.append((s, row))
    if not parsed:
        return []

    lo = current_price * (1 - STRIKE_WINDOW_PCT)
    hi = current_price * (1 + STRIKE_WINDOW_PCT)
    in_window = sorted(s for s, _ in parsed if lo <= s <= hi)
    return in_window


# ---------------------------------------------------------------- bucket assignment

def _bucket_for_dte(dte: int) -> str | None:
    """Map a days-to-expiry to one of {'short','mid','long'} or None."""
    for bucket, lo, hi in WINDOWS:
        if lo <= dte <= hi:
            return bucket
    return None


# ---------------------------------------------------------------- PCR fetch (extracted)

def _fetch_pcr_daily(symbol: str, count: int = 30) -> dict:
    """Pull broker daily option volume series (PCR + OI totals).

    Extracted as a module-level function so the --debug-pcr-tz diagnostic can
    re-invoke it without duplicating the CLI shape.
    """
    return _run_cli(["option", "volume", "daily", symbol, "--count", str(count)])


# ---------------------------------------------------------------- main entry

def fetch(symbol: str) -> dict:
    """Fetch a raw_payload for ``symbol`` (US market only).

    Args:
        symbol: e.g. ``"NVDA.US"``. Must end in ``.US`` (skill is US-only).

    Returns:
        raw_payload dict per references/raw-payload-schema.md.

    Raises:
        CLIError: longbridge CLI failure (non-zero exit, timeout, bad JSON).
        NoOptionsError: symbol has no listed options.
        ValueError: symbol not US-market.
    """
    if not symbol.endswith(".US"):
        raise ValueError(f"only US options supported, got {symbol!r}")

    ticker = _ticker_of(symbol)
    market = _market_of(symbol)
    # snapshot_date is the calendar date in ET — the user's analytical frame
    # is the US market, NOT the machine's local timezone. Using date.today()
    # here would silently flip a day if the call happens to run in the early
    # ET morning (late HK afternoon) and meanwhile the daily kline still has
    # only yesterday's ET close — dte values would then be off-by-one.
    et_zone = ZoneInfo("America/New_York")
    et_today = datetime.now(et_zone).date()
    today = et_today  # used by _pick_expiries — same anchor
    snapshot_date = et_today.isoformat()
    # v4: fetched_at is now ET-anchored for consistency with snapshot_date /
    # data_as_of / pcr_latest_date. v3 used datetime.now().astimezone() which
    # was HK on the user's laptop — cosmetic inconsistency, no downstream
    # parsing impact, but ET is the canonical reference for everything else
    # in the payload.
    fetched_at = datetime.now(et_zone).isoformat(timespec="seconds")

    # ------------------------------------------------------------ step 1: chain → expiry list
    # The initial chain call is the entry door — if it fails the whole skill
    # bails. _run_cli already handles 301607 quota AND transient retry
    # internally (v4 pushed transient retry into _run_cli), so a plain call
    # is sufficient. v6 removed the v3-era outer try/except + sleep + retry
    # wrapper here — it was a structural duplicate of the inner path.
    expiry_rows = _run_cli(["option", "chain", symbol])
    if not expiry_rows:
        raise NoOptionsError(f"{symbol}: no expiry dates returned (no listed options)")
    expiries = [r["expiry_date"] for r in expiry_rows if "expiry_date" in r]
    if not expiries:
        raise NoOptionsError(f"{symbol}: chain response missing expiry_date field")

    # ------------------------------------------------------------ step 2: parallel base fetches
    # kline (gives current_price + stock_closes), volume daily (gives pcr_history)
    # — both independent of expiry picks, so fire in parallel.
    def _fetch_kline() -> list[dict]:
        # --count 45 gives a buffer over weekends/holidays so the resulting
        # stock_closes lands at >= 30 trading days (HV downstream needs ~30).
        # Previously 32 included weekends → only ~22 trading days reached HV.
        return _run_cli(["kline", symbol, "--period", "day", "--count", "45"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_kline = pool.submit(_fetch_kline)
        f_pcr = pool.submit(_fetch_pcr_daily, symbol)
        kline_rows = f_kline.result()
        pcr_resp = f_pcr.result()

    if not kline_rows:
        raise CLIError(f"{symbol}: empty kline response")

    # current_price + data_as_of use kline's last row directly (no filtering).
    # Rationale (revised vs v3):
    #   - Intraday: last row IS today's intraday spot. distance_pct (Wall vs
    #     current_price) then reflects *current* position — useful. IV is
    #     genuinely live, handled by the is_intraday flag + downstream banner.
    #   - After-close (ET ≥ 16:00): last row is today's finalized close.
    #   - Weekend / holiday: last row is Friday close.
    # OI is exchange-settled at day-end so doesn't move intraday; PCR is broker
    # T+1 so doesn't move intraday either. Only IV truly drifts — handled by
    # the banner. There's no need to force everyone back to D-1.
    try:
        current_price = float(kline_rows[-1]["close"])
    except (KeyError, TypeError, ValueError) as e:
        raise CLIError(f"{symbol}: malformed close on last kline row: {e}") from e

    try:
        data_as_of = kline_rows[-1]["time"].split(" ")[0]
    except (KeyError, AttributeError, IndexError) as e:
        raise CLIError(f"{symbol}: malformed time on last kline row: {e}") from e

    # stock_closes (HV input) still excludes today's intraday row during
    # market hours — HV math needs *closed* sessions only.
    is_intraday_now = _compute_is_intraday()
    et_today_iso = et_today.isoformat()
    if is_intraday_now:
        hv_source_rows = [
            row for row in kline_rows
            if row.get("time", "").split(" ")[0] != et_today_iso
        ]
    else:
        hv_source_rows = list(kline_rows)
    if not hv_source_rows:
        raise CLIError(f"{symbol}: no fully-closed kline rows for HV computation")

    # ------------------------------------------------------------ step 3: pick expiries
    picks = _pick_expiries(expiries, today)
    if not picks:
        raise NoOptionsError(
            f"{symbol}: no expiries within any window (≤14d / 30-60d / 90-180d)"
        )

    # ------------------------------------------------------------ step 4: parallel chain-by-date
    # Map (bucket, expiry, dte) → list of strikes (ATM±STRIKE_WINDOW_PCT)
    expiry_strikes: dict[tuple[str, str, int], list[float]] = {}
    failed_expiries: list[tuple[str, str]] = []  # (expiry, error_msg)
    total_expiries = len(picks)
    with ThreadPoolExecutor(max_workers=CHAIN_PARALLEL) as pool:
        futures = {
            pool.submit(_strikes_for_expiry, symbol, exp, current_price): (bucket, exp, dte)
            for bucket, exp, dte in picks
        }
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                strikes = fut.result()
            except CLIError as e:
                # Surface the failure: a silent drop of one expiry knocks
                # OI down by 1/N with no signal. The coverage guard below
                # enforces a hard floor; below the floor we raise.
                bucket, exp, dte = key
                print(
                    f"WARN: chain --date {exp} failed: {e}",
                    file=sys.stderr,
                )
                failed_expiries.append((exp, str(e)))
                strikes = []
            expiry_strikes[key] = strikes

    if total_expiries > 0 and (len(failed_expiries) / total_expiries) > CHAIN_EXPIRY_FAIL_RATIO:
        failed_list = ", ".join(exp for exp, _ in failed_expiries)
        raise CLIError(
            f"chain fetch failed for {len(failed_expiries)}/{total_expiries} expiries "
            f"({failed_list}); above {CHAIN_EXPIRY_FAIL_RATIO:.0%} fail threshold"
        )

    # ------------------------------------------------------------ step 5: build OCC symbols
    # OCC keys are normalized to upper-case both when stored and when looked
    # up in step 6 — guards against a future broker change in symbol casing
    # that would otherwise silently drop every contract.
    occ_to_meta: dict[str, dict] = {}
    for (bucket, expiry, dte), strikes in expiry_strikes.items():
        for strike in strikes:
            for cp in ("call", "put"):
                occ = _make_occ(ticker, market, expiry, cp, strike).upper()
                occ_to_meta[occ] = {
                    "bucket": bucket,
                    "expiry": expiry,
                    "dte": dte,
                    "strike": strike,
                    "type": cp,
                }

    if not occ_to_meta:
        raise NoOptionsError(
            f"{symbol}: no strikes within ATM±{STRIKE_WINDOW_PCT*100:.0f}% in any window"
        )

    # ------------------------------------------------------------ step 6: bulk option quote
    occ_list = list(occ_to_meta.keys())
    # Chunk to QUOTE_BATCH_SIZE; fire chunks in parallel. Server soft-limits
    # large batches (saw HTTP 500 on a 120-symbol AAPL batch). v4: each
    # _run_cli call now retries once on transient 5xx / timeout via the new
    # retry_on_transient path — this is where most of the win lands, the
    # NVDA v3 run dropped 50 contracts because one quote chunk hit 500.
    chunks = [
        occ_list[i:i + QUOTE_BATCH_SIZE]
        for i in range(0, len(occ_list), QUOTE_BATCH_SIZE)
    ]
    quote_rows: list[dict] = []
    failed_chunks = 0
    total_chunks = len(chunks)
    if total_chunks == 1:
        try:
            quote_rows = _run_cli(["option", "quote", *chunks[0]])
        except CLIError as e:
            # Print warning so a missing chunk is observable upstream, then
            # treat as a hard failure (single-chunk failure = 100% loss).
            print(
                f"WARN: option quote chunk 1/1 failed: {e}",
                file=sys.stderr,
            )
            failed_chunks = 1
            quote_rows = []
    else:
        with ThreadPoolExecutor(max_workers=min(len(chunks), CHAIN_PARALLEL)) as pool:
            chunk_futures = {
                pool.submit(_run_cli, ["option", "quote", *chunk]): idx
                for idx, chunk in enumerate(chunks, start=1)
            }
            for fut in as_completed(chunk_futures):
                idx = chunk_futures[fut]
                try:
                    rows = fut.result()
                except CLIError as e:
                    # Per-chunk failure shouldn't kill the whole payload yet —
                    # but we MUST surface it. Silent drops mean downstream sees
                    # fewer contracts with no error signal. After the loop we
                    # also enforce a coverage floor.
                    print(
                        f"WARN: option quote chunk {idx}/{total_chunks} failed: {e}",
                        file=sys.stderr,
                    )
                    failed_chunks += 1
                    rows = []
                if isinstance(rows, list):
                    quote_rows.extend(rows)

    contracts: list[dict] = []
    for q in quote_rows:
        # OCC keys stored upper-case in step 5 — normalize lookup side too.
        raw_sym = q.get("symbol")
        sym = raw_sym.upper() if isinstance(raw_sym, str) else None
        meta = occ_to_meta.get(sym) if sym else None
        if not meta:
            continue
        try:
            iv = float(q.get("implied_volatility")) if q.get("implied_volatility") is not None else None
        except (TypeError, ValueError):
            iv = None
        try:
            oi = int(q.get("open_interest")) if q.get("open_interest") is not None else 0
        except (TypeError, ValueError):
            oi = 0
        try:
            vol = int(q.get("volume")) if q.get("volume") is not None else 0
        except (TypeError, ValueError):
            vol = 0
        contracts.append({
            "option_symbol": sym,
            "type": meta["type"],
            "strike": meta["strike"],
            "expiry": meta["expiry"],
            "days_to_expiry": meta["dte"],
            "bucket": meta["bucket"],
            "open_interest": oi,
            "volume": vol,
            "implied_volatility": iv,
        })

    # Coverage guard: if too few contracts came back relative to what we asked
    # for, the payload is unreliable — better to fail loudly than to ship a
    # report based on a partial chain.
    if len(contracts) < QUOTE_COVERAGE_MIN_RATIO * len(occ_to_meta):
        # Discriminate the entitlement case from a transport failure. When the
        # chain steps succeeded (occ_to_meta is non-empty), NO quote request
        # errored (failed_chunks == 0), yet zero contracts came back, the most
        # likely cause is the account lacking US-options quote entitlement —
        # `option quote` then returns [] (success, no rows) rather than failing.
        # This is NOT an OCC-format bug: the encoding is verified against live
        # CLI output. Spell that out so a downstream AI doesn't re-misdiagnose
        # it as a symbol-format problem and "fix" working code.
        if len(contracts) == 0 and failed_chunks == 0:
            raise CLIError(
                f"{symbol}: option chain 能取到合约，但 option quote 对全部 "
                f"{len(occ_to_meta)} 个合约都返回空（请求成功、0/{total_chunks} chunk 失败）。"
                f"最可能是账号未开通美股期权行情权限（LV1/LV2）——"
                f"请检查 longbridge 账号的期权行情权限。"
                f"这与 OCC 符号格式无关（编码已对 live CLI 实测验证），请勿修改符号格式。"
            )
        raise CLIError(
            f"{symbol}: option quote coverage too low — got {len(contracts)}/"
            f"{len(occ_to_meta)} contracts ("
            f"{len(contracts) / max(len(occ_to_meta), 1):.0%}), threshold "
            f"{QUOTE_COVERAGE_MIN_RATIO:.0%}; "
            f"{failed_chunks}/{total_chunks} chunk(s) failed"
        )

    # Sort: by bucket order then expiry then strike then type
    bucket_order = {b: i for i, (b, _, _) in enumerate(WINDOWS)}
    contracts.sort(key=lambda c: (
        bucket_order.get(c["bucket"], 99),
        c["expiry"],
        c["strike"],
        c["type"],
    ))

    # ------------------------------------------------------------ step 7: pcr_history
    # Broker timestamps converted to America/New_York for date attribution.
    # PCR reflects US market activity; ET is the semantically correct day.
    # (Empirically the broker anchors timestamps at 04:00 UTC = midnight ET,
    # so UTC and ET dates happen to coincide today, but ET is DST-robust and
    # the only correct choice if the broker ever changes the anchor hour.)
    pcr_history: list[dict] = []
    for row in pcr_resp.get("stats", []) if isinstance(pcr_resp, dict) else []:
        try:
            ts = int(row["timestamp"])
            iso_date = datetime.fromtimestamp(ts, tz=et_zone).date().isoformat()
            pcr_oi = float(row["put_call_open_interest_ratio"])
            call_oi = int(row["total_call_open_interest"])
            put_oi = int(row["total_put_open_interest"])
        except (KeyError, ValueError, TypeError):
            continue
        pcr_history.append({
            "date": iso_date,
            "pcr_oi": round(pcr_oi, 6),
            "call_oi_wan": round(call_oi / 10000, 4),
            "put_oi_wan": round(put_oi / 10000, 4),
        })
    # Ascending by date
    pcr_history.sort(key=lambda r: r["date"])
    # Latest PCR date — broker option volume daily is T+1 released around
    # noon, so on a morning run pcr_history[-1].date is yesterday. Exposing
    # the latest date as a top-level field lets compute.py warn / annotate
    # without re-deriving from the array.
    pcr_latest_date: str | None = pcr_history[-1]["date"] if pcr_history else None

    # Staleness check: PCR more than PCR_STALENESS_DAYS calendar days behind
    # ET today is suspicious. WARN (do not block) — pcr_latest_date is the
    # signal, downstream decides whether to use the field.
    if pcr_latest_date is not None:
        try:
            latest_d = date.fromisoformat(pcr_latest_date)
            staleness = (et_today - latest_d).days
            if staleness > PCR_STALENESS_DAYS:
                print(
                    f"WARN: pcr_history is {staleness} days stale "
                    f"(latest={pcr_latest_date}, et_today={et_today_iso})",
                    file=sys.stderr,
                )
        except ValueError:
            pass

    # ------------------------------------------------------------ step 8: stock_closes
    # Built from hv_source_rows: intraday today row is excluded only during
    # market hours (is_intraday=True). HV math always operates on closed
    # sessions, even when current_price / data_as_of reflect today's spot.
    stock_closes: list[dict] = []
    for row in hv_source_rows:
        try:
            iso_date = row["time"].split(" ")[0]
            close = float(row["close"])
        except (KeyError, ValueError, IndexError):
            continue
        stock_closes.append({"date": iso_date, "close": close})

    # ------------------------------------------------------------ assemble
    return {
        "symbol": symbol,
        "fetched_at": fetched_at,
        "snapshot_date": snapshot_date,
        "data_as_of": data_as_of,
        "current_price": current_price,
        "pcr_latest_date": pcr_latest_date,
        "is_intraday": _compute_is_intraday(),
        "contracts": contracts,
        "pcr_history": pcr_history,
        "stock_closes": stock_closes,
    }


# ---------------------------------------------------------------- CLI entry

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: fetch.py SYMBOL", file=sys.stderr)
        sys.exit(2)
    payload = fetch(sys.argv[1])
    print(json.dumps(payload, indent=2, ensure_ascii=False))
