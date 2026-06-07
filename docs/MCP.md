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
3. **美股期权行情权限**（关键）。本工具靠 `option quote` 拉每个合约的 OI / IV，这一步需要
   账号开通美股期权行情权限（LV1/LV2）。**没有权限时 `option quote` 会返回空 `[]`（请求成功、
   但无数据），不是报错**——这正是最常见的「抓不了」根因，与 OCC 符号格式无关。
   接入前自查：

   ```bash
   # chain 能返、quote 也能返带 open_interest 的数据 → 权限 OK
   longbridge option chain NVDA.US --date <最近一个到期日> --format json   # 应返回合约
   longbridge option quote NVDA260612C140000.US --format json              # 应含 open_interest
   ```

   若 chain 有数据、quote 返回 `[]` → 账号缺期权行情权限，去 longbridge 开通，与本工具无关。
   （工具侧已对此场景给出明确中文报错，不会再被误判成格式问题。）

## 安装（一键）

clone / 解压后，在仓库目录跑：

```bash
./install.sh
```

它会：建 venv → 装依赖 → 跑自检 → **按本机实际位置算出绝对路径，自动把 option-flow 写进
Claude Desktop 配置**（备份原文件、保留你已有的其它 server）。脚本无任何硬编码个人路径，
换电脑 / 换用户名照常工作。装完**重启 Claude Desktop** 即可。

> 写非默认位置的配置：`OPTION_FLOW_CONFIG_OVERRIDE=/path/to/config.json ./install.sh`
> 非 Claude Desktop 客户端（Cursor 等）见下方「客户端接入」手动填。

## 验证能跑（自检）

`install.sh` 末尾已自动跑一次。接入后、或每次换 longbridge 账号后，可随时手动复跑：

```bash
.venv/bin/python mcp_server.py --check
```

全绿示例：

```
✅ longbridge 可执行：/opt/homebrew/bin/longbridge
✅ 行情可取 (option chain)：NVDA.US 返回 27 个到期日
✅ OI 报价 (option quote)：NVDA260608C242500.US OI=545
结论：✅ 一切就绪，可直接使用。
```

最后一项 `option quote` 是三岔判定，直接告诉你卡在哪：

| 输出 | 含义 | 怎么办 |
|---|---|---|
| `✅ ... OI=…` | 权限齐全 | 直接用 |
| `❌ 请求成功但返回空 []` | 账号缺期权行情权限（LV1/LV2），**与符号格式无关** | 去 longbridge 开通期权行情权限 |
| `❌ 返回数据但缺 open_interest` | broker 改了字段名（字段问题，非权限） | 更新 fetch.py 字段映射 |

> 想确认"是没权限还是没找到字段"，用一个**全新、未开期权权限的账号**跑这条 `--check`，输出即定论。

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
