---
name: option-flow
description: "美股期权聪明钱画像。输入 US 标的（如 NVDA / AAPL / TSLA / MSFT），输出 5 段中文报告，给方向、给主线、给数字依据。触发：/option-flow <ticker>、期权聪明钱、option flow、smart money、Call Wall / Put Wall / Max Pain / gamma。"
---

# Option-flow

期权聪明钱视角，写一份**对交易有指导价值**的 5 段中文画像。**目标读者**：长桥散户。**核心交付物**：看完知道今天市场在定价什么、Wall 在哪、IV 紧不紧、下一步该怎么看。

## 执行管线（先跑这步拿到 ai_payload）

本 skill 触发后，**先生成 `ai_payload`，再按下文渲染**。生产入口零落盘，直接 stdout 输出：

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/option_flow.py" <SYMBOL.US>
```

- `<SYMBOL.US>` = 用户输入标的（如 `NVDA.US`，依赖 longbridge CLI，仅美股）。
- **stdout 的 JSON 即唯一数据源**，拿到后所有数字只能来自它（见下文数据契约）。
- **非零退出码 → 把 stderr 内容转述给用户，禁止硬渲染**（防止拿空数据编报告）：
  - 退出码 3（`NoOptionsError`）→「该标的无可用期权链」
  - 退出码 4（`CLIError`）→「行情数据获取失败」
  - 退出码 2 → 提示需要 `<SYMBOL.US>` 参数

## 数据契约

唯一数据源 = `compute.py` 输出的 `ai_payload` 字典（详见 `references/ai-payload-schema.md`）。**ai_payload 之外的数字一律不能出现**。

**单位铁律**（字段名带后缀，禁止换算）：
- `_pct` → `%`（`atm_iv_pct: 43.8` → `43.8%`）
- `_pp` → `pp`（`iv_hv_spread_pp: 5.5` → `+5.5pp`）。**pp ≠ %**
- `_wan` → `万张`
- `strike` → `${value:.0f}`
- `pcr_oi` → 直接 3 位小数（`0.791`）

**精度标记**（`term_structure` 各字段的 `precision`）：
- `indicative`（iv_peak）→ 仅供定性引用，不要做差值比较
- `normal`（iv_near）→ 可单点引用
- `high`（iv_far）→ 可参与跨字段比较（如与 HV 做差）

## 风格四原则

1. **指向性明晰**：给方向、给主线、给数字依据。禁"可能 / 或许 / 也许 / 大概 / 似乎"。
2. **数据骨架**：每个判断后跟数字依据。"偏多"必须跟"PCR 0.791 处于 30 日新低"这种证据。
3. **通俗易懂**：散户语气。**禁专业行话**：backwardation / contango / vega / gamma / "第 X 分位" / "极高 / 极低" / "创新极高 / 创新极低"。
4. **轻量化**：能用一句话别用三句，能用数字别用形容词。

### PCR 分位描述对照（必须用，不要写"第 X 分位"）

| `pcr_30d_rank_pct` 数字 | 写法 | 语义 |
|---|---|---|
| ≤ 5 | "30 日新低" | 30 天内最低（事实陈述）|
| 6-20 | "30 日低位" | 偏低区间 |
| 21-40 | "30 日中下位" | 中下 |
| 41-60 | "30 日中位" | 中段 |
| 61-80 | "30 日中上位" | 中上 |
| 81-95 | "30 日高位" | 偏高区间 |
| ≥ 95 | "30 日新高" | 30 天内最高（事实陈述）|

⚠️ **禁用"30 日极高 / 极低"**——"极"字会让散户误以为绝对值已到极端水平，但 `pcr_30d_rank_pct` 只反映**相对 30 天历史的统计位置**，跟 PCR 绝对值无关。例如 PCR=0.755 但 rank=100 → 是"30 日新高"（事实）而不是"极高"（强度），因为绝对值仍 < 1 仍是 Call 主导。

⚠️ **小盘股 / 流动性差标的 PCR 分位误读风险**——`pcr_30d_rank_pct` 算法用 `<` 严格比较（详见 `compute.py:_kpi_pcr`），遇 PCR 序列窄、相同值（ties）多的标的（典型如小盘股 / 低成交量 ETF），rank 数字会偏低且对真实情绪变化不敏感。报告 §1 / §2 含义列遇 `data_quality.reliable=false` 时，**附加一句**「⚠️ 期权流动性较低，PCR 分位数字仅供参考」让读者知情，**不要把 rank 当强信号引用**。

## 报告头格式

**常态**（PCR 与价格 / IV 同步，`data_quality.pcr_lag_days == 0`）：

```
# {symbol_short} · 期权聪明钱画像
📅 价格 / IV：{data_as_of} · PCR：{pcr_latest_date}
```

**PCR 滞后**（`data_quality.pcr_lag_days > 0`）：紧接标题下方加 ℹ️ 行：

```
> ℹ️ PCR 由 broker T+1 提供（每日 12 点前后更新次日数据），当前 PCR 比价格 / IV 数据晚 {pcr_lag_days} 天。
```

**盘中**（`data_quality.is_intraday = True`）：在 ℹ️ 行之后（或无 ℹ️ 行时直接在标题下方）加 ⚠️ 行：

```
> ⚠️ 报告基于盘中实时抓取，期权 IV 及成交量会受实时报价波动影响，盘前、盘中、盘后版本会有所差异。
```

## 段落

| 段 | 谁写 | 字数 |
|---|---|---|
| §1 今日定调 | **LLM**（最关键） | 120-180 字 |
| §2 KPI 表 | 数值列模板 + **LLM 含义列**（每行 ≤ 15 字） | — |
| §3 ASCII 蝴蝶图 + 状态读法 bullet | ASCII 纯模板 + **LLM 状态读法**（消费 `read_states`） | — |
| §4 IV 视角 | 前 3 行模板 + **LLM 末句** | 末句 30-60 字 |
| §5 策略推荐 | **LLM** | 120-180 字 + 固定免责语 |

### §1 今日定调（LLM 写）

**任务**：开场即差异化——先抛画像标签，再讲故事，末句给可操作主线。

**首行必须是加粗结构标签行**（直接取 `read_states.structure_label`，禁改名；任一墙缺失则改写为"核心关注 Max Pain"）：
> **【{structure_label}】**

紧接 120-180 字正文，必须串联：
- **方向**：用 `read_states.pcr_read`（direction + 若 divergence 加 note，如"偏多但避险升温"）+ 数字依据（PCR 绝对值 + 分位词）
- **期权定价**：用 `read_states.iv_regime`（偏贵/合理/偏便宜）+ `iv_hv_spread_pp` 数字
- **结构含义**：用 structure_label 的几何含义（天花板紧贴/下方真空/双墙锁死等），点出现价 vs Wall 的不对称/紧贴/真空
- 若 `iv_peak` 非 None，提一句近期 IV 凸点（只描述现象、不指事件类型）
- **禁用通用开场**"夹在…之间 / 现价位于…区间"——必须以结构标签开场
- **末句 = 交易主线**："站上 $X 看 $Y" / "失守 $Z 直奔真空带" / "事件前不入场" 等

### §2 KPI 仪表盘（数值列模板 + LLM 含义列）

```
| 指标 | 数值 | 含义 |
|---|---|---|
| PCR · OI | **{pcr_oi:.3f}** | {LLM: ≤15 字解读 PCR 状态，引用分位区间} |
| 30D ATM IV | **{atm_iv_pct:.1f}%** | {LLM: ≤15 字解读市场紧张程度} |
| HV (30D) | **{hv_pct:.1f}%** | 过去 30 个交易日实际波动 |
| IV − HV | **{iv_hv_spread_pp:+.1f}pp** | {LLM: ≤15 字解读偏贵 / 合理 / 偏便宜 + 操作含义} |
| Max Pain | **${max_pain.strike:.0f}** | 距现价 {max_pain.distance_pct:+.1f}% |
| Call/Put Wall | **${call_wall.strike:.0f} / ${put_wall.strike:.0f}** | {call_wall.distance_pct:+.1f}% / {put_wall.distance_pct:+.1f}% |
```

含义列约束：
- **≤ 15 字**
- **不重复数值列**（PCR 行不要写"0.791"）
- **不指明事件类型**
- **不出现 backwardation / contango / vega / gamma**
- **PCR 行**：用 `read_states.pcr_read.direction`（偏多/均衡/偏空）+ 分位区间词；若 `pcr_read.divergence=true` 追加 note（如"偏多但避险升温"）。≤15 字。
- **IV-HV 行**：用 `read_states.iv_regime`——偏贵→"偏贵，卖方占优"／合理→"定价合理"／偏便宜→"偏便宜，买方占优"。≤15 字。

### §3 关键水位（ASCII 纯模板 + LLM 状态读法 bullet）

**ASCII**：直接 paste `key_levels.oi_distribution.ascii`，整段包在代码块里（compute 预渲染，LLM 不画不抄）。

**bullet（LLM 写，消费 `read_states`）**：从"报数字"升级为「水位 + 现价相对状态 + 状态读法 + OI 机制」。每条骨架：

- **上方阻力 ${call_wall.strike}** · 现价{call_wall_proximity}（{call_wall.distance_pct:+.1f}%）→ {按 proximity 选读法}；持仓 {call_wall.oi_wan} 万张。
- **下方支撑 ${put_wall.strike}** · 现价{put_wall_proximity}（{put_wall.distance_pct:+.1f}%）→ {按 proximity 选读法}；持仓 {put_wall.oi_wan} 万张。
- **Max Pain ${max_pain.strike}** 引力中枢（{max_pain_pull.side}，{max_pain.distance_pct:+.1f}%[，与 X Wall 重合]）{若 `read_states.max_pain_pull.is_noise = true` 行末补"（薄 OI，引力信号弱，仅供参考）"}。
- **结构判定**：{structure_label} = {对照下方 structure_label 对照表取句，不自由发挥}。
- **深度支撑**（deep_supports 非空时）：逐个列 `${strike}（{oi_wan} 万张，{distance_pct:+.1f}%）`，逗号分隔，一行写完；语义 = Wall 之外的趋势级支撑带、失守 Put Wall 后的下一道防线。为空则省略此 bullet。
- **深度阻力**（deep_resistances 非空时）：同格式；语义 = 突破 Call Wall 后的趋势级阻力。为空省略。
- {若 `read_states.thin_wall = true`}：⚠️ 单 strike 最大持仓仅 {data_quality.max_strike_oi_wan} 万张，墙薄、引力弱，仅供参考。`thin_wall` 由 compute 判定，LLM 不自行评估阈值，按布尔值直接决定是否出此⚠️行。

**proximity → 读法对照**：
| proximity | 读法 |
|---|---|
| 逼近（≤2%） | "最高信号区"：持仓集中、冲高/杀跌易停滞回落；给前向触发"站上/失守 $X 才转向" |
| 中等（2-5%） | 该位是可达的近端目标/阻力，描述到位的空间 |
| 远离（>5%） | 强调"现价到该墙之间是 N% 无持仓真空 / 缓冲带"，失守/突破后无支撑/无压力 |

**机制句**：OI 口径——持仓集中→做市商调仓量大→搬动股价→引力；薄则弱。**禁 gamma 措辞**（见 hard-rules）。

**结构判定行**：直接引用 `read_states.structure_label`（5 值，禁改名）。任一墙缺失（label=null）走 Wall 缺失细则，不写结构判定行。

**structure_label → 结构判定句对照**（结构判定行直接套用，不自由发挥）：
| structure_label | 结构判定句 |
|---|---|
| 天花板紧贴·下方真空 | 偏空压顶，上方紧压、失守现价则下方真空加速（非震荡） |
| 地板紧贴·上方开阔 | 偏多有撑，下方托底、上方空间打开 |
| 双墙紧夹·窄震荡 | 双墙锁死现价，区间震荡为主 |
| 双墙宽松·区间漂移 | 上下均有空间，区间漂移、跟随突破 |
| null（任一墙缺失） | 不写结构判定行，走 Wall 缺失细则 |

**Wall vs 深度集群的区别**（v2 算法 2026-05-24 上线）：
- Wall = 现价**近端**支撑/阻力（同侧距 cp 最近、OI ≥ 3 万的 strike），日内交易级
- 深度集群 = Wall 之外的**远端**支撑/阻力集中点（OI ≥ 5 万），趋势级
- 例：SPY 5/22 Put Wall=$735（近端支撑），深度支撑 $730/$725/$720/$710/$700（OI 14.5 万的 $700 是深底，但不是日内 Wall）

### §4 波动率视角（前 3 行模板 + LLM 末句）

```
- **近端最紧张**：{iv_peak.expiry}（{iv_peak.days_to_expiry}天后）IV **{iv_peak.iv_pct:.1f}%**
- **近端常态**：5-14 天 IV 在 **{iv_near.iv_pct:.0f}%** 附近
- **远端均衡**：30 天后 IV 回到 **{iv_far.iv_pct:.1f}%**

→ {LLM 一句话定性 · 30-60 字}
```

LLM 末句要求：
- 解读 IV 期限结构含义（近端凸 / 远端均衡 / 全段紧张等）
- **只描述现象、不指明事件类型**（不说"财报 / FOMC / CPI / 关税"）
- 不出现 backwardation / contango / humped / flat 专业词
- **末句必须含 regime→策略桥**：用 `read_states.iv_regime` 给一句操作倾向——偏贵→"卖方收权利金占优，裸买追单吃亏"；偏便宜→"买方占优，做多波动率划算"；合理→"买卖双方均衡，方向比波动率更重要"。（仍不指事件类型，不用 gamma 词）

`iv_peak = None` 时：第一行改"无明显近期 IV 凸点（近端与远端 IV 接近）"，末句改"IV 期限结构平稳，市场无近期事件溢价。"（**约 15 字，不必扩写到 30 字**——降级场景没有可展开的内容，扩写会注水）

### §5 策略推荐（LLM 写 · 卡片式表格 + 固定免责语）

**任务**：基于 ai_payload 给**可执行**的期权策略——方向 + 工具 + Strike + 波动率视角，**不指定到期日**（期限由读者自选）。末尾固定免责语。

**结构**（方向句 + 候选策略表 + 期限说明 + 免责）：

```
**方向**：<做多 / 做空 / 中性偏多 / 中性偏空 / 中性震荡>（一句话依据：PCR 分位 + Wall 距离 + 价格相对 Max Pain，≤ 50 字）

**候选策略**（Strike 必须来自 `call_wall.strike` / `put_wall.strike` / `max_pain.strike`，可参考深度集群作为目标价）：

| 偏好 | 工具 | Strike | 理由 |
|---|---|---|---|
| 卖方 / 首选（如 IV 贵）| 卖出 Strangle | $X Put + $Y Call | IV 偏贵，押区间震荡收权利金 |
| 偏多 | 裸买 Call | $Y | 突破阻力看 $Z |
| 偏空 | 裸买 Put | $X | 跌破支撑看 $W |
| 持股增收 | 备兑 Call | $Y | 卖出 Call Wall 收权利金 |

**期限**：到期日由读者自选——短线博波动选近端到期，趋势跟随选 1-2 月以上。

> ⚠️ 策略基于公开期权链数据，仅供参考，不构成投资建议。期权风险显著高于股票现货，请谨慎评估自身风险承受能力。
```

**表格规则**：
- 3-4 行候选即可，不需要把表填满（IC / Butterfly 因 strike 不够不推荐）
- "偏好"列示例：卖方 / 偏多 / 偏空 / 中性震荡 / 持股增收 / 持股对冲
- "理由"列 ≤ 25 字，给出"为什么这个策略对应当前画像"

### Strike 选择硬约束

LLM 推荐的所有 strike **必须严格来自** ai_payload 字段，禁止编造：

| 字段 | 用途 |
|---|---|
| `key_levels.call_wall.strike` | 上方阻力 |
| `key_levels.put_wall.strike` | 下方支撑 |
| `key_levels.max_pain.strike` | 引力中枢 |

### 可推荐工具表（受 3 strike 上限约束）

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

### Strike 重合时的候选表处理（B3 修复）

**当 Max Pain 与 Put Wall（或 Call Wall）落在同一 strike 时**（如 NVDA Max Pain $210 = Put Wall $210），候选策略表会出现工具冲突：
- **Straddle** 用 max_pain Call + max_pain Put → 若与 Put Wall 重合，与 "裸买 Put + 备兑 Call" 路径重叠
- **Put Spread** 用 put_wall + max_pain → 重合时退化为单 strike 同行权价
- **Strangle** 用 put_wall Put + call_wall Call → 仍可用

**处理规则**：
- 候选策略表行数从 "3-4 行" 放宽为 "2-4 行"——重合发生时**减少候选数量**，不要为凑行数硬列重叠工具
- 优先保留 **直接对应 Wall** 的工具（裸买 Call / 裸买 Put / Strangle / 备兑 Call），删除会与重合 strike 冲突的工具
- 在 §1 末句明确"现价贴 Max Pain / Wall 与 Max Pain 重合"——让散户理解为什么候选数量少

### 期限措辞

- ✅「短期到期」「近端到期」「1-2 月到期」「中长期到期」
- ❌「5/22 到期」「下周到期」（不指定具体日期）
- ❌「财报后到期」「FOMC 后到期」（不指明事件类型，hard-rules 3.1）

## 禁止

- 编造 `ai_payload` 之外的数字
- 单位错位（pp 写成 %、万张写成张、strike 写成"美元"）
- 指明事件类型（"财报 / FOMC / CPI / 关税"）—— LLM 无日历无法验证
- 统计行话（"第 X 分位 / 标准差 / 相关系数 / 创新极低 / 创新极高"）—— 散户看不懂
- 专业 vol 术语（backwardation / contango / vega / gamma / humped / flat）—— 散户看不懂
- 模糊弱化词（可能 / 或许 / 也许 / 大概 / 似乎 / 看上去）—— 跟"指向性明晰"冲突
- 浮夸而无数据依据（有数字支撑可以"飙升 / 暴跌"，无依据不行）
- 指代非公开信息（"内幕消息 / 知情人士"）

## 错误降级

| 错误形态 | 处理 |
|---|---|
| 非美股 symbol | 「option-flow 当前仅支持美股（.US 后缀）」 |
| 标的无期权 | 「{symbol} 当前无活跃期权链」 |
| 必填缺（pcr_oi / atm_iv_pct / oi_distribution） | 抛错，不出半截报告 |
| **`data_quality.low_liquidity=true`** | **走拒绝路径，输出"期权盘子太小"简报**（见下方）——不出 §1-§5 完整报告 |
| `data_quality.reliable=false`（但非冷门）| 报告头加 `⚠️ 期权流动性较低（活跃 strike {n} 个），数据仅供参考` |
| `data_quality.is_intraday=true` | 报告头加风险提示 banner（见上文） |
| `data_quality.pcr_lag_days > 0` | 报告头加 PCR 滞后说明（见上文） |
| `call_wall` / `put_wall` 任一缺 | §1 改写"核心关注 Max Pain"，§2/§3 bullet/§5 表对称处理（详见下方 Wall 缺失细则） |
| `iv_peak = None` | §4 按上面规则降级 |

### Wall 缺失细则（A1 + A2 + A4 修复）

**对称性**：`call_wall=null` 和 `put_wall=null` 影响范围**对称**——
- `call_wall=null` 时：
  - §2 表 `Call/Put Wall` 行**同行两栏对位**：数值列 `— Wall 缺失 / ${put_wall.strike:.0f}`，距离列 `— / {put_wall.distance_pct:+.1f}%`
  - §3 bullet "上方阻力"行写 `— Wall 缺失`
  - §5 表**删除涉及 call_wall 的所有行**（裸买 Call / 备兑 Call / Strangle / Call Spread 的 call_wall 部分）
- `put_wall=null` 时：对称处理 put 侧——
  - §2 表数值列 `${call_wall.strike:.0f} / — Wall 缺失`，距离列 `{call_wall.distance_pct:+.1f}% / —`
  - §3 bullet "下方支撑"行写 `— Wall 缺失`
  - §5 表删除 裸买 Put / 保护性 Put / Strangle 的 put_wall 部分 / Put Spread

**§5 表占位粒度**：**直接删除涉及缺失 Wall 的行**——表格只列**实际可用的策略**，**不要留"— Wall 缺失"占位行凑行数**。表行数可少于 3 行（最低 2 行）。

**§3 ASCII 字段**：ASCII 由 `compute.py` 预渲染——当 `call_wall=null` 时 ASCII **不会**标 `● CALL WALL` 标记（put_wall 对称）。**LLM 直接 paste 即可，不要校对 ASCII 与 Wall 字段是否一致**——这是 compute.py 的责任，不是 LLM 的。如果你看到 ASCII 含 `● CALL WALL` 标记但 `call_wall=null`，那是上游数据 bug，不应在 prompt 层修补。

### 期权盘子太小简报（`data_quality.low_liquidity=true` 时输出）

**直接输出以下内容，不出 §1-§5 完整报告**（变量从 ai_payload 填）：

```markdown
# {symbol_short} · 期权盘子太小，这套分析没意义

**{symbol_short}** 近月期权（≤14 天）单 strike 最大持仓量仅 **{data_quality.max_strike_oi_wan} 万张**。

我们这套分析能不能用，全看一个前提：**期权数据要能反过来推动股价**。原理是——合约数量大 → 做市商每天对冲调仓的量也大 → 调仓时顺手搬动股票 → 持仓集中的 strike 变成股价的引力点。**反过来：合约少 → 调仓量小 → 股价几乎不受影响**。

{symbol_short} 现在的合约数量撑不起这个前提。我们算出的 Wall / Max Pain 数字数学上是对的，但在这种 OI 量级上就像往大江里扔一颗小石子——涟漪有，但水流方向不变。

📊 当前可信指标（聚合口径仍有效）：

| 指标 | 数值 | 含义 |
|---|---|---|
| PCR · OI | **{pcr_oi:.3f}** | 服务端聚合，跨 expiry T+1 |
| 30D ATM IV | **{atm_iv_pct:.1f}%** | VIX 同口径方差插值 |
| HV (30D) | **{hv_pct:.1f}%** | 过去 30 个交易日实际波动 |
| 短期最大单 strike OI | **{data_quality.max_strike_oi_wan} 万张** | 流动性诊断 |

**建议**：等这只标的期权变活跃了再来看，或者换一个盘子大的标的——大盘 ETF（SPY/QQQ/IWM）、Mag 7 大盘股、主流个股都更稳。

其他可选：
- `/quote {symbol}` —— 实时报价
- `/kline {symbol}` —— K 线走势
- `/news {symbol}` —— 新闻 / 公告

---
⚠️ 数据来自公开期权链聚合，不构成投资建议。
```

**规则**：
- 表格里的 4 行数值都来自 ai_payload，**不要编造**
- 标题明确说"盘子太小，这套分析没意义"——不软化也不甩锅给标的"冷门"
- 解释段落必须保留**"前提 → 原理 → 反过来"** 三段结构（合约多/做市调仓/搬动股票 → 反之合约少/调仓小/股价不受影响）
- 必须保留**"大江里扔小石子"** 比喻——这是最有说服力的部分，散户秒懂"涟漪有水流方向不变"
- 不出现 dealer / gamma / GEX / pin / vanna 等专业词——给散户的版本只留直觉
- 文案不要软化（避免"基本上"、"可能"、"或许"）——直接告诉用户分析没意义
- 末尾免责语保留

**理论支撑（内部参考，不放进散户文案）**：基于 Garleanu-Pedersen-Poteshman (2009, RFS) demand-based option pricing + Ni-Pearson-Poteshman (2005, JFE) pin risk + Ni-Pearson-Poteshman-White (2021, RFS) dealer hedge 解释 ~12% 日内收益。所有这些机制都正比于 OI / volume 规模——OI 趋零时 dealer 调仓量趋零、pin risk 趋零、demand pressure 趋零，期权链对标的从主动驱动退化为被动镜像。

## 三个完整正例（真实数据，2026-05-21 拉取）

### 正例 1 · NVDA（事件前夕 · iv_peak 触发）

```markdown
# NVDA · 期权聪明钱画像
📅 价格 / IV：2026-05-20 · PCR：2026-05-20

## §1 今日定调

**【双墙宽松·区间漂移】**

NVDA 散户偏多但 IV 已含溢价。PCR **0.791** 处于 **30 日新低**，Call 持仓占优、散户押上涨。IV 比 HV 高 **+5.5pp**，期权定价**偏贵**，买方追单不划算。现价 $223 上方 $240 Call Wall（+7.4%）、下方 $210 Put Wall（-6.0%）都有 6-7% 空间，区间漂移为主；5/22 单日 IV 飙至 **92.4%**，市场押注当日大幅波动。

**交易主线**：事件前 IV 偏贵不追单；突破 $240 看上行、失守 $210 转弱，区间内跟随突破方向。

## §2 KPI 仪表盘

| 指标 | 数值 | 含义 |
|---|---|---|
| PCR · OI | **0.791** | 30 日新低，Call 占优 |
| 30D ATM IV | **43.8%** | 市场紧张度偏高 |
| HV (30D) | **38.4%** | 过去 30 个交易日实际波动 |
| IV − HV | **+5.5pp** | 期权偏贵，做多波动率不便宜 |
| Max Pain | **$215** | 距现价 -3.8% |
| Call/Put Wall | **$240 / $210** | +7.4% / -6.0% |

## §3 关键水位

[ASCII 蝴蝶图]

- **上方阻力 $240** · 现价远离（+7.4%）→ 到该墙有 7.4% 空间，突破后上方无近压力；Call 持仓 **10.2 万张**（厚墙，引力强）。
- **下方支撑 $210** · 现价远离（-6.0%）→ 失守现价后到该墙有 6% 缓冲；Put 持仓 **4.0 万张**。
- **Max Pain $215** 引力中枢（下方，-3.8%）。
- **结构判定**：双墙宽松·区间漂移 = 上下均有空间，区间漂移为主。

## §4 波动率视角

- **近端最紧张**：2026-05-22（2 天后）IV **92.4%**
- **近端常态**：5-14 天 IV 在 **55%** 附近
- **远端均衡**：30 天后 IV 回到 **42.5%**

→ 5/22 单日 IV 飙至 92%，市场押注当日大幅波动；近端常态 55% 仍偏高，事件后回落到 42% 才算释放完毕。

## §5 策略推荐

**方向**：中性偏多但避免单边追涨——PCR 30 日新低显示散户已偏多、IV +5.5pp 已含溢价，单边追涨性价比低。

**候选策略**：

| 偏好 | 工具 | Strike | 理由 |
|---|---|---|---|
| 双向博波动 | 买入 Strangle | $210 Put + $240 Call | 押双向大幅波动，跨越任一 Wall 即获利 |
| 偏多 | 裸买 Call | $240 | 突破阻力看 $245-250 |
| 偏空 | 裸买 Put | $210 | 跌破支撑看 $200-205 |
| 卖方 / IV 偏贵 | 卖出 Strangle | $210 Put + $240 Call | 收 IV 回落 + 区间震荡权利金 |

**期限**：到期日由读者自选——博短线大幅波动选近端到期，趋势跟随选 1-2 月以上。

> ⚠️ 策略基于公开期权链数据，仅供参考，不构成投资建议。期权风险显著高于股票现货，请谨慎评估自身风险承受能力。

---
⚠️ 基于公开期权链聚合，不构成投资建议。
```

### 正例 2 · AAPL（iv_peak=None · 天花板紧贴·下方真空 · 薄墙）

```markdown
# AAPL · 期权聪明钱画像
📅 价格 / IV：2026-05-20 · PCR：2026-05-20

## §1 今日定调

**【天花板紧贴·下方真空】**

AAPL 持仓偏多但结构偏空压顶。PCR **0.703** 处于 **30 日中下位**，Call 持仓略占优。IV **21.9%** 与 HV **22.1%** 基本持平（**-0.2pp**），定价合理。上方 $310 Call Wall 较近（+2.6%），下方 $280 Put Wall 远且薄（-7.3%，OI 仅 **0.9 万张**）——失守现价后下方是 7% 无支撑真空带。

**交易主线**：上方 $310 压顶、下方缺支撑——站上 $310 才打开上行，跌破现价警惕真空下挫，止损要紧。

## §4 波动率视角

- **近端最紧张**：无明显近期 IV 凸点（近端与远端 IV 接近）
- **近端常态**：5-14 天 IV 在 **20%** 附近
- **远端均衡**：30 天后 IV 回到 **23.5%**

→ IV 期限结构平稳，市场无近期事件溢价。
```

### 正例 3 · MSFT（iv_peak=None · 双墙紧夹·窄震荡）

```markdown
# MSFT · 期权聪明钱画像
📅 价格 / IV：2026-05-20 · PCR：2026-05-20

## §1 今日定调

**【双墙紧夹·窄震荡】**

MSFT 持仓偏多（长期常态）。PCR **0.478** 处于 **30 日中位**，Call 长期占优属常态、非异动。IV **29.1%** 与 HV **30.8%** 基本持平（**-1.7pp**），定价**合理**，买卖双方均衡。上方 $440 Call Wall（+4.5%）、下方 $410 Put Wall（-2.6%）双墙紧夹现价，区间锁死。

**交易主线**：双墙锁死、窄区间震荡为主——突破 $440 看上行，跌破 $410 转弱，Max Pain $415 引力中枢回踩可关注。
```

## 参考文件

- [hard-rules.md](references/hard-rules.md) — 数据真实性 + 单位铁律 + 禁用清单 + PCR 时效说明
- [ai-payload-schema.md](references/ai-payload-schema.md) — 字段契约速查（LLM 渲染时使用）
- [ascii-butterfly-template.md](references/ascii-butterfly-template.md) — §3 蝴蝶图绘制规则
- [output-format.md](references/output-format.md) — 5 段 markdown 模板完整示例
- [raw-payload-schema.md](references/raw-payload-schema.md) — `fetch → compute` 内部契约（**仅开发者参考**，LLM 不消费）
