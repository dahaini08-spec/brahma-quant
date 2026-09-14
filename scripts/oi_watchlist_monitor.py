#!/usr/bin/env python3
"""
oi_watchlist_monitor.py — OI监控标的触发条件检测 v2
每2h由supercronic执行

[2026-09-14 苏摩111 四项改革封印]
1. 收紧触发标准: score>=total（不允许缺口）
2. 接入SMC结构检测: 调smc_engine自动确认15M CHoCH/Hammer
3. 加WR回测: 对watchlist标的做OI信号历史WR验证
4. 降级为日志: 未满足SMC结构时只写日志不推Jarvis

核心原则: MEMORY.md铁律"没有SMC结构锚点的OI信号不入场"
"""
import json, time, sys, os, requests
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
WATCHLIST_FILE = BASE_DIR / "data" / "oi_watchlist.json"
WR_HISTORY_FILE = BASE_DIR / "data" / "oi_watchlist_wr.json"
JARVIS_USER_ID = "73295708"
JARVIS_THREAD_ID = "01a07628-0405-7e85-a34b-e68cd029dfc6"
FAPI = "https://fapi.binance.com"
DEDUP_FILE = BASE_DIR / "data" / "oi_watchlist_dedup.json"

# ── SMC结构检测 ─────────────────────────────────────────
def check_smc_structure(symbol: str, direction: str) -> dict:
    """
    调用smc_engine检测15M结构
    返回: {confirmed: bool, type: 'BULL_CHOCH'|'HAMMER'|'NONE', detail: str}
    """
    try:
        _root = str(BASE_DIR)
        _brain = str(BASE_DIR / 'brahma_brain')
        for _p in [_root, _brain]:
            if _p not in sys.path:
                sys.path.insert(0, _p)
        from smc_engine import detect_bos_choch, get_klines
        
        # 获取15M K线
        klines = get_klines(symbol, '15m', 200)
        if not klines or len(klines) < 20:
            return {'confirmed': False, 'type': 'NONE', 'detail': 'K线数据不足'}
        
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]
        
        # 检测CHoCH
        structure = detect_bos_choch(highs, lows, closes)
        choch_list = structure.get('choch_list', [])
        
        # 检测Hammer（最近3根K线）
        hammer_found = False
        for k in klines[-3:]:
            o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
            body = abs(c - o)
            lower_wick = min(o, c) - l
            upper_wick = h - max(o, c)
            # Hammer: 下影线>2×实体，上影线<0.5×实体
            is_hammer = lower_wick > 2 * body and upper_wick < 0.5 * body if body > 0 else False
            if is_hammer and direction == 'LONG':
                hammer_found = True
            # Shooting Star（做空用）
            if upper_wick > 2 * body and lower_wick < 0.5 * body if body > 0 else False:
                if direction == 'SHORT':
                    hammer_found = True
        
        # 判断
        if direction == 'LONG':
            bull_choch = any(c['type'] == 'BULL_CHOCH' for c in choch_list)
            if bull_choch:
                return {'confirmed': True, 'type': 'BULL_CHOCH', 
                        'detail': '15M Bull CHoCH已确认'}
            if hammer_found:
                return {'confirmed': True, 'type': 'HAMMER',
                        'detail': '15M Hammer反转K线已确认'}
        else:
            bear_choch = any(c['type'] == 'BEAR_CHOCH' for c in choch_list)
            if bear_choch:
                return {'confirmed': True, 'type': 'BEAR_CHOCH',
                        'detail': '15M Bear CHoCH已确认'}
            if hammer_found:
                return {'confirmed': True, 'type': 'SHOOTING_STAR',
                        'detail': '15M Shooting Star已确认'}
        
        # 结构未确认
        market_struct = structure.get('market_structure', 'UNKNOWN')
        return {'confirmed': False, 'type': 'NONE',
                'detail': f'15M结构未确认(当前:{market_struct})，等待CHoCH/Hammer'}
    
    except Exception as e:
        return {'confirmed': False, 'type': 'ERROR', 'detail': f'SMC检测失败: {str(e)[:60]}'}


# ── WR历史记录与验证 ─────────────────────────────────────
def load_wr_history() -> dict:
    """加载OI watchlist信号WR历史"""
    if WR_HISTORY_FILE.exists():
        try:
            return json.loads(WR_HISTORY_FILE.read_text())
        except:
            pass
    return {'signals': [], 'stats': {}}

def save_wr_history(data: dict):
    WR_HISTORY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def record_signal(symbol: str, direction: str, price: float, triggered_at: str):
    """记录触发的信号，供后续WR验证"""
    hist = load_wr_history()
    hist['signals'].append({
        'symbol': symbol,
        'direction': direction,
        'price': price,
        'triggered_at': triggered_at,
        'settled': False,
        'outcome': None,
        'pnl_pct': None,
    })
    # 保存
    save_wr_history(hist)

def get_wr_stats(symbol: str) -> dict:
    """获取某标的的历史WR统计"""
    hist = load_wr_history()
    signals = [s for s in hist['signals'] if s['symbol'] == symbol and s.get('settled')]
    if not signals:
        return {'n': 0, 'wr': 0, 'note': '无历史数据'}
    wins = sum(1 for s in signals if (s.get('pnl_pct') or 0) > 0)
    n = len(signals)
    return {'n': n, 'wr': round(wins / n * 100, 1), 'note': f'历史{n}笔 WR={wins}/{n}'}


# ── API数据获取 ──────────────────────────────────────────
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
    r = requests.get(f"{FAPI}/futures/data/openInterestHist",
                     params={"symbol": symbol, "period": "1h", "limit": 2}, timeout=8)
    data = r.json()
    if len(data) >= 2:
        oi_now  = float(data[-1]["sumOpenInterest"])
        oi_prev = float(data[-2]["sumOpenInterest"])
        return (oi_now - oi_prev) / max(oi_prev, 1) * 100
    return 0.0

def check_atr(symbol):
    r = requests.get(f"{FAPI}/fapi/v1/klines",
                     params={"symbol": symbol, "interval": "1h", "limit": 15}, timeout=8)
    klines = r.json()
    if len(klines) < 2:
        return 0.0
    trs = [max(float(k[2]), float(k[4])) - min(float(k[3]), float(k[4])) for k in klines[-14:]]
    return sum(trs) / len(trs)


# ── 推送 ─────────────────────────────────────────────────
def push_jarvis(msg: str):
    try:
        import hashlib as _hl
        dedup_key = _hl.md5(msg[:100].encode()).hexdigest()[:16]
        if DEDUP_FILE.exists():
            dedup = json.loads(DEDUP_FILE.read_text())
        else:
            dedup = {}
        now = time.time()
        dedup = {k: v for k, v in dedup.items() if now - v < 86400}
        if dedup_key in dedup:
            print("[push_jarvis] 24h内已推送过，跳过")
            return
        dedup[dedup_key] = now
        DEDUP_FILE.write_text(json.dumps(dedup, ensure_ascii=False, indent=2))
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    try:
        import subprocess
        subprocess.Popen(
            ['openclaw', 'message', 'send',
             '--to', f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}',
             '--channel', 'jarvis',
             '--message', msg],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"[push_jarvis] 推送失败: {e}", file=sys.stderr)


# ── 评估触发条件 ─────────────────────────────────────────
def evaluate_trigger(symbol, entry, ticker, fr, lsr, oi_chg, atr, smc_result):
    """
    v2: 收紧触发标准 + SMC结构作为硬条件
    
    变更:
    - score >= total（不允许缺口，旧版允许1个）
    - SMC结构作为第5个硬条件（旧版⚠️人工确认）
    - 全部满足才推送Jarvis，否则只写日志
    """
    conds = entry.get("trigger_conditions", {})
    direction = entry.get("direction", "LONG")
    price = float(ticker.get("lastPrice", 0))
    chg24h = float(ticker.get("priceChangePercent", 0))

    gaps = []
    score = 0
    total = 0

    # ── FR条件 ────────────────────────────────────────────
    if "fr" in conds:
        total += 1
        if symbol == "LAUSDT":
            threshold = -0.1
            if fr > threshold:
                score += 1; gaps.append(f"FR={fr:+.4f}% ✅(>{threshold}%)")
            else:
                gaps.append(f"FR={fr:+.4f}% ❌(需>{threshold}%)")
        elif symbol == "ACEUSDT":
            threshold = -0.2
            if fr > threshold:
                score += 1; gaps.append(f"FR={fr:+.4f}% ✅(>{threshold}%)")
            else:
                gaps.append(f"FR={fr:+.4f}% ❌(需>{threshold}%)")
        elif symbol == "XAUUSDT":
            if fr >= 0:
                score += 1; gaps.append(f"FR={fr:+.4f}% ✅(已转正)")
            else:
                gaps.append(f"FR={fr:+.4f}% ❌(未转正)")
        else:
            if fr > 0:
                score += 1; gaps.append(f"FR={fr:+.4f}% ✅(>0)")
            else:
                gaps.append(f"FR={fr:+.4f}% ❌(需>0)")

    # ── LSR条件 ─────────────────────────────────────────
    if "lsr" in conds and symbol == "LAUSDT":
        total += 1
        if lsr < 1.3:
            score += 1; gaps.append(f"LSR={lsr:.2f} ✅(<1.3)")
        else:
            gaps.append(f"LSR={lsr:.2f} ❌(需<1.3)")

    # ── 价格动量条件 ──────────────────────────────────────
    total += 1
    if direction == "SHORT":
        if chg24h < -1.0:
            score += 1; gaps.append(f"价格{chg24h:+.1f}% ✅(开始下跌)")
        else:
            gaps.append(f"价格{chg24h:+.1f}% ❌(等下跌破位)")
    else:
        if -15 < chg24h < 3:
            score += 1; gaps.append(f"价格{chg24h:+.1f}% ✅(跌幅收敛)")
        else:
            gaps.append(f"价格{chg24h:+.1f}% ❌(等企稳)")

    # ── OI条件 ────────────────────────────────────────────
    total += 1
    if direction == "SHORT" and oi_chg > 5:
        score += 1; gaps.append(f"OI1h={oi_chg:+.1f}% ✅(空头建仓)")
    elif direction == "LONG" and oi_chg < 3:
        score += 1; gaps.append(f"OI1h={oi_chg:+.1f}% ✅(增速放缓)")
    else:
        gaps.append(f"OI1h={oi_chg:+.1f}% ❌(未满足)")

    # ── SMC结构条件（v2新增，硬条件）──────────────────────
    total += 1
    if smc_result['confirmed']:
        score += 1; gaps.append(f"SMC {smc_result['type']} ✅({smc_result['detail']})")
    else:
        gaps.append(f"SMC ❌({smc_result['detail']})")

    # v2: 严格触发，全部满足才推送
    triggered = score >= total
    pct = score / total * 100

    summary = f"{symbol} 触发度{pct:.0f}%({score}/{total}) | " + " | ".join(gaps)
    return triggered, pct, summary


# ── 主函数 ───────────────────────────────────────────────
def main():
    if not WATCHLIST_FILE.exists():
        print("HEARTBEAT_OK")
        return

    watchlist = json.loads(WATCHLIST_FILE.read_text())
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    results = []
    push_alerts = []      # 全部条件满足 → 推送Jarvis
    log_alerts = []        # OI条件满足但SMC未确认 → 仅日志

    for symbol, entry in watchlist.items():
        if entry.get("status") != "WATCHING":
            continue
        try:
            ticker  = get_ticker(symbol)
            fr      = get_fr(symbol)
            lsr     = get_lsr(symbol)
            oi_chg  = get_oi_change(symbol)
            atr     = check_atr(symbol)
            direction = entry.get("direction", "LONG")
            
            # v2: SMC结构检测
            smc_result = check_smc_structure(symbol, direction)

            triggered, pct, summary = evaluate_trigger(
                symbol, entry, ticker, fr, lsr, oi_chg, atr, smc_result
            )

            price = float(ticker.get("lastPrice", 0))
            sl_dist = atr * 1.5
            
            # v2: WR统计
            wr_stats = get_wr_stats(symbol)

            result = {
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
                "direction": direction,
                "size_pct": entry.get("size_pct", 1.0),
                "smc": smc_result,
                "wr_stats": wr_stats,
            }
            results.append(result)

            if triggered:
                push_alerts.append(result)
                # 记录信号供WR验证
                record_signal(symbol, direction, price, now_str)
            elif pct >= 60:  # OI条件大部分满足但SMC未确认
                log_alerts.append(result)

            entry["last_check"] = now_str
            entry["last_fr"] = round(fr, 6)
            entry["last_price"] = price
            entry["last_trigger_pct"] = round(pct, 1)
            entry["last_smc"] = smc_result['type']

        except Exception as e:
            results.append({"symbol": symbol, "error": str(e)[:80]})

    WATCHLIST_FILE.write_text(json.dumps(watchlist, indent=2, ensure_ascii=False))

    # ── 构建输出 ─────────────────────────────────────────
    if push_alerts:
        # 全部条件满足（含SMC）→ 推送Jarvis
        lines = [f"🚨 OI监控触发(已确认SMC结构) | {now_str}", ""]
        for r in push_alerts:
            dir_icon = "🟢" if r["direction"] == "LONG" else "🔴"
            lines.append(f"{dir_icon} {r['symbol']} | ${r['price']:.4f} | 触发度{r['pct']:.0f}%")
            lines.append(f"   方向: {r['direction']} | 仓位: {r['size_pct']}%NAV")
            lines.append(f"   FR={r['fr']:+.4f}% | SL距离≈${r['sl_dist']:.4f}(1.5×ATR1H)")
            lines.append(f"   SMC: {r['smc']['type']} ✅ {r['smc']['detail']}")
            wr = r['wr_stats']
            lines.append(f"   WR历史: {wr['note']}")
            lines.append("")
        msg = "\n".join(lines)
        push_jarvis(msg)
        print(msg)
    
    if log_alerts:
        # OI条件满足但SMC未确认 → 仅日志，不推送
        lines = [f"📊 OI监控日志(未确认SMC结构) | {now_str}", ""]
        for r in log_alerts:
            dir_icon = "🟡" if r["direction"] == "LONG" else "🟠"
            lines.append(f"{dir_icon} {r['symbol']} | 触发度{r['pct']:.0f}% | SMC: {r['smc']['detail']}")
            lines.append(f"   {r['summary']}")
            lines.append(f"   → 待SMC结构确认后自动升级为推送")
            lines.append("")
        report = "\n".join(lines)
        print(report)  # 只写日志，不推送
    
    if not push_alerts and not log_alerts:
        lines = [f"📊 OI监控巡检 | {now_str} | 无触发", ""]
        for r in results:
            if "error" in r:
                lines.append(f"  ⚠️ {r['symbol']}: {r['error']}")
            else:
                flag = "🟡" if r["pct"] >= 50 else "⚪"
                lines.append(f"  {flag} {r['summary']}")
        print("\n".join(lines))


if __name__ == "__main__":
    main()
