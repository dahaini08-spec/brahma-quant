#!/usr/bin/env python3
"""
dharma_backtest_engine.py — 达摩院纯指标回测引擎
设计院封印 2026-09-04 苏摩111

目标：不加体制过滤，对每个技术指标单独验证真实胜率
原则：
  1. 零上帝视角 — 信号只用K线收盘时已知数据，不用当根最高/最低
  2. 严格walk-forward — 信号生成时刻 < 出场判断时刻
  3. 单指标隔离 — 每次只测一个指标，防止相互污染
  4. 多币验证 — 47个币种跑同样指标，找出跨市场普适规律

接入位置：独立脚本，输出data/dharma_backtest_result.json
"""
import sys, json, math, time, signal
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

MAX_RUNTIME = 300
signal.signal(signal.SIGALRM, lambda s,f: sys.exit(0))
signal.alarm(MAX_RUNTIME)

# ── 回测参数（封印值）────────────────────────────────────────
HOLD_BARS   = 12      # 持仓最大K线数（超时平仓）
SL_PCT      = 0.020   # 止损 2%
TP_PCT      = 0.020   # 止盈 2%（RR=1:1，纯净测WR）
MIN_SIGNALS = 30      # 最小样本量，不足则标记INSUFFICIENT
# ────────────────────────────────────────────────────────────

FANGCANG_DIR = BASE / 'data'


def load_klines(symbol: str, tf: str) -> list:
    """从fangcang数据加载K线 [ts, o, h, l, c, v]"""
    sym_lower = symbol.lower().replace('usdt', '')
    path = FANGCANG_DIR / f'fangcang_{sym_lower}_{tf}.json'
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    # fangcang格式：list of dicts 或 list of lists
    if not raw:
        return []
    if isinstance(raw[0], dict):
        # squeeze案例格式，不是OHLCV
        return []
    return raw  # [ts, o, h, l, c, v, ...]


def load_klines_from_cases(symbol: str) -> list:
    """从cases文件提取时间序列特征供回测"""
    sym_lower = symbol.lower().replace('usdt', '')
    path = FANGCANG_DIR / f'fangcang_cases_{sym_lower}.json'
    if not path.exists():
        return []
    return json.loads(path.read_text())


def fetch_binance_klines(symbol: str, tf: str, limit: int = 1500) -> list:
    """实时拉取K线作为回测数据源"""
    import urllib.request
    try:
        url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={tf}&limit={limit}'
        data = json.loads(urllib.request.urlopen(url, timeout=10).read())
        # [ts, o, h, l, c, v, ...]
        return [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in data]
    except Exception as e:
        print(f'  [fetch] {symbol} {tf} 失败: {e}')
        return []


def backtest_signals(klines: list, signals: list) -> dict:
    """
    核心回测引擎 — 零上帝视角
    
    signals: list of (bar_index, direction) — 在bar_index收盘时入场
    direction: 'LONG' or 'SHORT'
    
    出场规则（严格无上帝视角）：
      入场价 = bar_index收盘价（信号K线收盘后入场）
      从bar_index+1开始，逐根检查SL/TP
      用后续K线的high/low判断触发（非收盘价）
      最多持仓HOLD_BARS根K线
    """
    wins = losses = timeouts = 0
    pnls = []

    for idx, direction in signals:
        if idx + HOLD_BARS >= len(klines):
            continue
        entry = klines[idx][4]  # 收盘价入场
        if entry <= 0:
            continue

        outcome = 'TIMEOUT'
        exit_pnl = 0.0

        for j in range(1, HOLD_BARS + 1):
            bar = klines[idx + j]
            h, l = bar[2], bar[3]

            if direction == 'LONG':
                if l <= entry * (1 - SL_PCT):
                    outcome = 'LOSS'
                    exit_pnl = -SL_PCT
                    break
                if h >= entry * (1 + TP_PCT):
                    outcome = 'WIN'
                    exit_pnl = TP_PCT
                    break
            else:  # SHORT
                if h >= entry * (1 + SL_PCT):
                    outcome = 'LOSS'
                    exit_pnl = -SL_PCT
                    break
                if l <= entry * (1 - TP_PCT):
                    outcome = 'WIN'
                    exit_pnl = TP_PCT
                    break

        if outcome == 'WIN':
            wins += 1
            pnls.append(exit_pnl)
        elif outcome == 'LOSS':
            losses += 1
            pnls.append(exit_pnl)
        else:
            timeouts += 1
            # 超时：用最后一根K线收盘价计算实际PnL
            final_c = klines[idx + HOLD_BARS][4]
            if direction == 'LONG':
                pnls.append((final_c - entry) / entry)
            else:
                pnls.append((entry - final_c) / entry)

    n = wins + losses + timeouts
    wl = wins + losses
    wr = wins / wl if wl > 0 else 0.5
    ev = sum(pnls) / len(pnls) if pnls else 0
    avg_win  = sum(p for p in pnls if p > 0) / max(wins, 1)
    avg_loss = sum(p for p in pnls if p < 0) / max(losses, 1)

    return {
        'n': n, 'wins': wins, 'losses': losses, 'timeouts': timeouts,
        'wr': round(wr, 4), 'ev': round(ev, 6),
        'avg_win': round(avg_win, 4), 'avg_loss': round(avg_loss, 4),
        'sufficient': n >= MIN_SIGNALS,
    }


# ══════════════════════════════════════════════════════════════
# 指标信号生成函数（每个函数只用i时刻及之前的数据）
# ══════════════════════════════════════════════════════════════

def ema(data: list, n: int) -> list:
    k = 2 / (n + 1)
    out = [data[0]]
    for v in data[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi_series(closes: list, n: int = 14) -> list:
    gains = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
    result = [None] * n
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n-1) + gains[i]) / n
        avg_l = (avg_l * (n-1) + losses[i]) / n
        rs = avg_g / avg_l if avg_l > 0 else 99
        result.append(100 - 100 / (1 + rs))
    return [None] + result  # 对齐closes


def atr_series(klines: list, n: int = 14) -> list:
    trs = [max(klines[i][2]-klines[i][3],
               abs(klines[i][2]-klines[i-1][4]),
               abs(klines[i][3]-klines[i-1][4])) for i in range(1, len(klines))]
    result = [None] * n
    avg = sum(trs[:n]) / n
    result.append(avg)
    for t in trs[n:]:
        avg = (avg * (n-1) + t) / n
        result.append(avg)
    return [None] + result


def bb_series(closes: list, n: int = 20, k: float = 2.0):
    """布林带，返回(upper, mid, lower, width_pct)列表"""
    result = []
    for i in range(len(closes)):
        if i < n - 1:
            result.append(None)
            continue
        window = closes[i-n+1:i+1]
        mid = sum(window) / n
        std = math.sqrt(sum((x-mid)**2 for x in window) / n)
        upper = mid + k * std
        lower = mid - k * std
        width_pct = (upper - lower) / mid * 100
        result.append((upper, mid, lower, width_pct))
    return result


# ── 指标1：RSI超卖做多 / 超买做空 ───────────────────────────
def signals_rsi_reversal(klines: list) -> tuple:
    """RSI<30做多，RSI>70做空"""
    closes = [k[4] for k in klines]
    rsi = rsi_series(closes, 14)
    long_sigs, short_sigs = [], []
    for i in range(15, len(klines)-1):
        if rsi[i] is None: continue
        if rsi[i] < 30:
            long_sigs.append((i, 'LONG'))
        elif rsi[i] > 70:
            short_sigs.append((i, 'SHORT'))
    return long_sigs, short_sigs


# ── 指标2：EMA金叉/死叉 ────────────────────────────────────
def signals_ema_cross(klines: list) -> tuple:
    """EMA9上穿EMA21做多，下穿做空"""
    closes = [k[4] for k in klines]
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    long_sigs, short_sigs = [], []
    for i in range(22, len(klines)-1):
        if e9[i] > e21[i] and e9[i-1] <= e21[i-1]:
            long_sigs.append((i, 'LONG'))
        elif e9[i] < e21[i] and e9[i-1] >= e21[i-1]:
            short_sigs.append((i, 'SHORT'))
    return long_sigs, short_sigs


# ── 指标3：成交量突变（≥3x均量）+ 方向 ────────────────────
def signals_volume_spike(klines: list) -> tuple:
    """量能≥前4根均量3倍，收阳做多，收阴做空"""
    long_sigs, short_sigs = [], []
    for i in range(5, len(klines)-1):
        avg_v = sum(k[5] for k in klines[i-4:i]) / 4
        cur_v = klines[i][5]
        if avg_v <= 0: continue
        if cur_v >= avg_v * 3:
            if klines[i][4] > klines[i][1]:   # 收阳
                long_sigs.append((i, 'LONG'))
            elif klines[i][4] < klines[i][1]:  # 收阴
                short_sigs.append((i, 'SHORT'))
    return long_sigs, short_sigs


# ── 指标4：布林带收缩后突破 ────────────────────────────────
def signals_bb_squeeze_breakout(klines: list) -> tuple:
    """BB宽度<1.5%后，价格突破上轨做多，突破下轨做空"""
    closes = [k[4] for k in klines]
    bbs = bb_series(closes, 20, 2.0)
    long_sigs, short_sigs = [], []
    for i in range(21, len(klines)-1):
        if bbs[i] is None or bbs[i-1] is None: continue
        prev_width = bbs[i-1][3]
        if prev_width > 2.0: continue  # 前一根宽度不够窄
        upper, _, lower, _ = bbs[i]
        c = closes[i]
        if c > upper:
            long_sigs.append((i, 'LONG'))
        elif c < lower:
            short_sigs.append((i, 'SHORT'))
    return long_sigs, short_sigs


# ── 指标5：Hurst指数（H>0.6=趋势，H<0.4=均值回归）──────────
def signals_hurst(klines: list, window: int = 100) -> tuple:
    """
    用R/S方法估算Hurst，H>0.6顺趋势，H<0.4逆趋势
    顺趋势：价格高于EMA50做多，低于做空
    逆趋势：价格高于EMA50做空（回归），低于做多
    """
    closes = [k[4] for k in klines]
    e50 = ema(closes, 50)
    long_sigs, short_sigs = [], []

    for i in range(window + 50, len(klines)-1):
        w = closes[i-window:i]
        # R/S Hurst估算
        try:
            log_r = [math.log(w[j]/w[j-1]) for j in range(1, len(w))]
            mean_r = sum(log_r) / len(log_r)
            dev = [r - mean_r for r in log_r]
            cum = [sum(dev[:j+1]) for j in range(len(dev))]
            R = max(cum) - min(cum)
            S = math.sqrt(sum(d**2 for d in dev) / len(dev))
            if S == 0: continue
            rs = R / S
            if rs <= 0: continue
            H = math.log(rs) / math.log(len(log_r))
        except Exception:
            continue

        c = closes[i]
        e = e50[i]
        if e is None: continue

        if H > 0.6:  # 趋势
            if c > e:
                long_sigs.append((i, 'LONG'))
            else:
                short_sigs.append((i, 'SHORT'))
        elif H < 0.4:  # 均值回归
            if c > e:
                short_sigs.append((i, 'SHORT'))
            else:
                long_sigs.append((i, 'LONG'))

    return long_sigs, short_sigs


# ── 指标6：ATR突破（波动率扩张）─────────────────────────────
def signals_atr_expansion(klines: list) -> tuple:
    """ATR > 前10根均ATR的1.5倍，收阳做多，收阴做空"""
    atrs = atr_series(klines, 14)
    long_sigs, short_sigs = [], []
    for i in range(25, len(klines)-1):
        if atrs[i] is None: continue
        recent_atrs = [atrs[j] for j in range(i-10, i) if atrs[j] is not None]
        if not recent_atrs: continue
        avg_atr = sum(recent_atrs) / len(recent_atrs)
        if atrs[i] > avg_atr * 1.5:
            if klines[i][4] > klines[i][1]:
                long_sigs.append((i, 'LONG'))
            else:
                short_sigs.append((i, 'SHORT'))
    return long_sigs, short_sigs


# ── 指标7：连续下跌N根反弹（超跌反弹）──────────────────────
def signals_consecutive_reversal(klines: list, n: int = 4) -> tuple:
    """连续n根阴线后收阳做多，连续n根阳线后收阴做空"""
    closes = [k[4] for k in klines]
    opens  = [k[1] for k in klines]
    long_sigs, short_sigs = [], []
    for i in range(n+1, len(klines)-1):
        # 前n根全是阴线，第i根收阳
        if all(closes[j] < opens[j] for j in range(i-n, i)) and closes[i] > opens[i]:
            long_sigs.append((i, 'LONG'))
        # 前n根全是阳线，第i根收阴
        elif all(closes[j] > opens[j] for j in range(i-n, i)) and closes[i] < opens[i]:
            short_sigs.append((i, 'SHORT'))
    return long_sigs, short_sigs


# ══════════════════════════════════════════════════════════════
# 主回测流程
# ══════════════════════════════════════════════════════════════

INDICATORS = {
    'RSI_reversal':           signals_rsi_reversal,
    'EMA_cross_9_21':         signals_ema_cross,
    'Volume_spike_3x':        signals_volume_spike,
    'BB_squeeze_breakout':    signals_bb_squeeze_breakout,
    'Hurst_trend_reversion':  signals_hurst,
    'ATR_expansion':          signals_atr_expansion,
    'Consecutive_reversal_4': signals_consecutive_reversal,
}

# 47个币的symbol映射（与fangcang文件名对应）
SYMBOLS_47 = [
    'btc','eth','sol','bnb','ada','dot','link','ltc','xrp','doge',
    'atom','algo','crv','dash','etc','egld','iota','kava','neo','ont',
    'rune','snx','sushi','theta','trb','trx','vet','xlm','xmr','zec',
    'zil','bch','comp',
]

TIMEFRAMES = ['1h', '4h']


def run_single(symbol_lower: str, tf: str, indicator_name: str, verbose: bool = False) -> dict:
    """对单个币+时间框架+指标跑回测"""
    # 优先用实时数据（Binance），回退到fangcang静态数据
    sym_upper = symbol_lower.upper() + 'USDT'
    klines = fetch_binance_klines(sym_upper, tf, limit=1500)

    if len(klines) < 200:
        # 尝试fangcang静态文件
        path = FANGCANG_DIR / f'fangcang_{symbol_lower}_{tf}.json'
        if path.exists():
            raw = json.loads(path.read_text())
            if raw and isinstance(raw[0], list):
                klines = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in raw]

    if len(klines) < 200:
        return {'symbol': symbol_lower, 'tf': tf, 'indicator': indicator_name,
                'status': 'INSUFFICIENT_DATA', 'n': len(klines)}

    fn = INDICATORS[indicator_name]
    try:
        long_sigs, short_sigs = fn(klines)
    except Exception as e:
        return {'symbol': symbol_lower, 'tf': tf, 'indicator': indicator_name,
                'status': 'ERROR', 'error': str(e)[:80]}

    long_r  = backtest_signals(klines, long_sigs)
    short_r = backtest_signals(klines, short_sigs)

    def grade(r):
        if not r['sufficient']: return 'INSUFFICIENT'
        if r['wr'] >= 0.65 and r['ev'] > 0: return 'TIER_1'
        if r['wr'] >= 0.55 and r['ev'] > 0: return 'TIER_2'
        if r['wr'] >= 0.50 and r['ev'] > 0: return 'TIER_3'
        return 'NOISE'

    result = {
        'symbol': symbol_lower, 'tf': tf, 'indicator': indicator_name,
        'klines_n': len(klines),
        'LONG':  {**long_r,  'grade': grade(long_r)},
        'SHORT': {**short_r, 'grade': grade(short_r)},
    }
    if verbose:
        lg, sg = grade(long_r), grade(short_r)
        lw = f"{long_r['wr']*100:.1f}%" if long_r['sufficient'] else 'n/a'
        sw = f"{short_r['wr']*100:.1f}%" if short_r['sufficient'] else 'n/a'
        print(f"  {symbol_lower:<6} {tf:<3} {indicator_name:<28} LONG={lw}({lg}) SHORT={sw}({sg})")
    return result


def run_full_backtest(symbols=None, timeframes=None, indicators=None, verbose=True):
    """全量回测入口"""
    symbols    = symbols    or SYMBOLS_47[:10]  # 默认先跑10个
    timeframes = timeframes or TIMEFRAMES
    indicators = indicators or list(INDICATORS.keys())

    results = []
    t0 = time.time()

    print(f'\n{"="*70}')
    print(f'  达摩院回测引擎 | {len(symbols)}币 × {len(timeframes)}TF × {len(indicators)}指标')
    print(f'  SL={SL_PCT*100:.0f}% TP={TP_PCT*100:.0f}% 持仓≤{HOLD_BARS}根 最小样本={MIN_SIGNALS}')
    print(f'{"="*70}')

    for ind in indicators:
        print(f'\n▌ {ind}')
        for tf in timeframes:
            for sym in symbols:
                r = run_single(sym, tf, ind, verbose=verbose)
                results.append(r)

    elapsed = time.time() - t0

    # 汇总TIER_1
    tier1 = [(r['indicator'], r['symbol'], r['tf'], 'LONG',  r['LONG'])
             for r in results if r.get('LONG', {}).get('grade') == 'TIER_1']
    tier1 += [(r['indicator'], r['symbol'], r['tf'], 'SHORT', r['SHORT'])
              for r in results if r.get('SHORT', {}).get('grade') == 'TIER_1']

    print(f'\n{"="*70}')
    print(f'  完成 {len(results)}项回测 耗时{elapsed:.0f}s')
    print(f'\n  🏆 TIER_1 (WR≥65% EV>0):')
    if tier1:
        for ind, sym, tf, d, r in sorted(tier1, key=lambda x: -x[4]['wr'])[:20]:
            print(f'    {ind:<28} {sym:<6} {tf} {d:<5} WR={r["wr"]*100:.1f}% EV={r["ev"]*100:+.2f}% n={r["n"]}')
    else:
        print('    无TIER_1指标（样本不足或市场当前无效）')
    print(f'{"="*70}\n')

    # 保存结果
    output = {
        'ts': datetime.utcnow().isoformat() + 'Z',
        'params': {'SL': SL_PCT, 'TP': TP_PCT, 'HOLD': HOLD_BARS, 'MIN_N': MIN_SIGNALS},
        'symbols': symbols, 'timeframes': timeframes, 'indicators': indicators,
        'results': results,
        'tier1_summary': [
            {'indicator': i, 'symbol': s, 'tf': t, 'direction': d,
             'wr': r['wr'], 'ev': r['ev'], 'n': r['n']}
            for i, s, t, d, r in tier1
        ],
    }
    out_path = BASE / 'data' / 'dharma_backtest_result.json'
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f'结果已保存: {out_path}')
    return output


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', default=None, help='币种列表（小写，不含USDT）')
    parser.add_argument('--tf',      nargs='+', default=['1h'], help='时间框架')
    parser.add_argument('--all',     action='store_true', help='跑全部47个币')
    parser.add_argument('--quick',   action='store_true', help='只跑BTC/ETH验证框架')
    args = parser.parse_args()

    if args.quick:
        syms = ['btc', 'eth']
    elif args.all:
        syms = SYMBOLS_47
    else:
        syms = args.symbols or ['btc', 'eth', 'sol', 'bnb', 'ada']

    run_full_backtest(symbols=syms, timeframes=args.tf)
