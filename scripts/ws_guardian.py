#!/usr/bin/env python3
"""
ws_guardian.py
梵天 · WebSocket持仓守卫
苏摩111封印 2026-07-19

功能：当有持仓时，实时监控价格接近止损线的风险
      通过REST轮询替代WebSocket（避免长连接依赖）
      每次运行检查一次，由cron调度

触发推送条件：
  止损距离 < 0.5% → 🚨 立即告警
  止损距离 < 1.0% → ⚠️ 接近告警
  持仓方向与当前体制严重冲突 → ⚡ 体制冲突
"""
import sys
import json
import requests
from pathlib import Path

BASE = Path(__file__).parent.parent
POSITIONS_FILE = BASE / "data" / "wuqu_positions.json"
STATE_FILE = BASE / "data" / "brahma_state.json"

SL_ALERT_PCT = 0.5   # 距止损<0.5%立即告警
SL_WARN_PCT  = 1.0   # 距止损<1.0%预警


def get_mark_price(symbol: str) -> float:
    try:
        r = requests.get(
            f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}",
            timeout=5
        ).json()
        return float(r.get("price", 0))
    except Exception:
        return 0.0


def get_regime() -> str:
    try:
        with open(STATE_FILE) as f:
            return json.load(f).get("regime", "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def load_positions() -> list:
    try:
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def check_positions() -> list[str]:
    """检查所有持仓，返回告警列表"""
    positions = load_positions()
    if not positions:
        return []

    regime = get_regime()
    alerts = []

    for pos in positions:
        if not pos.get("active", True):
            continue

        symbol  = pos.get("symbol", "?")
        side    = pos.get("side", "LONG")
        entry   = float(pos.get("entry_price", 0) or 0)
        sl      = float(pos.get("stop_loss", 0) or 0)
        qty     = float(pos.get("qty", 0) or 0)

        if entry == 0 or sl == 0:
            continue

        mark = get_mark_price(symbol)
        if mark == 0:
            continue

        upnl_pct = (mark - entry) / entry * 100 if side == "LONG" else (entry - mark) / entry * 100

        # ① 止损距离计算
        if side == "LONG":
            sl_dist_pct = (mark - sl) / mark * 100
        else:
            sl_dist_pct = (sl - mark) / mark * 100

        # ② 体制冲突检测
        regime_conflict = (
            (side == "LONG" and regime in ("BEAR_TREND",)) or
            (side == "SHORT" and regime in ("BULL_TREND",))
        )

        # ③ 生成告警
        if sl_dist_pct < SL_ALERT_PCT and sl_dist_pct > -5:
            alerts.append(
                f"🚨 {symbol} {side} 止损极近！"
                f"mark={mark:.4f} SL={sl:.4f} 距离={sl_dist_pct:.2f}% upnl={upnl_pct:+.2f}%"
            )
        elif sl_dist_pct < SL_WARN_PCT and sl_dist_pct > -5:
            alerts.append(
                f"⚠️ {symbol} {side} 接近止损 "
                f"mark={mark:.4f} SL={sl:.4f} 距离={sl_dist_pct:.2f}% upnl={upnl_pct:+.2f}%"
            )

        if regime_conflict:
            alerts.append(
                f"⚡ {symbol} {side} 体制冲突 "
                f"当前体制={regime} upnl={upnl_pct:+.2f}%"
            )

    return alerts


def main():
    alerts = check_positions()

    if not alerts:
        print("HEARTBEAT_OK")
        return

    print("【ws_guardian 持仓告警】")
    for a in alerts:
        print(a)


if __name__ == "__main__":
    main()
