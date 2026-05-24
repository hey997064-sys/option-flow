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
from datetime import datetime
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


if __name__ == "__main__":
    unittest.main()
