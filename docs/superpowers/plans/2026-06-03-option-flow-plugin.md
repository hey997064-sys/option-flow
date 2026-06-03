# option-flow 插件化改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 option-flow 从「个人软链 skill」改造成自包含、路径可移植的标准 Claude Code 插件，装好后任意目录 `/option-flow <SYMBOL.US>` 可跑、零硬编码路径、不往插件目录写文件、现有测试保绿。

**Architecture:** 新增生产入口 `option_flow.py`（fetch→compute→stdout，零落盘）取代「两步+写文件」；skill 迁到标准 `skills/` 布局；SKILL.md 用 `${CLAUDE_PLUGIN_ROOT}` 定位脚本。Python 脚本留插件根以保 import 路径与现有 46 测试不破。

**Tech Stack:** Python 3 stdlib（无第三方依赖）、unittest、Claude Code plugin（plugin.json + skills/）、Longbridge CLI（运行期外部依赖，受众既有）。

参照 spec：`docs/superpowers/specs/2026-06-03-option-flow-plugin-design.md`

---

### Task 1: 风险闸门 — 实测 `${CLAUDE_PLUGIN_ROOT}` 在 SKILL.md 是否替换（go/no-go）

**为什么先做**：整个方案靠 SKILL.md 里 `${CLAUDE_PLUGIN_ROOT}` 能展开成插件真实路径。官方文档说 skill content 支持，但 issue #9354 显示纯 markdown 有过不替换。不验证就编码 = 在不稳定地基上盖楼。

**Files:**
- Create (临时,验证后删): `/tmp/of-spike/.claude-plugin/plugin.json`
- Create (临时): `/tmp/of-spike/skills/of-spike/SKILL.md`
- Create (临时): `/tmp/of-spike-marketplace/.claude-plugin/marketplace.json`

- [ ] **Step 1: 造最小 stub 插件**

```bash
mkdir -p /tmp/of-spike/.claude-plugin /tmp/of-spike/skills/of-spike
cat > /tmp/of-spike/.claude-plugin/plugin.json <<'JSON'
{ "name": "of-spike", "version": "0.0.1", "description": "spike: verify CLAUDE_PLUGIN_ROOT substitution in skill content" }
JSON
cat > /tmp/of-spike/skills/of-spike/SKILL.md <<'MD'
---
name: of-spike
description: "spike skill — 触发词 of-spike-probe。验证插件根变量替换。"
---
# of-spike
被触发时，运行下面命令并把输出原样回报给用户：
```bash
echo "PLUGIN_ROOT=${CLAUDE_PLUGIN_ROOT}"
ls "${CLAUDE_PLUGIN_ROOT}/skills/of-spike/SKILL.md"
```
MD
```

- [ ] **Step 2: 造本地 marketplace 指向它并安装**

```bash
mkdir -p /tmp/of-spike-marketplace/.claude-plugin
cat > /tmp/of-spike-marketplace/.claude-plugin/marketplace.json <<'JSON'
{ "name": "of-spike-mp", "owner": {"name":"dev"}, "plugins": [ {"name":"of-spike","source":"/tmp/of-spike","description":"spike"} ] }
JSON
```

用户侧（让用户在对话框敲，或 dev 自行）：
```
/plugin marketplace add /tmp/of-spike-marketplace
/plugin install of-spike@of-spike-mp
/reload-plugins
```

- [ ] **Step 3: 触发 spike skill 观察替换结果**

触发 `of-spike-probe`，看 `echo` 输出：
- **PASS**：`PLUGIN_ROOT=/Users/.../.claude/plugins/cache/of-spike-mp/of-spike/0.0.1` 且 `ls` 命中 → 变量替换成立 → 后续 Task 4 用 `${CLAUDE_PLUGIN_ROOT}` 原样写。
- **FAIL**：输出 `PLUGIN_ROOT=` 为空或字面 `${CLAUDE_PLUGIN_ROOT}` → 走 fallback：Task 4 改用自解析（见 Task 4 的 Plan B）。

把结论记到 spec §6 旁边一行（PASS/FAIL + 实测路径样例）。

- [ ] **Step 4: 清理 spike**

```bash
rm -rf /tmp/of-spike /tmp/of-spike-marketplace
```
用户侧：`/plugin uninstall of-spike@of-spike-mp` + `/plugin marketplace remove of-spike-mp`。

**Gate:** 本 Task 未出 PASS/FAIL 结论前，不进入 Task 4 的最终写法选择（但 Task 2/3 可并行做，它们与变量无关）。

---

### Task 2: 新增生产入口 `option_flow.py`（TDD · 零落盘）

**Files:**
- Test: `tests/test_option_flow.py` (create)
- Create: `option_flow.py` (plugin root)

`option_flow.py` 是胶水层：fetch→compute→stdout。fetch/compute 的正确性已由现有 46 测试覆盖，故本入口的单元测试 **mock 掉 fetch 与 compute**，只验证「编排正确 + 退出码契约 + 零落盘」。

- [ ] **Step 1: 写失败测试**

`tests/test_option_flow.py`:
```python
"""Tests for option_flow.py — the zero-write production entry (fetch→compute→stdout)."""
from __future__ import annotations

import io
import json
import os
import sys
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
        tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd /Users/a/projects/option-flow && python3 -m unittest tests.test_option_flow -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'option_flow'`

- [ ] **Step 3: 写最小实现**

`option_flow.py`:
```python
#!/usr/bin/env python3
"""Production entry: fetch → compute → ai_payload JSON to stdout. ZERO disk writes.

Plugin-runtime entrypoint. Invoked by the option-flow skill as:
    python3 "${CLAUDE_PLUGIN_ROOT}/option_flow.py" <SYMBOL.US>

For local dev with on-disk payload inspection, use run.py instead (dev-only).
"""
from __future__ import annotations

import json
import sys

from fetch import CLIError, NoOptionsError, fetch
from compute import compute


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python3 option_flow.py <SYMBOL.US>   (e.g. NVDA.US)", file=sys.stderr)
        return 2
    symbol = argv[1].upper()
    try:
        raw = fetch(symbol)
    except NoOptionsError as e:
        print(f"NoOptionsError: {e}", file=sys.stderr)
        return 3
    except CLIError as e:
        print(f"CLIError: {e}", file=sys.stderr)
        return 4
    ai = compute(raw)
    print(json.dumps(ai, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python3 -m unittest tests.test_option_flow -v`
Expected: PASS（5 tests）

- [ ] **Step 5: 跑全量回归确认 46 测试仍绿**

Run: `python3 -m unittest discover tests -v`
Expected: 原 46 + 新 5 = 51 全 PASS

- [ ] **Step 6: 提交**

```bash
git add option_flow.py tests/test_option_flow.py
git commit -m "feat: add option_flow.py zero-write production entry (fetch→compute→stdout)"
```

---

### Task 3: Mutation test — 证明「零落盘」断言真能抓 bug

**Files:**
- Modify: `tests/test_option_flow.py`（追加 mutation 类）

**为什么**：harness 模式 E——空断言也会绿。必须构造「写文件」的变异版，确认 `test_writes_no_files` 真能抓到。

- [ ] **Step 1: 追加 mutation 测试**

在 `tests/test_option_flow.py` 末尾（`if __name__` 之前）插入：
```python
class TestOptionFlowMutations(unittest.TestCase):
    """构造违例：若 option_flow 落盘，零落盘断言必须 FAIL。"""

    @patch("option_flow.compute")
    @patch("option_flow.fetch")
    def test_zero_write_assertion_catches_a_writer(self, mock_fetch, mock_compute):
        mock_fetch.return_value = {"symbol": "NVDA"}
        mock_compute.return_value = {"symbol": "NVDA"}
        import tempfile
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
```

- [ ] **Step 2: 运行，确认通过（即检测逻辑有效）**

Run: `python3 -m unittest tests.test_option_flow -v`
Expected: PASS（含新 mutation 用例，6 tests）

- [ ] **Step 3: 提交**

```bash
git add tests/test_option_flow.py
git commit -m "test: mutation test proving zero-write assertion is load-bearing"
```

---

### Task 4: 重构为插件布局 + 重写 SKILL.md 执行管线段

**Files:**
- Create: `.claude-plugin/plugin.json`
- Move: `.claude/skills/option-flow/` → `skills/option-flow/`（git mv，含 references/）
- Modify: `skills/option-flow/SKILL.md`（回退软链版「执行管线」段 → ${CLAUDE_PLUGIN_ROOT} 版）

- [ ] **Step 1: 创建 plugin.json**

`.claude-plugin/plugin.json`:
```json
{
  "name": "option-flow",
  "version": "1.0.0",
  "description": "美股期权聪明钱画像 — 输入 US 标的，输出 5 段中文报告（方向/主线/数字依据）。Powered by Longbridge CLI.",
  "author": { "name": "Longbridge", "email": "yirun.yang@longbridge-inc.com" }
}
```

- [ ] **Step 2: git mv skill 到标准布局**

```bash
cd /Users/a/projects/option-flow
mkdir -p skills
git mv .claude/skills/option-flow skills/option-flow
# 若 .claude/ 下已无其他内容，保留 settings.local.json 所在的 .claude/（不删）
```

- [ ] **Step 3: 重写 SKILL.md 执行管线段**

在 `skills/option-flow/SKILL.md` 找到本会话加入的「## 执行管线」段（含 `~/.claude/skills/option-flow` 软链解析的版本），**整段替换**为：

````markdown
## 执行管线（先跑这步拿到 ai_payload）

本 skill 触发后，**先生成 `ai_payload`，再按下文渲染**。生产入口零落盘，直接 stdout 输出：

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/option_flow.py" <SYMBOL.US>
```

- `<SYMBOL.US>` = 用户输入标的（如 `NVDA.US`）。
- **stdout 的 JSON 即唯一数据源**，拿到后所有数字只能来自它（见下文数据契约）。
- **非零退出码 → 把 stderr 内容转述给用户，禁止硬渲染**：
  - 退出码 3（NoOptionsError）→「该标的无可用期权链」
  - 退出码 4（CLIError）→「行情数据获取失败」
  - 退出码 2 → 提示需要 `<SYMBOL.US>` 参数
````

> **Plan B（仅当 Task 1 spike = FAIL 时改用此 bash）**：
> ```bash
> SKILL_DIR="$(python3 -c "import os,sys;print(os.path.dirname(os.path.realpath(sys.argv[1])))" "$0" 2>/dev/null)"
> # SKILL.md 无法拿到自身路径时，退到：从已知插件 skills 相对位置上跳到插件根
> PROOT="$(python3 -c "import os,glob;c=glob.glob(os.path.expanduser('~/.claude/plugins/cache/*/option-flow/*/option_flow.py'));print(os.path.dirname(sorted(c)[-1]) if c else '')")"
> python3 "$PROOT/option_flow.py" <SYMBOL.US>
> ```
> （glob 取最新版本目录，避免硬编码 marketplace 名/版本号；仅 spike 失败时启用。）

- [ ] **Step 4: 确认测试不受布局变动影响**

Run: `python3 -m unittest discover tests -v`
Expected: 51 全 PASS（脚本仍在根，import 路径未变）

- [ ] **Step 5: 提交**

```bash
git add .claude-plugin/plugin.json skills/ .claude/
git commit -m "refactor: convert to standard plugin layout; SKILL.md uses CLAUDE_PLUGIN_ROOT"
```

---

### Task 5: 迁移清理 + run.py 标注 dev-only（防死代码反噬）

**Files:**
- Modify: `run.py`（顶部 docstring 加 dev-only 标注）
- Modify: `README.md`（用法段改为插件安装 + `/option-flow`；架构表路径更新）
- 外部动作: 撤 `~/.claude/skills/option-flow` 软链

- [ ] **Step 1: run.py 标注 dev-only**

`run.py` 模块 docstring 改为（首行后追加）：
```python
"""CLI entry (DEV-ONLY): ``python3 run.py NVDA.US`` → prints raw_payload summary + writes
``_dev_payloads/<TICKER>_raw_payload.json`` for local inspection.

NOT the plugin runtime entry — production uses option_flow.py (zero-write, stdout ai_payload).
Kept for local debugging of the fetch layer.
"""
```

- [ ] **Step 2: 更新 README 用法 + 架构表**

`README.md`「## 用法」段，把第一条改为：
```markdown
- 安装：`/plugin install option-flow@<marketplace>`（注册到 marketplace 后）
- Slash command：`/option-flow <SYMBOL.US>` 例 `/option-flow SPY.US`
- 自然语言：「分析下 SPY 期权聪明钱」、「NVDA option flow」等
```
「## 架构 3 层」表 `LLM 行为指令` 行路径 `.claude/skills/option-flow/SKILL.md` → `skills/option-flow/SKILL.md`。新增一行说明生产入口 `option_flow.py`（零落盘）vs `run.py`（dev-only）。

- [ ] **Step 3: 撤个人软链**

```bash
rm -f ~/.claude/skills/option-flow
ls -la ~/.claude/skills/option-flow 2>&1 || echo "symlink removed ✓"
```

- [ ] **Step 4: 提交**

```bash
git add run.py README.md
git commit -m "docs: mark run.py dev-only; README uses plugin install + remove personal symlink"
```

---

### Task 6: 真实数据端到端 + 双盲 sub-agent 验证（消除残余 bug）

**Files:** 无（验证任务）

- [ ] **Step 1: 真实数据 — 正常路径**

```bash
cd /Users/a/projects/option-flow
python3 option_flow.py NVDA.US | python3 -c "import sys,json; d=json.load(sys.stdin); print('keys:', list(d.keys()))"
```
Expected: 退出码 0，输出含 `kpi key_levels term_structure data_quality` 等键，**无 _dev_payloads 文件被创建**（`ls _dev_payloads/NVDA_raw_payload.json` 应仍是旧的或不存在）。

- [ ] **Step 2: 真实数据 — 错误路径（边界，模式 H）**

```bash
python3 option_flow.py BRK.A.US ; echo "exit=$?"   # 或任一已知无标准期权链/格式异常标的
```
Expected: 非零退出码 + stderr 有 NoOptionsError/CLIError 文案，stdout 无半截 JSON。

- [ ] **Step 3: 双盲 sub-agent 验证（模式 K）**

派一个**无本对话上下文**的 sub-agent，仅给它插件目录，让它：
1. 用本地 marketplace 全新安装该插件（复用 Task 1 的 marketplace 写法，source 指向真实仓库路径）。
2. 触发 `/option-flow AAPL.US`，**不许参考任何已有渲染产物**，按 SKILL.md 独立渲染一份。
3. 自报「安装/调用/渲染中遇到的歧义或报错」。

验收：报告 5 段齐全、数字均来自 ai_payload、`${CLAUDE_PLUGIN_ROOT}` 路径在它机器/会话也解析正确。sub-agent 报回的歧义/报错逐条评估，真 bug 当场修（回到对应 Task）。

- [ ] **Step 4: 收尾提交（如 Step 3 触发修复）**

```bash
git add -A && git commit -m "fix: address portability/ambiguity findings from double-blind verification"
```

---

## Self-Review

**Spec 覆盖**：
- 成功标准 1（任意目录可调）→ Task 4（${CLAUDE_PLUGIN_ROOT}）+ Task 6 Step 3 ✓
- 成功标准 2（无硬编码路径）→ Task 4（变量/glob fallback）✓
- 成功标准 3（不写插件目录）→ Task 2 零落盘 + Task 3 mutation ✓
- 成功标准 4（46 测试绿）→ Task 2 Step 5、Task 4 Step 4 ✓
- 成功标准 5（零落盘不变量被守护）→ Task 2 + Task 3 ✓
- spec §6 闸门 → Task 1 ✓
- spec §8 迁移清理 → Task 5 ✓
- spec §7 防 bug（TDD/mutation/真实数据/双盲）→ Task 2/3/6 ✓

**Placeholder 扫描**：无 TBD/TODO；所有代码步均含完整代码；错误处理为具体退出码非「适当处理」。✓

**类型/签名一致性**：`option_flow.main(argv: list[str]) -> int`、`fetch(symbol)`、`compute(raw)` 全程一致；测试 patch 目标 `option_flow.fetch` / `option_flow.compute` 与实现 `from fetch import fetch` / `from compute import compute` 对应（patch 模块内引用名）✓。退出码 2/3/4 与 run.py 既有契约一致 ✓。
