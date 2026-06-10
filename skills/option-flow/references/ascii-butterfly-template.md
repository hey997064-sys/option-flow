# ASCII 双向蝴蝶图绘制规则

> §3 关键水位段使用，模板填充，**无 LLM**。
>
> **渲染由 `compute.py:_render_butterfly_ascii` 负责**——本文档是算法 spec，SKILL.md §3 直接 paste `ai_payload.key_levels.oi_distribution.ascii` 字段。LLM **不要**照着规则手画 ASCII（实测 158 行抄录会大概率错位）。

## 数据来源

```python
oi_dist = ai_payload["key_levels"]["oi_distribution"]
strikes      = oi_dist["strikes"]       # 升序 float 列表
call_oi_wan  = oi_dist["call_oi_wan"]   # 与 strikes 对齐
put_oi_wan   = oi_dist["put_oi_wan"]

call_wall  = ai_payload["key_levels"]["call_wall"]    # dict or None
put_wall   = ai_payload["key_levels"]["put_wall"]     # dict or None
max_pain   = ai_payload["key_levels"]["max_pain"]     # dict or None
current_price = ai_payload["current_price"]
```

## 视觉规则

```
持仓分布 · ≤14d 多 expiry 合计 · 现价 ${current_price:.2f}

          PUT OI         STRIKE        CALL OI
            {put_label:>5} ▎▎ ──── ${strike_label:>4} ─────  ▎▎▎▎▎▎▎▎ {call_label:<6}  {tag}
            ...
```

- **降序**：strike 从高到低排列（高在上、低在下，符合期权链习惯）
- **左侧 Put OI 柱、右侧 Call OI 柱**，中轴显示 strike
- **柱字符**：`▎` 每个代表 1 万张（floor 取整）
- **半柱**：0 < oi_wan < 1 用 `▏`
- **零柱**：oi_wan = 0 留空（不画 `▏` 也不画 `▎`，保持空格对齐）
- **strike 标签**：整数 strike 无小数（`$60`），非整数保留一位（`$59.5`）——与 ai_payload strike 单位铁律同口径，两个不同 strike 不得显示为同一数字
- **最大柱长 30 字符**（防止超大 OI 把图压扁）：`min(int(oi_wan), 30) * '▎'`
- **数字标签**：bar 外侧紧贴显示 `{oi_wan:.1f}万`
- **strike 选择**（散户友好：主刻度自适应 + 必保留关键位 + OI 阈值过滤）：

  ① **主刻度自适应**（compute.py `_major_tick_for_price`）：
  - `cp ≥ 500` → $10（SPY/SPX/BRKB）
  - `100 ≤ cp < 500` → $5（NVDA/MSFT/META）
  - `30 ≤ cp < 100` → $2.5（多数中盘股）
  - `cp < 30` → $1（低价股 / 微价股）

  ② **现价上下各 N 档主刻度**：`BUTTERFLY_TICKS_PER_SIDE = 5`
  - SPY 现价 $745.64（cp ≥ 500 → $10 主刻度）→ 上方 [$750, $760, $770, $780, $790] + 下方 [$740, $730, $720, $710, $700]

  ③ **必保留**（即使不在主刻度上 / 即使距现价远）：
  - `call_wall.strike` / `put_wall.strike` / `max_pain.strike`
  - `deep_supports[].strike` / `deep_resistances[].strike`（让远端 OI 大点不被砍）
  ④ **OI 阈值过滤**：主刻度行只有 `max(call_oi_wan, put_oi_wan) ≥ BUTTERFLY_MIN_ROW_OI_WAN (= 1.0 万)` 才进图。必保留行不受过滤影响。

  ⑤ **降序展示**：取 ②③ 并集按 strike 降序排列，**不插入 gap marker**（strike 数字本身指示跳跃）。

  ⑥ **Header 明示口径**：「持仓分布 · ≤14d 多 expiry 合计 · 现价 $X」——告诉散户 OI 数字是聚合的（跟友商单 expiry 口径不同）。

  ⑦ **Footer 明示规则**：「显示规则：现价上下各 5 档 ${TICK} 整数关口 + Wall / Max Pain / 深度集中点（OI ≥ 5 万）」。

  设计意图：跟散户期权链直觉对齐。视觉密度稳定（SPY 14 strike + header/footer ≈ 20 行）。深度集中点保证 $710 / $700 这种远端真支撑位不会被砍。

## 标注规则（注解列）

每行末尾可有 0-2 个标注，按以下优先级附加：

| 条件 | 标注 |
|---|---|
| `strike == call_wall.strike` | `● CALL WALL` |
| `strike == put_wall.strike` | `● PUT WALL` |
| `strike == max_pain.strike` | `◆ MAX PAIN` |
| 现价的上界行（已展示行中 strike ≥ current_price 的最小者）| `← 现价 ${current_price:.2f}`（现价高于全部展示行时标最顶行；低于全部时上界行即最底行）|

**现价指向逻辑**：展示行降序 `[..., 230, 225, 220, ...]`，current_price=225.32 → 标在 strike=230 那行（已展示行中 ≥ 225.32 的最小 strike）。若 current_price 高于所有展示行 → 标最顶行；低于所有 → 标最底行（此时全部行 ≥ 现价，最小者即最底行）。

## 列宽（等宽字体下）

- PUT OI 数字：右对齐 5 字符
- 左柱区间：6 字符（最多 6 个 `▎`，溢出说明 OI 过大需 `+`）
- `─────` 分隔：固定 5 个连字符
- STRIKE：`$XX` 标签右对齐 4 字符（整数无小数 / 非整数留一位；5 字符标签如 `112.5` 自然溢出 1 字符）
- `─────` 分隔：固定 5 个连字符
- 右柱区间：可变长度（最多 30 字符）
- CALL OI 数字：左对齐 6 字符
- 标注：空格分隔，可有可无

## 渲染伪代码（仅渲染层）

> 选 strike 准入（主刻度窗口 ∩ OI 过滤 + 必保留）见上文「strike 选择」段；本伪代码假设入参已是选中行。

```python
def render_butterfly(ai_payload) -> str:
    oi_dist = ai_payload["key_levels"]["oi_distribution"]
    strikes = oi_dist["strikes"]
    call_oi = oi_dist["call_oi_wan"]
    put_oi  = oi_dist["put_oi_wan"]
    cp = ai_payload["current_price"]

    call_wall_strike = ai_payload["key_levels"].get("call_wall", {}) or {}
    put_wall_strike  = ai_payload["key_levels"].get("put_wall", {}) or {}
    max_pain_strike  = ai_payload["key_levels"].get("max_pain", {}) or {}
    cw = call_wall_strike.get("strike")
    pw = put_wall_strike.get("strike")
    mp = max_pain_strike.get("strike")

    BAR = "▎"
    HALF = "▏"
    MAX_BARS = 30

    def bar_str(oi_wan: float) -> str:
        if oi_wan == 0:
            return ""
        if oi_wan < 1:
            return HALF
        return BAR * min(int(oi_wan), MAX_BARS)

    lines = [
        f"持仓分布 · ≤14d 多 expiry 合计 · 现价 ${cp:.2f}",
        "",
        "          PUT OI         STRIKE        CALL OI",
    ]

    # Descending iteration: high strikes on top
    pairs = sorted(zip(strikes, call_oi, put_oi), key=lambda x: -x[0])

    # 现价箭头：已展示行中 strike ≥ cp 的最小者（上界行）；cp 高于全部 → 最顶行
    arrow_candidates = [i for i, (k, _, _) in enumerate(pairs) if k >= cp]
    arrow_idx = max(arrow_candidates) if arrow_candidates else 0

    for i, (k, c_oi, p_oi) in enumerate(pairs):
        put_bar  = bar_str(p_oi)
        call_bar = bar_str(c_oi)
        put_label  = f"{p_oi:.1f}"
        call_label = f"{c_oi:.1f}万"
        strike_label = f"{k:.0f}" if k == int(k) else f"{k:.1f}"

        tags = []
        if cw is not None and k == cw:
            tags.append("● CALL WALL")
        if pw is not None and k == pw:
            tags.append("● PUT WALL")
        if mp is not None and k == mp:
            tags.append("◆ MAX PAIN")
        if i == arrow_idx:
            tags.append(f"← 现价 ${cp:.2f}")

        tag_str = "  " + "  ".join(tags) if tags else ""

        line = (
            f"            {put_label:>5} {put_bar:<6} ─────"
            f" ${strike_label:>4} ─────"
            f"  {call_bar} {call_label}{tag_str}"
        )
        lines.append(line)

    # TICKS_PER_SIDE / tick_label 来自选 strike 步骤
    lines.append("")
    lines.append("每 ▎ ≈ 1 万张（OI）。OI = ≤14d 短期所有 expiry 在该 strike 合计。")
    lines.append(f"显示规则：现价上下各 {TICKS_PER_SIDE} 档 {tick_label} 整数关口 + Wall / Max Pain / 深度集中点（OI ≥ 5 万）。")
    return "\n".join(lines)
```

## 输出样本（NVDA mock）

```
持仓分布 · ≤14d 多 expiry 合计 · 现价 $225.32

          PUT OI         STRIKE        CALL OI
              0.0        ───── $250 ─────  ▎▎▎▎▎▎▎▎▎▎▎▎▎▎ 14.3万
              0.5 ▏      ───── $245 ─────  ▎▎▎ 3.2万
              0.5 ▏      ───── $240 ─────  ▎▎▎▎▎▎▎▎▎▎▎▎ 12.7万  ● CALL WALL
              1.1 ▎      ───── $235 ─────  ▎▎▎▎▎▎▎ 7.8万
              2.2 ▎▎     ───── $230 ─────  ▎▎▎▎▎▎▎▎ 8.1万  ← 现价 $225.32
              1.8 ▎      ───── $225 ─────  ▎▎▎ 3.9万
              3.1 ▎▎▎    ───── $220 ─────  ▎▎▎▎▎▎▎ 7.2万
              2.8 ▎▎     ───── $215 ─────  ▎▎▎▎▎ 5.7万  ● PUT WALL
              4.1 ▎▎▎▎   ───── $210 ─────  ▎▎▎▎▎▎▎ 7.5万  ◆ MAX PAIN
              2.0 ▎▎     ───── $205 ─────  ▎▎▎▎ 4.1万
              3.8 ▎▎▎    ───── $200 ─────  ▎▎▎▎ 3.9万

每 ▎ ≈ 1 万张（OI）。OI = ≤14d 短期所有 expiry 在该 strike 合计。
显示规则：现价上下各 5 档 $5 整数关口 + Wall / Max Pain / 深度集中点（OI ≥ 5 万）。
```

## 边界

- `strikes` 过短/为空：渲染层不校验，照常渲染（必填校验在上游 payload 层）
- 所有 OI 都为 0：极端罕见，render 出来就是一堆 `─` 线，可加 `⚠️ 期权流动性极低` 提示
- `current_price` 落在 strikes 范围外：标注画在最近一端
