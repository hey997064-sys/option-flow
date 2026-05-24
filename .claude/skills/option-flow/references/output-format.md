# Output Format · 5 段 markdown 模板

> 完整正例（NVDA / AAPL / MSFT 三份真实数据）见 SKILL.md 末尾。本文件是结构骨架与每段格式约束。硬规则见 `hard-rules.md`，字段速查见 `ai-payload-schema.md`。

## 整体骨架

```markdown
# {symbol_short} · 期权聪明钱画像
📅 价格 / IV：{data_as_of}  · PCR：{pcr_latest_date}

> ℹ️ PCR 由 broker T+1 提供（每日 12 点前后更新次日数据），当前 PCR 比价格 / IV 数据晚 {pcr_lag_days} 天。
> ⚠️ 报告基于盘中实时抓取，期权 IV 及成交量会受实时报价波动影响，盘前、盘中、盘后版本会有所差异。
> ⚠️ 期权流动性较低（活跃 strike {active_strikes} 个），数据仅供参考。

## §1 今日定调

{LLM 写 120-180 字 · 串联方向 + 期权定价 + Wall 区间 + iv_peak（如有），末句必须是交易主线}

## §2 KPI 仪表盘

| 指标 | 数值 | 含义 |
|---|---|---|
| PCR · OI | **{pcr_oi:.3f}** | {LLM ≤15 字} |
| 30D ATM IV | **{atm_iv_pct:.1f}%** | {LLM ≤15 字} |
| HV (30D) | **{hv_pct:.1f}%** | 过去 30 个交易日实际波动 |
| IV − HV | **{iv_hv_spread_pp:+.1f}pp** | {LLM ≤15 字} |
| Max Pain | **${max_pain.strike:.0f}** | 距现价 {max_pain.distance_pct:+.1f}% |
| Call/Put Wall | **${call_wall.strike:.0f} / ${put_wall.strike:.0f}** | {call_wall.distance_pct:+.1f}% / {put_wall.distance_pct:+.1f}% |

## §3 关键水位

\`\`\`
{ASCII 双向蝴蝶图}
\`\`\`

- 上方阻力 **${call_wall.strike}**（Call Wall）：Call OI **{call_wall.oi_wan} 万张**，距现价 **{call_wall.distance_pct:+.1f}%**
- 下方支撑 **${put_wall.strike}**（Put Wall）：Put OI **{put_wall.oi_wan} 万张**，距现价 **{put_wall.distance_pct:+.1f}%**
- Max Pain **${max_pain.strike}** 引力中枢，距现价 **{max_pain.distance_pct:+.1f}%**
- 深度支撑：{deep_supports[]} · 深度阻力：{deep_resistances[]} （任一为空时省略该方向）

## §4 波动率视角

- **近端最紧张**：{iv_peak.expiry}（{iv_peak.days_to_expiry}天后）IV **{iv_peak.iv_pct:.1f}%**
- **近端常态**：5-14 天 IV 在 **{iv_near.iv_pct:.0f}%** 附近
- **远端均衡**：30 天后 IV 回到 **{iv_far.iv_pct:.1f}%**

→ {LLM 末句 · 30-60 字}

## §5 策略推荐

**方向**：<做多 / 做空 / 中性偏多 / 中性偏空 / 中性震荡>（一句话依据：PCR 分位 + Wall 距离 + 价格相对 Max Pain，≤ 50 字）

**候选策略**：

| 偏好 | 工具 | Strike | 理由 |
|---|---|---|---|
| {卖方 / 偏多 / 偏空 / 中性震荡 / 持股增收 / 持股对冲} | {工具名} | {Strike，来自 ai_payload} | {≤25 字依据} |
| ... | ... | ... | ... |

**期限**：到期日由读者自选——短线博波动选近端到期，趋势跟随选 1-2 月以上。

> ⚠️ 策略基于公开期权链数据，仅供参考，不构成投资建议。期权风险显著高于股票现货，请谨慎评估自身风险承受能力。

---
⚠️ 基于公开期权链聚合，不构成投资建议。
```

## Header 行

- **标题**：`# {symbol_short} · 期权聪明钱画像` — symbol 去 `.US` 后缀（"NVDA.US" → "NVDA"）
- **日期行**：`📅 价格 / IV：{data_as_of}  · PCR：{pcr_latest_date}`

**3 条 conditional banner**（顺序固定：ℹ️ → ⚠️ 盘中 → ⚠️ 低流动性；用 markdown `>` 引用块）：

1. **ℹ️ PCR 滞后**（仅当 `data_quality.pcr_lag_days > 0`）：
   ```
   > ℹ️ PCR 由 broker T+1 提供（每日 12 点前后更新次日数据），当前 PCR 比价格 / IV 数据晚 {pcr_lag_days} 天。
   ```

2. **⚠️ 盘中**（仅当 `data_quality.is_intraday = true`）：
   ```
   > ⚠️ 报告基于盘中实时抓取，期权 IV 及成交量会受实时报价波动影响，盘前、盘中、盘后版本会有所差异。
   ```

3. **⚠️ 低流动性**（仅当 `data_quality.reliable = false` 且 **非冷门**——`low_liquidity = false`）：
   ```
   > ⚠️ 期权流动性较低（活跃 strike {active_strikes} 个），数据仅供参考。
   ```

常态（三个条件全部不满足）：header 只有标题 + 日期行，无 banner。

## 冷门标的拒绝路径（`data_quality.low_liquidity = true`）

**不出 §1-§5 完整报告**——直接输出 SKILL.md「冷门标的简报」模板：

- 标题：`# {symbol_short} · 期权流动性不足，无法画像`
- 一段诊断文字（点名 `max_strike_oi_wan` < 阈值 1 万）
- 4 行可信指标表（PCR / 30D IV / HV / 短期最大单 strike OI）
- 适用范围说明 + 引导到 `/quote` / `/kline` / `/news`
- 免责语

文案要求（详见 SKILL.md「冷门标的简报」节）：
- 4 行数值都来自 ai_payload，**不要编造**
- 标题明确说"无法画像"，不要软化成"分析"
- 不用"基本上 / 可能 / 或许"等弱化词

## §1 详细约束（LLM 全写，120-180 字）

详见 SKILL.md「§1 今日定调」一节。要点速记：

- 必须串联：**方向**（多 / 空 / 中性 + 数字依据）→ **期权定价**（iv_hv_spread_pp + 贵 / 合理 / 便宜）→ **Wall 区间含义**（call/put_wall 与现价位置）→ **iv_peak 凸点描述**（若非 None，只描述现象不指事件）
- **末句必须是交易主线**："破 \$X 看 \$Y" / "等回踩 \$Z 再判断" / "事件前不入场" 等
- 禁用清单见 `hard-rules.md §3`

## §2 详细约束（数值列模板 + LLM 含义列）

- **必须用 markdown 表格**；行顺序固定（PCR → 30D IV → HV → IV-HV → Max Pain → Wall），不可调
- **数值列**：用 `**bold**` 包裹关键数字；格式严格按 `ai-payload-schema.md` 单位约定（`_pct` → `%`，`_pp` → `+/-N.Npp`，`strike` → `${value:.0f}`）
- **含义列约束**：
  - **≤ 15 字**
  - 不重复数值列内容（PCR 含义列不要写"0.791"）
  - 不指明事件类型
  - 不出现 backwardation / contango / vega / gamma
  - PCR 行：引用 7 档分位区间词（"30 日新低 / 低位 / 中下位 / 中位 / 中上位 / 高位 / 新高"，详见 SKILL.md PCR 分位描述对照表）
  - IV-HV 行：贵 / 合理 / 便宜 + 一句操作含义（"做多波动率不便宜"）
- **HV 行的含义列固定**：`过去 30 个交易日实际波动`（不让 LLM 改写）

**小盘股 / 流动性差标的 PCR 分位 caveat**：`pcr_30d_rank_pct` 算法用严格小于比较，遇 PCR 序列窄 / 相同值多的标的，rank 数字会偏低且不敏感。`data_quality.reliable = false` 时，含义列附加一句「⚠️ 流动性较低，分位仅供参考」让读者知情，不要把 rank 当强信号引用。详见 SKILL.md。

## §3 详细约束（纯模板）

- **ASCII 蝴蝶图**：绘制规则见 `ascii-butterfly-template.md`，必须用三反引号代码块包裹（确保等宽字体）
- **4 行 bullet**：模板填充，**不是 LLM 写**
  - bullet 1 / 2 / 3：Call Wall / Put Wall / Max Pain（行内 `**bold**` 强调 strike / OI / distance_pct 三个数字）
  - bullet 4：深度集群——`deep_supports` 非空时列出每个 `{strike} (OI {oi_wan}万, {distance_pct:+.1f}%)`，逗号分隔；`deep_resistances` 同上；两边都空则省略整条 bullet
- Max Pain 缺失则跳过第 3 行 bullet

**Wall vs 深度集群的区别**（v2 算法 2026-05-24 上线）：Wall = 现价近端支撑/阻力（同侧距 cp 最近、OI ≥ 3 万），日内交易级；深度集群 = Wall 之外的远端集中点（OI ≥ 5 万），趋势级。

## §4 详细约束（前 3 行模板 + LLM 末句）

**常态**（`iv_peak` 非 None）：3 行模板照填，末句 LLM 写 30-60 字。

**iv_peak = None 降级**：
- 第 1 行改：`- **近端最紧张**：无明显近期 IV 凸点（近端与远端 IV 接近）`
- 末句改：`→ IV 期限结构平稳，市场无近期事件溢价。`
- 第 2、3 行保持不变

**LLM 末句要求**：
- 解读 IV 期限结构含义（近端凸 / 远端均衡 / 全段紧张等）
- **只描述现象，不指明事件类型**（不说"财报 / FOMC / CPI / 关税"）
- **不出现** backwardation / contango / humped / flat 专业词

## §5 详细约束（卡片表格式 · LLM 写）

**结构**（方向句 + 候选策略表 + 期限说明 + 免责语）。

**方向句**：
- ≤ 50 字
- 一句话依据 = PCR 分位 + Wall 距离 + 价格相对 Max Pain 三个维度综合
- 方向枚举：做多 / 做空 / 中性偏多 / 中性偏空 / 中性震荡

**候选策略表**：4 列固定（偏好 / 工具 / Strike / 理由）：

| 列 | 约束 |
|---|---|
| 偏好 | 示例：卖方 / 偏多 / 偏空 / 中性震荡 / 持股增收 / 持股对冲。如 IV 偏贵优先放卖方 |
| 工具 | 见下方「可推荐工具表」，**不在表内的工具禁用**（IC / Butterfly 因 strike 不够禁用） |
| Strike | **必须严格来自 ai_payload 的 3 个 strike**：`call_wall.strike` / `put_wall.strike` / `max_pain.strike`。Strangle / Spread 用「$X Put + $Y Call」格式。深度集群只能当目标价提一句，不能当 strike 推荐 |
| 理由 | ≤ 25 字，给出"为什么这个策略对应当前画像" |

**表行数**：3-4 行候选即可，不要强行填满。

**可推荐工具表**（受 3 strike 上限约束）：

| 工具 | 用到的 strike |
|---|---|
| 裸买 Call | call_wall |
| 裸买 Put | put_wall |
| Straddle | max_pain Call + max_pain Put |
| Strangle | put_wall Put + call_wall Call |
| Call Spread | max_pain + call_wall |
| Put Spread | put_wall + max_pain |
| 备兑 Call（持股增收）| call_wall（卖 Call）|
| 保护性 Put（持股对冲）| put_wall（买 Put）|

**Iron Condor / Iron Butterfly 不在推荐范围**（需 4 strike，超 ai_payload 提供）。

**期限句**：固定文案——
```
**期限**：到期日由读者自选——短线博波动选近端到期，趋势跟随选 1-2 月以上。
```

期限措辞红线（详见 hard-rules.md §4.6）：禁具体日期（"5/22 到期" / "下周到期"）+ 禁事件类型（"财报后 / FOMC 后"）。

**免责语**：固定文案——
```
> ⚠️ 策略基于公开期权链数据，仅供参考，不构成投资建议。期权风险显著高于股票现货，请谨慎评估自身风险承受能力。
```

## 免责声明

固定文案，不允许修改：

```
---
⚠️ 基于公开期权链聚合，不构成投资建议。
```

## 字数与节奏

| 段 | 字数 | 主要内容 | 谁写 |
|---|---|---|---|
| header | 1-4 行 | 标题 + 日期 +（条件 banner） | 模板 |
| §1 | 120-180 字 | 方向 + 定价 + Wall + 凸点 + 交易主线 | LLM |
| §2 | ~80 字（含表格） | 6 行 KPI 表 | 模板 + LLM 含义列 |
| §3 | ASCII + ~80 字 | 蝴蝶图 + 4 行 bullet（含深度集群）| 纯模板 |
| §4 | ~80 字 | 3 行 IV 数据 + 末句 | 模板 + LLM 末句 |
| §5 | 120-180 字 + 表 + 期限 + 免责 | 方向 + 候选策略卡片表 | LLM |
| **合计** | **~500-650 字 + ASCII 蝴蝶图 + 策略表** | 阅读 ≤ 3 分钟 | — |
