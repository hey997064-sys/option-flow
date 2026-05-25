# Test payloads — fixture for degradation paths

本目录保留构造测试用的 ai_payload fixture，用于测试 SKILL.md 降级路径。

## 派生策略

每个 fixture 都基于真实 ai_payload + 修改特定字段触发降级：

| 文件 | 基础 payload | 修改 | 触发降级 |
|---|---|---|---|
| `AAPL_pcr_lag3_payload.json` | `_dev_payloads/AAPL_ai_payload.json` | `data_quality.pcr_lag_days=3` + `pcr_latest_date="2026-05-19"` | 报告头加 ℹ️ PCR 滞后 banner |
| `AAPL_no_callwall_payload.json` | `_dev_payloads/AAPL_ai_payload.json` | `key_levels.call_wall=null` | §1 改写"核心关注 Max Pain" + §2/§3/§5 对称处理 |

## 如何加新的 fixture

1. 从 `_dev_payloads/` 选一个干净的真实 payload
2. 复制到 `_test_payloads/<symbol>_<scenario>_payload.json`
3. 修改触发降级的字段
4. 让 sub-agent 按 SKILL.md 渲染并对比预期降级行为
5. 把渲染结果存到 `_test_reports/<symbol>_<scenario>_report.md`
6. 更新本 README + `_test_reports/README.md`

## ⚠️ 构造产物的副作用

构造 fixture 时**只改部分字段**，其他字段保留原始值。这会产生人为副作用：

- 例：`AAPL_no_callwall_payload.json` 把 `call_wall=null`，但 `oi_distribution.ascii` 字段仍是用原始 call_wall 渲染的（含 `● CALL WALL` 标记）—— 这与 `call_wall=null` **直接矛盾**
- 真实生产中 compute.py 会保证一致（wall=null 时 ASCII 不会标记），构造测试是**人为副作用**
- 测试时记得在 sub-agent prompt 里告知"ASCII 含 ● CALL WALL 是构造副作用，不是 LLM 该校对的事"

详见 SKILL.md「Wall 缺失细则」段对 ASCII 字段处理的明示规则。
