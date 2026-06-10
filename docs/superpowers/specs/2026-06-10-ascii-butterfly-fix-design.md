# ASCII 蝴蝶图回归 spec 修复 · 设计

> 2026-06-10 · 方案 B（回归 spec）已经用户批准

## 问题

真实数据（NOK / DRAM，$0.5 strike 间隔标的）暴露 `compute.py:_render_butterfly_ascii` 两个缺陷：

1. **strike 标签格式 bug**：行模板用 `${k:>4.0f}`，非整数 strike 被四舍五入——NOK 的 Put Wall $13.5 显示成 `$14`（与 Call Wall $14 同名，读者误以为双墙同价）；DRAM 的 $59.5 显示成 `$60`（出现两行 "$60"）。该格式在 spec（`ascii-butterfly-template.md`）里同样存在，bug 是从 spec 固化进代码的。
2. **零 OI 噪音行**：实现里"现价上下相邻 strike 强制保留"（`forced_keep`）绕过 OI ≥ 1.0 万过滤，DRAM 的 $59.5（OI 0.0/0.0）作为 `nearby_below` 被强制渲染成一行纯噪音。spec 的现价标注规则本是**区间判定**（现价落在哪两行之间，箭头标上界行），不依赖相邻 strike 在场——强制保留是实现偏离 spec 引入的。

## 第一性原理

这张图对散户只承担三个职责，由此推导行准入规则：

| 职责 | 推导出的要求 |
|---|---|
| ① 看出 OI 在现价上下的分布 | 每行必须携带信息：关键位 或 OI ≥ 阈值；零 OI 非关键行无存在理由 |
| ② 在梯子里定位现价 | 箭头标注即可完成，不需要专门保留一行 |
| ③ 标出 Wall / Max Pain / 深度集中点 | 标签无歧义：不同 strike 不得显示为同一数字 |

## 设计（方案 B）

改动全部收敛在 `_render_butterfly_ascii` 一个函数 + spec 文档 + 测试。`ai_payload` schema 不变，仅 `ascii` 字符串内容变化；SKILL.md §3"直接 paste"行为不受影响。

### 渲染规则变更（三条）

1. **strike 标签格式**：整数 strike 显示无小数（`$60`），非整数保留一位（`$59.5`）——与 ai_payload strike 单位铁律同口径。右对齐 4 字符宽度不变。
2. **删"现价相邻 strike 强制保留"**：`nearby_above` / `nearby_below` 从 `forced_keep` 与 `chosen` 移除。行准入只剩：关键位（call_wall / put_wall / max_pain / 深度集中点）或 `max(call_oi, put_oi) ≥ BUTTERFLY_MIN_ROW_OI_WAN`。
3. **现价箭头改为对已展示行判定**：箭头标"已展示行中 strike ≥ 现价的最小者"（即现价的上界行）；若现价高于全部展示行 → 标最顶行。该单条规则自然覆盖"现价恰好等于某行 strike"（标该行）与"现价低于全部展示行"（标最底行）两个边界，且与原 `nearby_above` 语义连续——只是判定域从"全部 strike"收窄为"已展示行"，不再依赖相邻 strike 在场。

### spec / 文档同步

- `skills/option-flow/references/ascii-butterfly-template.md`：③ 必保留清单删"现价上下相邻 strike"条目；视觉规则与伪代码中 `:>4.0f` 改为条件格式（整数无小数 / 非整数一位）；修复模板自身的内部矛盾——"现价指向逻辑"的示例（225.32 标在 $225 下界行）与伪代码（标上界行）不一致，统一为上界行，与本次实现对齐。
- `compute.py` docstring（选 strike 算法②条）同步删相邻 strike 表述。
- footer"显示规则"文案不涉及相邻 strike，不改。

### 错误处理

无新增错误路径。`strikes` 长度 < 3 仍由上游必填校验拦截；`selected` 实践中非空（max_pain 必填且必保留）。

### 测试（开发期验证；运行期不加 validator——用户即 validator）

`_render_butterfly_ascii` 此前零覆盖。新增直接调用该函数的单元测试（`tests/test_compute.py`）：

| case | 断言 |
|---|---|
| DRAM 形态（$0.5 间隔，现价 $59.86）| 无重复 strike 标签；零 OI 的 59.5 不出现；箭头在 $60 行 |
| NOK 形态（Put Wall $13.5）| 标签渲染为 `$13.5` 而非 `$14` |
| 整数 strike 回归（SPY 形态）| 整数标的输出格式与现行为一致（防误伤）|
| 箭头边界 | 现价高于所有展示行 → 最顶行；低于所有 → 最底行 |

## 不做的事（YAGNI）

- 不做"现价独立分隔行"重设计（方案 C）——职责②用箭头已满足。
- 不为渲染输出加运行期 validator——skill 形态，用户即 validator（全局模式 G/J）。
