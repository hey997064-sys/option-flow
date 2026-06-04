# Geometry-State Reads (option-flow §3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `read_states` derived layer to `compute.py` and rewrite the §3 key-levels section of the option-flow skill so each ticker's support/resistance read is state-dependent and differentiated instead of a "price sandwiched between walls" template.

**Architecture:** `read_states` is a pure-geometry derived dict (no new data, no IO) computed from the already-built `key_levels` + `data_quality`. It classifies each wall's proximity to spot (逼近/中等/远离), wall-distance asymmetry, wall thickness (OI magnitude), a thin-wall flag, max-pain pull side, and a 5-value geometry `structure_label`. The skill's §3 prompt consumes these to produce a per-wall state reading + OI mechanism sentence + structure verdict + thin-wall caveat. Phase 1 touches §3 only; §1/§4/§5 are deferred (recorded in the spec).

**Tech Stack:** Python 3.14 stdlib only (matches `compute.py` — pure functions, no deps). Tests via `unittest` (`python3 -m unittest discover tests`). Skill prompt is Markdown.

**Spec:** `docs/superpowers/specs/2026-06-04-option-flow-geometry-state-reads-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `compute.py` | Pure `raw_payload → ai_payload`. Add `read_states` derived block + its helper functions; wire into `compute()` return. | Modify |
| `tests/test_compute.py` | unittest suite. Add `TestReadStates` covering each helper + integration wiring + boundary cases. | Modify |
| `skills/option-flow/SKILL.md` | §3 prompt rewritten to state-reading; add `structure_label` usage + OI-vs-gamma honesty guardrails. | Modify |
| `skills/option-flow/references/output-format.md` | §3 template updated. | Modify |
| `skills/option-flow/references/hard-rules.md` | Add OI-口径 guardrail + banned gamma vocabulary. | Modify |
| `skills/option-flow/references/ai-payload-schema.md` | Document `read_states` field contract. | Modify |

**Constants** (add near existing `WALL_MIN_OI_WAN` at `compute.py:56-60`):
```python
PROXIMITY_NEAR_PCT = 2.0        # |distance_pct| ≤ 此值 → 逼近
PROXIMITY_MID_PCT = 5.0         # |distance_pct| ≤ 此值 → 中等；> → 远离
ASYMMETRY_RATIO = 2.5           # 一侧墙距 ≥ 此倍数另一侧 → 不对称
WALL_THICK_WAN = 10.0           # oi_wan ≥ 此值 → 厚
# 薄/中 边界复用 WALL_MIN_OI_WAN (3.0)：< 3.0 → 薄，[3.0, 10.0) → 中
```

**`read_states` field shape** (top-level key in `ai_payload`, used by every later task — fix names now):
```python
{
    "call_wall_proximity": "逼近" | "中等" | "远离" | None,
    "put_wall_proximity":  "逼近" | "中等" | "远离" | None,
    "asymmetry":           "对称" | "偏空真空" | "偏多开阔" | None,
    "call_wall_thickness": "厚" | "中" | "薄" | None,
    "put_wall_thickness":  "厚" | "中" | "薄" | None,
    "thin_wall":           bool,
    "max_pain_pull":       {"side": "上方" | "下方" | "重合", "is_noise": bool} | None,
    "structure_label":     "天花板紧贴·下方真空" | "地板紧贴·上方开阔"
                           | "双墙紧夹·窄震荡" | "双墙宽松·区间漂移" | None,
}
```

---

## Task 1: Proximity classification + wiring

Add `_proximity()` and a `_read_states()` assembler wired into `compute()`. This task delivers the `read_states` key with only proximity populated; later tasks fill the rest.

**Files:**
- Modify: `compute.py` (constants near `:56`; new helpers in a new `# ⑤ read_states` section after the `④ data_quality` helpers ~`:240`; wire into `compute()` return at `:174-184`)
- Test: `tests/test_compute.py` (append `TestReadStates`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_compute.py`:
```python
# -----------------------------------------------------------------------------
# ⑤ read_states
# -----------------------------------------------------------------------------


class TestReadStates(unittest.TestCase):

    def test_proximity_buckets(self):
        self.assertEqual(compute._proximity(0.9), "逼近")
        self.assertEqual(compute._proximity(-2.0), "逼近")   # boundary ≤2 → 逼近
        self.assertEqual(compute._proximity(3.5), "中等")
        self.assertEqual(compute._proximity(-5.0), "中等")   # boundary ≤5 → 中等
        self.assertEqual(compute._proximity(8.0), "远离")
        self.assertIsNone(compute._proximity(None))

    def test_read_states_present_in_output(self):
        out = compute.compute(make_raw(contracts=[]))
        self.assertIn("read_states", out)
        # No walls in an empty payload → proximity None
        self.assertIsNone(out["read_states"]["call_wall_proximity"])
        self.assertIsNone(out["read_states"]["put_wall_proximity"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/a/projects/option-flow && python3 -m unittest tests.test_compute.TestReadStates -v`
Expected: FAIL — `AttributeError: module 'compute' has no attribute '_proximity'`

- [ ] **Step 3: Add constants**

In `compute.py`, after the line `ACTIVE_STRIKES_RELIABLE = 8` (`:60`), add:
```python
PROXIMITY_NEAR_PCT = 2.0        # |distance_pct| ≤ 此值 → 逼近
PROXIMITY_MID_PCT = 5.0         # |distance_pct| ≤ 此值 → 中等；> → 远离
ASYMMETRY_RATIO = 2.5           # 一侧墙距 ≥ 此倍数另一侧 → 不对称
WALL_THICK_WAN = 10.0           # oi_wan ≥ 此值 → 厚（薄/中边界复用 WALL_MIN_OI_WAN=3.0）
```

- [ ] **Step 4: Implement `_proximity` + `_read_states` (proximity only)**

Add a new section to `compute.py` immediately before the `# helpers` divider (just before `def _round`):
```python
# -----------------------------------------------------------------------------
# ⑤ read_states — 纯几何派生（现价 vs 各水位的状态读法，§3 消费）
# -----------------------------------------------------------------------------


def _proximity(distance_pct: float | None) -> str | None:
    """|distance_pct| 分档：≤2% 逼近 / ≤5% 中等 / >5% 远离。None → None。"""
    if distance_pct is None:
        return None
    d = abs(distance_pct)
    if d <= PROXIMITY_NEAR_PCT:
        return "逼近"
    if d <= PROXIMITY_MID_PCT:
        return "中等"
    return "远离"


def _read_states(
    current_price: float,
    call_wall: dict | None,
    put_wall: dict | None,
    max_pain: dict | None,
    data_quality: dict,
) -> dict:
    """key_levels + data_quality → 几何状态。无新数据、无 IO。"""
    return {
        "call_wall_proximity": _proximity(call_wall["distance_pct"]) if call_wall else None,
        "put_wall_proximity": _proximity(put_wall["distance_pct"]) if put_wall else None,
        "asymmetry": None,
        "call_wall_thickness": None,
        "put_wall_thickness": None,
        "thin_wall": False,
        "max_pain_pull": None,
        "structure_label": None,
    }
```

Then wire it into the `compute()` return. Change the return block at `:174-184` from:
```python
    return {
        "symbol": symbol,
        "current_price": current_price,
        "snapshot_date": snapshot_date,
        "data_as_of": data_as_of,
        "pcr_latest_date": pcr_latest_date,
        "kpi": kpi,
        "key_levels": key_levels,
        "term_structure": term_structure,
        "data_quality": data_quality,
    }
```
to (add `read_states` after `data_quality` is built — it is already in scope by `:172`):
```python
    read_states = _read_states(
        current_price, call_wall, put_wall, max_pain, data_quality,
    )

    return {
        "symbol": symbol,
        "current_price": current_price,
        "snapshot_date": snapshot_date,
        "data_as_of": data_as_of,
        "pcr_latest_date": pcr_latest_date,
        "kpi": kpi,
        "key_levels": key_levels,
        "term_structure": term_structure,
        "data_quality": data_quality,
        "read_states": read_states,
    }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/a/projects/option-flow && python3 -m unittest tests.test_compute.TestReadStates -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
cd /Users/a/projects/option-flow
git add compute.py tests/test_compute.py
git commit -m "feat(compute): add read_states with wall proximity"
```

---

## Task 2: Asymmetry + wall thickness + thin_wall flag

**Files:**
- Modify: `compute.py` (add `_asymmetry`, `_thickness`; fill those fields in `_read_states`)
- Test: `tests/test_compute.py` (extend `TestReadStates`)

- [ ] **Step 1: Write the failing test**

Add these methods to `TestReadStates`:
```python
    def test_thickness_buckets(self):
        self.assertEqual(compute._thickness({"oi_wan": 2.6}), "薄")   # < 3.0
        self.assertEqual(compute._thickness({"oi_wan": 3.0}), "中")   # boundary
        self.assertEqual(compute._thickness({"oi_wan": 9.9}), "中")
        self.assertEqual(compute._thickness({"oi_wan": 10.0}), "厚")  # boundary
        self.assertIsNone(compute._thickness(None))

    def test_asymmetry_bearish_vacuum(self):
        # call near (+0.9%), put far (-8%) → ceiling tight, floor vacuum
        cw = {"distance_pct": 0.9}
        pw = {"distance_pct": -8.0}
        self.assertEqual(compute._asymmetry(cw, pw), "偏空真空")

    def test_asymmetry_bullish_open(self):
        # put near (-1%), call far (+9%) → floor tight, upside open
        self.assertEqual(
            compute._asymmetry({"distance_pct": 9.0}, {"distance_pct": -1.0}),
            "偏多开阔",
        )

    def test_asymmetry_symmetric(self):
        # 4% vs 6% → ratio 1.5 < 2.5 → 对称
        self.assertEqual(
            compute._asymmetry({"distance_pct": 6.0}, {"distance_pct": -4.0}),
            "对称",
        )

    def test_asymmetry_none_when_wall_missing(self):
        self.assertIsNone(compute._asymmetry(None, {"distance_pct": -4.0}))

    def test_thin_wall_flag(self):
        # Build a payload whose walls are thin (< 3万 OI) and assert thin_wall.
        out = compute.compute(make_raw(contracts=_walls_payload(
            call_oi=20000, put_oi=15000)))  # 2.0万 / 1.5万 → both 薄
        self.assertTrue(out["read_states"]["thin_wall"])
        self.assertEqual(out["read_states"]["call_wall_thickness"], "薄")
```

Also add this builder helper near the other builders (after `make_raw`, ~`:101`):
```python
def _walls_payload(*, call_oi: int, put_oi: int,
                   call_strike: float = 105.0, put_strike: float = 95.0):
    """Short-bucket contracts producing one call wall (above) + one put wall (below)."""
    expiry = _expiry_from_dte(7)
    base = dict(expiry=expiry, days_to_expiry=7, bucket="short",
                volume=100, implied_volatility=0.30)
    return [
        {"type": "call", "strike": call_strike, "open_interest": call_oi, **base},
        {"type": "put", "strike": put_strike, "open_interest": put_oi, **base},
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/a/projects/option-flow && python3 -m unittest tests.test_compute.TestReadStates -v`
Expected: FAIL — `AttributeError: module 'compute' has no attribute '_thickness'`

- [ ] **Step 3: Implement `_asymmetry` + `_thickness`, fill `_read_states`**

Add to the `read_states` section of `compute.py` (after `_proximity`):
```python
def _thickness(wall: dict | None) -> str | None:
    """墙厚 = OI 量级（机制强度）：<3万 薄 / [3,10)万 中 / ≥10万 厚。None → None。"""
    if not wall:
        return None
    oi = wall["oi_wan"]
    if oi < WALL_MIN_OI_WAN:
        return "薄"
    if oi < WALL_THICK_WAN:
        return "中"
    return "厚"


def _asymmetry(call_wall: dict | None, put_wall: dict | None) -> str | None:
    """墙距对称度。一侧 ≥ 2.5× 另一侧 → 不对称（近端定调）。任一墙缺失 → None。

    call 近 put 远 → 偏空真空（天花板压顶、下方踩空）
    put 近 call 远 → 偏多开阔（地板托底、上方开阔）
    """
    if not call_wall or not put_wall:
        return None
    cd = abs(call_wall["distance_pct"])
    pd = abs(put_wall["distance_pct"])
    lo = min(cd, pd) or 0.01           # 防除零；一侧贴现价时视为极不对称
    if max(cd, pd) / lo >= ASYMMETRY_RATIO:
        return "偏空真空" if cd < pd else "偏多开阔"
    return "对称"
```

Then update the `_read_states` return to fill the three fields:
```python
    call_thick = _thickness(call_wall)
    put_thick = _thickness(put_wall)
    return {
        "call_wall_proximity": _proximity(call_wall["distance_pct"]) if call_wall else None,
        "put_wall_proximity": _proximity(put_wall["distance_pct"]) if put_wall else None,
        "asymmetry": _asymmetry(call_wall, put_wall),
        "call_wall_thickness": call_thick,
        "put_wall_thickness": put_thick,
        "thin_wall": "薄" in (call_thick, put_thick),
        "max_pain_pull": None,
        "structure_label": None,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/a/projects/option-flow && python3 -m unittest tests.test_compute.TestReadStates -v`
Expected: PASS (all TestReadStates tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/a/projects/option-flow
git add compute.py tests/test_compute.py
git commit -m "feat(compute): read_states asymmetry + wall thickness + thin_wall"
```

---

## Task 3: max_pain_pull

**Files:**
- Modify: `compute.py` (add `_max_pain_pull`; fill field in `_read_states`)
- Test: `tests/test_compute.py` (extend `TestReadStates`)

- [ ] **Step 1: Write the failing test**

Add to `TestReadStates`:
```python
    def test_max_pain_pull_side(self):
        self.assertEqual(
            compute._max_pain_pull({"strike": 110.0}, 100.0, 12.0)["side"], "上方")
        self.assertEqual(
            compute._max_pain_pull({"strike": 90.0}, 100.0, 12.0)["side"], "下方")
        self.assertEqual(
            compute._max_pain_pull({"strike": 100.0}, 100.0, 12.0)["side"], "重合")

    def test_max_pain_pull_noise_flag(self):
        # max_strike_oi_wan < 3.0 → noise
        self.assertTrue(compute._max_pain_pull({"strike": 90.0}, 100.0, 2.6)["is_noise"])
        self.assertFalse(compute._max_pain_pull({"strike": 90.0}, 100.0, 12.0)["is_noise"])

    def test_max_pain_pull_none_when_missing(self):
        self.assertIsNone(compute._max_pain_pull(None, 100.0, 12.0))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/a/projects/option-flow && python3 -m unittest tests.test_compute.TestReadStates -v`
Expected: FAIL — `AttributeError: module 'compute' has no attribute '_max_pain_pull'`

- [ ] **Step 3: Implement `_max_pain_pull`, fill field**

Add to the `read_states` section (after `_asymmetry`):
```python
def _max_pain_pull(
    max_pain: dict | None,
    current_price: float,
    max_strike_oi_wan: float | None,
) -> dict | None:
    """Max Pain 相对现价的引力方向 + 是否薄 OI 噪音。max_pain 缺失 → None。"""
    if not max_pain:
        return None
    strike = max_pain["strike"]
    if abs(strike - current_price) < 1e-9:
        side = "重合"
    elif strike > current_price:
        side = "上方"
    else:
        side = "下方"
    return {"side": side, "is_noise": (max_strike_oi_wan or 0) < WALL_MIN_OI_WAN}
```

Update the `_read_states` return line for `max_pain_pull`:
```python
        "max_pain_pull": _max_pain_pull(
            max_pain, current_price, data_quality.get("max_strike_oi_wan")),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/a/projects/option-flow && python3 -m unittest tests.test_compute.TestReadStates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/a/projects/option-flow
git add compute.py tests/test_compute.py
git commit -m "feat(compute): read_states max_pain_pull"
```

---

## Task 4: structure_label (pure geometry)

**Files:**
- Modify: `compute.py` (add `_structure_label`; fill field in `_read_states`)
- Test: `tests/test_compute.py` (extend `TestReadStates`)

- [ ] **Step 1: Write the failing test**

Add to `TestReadStates`:
```python
    def test_structure_label_bearish_vacuum(self):
        self.assertEqual(
            compute._structure_label(
                {"distance_pct": 0.9}, {"distance_pct": -8.0}, "偏空真空"),
            "天花板紧贴·下方真空")

    def test_structure_label_bullish_open(self):
        self.assertEqual(
            compute._structure_label(
                {"distance_pct": 9.0}, {"distance_pct": -1.0}, "偏多开阔"),
            "地板紧贴·上方开阔")

    def test_structure_label_tight_range(self):
        # symmetric, both within 5% → 窄震荡
        self.assertEqual(
            compute._structure_label(
                {"distance_pct": 1.5}, {"distance_pct": -2.0}, "对称"),
            "双墙紧夹·窄震荡")

    def test_structure_label_loose_drift(self):
        # symmetric, one wall 远离 (>5%) → 区间漂移
        self.assertEqual(
            compute._structure_label(
                {"distance_pct": 7.0}, {"distance_pct": -6.0}, "对称"),
            "双墙宽松·区间漂移")

    def test_structure_label_none_when_wall_missing(self):
        self.assertIsNone(
            compute._structure_label(None, {"distance_pct": -4.0}, None))

    def test_structure_label_integration_nok_like(self):
        # call wall +0.9% thin, put wall -8% thin → 偏空真空 structure
        out = compute.compute(make_raw(
            current_price=16.85,
            contracts=_walls_payload(
                call_oi=26000, put_oi=17000,
                call_strike=17.0, put_strike=15.5)))
        rs = out["read_states"]
        self.assertEqual(rs["structure_label"], "天花板紧贴·下方真空")
        self.assertTrue(rs["thin_wall"])
        self.assertEqual(rs["call_wall_proximity"], "逼近")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/a/projects/option-flow && python3 -m unittest tests.test_compute.TestReadStates -v`
Expected: FAIL — `AttributeError: module 'compute' has no attribute '_structure_label'`

- [ ] **Step 3: Implement `_structure_label`, fill field**

Add to the `read_states` section (after `_max_pain_pull`):
```python
def _structure_label(
    call_wall: dict | None,
    put_wall: dict | None,
    asymmetry: str | None,
) -> str | None:
    """纯墙几何分类（Phase 1，5 值）。任一墙缺失 → None（交 §3 缺墙路径处理）。

    规则（确定性，仅依赖 proximity + asymmetry）：
      1. 任一墙缺失 → None
      2. 偏空真空 → 天花板紧贴·下方真空；偏多开阔 → 地板紧贴·上方开阔
      3. 对称：任一墙 远离 → 双墙宽松·区间漂移；否则 → 双墙紧夹·窄震荡
    """
    if not call_wall or not put_wall:
        return None
    if asymmetry == "偏空真空":
        return "天花板紧贴·下方真空"
    if asymmetry == "偏多开阔":
        return "地板紧贴·上方开阔"
    cp = _proximity(call_wall["distance_pct"])
    pp = _proximity(put_wall["distance_pct"])
    if "远离" in (cp, pp):
        return "双墙宽松·区间漂移"
    return "双墙紧夹·窄震荡"
```

Update the `_read_states` return. Compute `asymmetry` once into a local and reuse for both `asymmetry` field and `_structure_label`:
```python
    asymmetry = _asymmetry(call_wall, put_wall)
    call_thick = _thickness(call_wall)
    put_thick = _thickness(put_wall)
    return {
        "call_wall_proximity": _proximity(call_wall["distance_pct"]) if call_wall else None,
        "put_wall_proximity": _proximity(put_wall["distance_pct"]) if put_wall else None,
        "asymmetry": asymmetry,
        "call_wall_thickness": call_thick,
        "put_wall_thickness": put_thick,
        "thin_wall": "薄" in (call_thick, put_thick),
        "max_pain_pull": _max_pain_pull(
            max_pain, current_price, data_quality.get("max_strike_oi_wan")),
        "structure_label": _structure_label(call_wall, put_wall, asymmetry),
    }
```

- [ ] **Step 4: Run the full compute suite to verify pass + no regression**

Run: `cd /Users/a/projects/option-flow && python3 -m unittest tests.test_compute -v`
Expected: PASS (all TestReadStates + all pre-existing compute tests green)

- [ ] **Step 5: Commit**

```bash
cd /Users/a/projects/option-flow
git add compute.py tests/test_compute.py
git commit -m "feat(compute): read_states structure_label (geometry)"
```

---

## Task 5: §3 prompt + docs rewrite

No pytest here — these are Markdown prompt changes. Verification is the regeneration in Task 6.

**Files:**
- Modify: `skills/option-flow/references/ai-payload-schema.md`
- Modify: `skills/option-flow/references/hard-rules.md`
- Modify: `skills/option-flow/SKILL.md`
- Modify: `skills/option-flow/references/output-format.md`

- [ ] **Step 1: Document `read_states` in ai-payload-schema.md**

Append a new section to `skills/option-flow/references/ai-payload-schema.md`:
```markdown
## read_states（几何状态派生 · §3 消费）

| 字段 | 取值 | 含义 |
|---|---|---|
| `call_wall_proximity` / `put_wall_proximity` | 逼近 / 中等 / 远离 / null | 现价距该墙：≤2% 逼近 / ≤5% 中等 / >5% 远离 |
| `asymmetry` | 对称 / 偏空真空 / 偏多开阔 / null | 偏空真空=天花板贴、下方远；偏多开阔=地板贴、上方远 |
| `call_wall_thickness` / `put_wall_thickness` | 厚 / 中 / 薄 / null | <3万 薄 / [3,10)万 中 / ≥10万 厚 |
| `thin_wall` | true / false | 任一墙薄 → §3 必出薄墙 caveat 行 |
| `max_pain_pull` | `{side:上方/下方/重合, is_noise:bool}` / null | side=Max Pain 相对现价；is_noise=薄 OI |
| `structure_label` | 5 值之一 / null | 纯墙几何分类，§3 结构判定行直接引用，**禁改名/自创** |

`structure_label` 5 值：天花板紧贴·下方真空 / 地板紧贴·上方开阔 / 双墙紧夹·窄震荡 / 双墙宽松·区间漂移 / null（任一墙缺失）。
```

- [ ] **Step 2: Add OI-口径 guardrail to hard-rules.md**

Append to the 禁止/禁用 section of `skills/option-flow/references/hard-rules.md`:
```markdown
## 机制叙事 · OI 口径护栏（§3 状态读法）

本产品的水位口径是 **持仓量（OI）**，不是做市商 gamma。机制叙事只能用 OI 语言：
> 持仓集中在某 strike → 做市商日常对冲调仓量大 → 调仓时搬动股价 → 形成引力 / 阻力 / 支撑；持仓薄 → 调仓量小 → 引力弱。

**禁用词**（gamma 口径，会误导且我们无此数据）：做市商 gamma / GEX / Gamma Flip / Zero Gamma / vanna / charm / 负 gamma / 正 gamma regime。

**禁止伪造** regime 拐点价位（如"波动率触发位""零 gamma 位"）——我们没有该数据。

`structure_label` 由 compute 指派，LLM **不自创、不改名、不新增类别**；机制方向措辞从简，只说"持仓集中处形成引力/阻力/支撑"，不展开做市商 delta 对冲方向细节（易写反）。
```

- [ ] **Step 3: Rewrite §3 in SKILL.md**

In `skills/option-flow/SKILL.md`, replace the `### §3 关键水位（纯模板 · 无 LLM）` section body. The ASCII paste rule stays; the bullet list becomes a state-driven reading. New section body:
```markdown
### §3 关键水位（ASCII 纯模板 + LLM 状态读法 bullet）

**ASCII**：直接 paste `key_levels.oi_distribution.ascii`，整段包在代码块里（compute 预渲染，LLM 不画不抄）。

**bullet（LLM 写，消费 `read_states`）**：从"报数字"升级为「水位 + 现价相对状态 + 状态读法 + OI 机制」。每条骨架：

- **上方阻力 ${call_wall.strike}** · 现价{call_wall_proximity}（{call_wall.distance_pct:+.1f}%）→ {按 proximity 选读法}；持仓 {call_wall.oi_wan} 万张。
- **下方支撑 ${put_wall.strike}** · 现价{put_wall_proximity}（{put_wall.distance_pct:+.1f}%）→ {按 proximity 选读法}；持仓 {put_wall.oi_wan} 万张。
- **Max Pain ${max_pain.strike}** 引力中枢（{max_pain_pull.side}，{max_pain.distance_pct:+.1f}%）{若与某 Wall 同 strike 补"与 X Wall 重合"}。
- **结构判定**：{structure_label} = {一句话方向含义，如"偏空压顶（非震荡）"}。
- 深度支撑 / 阻力：{deep_supports / deep_resistances 非空时列出，为空省略}。
- {若 `read_states.thin_wall = true`}：⚠️ 单 strike 最大持仓仅 {data_quality.max_strike_oi_wan} 万张，墙薄、引力弱，仅供参考。

**proximity → 读法对照**：
| proximity | 读法 |
|---|---|
| 逼近（≤2%） | "最高信号区"：持仓集中、冲高/杀跌易停滞回落；给前向触发"站上/失守 $X 才转向" |
| 中等（2-5%） | 该位是可达的近端目标/阻力，描述到位的空间 |
| 远离（>5%） | 强调"现价到该墙之间是 N% 无持仓真空 / 缓冲带"，失守/突破后无支撑/无压力 |

**机制句**：OI 口径——持仓集中→做市商调仓量大→搬动股价→引力；薄则弱。**禁 gamma 措辞**（见 hard-rules）。

**结构判定行**：直接引用 `read_states.structure_label`（5 值，禁改名）。任一墙缺失（label=null）走 Wall 缺失细则，不写结构判定行。
```

- [ ] **Step 4: Update §3 template in output-format.md**

In `skills/option-flow/references/output-format.md`, replace the §3 bullet block (the 4 lines under the ASCII fenced block, starting `- 上方阻力 **${call_wall.strike}**`) with the state-driven bullets matching SKILL.md Step 3:
```markdown
- **上方阻力 ${call_wall.strike}** · 现价{call_wall_proximity}（{call_wall.distance_pct:+.1f}%）→ {状态读法}；持仓 **{call_wall.oi_wan} 万张**
- **下方支撑 ${put_wall.strike}** · 现价{put_wall_proximity}（{put_wall.distance_pct:+.1f}%）→ {状态读法}；持仓 **{put_wall.oi_wan} 万张**
- **Max Pain ${max_pain.strike}** 引力中枢（{max_pain_pull.side}，{max_pain.distance_pct:+.1f}%）
- **结构判定**：{read_states.structure_label} = {一句方向含义}
- 深度支撑 / 阻力：{deep_supports[] · deep_resistances[]，任一为空省略}
- （thin_wall 时）⚠️ 单 strike 最大持仓仅 {max_strike_oi_wan} 万张，墙薄、引力弱，仅供参考
```

- [ ] **Step 5: Commit**

```bash
cd /Users/a/projects/option-flow
git add skills/option-flow/SKILL.md skills/option-flow/references/
git commit -m "feat(skill): §3 state-dependent wall reading + OI guardrails"
```

---

## Task 6: Differentiation regression + double-blind verification

Validation per spec §6 (原则 1 反馈闭环 + 模式 K/H). No code; produces a verification note.

**Files:**
- Create: `_test_reports/_phase1_§3_verification.md` (scratch note; gitignored dir is fine to keep local)

- [ ] **Step 1: Regenerate read_states for a state-diverse set**

Run for each ticker; capture `read_states` from stdout:
```bash
cd /Users/a/projects/option-flow
for S in NOK NVDA AAPL SPY; do
  python3 run.py ${S}.US >/dev/null 2>&1
  echo "=== $S ===";
  python3 compute.py _dev_payloads/${S}_raw_payload.json \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print(json.dumps(d['read_states'],ensure_ascii=False,indent=2))"
done
```
Expected: `structure_label` / `proximity` differ across tickers (NOT all identical). NOK ≈ 天花板紧贴·下方真空 + thin_wall true; large-cap ETFs ≈ 厚墙, different structure. If any two are structurally identical, that's expected only if their geometry truly matches — eyeball for the "same template" smell.

- [ ] **Step 2: Render §3 for 2 tickers via the skill, eyeball differentiation**

Manually run the option-flow skill (or hand-render §3 from each `ai_payload`) for NOK and one large-cap. Confirm the two §3 sections read **differently** (different proximity language, different structure verdict, NOK has thin-wall caveat, large-cap does not). Record both in the verification note.

- [ ] **Step 3: Double-blind sub-agent render (模式 K)**

Dispatch a fresh subagent (no conversation context) with ONLY: the updated SKILL.md §3 + hard-rules §OI guardrail + one ticker's `ai_payload`. Ask it to render §3 and to self-report any prompt ambiguity. Capture findings.

Use the Agent tool:
> Render the §3 关键水位 section per skills/option-flow/SKILL.md for this ai_payload: <paste>. Also list any ambiguity you hit while choosing the state reading. Do NOT read other tickers' reports.

Expected: rendered §3 matches the proximity→读法 table; banned gamma vocab absent; structure_label used verbatim. Log any ambiguity for inline SKILL.md fixes.

- [ ] **Step 4: Fix any ambiguity found, re-render if changed**

If Step 3 surfaced prompt ambiguity, edit SKILL.md/output-format.md inline and re-run Step 3 for that ticker. Repeat until the double-blind render is clean.

- [ ] **Step 5: Commit verification note + any prompt fixes**

```bash
cd /Users/a/projects/option-flow
git add -A
git commit -m "test(skill): §3 differentiation + double-blind verification (phase 1)"
```

---

## Self-Review (completed during authoring)

- **Spec coverage:** read_states fields (§4.1) → Tasks 1-4; §3 rewrite (§4.2) → Task 5; honesty guardrails (§4.3) → Task 5 Step 2; verification (§6) → Task 6. ✅
- **Reconciled vs spec:** thin-wall is an additive flag (not a structure_label); structure_label is pure geometry (5 values); PCR-driven 单边 deferred to Phase 2. Spec §4.1 updated to match. ✅
- **Type consistency:** field names (`call_wall_proximity`, `structure_label`, `max_pain_pull.side`, `thin_wall`) identical across Tasks 1-6 and both docs. Helper names (`_proximity`, `_thickness`, `_asymmetry`, `_max_pain_pull`, `_structure_label`, `_read_states`) consistent. Boundary constants (`PROXIMITY_NEAR_PCT`, `WALL_MIN_OI_WAN`, `WALL_THICK_WAN`) reused, not duplicated. ✅
- **No placeholders:** every code step shows full code; every command shows expected output. ✅
