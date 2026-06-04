"""Option-flow skill · 数据处理层（第二层）· compute_v3。

纯函数 compute(raw_payload) → ai_payload。无 SQLite / SDK / 网络 / 文件 IO 依赖。

本版本相对 compute_v2 的差异（compute_v3）：

  ── Patch 1: _iv_peak 加 DTE 上限 + 触发阈值 ─────────────────────────────────
  · 仅在 DTE ∈ [0, 14] 窗口内寻找 ATM IV 最高 expiry（IV_PEAK_DTE_MAX=14）
  · 额外要求 iv_peak.iv_pct ≥ iv_far.iv_pct × 1.3（IV_PEAK_TRIGGER_RATIO=1.3）
  · 不满足则返回 None（"近端无明显压力"语义）
  · 保留 IV_SANITY_CEILING > 200% 过滤
  · 修复了 AAPL 平坦 IV 面 → iv_peak 落到 DTE=149/IV=23.8% 的语义错误

  ── Patch 2: _hv_from_closes 严格 30 交易日 ──────────────────────────────────
  · 严格要求 stock_closes ≥ 31 条目（30 个 log return），否则返回 None
  · 取末尾 31 条目算 30 日 log return → 年化 σ × √252 × 100
  · 对齐 CBOE / 行业标准 30D HV 口径
  · 不做 fallback "用现有数据"——错周期 HV 给假数

  ── Patch 3: 删除 pcr_label / iv_hv_label ───────────────────────────────────
  · LLM 负责所有中文语义解读（"偏多/偏空"、"偏贵/合理/偏便宜"）
  · compute 只产出数字：pcr_oi、pcr_30d_rank_pct、iv_hv_spread_pp
  · 删除 _kpi_pcr / _iv_hv_diagnostics 中的标签派生
  · 删除 ai_payload.kpi.pcr_label 和 ai_payload.kpi.iv_hv_label 字段

  ── 继承 compute_v2 的算法身份（保持不变）───────────────────────────────────
  · _iter_expiry_dte_iv / _expiry_atm_iv_pct
  · _iv_window_median（iv_near / iv_far）
  · _near_wall（Wall 方向硬约束：Call 在现价上方，Put 在下方；v2 算法 2026-05-24
    起改为"现价同侧距 cp 最近、OI ≥ WALL_MIN_OI_WAN 的 strike"，旧"OI 最大" fallback）
  · _deep_clusters（Wall 之外的远端 OI ≥ 5 万集中点，新增 2026-05-24）
  · _max_pain / _oi_distribution
  · _atm_iv_at_days（VIX 同口径方差插值）
  · _data_quality
  · DTE 锚点 = raw_payload.data_as_of（IV 与价格 as-of 时间）

5 段算法（详见 references/ai-payload-schema.md）：
  ① KPI（pcr_oi / pcr_30d_rank_pct / atm_iv_pct / hv_pct / iv_hv_spread_pp）
  ② key_levels（call_wall / put_wall / max_pain / deep_supports / deep_resistances
                + oi_distribution 蝴蝶图原料 + ascii 预渲染）
  ③ term_structure（iv_peak / iv_near / iv_far）
  ④ data_quality（active_strikes / reliable / contracts_fetched）

继承 Options Edge 关键约束：
  - Wall 方向硬性：call_wall 必须在现价上方，put_wall 必须在下方
  - 单位后缀：_pct（百分比）/ _pp（百分点）/ _wan（万张）/ strike（USD），下游不转换
  - 不写中文语义字段：compute.py 只负责数学，所有中文描述交由 LLM 层完成
"""
from __future__ import annotations

import math
from datetime import date
from statistics import median

# 算法常量（与 references/ai-payload-schema.md 对齐）
ATM_WINDOW_PCT = 0.10           # Wall 在 ATM ± 10% 内
WALL_MIN_OI_WAN = 3.0           # Wall 判定：现价同侧距离最近、OI ≥ 此阈值（万张）的 strike
ATM_IV_WINDOW_PCT = 0.05        # ATM IV 插值在 ATM ± 5% 内取样
TERM_ALGO_MIN_DTE = 7           # _atm_iv_at_days 插值剔除 < 7d 近端（Vega→0 噪声）
ACTIVE_STRIKES_RELIABLE = 8
PROXIMITY_NEAR_PCT = 2.0        # |distance_pct| ≤ 此值 → 逼近
PROXIMITY_MID_PCT = 5.0         # |distance_pct| ≤ 此值 → 中等；> → 远离
ASYMMETRY_RATIO = 2.5           # 一侧墙距 ≥ 此倍数另一侧 → 不对称
WALL_THICK_WAN = 10.0           # oi_wan ≥ 此值 → 厚（薄/中边界复用 WALL_MIN_OI_WAN=3.0）

# 短桶 DTE 上限（与 raw_payload.contracts[*].bucket="short" 对齐；mid/long 桶由 fetch.py 维护，compute 不消费）
SHORT_MAX_DTE = 14

# 目标天数
TARGET_30D = 30

# iv_near / iv_far 窗口
NEAR_MIN_DTE, NEAR_MAX_DTE = 5, 14
FAR_MIN_DTE, FAR_MAX_DTE = 30, 180

# IV 合理性上限：真实 ATM IV 不会 > 200%，超过即为收盘后清算异常 / 数据脏。
IV_SANITY_CEILING = 200.0

# iv_peak (v3) — only inspect short-dated window and require a real "stress" spike.
# Without these guards a flat IV surface lets some random far-dated expiry surface
# as "the peak" (AAPL bug: DTE=149 IV=23.8% landed as iv_peak).
IV_PEAK_DTE_MIN = 1         # exclude 0DTE — Vega→0 makes IV reverse-engineering unstable
IV_PEAK_DTE_MAX = 14        # only look in [IV_PEAK_DTE_MIN, 14] calendar-day window
IV_PEAK_TRIGGER_RATIO = 1.3 # peak must be >= iv_far * 1.3 to qualify

# 30D HV (v3) — strict CBOE/industry standard requires 30 trading-day log returns.
HV_TRADING_DAYS = 30
HV_REQUIRED_CLOSES = HV_TRADING_DAYS + 1  # 31 closes → 30 returns


# -----------------------------------------------------------------------------
# 入口
# -----------------------------------------------------------------------------


def compute(raw_payload: dict) -> dict:
    """raw_payload → ai_payload（严格符合 references/ai-payload-schema.md 契约）。"""
    symbol = raw_payload["symbol"]
    current_price = float(raw_payload["current_price"])
    # fetch_v4 起 snapshot_date 是必填顶层字段。不再 fallback 到 fetched_at[:10]。
    snapshot_date = raw_payload["snapshot_date"]
    # data_as_of = 前一个 fully-closed US 交易日（current_price 来自该日收盘）。
    # 这是 DTE 计算的语义正确锚点。
    data_as_of = raw_payload.get("data_as_of")
    pcr_latest_date = raw_payload.get("pcr_latest_date")
    contracts = raw_payload.get("contracts") or []
    pcr_history = raw_payload.get("pcr_history") or []
    stock_closes = raw_payload.get("stock_closes") or []

    # DTE 锚点：以 data_as_of 为准；data_as_of 缺失时降级到 snapshot_date。
    dte_anchor = data_as_of or snapshot_date

    # ① KPI
    pcr_oi, pcr_30d_rank_pct = _kpi_pcr(pcr_history)
    atm_iv_pct = _atm_iv_at_days(contracts, current_price, TARGET_30D, dte_anchor)
    hv_pct = _hv_from_closes(stock_closes)
    iv_hv_spread_pp = _iv_hv_diagnostics(atm_iv_pct, hv_pct)

    kpi = {
        "pcr_oi": _round(pcr_oi, 3),
        "pcr_30d_rank_pct": _round(pcr_30d_rank_pct, 1),
        "atm_iv_pct": _round(atm_iv_pct, 1),
        "hv_pct": _round(hv_pct, 1),
        "iv_hv_spread_pp": _round(iv_hv_spread_pp, 1),
    }

    # ② key_levels — 全用 ≤14d 短桶
    short_contracts = [c for c in contracts if c.get("bucket") == "short"]
    call_wall = _near_wall(short_contracts, current_price, side="above")
    put_wall = _near_wall(short_contracts, current_price, side="below")
    max_pain = _max_pain(short_contracts, current_price)
    deep_resistances = _deep_clusters(short_contracts, current_price, side="above")
    deep_supports = _deep_clusters(short_contracts, current_price, side="below")
    oi_distribution = _oi_distribution(short_contracts)
    oi_distribution["ascii"] = _render_butterfly_ascii(
        oi_distribution, current_price, call_wall, put_wall, max_pain,
        deep_supports=deep_supports, deep_resistances=deep_resistances,
    )

    key_levels = {
        "call_wall": call_wall,
        "put_wall": put_wall,
        "max_pain": max_pain,
        "deep_supports": deep_supports,
        "deep_resistances": deep_resistances,
        "oi_distribution": oi_distribution,
    }

    # ③ term_structure（三字段：iv_peak / iv_near / iv_far）
    iv_near = _iv_window_median(
        contracts, current_price, dte_anchor,
        NEAR_MIN_DTE, NEAR_MAX_DTE, precision="normal",
    )
    iv_far = _iv_window_median(
        contracts, current_price, dte_anchor,
        FAR_MIN_DTE, FAR_MAX_DTE, precision="high",
    )
    # iv_peak now requires iv_far as a trigger baseline.
    iv_far_pct = iv_far["iv_pct"] if iv_far else None
    iv_peak = _iv_peak(contracts, current_price, dte_anchor, iv_far_pct)

    term_structure = {
        "iv_peak": iv_peak,
        "iv_near": iv_near,
        "iv_far": iv_far,
    }

    # ④ data_quality（含 is_intraday 透传 + pcr_lag_days 派生）
    is_intraday = bool(raw_payload.get("is_intraday", False))
    pcr_lag_days = _compute_pcr_lag_days(data_as_of, pcr_latest_date)
    data_quality = _data_quality(
        short_contracts,
        total_contracts=len(contracts),
        is_intraday=is_intraday,
        pcr_lag_days=pcr_lag_days,
    )

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


# -----------------------------------------------------------------------------
# ① KPI
# -----------------------------------------------------------------------------


def _kpi_pcr(pcr_history: list[dict]) -> tuple[float | None, float | None]:
    """取 pcr_history 末日 pcr_oi + 30 天百分位。

    Patch 3: 去掉标签派生——LLM 层负责"偏多/偏空/中性"等语义解读。
    """
    if not pcr_history:
        return None, None
    series = [float(p["pcr_oi"]) for p in pcr_history if p.get("pcr_oi") is not None]
    if not series:
        return None, None
    current = series[-1]

    rank_pct = None
    if len(series) >= 2:
        below = sum(1 for x in series if x < current)
        rank_pct = 100.0 * below / (len(series) - 1)

    return current, rank_pct


def _hv_from_closes(stock_closes: list[dict]) -> float | None:
    """30 trading days HV (CBOE/industry standard).

    Requires >= 31 close entries. Annualized by √252.

    取末尾 31 条 close → 30 个 log return → σ × √252 × 100。
    严格 30 交易日窗口；若 stock_closes 不足 31 条，返回 None（不做 "use what
    you have" fallback——错周期 HV 等于给假数）。

    输入契约：fetch 层保证 stock_closes 末行日期 == raw_payload.data_as_of
    （已过滤盘中 "today ET" 行；详见 fetch.py 步骤 8）。compute 直接消费，不做
    日期校验。
    """
    closes = [float(s["close"]) for s in stock_closes if s.get("close") is not None]
    if len(closes) < HV_REQUIRED_CLOSES:
        return None
    window = closes[-HV_REQUIRED_CLOSES:]  # last 31 closes → 30 returns
    log_returns = []
    for i in range(1, len(window)):
        if window[i - 1] <= 0 or window[i] <= 0:
            continue
        log_returns.append(math.log(window[i] / window[i - 1]))
    if len(log_returns) < 2:
        return None
    mean_r = sum(log_returns) / len(log_returns)
    var = sum((r - mean_r) ** 2 for r in log_returns) / (len(log_returns) - 1)
    sigma_daily = math.sqrt(var)
    return sigma_daily * math.sqrt(252) * 100


def _iv_hv_diagnostics(
    atm_iv_pct: float | None,
    hv_pct: float | None,
) -> float | None:
    """ATM IV − HV 价差 (percentage points)。

    Patch 3: 去掉标签派生——LLM 层负责"偏贵/合理/偏便宜"语义解读。
    """
    if atm_iv_pct is None or hv_pct is None:
        return None
    return atm_iv_pct - hv_pct


# -----------------------------------------------------------------------------
# ② key_levels
# -----------------------------------------------------------------------------


def _near_wall(
    short_contracts: list[dict],
    current_price: float,
    side: str,
) -> dict | None:
    """≤14d、ATM ± 10%、现价同侧 Wall——优先选**近端集中点**而非远端最大 OI。

    算法（v2，对齐散户"支撑/阻力"直觉）：
      ① 候选集：现价同侧 ATM ± 10% 内、OI > 0 的 strikes
      ② 优先取 OI ≥ WALL_MIN_OI_WAN 万张 中**距 cp 最近**的 strike
      ③ 若 ② 无符合阈值的 strike，fallback 到 OI 最大的（原算法）

    side="above" 取现价**上方** Call OI（上方阻力）
    side="below" 取现价**下方** Put OI（下方支撑）

    与旧算法的差异：SPY 5/22 数据下旧算法把 Put Wall 选成 $700（最大 OI 14.5 万，距 -6.1%），
    新算法选 $735（OI 4.2 万，距 -1.4%）。$700 / $710 / $720 等深度 Put 集中点改由
    `deep_supports` 字段单独暴露，避免 §3 Wall 信息位被"远端最大 OI"占据。
    """
    if not short_contracts or current_price <= 0:
        return None
    lo = current_price * (1 - ATM_WINDOW_PCT)
    hi = current_price * (1 + ATM_WINDOW_PCT)

    oi_per_strike: dict[float, int] = {}
    target_type = "call" if side == "above" else "put"
    for c in short_contracts:
        if c.get("type") != target_type:
            continue
        strike = c.get("strike")
        if strike is None:
            continue
        if side == "above" and not (current_price < strike <= hi):
            continue
        if side == "below" and not (lo <= strike < current_price):
            continue
        oi_per_strike[strike] = oi_per_strike.get(strike, 0) + int(c.get("open_interest") or 0)

    candidates = [(s, oi) for s, oi in oi_per_strike.items() if oi > 0]
    if not candidates:
        return None

    # ② 阈值过滤后取距 cp 最近的
    threshold = WALL_MIN_OI_WAN * 10000
    qualifying = [(s, oi) for s, oi in candidates if oi >= threshold]
    if qualifying:
        strike, oi = min(qualifying, key=lambda p: abs(p[0] - current_price))
    else:
        # ③ fallback: OI 最大（原算法）
        strike, oi = max(candidates, key=lambda p: p[1])

    return {
        "strike": float(strike),
        "oi_wan": round(oi / 10000, 1),
        "distance_pct": round((strike - current_price) / current_price * 100, 1),
    }


def _deep_clusters(
    short_contracts: list[dict],
    current_price: float,
    side: str,
    min_oi_wan: float = 5.0,
    max_count: int = 3,
) -> list[dict]:
    """识别现价同侧 OI ≥ 阈值的"深度集中点"——比 Wall 更远但有意义的支撑/阻力位。

    side="above" → 现价上方 Call OI 集群
    side="below" → 现价下方 Put OI 集群
    """
    if not short_contracts or current_price <= 0:
        return []
    lo = current_price * (1 - ATM_WINDOW_PCT)
    hi = current_price * (1 + ATM_WINDOW_PCT)

    oi_per_strike: dict[float, int] = {}
    target_type = "call" if side == "above" else "put"
    for c in short_contracts:
        if c.get("type") != target_type:
            continue
        strike = c.get("strike")
        if strike is None:
            continue
        if side == "above" and not (current_price < strike <= hi):
            continue
        if side == "below" and not (lo <= strike < current_price):
            continue
        oi_per_strike[strike] = oi_per_strike.get(strike, 0) + int(c.get("open_interest") or 0)

    threshold = min_oi_wan * 10000
    qualifying = [(s, oi) for s, oi in oi_per_strike.items() if oi >= threshold]
    # 距 cp 升序（最近的先 → 给散户最常用的视角）
    qualifying.sort(key=lambda p: abs(p[0] - current_price))
    return [
        {
            "strike": float(s),
            "oi_wan": round(oi / 10000, 1),
            "distance_pct": round((s - current_price) / current_price * 100, 1),
        }
        for s, oi in qualifying[:max_count]
    ]


def _max_pain(short_contracts: list[dict], current_price: float) -> dict | None:
    """≤14d 多 expiry 合并 OI 求 Max Pain。"""
    if not short_contracts or current_price <= 0:
        return None
    call_oi: dict[float, int] = {}
    put_oi: dict[float, int] = {}
    for c in short_contracts:
        strike = c.get("strike")
        if strike is None:
            continue
        oi = int(c.get("open_interest") or 0)
        if c.get("type") == "call":
            call_oi[strike] = call_oi.get(strike, 0) + oi
        elif c.get("type") == "put":
            put_oi[strike] = put_oi.get(strike, 0) + oi
    candidates = sorted(set(call_oi) | set(put_oi))
    if not candidates:
        return None
    best_strike, best_pain = None, math.inf
    for S in candidates:
        pain = 0.0
        for K, oi in call_oi.items():
            if oi and S > K:
                pain += oi * (S - K)
        for K, oi in put_oi.items():
            if oi and K > S:
                pain += oi * (K - S)
        if pain < best_pain:
            best_pain = pain
            best_strike = S
    if best_strike is None:
        return None
    return {
        "strike": float(best_strike),
        "distance_pct": round((best_strike - current_price) / current_price * 100, 1),
    }


def _oi_distribution(short_contracts: list[dict]) -> dict:
    """≤14d 多 expiry 合并 OI 按 strike 升序输出（蝴蝶图原料）。"""
    call_oi: dict[float, int] = {}
    put_oi: dict[float, int] = {}
    for c in short_contracts:
        strike = c.get("strike")
        if strike is None:
            continue
        oi = int(c.get("open_interest") or 0)
        if c.get("type") == "call":
            call_oi[strike] = call_oi.get(strike, 0) + oi
        elif c.get("type") == "put":
            put_oi[strike] = put_oi.get(strike, 0) + oi
    strikes = sorted(set(call_oi) | set(put_oi))
    return {
        "strikes": [float(s) for s in strikes],
        "call_oi_wan": [round(call_oi.get(s, 0) / 10000, 1) for s in strikes],
        "put_oi_wan": [round(put_oi.get(s, 0) / 10000, 1) for s in strikes],
    }


BUTTERFLY_TICKS_PER_SIDE = 5     # 现价上下各取多少档主刻度
BUTTERFLY_MIN_ROW_OI_WAN = 1.0   # 每行至少一边 OI ≥ 此阈值才进图（万张）


def _major_tick_for_price(cp: float) -> float:
    """主刻度自适应——高价标的用大刻度，避免上下 5 档跨度太窄。"""
    if cp >= 500:
        return 10.0
    if cp >= 100:
        return 5.0
    if cp >= 30:
        return 2.5
    return 1.0


def _render_butterfly_ascii(
    oi_distribution: dict,
    current_price: float,
    call_wall: dict | None,
    put_wall: dict | None,
    max_pain: dict | None,
    deep_supports: list[dict] | None = None,
    deep_resistances: list[dict] | None = None,
) -> str:
    """渲染 §3 ASCII 双向蝴蝶图，规则同 references/ascii-butterfly-template.md。

    SKILL.md §3 直接 paste 本字段——不再依赖 LLM 抄数。

    选 strike 算法（散户友好：主刻度自适应 + 必保留关键位 + OI 阈值过滤）：
      ① 主刻度由 _major_tick_for_price(cp) 决定（$10 / $5 / $2.5 / $1）；
         现价上下各取 BUTTERFLY_TICKS_PER_SIDE 个主刻度整数关口
         （SPY $745.64 → $10 主刻度 → 上 [$750-$790] + 下 [$700-$740]）
      ② 必保留：call_wall / put_wall / max_pain / deep_supports / deep_resistances /
         现价上下相邻 strike（即使不在主刻度上、即使被 OI 过滤）
      ③ OI 阈值：非必保留行需 max(call_oi, put_oi) ≥ BUTTERFLY_MIN_ROW_OI_WAN
      ④ 选中 strike 按 strike 降序展示，**不插入 gap marker**（strike 数字本身指示跳跃）

    口径说明（写在 header）：OI 数字 = ≤14d 多 expiry 合计。散户对账友商单 expiry 数字会高，是口径不同（非 bug）。
    """
    strikes = oi_distribution.get("strikes") or []
    call_oi = oi_distribution.get("call_oi_wan") or []
    put_oi = oi_distribution.get("put_oi_wan") or []
    cp = current_price
    cw = (call_wall or {}).get("strike")
    pw = (put_wall or {}).get("strike")
    mp = (max_pain or {}).get("strike")

    BAR, HALF, MAX_BARS = "▎", "▏", 30
    TICK = _major_tick_for_price(cp)
    TICKS_PER_SIDE = BUTTERFLY_TICKS_PER_SIDE
    MIN_ROW_OI = BUTTERFLY_MIN_ROW_OI_WAN

    def bar_str(o: float) -> str:
        if o == 0:
            return ""
        if o < 1:
            return HALF
        return BAR * min(int(o), MAX_BARS)

    strike_set = set(strikes)
    must_keep = {s for s in (cw, pw, mp) if s is not None}
    # 深度集中点也必保留（让 $720 / $710 / $700 这种远端 OI 大点不被砍）
    for cluster in (deep_supports or []) + (deep_resistances or []):
        must_keep.add(cluster["strike"])

    # 现价上下相邻 strike（基于全集 strikes）
    nearby_below = max((k for k in strikes if k < cp), default=None)
    nearby_above = min((k for k in strikes if k >= cp), default=None)

    # ① 找现价上下最近的主刻度
    next_tick_above = (int(cp // TICK) + 1) * TICK
    next_tick_below = int(cp // TICK) * TICK
    if next_tick_below == cp:
        next_tick_below -= TICK

    # ② 上下各取 TICKS_PER_SIDE 档主刻度
    major_ticks = set()
    for i in range(TICKS_PER_SIDE):
        major_ticks.add(next_tick_above + i * TICK)
        major_ticks.add(next_tick_below - i * TICK)
    major_ticks &= strike_set

    # ③ 必保留 + 主刻度并集
    chosen = major_ticks | must_keep | {
        s for s in (nearby_above, nearby_below) if s is not None
    }
    chosen &= strike_set

    # ④ OI 过滤：每行 max(call_oi, put_oi) ≥ MIN_ROW_OI；
    #    must_keep 集合内的强制保留（Wall / MP / 深度集群 / 现价相邻）
    forced_keep = must_keep | {
        s for s in (nearby_above, nearby_below) if s is not None
    }
    selected = []
    for k, c, p in zip(strikes, call_oi, put_oi):
        if k not in chosen:
            continue
        if k in forced_keep or max(c, p) >= MIN_ROW_OI:
            selected.append((k, c, p))
    selected.sort(key=lambda x: -x[0])  # descending

    def render_row(k: float, c: float, p: float) -> str:
        pb = bar_str(p)
        cb = bar_str(c)
        pl = f"{p:.1f}"
        cl = f"{c:.1f}万"
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
        return f"            {pl:>5} {pb:<6} ───── ${k:>4.0f} ─────  {cb} {cl}{tag_str}"

    output_rows: list[str] = [render_row(k, c, p) for k, c, p in selected]

    tick_label = f"${TICK:.0f}" if TICK >= 1 else f"${TICK:g}"
    lines = [
        f"持仓分布 · ≤14d 多 expiry 合计 · 现价 ${cp:.2f}",
        "",
        "          PUT OI         STRIKE        CALL OI",
    ]
    lines.extend(output_rows)
    lines += [
        "",
        "每 ▎ ≈ 1 万张（OI）。OI = ≤14d 短期所有 expiry 在该 strike 合计。",
        f"显示规则：现价上下各 {TICKS_PER_SIDE} 档 {tick_label} 整数关口 + Wall / Max Pain / 深度集中点（OI ≥ 5 万）。",
    ]
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# ③ IV 期限结构（iv_peak / iv_near / iv_far）
# -----------------------------------------------------------------------------


def _expiry_atm_iv_pct(
    contracts_for_expiry: list[dict],
    current_price: float,
) -> float | None:
    """单 expiry 的 ATM IV：取 ATM ± 5% 内 Call IV 中位数（百分比数字）。

    回退：若 ATM 窗口内无 Call 样本，用全 expiry Call IV 中位数。
    """
    candidates = []
    lo = current_price * (1 - ATM_IV_WINDOW_PCT)
    hi = current_price * (1 + ATM_IV_WINDOW_PCT)
    for c in contracts_for_expiry:
        if c.get("type") != "call":
            continue
        iv = c.get("implied_volatility")
        strike = c.get("strike")
        if iv is None or iv <= 0 or strike is None:
            continue
        if lo <= strike <= hi:
            candidates.append(iv)
    if not candidates:
        # 回退到全 Call IV 中位数
        for c in contracts_for_expiry:
            if c.get("type") != "call":
                continue
            iv = c.get("implied_volatility")
            if iv is not None and iv > 0:
                candidates.append(iv)
    if not candidates:
        return None
    return median(candidates) * 100


def _iter_expiry_dte_iv(
    contracts: list[dict],
    current_price: float,
    dte_anchor: str | None,
):
    """Yield ``(expiry, dte, iv_pct)`` for every expiry that has a usable ATM IV.

    NO DTE filtering — caller picks the DTE window. Skips any expiry whose ATM
    IV exceeds ``IV_SANITY_CEILING`` (data-corruption guard).

    DTE 计算锚点（``dte_anchor``）= raw_payload.data_as_of 时为正确语义
    （IV/价格的 as-of 时间）。若 raw_payload.contracts[*].days_to_expiry 由
    fetch 用 snapshot_date 计算，本函数**不信任** raw 字段——总是用 ``dte_anchor``
    重新算，确保 _iv_peak / _iv_window_median 与 _atm_iv_at_days 用同一锚点。
    """
    by_expiry: dict[str, list[dict]] = {}
    for c in contracts:
        exp = c.get("expiry")
        if exp is None:
            continue
        by_expiry.setdefault(exp, []).append(c)

    base = None
    if dte_anchor:
        try:
            base = date.fromisoformat(dte_anchor)
        except ValueError:
            base = None

    for exp, items in by_expiry.items():
        if base is not None:
            try:
                dte = (date.fromisoformat(exp) - base).days
            except ValueError:
                continue
        else:
            dte = items[0].get("days_to_expiry")
        if dte is None:
            continue
        iv_pct = _expiry_atm_iv_pct(items, current_price)
        if iv_pct is None or iv_pct <= 0:
            continue
        if iv_pct > IV_SANITY_CEILING:
            # Data-corruption guard: real ATM IV doesn't exceed 200%.
            continue
        yield exp, int(dte), iv_pct


def _iv_peak(
    contracts: list[dict],
    current_price: float,
    dte_anchor: str | None,
    iv_far_pct: float | None,
) -> dict | None:
    """近端（DTE ∈ [IV_PEAK_DTE_MIN, IV_PEAK_DTE_MAX]）ATM IV 最高的 expiry。

    Patch 1 (v3): DTE ceiling + trigger threshold.
    Patch 2 (post-audit): exclude 0DTE — Vega→0 makes IV reverse-engineering
    unstable (bid-ask spread of $0.01 can swing IV by dozens of pp), and the
    same-day variance annualizer blows up, making 0DTE not同口径 with 30D IV.
    业界共识 (CBOE / Tastytrade / IBKR): 0DTE 跟 30D-aligned 期限结构不可混用。
    Reference: SPY 5/21 audit, 2026-05-22.

      1. DTE window — 只看 [IV_PEAK_DTE_MIN=1, IV_PEAK_DTE_MAX=14] 日历日窗口。
         排除 0DTE 数学噪声 + 远期平坦 IV 面（避免 AAPL/SPY bug）。
      2. Trigger threshold — 候选 peak 必须满足
         iv_peak.iv_pct ≥ iv_far.iv_pct × IV_PEAK_TRIGGER_RATIO（1.3×）。
         否则返回 None，语义上表达 "近端没有显著压力"。
      3. 保留 IV_SANITY_CEILING（>200% 跳过）—— 在 _iter_expiry_dte_iv 里实现。

    算法身份：iv_peak.iv_pct ≡ max(_expiry_atm_iv_pct(items, cp))
      over expiries where IV_PEAK_DTE_MIN ≤ DTE ≤ IV_PEAK_DTE_MAX
                     AND 0 < iv_pct ≤ IV_SANITY_CEILING
                     AND iv_pct ≥ iv_far_pct × IV_PEAK_TRIGGER_RATIO

    precision = "indicative" — 单点最大值方差大，给 LLM 一个"参考值"的标签。
    """
    peak = None
    for exp, dte, iv_pct in _iter_expiry_dte_iv(contracts, current_price, dte_anchor):
        if dte < IV_PEAK_DTE_MIN or dte > IV_PEAK_DTE_MAX:
            continue
        if peak is None or iv_pct > peak["iv_pct_raw"]:
            peak = {
                "expiry": exp,
                "days_to_expiry": dte,
                "iv_pct_raw": iv_pct,
            }
    if peak is None:
        return None

    # Trigger threshold check.
    if iv_far_pct is None or iv_far_pct <= 0:
        # 没有 iv_far baseline 时无法判断"显著压力"，保守返回 None。
        return None
    if peak["iv_pct_raw"] < iv_far_pct * IV_PEAK_TRIGGER_RATIO:
        return None

    return {
        "expiry": peak["expiry"],
        "days_to_expiry": peak["days_to_expiry"],
        "iv_pct": round(peak["iv_pct_raw"], 1),
        "precision": "indicative",
    }


def _iv_window_median(
    contracts: list[dict],
    current_price: float,
    dte_anchor: str | None,
    min_dte: int,
    max_dte: int,
    *,
    precision: str,
) -> dict | None:
    """DTE in [min_dte, max_dte] 区间内（闭区间）所有 expiry 的 ATM IV 中位数。

    precision 由调用方指定（"normal" / "high"）—— 跟 (min_dte, max_dte) 一一对应：
    iv_near (5, 14) → "normal"
    iv_far  (30, 180) → "high"
    """
    samples = [
        iv_pct
        for _, dte, iv_pct in _iter_expiry_dte_iv(contracts, current_price, dte_anchor)
        if min_dte <= dte <= max_dte
    ]
    if not samples:
        return None
    return {
        "iv_pct": round(median(samples), 1),
        "precision": precision,
    }


def _atm_iv_at_days(
    contracts: list[dict],
    current_price: float,
    target_days: int,
    dte_anchor: str | None,
) -> float | None:
    """30 天 ATM IV：在 (T, sigma²·T) 序列上线性插值（VIX 同口径思路）。

    1. 每个 expiry 取 ATM IV
    2. 找包夹 target_T = target_days/365 的两端 (T1, T2)
    3. 方差插值 var_target = var1 + (target-T1)/(T2-T1) × (var2-var1)
    4. sigma_target = sqrt(var_target / target_T)

    若 target 超出端点 → 用端点两个点外推；只有 1 个点 → 直接返回。

    DTE<7 过滤：保留——此处 IV 反算的 Vega→0 不稳定性会污染插值；
    与 _iv_peak 不同（peak 要暴露近端凸点，故不过滤）。
    """
    by_expiry: dict[str, list[dict]] = {}
    for c in contracts:
        exp = c.get("expiry")
        if exp is None:
            continue
        by_expiry.setdefault(exp, []).append(c)
    if not by_expiry:
        return None

    base = None
    if dte_anchor:
        try:
            base = date.fromisoformat(dte_anchor)
        except ValueError:
            base = None

    points = []
    for exp, items in by_expiry.items():
        try:
            exp_date = date.fromisoformat(exp)
        except ValueError:
            continue
        days = (exp_date - base).days if base else items[0].get("days_to_expiry")
        if days is None or days <= 0:
            continue
        if days < TERM_ALGO_MIN_DTE:
            continue  # DTE<7 Vega→0 IV 反算不稳定，剔除避免污染插值
        sigma_pct = _expiry_atm_iv_pct(items, current_price)
        if sigma_pct is None or sigma_pct <= 0:
            continue
        if sigma_pct > IV_SANITY_CEILING:
            continue  # 数据腐败保护
        sigma = sigma_pct / 100
        points.append({"days": days, "T": days / 365.0, "sigma": sigma})

    if not points:
        return None
    points.sort(key=lambda p: p["T"])
    target_T = target_days / 365.0

    # 精确命中
    for p in points:
        if abs(p["T"] - target_T) < 1e-9:
            return p["sigma"] * 100

    if len(points) == 1:
        return points[0]["sigma"] * 100

    if target_T < points[0]["T"]:
        p1, p2 = points[0], points[1]
    elif target_T > points[-1]["T"]:
        p1, p2 = points[-2], points[-1]
    else:
        p1, p2 = None, None
        for i in range(len(points) - 1):
            if points[i]["T"] < target_T <= points[i + 1]["T"]:
                p1, p2 = points[i], points[i + 1]
                break
    if p1 is None or p2 is None:
        return None
    var1 = p1["sigma"] ** 2 * p1["T"]
    var2 = p2["sigma"] ** 2 * p2["T"]
    if p2["T"] == p1["T"]:
        return p1["sigma"] * 100
    var_target = var1 + (target_T - p1["T"]) / (p2["T"] - p1["T"]) * (var2 - var1)
    if var_target <= 0:
        return None
    return math.sqrt(var_target / target_T) * 100


# -----------------------------------------------------------------------------
# ④ data_quality
# -----------------------------------------------------------------------------


LOW_LIQUIDITY_MAX_OI_WAN = 1.0  # 短桶 max(OI per strike per type) < 此阈值 → 冷门


def _data_quality(
    short_contracts: list[dict],
    total_contracts: int,
    *,
    is_intraday: bool = False,
    pcr_lag_days: int = 0,
) -> dict:
    """active_strikes：≤14d 桶内当日 volume > 0 的 strike 数（同 strike Call+Put 算一个）。

    low_liquidity: 短桶里没有任何一个 (strike, type) 组合的 OI ≥ LOW_LIQUIDITY_MAX_OI_WAN 万张。
        意味着 Wall / Max Pain / 蝴蝶图都建立在噪声上，SKILL.md 走拒绝路径——
        不出 5 段报告，给一段简短诊断 + 引导到其他 skill。
        典型例：低成交量 ETF（如 EWJ / 部分 sector ETF）+ 小盘股。
    is_intraday: fetch 层标记当前是否盘中抓取（ET 9:30-16:00 周一-周五）。
        SKILL.md 用此字段决定是否在报告头加风险提示 banner。
    pcr_lag_days: data_as_of 与 pcr_latest_date 的日数差，反映 PCR 滞后情况。
        broker T+1 节奏；盘中 / 上午 12 点前跑通常滞后 1 天。
        > 0 时 SKILL.md 在报告头加 PCR 时效说明 banner。
    """
    vol_per_strike: dict[float, int] = {}
    oi_per_strike_type: dict = {}
    for c in short_contracts:
        strike = c.get("strike")
        if strike is None:
            continue
        vol_per_strike[strike] = vol_per_strike.get(strike, 0) + int(c.get("volume") or 0)
        key = (strike, c.get("type"))
        oi_per_strike_type[key] = oi_per_strike_type.get(key, 0) + int(c.get("open_interest") or 0)
    active = sum(1 for v in vol_per_strike.values() if v > 0)
    max_strike_oi = max(oi_per_strike_type.values()) if oi_per_strike_type else 0
    low_liquidity = max_strike_oi < LOW_LIQUIDITY_MAX_OI_WAN * 10000
    return {
        "active_strikes": active,
        "reliable": active >= ACTIVE_STRIKES_RELIABLE,
        "low_liquidity": low_liquidity,
        "max_strike_oi_wan": round(max_strike_oi / 10000, 1),
        "contracts_fetched": total_contracts,
        "is_intraday": bool(is_intraday),
        "pcr_lag_days": int(pcr_lag_days),
    }


def _compute_pcr_lag_days(data_as_of: str | None, pcr_latest_date: str | None) -> int:
    """data_as_of 与 pcr_latest_date 的日数差。任一缺失返回 0。"""
    if not data_as_of or not pcr_latest_date:
        return 0
    try:
        d1 = date.fromisoformat(data_as_of)
        d2 = date.fromisoformat(pcr_latest_date)
    except ValueError:
        return 0
    delta = (d1 - d2).days
    return max(delta, 0)


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


def _read_states(
    current_price: float,
    call_wall: dict | None,
    put_wall: dict | None,
    max_pain: dict | None,
    data_quality: dict,
) -> dict:
    """key_levels + data_quality → 几何状态。无新数据、无 IO。"""
    call_thick = _thickness(call_wall)
    put_thick = _thickness(put_wall)
    return {
        "call_wall_proximity": _proximity(call_wall["distance_pct"]) if call_wall else None,
        "put_wall_proximity": _proximity(put_wall["distance_pct"]) if put_wall else None,
        "asymmetry": _asymmetry(call_wall, put_wall),
        "call_wall_thickness": call_thick,
        "put_wall_thickness": put_thick,
        "thin_wall": "薄" in (call_thick, put_thick),
        "max_pain_pull": _max_pain_pull(
            max_pain, current_price, data_quality.get("max_strike_oi_wan")),
        "structure_label": None,
    }


# -----------------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------------


def _round(v: float | None, n: int) -> float | None:
    return None if v is None else round(v, n)


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "mock_raw_payload.json"
    raw = json.loads(src.read_text())
    ai = compute(raw)
    print(json.dumps(ai, indent=2, ensure_ascii=False))
