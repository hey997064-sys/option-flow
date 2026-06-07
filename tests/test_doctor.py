"""Unit tests for doctor.py — the `--check` self-diagnostic.

doctor.diagnose(probe) walks the hidden-precondition chain between
"downloaded + longbridge logged in" and "produces a report", emitting one
CheckResult per link. CLI access is injected via ``probe(args)->(rc,out,err)``
so every branch is testable without touching the real broker.

The high-value behaviour: the `option quote` step is a THREE-way verdict that
answers the exact question "是没权限还是没找到字段" —
  - rows with open_interest        → ok
  - rows == []  (success, no data) → fail, names options-data entitlement
  - rows without open_interest     → fail, names a field/shape change
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))

import doctor  # noqa: E402


def _make_probe(quote_rows, *, chain_ok=True):
    """Build a fake probe. quote_rows = what `option quote` returns (parsed)."""
    exp = (date.today() + timedelta(days=5)).isoformat()

    def probe(args):
        if args[:2] == ["option", "chain"] and "--date" not in args:
            if not chain_ok:
                return (1, "", "not logged in: please run auth login")
            return (0, json.dumps([{"expiry_date": exp}]), "")
        if args[:2] == ["option", "chain"] and "--date" in args:
            return (0, json.dumps([{"strike": s} for s in ("190", "200", "210")]), "")
        if args[:2] == ["option", "quote"]:
            return (0, json.dumps(quote_rows), "")
        return (1, "", f"unexpected: {args}")

    return probe


def _by_name(results, needle):
    return next(r for r in results if needle in r.name)


class TestDoctor(unittest.TestCase):

    def test_binary_missing_fails_fast(self):
        with patch("doctor.shutil.which", return_value=None):
            results = doctor.diagnose(probe=_make_probe([]))
        self.assertEqual(results[0].status, "fail")
        self.assertIn("longbridge", results[0].name)

    def test_chain_auth_failure_points_at_login(self):
        with patch("doctor.shutil.which", return_value="/fake/longbridge"):
            results = doctor.diagnose(probe=_make_probe([], chain_ok=False))
        chain = _by_name(results, "chain")
        self.assertEqual(chain.status, "fail")
        self.assertIn("login", chain.fix.lower())

    def test_quote_empty_is_entitlement_not_format(self):
        with patch("doctor.shutil.which", return_value="/fake/longbridge"):
            results = doctor.diagnose(probe=_make_probe([]))  # quote → []
        quote = _by_name(results, "quote")
        self.assertEqual(quote.status, "fail")
        self.assertIn("权限", quote.fix)        # names entitlement
        self.assertIn("格式", quote.fix)        # absolves OCC format

    def test_quote_missing_oi_field_is_field_problem(self):
        rows = [{"symbol": "NVDA...C200000.US", "implied_volatility": "1.8", "volume": 0}]
        with patch("doctor.shutil.which", return_value="/fake/longbridge"):
            results = doctor.diagnose(probe=_make_probe(rows))
        quote = _by_name(results, "quote")
        self.assertEqual(quote.status, "fail")
        self.assertIn("字段", quote.fix)        # names a field/shape change

    def test_all_green_when_oi_present(self):
        rows = [{"symbol": "NVDA...C200000.US", "open_interest": 15,
                 "implied_volatility": "1.8", "volume": 0}]
        with patch("doctor.shutil.which", return_value="/fake/longbridge"):
            results = doctor.diagnose(probe=_make_probe(rows))
        self.assertTrue(all(r.status == "ok" for r in results),
                        msg=[(r.name, r.status) for r in results])
        self.assertIn("15", _by_name(results, "quote").detail)


if __name__ == "__main__":
    unittest.main()
