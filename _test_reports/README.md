# Test reports — baseline snapshots

本目录保留 option-flow skill 在不同标的 / 不同降级路径下的渲染基线，用于回归测试和文档示例。

## Snapshot date

**2026-05-22**（市场数据 as-of 日）—— 所有 12 份报告基于同一天的真实数据 + 构造测试 payload 渲染。

## 文件清单

### ✅ 完整 5 段报告（10 标的）

| 文件 | 标的特征 | iv_peak | low_liquidity |
|---|---|---|---|
| `AAPL_report.md` | 中性偏多，贴 Call Wall | None | false |
| `AMZN_report.md` | 温和偏多但 IV 偏贵（+6.0pp） | None | false |
| `IWM_report.md` | 小盘 ETF，三层深度支撑厚 | None | false |
| `MSFT_report.md` | 中性偏多，期权偏便宜（-4.0pp） | None | false |
| `NVDA_report.md` | PCR 30 日新低 + 上方三层深度阻力 | None | false |
| `QQQ_report.md` | 偏紧，避险盘加重（PCR 30 日高位） | None | false |
| `SPY_report.md` | 中性偏紧，三层深度支撑 | None | false |
| `TSLA_report.md` | Wall 区间宽 ±5-6% | None | false |

### 🚫 拒绝路径报告（2 标的）

| 文件 | 标的特征 | 触发原因 |
|---|---|---|
| `META_report.md` | 高股价 OI 分散 | 单 strike max OI 仅 0.5 万张（全 chain 12.2 万张充足但 68 strike 分散）|
| `XLK_report.md` | 真冷门 ETF | chain OI 仅 0.4 万张 |

### ⚙️ 构造测试报告（2 份）

基于 AAPL 真实数据 + 字段构造，用于测试降级路径：

| 文件 | 构造 fixture | 触发降级 |
|---|---|---|
| `AAPL_pcr_lag3_report.md` | `_test_payloads/AAPL_pcr_lag3_payload.json` | PCR 滞后 ℹ️ banner |
| `AAPL_no_callwall_report.md` | `_test_payloads/AAPL_no_callwall_payload.json` | Wall 缺失 §1/§2/§3/§5 对称处理 |

## 用途

- **回归测试**：改 SKILL.md / compute.py 后，让 sub-agent 渲染对比这些 baseline 找差异
- **文档示例**：SKILL.md 正例段已涵盖核心 case，本目录提供更宽覆盖
- **教学**：散户视角看 skill 在不同标的下的真实输出

## 刷新策略

baseline 半年内有效（broker 数据 / 算法 / SKILL.md 都会演进）。半年后由维护者重新跑全集替换。

未来回归测试可加日期目录隔离：`_test_reports/2026-05-22/` vs `_test_reports/2026-11-22/`，避免新旧覆盖。
