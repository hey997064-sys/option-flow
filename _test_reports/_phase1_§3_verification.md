# Phase 1 §3 几何状态读法 · 验证记录（2026-06-04）

分支 `feat/geometry-state-reads`。验证 spec §6（原则 1 反馈闭环 + 模式 H/K）。

## 1. 差异化回归（真实数据，5 标的）

| 标的 | structure_label | call | put | asym | thin |
|---|---|---|---|---|---|
| NOK | 天花板紧贴·下方真空 | +0.9% 逼近/薄 | −8.0% 远离/薄 | 偏空真空 | ✅ |
| NVDA | 双墙紧夹·窄震荡 | +0.1% 逼近 | −2.2% 中等 | 对称 | ✗ |
| AAPL | 双墙紧夹·窄震荡 | +1.5% 逼近 | −3.3% 中等/薄 | 对称 | ✅ |
| SPY | 双墙紧夹·窄震荡 | +0.1% 逼近 | −0.6% 逼近 | 对称 | ✗ |
| TSLA | 双墙宽松·区间漂移 | +3.8% 中等 | −5.6% 远离/薄 | 对称 | ✅ |

5 标的 → 3 种 structure，由真实几何驱动（非模板）。结论：差异化成立。

## 2. 模式 H 边界 bug（真实数据暴露 + 已修）

`_asymmetry` 初版只看比值 ≥2.5，导致两墙都贴脸时误判"真空"：
- SPY call +0.1% / put −0.6% → 比值 6 → 误判偏空真空（put −0.6% 根本不是真空）
- NVDA put −2.2% 同样被夸大

**修复**（commit `676b457`）：真空/开阔需"远侧确为 `远离`（>5%）"。修后 SPY/NVDA→对称→双墙紧夹（正确）；NOK（put −8%）仍真空（正确）。新增回归测试 `test_asymmetry_near_walls_not_vacuum`。

## 3. 双盲 sub-agent 渲染（模式 K）

- **首轮**（NOK + SPY，独立无上下文）：两份 §3 清晰差异化，无 gamma 禁词，structure_label 逐字使用，proximity 读法正确。揪出 8 处歧义，其中 4 处真缺口已修（commit `9f51a66`）：结构判定句对照表、Max Pain is_noise 降级、Max Pain 重合措辞位置、深度集群格式。
- **重渲验证**（NOK）：结构句逐字套表、Max Pain 同时带"重合"+is_noise caveat、thin_wall ⚠️行、零猜测。干净通过。

## 4. 已知遗留（不在 Phase 1 范围）

- 半美元行权价（如 $15.5）受单位铁律 `strike→${value:.0f}` 影响渲染为 $16。pre-existing，与本次改动无关；Phase 2 统一评估。
- §1/§2/§4/§5 仍为旧逻辑；structure_label 加粗标签行、IV regime→策略桥、具名打法 列入 Phase 2。

全套测试：73 passed。
