# option-flow 插件化改造 · 设计 spec

- 日期：2026-06-03
- 方案：管道直连重包（brainstorming 选定方案 2）
- 交付边界：**第 1 层**——把仓改造成自包含、路径可移植的标准 Claude Code 插件。**不含**注册进 longbridge-plugins marketplace（第 2 层，用户后做）。

---

## 1. 背景与需求（产品层）

option-flow 现为「个人软链 skill」：靠 `~/.claude/skills/option-flow` 软链指向本地 git 仓才能调用，别人无法安装。

**需求**：改造成可被他人一键安装的标准插件，融入 longbridge CLI 生态（受众 = longbridge 用户，故 longbridge CLI + 凭证视为既有前提，不在本次处理）。

**成功标准**：
1. 装好后**任意工作目录** `/option-flow <SYMBOL.US>` 可跑。
2. **无硬编码个人路径**（不出现 `/Users/a/...`）。
3. **不往插件目录写文件**（插件目录是 ephemeral 只读资产）。
4. 现有 **46 个测试保持绿**。
5. 新增「零落盘」不变量被测试守护。

**非目标（YAGNI）**：marketplace 注册、`/option-flow` 独立 command 双入口、`${CLAUDE_PLUGIN_DATA}` 持久缓存（本工具每次都要最新行情，缓存无价值）。

---

## 2. 关键技术事实（已核实，载荷性）

- `${CLAUDE_PLUGIN_ROOT}` **存在**，官方文档明确「substituted inline in **skill content**」——即 SKILL.md 里可用。用法 `cd "${CLAUDE_PLUGIN_ROOT}" && ...`。
- 该目录 **ephemeral、不可写入做状态**；旧版本约 7 天后清理。→ 现有 `run.py` 往 `_dev_payloads/` 落盘的做法在插件形态下不成立，必须改。
- 已知风险（issue #9354）：`${CLAUDE_PLUGIN_ROOT}` 在纯 command markdown 里历史上有过不替换。SKILL.md 属 skill content，文档说支持，但**实现首步实测验证**（见 §6）。
- 标准布局：skill 放 `skills/<name>/SKILL.md`；`.claude-plugin/plugin.json` 必填 name/version/description。

来源：code.claude.com/docs/en/plugins-reference、anthropics/claude-code issues #9354 / #15642。

---

## 3. 目标布局

```
option-flow/                      ← 插件根 = git 仓根（${CLAUDE_PLUGIN_ROOT} 指向这里）
├── .claude-plugin/
│   └── plugin.json               ← 新增
├── skills/
│   └── option-flow/              ← 从 .claude/skills/option-flow/ 整体迁移
│       ├── SKILL.md
│       └── references/
├── fetch.py                      ← 留根（不动 import 路径，保测试）
├── compute.py                    ← 留根
├── option_flow.py                ← 新增：生产入口
├── run.py                        ← 保留（dev 调试，插件运行期不调用）
├── tests/  README.md  CLAUDE.md  LICENSE
```

**取舍**：Python 脚本留插件根而非塞进 `skills/.../scripts/`，因 46 测试依赖现有 import 路径，移动会连带改测试 → churn 最小。

---

## 4. 组件

### 4.1 `plugin.json`（新增）
```json
{
  "name": "option-flow",
  "version": "1.0.0",
  "description": "美股期权聪明钱画像 — 输入 US 标的，输出 5 段中文报告（方向/主线/数字依据）。Powered by Longbridge CLI.",
  "author": { "name": "Longbridge", "email": "yirun.yang@longbridge-inc.com" }
}
```

### 4.2 `option_flow.py`（新增 · 生产入口）
职责：`fetch(symbol) → compute(raw) → 把 ai_payload JSON 打到 stdout`，**零落盘**。
- 复用 `run.py` 的异常 → 退出码契约（见 §5）。
- 不 import run.py（run.py 含落盘逻辑）；直接 `from fetch import fetch, ...` + `from compute import compute`。

### 4.3 `SKILL.md` 执行管线段（重写）
回退本会话临时加的「软链版」段落，改为：
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/option_flow.py" <SYMBOL.US>
# stdout = ai_payload JSON（唯一数据源）；非零退出码见 §5
```
（spike 失败时退到 §6 的 fallback 解析写法。）

---

## 5. 数据流与错误处理

**数据流**：skill 触发 → `python3 "${CLAUDE_PLUGIN_ROOT}/option_flow.py" NVDA.US` → stdout = ai_payload JSON（不落任何文件）→ LLM 读 stdout 按 SKILL.md 渲染 5 段。

**错误处理**（沿用现契约，不新增分支）：

| 情况 | 退出码 | stderr | SKILL.md 指令 |
|---|---|---|---|
| 正常 | 0 | — | 读 stdout 渲染 |
| 无期权链 NoOptionsError | 3 | 原因 | 告知「该标的无期权链」，不渲染 |
| CLI/数据错 CLIError | 4 | 原因 | 告知数据获取失败，不渲染 |
| 参数缺失 | 2 | usage | 提示需要 `<SYMBOL.US>` |

SKILL.md 加硬约束：**非零退出码 → 转述 stderr 给用户，禁止硬渲染**（防 LLM 拿空数据编报告）。

---

## 6. 风险闸门（go/no-go · 实现首步）

`${CLAUDE_PLUGIN_ROOT}` 在 SKILL.md 是否替换 = 方案成立前提。

- **Spike**：最小 stub skill 内 `echo "ROOT=${CLAUDE_PLUGIN_ROOT}"`，本地装成插件实跑。
- 替换 → 方案 2 原样落地。
- 不替换 → **fallback**：SKILL.md 用 `python3 -c` 自解析插件根（锚定 skills 目录相对位置，**不硬编码个人路径**），不依赖软链。

闸门不过不写后续。（原则 2：先稳定再优化）

---

## 7. 防 bug 策略（消除而非掩盖 · 对齐 harness 模式 B/D/E/H/K）

1. **闸门**：§6 spike 先行。
2. **TDD**：先写 `option_flow.py` 的失败测试再实现。
3. **回归**：46 现有测试保持绿。
4. **新不变量**：`option_flow.py` 入口测试——mock fetch → 断言 ① stdout 为合法 ai_payload JSON ② **全程不写任何文件**（零落盘不变量）。
5. **Mutation test**：破坏「零落盘」与「stdout 合法」各造一个反例，确认测试真能抓。
6. **真实数据端到端**：跑 ≥1 个边界标的（低流动性/无期权链），确认错误路径正确。
7. **双盲 sub-agent**（模式 K）：独立 agent 全新装插件、无主对话上下文渲染一份报告，揪可移植性 + prompt 歧义 bug。

---

## 8. 迁移与清理（防模式 I 死代码反噬）

- 撤 `~/.claude/skills/option-flow` 软链（被插件安装取代）。
- 回退本会话加进 SKILL.md 的软链版「执行管线」段 → 换 §4.3 版本。
- dev 调试：本地 marketplace 指向仓库 `/plugin install`，保真插件形态。
- `run.py` 保留但标注「dev-only」，避免误以为是运行期入口。

---

## 9. 验收清单

- [ ] §6 spike 通过（或 fallback 落地）
- [ ] plugin.json 合法
- [ ] skill 迁移到 skills/，references 跟随
- [ ] option_flow.py 零落盘 + stdout 正确（TDD + mutation）
- [ ] 46 + 新增测试全绿
- [ ] 真实标的端到端（正常 + 错误路径）
- [ ] 双盲 sub-agent 验证无可移植性 bug
- [ ] 软链撤除 + SKILL.md 段回退
