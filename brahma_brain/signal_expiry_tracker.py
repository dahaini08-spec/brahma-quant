"""
signal_expiry_tracker.py — 梵天2.0 Phase 3b · 信号有效期追踪
设计院×达摩院 封印 2026-07-20

功能：
  追踪入场信号的有效期
  超过有效期后发出「信号过期警告」
  防止「扛单」（持仓超过信号有效窗口）

信号有效期规则：
  RSI超买做空：4H（超买状态可能持续但方向无效）
  MACD死叉：8H（死叉信号通常在8H内见效）
  CHoCH结构：24H（结构转换信号持续较长）
  FVG填满：12H（缺口填满后效应持续时间）
  庄家行情顺多：持续至MODE_C结束

VERSION = v1.0 · 2026-07-20
"""

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

_STATE_PATH = Path(__file__).parent.parent / 'data' / 'signal_expiry.json'

# 各信号类型有效期（小时）
SIGNAL_TTL = {
    "RSI_OB":      4,    # RSI超买，4H内有效
    "RSI_OS":      4,    # RSI超卖
    "MACD_DC":     8,    # MACD死叉
    "MACD_GC":     8,    # MACD金叉
    "CHoCH":      24,    # 结构转换
    "BOS":        12,    # 结构突破
    "FVG_FILL":   12,    # FVG填满
    "OB_TOUCH":    6,    # OB触及
    "SSI_END":    16,    # 轧空结束
    "MODE_C_LONG": 48,   # 庄家模式顺多
    "DEFAULT":     8,    # 默认
}

def _load():
    try:
        if _STATE_PATH.exists():
            return json.loads(_STATE_PATH.read_text())
    except Exception: pass
    return {}

def _save(data):
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception: pass


def register(
    symbol: str,
    signal_type: str,
    direction: str,
    entry_price: float,
    entry_ts: Optional[str] = None,
) -> dict:
    """
    注册入场信号，记录有效期
    """
    now = entry_ts or datetime.now(timezone.utc).isoformat()
    ttl_hours = SIGNAL_TTL.get(signal_type, SIGNAL_TTL["DEFAULT"])

    try:
        entry_dt = datetime.fromisoformat(now.replace('Z', '+00:00'))
        expiry   = (entry_dt + timedelta(hours=ttl_hours)).isoformat()
    except Exception:
        expiry = "未知"

    record = {
        "symbol": symbol,
        "signal_type": signal_type,
        "direction": direction,
        "entry_price": entry_price,
        "entry_ts": now,
        "expiry_ts": expiry,
        "ttl_hours": ttl_hours,
        "status": "ACTIVE",
    }

    all_signals = _load()
    key = f"{symbol}_{signal_type}_{direction}"
    all_signals[key] = record
    _save(all_signals)
    return record


def check_expiry(symbol: str, signal_type: str, direction: str) -> dict:
    """
    检查信号是否已过期
    返回 {expired, hours_remaining, warning, action}
    """
    result = {"expired": False, "hours_remaining": 999, "warning": "", "action": ""}
    try:
        all_signals = _load()
        key = f"{symbol}_{signal_type}_{direction}"
        record = all_signals.get(key)
        if not record:
            return result

        now = datetime.now(timezone.utc)
        expiry = datetime.fromisoformat(record["expiry_ts"].replace('Z', '+00:00'))
        entry  = datetime.fromisoformat(record["entry_ts"].replace('Z', '+00:00'))

        hours_held      = (now - entry).total_seconds() / 3600
        hours_remaining = (expiry - now).total_seconds() / 3600
        expired = hours_remaining <= 0

        warning = ""
        action  = ""

        if expired:
            warning = (
                f"⚠️ 信号过期！{signal_type} {direction}信号已持续{hours_held:.1f}H"
                f"（有效期{record['ttl_hours']}H），原入场逻辑不再成立"
            )
            action = "建议止损离场，重新等待有效信号"
            # 更新状态
            record["status"] = "EXPIRED"
            all_signals[key] = record
            _save(all_signals)
        elif hours_remaining < 2:
            warning = f"⚠️ 信号即将过期（剩余{hours_remaining:.1f}H），评估是否继续持有"
            action = "建议设置止损，信号失效后不继续扛单"

        result.update({
            "expired": expired,
            "hours_held": round(hours_held, 1),
            "hours_remaining": round(max(0, hours_remaining), 1),
            "warning": warning,
            "action": action,
            "signal_type": signal_type,
            "entry_price": record["entry_price"],
            "ttl_hours": record["ttl_hours"],
        })
    except Exception as e:
        result["warning"] = f"[signal_expiry_tracker异常,不阻断] {e}"
    return result


def check_all(symbol: str) -> list:
    """检查symbol所有信号的过期状态"""
    results = []
    try:
        all_signals = _load()
        for key, record in all_signals.items():
            if record.get("symbol") == symbol and record.get("status") == "ACTIVE":
                r = check_expiry(symbol, record["signal_type"], record["direction"])
                if r.get("warning"):
                    results.append(r)
    except Exception: pass
    return results


def get_hold_hours(symbol: str, signal_type: str, direction: str) -> float:
    """获取已持仓小时数"""
    try:
        all_signals = _load()
        key = f"{symbol}_{signal_type}_{direction}"
        record = all_signals.get(key)
        if not record:
            return 0.0
        entry = datetime.fromisoformat(record["entry_ts"].replace('Z', '+00:00'))
        return (datetime.now(timezone.utc) - entry).total_seconds() / 3600
    except Exception:
        return 0.0
