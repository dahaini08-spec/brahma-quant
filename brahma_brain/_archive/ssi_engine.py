"""
ssi_engine.py — 梵天2.0 Phase 2a · 轧空强度指数 (Short Squeeze Index)
设计院×达摩院 封印 2026-07-20

SSI 公式：
  SSI = (空头比例 - 50%) × |OI变化率| × 价格涨幅 × 1000
  
  SSI 分级：
  < 0.05  → 正常行情，技术分析有效
  0.05~0.10 → ⚠️ 轧空预警，做空风险上升
  0.10~0.15 → 🚨 高强度轧空，禁止做空
  > 0.15  → 🚨🚨 极端轧空，强制触发减空指令

轧空生命周期（SqueezeLifecycle）：
  阶段1 积累：空头60~65%，价格小量上涨
  阶段2 加速：空头65~70%，爆量上涨
  阶段3 峰值：空头70~73%，新高量能萎缩
  阶段4 瓦解：OI骤降+量能枯竭，空头被迫平仓
  阶段5 反转：价格快速下行，轧空结束

设计原则：
  - 零积分消耗：纯本地计算
  - 状态持久化：data/ssi_state.json
  - fail-safe：任何异常不阻断主流程

VERSION = v1.0 · 2026-07-20
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

_STATE_PATH = Path(__file__).parent.parent / 'data' / 'ssi_state.json'

# ─── SSI 等级定义 ─────────────────────────────────────────────
SSI_LEVELS = {
    "EXTREME":  (0.15, "🚨🚨 极端轧空 — 强制减空50%，禁止新开空单"),
    "HIGH":     (0.10, "🚨 高强度轧空 — 禁止做空，现有空单警戒"),
    "WARNING":  (0.05, "⚠️ 轧空预警 — 做空风险上升，谨慎"),
    "NORMAL":   (0.00, "✅ 正常行情 — 技术分析有效"),
}

def get_ssi_level(ssi: float) -> tuple:
    """返回 (level_name, description)"""
    for name, (threshold, desc) in SSI_LEVELS.items():
        if ssi >= threshold:
            return name, desc
    return "NORMAL", SSI_LEVELS["NORMAL"][1]

# ─── 状态持久化 ───────────────────────────────────────────────
def _load_state(symbol: str) -> dict:
    try:
        if _STATE_PATH.exists():
            return json.loads(_STATE_PATH.read_text()).get(symbol, {})
    except Exception:
        pass
    return {}

def _save_state(symbol: str, data: dict):
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        all_s = {}
        if _STATE_PATH.exists():
            try:
                all_s = json.loads(_STATE_PATH.read_text())
            except Exception:
                pass
        all_s[symbol] = data
        _STATE_PATH.write_text(json.dumps(all_s, ensure_ascii=False))
    except Exception:
        pass

# ─── SSI 核心计算 ─────────────────────────────────────────────
def compute_ssi(
    symbol: str,
    short_ratio: float,       # 空头比例 0~1
    oi: float,                # 当前OI
    price: float,             # 当前价格
    vol_current: float = 0,   # 当前根量能
    vol_avg: float = 1,       # 近N根均值量能
    fr_rate: float = 0.0,     # 当前FR
) -> dict:
    """
    计算轧空强度指数并判定阶段
    """
    result = {
        "ssi": 0.0, "level": "NORMAL", "description": "",
        "phase": 1, "phase_name": "积累期",
        "short_ratio": short_ratio, "oi": oi,
        "action": "", "note": ""
    }
    try:
        state = _load_state(symbol)
        prev_oi    = state.get("prev_oi", oi)
        prev_price = state.get("prev_price", price)
        ssi_history = state.get("ssi_history", [])

        # SSI计算
        oi_change_rate = abs(oi - prev_oi) / prev_oi if prev_oi > 0 else 0
        price_change   = max(0.0, (price - prev_price) / prev_price) if prev_price > 0 else 0
        sr_excess      = max(0.0, short_ratio - 0.50)
        ssi = sr_excess * oi_change_rate * price_change * 1000

        # 更新历史（保留最近10个）
        ssi_history.append(round(ssi, 5))
        ssi_history = ssi_history[-10:]

        # 轧空阶段判定
        vol_ratio = vol_current / vol_avg if vol_avg > 0 else 1.0
        phase, phase_name = _detect_phase(
            short_ratio=short_ratio,
            oi=oi, prev_oi=prev_oi,
            price=price, prev_price=prev_price,
            vol_ratio=vol_ratio,
            ssi=ssi,
            ssi_history=ssi_history,
            fr_rate=fr_rate,
        )

        level, desc = get_ssi_level(ssi)

        # 行动建议
        action = ""
        if level == "EXTREME":
            action = "立即减空50% + 禁止新开空单"
        elif level == "HIGH":
            action = "禁止做空，现有空单设止损"
        elif level == "WARNING":
            action = "空单谨慎，仓位减半"
        elif phase == 4:
            action = "轧空瓦解期：可轻仓做空（止损高点+3%）"
        elif phase == 5:
            action = "轧空结束：空单方向可用，等CHoCH确认"

        # 保存状态
        _save_state(symbol, {
            "prev_oi": oi,
            "prev_price": price,
            "ssi_history": ssi_history,
            "last_phase": phase,
            "last_ts": datetime.now(timezone.utc).isoformat(),
        })

        result.update({
            "ssi": round(ssi, 5),
            "level": level,
            "description": desc,
            "phase": phase,
            "phase_name": phase_name,
            "oi_change_pct": round(oi_change_rate * 100, 3),
            "price_change_pct": round(price_change * 100, 3),
            "vol_ratio": round(vol_ratio, 2),
            "ssi_trend": "↑" if len(ssi_history) >= 2 and ssi_history[-1] > ssi_history[-2] else "↓",
            "action": action,
            "note": f"SSI={ssi:.4f} | 阶段{phase}:{phase_name} | {desc}",
        })

    except Exception as e:
        result["note"] = f"[ssi_engine异常,不阻断] {e}"

    return result


def _detect_phase(
    short_ratio, oi, prev_oi, price, prev_price,
    vol_ratio, ssi, ssi_history, fr_rate
) -> tuple:
    """
    判定轧空生命周期阶段
    返回 (phase_int, phase_name)
    """
    oi_change_pct = (oi - prev_oi) / prev_oi if prev_oi > 0 else 0
    price_up = price > prev_price

    # 阶段5：反转（价格已开始下行+OI下降）
    if price < prev_price and oi_change_pct < -0.01 and short_ratio < 0.68:
        return 5, "反转期"

    # 阶段4：瓦解（量能枯竭+OI骤降+价格仍高位）
    if vol_ratio < 0.30 and oi_change_pct < -0.02 and price >= prev_price:
        return 4, "瓦解期"

    # 阶段3：峰值（空头>70%+新高但量能开始萎缩）
    if short_ratio > 0.70 and price_up and vol_ratio < 0.60:
        return 3, "峰值期"

    # 阶段2：加速（空头65~70%+爆量上涨）
    if short_ratio > 0.65 and price_up and vol_ratio > 3.0:
        return 2, "加速期"

    # 阶段1：积累
    return 1, "积累期"


# ─── 轧空结束信号（供外部调用）──────────────────────────────
def is_squeeze_over(symbol: str, consecutive_bars: int = 3) -> dict:
    """
    判断轧空是否已结束（阶段4/5持续N根K线）
    返回 {over: bool, confidence: float, reason: str}
    """
    try:
        state = _load_state(symbol)
        last_phase = state.get("last_phase", 1)
        ssi_history = state.get("ssi_history", [])

        # 最近N个SSI是否在下降
        ssi_declining = (
            len(ssi_history) >= 3 and
            ssi_history[-1] < ssi_history[-2] < ssi_history[-3]
        )

        over = last_phase >= 4 and ssi_declining
        confidence = 0.74 if last_phase == 5 else (0.55 if last_phase == 4 else 0.20)

        return {
            "over": over,
            "confidence": confidence,
            "phase": last_phase,
            "ssi_declining": ssi_declining,
            "reason": f"阶段{last_phase}+SSI{'下降' if ssi_declining else '未下降'} → 轧空{'已结束' if over else '未结束'}",
        }
    except Exception as e:
        return {"over": False, "confidence": 0, "reason": f"[异常] {e}"}
