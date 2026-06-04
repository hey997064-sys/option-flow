# option-flow · 几何状态读法（Geometry-State Reads）设计

> 日期：2026-06-04 · 分支：`feat/geometry-state-reads`
> 目标：让阻力/支撑位 + PCR/IV/HV 的解读**有指导价值且差异化**，终结"所有标的同一套逻辑（上面 Call 墙、下面 Put 墙、夹在中间）"的模板复述。

## 1. 问题诊断

现有 5 段报告的两个核心功能——(1) 阻力/支撑位、(2) PCR/IV/HV 解读——退化成模板复述：每个标的都是"上面 Call 墙、下面 Put 墙、现价夹在中间"。这句话对几乎所有流动性正常的标的都成立（calls 天然聚上方、puts 聚下方），**等于没说**，散户拿不到"这个标的和别的不一样在哪、所以该怎么做"。

根因：§1 prompt 要求"串联 方向+定价+Wall 区间含义"，"Wall 区间含义"这条把 LLM 逼回"夹在中间"的复述。**结构本身鼓励复述、不鼓励差异化判断。**

## 2. 设计原则与外部基准

竞品调研（SpotGamma / MenthorQ，对抗验证多为 3-0 通过）给出 4 条可借鉴范式：

1. **状态依赖三段读法**（最关键）：同一水位，价格 远离/逼近/突破 给三种不同动作 → 这是杀掉模板复述的根。
2. **机制叙事**：每个水位后跟"谁被迫买/卖"。⚠️ 竞品是 gamma 口径，我们是 **OI 口径**，必须改写成自己的 OI 机制，不能照抄 gamma 措辞。
3. **regime→策略映射**：波动率状态直接映射策略类型。我们用 **IV−HV 贵贱** 当代理（无 gamma）。
4. **具名打法 + caveat 绑定**：具名 setup + 每个指标绑"何时有效、何时是噪音"。

**核心洞察**：不把标的塞进固定盒子（那会再次模板化），而是让解读成为"现价 vs 各水位相对状态 + 墙距对称度 + 墙薄厚 + IV 贵贱"的**函数**。状态不同 → 读法天然不同。

## 3. 范围（分阶段）

| 阶段 | 内容 | 本 spec |
|---|---|---|
| **Phase 1** | `read_states` 派生层 + §3 关键水位段升级 | ✅ 本次实现 + 验证 |
| Phase 2 | §1 加粗 structure_label 标签行、§2 含义列、§4 regime→策略桥末句、§5 具名打法+caveat | ⏸ 设计已记录，待 Phase 1 验证通过后做 |

**先做 §3 的理由**（原则 2 先稳定再优化）：§3 是最大杠杆且改动可独立验证；状态读法在此先跑通、肉眼确认差异化成立，再扩到其余段，避免一次性大改难定位问题。

## 4. Phase 1 详细设计

### 4.1 新增数据层 `read_states`（compute.py 派生）

不靠 LLM 自由发挥；先把"几何状态"算成结构化字段，prompt 只把状态翻成人话。挂在 `ai_payload` 顶层新增 `read_states` 字典。

Phase 1 需要的字段（§3 消费）：

| 字段 | 取值 | 算法 |
|---|---|---|
| `call_wall.proximity` | `逼近` / `中等` / `远离` | `\|call_wall.distance_pct\|`：≤2% 逼近，2–5% 中等，>5% 远离 |
| `put_wall.proximity` | `逼近` / `中等` / `远离` | `\|put_wall.distance_pct\|` 同上 |
| `asymmetry` | `对称` / `偏空真空` / `偏多开阔` | 一侧墙距 ≥ 2.5× 另一侧 → 近端侧定调；call 近 put 远=偏空真空，put 近 call 远=偏多开阔；否则对称 |
| `call_wall.thickness` / `put_wall.thickness` | `厚` / `中` / `薄` / `None` | 按各自 `oi_wan` 绝对量：≥10 万 厚，3–10 万 中，<3 万 薄（OI 量级 = 机制强度，诚实口径；<3 万即低于 `WALL_MIN_OI_WAN`=3.0，是 fallback 选出的薄墙）。墙缺失为 None |
| `thin_wall` | `bool` | 任一存在的墙 thickness=`薄` → true。**独立加性标记**，驱动 §3 caveat 行，不进 structure_label |
| `max_pain_pull` | `{side: 上方/下方/重合, is_noise: bool}` | side=`max_pain.strike` 相对现价位置（高于现价=上方）；`is_noise = data_quality.max_strike_oi_wan < 3.0` |
| `structure_label` | 5 类之一 / `None`（见下） | 由**纯墙几何**（proximity + asymmetry）规则指派，**LLM 不自创、不改名** |

`structure_label` 取值（Phase 1 = 纯墙几何，小而封闭、规则驱动→可复现）：

- `天花板紧贴·下方真空`（asymmetry = 偏空真空）
- `地板紧贴·上方开阔`（asymmetry = 偏多开阔）
- `双墙紧夹·窄震荡`（对称 + 双墙均 逼近/中等，或混合但无 远离）
- `双墙宽松·区间漂移`（对称 + 任一墙 远离）
- `None`（任一墙缺失 → 交由 §3 Wall 缺失路径处理）

指派规则（确定性，仅依赖 call/put proximity + asymmetry）：
1. 任一墙缺失 → `None`
2. asymmetry=偏空真空 → 天花板紧贴·下方真空；asymmetry=偏多开阔 → 地板紧贴·上方开阔
3. 对称：任一墙 `远离` → 双墙宽松·区间漂移；否则 → 双墙紧夹·窄震荡

**PCR 驱动的 `单边强多/空`、`薄墙·水位失真` 不进 Phase 1 structure_label**——前者属 §1 方向（Phase 2），后者由 `thin_wall` 加性标记承担。规则边界用真实数据测（模式 H）。

### 4.2 §3 关键水位段升级

ASCII 图仍直接 paste（compute 预渲染，LLM 不碰）。下方 bullet 从"报数字"升级为 **水位 + 现价相对状态 + 状态读法 + OI 机制一句**，并加结构判定行 + 薄墙 caveat。

模板（以 NOK 为例）：

```
- 上方 $17 阻力 · 现价已逼近（+0.9%）→ 最高信号区：Call 持仓集中、越冲高卖压越大，易停滞回落；站上 $17 才翻多。
- 下方 $15.5 支撑 · 现价远在上方（-8%）→ 失守 $16.85 下方是 8% 无持仓真空带，没有缓冲、直奔 $15.5。
- Max Pain $15.5 引力中枢（与 Put Wall 重合）· 距现价 -8.0%。
- 结构判定：天花板紧贴 + 下方真空 = 偏空压顶（非震荡）。
- ⚠️ 单 strike 最大持仓仅 2.6 万张，墙薄、引力弱，仅供参考。
```

状态读法对照（compute 给 proximity，prompt 按状态选读法）：

| proximity | 读法骨架 |
|---|---|
| 逼近（≤2%） | "最高信号区"：临近水位、持仓集中、冲高/杀跌易停滞；给"站上/失守 $X 才转向"的前向触发 |
| 中等（2–5%） | 给该水位为可达的近端目标/阻力，描述到该位的空间 |
| 远离（>5%） | 强调"到该水位之间是 N% 无持仓真空带/缓冲带"，失守/突破后无支撑/无压力 |

机制一句（OI 口径，**禁 gamma 措辞**）：持仓集中 → 做市商日常对冲调仓量大 → 调仓搬动股价 → 形成引力；薄 → 调仓量小 → 引力弱。

### 4.3 诚实护栏（写进 hard-rules）

1. **禁称** 做市商 gamma / GEX / Gamma Flip / vanna / charm —— 我们是 **OI 口径**，机制叙事只用"持仓集中→调仓搬股→引力，薄则弱"。
2. 不伪造 regime 拐点价位（我们没有该数据）。
3. `structure_label` 由 compute 指派，LLM 不自创、不改名、不新增类别。
4. 机制方向措辞谨慎（调研警告易写反）：只说"持仓集中处形成引力/阻力支撑"，不展开做市商 delta 对冲方向细节。

## 5. 改动文件

| 文件 | 改动 |
|---|---|
| `compute.py` | 新增 `read_states` 派生块（4.1 的字段 + structure_label 规则） |
| `skills/option-flow/SKILL.md` | §3 段说明改写为状态读法；新增 structure_label 用法 + 诚实护栏 |
| `skills/option-flow/references/output-format.md` | §3 模板更新 |
| `skills/option-flow/references/hard-rules.md` | 新增 OI-vs-gamma 护栏 + 禁用词 |
| `skills/option-flow/references/ai-payload-schema.md` | 补 `read_states` 字段契约 |

## 6. 验证（原则 1 反馈闭环 + 模式 K 双盲）

1. **差异化回归**：重渲一组状态各异的标的——NOK（薄墙·偏空真空）、NVDA（宽松·事件）、AAPL（平淡·窄区间）、SPY（大盘·厚墙）、一个单边标的——肉眼确认 §3 读法**各不相同**，无"夹在中间"复述。
2. **边界 case 真实数据测**（模式 H）：structure_label 规则边界（如恰好 2% / 2.5× 临界、call_wall 或 put_wall 缺失、Max Pain 与 Wall 重合）用真实 payload 验证不误判。
3. **双盲 sub-agent 渲染**（模式 K）：对 2–3 个标的让独立 sub-agent（不带本对话上下文）按新 SKILL.md 渲染 §3，揪 prompt 歧义与状态读法分支不一致。
4. **mutation 不需要**：read_states 是纯几何派生，开发期单元测试覆盖分档边界即可（模式 G：skill 形态不挂 runtime validator）。

## 7. 非目标（YAGNI）

- 不引入 gamma/GEX 计算（数据口径不支持，且会误导）。
- 不做全量打分模型（方案 C，over-engineer）。
- 不在 Phase 1 动 §1/§2/§4/§5（范围控制，先验证 §3）。
