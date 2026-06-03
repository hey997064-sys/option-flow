"""Tests for option_flow.py — the zero-write production entry (fetch→compute→stdout)."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))

import option_flow  # noqa: E402

from fetch import CLIError, NoOptionsError  # noqa: E402


class TestOptionFlowEntry(unittest.TestCase):
    @patch("option_flow.compute")
    @patch("option_flow.fetch")
    def test_prints_ai_payload_json_to_stdout(self, mock_fetch, mock_compute):
        mock_fetch.return_value = {"symbol": "NVDA", "current_price": 100.0}
        mock_compute.return_value = {"symbol": "NVDA", "kpi": {"pcr_oi": 0.79}}
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = option_flow.main(["option_flow.py", "nvda.us"])
        self.assertEqual(rc, 0)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed, {"symbol": "NVDA", "kpi": {"pcr_oi": 0.79}})
        # symbol 被 upper-case 后传给 fetch
        mock_fetch.assert_called_once_with("NVDA.US")

    @patch("option_flow.compute")
    @patch("option_flow.fetch")
    def test_writes_no_files(self, mock_fetch, mock_compute):
        mock_fetch.return_value = {"symbol": "NVDA"}
        mock_compute.return_value = {"symbol": "NVDA"}
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        before = set(os.listdir(tmp))
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            with redirect_stdout(io.StringIO()):
                option_flow.main(["option_flow.py", "NVDA.US"])
        finally:
            os.chdir(cwd)
        after = set(os.listdir(tmp))
        self.assertEqual(before, after, "option_flow 不应写任何文件")

    @patch("option_flow.fetch", side_effect=NoOptionsError("no chain"))
    def test_no_options_returns_3(self, _mock_fetch):
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = option_flow.main(["option_flow.py", "XYZ.US"])
        self.assertEqual(rc, 3)
        self.assertIn("NoOptionsError", buf.getvalue())

    @patch("option_flow.fetch", side_effect=CLIError("cli boom"))
    def test_cli_error_returns_4(self, _mock_fetch):
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = option_flow.main(["option_flow.py", "NVDA.US"])
        self.assertEqual(rc, 4)
        self.assertIn("CLIError", buf.getvalue())

    def test_missing_arg_returns_2(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = option_flow.main(["option_flow.py"])
        self.assertEqual(rc, 2)
        self.assertIn("usage", buf.getvalue())


class TestOptionFlowMutations(unittest.TestCase):
    """构造违例：若 option_flow 落盘，零落盘检测逻辑必须能抓到。"""

    @patch("option_flow.compute")
    @patch("option_flow.fetch")
    def test_zero_write_assertion_catches_a_writer(self, mock_fetch, mock_compute):
        mock_fetch.return_value = {"symbol": "NVDA"}
        mock_compute.return_value = {"symbol": "NVDA"}
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        before = set(os.listdir(tmp))
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            with redirect_stdout(io.StringIO()):
                option_flow.main(["option_flow.py", "NVDA.US"])
                # 变异：模拟某次回归引入落盘
                Path("leaked_payload.json").write_text("{}")
        finally:
            os.chdir(cwd)
        after = set(os.listdir(tmp))
        # 断言「检测逻辑有效」：注入写文件后 before != after
        self.assertNotEqual(before, after,
                            "若这条相等，说明零落盘检测逻辑本身失效")


if __name__ == "__main__":
    unittest.main()
