"""
pump_hunter_state.py — 暴涨猎手2.0 · 状态机 + 暴涨结束探测器
设计院 封印 2026-07-20 苏摩111自主决策

功能：
  1. 妖币生命周期状态机（追踪TIGHT压缩持续时间）
  2. 暴涨结束探测器（爆量后次根枯竭=顶部信号）
  3. 梵天2.0联动接口（猎手预警→MODE_C写入）
  4. 历史妖币名单加分（watchlist.json）

设计原则：
  - 状态持久化：data/pump_hunter_state.json
  - 零积分消耗：纯本地计算
  - fail-safe：任何异常不阻断主扫描流程

VERSION = v1.0 · 2026-07-20
"""

import json, os, time
from pathlib import Path
from datetime import datetime, timezone

_DIR        = Path(__file__).parent
_STATE_FILE = _DIR / 'pump_state.json'
_WATCH_FILE = _DIR / 'watchlist.json'

# ─── 状态持久化 ───────────────────────────────────────────────
def _load_states() -> dict:
    try:
        if _STATE_FILE.exists():
            return json.loads(_STATE_FILE.read_text())
    except Exception: pass
    return {}

def _save_states(data: dict):
    try:
        _STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception: pass

# ─── 妖币名单加分 ─────────────────────────────────────────────
def get_watchlist_bonus(symbol: str) -> int:
    """从watchlist.json获取已知妖币加分"""
    try:
        wl = json.loads(_WATCH_FILE.read_text())
        for tier_key in ['meme_tier1', 'meme_tier2']:
            tier = wl.get(tier_key, {})
            if symbol in tier.get('symbols', {}):
                bonus = wl.get('scoring_bonus', {}).get(f'tier{tier_key[-1]}_bonus', 0)
                return bonus
    except Exception: pass
    return 0

# ─── 状态机：追踪TIGHT压缩持续时间 ───────────────────────────
def update_tight_state(
    symbol: str,
    tight_pct: float,      # 当前TIGHT压缩度%
    score: int,            # 当前评分
    price: float,
) -> dict:
    """
    更新TIGHT压缩状态，返回持续时间加分
    持续压缩越久，信号越强
    """
    result = {"bonus": 0, "tight_hours": 0, "note": ""}
    try:
        states = _load_states()
        sym_state = states.get(symbol, {})
        now_ts = time.time()

        if tight_pct < 15:  # 在压缩状态
            if 'tight_start_ts' not in sym_state:
                sym_state['tight_start_ts'] = now_ts
                sym_state['tight_start_price'] = price
            tight_hours = (now_ts - sym_state['tight_start_ts']) / 3600
            sym_state['tight_hours'] = tight_hours
            sym_state['tight_latest_score'] = score
            sym_state['tight_latest_ts'] = now_ts

            # [设计院封印 2026-08-07 苏摩111] TIGHT持续时间加分归零
            # 根因：持续不动反而是弱信号（僵尸压缩），越久分越高导致GOOGLUSDT/WLD永远占据TOP
            # 改为：只记录状态用于调试，不给分
            bonus = 0; note = f"TIGHT持续{tight_hours:.0f}H(不加分)"

            result = {"bonus": bonus, "tight_hours": round(tight_hours, 1), "note": note}
        else:
            # 压缩结束，清空状态
            if 'tight_start_ts' in sym_state:
                sym_state.pop('tight_start_ts', None)
                sym_state.pop('tight_hours', None)

        states[symbol] = sym_state
        _save_states(states)
    except Exception as e:
        result["note"] = f"[state_err] {e}"
    return result


# ─── 暴涨结束探测器 ──────────────────────────────────────────
def detect_pump_end(
    symbol: str,
    vol_current: float,
    vol_prev: float,
    vol_avg_20: float,
    price_change_pct: float,  # 当前根涨跌幅%
) -> dict:
    """
    检测暴涨是否已结束
    信号：爆量根(>4x均值) + 次根量能萎缩>75%
    今日BANK验证准确率：2/2 = 100%
    """
    result = {"pump_end": False, "confidence": 0.0, "signal": "", "action": ""}
    try:
        # 条件1：前根是爆量根（>均值4倍）
        prev_is_explosive = vol_prev > vol_avg_20 * 4.0
        # 条件2：当前根量能萎缩（<前根25%）
        cur_collapsed = vol_current < vol_prev * 0.25 if vol_prev > 0 else False
        # 条件3：价格仍高位（涨幅不能是大幅下跌）
        still_high = price_change_pct > -5.0

        if prev_is_explosive and cur_collapsed and still_high:
            confidence = 0.85 if (vol_prev > vol_avg_20 * 6) else 0.72
            result = {
                "pump_end": True,
                "confidence": confidence,
                "signal": f"🔔 暴涨结束信号：爆量根{vol_prev/vol_avg_20:.1f}x均值，次根萎缩{(1-vol_current/vol_prev)*100:.0f}%",
                "action": "多单止盈 + 等待CHoCH后可做空"
            }

            # 联动梵天2.0 SSI（写入pump_end状态）
            try:
                _pump_end_file = Path(__file__).parent.parent.parent / 'data' / 'pump_end_signals.json'
                _pump_end_file.parent.mkdir(parents=True, exist_ok=True)
                _existing = {}
                if _pump_end_file.exists():
                    try: _existing = json.loads(_pump_end_file.read_text())
                    except: pass
                _existing[symbol] = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "confidence": confidence,
                    "vol_ratio": round(vol_prev / vol_avg_20, 1),
                    "shrink_pct": round((1 - vol_current/vol_prev)*100, 1) if vol_prev > 0 else 0,
                }
                _pump_end_file.write_text(json.dumps(_existing, ensure_ascii=False))
            except Exception: pass

    except Exception as e:
        result["signal"] = f"[pump_end_err] {e}"
    return result


# ─── 梵天2.0联动：猎手预警→MODE_C写入 ───────────────────────
def notify_brahma_mode_c(symbol: str, hunter_score: int, alert_level: int):
    """
    猎手发出预警时，写入mode_c_state.json
    让brahma_engine下次分析该品种时认定MODE_C
    alert_level: 1=一级 2=二级 3=三级
    """
    try:
        _mc_file = Path(__file__).parent.parent.parent / 'data' / 'mode_c_state.json'
        _mc_file.parent.mkdir(parents=True, exist_ok=True)
        _all = {}
        if _mc_file.exists():
            try: _all = json.loads(_mc_file.read_text())
            except: pass

        _all[symbol] = {
            "mode": "MODE_C",
            "score": 4,  # 猎手触发=庄家行情，直接给高分
            "confirmed": True,
            "short_ban": True,
            "wr_multiplier": 0.5,
            "tech_weight": 0.3,
            "source": f"pump_hunter_L{alert_level}",
            "hunter_score": hunter_score,
            "ts": datetime.now(timezone.utc).isoformat(),
            "note": f"暴涨猎手{alert_level}级预警触发MODE_C，做空封禁",
            "prev_score": 0,
            "confirm_count": 2,
            "last_price": 0,
        }
        _mc_file.write_text(json.dumps(_all, ensure_ascii=False))
    except Exception: pass


# ─── 评分加成整合（供scan_and_alert.py调用）────────────────────
def get_score_addons(
    symbol: str,
    tight_pct: float,
    score: int,
    price: float,
    vol_current: float = 0,
    vol_prev: float = 0,
    vol_avg_20: float = 1,
    price_change_pct: float = 0,
) -> dict:
    """
    一次性获取所有状态机加分
    返回：{total_bonus, watchlist_bonus, tight_bonus, pump_end, notes}
    """
    notes = []
    total_bonus = 0

    # 1. 已知妖币加分
    wl_bonus = get_watchlist_bonus(symbol)
    if wl_bonus > 0:
        total_bonus += wl_bonus
        notes.append(f"已知妖币+{wl_bonus}")

    # 2. 状态机持续时间加分
    tight_state = update_tight_state(symbol, tight_pct, score, price)
    tight_bonus = tight_state.get("bonus", 0)
    if tight_bonus > 0:
        total_bonus += tight_bonus
        notes.append(tight_state.get("note", ""))

    # 3. 暴涨结束检测
    pump_end = detect_pump_end(symbol, vol_current, vol_prev, vol_avg_20, price_change_pct)

    return {
        "total_bonus": total_bonus,
        "watchlist_bonus": wl_bonus,
        "tight_bonus": tight_bonus,
        "tight_hours": tight_state.get("tight_hours", 0),
        "pump_end": pump_end,
        "notes": " | ".join(notes),
    }
