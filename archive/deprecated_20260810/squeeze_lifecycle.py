"""
squeeze_lifecycle.py — 梵天2.0 Phase 2b · 轧空生命周期追踪器
设计院×达摩院 封印 2026-07-20
[P2 Phase1.5临界期预警 2026-08-09 苏摩111]

功能：
  追踪轧空从积累到结束的完整生命周期
  在「瓦解期/反转期」自动解锁做空权限

Phase设计（v2.0）：
  1   积累期   空头<65%，量比任意，观望
  1.5 临界期   空头>62% + 量比0.2~1.2（枯竭开始回升）→ ⭐预判预警
  2   加速期   空头>65% + 价格+5% + 量比>3.0（已爆量）
  3   峰值期   空头>70% + 价格上涨 + 量比<0.5
  4   瓦解期   量极度萎缩 + OI下降 + 价格企稳
  5   反转期   价格下跌 + OI暴降 + 空头<68%

核心设计理念（P2封印）：
  Phase2要求vol_ratio>3.0 = 爆量后才触发 = 确认型（苏摩已错过布局）
  Phase1.5在量比0.2~1.2时触发 = 枯竭刚开始回升 = 预判型（最佳布局窗口）

VERSION = v2.0 · 2026-08-09
"""

import json
from pathlib import Path
from datetime import datetime, timezone

_STATE_PATH = Path(__file__).parent.parent / 'data' / 'squeeze_lifecycle_state.json'

PHASES = {
    1:   {"name": "积累期",  "short_allowed": False, "note": "空头<65%，保持观望"},
    1.5: {"name": "临界期",  "short_allowed": False, "note": "⭐空头拥挤+量能刚回升，轧空即将发动，预判布局窗口"},
    2:   {"name": "加速期",  "short_allowed": False, "note": "爆量拉升，严禁做空"},
    3:   {"name": "峰值期",  "short_allowed": False, "note": "量能枯竭，等待反转信号"},
    4:   {"name": "瓦解期",  "short_allowed": True,  "note": "轧空接近尾声，可轻仓做空"},
    5:   {"name": "反转期",  "short_allowed": True,  "note": "轧空结束，空单有结构支撑"},
}

# Phase1.5 临界期判定参数（P2封印 2026-08-09）
PHASE_15_SHORT_RATIO_MIN = 0.62   # 空头>62%（拥挤但未峰值）
PHASE_15_VOL_RATIO_MIN   = 0.20   # 量比>0.2（不是绝对枯竭）
PHASE_15_VOL_RATIO_MAX   = 1.20   # 量比<1.2（未爆量，仍是蓄力阶段）
PHASE_15_PRICE_CHG_MIN   = -0.01  # 价格不能在大跌（-1%以内）

def _load(symbol): 
    try:
        if _STATE_PATH.exists():
            return json.loads(_STATE_PATH.read_text()).get(symbol, {})
    except Exception: pass
    return {}

def _save(symbol, data):
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        all_s = {}
        if _STATE_PATH.exists():
            try: all_s = json.loads(_STATE_PATH.read_text())
            except Exception: pass
        all_s[symbol] = data
        _STATE_PATH.write_text(json.dumps(all_s, ensure_ascii=False))
    except Exception: pass

def update(
    symbol: str,
    short_ratio: float,
    oi: float,
    price: float,
    vol_current: float,
    vol_avg: float,
    price_high_session: float = 0.0,
) -> dict:
    """
    更新轧空生命周期状态，返回当前阶段和行动建议
    """
    result = {"phase": 1, "phase_name": "积累期", "short_allowed": False,
              "action": "", "squeeze_peak": 0.0, "note": ""}
    try:
        state = _load(symbol)
        prev_oi    = state.get("prev_oi", oi)
        prev_price = state.get("prev_price", price)
        phase_hist = state.get("phase_history", [])
        squeeze_peak = state.get("squeeze_peak", 0.0)

        # 计算变化指标
        oi_chg     = (oi - prev_oi) / prev_oi if prev_oi > 0 else 0
        price_chg  = (price - prev_price) / prev_price if prev_price > 0 else 0
        vol_ratio  = vol_current / vol_avg if vol_avg > 0 else 1.0

        # 更新峰值价格
        if price > squeeze_peak:
            squeeze_peak = price

        # 阶段判定（v2.0：Phase1.5临界期优先判断）
        if price < prev_price and oi_chg < -0.01 and short_ratio < 0.68:
            phase = 5
        elif vol_ratio < 0.25 and oi_chg < -0.015 and price >= prev_price * 0.98:
            phase = 4
        elif short_ratio > 0.70 and price_chg > 0 and vol_ratio < 0.50:
            phase = 3
        elif short_ratio > 0.65 and price_chg > 0.05 and vol_ratio > 3.0:
            phase = 2
        elif (short_ratio >= PHASE_15_SHORT_RATIO_MIN
              and PHASE_15_VOL_RATIO_MIN <= vol_ratio <= PHASE_15_VOL_RATIO_MAX
              and price_chg >= PHASE_15_PRICE_CHG_MIN):
            # [P2 Phase1.5] 临界期：空头拥挤 + 量能刚从枯竭回升 + 价格未大跌
            # 这是轧空发动前的最佳预判布局窗口
            phase = 1.5
        else:
            phase = 1

        phase_hist.append(phase)
        phase_hist = phase_hist[-20:]

        # 阶段4/5持续确认（连续N根）
        recent_phases = phase_hist[-3:]
        phase_confirmed = len(recent_phases) >= 2 and all(p >= 4 for p in recent_phases)

        p_info = PHASES[phase]
        short_allowed = p_info["short_allowed"] and phase_confirmed

        # 行动建议
        if phase == 5 and phase_confirmed:
            action = f"✅ 轧空已结束（反转期确认）→ 可做空，止损={squeeze_peak*1.03:.4f}（峰值+3%）"
        elif phase == 4 and phase_confirmed:
            action = "⚡ 轧空瓦解期 → 轻仓做空（0.5%NAV），等CHoCH确认加仓"
        elif phase == 3:
            action = "👀 峰值期观察 → 等量能枯竭信号（次根量能<前根20%）"
        elif phase == 2:
            action = "🚫 加速期 → 禁止做空，多单可持有"
        elif phase == 1.5:
            action = (f"⭐ [临界期预警] 空头拥挤({short_ratio:.0%})+量能刚回升({vol_ratio:.2f}x)"
                      f" → 轧空即将发动，可预判多头布局（0.5%NAV）")
        else:
            action = "⏳ 积累期 → 观望，积分节省模式"

        _save(symbol, {
            "prev_oi": oi, "prev_price": price,
            "phase_history": phase_hist,
            "squeeze_peak": squeeze_peak,
            "last_phase": phase,
            "last_ts": datetime.now(timezone.utc).isoformat(),
        })

        result.update({
            "phase": phase,
            "phase_name": p_info["name"],
            "short_allowed": short_allowed,
            "phase_confirmed": phase_confirmed,
            "squeeze_peak": squeeze_peak,
            "action": action,
            "vol_ratio": round(vol_ratio, 2),
            "oi_chg_pct": round(oi_chg * 100, 3),
            "note": p_info["note"],
        })
    except Exception as e:
        result["note"] = f"[squeeze_lifecycle异常,不阻断] {e}"
    return result
