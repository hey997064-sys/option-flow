# ASCII 蝴蝶图回归 spec 修复 · 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `_render_butterfly_ascii` 的非整数 strike 标签四舍五入 bug 与零 OI 强制相邻行噪音，实现回归 spec 并补齐单元测试。

**Architecture:** 全部改动收敛在 `compute.py:_render_butterfly_ascii`（纯渲染函数，无副作用）+ spec 文档同步 + 新增测试类。`ai_payload` schema 不变，仅 `ascii` 字符串内容变化。三条规则变更：① strike 标签条件格式（整数无小数/非整数一位，footer 主刻度同理）；② 删"现价相邻 strike 强制保留"；③ 现价箭头改为"已展示行中 strike ≥ 现价的最小者（上界行），现价高于全部 → 最顶行"。

**Tech Stack:** Python 3 标准库；测试用 `unittest`（repo 既有约定，跑法 `python3 -m unittest ...`）。

**Spec:** `docs/superpowers/specs/2026-06-10-ascii-butterfly-fix-design.md`（已批准）

**分支:** `fix/ascii-butterfly-strike-labels`（已基于 origin/main 创建）

---

## 背景速览（给零上下文工程师）

- 仓库根目录：`/Users/a/projects/_local-marketplace/option-flow/`
- 被改函数：`compute.py:455-574` `_render_butterfly_ascii(oi_distribution, current_price, call_wall, put_wall, max_pain, deep_supports=None, deep_resistances=None) -> str`
  - `oi_distribution` = `{"strikes": [float 升序], "call_oi_wan": [float], "put_oi_wan": [float]}`（单位：万张）
  - `call_wall`/`put_wall`/`max_pain` = `{"strike": float, ...}` 或 `None`
- 现 bug 复现：NOK Put Wall $13.5 渲染成 `$  14`；DRAM 的 $59.5（OI 0/0）作为现价下方相邻 strike 被强制保留成一行 `$  60` 零行；DRAM footer 把 $2.5 主刻度写成 "$2 整数关口"
- 测试文件：`tests/test_compute.py`，顶部已有 `import compute`，新测试类直接追加到文件末尾
- 跑测试：`cd /Users/a/projects/_local-marketplace/option-flow && python3 -m unittest tests.test_compute.TestRenderButterflyAscii -v`

---

### Task 1: strike 标签条件格式 + footer 主刻度格式

**Files:**
- Modify: `compute.py:558`（render_row 行模板）、`compute.py:562`（tick_label）
- Test: `tests/test_compute.py`（文件末尾追加新类）

- [ ] **Step 1.1: 写失败测试**

在 `tests/test_compute.py` 文件末尾（最后一个测试类之后、`if __name__ == "__main__":` 之前若有；没有则直接文件末尾）追加：

```python
# -----------------------------------------------------------------------------
# _render_butterfly_ascii（§3 ASCII 蝴蝶图渲染）
# -----------------------------------------------------------------------------


def _ascii_line_with(text: str, needle: str) -> str:
    """Return the first rendered line containing ``needle`` (raises if absent)."""
    return next(line for line in text.splitlines() if needle in line)


class TestRenderButterflyAscii(unittest.TestCase):
    """渲染规则单元测试（开发期验证；运行期无 validator——用户即 validator）。

    覆盖 2026-06-09 NOK/DRAM 真实数据暴露的 $0.5 间隔边界 case：
    - 非整数 strike 被 .0f 抹掉小数（13.5→14、59.5→60）产生重复/误导标签
    - 零 OI 的现价相邻 strike 被强制保留成纯噪音行
    """

    def test_fractional_strike_label_keeps_one_decimal(self):
        # NOK 形态：Put Wall $13.5 必须渲染为 $13.5，不得与 $14 同名
        out = compute._render_butterfly_ascii(
            {
                "strikes": [13.0, 13.5, 14.0, 15.0],
                "call_oi_wan": [1.7, 0.2, 7.4, 11.5],
                "put_oi_wan": [1.1, 2.3, 6.3, 2.9],
            },
            current_price=13.85,
            call_wall={"strike": 14.0},
            put_wall={"strike": 13.5},
            max_pain={"strike": 14.0},
        )
        self.assertIn("$13.5", out)
        # $13.5 行必须挂 PUT WALL 标注（标签与关键位绑定正确）
        self.assertIn("● PUT WALL", _ascii_line_with(out, "$13.5"))
        # 整数 $14 标签只出现一行（修复前 13.5 也渲染成 "$  14" → 2 行）
        body = [l for l in out.splitlines() if "─────" in l]
        self.assertEqual(sum("$  14 " in l for l in body), 1)

    def test_integer_strike_label_unchanged(self):
        # 整数 strike 回归：格式与修复前一致（防误伤）
        out = compute._render_butterfly_ascii(
            {
                "strikes": [95.0, 100.0, 105.0],
                "call_oi_wan": [1.2, 3.0, 5.0],
                "put_oi_wan": [2.0, 1.5, 1.0],
            },
            current_price=99.5,
            call_wall={"strike": 105.0},
            put_wall={"strike": 95.0},
            max_pain={"strike": 100.0},
        )
        self.assertIn("$ 100", out)
        self.assertIn("$ 105", out)
        self.assertNotIn("100.0 ─", out)  # 整数不得带小数渲染

    def test_footer_tick_label_keeps_fraction(self):
        # cp=99.5 → 主刻度 $2.5（30≤cp<100），footer 不得写成 "$2"
        out = compute._render_butterfly_ascii(
            {
                "strikes": [95.0, 100.0, 105.0],
                "call_oi_wan": [1.2, 3.0, 5.0],
                "put_oi_wan": [2.0, 1.5, 1.0],
            },
            current_price=99.5,
            call_wall={"strike": 105.0},
            put_wall={"strike": 95.0},
            max_pain={"strike": 100.0},
        )
        self.assertIn("$2.5 整数关口", out)
```

- [ ] **Step 1.2: 跑测试确认失败**

```bash
cd /Users/a/projects/_local-marketplace/option-flow && python3 -m unittest tests.test_compute.TestRenderButterflyAscii -v
```

预期：`test_fractional_strike_label_keeps_one_decimal` FAIL（`$13.5` 不在输出里）；`test_footer_tick_label_keeps_fraction` FAIL（footer 是 `$2 整数关口`）；`test_integer_strike_label_unchanged` PASS（基线行为）。

- [ ] **Step 1.3: 最小实现**

`compute.py` render_row（当前 541-558 行）中，在 `tags = []` 之前加一行 strike 标签格式化，并改行模板：

```python
    def render_row(k: float, c: float, p: float) -> str:
        pb = bar_str(p)
        cb = bar_str(c)
        pl = f"{p:.1f}"
        cl = f"{c:.1f}万"
        ks = f"{k:.0f}" if k == int(k) else f"{k:.1f}"  # 单位铁律：整数去小数点、非整数留一位
        tags = []
        if cw is not None and k == cw:
            tags.append("● CALL WALL")
        if pw is not None and k == pw:
            tags.append("● PUT WALL")
        if mp is not None and k == mp:
            tags.append("◆ MAX PAIN")
        if k == nearby_above:
            tags.append(f"← 现价 ${cp:.2f}")
        elif nearby_above is None and selected and k == selected[0][0]:
            tags.append(f"← 现价 ${cp:.2f}")
        tag_str = "  " + "  ".join(tags) if tags else ""
        return f"            {pl:>5} {pb:<6} ───── ${ks:>4} ─────  {cb} {cl}{tag_str}"
```

（本 Task 只改格式两处：新增 `ks` 行 + 行模板 `${k:>4.0f}` → `${ks:>4}`；现价箭头逻辑 Task 2 再动。）

`compute.py:562` footer 主刻度：

```python
    tick_label = f"${TICK:g}"
```

（`:g` 对 10/5/1 输出整数、对 2.5 输出 `2.5`，原来的 `TICK >= 1` 分支不再需要。）

- [ ] **Step 1.4: 跑测试确认通过**

```bash
cd /Users/a/projects/_local-marketplace/option-flow && python3 -m unittest tests.test_compute.TestRenderButterflyAscii -v
```

预期：3 个全 PASS。

- [ ] **Step 1.5: Commit**

```bash
cd /Users/a/projects/_local-marketplace/option-flow && git add compute.py tests/test_compute.py && git commit -m "fix(compute): ASCII strike 标签按单位铁律条件格式化（13.5 不再渲染成 14）

非整数 strike 被 \${k:>4.0f} 四舍五入，NOK Put Wall \$13.5 显示成 \$14
（与 Call Wall 同名）、DRAM \$59.5 显示成 \$60（重复行）。footer 主刻度
\$2.5 同理被写成 \$2。改为整数无小数/非整数留一位。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 删零 OI 强制相邻行 + 现价箭头改上界行判定

**Files:**
- Modify: `compute.py:505-507`（nearby 计算）、`compute.py:523-526`（chosen）、`compute.py:530-532`（forced_keep）、`compute.py:541-560`（render_row 箭头 + 行渲染循环）、`compute.py:464-477`（docstring）
- Test: `tests/test_compute.py`（`TestRenderButterflyAscii` 类内追加）

- [ ] **Step 2.1: 写失败测试**

在 `TestRenderButterflyAscii` 类内追加：

```python
    def test_zero_oi_adjacent_strike_dropped(self):
        # DRAM 形态：现价下方相邻 $59.5 OI 0/0，不得为"相邻"而强制渲染
        out = compute._render_butterfly_ascii(
            {
                "strikes": [55.0, 59.0, 59.5, 60.0, 65.0],
                "call_oi_wan": [0.8, 0.3, 0.0, 1.5, 1.9],
                "put_oi_wan": [1.3, 0.3, 0.0, 0.8, 0.4],
            },
            current_price=59.86,
            call_wall={"strike": 65.0},
            put_wall={"strike": 55.0},
            max_pain={"strike": 59.0},
        )
        self.assertNotIn("59.5", out)
        body = [l for l in out.splitlines() if "─────" in l]
        self.assertEqual(sum("$  60 " in l for l in body), 1)
        # 箭头在上界行 $60（已展示行中 strike ≥ 59.86 的最小者）
        self.assertIn("← 现价 $59.86", _ascii_line_with(out, "$  60"))

    def test_arrow_on_upper_bound_row(self):
        # 现价 99.5 落在 95 与 100 之间 → 箭头标 $100 行（上界行）
        out = compute._render_butterfly_ascii(
            {
                "strikes": [95.0, 100.0, 105.0],
                "call_oi_wan": [1.2, 3.0, 5.0],
                "put_oi_wan": [2.0, 1.5, 1.0],
            },
            current_price=99.5,
            call_wall={"strike": 105.0},
            put_wall={"strike": 95.0},
            max_pain={"strike": 100.0},
        )
        self.assertIn("← 现价 $99.50", _ascii_line_with(out, "$ 100"))

    def test_arrow_when_price_above_all_rows(self):
        # 现价高于全部展示行 → 箭头标最顶行
        out = compute._render_butterfly_ascii(
            {
                "strikes": [50.0, 55.0, 60.0],
                "call_oi_wan": [1.0, 2.0, 3.0],
                "put_oi_wan": [1.0, 2.0, 1.5],
            },
            current_price=70.0,
            call_wall=None,
            put_wall={"strike": 60.0},
            max_pain={"strike": 55.0},
        )
        first_body_line = next(l for l in out.splitlines() if "─────" in l)
        self.assertIn("← 现价 $70.00", first_body_line)

    def test_arrow_when_price_below_all_rows(self):
        # 现价低于全部展示行 → 全部行 strike ≥ cp，最小者 = 最底行
        out = compute._render_butterfly_ascii(
            {
                "strikes": [50.0, 55.0, 60.0],
                "call_oi_wan": [1.0, 2.0, 3.0],
                "put_oi_wan": [1.0, 2.0, 1.5],
            },
            current_price=45.0,
            call_wall={"strike": 50.0},
            put_wall=None,
            max_pain={"strike": 55.0},
        )
        last_body_line = [l for l in out.splitlines() if "─────" in l][-1]
        self.assertIn("← 现价 $45.00", last_body_line)
```

- [ ] **Step 2.2: 跑测试确认失败**

```bash
cd /Users/a/projects/_local-marketplace/option-flow && python3 -m unittest tests.test_compute.TestRenderButterflyAscii -v
```

预期：`test_zero_oi_adjacent_strike_dropped` FAIL（`59.5` 仍被强制保留渲染）。其余 3 个箭头测试在旧逻辑（nearby_above 基于全部 strikes）下也 PASS——它们是新逻辑的回归保护。

- [ ] **Step 2.3: 实现**

`compute.py` 四处改动：

(a) 删 `compute.py:505-507`：

```python
    # 现价上下相邻 strike（基于全集 strikes）
    nearby_below = max((k for k in strikes if k < cp), default=None)
    nearby_above = min((k for k in strikes if k >= cp), default=None)
```

(b) `chosen`（原 523-526 行）收敛为：

```python
    # ③ 必保留 + 主刻度并集
    chosen = (major_ticks | must_keep) & strike_set
```

(c) `forced_keep`（原 528-532 行）收敛为：

```python
    # ④ OI 过滤：每行 max(call_oi, put_oi) ≥ MIN_ROW_OI；
    #    must_keep（Wall / MP / 深度集群）强制保留
    forced_keep = must_keep
```

(d) render_row 与行渲染循环（原 541-560 行）整体替换为：

```python
    def render_row(k: float, c: float, p: float, show_arrow: bool) -> str:
        pb = bar_str(p)
        cb = bar_str(c)
        pl = f"{p:.1f}"
        cl = f"{c:.1f}万"
        ks = f"{k:.0f}" if k == int(k) else f"{k:.1f}"  # 单位铁律：整数去小数点、非整数留一位
        tags = []
        if cw is not None and k == cw:
            tags.append("● CALL WALL")
        if pw is not None and k == pw:
            tags.append("● PUT WALL")
        if mp is not None and k == mp:
            tags.append("◆ MAX PAIN")
        if show_arrow:
            tags.append(f"← 现价 ${cp:.2f}")
        tag_str = "  " + "  ".join(tags) if tags else ""
        return f"            {pl:>5} {pb:<6} ───── ${ks:>4} ─────  {cb} {cl}{tag_str}"

    # 现价箭头：已展示行中 strike ≥ cp 的最小者（上界行）；
    # cp 高于全部 → 最顶行；cp 低于全部 → 全行皆候选 → 最底行。
    arrow_candidates = [i for i, (k, _, _) in enumerate(selected) if k >= cp]
    arrow_idx = max(arrow_candidates) if arrow_candidates else 0
    output_rows: list[str] = [
        render_row(k, c, p, show_arrow=(i == arrow_idx))
        for i, (k, c, p) in enumerate(selected)
    ]
```

（selected 为降序，strike ≥ cp 的行集中在顶部，其中最小者 = 候选里 index 最大者。）

(e) docstring（原 464-477 行）的选 strike 算法②③条改为：

```python
    选 strike 算法（散户友好：主刻度自适应 + 必保留关键位 + OI 阈值过滤）：
      ① 主刻度由 _major_tick_for_price(cp) 决定（$10 / $5 / $2.5 / $1）；
         现价上下各取 BUTTERFLY_TICKS_PER_SIDE 个主刻度整数关口
         （SPY $745.64 → $10 主刻度 → 上 [$750-$790] + 下 [$700-$740]）
      ② 必保留：call_wall / put_wall / max_pain / deep_supports / deep_resistances
         （即使不在主刻度上、即使被 OI 过滤）
      ③ OI 阈值：非必保留行需 max(call_oi, put_oi) ≥ BUTTERFLY_MIN_ROW_OI_WAN
      ④ 选中 strike 按 strike 降序展示，**不插入 gap marker**（strike 数字本身指示跳跃）
      ⑤ 现价箭头标"已展示行中 strike ≥ 现价的最小者"（上界行）；
         现价高于全部展示行 → 标最顶行（低于全部时上界行即最底行）
```

- [ ] **Step 2.4: 跑测试确认通过**

```bash
cd /Users/a/projects/_local-marketplace/option-flow && python3 -m unittest tests.test_compute.TestRenderButterflyAscii -v
```

预期：7 个全 PASS。

- [ ] **Step 2.5: Commit**

```bash
cd /Users/a/projects/_local-marketplace/option-flow && git add compute.py tests/test_compute.py && git commit -m "fix(compute): ASCII 删零 OI 强制相邻行，现价箭头改上界行判定

「现价相邻 strike 强制保留」是实现偏离 spec 引入的——spec 的现价标注
本是区间判定，不依赖相邻 strike 在场。DRAM \$59.5（OI 0/0）因此被渲染
成纯噪音行。行准入收敛为：关键位 或 OI ≥ 1.0 万；箭头标已展示行中
strike ≥ 现价的最小者。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 同步 spec 文档（ascii-butterfly-template.md）

**Files:**
- Modify: `skills/option-flow/references/ascii-butterfly-template.md`

- [ ] **Step 3.1: 改视觉规则模板行（第 27 行）**

```
            {put_label:>5} ▎▎ ──── ${strike_label:>4} ─────  ▎▎▎▎▎▎▎▎ {call_label:<6}  {tag}
```

并在"零柱"条目后追加一条：

```
- **strike 标签**：整数 strike 无小数（`$60`），非整数保留一位（`$59.5`）——与 ai_payload strike 单位铁律同口径，两个不同 strike 不得显示为同一数字
```

- [ ] **Step 3.2: 删③必保留清单中的相邻 strike 条目（第 52 行）**

删除：

```
  - 现价上下相邻 strike：`nearby_above` = min{k : k ≥ cp}，`nearby_below` = max{k : k < cp}
```

- [ ] **Step 3.3: 统一现价指向逻辑（第 73-75 行，原文示例与伪代码矛盾）**

标注规则表的现价行改为：

```
| 现价的上界行（已展示行中 strike ≥ current_price 的最小者）| `← 现价 ${current_price:.2f}`（现价高于全部展示行时标最顶行；低于全部时上界行即最底行）|
```

"现价指向逻辑"段落改为：

```
**现价指向逻辑**：展示行降序 `[..., 230, 225, 220, ...]`，current_price=225.32 → 标在 strike=230 那行（已展示行中 ≥ 225.32 的最小 strike）。若 current_price 高于所有展示行 → 标最顶行；低于所有 → 标最底行（此时全部行 ≥ 现价，最小者即最底行）。
```

- [ ] **Step 3.4: 伪代码同步（第 109-156 行）**

`bar_str` 之后、`lines` 之前的循环部分整体替换为：

```python
    lines = [
        f"持仓分布 · 短期 ≤14d · 现价 ${cp:.2f}",
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
```

- [ ] **Step 3.5: 输出样本箭头位置修正（第 174 行）**

NVDA mock 样本中 `← 现价 $225.32` 从 $225 行移到 $230 行：

```
              2.2 ▎▎     ───── $230 ─────  ▎▎▎▎▎▎▎▎ 8.1万  ← 现价 $225.32
              1.8 ▎      ───── $225 ─────  ▎▎▎ 3.9万
```

- [ ] **Step 3.6: Commit**

```bash
cd /Users/a/projects/_local-marketplace/option-flow && git add skills/option-flow/references/ascii-butterfly-template.md && git commit -m "docs(spec): ascii-butterfly-template 同步实现修复（条件格式 + 删相邻强制保留 + 箭头统一上界行）

修复模板自身内部矛盾：伪代码标上界行、示例却标下界行，统一为上界行。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 全量回归 + 真实数据冒烟

**Files:** 无新改动（验证）

- [ ] **Step 4.1: 全量单元测试**

```bash
cd /Users/a/projects/_local-marketplace/option-flow && python3 -m unittest discover tests -v 2>&1 | tail -5
```

预期：`OK`，0 failures（若有既有测试因渲染输出断言失败，逐个检查是否依赖旧格式——按新格式更新断言，不回退实现）。

- [ ] **Step 4.2: 真实数据冒烟（依赖 longbridge CLI，离线可跳过）**

```bash
cd /Users/a/projects/_local-marketplace/option-flow && python3 option_flow.py NOK.US | python3 -c "
import json,sys
a=json.load(sys.stdin)['key_levels']['oi_distribution']['ascii']
print(a)
assert '\$13.5' in a or '13.5' not in json.dumps(a), 'NOK put wall 13.5 应正确显示'
"
python3 option_flow.py DRAM.US | python3 -c "
import json,sys
a=json.load(sys.stdin)['key_levels']['oi_distribution']['ascii']
print(a)
assert '59.5' not in a, '零 OI 59.5 行应消失'
"
```

预期：NOK 的 ascii 出现 `$13.5 ● PUT WALL` 行且只有一行 `$  14`；DRAM 无 `59.5` 行、只有一行 `$  60`、footer 为 `$2.5 整数关口`。

（注：若当日 Put Wall 已不在 13.5，以"非整数 strike 带一位小数、无重复标签"为验收口径，数字本身随行情变化。）

- [ ] **Step 4.3: 推分支 + 开 PR**

```bash
cd /Users/a/projects/_local-marketplace/option-flow && git push -u origin fix/ascii-butterfly-strike-labels && gh pr create --title "fix: ASCII 蝴蝶图 strike 标签格式 + 零 OI 相邻行（回归 spec）" --body "$(cat <<'EOF'
## 问题
NOK / DRAM（\$0.5 strike 间隔）真实数据暴露 _render_butterfly_ascii 两个缺陷：
1. \`\${k:>4.0f}\` 把非整数 strike 四舍五入——NOK Put Wall \$13.5 显示成 \$14（与 Call Wall 同名）、DRAM \$59.5 显示成 \$60（重复行）；footer 主刻度 \$2.5 同理写成 \$2
2. 「现价相邻 strike 强制保留」绕过 OI 过滤，DRAM \$59.5（OI 0/0）渲染成纯噪音行——该规则是实现偏离 spec 引入的

## 修复（方案 B·回归 spec）
- strike 标签条件格式：整数无小数 / 非整数留一位（与 ai_payload 单位铁律同口径）
- 删相邻 strike 强制保留；行准入 = 关键位 或 OI ≥ 1.0 万
- 现价箭头改"已展示行中 strike ≥ 现价的最小者"（上界行），单条规则覆盖全部边界
- 同步 ascii-butterfly-template.md（并修复其伪代码与示例互相矛盾的现价指向）
- 补 _render_butterfly_ascii 单元测试 7 例（此前零覆盖）

设计文档：docs/superpowers/specs/2026-06-10-ascii-butterfly-fix-design.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review 记录

- **Spec 覆盖**：设计文档"渲染规则变更三条"→ Task 1（格式）/ Task 2（强制行 + 箭头）；"spec/文档同步"→ Task 3 + Task 2 Step 2.3(e)（compute docstring）；"测试四类 case"→ Task 1 测试（NOK 形态、整数回归）+ Task 2 测试（DRAM 形态、箭头两边界）。footer tick_label 是计划阶段新发现的同根因 bug，已并入 Task 1 并在 PR 描述注明。
- **占位符**：无 TBD/TODO；每步含完整代码与命令。
- **类型/命名一致性**：`render_row(k, c, p, show_arrow)` 签名在 Task 2 (d) 定义并在同处使用；`ks` / `strike_label` 分别是 compute.py 与模板伪代码内部局部名，互不引用。
