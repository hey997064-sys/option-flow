"""Unit tests for install_config.merge_mcp_config — the risky part of install.sh.

Writing into a user's claude_desktop_config.json must NEVER clobber their
existing servers / preferences. These tests pin that contract:
  - create when missing
  - preserve other servers + other top-level keys
  - idempotent re-run; .bak keeps the PRISTINE original
  - malformed existing JSON aborts WITHOUT touching the file
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))

import install_config  # noqa: E402

CMD = "/x/.venv/bin/python"
ARGS = ["/x/mcp_server.py"]


class TestMergeMcpConfig(unittest.TestCase):

    def test_creates_config_when_missing(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "sub" / "claude_desktop_config.json"  # parent missing too
            install_config.merge_mcp_config(str(p), CMD, ARGS)
            cfg = json.loads(p.read_text())
            self.assertEqual(cfg["mcpServers"]["option-flow"]["command"], CMD)
            self.assertEqual(cfg["mcpServers"]["option-flow"]["args"], ARGS)

    def test_preserves_other_servers_and_keys(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps({
                "mcpServers": {"other": {"command": "keepme", "args": []}},
                "globalShortcut": "Cmd+X",
            }))
            install_config.merge_mcp_config(str(p), CMD, ARGS)
            cfg = json.loads(p.read_text())
            self.assertIn("other", cfg["mcpServers"])
            self.assertEqual(cfg["mcpServers"]["other"]["command"], "keepme")
            self.assertIn("option-flow", cfg["mcpServers"])
            self.assertEqual(cfg["globalShortcut"], "Cmd+X")

    def test_idempotent_and_backup_keeps_pristine_original(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            original = json.dumps({"mcpServers": {"other": {"command": "keepme", "args": []}}})
            p.write_text(original)
            install_config.merge_mcp_config(str(p), CMD, ARGS)  # 1st
            install_config.merge_mcp_config(str(p), CMD, ARGS)  # 2nd (idempotent)
            cfg = json.loads(p.read_text())
            # option-flow present exactly once, other server still there
            self.assertEqual(set(cfg["mcpServers"]), {"other", "option-flow"})
            # .bak holds the PRISTINE original, not the modified version
            bak = Path(str(p) + ".bak")
            self.assertTrue(bak.exists())
            self.assertEqual(json.loads(bak.read_text()), json.loads(original))

    def test_malformed_json_aborts_without_clobber(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text("{ this is not json")
            with self.assertRaises(SystemExit):
                install_config.merge_mcp_config(str(p), CMD, ARGS)
            # file untouched, no .bak written
            self.assertEqual(p.read_text(), "{ this is not json")
            self.assertFalse(Path(str(p) + ".bak").exists())


if __name__ == "__main__":
    unittest.main()
