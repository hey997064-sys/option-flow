"""Unit tests for fetch.py.

Run from repo root:
    python3 -m unittest discover tests -v

fetch.py shells out to ``longbridge`` via _run_cli, so all CLI-dependent paths
are mocked. Pure-function helpers (_compute_is_intraday, _make_occ,
_bucket_for_dte, _ticker_of / _market_of) are exercised directly.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))

import fetch  # noqa: E402


ET = ZoneInfo("America/New_York")


# -----------------------------------------------------------------------------
# ① is_intraday helper
# -----------------------------------------------------------------------------

class TestIsIntradayHelper(unittest.TestCase):
    """7 boundary cases for ``_compute_is_intraday``.

    Uses the ``now=`` optional override built into the function — no
    monkey-patching needed.
    """

    def test_weekday_during_session(self):
        # Wed 2026-05-20 12:00 ET
        now = datetime(2026, 5, 20, 12, 0, tzinfo=ET)
        self.assertTrue(fetch._compute_is_intraday(now))

    def test_pre_market(self):
        # Wed 2026-05-20 08:00 ET
        now = datetime(2026, 5, 20, 8, 0, tzinfo=ET)
        self.assertFalse(fetch._compute_is_intraday(now))

    def test_after_close(self):
        # Wed 2026-05-20 17:00 ET
        now = datetime(2026, 5, 20, 17, 0, tzinfo=ET)
        self.assertFalse(fetch._compute_is_intraday(now))

    def test_saturday(self):
        # Sat 2026-05-23 12:00 ET — weekend, not intraday.
        now = datetime(2026, 5, 23, 12, 0, tzinfo=ET)
        self.assertFalse(fetch._compute_is_intraday(now))

    def test_sunday(self):
        # Sun 2026-05-24 12:00 ET
        now = datetime(2026, 5, 24, 12, 0, tzinfo=ET)
        self.assertFalse(fetch._compute_is_intraday(now))

    def test_session_open_inclusive_09_30(self):
        # Wed 2026-05-20 09:30:00 ET — inclusive boundary (market_open <= now).
        now = datetime(2026, 5, 20, 9, 30, 0, tzinfo=ET)
        self.assertTrue(fetch._compute_is_intraday(now))

    def test_session_close_exclusive_16_00(self):
        # Wed 2026-05-20 16:00:00 ET — exclusive boundary (now < market_close).
        now = datetime(2026, 5, 20, 16, 0, 0, tzinfo=ET)
        self.assertFalse(fetch._compute_is_intraday(now))


# -----------------------------------------------------------------------------
# ② OCC encoding
# -----------------------------------------------------------------------------

class TestOCCEncoding(unittest.TestCase):

    def test_make_occ_nvda_220(self):
        """NVDA + US + 2026-05-22 + call + 220.0 → 'NVDA260522C220000.US'."""
        occ = fetch._make_occ("NVDA", "US", "2026-05-22", "call", 220.0)
        self.assertEqual(occ, "NVDA260522C220000.US")

    def test_make_occ_half_dollar(self):
        """222.5 → strike encoded as 222500."""
        occ = fetch._make_occ("NVDA", "US", "2026-05-22", "call", 222.5)
        self.assertEqual(occ, "NVDA260522C222500.US")

    def test_make_occ_put(self):
        """type='put' → 'P' in OCC encoding."""
        occ = fetch._make_occ("AAPL", "US", "2026-06-19", "put", 175.0)
        self.assertEqual(occ, "AAPL260619P175000.US")

    def test_make_occ_rejects_sub_dollar_strike(self):
        """strike=0.0005 rounds to 0 milli-dollars → CLIError."""
        with self.assertRaises(fetch.CLIError):
            fetch._make_occ("XYZ", "US", "2026-05-22", "call", 0.0005)


# -----------------------------------------------------------------------------
# ③ _bucket_for_dte
# -----------------------------------------------------------------------------

class TestBucketForDte(unittest.TestCase):
    """Verifies that DTE falls into the canonical 3 windows (or None in gaps)."""

    cases = [
        (0, "short"),
        (14, "short"),
        (15, None),    # gap between short (≤14) and mid (30-60)
        (30, "mid"),
        (60, "mid"),
        (61, None),    # gap between mid (≤60) and long (≥90)
        (90, "long"),
        (180, "long"),
        (181, None),
    ]

    def test_bucket_for_dte(self):
        for dte, expected in self.cases:
            with self.subTest(dte=dte):
                self.assertEqual(fetch._bucket_for_dte(dte), expected)


# -----------------------------------------------------------------------------
# ④ Symbol guard
# -----------------------------------------------------------------------------

class TestSymbolGuard(unittest.TestCase):
    """fetch(symbol) must reject non-US markets immediately (no CLI calls)."""

    def test_fetch_rejects_hk(self):
        with patch.object(fetch, "_run_cli") as mock_cli:
            with self.assertRaises(ValueError):
                fetch.fetch("700.HK")
            mock_cli.assert_not_called()

    def test_fetch_rejects_sh(self):
        with patch.object(fetch, "_run_cli") as mock_cli:
            with self.assertRaises(ValueError):
                fetch.fetch("600519.SH")
            mock_cli.assert_not_called()


# -----------------------------------------------------------------------------
# ⑤ Entitlement-gated empty quote  (root-cause of "抓不了" misdiagnosis)
# -----------------------------------------------------------------------------

class TestEmptyQuoteEntitlement(unittest.TestCase):
    """When the account lacks US-options quote entitlement, ``option quote``
    returns ``[]`` (request SUCCEEDS, broker just returns no rows) for every
    contract. The chain steps still work, so we have OCC symbols to query.

    fetch() must raise a CLIError whose message NAMES the entitlement
    hypothesis — otherwise a downstream AI re-misdiagnoses it as an OCC-format
    / code bug (the original ClawBot failure). The discriminating signature is
    ``contracts == 0 and failed_chunks == 0``: every quote request succeeded
    but returned nothing.
    """

    def _fake_cli(self, expiry_iso):
        def side_effect(args, **kwargs):
            # step 1: chain (no --date) → expiry list
            if args[:2] == ["option", "chain"] and "--date" not in args:
                return [{"expiry_date": expiry_iso}]
            # step 2a: kline → current_price + closes (old dates, never "today")
            if args[0] == "kline":
                return [
                    {"time": "2026-05-01 00:00:00", "close": "200.0"},
                    {"time": "2026-05-02 00:00:00", "close": "200.0"},
                ]
            # step 2b: option volume daily → pcr (unused, guard fires first)
            if args[:3] == ["option", "volume", "daily"]:
                return {"stats": []}
            # step 4: chain --date → strikes near ATM (price=200, ±15% window)
            if args[:2] == ["option", "chain"] and "--date" in args:
                return [{"strike": str(s)} for s in (190, 195, 200, 205, 210)]
            # step 6: quote → [] = entitlement gate (success, no rows)
            if args[:2] == ["option", "quote"]:
                return []
            raise AssertionError(f"unexpected CLI args: {args}")
        return side_effect

    def test_empty_quote_raises_entitlement_hint(self):
        # Expiry 5 calendar days out → always inside the short (0-14d) window,
        # independent of the wall clock when the test runs.
        et_today = datetime.now(ET).date()
        expiry_iso = (et_today + timedelta(days=5)).isoformat()

        with patch.object(fetch, "_run_cli", side_effect=self._fake_cli(expiry_iso)):
            with self.assertRaises(fetch.CLIError) as cm:
                fetch.fetch("NVDA.US")

        msg = str(cm.exception)
        # Must point at the real cause (options market-data entitlement)...
        self.assertIn("权限", msg)
        # ...and explicitly absolve the OCC format, so no AI re-walks the
        # ClawBot path of "fix" the encoding.
        self.assertIn("格式", msg)


if __name__ == "__main__":
    unittest.main()
