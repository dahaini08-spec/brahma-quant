"""
mode_c_detector.py — 梵天2.0 Phase 1a · MODE_C 庄家行情识别器
设计院×达摩院 封印 2026-07-20

职责：
  识别「庄家控盘妖币行情」(MODE_C)，在此模式下：
  1. 封禁所有新增做空信号
  2. 所有空单 WR 降权 ×0.5
  3. Kronos/技术指标权重降至 30%
  4. 输出对冲组合健康指数

设计原则：
  - 零阻断：任何异常均不影响 brahma_engine 主流程
  - 最小侵入：不修改任何现有模块，仅作为叠加层
  - 积分节省：仅在需要时调用，不产生额外 API 请求
  - 单例缓存：同一 symbol 同一轮分析只运行一次

触发条件（满足 score≥3/5 且持续 2 根 K 线）：
  C1 空头比例 > 65%
  C2 累计涨幅（从最近低点）> 50%
  C3 单根量能 > 近 20 根均值 5 倍
  C4 单根振幅 > 6%
  C5 FR 连续满值 > 3 期（+0.005%）

VERSION = v1.0 · 2026-07-20
"""

import os, time, json
from pathlib import Path
from datetime import datetime, timezone

# ─── 常量 ────────────────────────────────────────────────────
_STATE_PATH = Path(__file__).parent.parent / 'data' / 'mode_c_state.json'
_MIN_SCORE  = 3    # 触发阈值（满足条件数）
_CONFIRM_BARS = 2  # 连续N根才确认

# ─── 状态持久化 ───────────────────────────────────────────────
def _load_state(symbol: str) -> dict:
    try:
        if _STATE_PATH.exists():
            s = json.loads(_STATE_PATH.read_text())
            return s.get(symbol, {})
    except Exception:
        pass
    return {}

def _save_state(symbol: str, data: dict):
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        all_states = {}
        if _STATE_PATH.exists():
            try:
                all_states = json.loads(_STATE_PATH.read_text())
            except Exception:
                pass
        all_states[symbol] = data
        _STATE_PATH.write_text(json.dumps(all_states, ensure_ascii=False))
    except Exception:
        pass

# ─── 核心检测函数 ─────────────────────────────────────────────
def detect(
    symbol: str,
    price: float,
    price_low_24h: float,
    short_ratio: float,        # 空头比例 0~1
    vol_current: float,        # 当前根量能
    vol_avg_20: float,         # 近20根均量
    candle_high: float,
    candle_low: float,
    fr_rate: float = 0.0,      # 最新资金费率
    fr_saturation_count: int = 0,  # FR连续满值期数
) -> dict:
    """
    返回：
    {
        "mode": "MODE_C" | "MODE_B" | "MODE_A",
        "score": int,           # 满足条件数
        "confirmed": bool,      # 是否连续2根确认
        "short_ban": bool,      # 是否封禁做空
        "wr_multiplier": float, # 空单WR乘数
        "tech_weight": float,   # 技术指标权重
        "note": str,            # 说明文字
        "conditions": dict,     # 各条件明细
    }
    """
    result = {
        "mode": "MODE_A", "score": 0, "confirmed": False,
        "short_ban": False, "wr_multiplier": 1.0, "tech_weight": 1.0,
        "note": "", "conditions": {}
    }

    try:
        # 计算各条件
        cum_change = (price - price_low_24h) / price_low_24h if price_low_24h > 0 else 0
        vol_ratio  = vol_current / vol_avg_20 if vol_avg_20 > 0 else 0
        candle_amp = (candle_high - candle_low) / candle_low if candle_low > 0 else 0

        c1 = short_ratio > 0.65
        c2 = cum_change  > 0.50
        c3 = vol_ratio   > 5.0
        c4 = candle_amp  > 0.06
        c5 = fr_saturation_count >= 3

        score = sum([c1, c2, c3, c4, c5])

        result["conditions"] = {
            "C1_空头>65%":     {"ok": c1, "val": f"{short_ratio:.1%}"},
            "C2_涨幅>50%":     {"ok": c2, "val": f"{cum_change:.1%}"},
            "C3_量能>5x均值":  {"ok": c3, "val": f"{vol_ratio:.1f}x"},
            "C4_振幅>6%":      {"ok": c4, "val": f"{candle_amp:.1%}"},
            "C5_FR满值≥3期":   {"ok": c5, "val": f"{fr_saturation_count}期"},
        }
        result["score"] = score

        # 加载历史状态（用于连续确认）
        state = _load_state(symbol)
        prev_score = state.get("prev_score", 0)
        confirm_count = state.get("confirm_count", 0)

        # 更新连续计数
        if score >= _MIN_SCORE:
            confirm_count = min(confirm_count + 1, 5)
        else:
            confirm_count = 0

        _save_state(symbol, {
            "prev_score": score,
            "confirm_count": confirm_count,
            "last_ts": datetime.now(timezone.utc).isoformat(),
            "last_price": price
        })

        confirmed = confirm_count >= _CONFIRM_BARS

        # 判定模式
        # MODE_C：score≥3且已确认（连续2根），或score≥4立即确认
        if score >= _MIN_SCORE and (confirmed or score >= 4):
            mode = "MODE_C"
        elif score >= 2 or cum_change > 0.30:
            mode = "MODE_B"
        else:
            mode = "MODE_A"

        result["mode"] = mode
        result["confirmed"] = confirmed

        # 模式对应行为
        if mode == "MODE_C":
            result["short_ban"]      = True
            result["wr_multiplier"]  = 0.50   # 空单WR降权50%
            result["tech_weight"]    = 0.30   # 技术分析降权至30%
            conds_hit = [k for k, v in result["conditions"].items() if v["ok"]]
            result["note"] = (
                f"⚠️ MODE_C庄家行情：满足{score}/5条件"
                f"（{', '.join(conds_hit)}），"
                f"已确认{confirm_count}根K线。"
                f"做空封禁，WR×0.5，技术指标权重×0.3"
            )
        elif mode == "MODE_B":
            result["short_ban"]     = False
            result["wr_multiplier"] = 0.75
            result["tech_weight"]   = 0.70
            result["note"] = f"MODE_B高波动行情：score={score}/5，技术分析降权至70%"
        else:
            result["note"] = f"MODE_A正常行情：score={score}/5，技术分析全权重"

    except Exception as e:
        result["note"] = f"[mode_c_detector异常,不阻断] {e}"

    return result


# ─── 对冲组合健康指数 ─────────────────────────────────────────
def hedge_health(
    short_notional: float,
    long_notional: float,
    short_entry: float,
    long_entry: float,
    current_price: float,
    leverage: int = 5,
    realized_pnl: float = 0.0,
    fr_per_8h: float = 0.00005,   # 资金费率/8H
    hold_hours: float = 0.0,
) -> dict:
    """
    对冲组合健康报告
    返回多空比、净δ、强平缓冲、时间成本、建议操作
    """
    result = {}
    try:
        ratio = short_notional / long_notional if long_notional > 0 else 999

        # 净δ：价格每涨1%的净损益
        net_delta = (long_notional - short_notional) * leverage / 100

        # 浮动盈亏（注意：notional是已包含杠杆的合约名义，不再×leverage）
        # 与 Binance 展示的浮动盈亏一致
        short_pnl = -(current_price - short_entry) / short_entry * short_notional
        long_pnl  =  (current_price - long_entry)  / long_entry  * long_notional
        net_float = short_pnl + long_pnl
        total_pnl = realized_pnl + net_float

        # 时间成本（FR）
        fr_cost_per_h = short_notional * fr_per_8h / 8
        total_fr_cost = fr_cost_per_h * hold_hours

        # 强平价估算（简化：假设组合保证金共用）
        margin_total = short_notional / leverage + long_notional / leverage
        liq_buffer_pct = (margin_total / (short_notional + long_notional)) * 100

        # 健康评级
        if ratio > 2.5:
            grade = "🔴 危险"
            action = f"立即减空50%（空/多={ratio:.1f}:1，每涨1%净亏{abs(net_delta):.0f}USDT）"
        elif ratio > 1.8:
            grade = "🟡 注意"
            action = f"建议减空至1.5:1（当前空/多={ratio:.1f}:1）"
        elif ratio > 0.5:
            grade = "🟢 健康"
            action = "无需调整"
        else:
            grade = "🟡 注意"
            action = f"多单过大（多/空={1/ratio:.1f}:1），考虑减多"

        result = {
            "grade": grade,
            "ratio": round(ratio, 2),
            "net_delta_per_pct": round(net_delta, 0),
            "short_pnl": round(short_pnl, 0),
            "long_pnl": round(long_pnl, 0),
            "net_float": round(net_float, 0),
            "total_pnl": round(total_pnl, 0),
            "fr_cost_per_h": round(fr_cost_per_h, 1),
            "total_fr_cost": round(total_fr_cost, 1),
            "action": action,
            "triggers": {
                "P0_减空50%": f"价格涨至强平价-15%",
                "P1_多单止盈": f"多单盈利达入场价+{leverage*8:.0f}%时",
                "P2_调仓线":   f"空/多比>{ratio:.1f} → 减空至1:1",
                "P3_时间止损": f"持仓超72H未回本 → 全平重新布局",
            }
        }
    except Exception as e:
        result = {"grade": "⚠️ 计算异常", "action": str(e)}

    return result


# ─── EV期望值过滤器 ───────────────────────────────────────────
# WR基准表（基于梵天历史实盘数据，每月从 performance_logger 更新）
_WR_BASE = {
    # (信号类型, 方向, 模式): 胜率
    ("RSI_OB",      "SHORT", "MODE_C"): 0.28,
    ("RSI_OB",      "SHORT", "MODE_B"): 0.42,
    ("RSI_OB",      "SHORT", "MODE_A"): 0.55,
    ("MACD_DC",     "SHORT", "MODE_C"): 0.35,
    ("MACD_DC",     "SHORT", "MODE_A"): 0.58,
    ("CHoCH",       "SHORT", "MODE_A"): 0.68,
    ("CHoCH",       "SHORT", "MODE_B"): 0.58,
    ("FVG_FILL",    "LONG",  "MODE_C"): 0.72,
    ("SSI_END",     "SHORT", "MODE_C"): 0.65,  # 修正：轧空结束后做空WR调低至真实水平
    ("STRUCTURE",   "SHORT", "MODE_A"): 0.62,
    # 默认
    ("DEFAULT",     "SHORT", "MODE_C"): 0.38,
    ("DEFAULT",     "SHORT", "MODE_A"): 0.52,
    ("DEFAULT",     "LONG",  "MODE_C"): 0.60,
}

def ev_filter(
    signal_type: str,
    direction: str,
    mode: str,
    position_pct: float = 0.05,  # 占账户比例
    leverage: int = 5,
    rr_ratio: float = 1.5,
    mode_c_wr_penalty: float = 0.50,  # MODE_C下WR惩罚
) -> dict:
    """
    计算入场期望值并给出入场建议
    EV = WR×(position_pct×leverage×rr) - (1-WR)×(position_pct×leverage)
    """
    try:
        # 查找基准胜率
        wr = _WR_BASE.get(
            (signal_type, direction, mode),
            _WR_BASE.get(("DEFAULT", direction, mode), 0.45)
        )

        # MODE_C惩罚
        if mode == "MODE_C" and direction == "SHORT":
            wr = wr * mode_c_wr_penalty

        avg_win  = position_pct * leverage * rr_ratio
        avg_loss = position_pct * leverage
        ev = wr * avg_win - (1 - wr) * avg_loss

        approved = ev >= 0.02  # 最低EV门槛

        return {
            "signal": signal_type,
            "direction": direction,
            "mode": mode,
            "win_rate": round(wr, 3),
            "rr_ratio": rr_ratio,
            "ev": round(ev, 4),
            "approved": approved,
            "verdict": "✅ 允许入场" if approved else f"❌ EV={ev:.4f}<0.02，拒绝入场",
        }
    except Exception as e:
        return {"approved": True, "ev": 0, "verdict": f"[EV计算异常,默认通过] {e}"}


# ─── 快捷检查（供 brahma_engine 直接调用）────────────────────
def quick_mode_check(ms: dict, extra_data: dict) -> dict:
    """
    从 brahma_engine 的 ms + extra_data 快速提取数据并运行 MODE_C 检测
    供 brahma_engine.analyze() 内嵌调用，fail-safe设计
    """
    try:
        sym    = ms.get('symbol', '?')
        price  = float(ms.get('price', 0))
        low24h = float(ms.get('low24h', price * 0.5) or price * 0.5)

        # 多空比
        sentiment = ms.get('sentiment', {})
        short_r = 1.0 - float(sentiment.get('long_short_ratio', 0.5) or 0.5)

        # 量能（从 extra_data 获取K线）
        klines = extra_data.get('klines_15m', []) if extra_data else []
        vol_cur = float(klines[-1][5]) if klines and len(klines[-1]) > 5 else 0
        vol_avg = sum(float(k[5]) for k in klines[-20:]) / 20 if len(klines) >= 20 else vol_cur or 1
        candle_h = float(klines[-1][2]) if klines and len(klines[-1]) > 2 else price * 1.01
        candle_l = float(klines[-1][3]) if klines and len(klines[-1]) > 3 else price * 0.99

        # FR满值计数
        funding = extra_data.get('funding_history', []) if extra_data else []
        fr_count = sum(1 for f in funding[-6:] if abs(float(f.get('fundingRate', 0))) >= 0.000049)
        fr_rate  = float(funding[-1].get('fundingRate', 0)) if funding else 0

        return detect(
            symbol=sym, price=price, price_low_24h=low24h,
            short_ratio=short_r, vol_current=vol_cur, vol_avg_20=vol_avg,
            candle_high=candle_h, candle_low=candle_l,
            fr_rate=fr_rate, fr_saturation_count=fr_count
        )
    except Exception as e:
        return {
            "mode": "MODE_A", "score": 0, "confirmed": False,
            "short_ban": False, "wr_multiplier": 1.0, "tech_weight": 1.0,
            "note": f"[quick_mode_check异常,不阻断] {e}"
        }
