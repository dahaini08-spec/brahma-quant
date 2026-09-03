#!/usr/bin/env python3
"""
oi_watchlist_monitor.py — OI监控标的触发条件检测
每2h由supercronic执行，检查watchlist里每个标的是否达到入场条件
若达到则推送到jarvis，未达到则输出距离触发条件的差距

2026-09-03 梵天设计院 苏摩111封印
"""
import json, time, sys, os, requests
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
WATCHLIST_FILE = BASE_DIR / "data" / "oi_watchlist.json"
JARVIS_USER_ID = "73295708"
JARVIS_THREAD_ID = "01a03e25-a459-733e-a2ba-a56083050f26"
FAPI = "https://fapi.binance.com"

def get_ticker(symbol):
    r = requests.get(f"{FAPI}/fapi/v1/ticker/24hr", params={"symbol": symbol}, timeout=8)
    return r.json()

def get_fr(symbol):
    r = requests.get(f"{FAPI}/fapi/v1/fundingRate", params={"symbol": symbol, "limit": 1}, timeout=8)
    data = r.json()
    return float(data[0]["fundingRate"]) * 100 if data else 0.0

def get_lsr(symbol):
    r = requests.get(f"{FAPI}/futures/data/globalLongShortAccountRatio",
                     params={"symbol": symbol, "period": "1h", "limit": 1}, timeout=8)
    data = r.json()
    return float(data[0]["longShortRatio"]) if data else 1.0

def get_oi_change(symbol):
    """获取1h OI变化率"""
    r = requests.get(f"{FAPI}/futures/data/openInterestHist",
                     params={"symbol": symbol, "period": "1h", "limit": 2}, timeout=8)
    data = r.json()
    if len(data) >= 2:
        oi_now  = float(data[-1]["sumOpenInterest"])
        oi_prev = float(data[-2]["sumOpenInterest"])
        return (oi_now - oi_prev) / max(oi_prev, 1) * 100
    return 0.0

def check_atr(symbol):
    """获取ATR1H近似值（用1H K线最近14根计算）"""
    r = requests.get(f"{FAPI}/fapi/v1/klines",
                     params={"symbol": symbol, "interval": "1h", "limit": 15}, timeout=8)
    klines = r.json()
    if len(klines) < 2:
        return 0.0
    trs = [max(float(k[2]), float(k[4])) - min(float(k[3]), float(k[4])) for k in klines[-14:]]
    return sum(trs) / len(trs)

def push_jarvis(msg: str):
    """通过openclaw推送到jarvis"""
    import subprocess
    subprocess.run([
        "openclaw", "message", "send",
        "--channel", "jarvis",
        "--to", f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}",
        "--message", msg
    ], capture_output=True, timeout=15)

def evaluate_trigger(symbol, entry, ticker, fr, lsr, oi_chg, atr):
    """
    评估每个标的的触发条件，返回:
    - triggered: bool
    - distance: 各条件距离触发的差距
    - summary: 一句话描述
    """
    conds = entry.get("trigger_conditions", {})
    direction = entry.get("direction", "LONG")
    price = float(ticker.get("lastPrice", 0))
    chg24h = float(ticker.get("priceChangePercent", 0))

    gaps = []
    score = 0  # 满足条件数
    total = 0

    # ── FR条件 ────────────────────────────────────────────
    if "fr" in conds:
        total += 1
        if symbol == "LAUSDT":
            # FR需回归 > -0.1%
            threshold = -0.1
            if fr > threshold:
                score += 1; gaps.append(f"FR={fr:+.4f}% ✅(>{threshold}%)")
            else:
                gap = threshold - fr
                gaps.append(f"FR={fr:+.4f}% 还差{gap:.4f}%回归")
        elif symbol == "ACEUSDT":
            threshold = -0.2
            if fr > threshold:
                score += 1; gaps.append(f"FR={fr:+.4f}% ✅(>{threshold}%)")
            else:
                gaps.append(f"FR={fr:+.4f}% 还差{abs(threshold-fr):.4f}%回升")
        elif symbol == "XAUUSDT":
            if fr >= 0:
                score += 1; gaps.append(f"FR={fr:+.4f}% ✅(已转正)")
            else:
                gaps.append(f"FR={fr:+.4f}% 未转正(差{abs(fr):.4f}%)")
        else:
            # SOXLUSDT: FR>0
            if fr > 0:
                score += 1; gaps.append(f"FR={fr:+.4f}% ✅(>0)")
            else:
                gaps.append(f"FR={fr:+.4f}% 未满足(需>0)")

    # ── LSR条件（仅LAUSDT）────────────────────────────────
    if "lsr" in conds and symbol == "LAUSDT":
        total += 1
        if lsr < 1.3:
            score += 1; gaps.append(f"LSR={lsr:.2f} ✅(<1.3)")
        else:
            gaps.append(f"LSR={lsr:.2f} 多头仍拥挤(需<1.3，差{lsr-1.3:.2f})")

    # ── 价格动量条件 ──────────────────────────────────────
    total += 1
    if direction == "SHORT":
        if chg24h < -1.0:
            score += 1; gaps.append(f"价格{chg24h:+.1f}% ✅(开始下跌)")
        else:
            gaps.append(f"价格{chg24h:+.1f}% 等待下跌破位")
    else:  # LONG
        if chg24h > -15 and chg24h < 3:  # 止跌迹象
            score += 1; gaps.append(f"价格{chg24h:+.1f}% ✅(跌幅收敛)")
        else:
            gaps.append(f"价格{chg24h:+.1f}% 等待企稳")

    # ── OI条件 ────────────────────────────────────────────
    total += 1
    if direction == "SHORT" and oi_chg > 5:
        score += 1; gaps.append(f"OI1h={oi_chg:+.1f}% ✅(空头仍在建仓)")
    elif direction == "LONG" and oi_chg < 3:
        score += 1; gaps.append(f"OI1h={oi_chg:+.1f}% ✅(增速放缓)")
    else:
        gaps.append(f"OI1h={oi_chg:+.1f}% 未满足")

    triggered = score >= total - 1  # 允许1个条件未满足（结构需人工确认）
    pct = score / total * 100

    summary = f"{symbol} 触发度{pct:.0f}%({score}/{total}条件) | " + " | ".join(gaps)
    return triggered, pct, summary


def main():
    if not WATCHLIST_FILE.exists():
        print("HEARTBEAT_OK")
        return

    watchlist = json.loads(WATCHLIST_FILE.read_text())
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    results = []
    triggered_alerts = []

    for symbol, entry in watchlist.items():
        if entry.get("status") != "WATCHING":
            continue
        try:
            ticker  = get_ticker(symbol)
            fr      = get_fr(symbol)
            lsr     = get_lsr(symbol)
            oi_chg  = get_oi_change(symbol)
            atr     = check_atr(symbol)

            triggered, pct, summary = evaluate_trigger(symbol, entry, ticker, fr, lsr, oi_chg, atr)

            price = float(ticker.get("lastPrice", 0))
            sl_dist = atr * 1.5
            results.append({
                "symbol": symbol,
                "price": price,
                "fr": fr,
                "lsr": lsr,
                "oi_chg": oi_chg,
                "atr": atr,
                "sl_dist": sl_dist,
                "triggered": triggered,
                "pct": pct,
                "summary": summary,
                "direction": entry.get("direction", "LONG"),
                "size_pct": entry.get("size_pct", 1.0),
            })

            if triggered:
                triggered_alerts.append((symbol, entry, price, fr, sl_dist, pct))

            # 更新 last_check
            entry["last_check"] = now_str
            entry["last_fr"] = round(fr, 6)
            entry["last_price"] = price
            entry["last_trigger_pct"] = round(pct, 1)

        except Exception as e:
            results.append({"symbol": symbol, "error": str(e)[:80]})

    # 写回 watchlist（更新 last_check）
    WATCHLIST_FILE.write_text(json.dumps(watchlist, indent=2, ensure_ascii=False))

    # ── 构建输出 ─────────────────────────────────────────
    if triggered_alerts:
        # 有触发信号，推送到jarvis
        lines = [f"🚨 OI监控触发 | {now_str}", ""]
        for symbol, entry, price, fr, sl_dist, pct in triggered_alerts:
            dir_icon = "🟢" if entry.get("direction") == "LONG" else "🔴"
            lines.append(f"{dir_icon} {symbol} | ${price:.4f} | 触发度{pct:.0f}%")
            lines.append(f"   方向: {entry.get('direction')} | 仓位: {entry.get('size_pct',1.0)}%NAV")
            lines.append(f"   FR={fr:+.4f}% | SL距离≈${sl_dist:.4f}(1.5×ATR1H)")
            lines.append(f"   ⚠️ 仍需人工确认15M结构(CHoCH/Hammer)")
            lines.append("")
        msg = "\n".join(lines)
        push_jarvis(msg)
        print(msg)
    else:
        # 无触发，输出距离报告（写到日志，不推送）
        lines = [f"📊 OI监控巡检 | {now_str} | 无触发", ""]
        for r in results:
            if "error" in r:
                lines.append(f"  ⚠️ {r['symbol']}: {r['error']}")
            else:
                flag = "🟡" if r["pct"] >= 50 else "⚪"
                lines.append(f"  {flag} {r['summary']}")
        report = "\n".join(lines)
        print(report)  # 写到syscron.log，不推送jarvis


if __name__ == "__main__":
    main()
