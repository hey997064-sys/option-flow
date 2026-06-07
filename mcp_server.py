#!/usr/bin/env python3
"""option-flow MCP server — 让任意 MCP 客户端（Claude Desktop / Cursor / 其他 AI）
都能用美股期权聪明钱画像，不再局限于 Claude Code 插件。

分层（沿用现有设计，不重写数据层）：
    fetch.py + compute.py   →  ai_payload（数据层，AI 中立，纯 longbridge CLI 依赖）
    本文件                   →  MCP 包装层（tool 出数据 + prompt 出渲染说明）

暴露两个 MCP 原语：
    tool   option_flow(symbol)     → 跑 fetch→compute，返回 ai_payload JSON（唯一数据源）
    prompt option_flow_report      → 把 SKILL.md 渲染说明带给调用方 LLM，使其知道如何渲染

运行（stdio，本地，用调用方自己的 longbridge 账号）：
    .venv/bin/python mcp_server.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# PATH 兜底：macOS GUI 进程（从 Finder/Dock 启动的 Claude Desktop 等）默认 PATH
# 只有 /usr/bin:/bin:/usr/sbin:/sbin，不含 Homebrew 的 /opt/homebrew/bin 或
# /usr/local/bin —— longbridge CLI 通常装在那。不补的话 fetch 子进程会
# FileNotFoundError。这是不可控启动环境的防御（终端启动时这些目录本就在 PATH，
# 重复加无副作用）。
_EXTRA_BIN = ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".cargo/bin")]
_path_parts = os.environ.get("PATH", "").split(os.pathsep)
for _p in _EXTRA_BIN:
    if _p not in _path_parts and Path(_p).is_dir():
        _path_parts.append(_p)
os.environ["PATH"] = os.pathsep.join(_path_parts)

from mcp.server.fastmcp import FastMCP

from fetch import CLIError, NoOptionsError, fetch
from compute import compute

mcp = FastMCP("option-flow")

# SKILL.md = 渲染说明书（提示层）。MCP prompt 把它原样带给调用方 LLM。
_SKILL_PATH = Path(__file__).parent / "skills" / "option-flow" / "SKILL.md"


def _normalize(symbol: str) -> str:
    """裸 ticker 自动补 .US（option-flow 仅美股）。已带后缀则原样大写。"""
    s = symbol.strip().upper()
    return s if "." in s else f"{s}.US"


@mcp.tool()
def option_flow(symbol: str) -> str:
    """美股期权聪明钱画像数据。输入美股标的（如 NVDA / AAPL / TSLA / SPY），
    返回 ai_payload JSON —— 渲染报告时所有数字必须且只能来自这份 JSON。

    渲染规则见 prompt `option_flow_report`（5 段中文报告：方向 / 主线 / 数字依据）。

    Args:
        symbol: 美股标的，裸 ticker（NVDA）或带后缀（NVDA.US）均可；仅支持美股。

    Returns:
        成功 → ai_payload JSON 字符串（唯一数据源）。
        失败 → 以 "ERROR:" 开头的中文说明，调用方应转述给用户、禁止硬渲染编报告。
    """
    sym = _normalize(symbol)
    try:
        raw = fetch(sym)
    except NoOptionsError:
        return f"ERROR: {sym} 当前无可用期权链（标的无活跃期权）。"
    except CLIError as e:
        return f"ERROR: 行情数据获取失败（longbridge CLI 调用出错）：{e}"
    except ValueError:
        return f"ERROR: option-flow 当前仅支持美股（.US 后缀），收到 {symbol!r}。"
    ai = compute(raw)
    return json.dumps(ai, ensure_ascii=False)


@mcp.prompt()
def option_flow_report(symbol: str = "") -> str:
    """渲染指引：先调 option_flow 工具拿 ai_payload，再按 SKILL.md 输出 5 段中文报告。

    任何 MCP 客户端可拉取本 prompt，使其 LLM 获得与 Claude Code 插件一致的渲染规则。
    """
    skill = _SKILL_PATH.read_text(encoding="utf-8")
    target = _normalize(symbol) if symbol else "<用户输入的美股标的>"
    return (
        f"请为 {target} 生成期权聪明钱画像报告。\n\n"
        f"步骤：\n"
        f"1. 调用 `option_flow` 工具，参数 symbol={target}，拿到 ai_payload JSON。\n"
        f"2. 若返回以 ERROR: 开头，直接把该错误转述给用户，不要硬渲染。\n"
        f"3. 否则严格按下方 SKILL.md 的规则渲染——所有数字只能来自 ai_payload。\n\n"
        f"--- 以下为渲染规则（SKILL.md） ---\n\n{skill}"
    )


if __name__ == "__main__":
    import sys

    # 自检：`python mcp_server.py --check` 把隐性前置依赖逐项暴露，
    # 不进 MCP 事件循环。用户接入前 / 换账号后跑一次即可。
    if "--check" in sys.argv:
        from doctor import run as _run_doctor
        sys.exit(_run_doctor())

    mcp.run()
