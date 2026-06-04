# ai_payload 字段速查

> `compute.py` 输出的 `ai_payload` 字典是本 skill 唯一数据源。本文件是 LLM 渲染时的字段速查表。SKILL.md 为主入口，本文件为字段细则；硬规则见 `hard-rules.md`。

## 顶层字段

| 字段 | 类型 | 必填 | 含义 | 用在哪段 |
|---|---|---|---|---|
| `symbol` | str | Y | 全 ticker，如 `"NVDA.US"`；显示时去 `.US` 后缀 | header |
| `current_price` | float | Y | 现价；来自 `data_as_of` 收盘 | §1 / §3 |
| `snapshot_date` | str | Y | 当前 ET 日 (YYYY-MM-DD)，**仅 metadata**，不显示在报告里 | metadata |
| `data_as_of` | str | Y | 价格 / IV 数据 as-of 日（上一交易日 ET）；DTE 计算锚点 | header |
| `pcr_latest_date` | str \| None | N | PCR 最新数据日（broker T+1） | header |

**snapshot_date vs data_as_of 区别**：
- `snapshot_date` = skill 实际运行那一天（ET 日历）
- `data_as_of` = 价格 / IV 数据所属交易日（通常 = `snapshot_date - 1` 工作日）
- 报告 header 只显示 `data_as_of`，因为这是数据真实的归属时间

## `kpi` 字段（§1 + §2 + §5）

| 字段 | 类型 | 必填 | 含义 | 用在哪段 |
|---|---|---|---|---|
| `pcr_oi` | float | Y | Put/Call OI 比；3 位小数显示 | §1 / §2 / §5 |
| `pcr_30d_rank_pct` | float \| None | N | 0-100，PCR 在 30 日里的分位；用 7 档对照表翻译（详见 SKILL.md），不写"第 X 分位" | §1 / §2 / §5 |
| `atm_iv_pct` | float | Y | 30D ATM IV（VIX-style 方差插值口径） | §1 / §2 |
| `hv_pct` | float \| None | N | 30 交易日 HV（CBOE 标准，需 31 条收盘价才能算） | §2 |
| `iv_hv_spread_pp` | float \| None | N | `atm_iv_pct - hv_pct`，**百分点**（pp 不是 %） | §1 / §2 |

**已删除字段**（不要再期望）：
- `pcr_label`（"偏多 / 中性 / 偏空"）→ LLM 自行解读
- `iv_hv_label`（"偏贵 / 合理 / 偏便宜"）→ LLM 自行解读
- `direction_zh` → LLM 自行解读

## `key_levels` 字段（§1 + §2 + §3 + §5）

```
key_levels:
  call_wall: { strike, oi_wan, distance_pct } | None    # 近端阻力（v2 算法：现价上方距 cp 最近、OI ≥ 3 万的 strike）
  put_wall:  { strike, oi_wan, distance_pct } | None    # 近端支撑（v2 算法：现价下方距 cp 最近、OI ≥ 3 万的 strike）
  max_pain:  { strike, distance_pct } | None
  deep_resistances: [{ strike, oi_wan, distance_pct }, ...]  # 现价上方深度阻力（OI ≥ 5 万，最多 3 个）
  deep_supports:    [{ strike, oi_wan, distance_pct }, ...]  # 现价下方深度支撑（OI ≥ 5 万，最多 3 个）
  oi_distribution:
    strikes:     [float, ...]   # 升序
    call_oi_wan: [float, ...]   # 与 strikes 等长
    put_oi_wan:  [float, ...]   # 与 strikes 等长
    ascii:       str            # compute.py 预渲染 ASCII 蝴蝶图（§3 直接 paste）
```

| 子字段 | 含义 | 单位 |
|---|---|---|
| `call_wall.strike` | Call OI 最大行权价（现价**上方**最近一柱）| USD |
| `call_wall.oi_wan` | 该行权价 Call OI（call+put 不混算） | 万张 |
| `call_wall.distance_pct` | (strike - current_price) / current_price × 100 | % |
| `put_wall.*` | 同上，但取现价**下方**最近一柱 | — |
| `max_pain.strike` | Max Pain 引力位（≤14d 多 expiry 合并 OI 求解） | USD |
| `oi_distribution.strikes` | ≤14d 窗口聚合后的 strike 升序数组 | USD |
| `oi_distribution.ascii` | 预渲染 ASCII 蝴蝶图字符串（含 header / 数据行 / footer / 截断注释） | 文本 |
| `deep_supports[]` | Put Wall 之外的远端支撑集中点（OI ≥ 5 万），按距 cp 升序 | 列表 |
| `deep_resistances[]` | Call Wall 之外的远端阻力集中点（OI ≥ 5 万），按距 cp 升序 | 列表 |

**Wall 方向硬约束**（compute.py 保证，LLM 复述不能写反）：
- `call_wall.distance_pct > 0`（必须在现价上方）
- `put_wall.distance_pct < 0`（必须在现价下方）

**`oi_distribution` 窗口**：≤14d 多 expiry 合并 OI 后按 strike 升序输出，用于 §3 ASCII 蝴蝶图。

## `term_structure` 字段（§1 + §4）

```
term_structure:
  iv_peak: { expiry, days_to_expiry, iv_pct, precision="indicative" } | None
  iv_near: { iv_pct, precision="normal" } | None
  iv_far:  { iv_pct, precision="high"   } | None
```

### `iv_peak`（1-14d 单点最大 IV，**条件触发**）

| 字段 | 类型 | 含义 |
|---|---|---|
| `expiry` | str | 该 expiry 日期 (YYYY-MM-DD) |
| `days_to_expiry` | int | 1 ≤ DTE ≤ 14 |
| `iv_pct` | float | 该 expiry 的 ATM IV 百分比数字 |
| `precision` | "indicative" | 单点最大值方差大，仅供定性引用、**不要做差值比较** |

**触发条件**（全部满足才输出，否则 `iv_peak = None`）：
1. `days_to_expiry` ∈ [1, 14]（**排除 0DTE**——Vega→0 让 IV 反算不稳，业界共识不与 30D IV 同口径）
2. `iv_pct ≥ iv_far.iv_pct × 1.3`（"显著压力"门槛，避免平坦面误报）
3. `iv_pct ≤ 200`（IV_SANITY_CEILING，过滤脏数据）

**None 的语义**：近端无明显事件溢价压力（远期与近端 IV 接近）。

### `iv_near`（5-14 天 ATM IV 中位数）

| 字段 | 类型 | 含义 |
|---|---|---|
| `iv_pct` | float | DTE ∈ [5, 14] 所有 expiry 的 ATM IV 中位数 |
| `precision` | "normal" | 可单点引用 |

### `iv_far`（30-180 天 ATM IV 中位数）

| 字段 | 类型 | 含义 |
|---|---|---|
| `iv_pct` | float | DTE ∈ [30, 180] 所有 expiry 的 ATM IV 中位数 |
| `precision` | "high" | 可参与跨字段比较（如与 HV 做差） |

### 精度标记如何使用

- `indicative`（iv_peak）→ 只说"凸到 X%"，不和 iv_far 做减法
- `normal`（iv_near）→ 可单点引用，可参与同精度比较
- `high`（iv_far）→ 可参与跨字段比较（与 HV、与 atm_iv_pct 做差）

**已删除字段**（不要再期望）：`event_bumps`（列表）/ `shape`（contango/backwardation/humped/flat）/ `short_iv_pct` / `mid_iv_pct` / `long_iv_pct` 全部已替换为上述三字段 triad。

## `data_quality` 字段（错误降级判断）

| 字段 | 类型 | 含义 | 触发什么 |
|---|---|---|---|
| `active_strikes` | int | ≤14d 桶内当日 volume > 0 的 strike 数（同 strike Call+Put 算一个） | < 8 → `reliable=false` |
| `reliable` | bool | `active_strikes >= 8` | false → 报告头加 ⚠️ 低流动性 banner |
| **`low_liquidity`** | **bool** | **短桶 max(OI per strike per type) < 1 万张——所有 strike OI 都不足以支撑 Wall / 蝴蝶图 / 策略推荐** | **true → 走拒绝路径，输出冷门标的简报（见 SKILL.md），不出 §1-§5 完整报告** |
| `max_strike_oi_wan` | float | 短桶里单 (strike, type) 组合的最大 OI（万张），冷门判定的核心信号 | 显示在冷门简报里 |
| `contracts_fetched` | int | fetch 抓回来的总合约数（debug 用） | — |
| `is_intraday` | bool | 当前 ET 时间是否在 9:30-16:00 常规盘内 | true → 报告头加 ⚠️ 盘中 banner |
| `pcr_lag_days` | int | PCR 比价格 / IV 数据晚多少天（mechanism: broker T+1） | > 0 → 报告头加 ℹ️ PCR 滞后 banner |

## 字段映射汇总（哪段用哪些字段）

| 段落 | 关键字段 |
|---|---|
| **header** | `symbol` · `data_as_of` · `pcr_latest_date` · `data_quality.{is_intraday, pcr_lag_days, reliable, active_strikes}` |
| **§1 定调** | `current_price` · `kpi.{pcr_oi, pcr_30d_rank_pct, iv_hv_spread_pp}` · `key_levels.{call_wall, put_wall}.strike` · `term_structure.iv_peak`（若非 None）· `read_states.{pcr_read, iv_regime}` |
| **§2 KPI 表** | `kpi.*`（全部）· `key_levels.max_pain.{strike, distance_pct}` · `key_levels.{call,put}_wall.{strike, distance_pct}` · `read_states.{pcr_read, iv_regime}` |
| **§3 关键水位** | `current_price` · `key_levels.oi_distribution.*` · `key_levels.{call,put}_wall.*` · `key_levels.max_pain.*` · `read_states.*` · `data_quality.max_strike_oi_wan` |
| **§4 IV 视角** | `term_structure.{iv_peak, iv_near, iv_far}` |
| **§5 策略推荐** | 方向句：`kpi.{pcr_oi, pcr_30d_rank_pct}` · `current_price` · `key_levels.{call,put}_wall.{strike, distance_pct}` · `key_levels.max_pain.strike`<br>卡片表：`key_levels.{call_wall, put_wall, max_pain}.strike`（Strike 仅可取自这 3 个字段）· `key_levels.{deep_supports, deep_resistances}`（可作目标价参考）· `kpi.iv_hv_spread_pp`（理由列 IV 贵贱判断，可选）<br>期限说明 & 免责语：**模板段，不绑定字段** |

## 降级规则速查

字段缺失 / 为 None / 异常时的处理详见 **SKILL.md「错误降级」表**（这是 SoT，避免多处漂移）。本文件只负责字段契约（哪些必填、哪些可选、单位是什么），不重复降级后果。

## read_states（几何状态派生 · §3 消费）

| 字段 | 取值 | 含义 |
|---|---|---|
| `call_wall_proximity` / `put_wall_proximity` | 逼近 / 中等 / 远离 / null | 现价距该墙：≤2% 逼近 / ≤5% 中等 / >5% 远离 |
| `asymmetry` | 对称 / 偏空真空 / 偏多开阔 / null | 偏空真空=天花板贴、下方远；偏多开阔=地板贴、上方远 |
| `call_wall_thickness` / `put_wall_thickness` | 厚 / 中 / 薄 / null | <3万 薄 / [3,10)万 中 / ≥10万 厚 |
| `thin_wall` | true / false | 任一墙薄 → §3 必出薄墙 caveat 行 |
| `max_pain_pull` | `{side:上方/下方/重合, is_noise:bool}` / null | side=Max Pain 相对现价；is_noise=薄 OI |
| `structure_label` | 5 值之一 / null | 纯墙几何分类，§3 结构判定行直接引用，**禁改名/自创** |
| `iv_regime` | 偏贵 / 合理 / 偏便宜 | 偏贵=卖方占优 / 偏便宜=买方占优；§2 IV−HV 行 + §4 末句 + §5 排序消费 |
| `pcr_read` | `{direction:偏多/均衡/偏空, divergence:bool, note:避险升温/看空降温/""}` | §1 方向 + §2 PCR 行消费；direction 由 pcr_oi 绝对值，note 为与分位背离 |

`structure_label` 5 值：天花板紧贴·下方真空 / 地板紧贴·上方开阔 / 双墙紧夹·窄震荡 / 双墙宽松·区间漂移 / null（任一墙缺失）。
