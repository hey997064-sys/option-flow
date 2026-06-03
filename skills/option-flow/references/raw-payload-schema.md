# raw_payload 字段契约（fetch → compute）

> **开发者参考文档**——本文件描述 `fetch.py → compute.py` 的内部契约，**仅供调试 / 算法层开发**，LLM 渲染时不消费此 schema（LLM 消费的是 `ai-payload-schema.md` 描述的 `ai_payload`）。
>
> `fetch.py` 返回 dict、`compute.py` 消费 dict。两层之间的契约文档化在此。
> 改字段名 / 类型 = 跨层 breaking change，必须同时改 compute.py + 加测试。

## 顶层字段

| 字段 | 类型 | 含义 | 来源 |
|---|---|---|---|
| `symbol` | str | 标的代号，形如 `"NVDA.US"` | 入参 normalize |
| `fetched_at` | str | ISO 时间戳（ET 时区），日志用 | `datetime.now(ET).isoformat()` |
| `snapshot_date` | str | 当前 ET 日（YYYY-MM-DD），metadata | `date.today() in ET` |
| `data_as_of` | str | 价格 / IV 数据 as-of 日（kline 最后一行的日期。盘后 = today close，盘中 = today intraday，周末 = 上一 Friday） | kline 最后一行的 date |
| `pcr_latest_date` | str \| None | PCR 最新数据日（broker T+1，每日 ET 12:00 前后更新） | `pcr_history[-1].date` |
| `is_intraday` | bool | 当前是否为 ET 9:30-16:00 周一-五交易时段 | `_compute_is_intraday()` |
| `current_price` | float | kline 最后一行 close（盘后=today close，盘中=today intraday spot） | kline 最后一行 close |
| `contracts` | list[dict] | 期权合约（≤14d / 30-60d / 90-180d 三窗口聚合）| `option chain --date <d>` × `option quote` |
| `pcr_history` | list[dict] | 全市场 PCR 历史（broker T+1 聚合，30 天）| `option volume daily` |
| `stock_closes` | list[dict] | **已完整收盘**的 close 序列（≥31 行供算 30 天 HV）；盘中跑时排除 today intraday 行 | `kline daily --count 45`，盘中过滤 today |

## `contracts[*]` 字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `option_symbol` | str | OCC 编码，形如 `"NVDA260522C220000.US"` |
| `type` | str | `"call"` \| `"put"` |
| `strike` | float | 行权价（USD） |
| `expiry` | str | 到期日（YYYY-MM-DD） |
| `days_to_expiry` | int | fetch 用 ET today 算的 DTE（**注意 compute 会以 `data_as_of` 重算 DTE**，不信此字段） |
| `bucket` | str | `"short"`（≤14d）\| `"mid"`（30-60d）\| `"long"`（90-180d） |
| `open_interest` | int | OI 张数（**原始**，不是 `_wan`） |
| `volume` | int | 当日累计成交量（盘中是实时累计） |
| `implied_volatility` | float \| None | broker 给的 IV，**原始小数**（0.664，不是 66.4）。compute 会 ×100 转 `_pct` |

## `pcr_history[*]` 字段

| 字段 | 类型 |
|---|---|
| `date` | str（ISO，ET 时区） |
| `pcr_oi` | float（Put OI / Call OI） |
| `call_oi_wan` | float（万张） |
| `put_oi_wan` | float（万张） |

## `stock_closes[*]` 字段

| 字段 | 类型 |
|---|---|
| `date` | str（ISO） |
| `close` | float（USD） |

**保证**：`stock_closes` 末行的 `date` ≤ `raw_payload.data_as_of`。
- 盘后（is_intraday=False）：末行 date == data_as_of（同步）
- 盘中（is_intraday=True）：末行 date == 上一交易日，data_as_of == today intraday 日；末行比 data_as_of 早 1 天

## 关键约束（fetch 必须保证，compute 信任不重算）

1. **`current_price` 与 `data_as_of` 同步**——都来自 kline 最后一行；盘中是 intraday spot + today，盘后是 today close + today
2. **`stock_closes` 永远只含完整收盘价**——盘中跑过滤掉 today intraday 行，保护 HV 计算
3. **OCC 编码 case-normalized**（uppercase）——下游 dedup 不踩 case 坑
4. **bucket 标签来自 fetch 的窗口定义**——`SHORT_MAX_DTE=14`、`MID_MIN/MAX_DTE=30/60`、`LONG_MIN/MAX_DTE=90/180`
5. **chain 单 expiry 失败 + quote 单 chunk 失败均 fail-soft + WARN**，coverage < 70% 才抛错
6. **`is_intraday` 仅基于 ET 时间判断**——不查 broker 节日日历（节假日仍可能误报）

## 已删除字段（不要再期望）

无（自 fetch_v5 起所有现有字段都还在用）。

## 上游引用

- fetch.py 调用：`longbridge option chain` / `option quote` / `option volume daily` / `kline daily`
- compute.py 消费：见 `ai-payload-schema.md`
