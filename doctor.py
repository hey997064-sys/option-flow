#!/usr/bin/env python3
"""option-flow 自检 (doctor) —— 把「下载完能不能直接用」的隐性前置依赖逐项暴露。

设计哲学（全局 harness 模式 A / 原则 1）：每个隐性依赖只有两种正确处理——
启动时自动满足，或启动时显式自检 + 给一句话可执行指引。绝不让它在运行中途
以神秘报错或卡住的形式爆出来。

入口：``python mcp_server.py --check`` → 调 ``diagnose()`` 打印体检清单。

检查链（顺序即依赖顺序，前一项 fail 则后续无意义，提前返回）：
  1. longbridge 可执行（PATH 能找到）
  2. 行情可取（option chain）—— 同时覆盖「已登录」
  3. OI 报价（option quote）—— 三岔判定，回答「是没权限还是没找到字段」：
       · 有 open_interest 的数据  → ok
       · 返回空 []（请求成功无数据）→ fail：账号缺期权行情权限（与 OCC 格式无关）
       · 有数据但缺 open_interest → fail：字段/数据形态变了（这是字段问题，不是权限）

CLI 调用经注入的 ``probe(args) -> (returncode, stdout, stderr)`` 完成，便于测试；
默认 probe 走真实 subprocess。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date

from fetch import _make_occ, _market_of, _ticker_of

# 流动性高、几乎必有期权链的探针标的。
PROBE_SYMBOL = "NVDA.US"
_PROBE_TIMEOUT = 25


@dataclass
class CheckResult:
    name: str
    status: str  # 'ok' | 'fail' | 'warn'
    detail: str = ""
    fix: str = ""


def _real_probe(args: list[str]) -> tuple[int, str, str]:
    """默认 probe：真实调用 longbridge CLI，不重试、不 sleep（自检要快）。"""
    proc = subprocess.run(
        ["longbridge", *args, "--format", "json"],
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _parse(stdout: str):
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def _looks_auth(stderr: str) -> bool:
    needle = (stderr or "").lower()
    return any(k in needle for k in ("auth", "login", "token", "unauthor", "登录", "未登录"))


def diagnose(probe=None) -> list[CheckResult]:
    """跑体检链，返回 CheckResult 列表。probe 可注入以便测试。"""
    probe = probe or _real_probe
    results: list[CheckResult] = []

    # 1. longbridge 可执行 ----------------------------------------------------
    path = shutil.which("longbridge")
    if not path:
        results.append(CheckResult(
            "longbridge 可执行", "fail",
            "PATH 里找不到 longbridge",
            "安装 longbridge CLI；GUI 启动找不到见 mcp_server.py 的 PATH 兜底",
        ))
        return results  # 后续检查都依赖它，提前返回
    results.append(CheckResult("longbridge 可执行", "ok", path))

    # 2. 行情可取 (option chain) —— 同时覆盖已登录 ----------------------------
    rc, out, err = probe(["option", "chain", PROBE_SYMBOL])
    expiries = _parse(out) if rc == 0 else None
    if rc != 0 or not isinstance(expiries, list) or not expiries:
        results.append(CheckResult(
            "行情可取 (option chain)", "fail",
            (err or out or "无返回")[:200],
            "longbridge auth login（重新登录）" if _looks_auth(err)
            else "检查网络 / 账号行情权限",
        ))
        return results
    results.append(CheckResult(
        "行情可取 (option chain)", "ok",
        f"{PROBE_SYMBOL} 返回 {len(expiries)} 个到期日",
    ))

    # 3. OI 报价 (option quote) —— 三岔判定 -----------------------------------
    # 取最近一个未过期的到期日，再取 chain 中位 strike 构造一个真实 OCC。
    today = date.today()
    exp_iso = None
    for r in sorted(expiries, key=lambda r: r.get("expiry_date", "")):
        d = r.get("expiry_date")
        try:
            if d and date.fromisoformat(d) >= today:
                exp_iso = d
                break
        except (ValueError, TypeError):
            continue
    if exp_iso is None:
        exp_iso = expiries[0].get("expiry_date")

    rc, out, err = probe(["option", "chain", PROBE_SYMBOL, "--date", exp_iso])
    chain = _parse(out) or []
    strikes = [r["strike"] for r in chain if isinstance(r, dict) and "strike" in r]
    if not strikes:
        results.append(CheckResult(
            "OI 报价 (option quote)", "warn",
            f"{exp_iso} chain 未返回 strike，跳过 quote 检查",
        ))
        return results
    mid = strikes[len(strikes) // 2]
    occ = _make_occ(
        _ticker_of(PROBE_SYMBOL), _market_of(PROBE_SYMBOL),
        exp_iso, "call", float(mid),
    )

    rc, out, err = probe(["option", "quote", occ])
    rows = _parse(out)
    if rc != 0:
        results.append(CheckResult(
            "OI 报价 (option quote)", "fail",
            (err or out or "")[:200],
            "检查网络；若持续失败联系 longbridge 支持",
        ))
    elif isinstance(rows, list) and len(rows) == 0:
        results.append(CheckResult(
            "OI 报价 (option quote)", "fail",
            f"{occ} 请求成功但返回空 []",
            "账号未开通美股期权行情权限（LV1/LV2）→ 去 longbridge 开通。"
            "这是权限问题，与 OCC 符号格式无关。",
        ))
    elif isinstance(rows, list) and rows and "open_interest" not in rows[0]:
        results.append(CheckResult(
            "OI 报价 (option quote)", "fail",
            f"{occ} 返回数据但缺 open_interest，实际字段: {sorted(rows[0].keys())}",
            "数据形态变了 → 更新 fetch.py 的字段映射。这是字段问题，不是权限问题。",
        ))
    else:
        results.append(CheckResult(
            "OI 报价 (option quote)", "ok",
            f"{occ} OI={rows[0].get('open_interest')}",
        ))
    return results


# ---------------------------------------------------------------- pretty print

_ICON = {"ok": "✅", "fail": "❌", "warn": "⚠️"}


def format_report(results: list[CheckResult]) -> str:
    lines = ["option-flow 自检：", ""]
    for r in results:
        lines.append(f"{_ICON.get(r.status, '?')} {r.name}：{r.detail}")
        if r.fix:
            lines.append(f"     ↳ 修复：{r.fix}")
    lines.append("")
    if all(r.status != "fail" for r in results):
        lines.append("结论：✅ 一切就绪，可直接使用。")
    else:
        lines.append("结论：❌ 有阻断项，按上面「修复」处理后再用。")
    return "\n".join(lines)


def run() -> int:
    """跑自检并打印，返回 exit code（有 fail → 1）。"""
    results = diagnose()
    print(format_report(results))
    return 0 if all(r.status != "fail" for r in results) else 1


if __name__ == "__main__":
    import sys
    sys.exit(run())
