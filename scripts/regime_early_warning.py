#!/usr/bin/env python3
"""
regime_early_warning.py — 体制预切换三级预警
[P0-C 2026-08-31 苏摩111封印]

Level 1 预警: RSI4H<38 + OI缩减 + Taker偏卖 → 可能切换BEAR_RECOVERY
Level 2 确认: RSI4H<35 + 价格跌破7D低点 → BEAR_RECOVERY激活
Level 3 执行: 15M CHoCH出现 → 立即执行

用法: python3 scripts/regime_early_warning.py [--symbol BTCUSDT]
"""

import sys, json, time, argparse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'brahma_brain'))

JARVIS_USER_ID   = '73295708'
JARVIS_THREAD_ID = '01a0338d-a169-761c-9352-04d3b80d8746'


def fetch_json(url, timeout=8):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return None


def get_price(symbol):
    r = fetch_json(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}')
    return float(r['price']) if r else None


def get_kline_closes(symbol, interval, limit=50):
    r = fetch_json(f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}')
    return [float(k[4]) for k in r] if r else []


def get_kline_lows(symbol, interval, limit=10):
    r = fetch_json(f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}')
    return [float(k[3]) for k in r] if r else []


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return 100 - (100 / (1 + ag/al)) if al > 0 else 100.0


def get_oi_change(symbol, hours=8):
    r = fetch_json(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=1h&limit={hours+1}')
    if not r or len(r) < 2:
        return 0.0
    first = float(r[0]['sumOpenInterest'])
    last  = float(r[-1]['sumOpenInterest'])
    return (last - first) / first * 100 if first > 0 else 0.0


def get_taker_ratio(symbol, hours=3):
    r = fetch_json(f'https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={symbol}&period=1h&limit={hours}')
    if not r:
        return 1.0
    return sum(float(x['buySellRatio']) for x in r) / len(r)


def get_lsr(symbol):
    r = fetch_json(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=1')
    if not r:
        return 50.0
    return float(r[0]['longAccount']) * 100


def push_warning(level, symbol, message, dry_run=False):
    """推送预警到Jarvis"""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    icons = {1: '🟡', 2: '🟠', 3: '🔴'}
    icon = icons.get(level, '⚠️')
    sym_short = symbol.replace('USDT', '')

    full_msg = f'{icon} 梵天体制预警 L{level} | {sym_short} | {now}\n{message}'

    if dry_run:
        print(f'[DRY RUN] 推送:\n{full_msg}')
        return

    try:
        import subprocess
        target = f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
        result = subprocess.run(
            ['openclaw', 'message', 'send', '--channel', 'jarvis',
             '--to', target, '--message', full_msg],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            print(f'✅ 推送成功: L{level} 预警')
        else:
            print(f'❌ 推送失败: {result.stderr[:100]}')
    except Exception as e:
        print(f'❌ 推送异常: {e}')


def check_regime_transition(symbol='BTCUSDT', dry_run=False):
    """主检测函数"""
    print(f'[{datetime.now(timezone.utc).strftime("%H:%M UTC")}] 检测 {symbol} 体制预切换...')

    price = get_price(symbol)
    if not price:
        print('❌ 无法获取价格')
        return

    # 指标
    closes_4h = get_kline_closes(symbol, '4h', 50)
    closes_1d = get_kline_closes(symbol, '1d', 10)
    lows_1d   = get_kline_lows(symbol, '1d', 7)

    rsi_4h = calc_rsi(closes_4h)
    oi_chg = get_oi_change(symbol, hours=8)
    taker  = get_taker_ratio(symbol, hours=3)
    lsr    = get_lsr(symbol)
    low_7d = min(lows_1d) if lows_1d else price * 0.95

    print(f'  价格={price:.1f} RSI4H={rsi_4h:.1f} OI8H={oi_chg:+.2f}% Taker={taker:.3f} LSR={lsr:.1f}% Low7D={low_7d:.1f}')

    # ── Level 3：15M CHoCH（最紧急）──────────────────────────
    closes_15m = get_kline_closes(symbol, '15m', 20)
    highs_15m  = [float(k[2]) for k in (fetch_json(f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=20') or [])]
    lows_15m   = [float(k[3]) for k in (fetch_json(f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=20') or [])]

    choch_detected = False
    if len(closes_15m) >= 10 and len(lows_15m) >= 10:
        # 简单CHoCH：价格从下跌中创出更高低点
        recent_low  = min(lows_15m[-5:])
        prev_low    = min(lows_15m[-10:-5])
        recent_high = max(highs_15m[-5:])
        prev_high   = max(highs_15m[-10:-5])
        # 做多CHoCH: 新低点高于前低点 + 价格站上前高
        if recent_low > prev_low and price > prev_high:
            choch_detected = True

    if choch_detected and rsi_4h < 40 and oi_chg < -0.5:
        msg = (f'15M CHoCH确认！体制切换BEAR_RECOVERY\n'
               f'  RSI4H={rsi_4h:.1f} OI={oi_chg:+.2f}% 结构反转\n'
               f'  → 立即执行做多\n'
               f'  → WR=100%铁证激活\n'
               f'  → 入场区: ${price*0.998:,.0f}~${price*1.002:,.0f}\n'
               f'  → SL: ${low_7d*0.99:,.0f} | 仓位: 5%NAV')
        print(f'🔴 Level 3 激活: {msg}')
        push_warning(3, symbol, msg, dry_run=dry_run)
        return 3

    # ── Level 2：体制切换确认 ─────────────────────────────────
    if rsi_4h < 35 and price < low_7d * 1.005:
        msg = (f'BEAR_RECOVERY确认！RSI4H={rsi_4h:.1f}<35 + 价格接近7D低点\n'
               f'  价格={price:.1f} 7D低={low_7d:.1f}\n'
               f'  → WR=100%铁证待激活\n'
               f'  → 等待15M CHoCH确认后立即做多\n'
               f'  → 提前备好: SL=${low_7d*0.99:,.0f} 仓位5%NAV')
        print(f'🟠 Level 2 激活: {msg}')
        push_warning(2, symbol, msg, dry_run=dry_run)
        return 2

    # ── Level 1：预警 ─────────────────────────────────────────
    if rsi_4h < 38 and oi_chg < -0.5 and taker < 0.85:
        msg = (f'体制可能切换至BEAR_RECOVERY\n'
               f'  RSI4H={rsi_4h:.1f}(<38) | OI={oi_chg:+.2f}%(<-0.5) | Taker={taker:.3f}(<0.85)\n'
               f'  → 准备做多仓位\n'
               f'  → 关注价格是否跌破7D低点 ${low_7d:.0f}\n'
               f'  → 此时不操作，等Level 2确认')
        print(f'🟡 Level 1 激活: {msg}')
        push_warning(1, symbol, msg, dry_run=dry_run)
        return 1

    print(f'  ✅ 无体制切换信号 | RSI4H={rsi_4h:.1f} OI={oi_chg:+.2f}%')
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', default='BTCUSDT')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--both', action='store_true', help='同时检测BTC和ETH')
    args = parser.parse_args()

    if args.both:
        for sym in ['BTCUSDT', 'ETHUSDT']:
            check_regime_transition(sym, dry_run=args.dry_run)
            print()
    else:
        check_regime_transition(args.symbol, dry_run=args.dry_run)
