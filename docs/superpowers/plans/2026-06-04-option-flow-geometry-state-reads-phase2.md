# Geometry-State Reads · Phase 2 (§1/§2/§4/§5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Extend `read_states` with `iv_regime` + `pcr_read`, then upgrade §1 (bold structure label + direction), §2 (PCR/IV meaning columns), §4 (IV-regime→strategy bridge), §5 (named setups + caveat binding) so the whole report — not just §3 — is differentiated and actionable.

**Architecture:** Two new pure-geometry/derived fields in `read_states` (no new data). Prompt rewrites for §1/§2/§4/§5 consume them. The §3 state-reading from Phase 1 is unchanged. Honesty guardrails (OI口径, no gamma) from Phase 1 still apply.

**Tech Stack:** Python 3.14 stdlib (compute.py); unittest; Markdown prompts.

**Spec:** `docs/superpowers/specs/2026-06-04-option-flow-geometry-state-reads-design.md` (§2 patterns, §4.1 fields). Builds on Phase 1 plan (same dir).

---

## File Structure

| File | Change |
|---|---|
| `compute.py` | Add `_iv_regime`, `_pcr_read` helpers; add 2 fields to `_read_states`. |
| `tests/test_compute.py` | Extend `TestReadStates` for the 2 new fields. |
| `skills/option-flow/SKILL.md` | §1 bold label line + direction rewrite; §2 PCR/IV meaning rules; §4 regime-bridge末句; §5 named setups + caveat. Regenerate the 3 正例 (§1/§5). |
| `skills/option-flow/references/output-format.md` | Mirror §1/§2/§4/§5 template changes. |
| `skills/option-flow/references/ai-payload-schema.md` | Document `iv_regime` + `pcr_read`. |

**New read_states fields:**
```python
"iv_regime": "偏贵" | "合理" | "偏便宜",      # 卖方/中性/买方占优
"pcr_read": {
    "direction": "偏多" | "均衡" | "偏空",      # by pcr_oi vs 1.0
    "divergence": bool,                          # abs direction vs 30d rank conflict
    "note": "避险升温" | "看空降温" | "",        # divergence flavor; "" when no divergence
},
```

**New constants** (near Phase 1 constants):
```python
IV_RICH_PP = 3.0            # iv_hv_spread_pp ≥ +3pp → 偏贵；≤ −3pp → 偏便宜；之间 合理
PCR_BULL_MAX = 0.8          # pcr_oi < 0.8 → 偏多(Call主导)
PCR_BEAR_MIN = 1.2          # pcr_oi > 1.2 → 偏空(Put主导)；之间 均衡
PCR_RANK_HIGH = 80.0        # 分位 ≥ 此值算相对高位
PCR_RANK_LOW = 20.0         # 分位 ≤ 此值算相对低位
```

---

## Task A: read_states — iv_regime + pcr_read

**Files:** `compute.py` (helpers + `_read_states` fields), `tests/test_compute.py` (`TestReadStates`).

- [ ] **Step 1: Write the failing test**

Add to `TestReadStates`:
```python
    def test_iv_regime_buckets(self):
        self.assertEqual(compute._iv_regime(8.7), "偏贵")
        self.assertEqual(compute._iv_regime(3.0), "偏贵")    # boundary ≥3
        self.assertEqual(compute._iv_regime(0.0), "合理")
        self.assertEqual(compute._iv_regime(-3.0), "偏便宜")  # boundary ≤-3
        self.assertIsNone(compute._iv_regime(None))

    def test_pcr_read_direction(self):
        self.assertEqual(compute._pcr_read(0.277, 93.1)["direction"], "偏多")
        self.assertEqual(compute._pcr_read(1.0, 50.0)["direction"], "均衡")
        self.assertEqual(compute._pcr_read(2.118, 6.9)["direction"], "偏空")
        self.assertIsNone(compute._pcr_read(None, None))

    def test_pcr_read_divergence(self):
        # call-dominant (偏多) but rank high → 避险升温
        d = compute._pcr_read(0.277, 93.1)
        self.assertTrue(d["divergence"])
        self.assertEqual(d["note"], "避险升温")
        # put-dominant (偏空) but rank low → 看空降温
        d2 = compute._pcr_read(2.118, 6.9)
        self.assertTrue(d2["divergence"])
        self.assertEqual(d2["note"], "看空降温")
        # aligned → no divergence
        d3 = compute._pcr_read(0.5, 30.0)
        self.assertFalse(d3["divergence"])
        self.assertEqual(d3["note"], "")

    def test_phase2_fields_in_output(self):
        out = compute.compute(make_raw(contracts=[]))
        rs = out["read_states"]
        self.assertIn("iv_regime", rs)
        self.assertIn("pcr_read", rs)
```

- [ ] **Step 2: Run to verify fail**

Run: `cd /Users/a/projects/option-flow && python3 -m unittest tests.test_compute.TestReadStates -v`
Expected: FAIL — `AttributeError: module 'compute' has no attribute '_iv_regime'`

- [ ] **Step 3: Add constants + helpers**

Add the 5 constants (above) to the constants block. Add to the `⑤ read_states` section of `compute.py`:
```python
def _iv_regime(iv_hv_spread_pp: float | None) -> str | None:
    """IV−HV 贵贱：≥+3pp 偏贵(卖方占优) / ≤−3pp 偏便宜(买方占优) / 之间 合理。"""
    if iv_hv_spread_pp is None:
        return None
    if iv_hv_spread_pp >= IV_RICH_PP:
        return "偏贵"
    if iv_hv_spread_pp <= -IV_RICH_PP:
        return "偏便宜"
    return "合理"


def _pcr_read(pcr_oi: float | None, rank_pct: float | None) -> dict | None:
    """PCR 方向（绝对值 vs 1.0）+ 与 30 日分位的背离。任一缺失 → None。

    direction: <0.8 偏多(Call 主导) / >1.2 偏空(Put 主导) / 之间 均衡
    divergence: 偏多 但分位高(≥80) → 避险升温；偏空 但分位低(≤20) → 看空降温
    """
    if pcr_oi is None:
        return None
    if pcr_oi < PCR_BULL_MAX:
        direction = "偏多"
    elif pcr_oi > PCR_BEAR_MIN:
        direction = "偏空"
    else:
        direction = "均衡"
    note = ""
    if rank_pct is not None:
        if direction == "偏多" and rank_pct >= PCR_RANK_HIGH:
            note = "避险升温"
        elif direction == "偏空" and rank_pct <= PCR_RANK_LOW:
            note = "看空降温"
    return {"direction": direction, "divergence": bool(note), "note": note}
```

Add the two fields to the `_read_states` return (it already receives `kpi` — if not, add `kpi` param; see wiring note). Update `_read_states` signature and the `compute()` call to pass `kpi`:
```python
def _read_states(current_price, call_wall, put_wall, max_pain, data_quality, kpi):
    ...
    return {
        ...existing Phase 1 fields...,
        "iv_regime": _iv_regime(kpi.get("iv_hv_spread_pp")),
        "pcr_read": _pcr_read(kpi.get("pcr_oi"), kpi.get("pcr_30d_rank_pct")),
    }
```
And in `compute()` update the call site:
```python
    read_states = _read_states(
        current_price, call_wall, put_wall, max_pain, data_quality, kpi,
    )
```

- [ ] **Step 4: Run to verify pass + no regression**

Run: `cd /Users/a/projects/option-flow && python3 -m unittest tests.test_compute -v`
Expected: PASS (all TestReadStates + all prior tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/a/projects/option-flow
git add compute.py tests/test_compute.py
git commit -m "feat(compute): read_states iv_regime + pcr_read (phase 2)"
```

---

## Task B: §2 meaning columns + §4 regime bridge

Prompt-only. No tests.

**Files:** `skills/option-flow/SKILL.md`, `skills/option-flow/references/output-format.md`, `skills/option-flow/references/ai-payload-schema.md`.

- [ ] **Step 1: Document new fields in ai-payload-schema.md**

Append to the `read_states` table in `skills/option-flow/references/ai-payload-schema.md`:
```markdown
| `iv_regime` | 偏贵 / 合理 / 偏便宜 | 偏贵=卖方占优 / 偏便宜=买方占优；§2 IV−HV 行 + §4 末句 + §5 排序消费 |
| `pcr_read` | `{direction:偏多/均衡/偏空, divergence:bool, note:避险升温/看空降温/""}` | §1 方向 + §2 PCR 行消费；direction 由 pcr_oi 绝对值，note 为与分位背离 |
```

- [ ] **Step 2: §2 PCR + IV−HV meaning rules in SKILL.md**

In SKILL.md §2 含义列约束, replace the PCR-row and IV−HV-row guidance with:
```markdown
- PCR 行：用 `read_states.pcr_read.direction`（偏多/均衡/偏空）+ 分位区间词；若 `pcr_read.divergence=true` 追加 note（如"偏多但避险升温"）。≤15 字。
- IV-HV 行：用 `read_states.iv_regime`——偏贵→"偏贵，卖方占优"／合理→"定价合理"／偏便宜→"偏便宜，买方占优"。≤15 字。
```
Mirror the same two bullet rules into `output-format.md` §2 详细约束 (the PCR-row / IV-HV-row lines).

- [ ] **Step 3: §4 regime-bridge末句 in SKILL.md**

In SKILL.md §4 "LLM 末句要求", add a required clause:
```markdown
- **末句必须含 regime→策略桥**：用 `read_states.iv_regime` 给一句操作倾向——偏贵→"卖方收权利金占优，裸买追单吃亏"；偏便宜→"买方占优，做多波动率划算"；合理→"买卖双方均衡，方向比波动率更重要"。（仍不指事件类型，不用 gamma 词）
```
Mirror into `output-format.md` §4 详细约束.

- [ ] **Step 4: Commit**

```bash
cd /Users/a/projects/option-flow
git add skills/option-flow/SKILL.md skills/option-flow/references/
git commit -m "feat(skill): §2 PCR/IV meaning + §4 regime→strategy bridge"
```

---

## Task C: §1 bold structure label + direction rewrite

Prompt-only. Includes regenerating the 3 正例 §1.

**Files:** `skills/option-flow/SKILL.md` (§1 instruction + 正例 1/2/3 §1 blocks), `skills/option-flow/references/output-format.md` (§1 skeleton).

- [ ] **Step 1: Rewrite §1 instruction in SKILL.md**

Replace the SKILL.md "### §1 今日定调（LLM 写）" body with:
```markdown
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
```
Mirror the §1 skeleton (bold label line + body) into `output-format.md` 整体骨架 §1 and §1 详细约束.

- [ ] **Step 2: Regenerate 正例 1 (NVDA) §1**

The NVDA 正例 uses structure `双墙宽松·区间漂移` (Phase 1 §3 already updated). Rewrite its §1 to lead with the bold label. Replace the NVDA 正例 §1 block (`## §1 今日定调` ... ending before `## §2`) with:
```markdown
## §1 今日定调

**【双墙宽松·区间漂移】**

NVDA 散户偏多但 IV 已含溢价。PCR **0.791** 处于 **30 日新低**，Call 持仓占优、散户押上涨。IV 比 HV 高 **+5.5pp**，期权定价**偏贵**，买方追单不划算。现价 $223 上方 $240 Call Wall（+7.4%）、下方 $210 Put Wall（-6.0%）都有空间，区间漂移为主；5/22 单日 IV 飙至 **92.4%**，市场押注当日大幅波动。

**交易主线**：事件前 IV 偏贵不追单；突破 $240 看上行，失守 $210 转弱，区间内跟随突破方向。
```
(Keep numbers consistent with the existing NVDA 正例 KPI/§3 — call $240/+7.4%, put $210/-6.0%, PCR 0.791, +5.5pp. Use $223 as current price implied by those distances; verify against the example's existing §2 Max Pain row and adjust if the example states a different price.)

- [ ] **Step 3: Regenerate 正例 2 (AAPL) + 正例 3 (MSFT) §1**

For AAPL 正例 (currently "中性, Wall 区间偏窄"): lead with its structure label. AAPL geometry in the example: Put Wall thin, narrow range → label `双墙紧夹·窄震荡`. Rewrite §1 first line to `**【双墙紧夹·窄震荡】**` and adjust the opening sentence to lead from it (keep all existing numbers: PCR 0.703 30日中下位, IV 21.9% vs HV 22.1% −0.2pp 合理, walls). 

For MSFT 正例 (currently "中性, 期权定价微便宜"): MSFT geometry narrow, label `双墙紧夹·窄震荡`; IV −1.7pp → 合理 (since |−1.7| < 3, NOT 偏便宜). NOTE: the old MSFT 正例 says "微便宜" / "略有折价" — under the new `iv_regime` (−1.7pp → 合理), update the wording to 合理 to stay consistent with the rule. Rewrite §1 first line to `**【双墙紧夹·窄震荡】**` and fix the pricing clause to "定价合理（−1.7pp，买卖双方均衡）".

- [ ] **Step 4: Commit**

```bash
cd /Users/a/projects/option-flow
git add skills/option-flow/SKILL.md skills/option-flow/references/output-format.md
git commit -m "feat(skill): §1 bold structure label + pcr/iv-driven direction; regen 正例"
```

---

## Task D: §5 named setups + caveat binding

Prompt-only.

**Files:** `skills/option-flow/SKILL.md` (§5 instruction + 正例 1 §5 block), `skills/option-flow/references/output-format.md` (§5).

- [ ] **Step 1: Add named-setup + caveat rules to SKILL.md §5**

In SKILL.md §5, add after the 候选策略表 rules:
```markdown
**方向句**：keyed to `read_states.structure_label` + `pcr_read.direction`——结构定基调（天花板紧贴·下方真空→中性偏空；地板紧贴·上方开阔→中性偏多；双墙紧夹→中性震荡；双墙宽松→跟随突破），PCR 微调。

**"理由"列用具名打法**（不要泛泛"突破看涨"）：把策略绑定到当前结构/水位，如——
- 「天花板压顶·冲高 fade」（卖出 Call / 卖 Strangle，结构=天花板紧贴时）
- 「失守支撑·真空下挫」（裸买 Put，结构=下方真空时）
- 「区间两头收权利金」（卖 Strangle，结构=双墙紧夹时）
- 「突破跟随」（裸买 Call/Put，结构=双墙宽松时）

**IV 排序**：`read_states.iv_regime=偏贵` → 卖方策略（卖 Strangle / 备兑）放表首；偏便宜 → 买方策略放表首。

**caveat 绑定**（必须显式）：
- `read_states.thin_wall=true` → 表末加一行注："⚠️ 墙薄，strike 仅作参考，轻仓。"
- `read_states.max_pain_pull.is_noise=true` → 不要用 Max Pain 作为策略 strike（薄 OI 噪音）。
```
Mirror into `output-format.md` §5 详细约束.

- [ ] **Step 2: Regenerate 正例 1 (NVDA) §5**

Rewrite the NVDA 正例 §5 候选策略表 "理由" column to named setups and add the regime ordering. Keep strikes from ai_payload (call $240, put $210, max_pain $215). Example:
```markdown
## §5 策略推荐

**方向**：中性·跟随突破——双墙宽松上下均有空间（PCR 30 日新低偏多但 IV +5.5pp 已含溢价，单边追涨性价比低）。

**候选策略**：

| 偏好 | 工具 | Strike | 理由 |
|---|---|---|---|
| 卖方 / IV 偏贵首选 | 卖出 Strangle | $210 Put + $240 Call | IV 偏贵，押区间内收权利金 |
| 双向博波动 | 买入 Strangle | $210 Put + $240 Call | 押事件后大幅波动，破任一墙获利 |
| 偏多 | 裸买 Call | $240 | 突破跟随，站上看上行 |
| 偏空 | 裸买 Put | $210 | 跌破支撑·向下跟随 |

**期限**：到期日由读者自选——短线博波动选近端到期，趋势跟随选 1-2 月以上。

> ⚠️ 策略基于公开期权链数据，仅供参考，不构成投资建议。期权风险显著高于股票现货，请谨慎评估自身风险承受能力。
```

- [ ] **Step 3: Commit**

```bash
cd /Users/a/projects/option-flow
git add skills/option-flow/SKILL.md skills/option-flow/references/output-format.md
git commit -m "feat(skill): §5 named setups + caveat binding; regen 正例"
```

---

## Task E: Full-report double-blind verification

**Files:** append to `_test_reports/_phase1_§3_verification.md` (rename concept: phase verification note).

- [ ] **Step 1: Regenerate iv_regime + pcr_read across tickers**

```bash
cd /Users/a/projects/option-flow
for S in NOK NVDA AAPL SPY TSLA; do
  [ -f _dev_payloads/${S}_raw_payload.json ] || continue
  echo "=== $S ==="
  python3 compute.py _dev_payloads/${S}_raw_payload.json 2>/dev/null \
    | python3 -c "import json,sys;rs=json.load(sys.stdin)['read_states'];print(' iv_regime=',rs['iv_regime'],' pcr=',rs['pcr_read'])"
done
```
Eyeball: iv_regime and pcr_read direction/divergence vary sensibly per ticker (NOK 偏多+避险升温; SPY 偏空+看空降温).

- [ ] **Step 2: Double-blind full-report render (模式 K)**

Dispatch a fresh subagent (no context) with ONLY the updated SKILL.md + references + one ticker's ai_payload (use NOK — exercises thin_wall, is_noise, divergence, 偏贵 IV). Ask it to render the FULL 5-section report and self-report ambiguity. Verify: §1 leads with bold 【structure_label】; §2 PCR/IV meaning columns use pcr_read/iv_regime; §4 末句 has regime bridge; §5 uses named setups + thin-wall caveat + does NOT use Max Pain as a strike (is_noise). No banned gamma vocab.

- [ ] **Step 3: Fix any ambiguity, re-render if changed**

Edit SKILL.md/output-format.md inline for any gap; re-dispatch the double-blind for that ticker until clean.

- [ ] **Step 4: Commit verification + fixes**

```bash
cd /Users/a/projects/option-flow
git add -A
git commit -m "test(skill): phase-2 full-report double-blind verification"
```

---

## Self-Review (completed during authoring)

- **Coverage:** iv_regime+pcr_read → Task A; §2/§4 → Task B; §1 label+direction → Task C; §5 named setups+caveat → Task D; verification → Task E. ✅
- **Type consistency:** field names `iv_regime`, `pcr_read.{direction,divergence,note}` identical across tasks + schema. Constants reused. `_read_states` signature gains `kpi` param — call site updated in Task A Step 3. ✅
- **Cross-phase consistency:** MSFT 正例 IV −1.7pp reclassified 微便宜→合理 to match `_iv_regime` rule (|−1.7|<3). NVDA 正例 §1/§5 use the Phase-1 §3 structure 双墙宽松·区间漂移. ✅
- **Honesty:** no gamma vocab introduced; thin_wall/is_noise caveats bound; structure_label still compute-assigned. ✅
- **No placeholders:** all prompt blocks + test code shown in full. ✅
