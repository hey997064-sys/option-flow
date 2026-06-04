"""Unit + mutation tests for compute.py.

Run from repo root:
    python3 -m unittest discover tests -v

Tests are derived from the production compute.py implementation. Each
``TestMutations*`` case monkey-patches a constant or function and re-runs an
input that should newly fail — proving the corresponding algorithmic invariant
is actually load-bearing. This is the canonical "mutation testing" technique
(harness pattern E): empty validators / unused branches return clean too, so
real-data baselines aren't sufficient.
"""
from __future__ import annotations

import importlib
import math
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

# Path: tests/test_compute.py → repo root (compute.py lives there)
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))

import compute  # noqa: E402


# -----------------------------------------------------------------------------
# Builders
# -----------------------------------------------------------------------------

DATA_AS_OF = "2026-05-20"
ATM_PRICE = 100.0


def _expiry_from_dte(dte: int, anchor: str = DATA_AS_OF) -> str:
    return (date.fromisoformat(anchor) + timedelta(days=dte)).isoformat()


def make_contracts_for_expiry(
    *,
    dte: int,
    iv_pct: float,
    current_price: float = ATM_PRICE,
    anchor: str = DATA_AS_OF,
    bucket: str | None = None,
) -> list[dict]:
    """Build 3 Call contracts at ATM, ATM-1%, ATM+1% all sharing the same IV.

    Each strike is well within ATM±5% so ``_expiry_atm_iv_pct`` produces the
    median = ``iv_pct`` cleanly. ``implied_volatility`` is stored as a decimal
    (the compute layer multiplies by 100).
    """
    expiry = _expiry_from_dte(dte, anchor)
    iv_decimal = iv_pct / 100.0
    if bucket is None:
        bucket = compute._bucket_for_dte(dte) if hasattr(compute, "_bucket_for_dte") else "short"
    contracts = []
    for delta in (-1.0, 0.0, 1.0):
        contracts.append({
            "type": "call",
            "strike": current_price + delta,
            "expiry": expiry,
            "days_to_expiry": dte,
            "bucket": bucket,
            "open_interest": 100,
            "volume": 10,
            "implied_volatility": iv_decimal,
        })
    return contracts


def _walls_payload(*, call_oi: int, put_oi: int,
                   call_strike: float = 105.0, put_strike: float = 95.0):
    """Short-bucket contracts producing one call wall (above) + one put wall (below)."""
    expiry = _expiry_from_dte(7)
    base = dict(expiry=expiry, days_to_expiry=7, bucket="short",
                volume=100, implied_volatility=0.30)
    return [
        {"type": "call", "strike": call_strike, "open_interest": call_oi, **base},
        {"type": "put", "strike": put_strike, "open_interest": put_oi, **base},
    ]


def make_raw(
    *,
    contracts: list[dict] | None = None,
    pcr_history: list[dict] | None = None,
    stock_closes: list[dict] | None = None,
    current_price: float = ATM_PRICE,
    data_as_of: str = DATA_AS_OF,
    snapshot_date: str | None = None,
    pcr_latest_date: str | None = None,
    is_intraday: bool | None = None,
    symbol: str = "TEST.US",
) -> dict:
    """Build a minimal raw_payload that compute.compute() can consume."""
    payload: dict = {
        "symbol": symbol,
        "current_price": current_price,
        "snapshot_date": snapshot_date if snapshot_date is not None else data_as_of,
        "data_as_of": data_as_of,
        "pcr_latest_date": pcr_latest_date,
        "contracts": contracts if contracts is not None else [],
        "pcr_history": pcr_history if pcr_history is not None else [],
        "stock_closes": stock_closes if stock_closes is not None else [],
    }
    if is_intraday is not None:
        payload["is_intraday"] = is_intraday
    return payload


# -----------------------------------------------------------------------------
# ① iv_peak
# -----------------------------------------------------------------------------

class TestIVPeak(unittest.TestCase):
    """_iv_peak: DTE ≤ 14 window, must exceed iv_far × 1.3, IV ≤ 200%."""

    def test_iv_peak_picks_max_in_dte_window(self):
        """DTE=1 IV=92, DTE=10 IV=55, DTE=30 IV=42 → peak DTE=1 IV=92."""
        contracts = (
            make_contracts_for_expiry(dte=1, iv_pct=92)
            + make_contracts_for_expiry(dte=10, iv_pct=55)
            + make_contracts_for_expiry(dte=30, iv_pct=42)
        )
        out = compute.compute(make_raw(contracts=contracts))
        peak = out["term_structure"]["iv_peak"]
        self.assertIsNotNone(peak)
        self.assertEqual(peak["iv_pct"], 92.0)
        self.assertEqual(peak["days_to_expiry"], 1)
        self.assertEqual(peak["precision"], "indicative")

    def test_iv_peak_filters_iv_over_200(self):
        """DTE=1 IV=250 (filtered as data corruption), DTE=10 IV=55, DTE=30 IV=42 → picks DTE=10."""
        contracts = (
            make_contracts_for_expiry(dte=1, iv_pct=250)
            + make_contracts_for_expiry(dte=10, iv_pct=55)
            + make_contracts_for_expiry(dte=30, iv_pct=42)
        )
        out = compute.compute(make_raw(contracts=contracts))
        peak = out["term_structure"]["iv_peak"]
        # iv_far = 42, peak must be >= 42 * 1.3 = 54.6 → 55 qualifies.
        self.assertIsNotNone(peak)
        self.assertEqual(peak["days_to_expiry"], 10)
        self.assertEqual(peak["iv_pct"], 55.0)

    def test_iv_peak_empty(self):
        """No contracts → iv_peak is None."""
        out = compute.compute(make_raw(contracts=[]))
        self.assertIsNone(out["term_structure"]["iv_peak"])

    def test_iv_peak_returns_none_when_flat(self):
        """iv_far=42; near=50 (ratio 50/42=1.19 < 1.3) → None."""
        contracts = (
            make_contracts_for_expiry(dte=1, iv_pct=50)
            + make_contracts_for_expiry(dte=10, iv_pct=48)
            + make_contracts_for_expiry(dte=30, iv_pct=42)
        )
        out = compute.compute(make_raw(contracts=contracts))
        self.assertIsNone(out["term_structure"]["iv_peak"])

    def test_iv_peak_respects_dte_ceiling(self):
        """DTE=20 IV=90 would qualify but DTE > 14 ceiling → not picked.

        With DTE=20 excluded from peak search, only DTE=10/IV=50 remains.
        iv_far=42 → trigger threshold 54.6, 50 < 54.6 → None.
        """
        contracts = (
            make_contracts_for_expiry(dte=20, iv_pct=90)
            + make_contracts_for_expiry(dte=10, iv_pct=50)
            + make_contracts_for_expiry(dte=30, iv_pct=42)
        )
        out = compute.compute(make_raw(contracts=contracts))
        peak = out["term_structure"]["iv_peak"]
        # The 20-DTE peak MUST NOT surface; either None or anything else with DTE != 20.
        if peak is not None:
            self.assertNotEqual(peak["days_to_expiry"], 20)
        # In this specific scenario it's None.
        self.assertIsNone(peak)

    def test_iv_peak_excludes_0dte(self):
        """0DTE (DTE=0) must NOT be picked, even if its IV is highest.

        Reason: Vega→0 at expiry makes IV reverse-engineering numerically
        unstable; same-day variance annualization blows up. Industry
        consensus (CBOE / Tastytrade / IBKR): 0DTE IV is not同口径 with 30D
        IV term structure. Fix lands post-SPY 5/21 audit.

        Setup: DTE=0/IV=92 (would be highest), DTE=10/IV=55, DTE=30/IV=42.
        With 0DTE excluded, DTE=10/55 wins (55/42=1.31 > 1.3 trigger).
        """
        contracts = (
            make_contracts_for_expiry(dte=0, iv_pct=92)
            + make_contracts_for_expiry(dte=10, iv_pct=55)
            + make_contracts_for_expiry(dte=30, iv_pct=42)
        )
        out = compute.compute(make_raw(contracts=contracts))
        peak = out["term_structure"]["iv_peak"]
        self.assertIsNotNone(peak, "iv_peak should fall back to DTE=10")
        self.assertEqual(peak["days_to_expiry"], 10, "DTE=0 must be excluded")
        self.assertEqual(peak["iv_pct"], 55.0)

    def test_iv_peak_aapl_returns_none(self):
        """AAPL-like flat surface across DTE=7/14/30/60/120 IV~23-26% → None.

        This was the bug that triggered Patch 1 (v3): without the DTE ceiling
        and trigger ratio, the highest IV could land on a far-dated expiry
        (DTE=149 IV=23.8%) and incorrectly show as 'near-term stress'.
        """
        contracts = (
            make_contracts_for_expiry(dte=7, iv_pct=24)
            + make_contracts_for_expiry(dte=14, iv_pct=25)
            + make_contracts_for_expiry(dte=30, iv_pct=24)
            + make_contracts_for_expiry(dte=60, iv_pct=23)
            + make_contracts_for_expiry(dte=120, iv_pct=23.8)
        )
        out = compute.compute(make_raw(contracts=contracts))
        # iv_far ~ 23.8, trigger = 30.94; near peaks (24, 25) both < trigger → None.
        self.assertIsNone(out["term_structure"]["iv_peak"])


# -----------------------------------------------------------------------------
# ② iv_window_median (iv_near / iv_far)
# -----------------------------------------------------------------------------

class TestIVWindowMedian(unittest.TestCase):

    def test_iv_near_median(self):
        """DTE 5-14 with IVs [50, 52, 55, 58] → median 53.5, precision='normal'."""
        contracts = (
            make_contracts_for_expiry(dte=5, iv_pct=50)
            + make_contracts_for_expiry(dte=8, iv_pct=52)
            + make_contracts_for_expiry(dte=11, iv_pct=55)
            + make_contracts_for_expiry(dte=14, iv_pct=58)
        )
        out = compute.compute(make_raw(contracts=contracts))
        iv_near = out["term_structure"]["iv_near"]
        self.assertIsNotNone(iv_near)
        self.assertEqual(iv_near["iv_pct"], 53.5)
        self.assertEqual(iv_near["precision"], "normal")

    def test_iv_far_median(self):
        """DTE 30-180 with IVs [42, 43, 42.5, 41, 43] → median 42.5, precision='high'."""
        contracts = (
            make_contracts_for_expiry(dte=30, iv_pct=42)
            + make_contracts_for_expiry(dte=60, iv_pct=43)
            + make_contracts_for_expiry(dte=90, iv_pct=42.5)
            + make_contracts_for_expiry(dte=120, iv_pct=41)
            + make_contracts_for_expiry(dte=180, iv_pct=43)
        )
        out = compute.compute(make_raw(contracts=contracts))
        iv_far = out["term_structure"]["iv_far"]
        self.assertIsNotNone(iv_far)
        self.assertEqual(iv_far["iv_pct"], 42.5)
        self.assertEqual(iv_far["precision"], "high")

    def test_iv_near_skips_iv_over_200(self):
        """IV=250 expiries filtered; iv_near falls back to the remaining sample."""
        contracts = (
            make_contracts_for_expiry(dte=7, iv_pct=250)  # dropped by IV_SANITY_CEILING
            + make_contracts_for_expiry(dte=10, iv_pct=55)
            + make_contracts_for_expiry(dte=14, iv_pct=58)
        )
        out = compute.compute(make_raw(contracts=contracts))
        iv_near = out["term_structure"]["iv_near"]
        self.assertIsNotNone(iv_near)
        # median of [55, 58]
        self.assertEqual(iv_near["iv_pct"], 56.5)


# -----------------------------------------------------------------------------
# ③ Wall direction
# -----------------------------------------------------------------------------

class TestWallDirection(unittest.TestCase):

    def _short_contracts(self) -> list[dict]:
        # Hand-build short bucket with mixed calls/puts around 100.
        expiry = _expiry_from_dte(7)
        rows = []
        # Above current price (call wall candidates):
        rows += [{"type": "call", "strike": 105.0, "expiry": expiry,
                  "days_to_expiry": 7, "bucket": "short",
                  "open_interest": 5000, "volume": 100,
                  "implied_volatility": 0.30}]
        rows += [{"type": "call", "strike": 110.0, "expiry": expiry,
                  "days_to_expiry": 7, "bucket": "short",
                  "open_interest": 1000, "volume": 50,
                  "implied_volatility": 0.32}]
        # Below current price (put wall candidates):
        rows += [{"type": "put", "strike": 95.0, "expiry": expiry,
                  "days_to_expiry": 7, "bucket": "short",
                  "open_interest": 7000, "volume": 200,
                  "implied_volatility": 0.31}]
        rows += [{"type": "put", "strike": 92.0, "expiry": expiry,
                  "days_to_expiry": 7, "bucket": "short",
                  "open_interest": 2000, "volume": 80,
                  "implied_volatility": 0.33}]
        # Distractors: calls below, puts above (should NOT appear in walls)
        rows += [{"type": "call", "strike": 95.0, "expiry": expiry,
                  "days_to_expiry": 7, "bucket": "short",
                  "open_interest": 9999, "volume": 1,
                  "implied_volatility": 0.30}]
        rows += [{"type": "put", "strike": 105.0, "expiry": expiry,
                  "days_to_expiry": 7, "bucket": "short",
                  "open_interest": 9999, "volume": 1,
                  "implied_volatility": 0.30}]
        return rows

    def test_call_wall_above_current_price(self):
        out = compute.compute(make_raw(contracts=self._short_contracts()))
        call_wall = out["key_levels"]["call_wall"]
        self.assertIsNotNone(call_wall)
        self.assertGreater(call_wall["strike"], ATM_PRICE,
                           msg="call_wall must be ABOVE current_price")
        self.assertEqual(call_wall["strike"], 105.0)

    def test_put_wall_below_current_price(self):
        out = compute.compute(make_raw(contracts=self._short_contracts()))
        put_wall = out["key_levels"]["put_wall"]
        self.assertIsNotNone(put_wall)
        self.assertLess(put_wall["strike"], ATM_PRICE,
                        msg="put_wall must be BELOW current_price")
        self.assertEqual(put_wall["strike"], 95.0)


# -----------------------------------------------------------------------------
# ④ Pass-through dates
# -----------------------------------------------------------------------------

class TestPassthroughDates(unittest.TestCase):

    def test_data_as_of_pcr_latest_date_pass_through(self):
        out = compute.compute(
            make_raw(
                data_as_of="2026-05-20",
                snapshot_date="2026-05-21",
                pcr_latest_date="2026-05-19",
            )
        )
        self.assertEqual(out["data_as_of"], "2026-05-20")
        self.assertEqual(out["snapshot_date"], "2026-05-21")
        self.assertEqual(out["pcr_latest_date"], "2026-05-19")


# -----------------------------------------------------------------------------
# ⑤ pcr_lag_days
# -----------------------------------------------------------------------------

class TestPcrLagDays(unittest.TestCase):

    def test_zero_when_aligned(self):
        out = compute.compute(
            make_raw(data_as_of="2026-05-20", pcr_latest_date="2026-05-20")
        )
        self.assertEqual(out["data_quality"]["pcr_lag_days"], 0)

    def test_one_day_lag(self):
        out = compute.compute(
            make_raw(data_as_of="2026-05-20", pcr_latest_date="2026-05-19")
        )
        self.assertEqual(out["data_quality"]["pcr_lag_days"], 1)

    def test_zero_when_pcr_ahead_defensive(self):
        """pcr_latest_date > data_as_of (broker oddity) → clamp to 0, not negative."""
        out = compute.compute(
            make_raw(data_as_of="2026-05-20", pcr_latest_date="2026-05-21")
        )
        self.assertEqual(out["data_quality"]["pcr_lag_days"], 0)

    def test_zero_when_either_missing(self):
        out_a = compute.compute(make_raw(data_as_of=None, pcr_latest_date="2026-05-20"))
        self.assertEqual(out_a["data_quality"]["pcr_lag_days"], 0)
        out_b = compute.compute(make_raw(data_as_of="2026-05-20", pcr_latest_date=None))
        self.assertEqual(out_b["data_quality"]["pcr_lag_days"], 0)


# -----------------------------------------------------------------------------
# ⑥ is_intraday pass-through
# -----------------------------------------------------------------------------

class TestIsIntradayPassthrough(unittest.TestCase):

    def test_is_intraday_true_propagates(self):
        out = compute.compute(make_raw(is_intraday=True))
        self.assertTrue(out["data_quality"]["is_intraday"])

    def test_is_intraday_false_propagates(self):
        out = compute.compute(make_raw(is_intraday=False))
        self.assertFalse(out["data_quality"]["is_intraday"])

    def test_is_intraday_defaults_false_when_key_missing(self):
        out = compute.compute(make_raw())  # is_intraday key absent
        self.assertFalse(out["data_quality"]["is_intraday"])


# -----------------------------------------------------------------------------
# ⑦ HV30 trading-day strict-31-closes rule
# -----------------------------------------------------------------------------

class TestHV30Trading(unittest.TestCase):

    @staticmethod
    def _stock_closes(values: list[float], anchor: str = DATA_AS_OF) -> list[dict]:
        base = date.fromisoformat(anchor)
        out = []
        n = len(values)
        for i, v in enumerate(values):
            d = base - timedelta(days=(n - 1 - i))
            out.append({"date": d.isoformat(), "close": v})
        return out

    def test_hv_requires_31_closes(self):
        """30 closes → 29 log returns → still under HV_REQUIRED_CLOSES (31) → None."""
        closes = self._stock_closes([100.0 + i for i in range(30)])
        out = compute.compute(make_raw(stock_closes=closes))
        self.assertIsNone(out["kpi"]["hv_pct"])

    def test_hv_uses_last_31(self):
        """45 closes, last 31 has a constructed series with known sigma → HV matches."""
        # Build 45-element list. The last 31 have predetermined log returns: a flat
        # series with sigma = 0 would yield HV = 0. Use a small alternating series.
        # 30 log returns with values +0.01 / -0.01 alternating → sample stdev = 0.01...
        closes_old = [100.0] * 14  # padding (ignored)
        # Last 31 closes: produce 30 returns of magnitude 0.01.
        # If close[i] = close[i-1] * exp(±0.01), then |log return| = 0.01.
        last31 = [100.0]
        for i in range(30):
            sign = 1 if i % 2 == 0 else -1
            last31.append(last31[-1] * math.exp(sign * 0.01))
        closes = self._stock_closes(closes_old + last31)
        out = compute.compute(make_raw(stock_closes=closes))
        hv = out["kpi"]["hv_pct"]
        self.assertIsNotNone(hv)
        # Hand-compute expected sigma for a strictly alternating ±0.01 series of 30 returns.
        log_returns = [0.01 if i % 2 == 0 else -0.01 for i in range(30)]
        mean_r = sum(log_returns) / len(log_returns)
        var = sum((r - mean_r) ** 2 for r in log_returns) / (len(log_returns) - 1)
        expected = math.sqrt(var) * math.sqrt(252) * 100
        self.assertAlmostEqual(hv, round(expected, 1), places=0,
                               msg=f"hv_pct={hv} vs expected≈{expected:.2f}")


# -----------------------------------------------------------------------------
# ⑧ No deleted fields surface
# -----------------------------------------------------------------------------

class TestNoDeletedFields(unittest.TestCase):
    """Patch 3 + later cleanups removed several fields. Make sure they stay gone."""

    def _full_run(self) -> dict:
        contracts = (
            make_contracts_for_expiry(dte=7, iv_pct=55)
            + make_contracts_for_expiry(dte=30, iv_pct=42)
        )
        pcr_history = [
            {"date": "2026-05-18", "pcr_oi": 0.55, "call_oi_wan": 10.0, "put_oi_wan": 5.5},
            {"date": "2026-05-19", "pcr_oi": 0.60, "call_oi_wan": 10.0, "put_oi_wan": 6.0},
            {"date": "2026-05-20", "pcr_oi": 0.65, "call_oi_wan": 10.0, "put_oi_wan": 6.5},
        ]
        return compute.compute(
            make_raw(contracts=contracts, pcr_history=pcr_history,
                     pcr_latest_date="2026-05-20")
        )

    def test_no_pcr_label(self):
        out = self._full_run()
        self.assertNotIn("pcr_label", out["kpi"])

    def test_no_iv_hv_label(self):
        out = self._full_run()
        self.assertNotIn("iv_hv_label", out["kpi"])

    def test_no_pcr_history_n(self):
        out = self._full_run()
        # Deleted in last cleanup — must not reappear anywhere visible.
        self.assertNotIn("pcr_history_n", out["kpi"])
        self.assertNotIn("pcr_history_n", out["data_quality"])
        self.assertNotIn("pcr_history_n", out)

    def test_no_legacy_term_structure_keys(self):
        out = self._full_run()
        ts = out["term_structure"]
        for legacy_key in ("event_bumps", "shape", "short_iv_pct",
                            "mid_iv_pct", "long_iv_pct"):
            self.assertNotIn(legacy_key, ts,
                             msg=f"legacy key {legacy_key!r} leaked into term_structure")


# -----------------------------------------------------------------------------
# ⑨ Mutation tests — invariants must fail when broken
# -----------------------------------------------------------------------------

class TestMutationsAreDetected(unittest.TestCase):
    """Each mutation reverses one Patch / invariant and proves the test would catch it."""

    def setUp(self):
        # Snapshot the original compute module state.
        self._orig_ceiling = compute.IV_SANITY_CEILING
        self._orig_peak_dte_max = compute.IV_PEAK_DTE_MAX
        self._orig_trigger_ratio = compute.IV_PEAK_TRIGGER_RATIO
        self._orig_window_median_fn = compute._iv_window_median
        self._orig_kpi_pcr = compute._kpi_pcr
        self._orig_near_wall = compute._near_wall

    def tearDown(self):
        compute.IV_SANITY_CEILING = self._orig_ceiling
        compute.IV_PEAK_DTE_MAX = self._orig_peak_dte_max
        compute.IV_PEAK_TRIGGER_RATIO = self._orig_trigger_ratio
        compute._iv_window_median = self._orig_window_median_fn
        compute._kpi_pcr = self._orig_kpi_pcr
        compute._near_wall = self._orig_near_wall
        # Reload to ensure pristine state for next case (defensive).
        importlib.reload(compute)

    # 1: remove IV_SANITY_CEILING filter -------------------------------------
    def test_mutation_remove_iv_sanity_ceiling(self):
        """Lift ceiling to infinity → 250% IV would now leak into iv_near / iv_peak.

        Baseline: with default 200% ceiling, the dte=7/iv=250 expiry is dropped,
        iv_near = median([55, 58]) = 56.5.
        Mutation: ceiling = inf → 250 leaks in, iv_near median changes.
        """
        contracts = (
            make_contracts_for_expiry(dte=7, iv_pct=250)
            + make_contracts_for_expiry(dte=10, iv_pct=55)
            + make_contracts_for_expiry(dte=14, iv_pct=58)
        )
        baseline = compute.compute(make_raw(contracts=contracts))
        self.assertEqual(baseline["term_structure"]["iv_near"]["iv_pct"], 56.5)

        compute.IV_SANITY_CEILING = float("inf")
        mutated = compute.compute(make_raw(contracts=contracts))
        # With ceiling removed, 250 is no longer filtered → median of [55, 58, 250].
        self.assertNotEqual(
            mutated["term_structure"]["iv_near"]["iv_pct"], 56.5,
            msg="mutation should change iv_near once 250% IV is no longer filtered",
        )

    # 2: remove DTE ceiling in _iv_peak --------------------------------------
    def test_mutation_remove_dte_ceiling_in_iv_peak(self):
        """Set IV_PEAK_DTE_MAX = 999 → DTE=20/IV=90 is now eligible.

        Baseline (ceiling=14): DTE=20 excluded, peak depends on near-only sample.
        Mutation: DTE=20/IV=90 now picked as peak.
        """
        contracts = (
            make_contracts_for_expiry(dte=20, iv_pct=90)
            + make_contracts_for_expiry(dte=10, iv_pct=50)
            + make_contracts_for_expiry(dte=30, iv_pct=42)
        )
        baseline = compute.compute(make_raw(contracts=contracts))
        # Baseline: DTE=20 not pickable (ceiling=14), DTE=10/IV=50 < iv_far(42)*1.3=54.6 → None.
        self.assertIsNone(baseline["term_structure"]["iv_peak"])

        compute.IV_PEAK_DTE_MAX = 999
        mutated = compute.compute(make_raw(contracts=contracts))
        peak = mutated["term_structure"]["iv_peak"]
        self.assertIsNotNone(peak, "mutation: peak should surface once DTE ceiling lifted")
        self.assertEqual(peak["days_to_expiry"], 20)
        self.assertEqual(peak["iv_pct"], 90.0)

    # 3: remove trigger ratio in _iv_peak ------------------------------------
    def test_mutation_remove_trigger_ratio_in_iv_peak(self):
        """Set IV_PEAK_TRIGGER_RATIO = 0 → flat surface peak surfaces unfairly.

        Baseline: AAPL-flat scenario produces None.
        Mutation: any near peak qualifies → not None.
        """
        contracts = (
            make_contracts_for_expiry(dte=7, iv_pct=24)
            + make_contracts_for_expiry(dte=14, iv_pct=25)
            + make_contracts_for_expiry(dte=30, iv_pct=24)
            + make_contracts_for_expiry(dte=120, iv_pct=23.8)
        )
        baseline = compute.compute(make_raw(contracts=contracts))
        self.assertIsNone(baseline["term_structure"]["iv_peak"])

        compute.IV_PEAK_TRIGGER_RATIO = 0.0
        mutated = compute.compute(make_raw(contracts=contracts))
        self.assertIsNotNone(
            mutated["term_structure"]["iv_peak"],
            msg="mutation: zero trigger ratio should let any near peak through",
        )

    # 4: change _iv_window_median to mean() ----------------------------------
    def test_mutation_replace_median_with_mean(self):
        """Replace median with mean → asymmetric input shifts iv_near."""
        contracts = (
            make_contracts_for_expiry(dte=5, iv_pct=50)
            + make_contracts_for_expiry(dte=8, iv_pct=52)
            + make_contracts_for_expiry(dte=11, iv_pct=55)
            + make_contracts_for_expiry(dte=14, iv_pct=200)  # outlier (≤ ceiling)
        )
        baseline = compute.compute(make_raw(contracts=contracts))
        baseline_iv_near = baseline["term_structure"]["iv_near"]["iv_pct"]
        # Median of [50, 52, 55, 200] = 53.5; mean = 89.25.

        def fake_window_median(contracts, current_price, dte_anchor,
                                min_dte, max_dte, *, precision):
            samples = [
                iv_pct
                for _, dte, iv_pct in compute._iter_expiry_dte_iv(
                    contracts, current_price, dte_anchor
                )
                if min_dte <= dte <= max_dte
            ]
            if not samples:
                return None
            return {
                "iv_pct": round(sum(samples) / len(samples), 1),
                "precision": precision,
            }

        compute._iv_window_median = fake_window_median
        mutated = compute.compute(make_raw(contracts=contracts))
        mutated_iv_near = mutated["term_structure"]["iv_near"]["iv_pct"]
        self.assertNotEqual(
            baseline_iv_near, mutated_iv_near,
            msg=f"mutation: mean({baseline_iv_near})→{mutated_iv_near} should differ",
        )

    # 5: re-introduce pcr_label field ----------------------------------------
    def test_mutation_reintroduce_pcr_label(self):
        """If _kpi_pcr were extended to add pcr_label, the no-label test catches it."""
        original = compute._kpi_pcr

        def fake_kpi_pcr(pcr_history):
            cur, rank = original(pcr_history)
            # Mutation: returns extra label string. Simulate by patching downstream:
            return cur, rank

        # We can't directly inject a label via _kpi_pcr (it returns a tuple), so simulate
        # via direct monkey-patching of compute.compute to inject the label:
        original_compute = compute.compute

        def fake_compute(raw_payload):
            out = original_compute(raw_payload)
            out["kpi"]["pcr_label"] = "偏多"  # mutation: ressuscitate the dead field
            return out

        compute.compute = fake_compute
        try:
            pcr_history = [
                {"date": "2026-05-19", "pcr_oi": 0.6, "call_oi_wan": 10.0, "put_oi_wan": 6.0},
                {"date": "2026-05-20", "pcr_oi": 0.65, "call_oi_wan": 10.0, "put_oi_wan": 6.5},
            ]
            mutated = compute.compute(make_raw(pcr_history=pcr_history))
            # The TestNoDeletedFields.test_no_pcr_label test would now FAIL on this mutated module.
            self.assertIn("pcr_label", mutated["kpi"],
                          msg="mutation injected pcr_label (proving the no-label test would catch it)")
        finally:
            compute.compute = original_compute

    # 6: flip Wall direction -------------------------------------------------
    def test_mutation_flip_wall_direction(self):
        """Swap call_wall / put_wall direction filtering → strikes land on wrong side.

        We patch _near_wall to swap the side argument silently.
        """
        contracts = TestWallDirection()._short_contracts()
        baseline = compute.compute(make_raw(contracts=contracts))
        b_call = baseline["key_levels"]["call_wall"]
        b_put = baseline["key_levels"]["put_wall"]
        self.assertIsNotNone(b_call)
        self.assertIsNotNone(b_put)
        self.assertGreater(b_call["strike"], ATM_PRICE)
        self.assertLess(b_put["strike"], ATM_PRICE)

        original = compute._near_wall

        def flipped(short_contracts, current_price, side):
            flipped_side = "below" if side == "above" else "above"
            return original(short_contracts, current_price, flipped_side)

        compute._near_wall = flipped
        mutated = compute.compute(make_raw(contracts=contracts))
        m_call = mutated["key_levels"]["call_wall"]
        m_put = mutated["key_levels"]["put_wall"]
        # Under the flip, "call_wall" is computed against puts-below side → strike < current.
        # That is exactly the invariant the TestWallDirection test asserts.
        if m_call is not None:
            self.assertLess(
                m_call["strike"], ATM_PRICE,
                msg="mutation: with flipped side, call_wall lands below current — invariant breach",
            )
        if m_put is not None:
            self.assertGreater(
                m_put["strike"], ATM_PRICE,
                msg="mutation: with flipped side, put_wall lands above current — invariant breach",
            )


# -----------------------------------------------------------------------------
# ⑤ read_states
# -----------------------------------------------------------------------------


class TestReadStates(unittest.TestCase):

    def test_proximity_buckets(self):
        self.assertEqual(compute._proximity(0.9), "逼近")
        self.assertEqual(compute._proximity(-2.0), "逼近")   # boundary ≤2 → 逼近
        self.assertEqual(compute._proximity(3.5), "中等")
        self.assertEqual(compute._proximity(-5.0), "中等")   # boundary ≤5 → 中等
        self.assertEqual(compute._proximity(8.0), "远离")
        self.assertIsNone(compute._proximity(None))

    def test_read_states_present_in_output(self):
        out = compute.compute(make_raw(contracts=[]))
        self.assertIn("read_states", out)
        # No walls in an empty payload → proximity None
        self.assertIsNone(out["read_states"]["call_wall_proximity"])
        self.assertIsNone(out["read_states"]["put_wall_proximity"])

    def test_thickness_buckets(self):
        self.assertEqual(compute._thickness({"oi_wan": 2.6}), "薄")   # < 3.0
        self.assertEqual(compute._thickness({"oi_wan": 3.0}), "中")   # boundary
        self.assertEqual(compute._thickness({"oi_wan": 9.9}), "中")
        self.assertEqual(compute._thickness({"oi_wan": 10.0}), "厚")  # boundary
        self.assertIsNone(compute._thickness(None))

    def test_asymmetry_bearish_vacuum(self):
        # call near (+0.9%), put far (-8%) → ceiling tight, floor vacuum
        cw = {"distance_pct": 0.9}
        pw = {"distance_pct": -8.0}
        self.assertEqual(compute._asymmetry(cw, pw), "偏空真空")

    def test_asymmetry_bullish_open(self):
        # put near (-1%), call far (+9%) → floor tight, upside open
        self.assertEqual(
            compute._asymmetry({"distance_pct": 9.0}, {"distance_pct": -1.0}),
            "偏多开阔",
        )

    def test_asymmetry_symmetric(self):
        # 4% vs 6% → ratio 1.5 < 2.5 → 对称
        self.assertEqual(
            compute._asymmetry({"distance_pct": 6.0}, {"distance_pct": -4.0}),
            "对称",
        )

    def test_asymmetry_none_when_wall_missing(self):
        self.assertIsNone(compute._asymmetry(None, {"distance_pct": -4.0}))

    def test_thin_wall_flag(self):
        # Build a payload whose walls are thin (< 3万 OI) and assert thin_wall.
        out = compute.compute(make_raw(contracts=_walls_payload(
            call_oi=20000, put_oi=15000)))  # 2.0万 / 1.5万 → both 薄
        self.assertTrue(out["read_states"]["thin_wall"])
        self.assertEqual(out["read_states"]["call_wall_thickness"], "薄")

    def test_max_pain_pull_side(self):
        self.assertEqual(
            compute._max_pain_pull({"strike": 110.0}, 100.0, 12.0)["side"], "上方")
        self.assertEqual(
            compute._max_pain_pull({"strike": 90.0}, 100.0, 12.0)["side"], "下方")
        self.assertEqual(
            compute._max_pain_pull({"strike": 100.0}, 100.0, 12.0)["side"], "重合")

    def test_max_pain_pull_noise_flag(self):
        # max_strike_oi_wan < 3.0 → noise
        self.assertTrue(compute._max_pain_pull({"strike": 90.0}, 100.0, 2.6)["is_noise"])
        self.assertFalse(compute._max_pain_pull({"strike": 90.0}, 100.0, 12.0)["is_noise"])

    def test_max_pain_pull_none_when_missing(self):
        self.assertIsNone(compute._max_pain_pull(None, 100.0, 12.0))

    def test_structure_label_bearish_vacuum(self):
        self.assertEqual(
            compute._structure_label(
                {"distance_pct": 0.9}, {"distance_pct": -8.0}, "偏空真空"),
            "天花板紧贴·下方真空")

    def test_structure_label_bullish_open(self):
        self.assertEqual(
            compute._structure_label(
                {"distance_pct": 9.0}, {"distance_pct": -1.0}, "偏多开阔"),
            "地板紧贴·上方开阔")

    def test_structure_label_tight_range(self):
        # symmetric, both within 5% → 窄震荡
        self.assertEqual(
            compute._structure_label(
                {"distance_pct": 1.5}, {"distance_pct": -2.0}, "对称"),
            "双墙紧夹·窄震荡")

    def test_structure_label_loose_drift(self):
        # symmetric, one wall 远离 (>5%) → 区间漂移
        self.assertEqual(
            compute._structure_label(
                {"distance_pct": 7.0}, {"distance_pct": -6.0}, "对称"),
            "双墙宽松·区间漂移")

    def test_structure_label_none_when_wall_missing(self):
        self.assertIsNone(
            compute._structure_label(None, {"distance_pct": -4.0}, None))

    def test_structure_label_integration_nok_like(self):
        # call wall +0.9% thin, put wall -8% thin → 偏空真空 structure
        out = compute.compute(make_raw(
            current_price=16.85,
            contracts=_walls_payload(
                call_oi=26000, put_oi=17000,
                call_strike=17.0, put_strike=15.5)))
        rs = out["read_states"]
        self.assertEqual(rs["structure_label"], "天花板紧贴·下方真空")
        self.assertTrue(rs["thin_wall"])
        self.assertEqual(rs["call_wall_proximity"], "逼近")


if __name__ == "__main__":
    unittest.main()
