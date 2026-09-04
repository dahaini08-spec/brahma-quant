#!/usr/bin/env python3
"""
breakout_watch.py — CHOP体制盲区突破旁路预警
设计院封印 2026-09-04 苏摩111

根因：梵天CHOP_MID体制下两次错过大拉升（64K→81.5K / 77K→82.3K）
修复：独立旁路通道，不依赖体制判断，专门捕捉CHOP下的爆发前兆

三条铁律（三项同时满足 = BREAKOUT_WATCH激活）：
  条件1【量能突变】：1H成交量 ≥ 前4H均量 × VOLUME_MULT + 价格突破前高
  条件2【轧空积压】：全账户空头 > LSR_SHORT_THRESHOLD + 大户多头 > SMART_LONG_THRESHOLD
  条件3【弹簧蓄力】：4H价格区间 < CHOP_RANGE_PCT 连续 ≥ CHOP_BARS根 + OI不跌

接入位置：
  1. scripts/brahma_manual_analysis.py Step0（并行拉取时顺带检测）
  2. OpenClaw cron every 30m（主动巡检，触发时推送预警）
"""
import sys, json, time, urllib.request, signal
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# ── 参数（封印值，修改需苏摩111）──────────────────────────────
VOLUME_MULT          = 3.0    # 量能突变倍数阈值
LSR_SHORT_THRESHOLD  = 45.0   # 全账户空头%阈值（散户偏空=轧空燃料）
SMART_LONG_THRESHOLD = 58.0   # 大户多头%阈值（聪明钱站多）
CHOP_RANGE_PCT       = 1.5    # 4H横盘区间宽度（%）
CHOP_BARS            = 6      # 横盘最少根数
OI_DROP_TOLERANCE    = 0.005  # OI可接受下跌幅度（0.5%内视为不跌）
ENTRY_PULLBACK_PCT   = 0.005  # 入场回踩幅度（0.5%）
SL_BUFFER_PCT        = 0.011  # SL=放量K线低点下1.1%
# ─────────────────────────────────────────────────────────────

MAX_RUNTIME_S = 30
def _timeout(s, f): sys.exit(1)
signal.signal(signal.SIGALRM, _timeout)
signal.alarm(MAX_RUNTIME_S)


def _fetch(url: str) -> any:
    try:
        return json.loads(urllib.request.urlopen(url, timeout=8).read())
    except Exception as e:
        print(f'[bw] fetch失败: {e}', flush=True)
        return None


def check_volume_breakout(symbol: str) -> dict:
    """条件1：量能突变 + 价格突破前高"""
    klines = _fetch(f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1h&limit=10')
    if not klines or len(klines) < 6:
        return {'ok': False, 'reason': 'klines获取失败'}

    volumes  = [float(k[5]) for k in klines]
    closes   = [float(k[4]) for k in klines]
    highs    = [float(k[2]) for k in klines]

    cur_vol  = volumes[-2]          # 最近完整K线
    avg_vol  = sum(volumes[-6:-2]) / 4  # 前4根均量
    cur_c    = closes[-2]
    prev_high = max(highs[-7:-2])   # 前5根最高价

    ratio = cur_vol / avg_vol if avg_vol > 0 else 0
    breakout = cur_c > prev_high

    ok = ratio >= VOLUME_MULT and breakout
    return {
        'ok':        ok,
        'vol_ratio': round(ratio, 2),
        'cur_vol':   round(cur_vol, 0),
        'avg_vol':   round(avg_vol, 0),
        'breakout':  breakout,
        'cur_price': round(cur_c, 2),
        'prev_high': round(prev_high, 2),
        'reason':    f'量能{ratio:.1f}x {"突破前高" if breakout else "未突破"} cur={cur_c:.0f} prev_high={prev_high:.0f}',
        'entry_ref': round(cur_c * (1 - ENTRY_PULLBACK_PCT), 2),
        'sl_ref':    round(float(klines[-2][3]) * (1 - SL_BUFFER_PCT), 2),  # 最近K线低点-1.1%
    }


def check_lsr_trap(symbol: str) -> dict:
    """条件2：散户偏空（轧空燃料）+ 大户偏多（聪明钱确认）"""
    # 全账户多空比
    global_lsr = _fetch(
        f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio'
        f'?symbol={symbol}&period=5m&limit=3'
    )
    # 大户持仓比
    top_lsr = _fetch(
        f'https://fapi.binance.com/futures/data/topLongShortPositionRatio'
        f'?symbol={symbol}&period=5m&limit=3'
    )

    if not global_lsr or not top_lsr:
        return {'ok': False, 'reason': 'LSR获取失败'}

    retail_short = float(global_lsr[-1]['shortAccount']) * 100
    smart_long   = float(top_lsr[-1]['longAccount']) * 100

    # 趋势：散户空头是否在持续积累（过去3根）
    retail_shorts_trend = [float(d['shortAccount'])*100 for d in global_lsr]
    short_building = retail_shorts_trend[-1] >= retail_shorts_trend[0]  # 空头在堆积

    ok = retail_short > LSR_SHORT_THRESHOLD and smart_long > SMART_LONG_THRESHOLD
    return {
        'ok':           ok,
        'retail_short': round(retail_short, 1),
        'smart_long':   round(smart_long, 1),
        'short_building': short_building,
        'reason': (
            f'散户空头{retail_short:.1f}%(阈值>{LSR_SHORT_THRESHOLD}%) '
            f'大户多头{smart_long:.1f}%(阈值>{SMART_LONG_THRESHOLD}%) '
            f'{"空头堆积中🔴" if short_building else "空头平稳"}'
        ),
    }


def check_spring_coil(symbol: str) -> dict:
    """条件3：4H横盘≥6根 + OI不跌（弹簧蓄力）"""
    klines_4h = _fetch(
        f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=4h&limit=10'
    )
    oi_hist = _fetch(
        f'https://fapi.binance.com/futures/data/openInterestHist'
        f'?symbol={symbol}&period=1h&limit=12'
    )

    if not klines_4h or not oi_hist:
        return {'ok': False, 'reason': '4H数据获取失败'}

    # 检查最近CHOP_BARS根4H是否横盘
    recent = klines_4h[-CHOP_BARS-1:-1]
    highs  = [float(k[2]) for k in recent]
    lows   = [float(k[3]) for k in recent]
    rng_hi, rng_lo = max(highs), min(lows)
    range_pct = (rng_hi - rng_lo) / rng_lo * 100 if rng_lo > 0 else 99

    is_chop = range_pct < CHOP_RANGE_PCT

    # OI：最近6H是否不跌（OI变化 > -0.5%）
    oi_vals  = [float(d['sumOpenInterest']) for d in oi_hist[-7:]]
    oi_start = oi_vals[0]
    oi_end   = oi_vals[-1]
    oi_chg   = (oi_end - oi_start) / oi_start if oi_start > 0 else 0
    oi_stable = oi_chg > -OI_DROP_TOLERANCE

    ok = is_chop and oi_stable
    return {
        'ok':         ok,
        'range_pct':  round(range_pct, 2),
        'range_hi':   round(rng_hi, 2),
        'range_lo':   round(rng_lo, 2),
        'oi_chg_pct': round(oi_chg * 100, 3),
        'oi_stable':  oi_stable,
        'is_chop':    is_chop,
        'reason': (
            f'4H区间{range_pct:.2f}%(阈值<{CHOP_RANGE_PCT}%) '
            f'{"横盘✅" if is_chop else "非横盘❌"} '
            f'OI{oi_chg*100:+.2f}% {"稳定✅" if oi_stable else "下跌❌"}'
        ),
    }


def run_breakout_watch(symbols: list = None) -> dict:
    """主入口：对多个标的执行三条件检测"""
    if symbols is None:
        symbols = ['BTCUSDT', 'ETHUSDT']

    results = {}
    alerts  = []

    for sym in symbols:
        label = sym.replace('USDT', '')
        c1 = check_volume_breakout(sym)
        c2 = check_lsr_trap(sym)
        c3 = check_spring_coil(sym)

        # 三条件得分
        score    = sum([c1['ok'], c2['ok'], c3['ok']])
        triggered = score >= 3  # 三项全中才触发

        # 两项满足也发WATCH_PARTIAL预警
        partial   = score == 2

        level = 'BREAKOUT_WATCH' if triggered else ('WATCH_PARTIAL' if partial else 'NORMAL')

        results[sym] = {
            'symbol':    sym,
            'level':     level,
            'score':     score,
            'triggered': triggered,
            'c1_volume': c1,
            'c2_lsr':    c2,
            'c3_spring': c3,
        }

        if triggered or partial:
            alerts.append({
                'symbol': sym,
                'level':  level,
                'score':  score,
                'entry_ref': c1.get('entry_ref'),
                'sl_ref':    c1.get('sl_ref'),
                'c1': c1['reason'],
                'c2': c2['reason'],
                'c3': c3['reason'],
            })

    return {'ts': time.time(), 'results': results, 'alerts': alerts}


def format_alert_card(alert: dict) -> str:
    """格式化预警推送卡片"""
    sym   = alert['symbol'].replace('USDT', '')
    level = alert['level']
    score = alert['score']
    emoji = '🚨' if level == 'BREAKOUT_WATCH' else '⚠️'

    lines = [
        f"{emoji} {level} | {sym} | {score}/3条件满足",
        f"",
        f"条件1【量能突变】{'✅' if '突破' in alert['c1'] and '未突破' not in alert['c1'] else '❌'}  {alert['c1']}",
        f"条件2【轧空积压】{'✅' if '>' in alert['c2'] else '❌'}  {alert['c2']}",
        f"条件3【弹簧蓄力】{'✅' if '横盘✅' in alert['c3'] and '稳定✅' in alert['c3'] else '❌'}  {alert['c3']}",
    ]

    if level == 'BREAKOUT_WATCH' and alert.get('entry_ref'):
        lines += [
            f"",
            f"🟢 参考入场: ${alert['entry_ref']:,.2f}（回踩0.5%）",
            f"🚫 参考SL:   ${alert['sl_ref']:,.2f}（放量K低点-1.1%）",
            f"⚠️  CHOP体制旁路信号，仓位≤1.5%NAV，人工确认后执行",
        ]

    lines.append(f"\n📊 梵天CHOP盲区探测器 · breakout_watch · {datetime.now(timezone.utc).strftime('%m/%d %H:%M')} UTC")
    return '\n'.join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', default=['BTCUSDT', 'ETHUSDT'])
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()

    print(f'[bw] CHOP盲区探测 {" ".join(args.symbols)} @ {datetime.now(timezone.utc).strftime("%H:%M UTC")}')
    result = run_breakout_watch(args.symbols)

    # 写缓存
    cache = BASE / 'data' / 'breakout_watch_latest.json'
    cache.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    alerts = result['alerts']
    if not alerts:
        print('[bw] 无触发，三条件均未同时满足 → HEARTBEAT_OK')
        if not args.quiet:
            for sym, r in result['results'].items():
                print(f"  {sym}: score={r['score']}/3  level={r['level']}")
                print(f"    C1量能: {r['c1_volume']['reason']}")
                print(f"    C2LSR:  {r['c2_lsr']['reason']}")
                print(f"    C3弹簧: {r['c3_spring']['reason']}")
    else:
        for a in alerts:
            card = format_alert_card(a)
            print('\n' + '='*60)
            print(card)
            print('='*60)

    return 0 if not alerts else 1


if __name__ == '__main__':
    sys.exit(main())
