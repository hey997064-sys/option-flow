#!/usr/bin/env python3
"""把 option-flow MCP server 条目合并进 MCP 客户端配置（JSON），
保留所有现有 server 与顶层键。install.sh 调用本模块。

安全契约（见 tests/test_install_config.py）：
  - 文件不存在 → 新建（含缺失的父目录）
  - 已有其它 server / 顶层键 → 全部保留，只增/改 option-flow 这一项
  - 可重复执行；首次执行把**原始文件**备份到 <path>.bak（重跑不覆盖该备份）
  - 现有文件不是合法 JSON → 立即中止，**不改动任何文件**（绝不覆盖用户配置）

CLI:  python install_config.py <config_path> <command> [arg ...]
"""
from __future__ import annotations

import json
from pathlib import Path

SERVER_NAME = "option-flow"


def merge_mcp_config(config_path: str, command: str, args, server_name: str = SERVER_NAME) -> dict:
    p = Path(config_path)
    cfg: dict = {}

    if p.exists() and p.read_text(encoding="utf-8").strip():
        original = p.read_text(encoding="utf-8")
        # 解析失败先于任何写操作 → 中止，原文件零改动。
        try:
            cfg = json.loads(original)
        except json.JSONDecodeError as e:
            raise SystemExit(
                f"现有配置不是合法 JSON，已中止（未改动任何文件）：{config_path}\n  {e}"
            )
        if not isinstance(cfg, dict):
            raise SystemExit(f"现有配置顶层不是 JSON 对象，已中止：{config_path}")
        # 仅首次备份，保留最原始版本。
        bak = Path(str(p) + ".bak")
        if not bak.exists():
            bak.write_text(original, encoding="utf-8")

    servers = cfg.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise SystemExit(f"现有配置的 mcpServers 不是对象，已中止：{config_path}")

    servers[server_name] = {"command": command, "args": list(args)}

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return cfg


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("usage: python install_config.py <config_path> <command> [arg ...]", file=sys.stderr)
        sys.exit(2)
    merge_mcp_config(sys.argv[1], sys.argv[2], sys.argv[3:])
    print(f"已写入 {sys.argv[1]}（option-flow MCP server 条目；原配置已备份到 *.bak）")
