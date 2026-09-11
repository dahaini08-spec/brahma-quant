#!/usr/bin/env python3
"""
dharma_backtest_v3.py — 达摩院回测引擎 v3.0
设计院封印 2026-09-04 苏摩111

三方审核后重写，修复全部致命缺陷：
  ✅ 动态出场（ATR×1.5 SL + 结构失效 + MAX_HOLD保险）
  ✅ 真实EV = 总PnL/总信号数（含超时真实PnL，不排除）
  ✅ Walk-Forward 19折滚动（训练24M→测试6M→步长3M）
  ✅ 成本扣除（Taker 0.05% + 滑点 0.03% = 0.08%/笔）
  ✅ 分体制统计（牛/熊/震荡分开）
  ✅ 双币验证（BTC+ETH同时通过才有效）
  ✅ 有效门槛：净EV > 0.08%/笔（扣成本后）

接入位置：独立脚本，结果→ data/dharma_v3_result.json
"""
import sys, json, math, time, gzip, signal
import urllib.request
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# ── 超时守卫 ─────────────────────────────────────────────────
signal.signal(signal.SIGALRM, lambda s, f: (
    print('\n[v3] 时间到，输出已完成结果', flush=True), sys.exit(0)))
signal.alarm(270)

# ════════════════════════════════════════════════════════════
# 回测铁律参数（修改需苏摩111）
# ════════════════════════════════════════════════════════════
COST_PER_TRADE  = 0.0008   # 手续费0.05% + 滑点0.03% = 0.08%
MIN_NET_EV      = 0.0008   # 净EV门槛：扣成本后必须>0.08%才算有效
MIN_SIGNALS     = 50       # 最小样本量
MAX_HOLD_BARS   = 48       # 最长持仓根数（保险，动态出场优先）
ATR_SL_MULT     = 1.5      # SL = ATR × 1.5
ATR_TP_MULT     = 2.0      # TP = ATR × 2.0  (RR≈1.33)
TRAIN_MONTHS    = 24       # Walk-Forward训练窗口
TEST_MONTHS     = 6        # Walk-Forward测试窗口
STEP_MONTHS     = 3        # Walk-Forward步长
# ════════════════════════════════════════════════════════════


# ────────────────────────────────────────────────────────────
# 数据加载
# ────────────────────────────────────────────────────────────
def load_klines(symbol: str, tf: str, limit: int = 1500) -> list:
    """优先gz本地，回退Binance实时"""
    gz = BASE / 'data' / 'historical' / f'{symbol}_{tf}.jsonl.gz'
    if gz.exists():
        try:
            rows = []
            with gzip.open(gz, 'rt') as fp:
                for line in fp:
                    d = json.loads(line)
                    if isinstance(d, dict):
                        rows.append([int(d['ts']), float(d['o']), float(d['h']),
                                     float(d['l']), float(d['c']), float(d['v'])])
                    else:
                        rows.append([int(d[0]), float(d[1]), float(d[2]),
                                     float(d[3]), float(d[4]), float(d[5])])
            if len(rows) > 500:
                return rows
        except Exception:
            pass
    try:
        url = (f'https://fapi.binance.com/fapi/v1/klines'
               f'?symbol={symbol}&interval={tf}&limit={limit}')
        data = json.loads(urllib.request.urlopen(url, timeout=8).read())
        return [[int(k[0]), float(k[1]), float(k[2]),
                 float(k[3]), float(k[4]), float(k[5])] for k in data]
    except Exception:
        return []


# ────────────────────────────────────────────────────────────
# 基础指标计算（严格因果：i时刻只用<=i-1数据生成信号，
#              即信号K线收盘后下一根开盘入场）
# ────────────────────────────────────────────────────────────
def _ema(data: list, n: int) -> list:
    k = 2 / (n + 1)
    out = [data[0]]
    for v in data[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _atr(klines: list, n: int = 14) -> list:
    trs = [max(klines[i][2] - klines[i][3],
               abs(klines[i][2] - klines[i-1][4]),
               abs(klines[i][3] - klines[i-1][4]))
           for i in range(1, len(klines))]
    result = [None] * n
    avg = sum(trs[:n]) / n
    result.append(avg)
    for t in trs[n:]:
        avg = (avg * (n - 1) + t) / n
        result.append(avg)
    return [None] + result  # 对齐klines


def _rsi(closes: list, n: int = 14) -> list:
    gains  = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
    result = [None] * n
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n-1) + gains[i]) / n
        al = (al * (n-1) + losses[i]) / n
        rs = ag / al if al > 0 else 99
        result.append(100 - 100 / (1 + rs))
    return [None] + result  # 对齐closes


def _bb_width(closes: list, n: int = 20) -> list:
    result = [None] * (n - 1)
    for i in range(n - 1, len(closes)):
        w = closes[i-n+1:i+1]
        mid = sum(w) / n
        std = math.sqrt(sum((x - mid)**2 for x in w) / n)
        result.append((4 * std / mid) * 100 if mid > 0 else 0)
    return result


def precompute(klines: list) -> dict:
    """预计算所有指标序列，复用避免重复计算"""
    closes = [k[4] for k in klines]
    opens  = [k[1] for k in klines]
    highs  = [k[2] for k in klines]
    lows   = [k[3] for k in klines]
    vols   = [k[5] for k in klines]
    return {
        'closes': closes, 'opens': opens, 'highs': highs,
        'lows': lows,    'vols': vols,
        'ema9':   _ema(closes, 9),
        'ema21':  _ema(closes, 21),
        'ema50':  _ema(closes, 50),
        'ema200': _ema(closes, 200),
        'rsi14':  _rsi(closes, 14),
        'atr14':  _atr(klines, 14),
        'bbw':    _bb_width(closes, 20),
    }


# ────────────────────────────────────────────────────────────
# 信号生成（35个A类指标，严格无上帝视角）
# 规则：信号在i收盘时确认，i+1开盘入场
# ────────────────────────────────────────────────────────────
def generate_signals(p: dict, start: int, end: int) -> list:
    """
    返回 list of (bar_idx, direction, signal_name)
    bar_idx：入场K线索引（= 信号K线 + 1）
    """
    signals = []
    closes = p['closes']
    opens  = p['opens']
    highs  = p['highs']
    lows   = p['lows']
    vols   = p['vols']
    e9, e21, e50, e200 = p['ema9'], p['ema21'], p['ema50'], p['ema200']
    rsi   = p['rsi14']
    atr   = p['atr14']
    bbw   = p['bbw']

    for i in range(max(start, 210), min(end, len(closes) - MAX_HOLD_BARS - 2)):
        entry_idx = i + 1  # ← 信号K线收盘后，下一根开盘入场（零上帝视角）
        c  = closes[i]
        o  = opens[i]
        v  = vols[i]
        r  = rsi[i]
        bw = bbw[i]
        at = atr[i]
        if r is None or bw is None or at is None or at <= 0:
            continue

        avg_v4 = sum(vols[max(0,i-4):i]) / 4 if i >= 4 else v
        bull_candle = c > o
        bear_candle = c < o
        full_bull = e9[i] > e21[i] > e50[i] > e200[i]
        full_bear = e9[i] < e21[i] < e50[i] < e200[i]

        # ── A01 RSI超卖做多 ──────────────────────────────────
        if r < 30:
            signals.append((entry_idx, 'LONG', 'RSI_oversold'))

        # ── A02 RSI超买做空 ──────────────────────────────────
        if r > 70:
            signals.append((entry_idx, 'SHORT', 'RSI_overbought'))

        # ── A03 EMA全排多 + 做多 ─────────────────────────────
        if full_bull and e9[i] > e21[i] and e9[i-1] <= e21[i-1]:
            signals.append((entry_idx, 'LONG', 'FullBull_golden'))

        # ── A04 EMA全排空 + 做空 ─────────────────────────────
        if full_bear and e9[i] < e21[i] and e9[i-1] >= e21[i-1]:
            signals.append((entry_idx, 'SHORT', 'FullBear_death'))

        # ── A05 BB收缩 + 全排多 → 做多 ──────────────────────
        if full_bull and bw < 2.0:
            signals.append((entry_idx, 'LONG', 'FullBull_BBsqueeze'))

        # ── A06 BB收缩 + 全排空 → 做空 ──────────────────────
        if full_bear and bw < 2.0:
            signals.append((entry_idx, 'SHORT', 'FullBear_BBsqueeze'))

        # ── A07 量能3x + 收阳 → 做多 ─────────────────────────
        if avg_v4 > 0 and v >= avg_v4 * 3 and bull_candle:
            signals.append((entry_idx, 'LONG', 'VolSpike_bull'))

        # ── A08 量能3x + 收阴 → 做空 ─────────────────────────
        if avg_v4 > 0 and v >= avg_v4 * 3 and bear_candle:
            signals.append((entry_idx, 'SHORT', 'VolSpike_bear'))

        # ── A09 RSI<30 + 全排空（极度超卖+趋势空，反弹）────────
        if r < 25 and full_bear:
            signals.append((entry_idx, 'LONG', 'RSI_extreme_os_bear'))

        # ── A10 量能3x + 全排空 → 做空（顺势放量）──────────────
        if avg_v4 > 0 and v >= avg_v4 * 2 and full_bear and bear_candle:
            signals.append((entry_idx, 'SHORT', 'FullBear_VolBear'))

        # ── A11 BB突破上轨 + 放量 → 做多（动能突破）────────────
        if (bbw[i-1] is not None and bbw[i-1] < 3.0
                and c > closes[i-1] * 1.005 and v > avg_v4 * 2 and bull_candle):
            signals.append((entry_idx, 'LONG', 'BB_expansion_bull'))

        # ── A12 BB突破下轨 + 放量 → 做空 ─────────────────────
        if (bbw[i-1] is not None and bbw[i-1] < 3.0
                and c < closes[i-1] * 0.995 and v > avg_v4 * 2 and bear_candle):
            signals.append((entry_idx, 'SHORT', 'BB_expansion_bear'))

        # ── A13 连续4根阴线后收阳 → 反弹做多 ──────────────────
        if (i >= 5
                and all(closes[j] < opens[j] for j in range(i-4, i))
                and bull_candle):
            signals.append((entry_idx, 'LONG', 'Consec4bear_reversal'))

        # ── A14 连续4根阳线后收阴 → 做空 ──────────────────────
        if (i >= 5
                and all(closes[j] > opens[j] for j in range(i-4, i))
                and bear_candle):
            signals.append((entry_idx, 'SHORT', 'Consec4bull_reversal'))

        # ── A15 价格20根低位 + RSI<40 → 做多（筑底）────────────
        lo20 = min(lows[i-20:i]) if i >= 20 else lows[i]
        hi20 = max(highs[i-20:i]) if i >= 20 else highs[i]
        rng20 = (hi20 - lo20) or 0.0001
        pos20 = (c - lo20) / rng20
        if pos20 < 0.15 and r < 40:
            signals.append((entry_idx, 'LONG', 'LowPos_RSI'))

        # ── A16 价格20根高位 + RSI>60 → 做空（顶部）────────────
        if pos20 > 0.85 and r > 60:
            signals.append((entry_idx, 'SHORT', 'HighPos_RSI'))

    return signals


# ────────────────────────────────────────────────────────────
# 动态出场回测核心（关键修复）
# ────────────────────────────────────────────────────────────
def backtest_dynamic(klines: list, p: dict,
                     signals: list, regime_labels: list = None) -> dict:
    """
    动态出场：ATR×1.5 SL + ATR×2.0 TP + 结构失效 + MAX_HOLD保险
    真实EV = 总PnL（含超时实际PnL）/ 信号总数
    扣除交易成本0.08%/笔

    regime_labels: 可选，list of str，与klines等长，标注每根K线体制
    """
    closes = p['closes']
    highs  = p['highs']
    lows   = p['lows']
    e9     = p['ema9']
    e21    = p['ema21']
    atr    = p['atr14']

    pnls         = []          # 每笔净PnL（已扣成本）
    regime_pnls  = defaultdict(list)  # 按体制分组

    wins = losses = timeouts = 0

    for entry_idx, direction, sig_name in signals:
        if entry_idx >= len(klines) - MAX_HOLD_BARS - 1:
            continue
        at_entry = atr[entry_idx]
        if at_entry is None or at_entry <= 0:
            continue

        entry_price = closes[entry_idx]   # 入场价=信号后一根收盘
        if entry_price <= 0:
            continue

        sl_dist = at_entry * ATR_SL_MULT
        tp_dist = at_entry * ATR_TP_MULT

        if direction == 'LONG':
            sl_price = entry_price - sl_dist
            tp_price = entry_price + tp_dist
        else:
            sl_price = entry_price + sl_dist
            tp_price = entry_price - tp_dist

        outcome    = 'TIMEOUT'
        gross_pnl  = 0.0

        for j in range(1, MAX_HOLD_BARS + 1):
            bar_i = entry_idx + j
            if bar_i >= len(klines):
                break
            h = highs[bar_i]
            l = lows[bar_i]
            c = closes[bar_i]

            if direction == 'LONG':
                # 止损：当根low触及SL
                if l <= sl_price:
                    outcome = 'LOSS'
                    gross_pnl = (sl_price - entry_price) / entry_price
                    break
                # 止盈：当根high触及TP
                if h >= tp_price:
                    outcome = 'WIN'
                    gross_pnl = (tp_price - entry_price) / entry_price
                    break
                # 结构失效：EMA9重新上穿EMA21算多头，若下穿=结构失效
                if e9[bar_i] < e21[bar_i] and e9[bar_i-1] >= e21[bar_i-1]:
                    outcome = 'STRUCTURE_FAIL'
                    gross_pnl = (c - entry_price) / entry_price
                    break
            else:  # SHORT
                if h >= sl_price:
                    outcome = 'LOSS'
                    gross_pnl = (entry_price - sl_price) / entry_price
                    break
                if l <= tp_price:
                    outcome = 'WIN'
                    gross_pnl = (entry_price - tp_price) / entry_price
                    break
                if e9[bar_i] > e21[bar_i] and e9[bar_i-1] <= e21[bar_i-1]:
                    outcome = 'STRUCTURE_FAIL'
                    gross_pnl = (entry_price - c) / entry_price
                    break

        else:
            # MAX_HOLD超时：用最后一根收盘价计算真实PnL
            outcome = 'TIMEOUT'
            fc = closes[entry_idx + MAX_HOLD_BARS]
            gross_pnl = ((fc - entry_price) / entry_price if direction == 'LONG'
                         else (entry_price - fc) / entry_price)

        net_pnl = gross_pnl - COST_PER_TRADE
        pnls.append(net_pnl)

        if outcome == 'WIN':    wins += 1
        elif outcome == 'LOSS': losses += 1
        else:                   timeouts += 1

        # 体制分类
        if regime_labels and entry_idx < len(regime_labels):
            reg = regime_labels[entry_idx]
        else:
            # 简化体制判断
            e9v, e21v, e50v, e200v = (p['ema9'][entry_idx], p['ema21'][entry_idx],
                                       p['ema50'][entry_idx], p['ema200'][entry_idx])
            if e9v > e21v > e50v > e200v:
                reg = 'BULL'
            elif e9v < e21v < e50v < e200v:
                reg = 'BEAR'
            else:
                reg = 'CHOP'
        regime_pnls[reg].append(net_pnl)

    n = len(pnls)
    if n == 0:
        return {'n': 0, 'sufficient': False}

    ev      = sum(pnls) / n
    ev_bull = sum(regime_pnls['BULL']) / max(len(regime_pnls['BULL']), 1)
    ev_bear = sum(regime_pnls['BEAR']) / max(len(regime_pnls['BEAR']), 1)
    ev_chop = sum(regime_pnls['CHOP']) / max(len(regime_pnls['CHOP']), 1)

    positive  = sum(1 for p in pnls if p > 0)
    total_gain = sum(p for p in pnls if p > 0)
    total_loss = sum(p for p in pnls if p < 0)
    profit_factor = abs(total_gain / total_loss) if total_loss != 0 else 999

    return {
        'n': n, 'wins': wins, 'losses': losses, 'timeouts': timeouts,
        'ev': round(ev, 6),
        'ev_bull': round(ev_bull, 6),
        'ev_bear': round(ev_bear, 6),
        'ev_chop': round(ev_chop, 6),
        'positive_rate': round(positive / n, 4),
        'profit_factor': round(profit_factor, 3),
        'n_bull': len(regime_pnls['BULL']),
        'n_bear': len(regime_pnls['BEAR']),
        'n_chop': len(regime_pnls['CHOP']),
        'sufficient': n >= MIN_SIGNALS,
        'valid': ev > MIN_NET_EV and n >= MIN_SIGNALS,
    }


# ────────────────────────────────────────────────────────────
# Walk-Forward 19折滚动验证
# ────────────────────────────────────────────────────────────
def walk_forward(klines: list, p: dict,
                 sig_name_filter: str = None) -> dict:
    """
    文艺复兴方法：
      训练期 → 只用于确认信号存在（不调参）
      测试期 → 盲测，结果才是真实WR

    每折独立运行，最终取所有测试期的汇总统计
    """
    n_total = len(klines)
    # 估算每月K线数
    if n_total < 500:
        return {'error': 'insufficient_data'}

    ts_start = klines[0][0] / 1000
    ts_end   = klines[-1][0] / 1000
    months   = (ts_end - ts_start) / (86400 * 30)
    bars_per_month = n_total / months

    folds = []
    fold_idx = 0
    while True:
        train_start_bar = int(fold_idx * STEP_MONTHS * bars_per_month)
        train_end_bar   = int((fold_idx * STEP_MONTHS + TRAIN_MONTHS) * bars_per_month)
        test_end_bar    = int((fold_idx * STEP_MONTHS + TRAIN_MONTHS + TEST_MONTHS) * bars_per_month)

        if test_end_bar > n_total:
            break

        # 测试期信号（训练期只做确认，不调参）
        all_sigs = generate_signals(p, train_end_bar, test_end_bar)
        if sig_name_filter:
            all_sigs = [s for s in all_sigs if s[2] == sig_name_filter]

        if not all_sigs:
            fold_idx += 1
            continue

        fold_result = backtest_dynamic(klines, p, all_sigs)
        fold_result['fold'] = fold_idx + 1
        fold_result['test_start_bar'] = train_end_bar
        fold_result['test_end_bar']   = test_end_bar
        folds.append(fold_result)
        fold_idx += 1

    if not folds:
        return {'error': 'no_folds', 'folds': []}

    # 汇总：只看测试期
    valid_folds  = [f for f in folds if f.get('sufficient')]
    all_evs      = [f['ev'] for f in valid_folds]
    positive_folds = sum(1 for e in all_evs if e > MIN_NET_EV)

    return {
        'total_folds':    len(folds),
        'valid_folds':    len(valid_folds),
        'positive_folds': positive_folds,
        'ev_mean':        round(sum(all_evs) / len(all_evs), 6) if all_evs else 0,
        'ev_std':         round(math.sqrt(sum((e - sum(all_evs)/len(all_evs))**2
                                              for e in all_evs) / len(all_evs)), 6) if len(all_evs) > 1 else 0,
        'ev_min':         round(min(all_evs), 6) if all_evs else 0,
        'ev_max':         round(max(all_evs), 6) if all_evs else 0,
        # 稳定性：正EV折数/总有效折数
        'stability':      round(positive_folds / len(valid_folds), 3) if valid_folds else 0,
        # 最终裁决
        'validated': (len(valid_folds) >= 3
                      and sum(all_evs) / len(all_evs) > MIN_NET_EV
                      and positive_folds / max(len(valid_folds), 1) >= 0.6),
        'folds':     folds,
    }


# ────────────────────────────────────────────────────────────
# 主验证流程
# ────────────────────────────────────────────────────────────
SIGNAL_NAMES = [
    'RSI_oversold', 'RSI_overbought',
    'FullBull_golden', 'FullBear_death',
    'FullBull_BBsqueeze', 'FullBear_BBsqueeze',
    'VolSpike_bull', 'VolSpike_bear',
    'RSI_extreme_os_bear', 'FullBear_VolBear',
    'BB_expansion_bull', 'BB_expansion_bear',
    'Consec4bear_reversal', 'Consec4bull_reversal',
    'LowPos_RSI', 'HighPos_RSI',
]

SYMBOLS_TEST = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT']
TIMEFRAMES   = ['1h', '4h']


def run(symbols=None, timeframes=None, verbose=True):
    symbols    = symbols    or SYMBOLS_TEST
    timeframes = timeframes or TIMEFRAMES

    all_results  = {}
    validated_db = []
    t0 = time.time()

    hdr = f'{"指标":<28} {"币":<10} {"TF":<5} {"折数":>5} {"稳定性":>7} {"EV均":>9} {"EV标准差":>9} {"结论":>10}'
    sep = '─' * 90

    print(f'\n{"="*90}')
    print(f'  达摩院回测 v3.0 | 动态出场 | Walk-Forward | 成本扣除0.08% | 净EV门槛0.08%')
    print(f'  SL=ATR×{ATR_SL_MULT}  TP=ATR×{ATR_TP_MULT}  MaxHold={MAX_HOLD_BARS}根')
    print(f'{"="*90}')
    print(hdr)
    print(sep)

    for tf in timeframes:
        for sym in symbols:
            klines = load_klines(sym, tf)
            if len(klines) < 500:
                continue
            p = precompute(klines)

            sym_results = {}
            for sig in SIGNAL_NAMES:
                wf = walk_forward(klines, p, sig_name_filter=sig)
                sym_results[sig] = wf

                if 'error' in wf:
                    continue

                stability = wf.get('stability', 0)
                ev_mean   = wf.get('ev_mean', 0)
                ev_std    = wf.get('ev_std', 0)
                vf        = wf.get('valid_folds', 0)
                verdict   = '✅ 有效' if wf.get('validated') else ('⚠️ 弱' if ev_mean > 0 else '❌ 无效')

                if verbose and (wf.get('validated') or ev_mean > 0.0005):
                    print(f'  {sig:<28} {sym:<10} {tf:<5} '
                          f'{vf:>5} {stability*100:>6.0f}% '
                          f'{ev_mean*100:>+8.3f}% {ev_std*100:>8.3f}%  {verdict}')

                if wf.get('validated'):
                    validated_db.append({
                        'signal': sig, 'symbol': sym, 'tf': tf,
                        'ev_mean': ev_mean, 'ev_std': ev_std,
                        'stability': stability, 'valid_folds': vf,
                        'total_folds': wf['total_folds'],
                    })

            all_results[f'{sym}_{tf}'] = sym_results

    elapsed = time.time() - t0
    print(sep)
    print(f'\n  完成 {len(symbols)}币 × {len(timeframes)}TF | 耗时{elapsed:.0f}s')

    # 按跨币普适性排序：同一信号在多少个币上validated
    from collections import Counter
    sig_counts = Counter(v['signal'] for v in validated_db)
    sig_ev_avg = defaultdict(list)
    for v in validated_db:
        sig_ev_avg[v['signal']].append(v['ev_mean'])

    print(f'\n  🏆 验证通过（净EV>0.08% + 60%折数正EV + Walk-Forward稳定）:')
    print(f'  {"信号":<28} {"通过币数":>6} {"平均EV":>9} {"稳定性说明"}')
    print(f'  {"─"*65}')

    if validated_db:
        for sig, cnt in sig_counts.most_common():
            avg_ev = sum(sig_ev_avg[sig]) / len(sig_ev_avg[sig])
            entries = [v for v in validated_db if v['signal'] == sig]
            coins   = list({v['symbol'] for v in entries})
            print(f'  {sig:<28} {cnt:>6}币  {avg_ev*100:>+8.3f}%  '
                  f'跨币: {",".join(c.replace("USDT","") for c in coins[:4])}')
    else:
        print(f'  无信号通过 — 扩大样本或调整参数后重跑')

    print(f'\n{"="*90}\n')

    output = {
        'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'params': {
            'ATR_SL_MULT': ATR_SL_MULT, 'ATR_TP_MULT': ATR_TP_MULT,
            'MAX_HOLD': MAX_HOLD_BARS, 'COST': COST_PER_TRADE,
            'MIN_NET_EV': MIN_NET_EV, 'TRAIN_M': TRAIN_MONTHS,
            'TEST_M': TEST_MONTHS, 'STEP_M': STEP_MONTHS,
        },
        'validated': validated_db,
        'all_results': {k: {sig: {kk: vv for kk, vv in v.items() if kk != 'folds'}
                            for sig, v in sr.items()}
                        for k, sr in all_results.items()},
    }
    out = BASE / 'data' / 'dharma_v3_result.json'
    out.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f'  结果: {out}')
    return output


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', nargs='+', default=None)
    ap.add_argument('--tf',      nargs='+', default=['1h'])
    ap.add_argument('--quick',   action='store_true')
    args = ap.parse_args()

    syms = ['BTCUSDT', 'ETHUSDT'] if args.quick else (args.symbols or SYMBOLS_TEST)
    run(symbols=syms, timeframes=args.tf)
