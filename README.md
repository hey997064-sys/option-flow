# Option Flow

读懂美股期权"聪明钱"画像的 Claude Code skill，面向散户。基于公开期权链聚合数据，每日 5 段中文画像（定调 / KPI / 蝴蝶图 / 波动率 / 策略推荐）。

## 什么时候用 option-flow

- 每天美股盘前 / 盘中盘后想读懂某个标的「聪明钱在哪儿押注」时用
- 输入：US 标的（如 SPY.US / NVDA.US / TSLA.US）
- 输出：5 段中文画像（今日定调 + KPI + 蝴蝶图 + 波动率 + 策略推荐）
- 适用：大盘 ETF（SPY / QQQ / IWM / DIA）、大盘股（AAPL / NVDA / MSFT / TSLA / META / AMZN）、高 beta 个股、主流 sector ETF（XLK / XLF / XLE）
- 不适用：小盘股、低成交量 ETF（如 EWJ）、微价股 < $10 —— 会触发冷门拒绝路径告诉用户不出报告
- 频率：日度

## 安装

前置：

- Python 3.8+
- [longbridge CLI](https://open.longbridge.com/) 安装好并 `longbridge auth login` 登录
- [Claude Code](https://claude.ai/code) 安装好

步骤：

```bash
git clone <repo> option-flow
cd option-flow
```

验证（在 Claude Code 里）：

```
分析下 SPY 的期权聪明钱
```

或直接 slash command：

```
/option-flow SPY.US
```

## 用法

- Slash command：`/option-flow <SYMBOL.US>` 例 `/option-flow SPY.US`
- 自然语言：「分析下 SPY 期权聪明钱」、「NVDA option flow」、「看看 QQQ 期权聪明钱在押什么」等

## 输出样例（NVDA 节选）

```markdown
# NVDA · 期权聪明钱画像
价格 / IV：2026-05-20 · PCR：2026-05-20

## §1 今日定调

NVDA 散户偏多但 IV 已含溢价。PCR **0.791** 处于 **30 日新低**，Call OI 占比偏高，散户在押大幅上涨；同时避险盘减少。IV 比 HV 高 **+5.5pp**，期权定价偏贵；5/22 单日 IV 飙至 **92.4%**，市场押注当日 **±8% 大幅波动**。现价 $223 夹在 $210 Put Wall 与 $240 Call Wall 之间。

**交易主线**：5/22 大事件前不入场（IV 偏贵 + 情绪偏热是 contrarian 警告）；事件后看突破——破 $240 看 $245-250，破 $210 看 $200-205。

## §2 KPI 仪表盘

| 指标 | 数值 | 含义 |
|---|---|---|
| PCR · OI | **0.791** | 30 日新低，Call 占优 |
| 30D ATM IV | **43.8%** | 市场紧张度偏高 |
| HV (30D) | **38.4%** | 过去 30 个交易日实际波动 |
| IV − HV | **+5.5pp** | 期权偏贵，做多波动率不便宜 |
| Max Pain | **$215** | 距现价 -3.8% |
| Call/Put Wall | **$240 / $210** | +7.4% / -6.0% |

（接 §3 ASCII 蝴蝶图、§4 波动率、§5 策略推荐——完整结构见 `.claude/skills/option-flow/SKILL.md`）
```

## 架构 3 层

| 层 | 文件 | 职责 |
|---|---|---|
| IO 边界 | `fetch.py` | 调 longbridge CLI 抓期权链（短桶 ≤14d，长桶 30-180d）、PCR、kline；纯 stdlib |
| 算法层 | `compute.py` | 算 Call/Put Wall、Max Pain、IV 期限结构、深度集群、冷门标的判定、ASCII 蝴蝶图预渲染；纯 stdlib |
| LLM 行为指令 | `.claude/skills/option-flow/SKILL.md`（+ `references/`）| 5 段输出格式 + 硬规则 + 字段速查表，约束 LLM 渲染 |

## 测试

```bash
python3 -m unittest discover tests -v
```

46 tests 覆盖：算法不变量（Wall 方向、Max Pain 算法身份、IV 中位口径、HV 30d 严格 31 closes、PCR lag clamp）+ Mutation tests（每条不变量配反例确认 validator 真能抓 bug）+ Fetch 层 OCC 编码 / DTE 分桶 / US 市场守卫。

## 数据口径

- **OI** = ≤14 天短期所有 expiry 在该 strike 合计（不是单 expiry 快照）
- **PCR** 用 broker T+1 服务端聚合（盘前 / 12:00 ET 前可能滞后 1 天，报告头会标 ℹ️）
- **Max Pain** 用 ≤14d 合并 expiry 算（友商可能用单 expiry，结果差几个 strike 属口径差异非 bug）
- 详见 `.claude/skills/option-flow/references/`

## 局限

- 仅支持美股（`.US` 后缀）
- **目前仅支持在 Claude Code 上运行**——Cursor / ChatGPT / Claude Desktop / 其他 MCP-compatible client 暂不支持（依赖 `.claude/skills/` 目录约定，这是 Claude Code 私有）
- 流动性低的标的会拒绝出报告（这是设计——避免向散户输出可信度低的画像）
- LLM 输出仍有语义误判风险（散户当参考、不构成投资建议）
- 期权风险显著高于股票现货，请谨慎评估自身风险承受能力

## License

MIT
