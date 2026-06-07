# option-flow MCP server — 让任意 AI 都能用

把 option-flow 从「仅 Claude Code 插件」改造成标准 **MCP server**，任何 MCP 兼容客户端
（Claude Desktop / Cursor / Cline / 其他）都能调用，输出与 Claude Code 插件一致的 5 段中文报告。

## 架构（复用现有数据层，只加一层包装）

```
fetch.py + compute.py   →  ai_payload（数据层，AI 中立，仅依赖 longbridge CLI）
mcp_server.py           →  MCP 包装层
   ├─ tool   option_flow(symbol)   → 跑 fetch→compute，返回 ai_payload JSON（唯一数据源）
   └─ prompt option_flow_report    → 把 SKILL.md 渲染规则带给调用方 LLM
```

调用方 LLM 拉 `option_flow_report` 这个 prompt → 得到渲染规则 → 调 `option_flow` 工具拿数据 → 渲染。

## 前置条件

1. Python 3.9+
2. `longbridge` CLI 已安装并登录（`longbridge auth login`）——MCP server 在**本机**用**你自己的**
   longbridge 账号拉数据，不经过任何第三方服务器。
3. 美股期权数据权限（option chain / option quote 能返回数据即可，无需额外 LV 权限）。

## 安装

```bash
cd option-flow
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 验证能跑

```bash
.venv/bin/python -c "import mcp_server as m; print(m.option_flow('700.HK'))"
# 预期：ERROR: option-flow 当前仅支持美股（.US 后缀）...   ← 证明工具链通
```

## 客户端接入

### Claude Desktop

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）：

```json
{
  "mcpServers": {
    "option-flow": {
      "command": "/绝对路径/option-flow/.venv/bin/python",
      "args": ["/绝对路径/option-flow/mcp_server.py"]
    }
  }
}
```

重启 Claude Desktop → 工具栏出现 `option_flow` 工具与 `option_flow_report` 提示。

### Cursor

`~/.cursor/mcp.json`（或项目内 `.cursor/mcp.json`）用同样的 `command` / `args` 结构。

### 其他 MCP 客户端

任何支持 stdio 传输的客户端：启动命令 = `<venv>/bin/python <repo>/mcp_server.py`。

## 用法

- 直接对 AI 说：「用 option-flow 看 NVDA」/「option flow NVDA」。
- 或显式拉 `option_flow_report` prompt（参数 symbol=NVDA），客户端会自动带上渲染规则。

## 与 Claude Code 插件的关系

两条投递路径共享同一套数据层与提示层，互不影响：

| 路径 | 入口 | 适用 |
|---|---|---|
| Claude Code 插件 | `skills/option-flow/SKILL.md` + `option_flow.py` | Claude Code 用户 |
| MCP server | `mcp_server.py` | 所有 MCP 兼容 AI |

数据正确性（算法层）和渲染规则（SKILL.md）只维护一份，两路复用。
