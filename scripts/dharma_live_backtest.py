#!/usr/bin/env python3
"""
达摩院 外测引擎 v1.0 — 无上帝视角多周期回测
===============================================
设计原则：
  - 无上帝视角：每一个信号只能看到当前bar及之前的数据
  - 主战场：15分 + 1小时 产生入场信号
  - 高周期确认：4H 结构 + 1D 体制
  - 出场：基于 ATR 动态止损，非回望未来

梵天宪法规则（完整还原）：
  - 死穴：BEAR_TREND_LONG / BULL_TREND_SHORT
  - 死穴扩展：BEAR_RECOVERY_LONG / BEAR_EARLY_SHORT
  - EMA20_1H 门控：做空时价格必须 < EMA20_1H
  - 体制乘数 v4.0：BEAR_TREND 做空 1.6x | BULL_TREND 做多 1.6x
  - SL 铁律：做空 SL = entry × (1+SL_PCT)，做多 SL = entry × (1-SL_PCT)
  - 分批出场：50%@TP1(RR=1.5) + 50%追踪止损
  - 手续费：0.04% maker × 2 = 0.08% / 笔
"""

import json, math, sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

BASE = Path(__file__).parent.parent
DATA = BASE / 'data' / 'backtest'

# ══════════════════════════════════════════════════════════════
# 工具函数（纯因果序列，无未来数据）
# ══════════════════════════════════════════════════════════════
def ema(prices: list, n: int) -> float:
    if len(prices) < n: return prices[-1] if prices else 0
    k = 2 / (n + 1)
    e = sum(prices[:n]) / n
    for p in prices[n:]: e = e * (1 - k) + p * k
    return e

def rsi(closes: list, n: int = 14) -> float:
    if len(closes) < n + 1: return 50.0
    d = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    g = [max(0, x) for x in d[-n:]]
    l = [max(0, -x) for x in d[-n:]]
    ag, al = sum(g) / n, sum(l) / n
    return 100 - 100 / (1 + ag / al) if al > 0 else 100.0

def atr(highs: list, lows: list, closes: list, n: int = 14) -> float:
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        ))
    if not trs: return closes[-1] * 0.01
    return sum(trs[-n:]) / min(n, len(trs))

def bb_width(closes: list, n: int = 20) -> float:
    if len(closes) < n: return 0.05
    w = closes[-n:]
    mu = sum(w) / n
    std = math.sqrt(sum((x - mu) ** 2 for x in w) / n)
    return std * 2 / mu if mu > 0 else 0.05

def detect_regime_1d(c1d: list) -> str:
    """1D 体制判断（只用日线数据，严格因果）"""
    if len(c1d) < 50: return 'CHOP_MID'
    e20 = ema(c1d[-20:], 20)
    e50 = ema(c1d[-50:], 50)
    r = rsi(c1d[-20:])
    price = c1d[-1]
    if e20 > e50 and price > e20 and r > 55: return 'BULL_TREND'
    if e20 > e50 and r > 50: return 'BULL_EARLY'
    if e20 < e50 and price < e20 and r < 45: return 'BEAR_TREND'
    if e20 < e50 and r < 50: return 'BEAR_EARLY'
    return 'CHOP_MID'

def detect_regime_4h(c4h: list, regime_1d: str) -> str:
    """4H 结构（融合日线体制）"""
    if len(c4h) < 50: return regime_1d
    e20 = ema(c4h[-20:], 20)
    e50 = ema(c4h[-50:], 50)
    r = rsi(c4h[-20:])
    # 日线为 BEAR 时 4H 最多降为 CHOP
    if regime_1d == 'BEAR_TREND':
        if e20 < e50 and r < 50: return 'BEAR_TREND'
        return 'CHOP_MID'
    if regime_1d == 'BULL_TREND':
        if e20 > e50 and r > 50: return 'BULL_TREND'
        return 'CHOP_MID'
    return regime_1d

# ══════════════════════════════════════════════════════════════
# 信号检测器 —— 基于 1H / 15m 主战场
# ══════════════════════════════════════════════════════════════
def gen_signal_1h(c1h: list, h1h: list, l1h: list, v1h: list,
                  regime_4h: str) -> dict | None:
    """1H 信号生成（无未来数据）"""
    if len(c1h) < 50: return None
    rsi_1h = rsi(c1h[-20:])
    e20_1h = ema(c1h[-20:], 20)
    e50_1h = ema(c1h[-50:], 50) if len(c1h) >= 50 else e20_1h
    price = c1h[-1]
    bbw = bb_width(c1h[-20:])
    atr_1h = atr(h1h[-20:], l1h[-20:], c1h[-20:])

    # 成交量比（过去20根均值）
    avg_v = sum(v1h[-21:-1]) / 20 if len(v1h) >= 21 else v1h[-1]
    vol_ratio = v1h[-1] / avg_v if avg_v > 0 else 1.0

    score = 0
    direction = None

    # ── LONG 信号 ──
    if regime_4h in ('BULL_TREND', 'BULL_EARLY', 'CHOP_MID'):
        s = 0
        # RSI 超卖
        if rsi_1h < 30: s += 35
        elif rsi_1h < 40: s += 20
        elif rsi_1h < 50: s += 8
        # EMA 结构
        if price > e20_1h and e20_1h > e50_1h: s += 25
        elif price > e20_1h: s += 12
        # 体制加成
        if regime_4h == 'BULL_TREND': s = int(s * 1.6)
        elif regime_4h == 'CHOP_MID': s = int(s * 0.88)
        # BB 压缩放量
        if bbw < 0.03 and vol_ratio > 1.5: s += 20
        if s > score: score = s; direction = 'LONG'

    # ── SHORT 信号 ──
    if regime_4h in ('BEAR_TREND', 'BEAR_EARLY', 'CHOP_MID'):
        s = 0
        # EMA20 门控：做空时价格必须 < EMA20_1H（梵天新宪法）
        if price >= e20_1h: pass  # 不满足门控
        else:
            if rsi_1h > 70: s += 35
            elif rsi_1h > 60: s += 20
            elif rsi_1h > 50: s += 8
            if price < e20_1h and e20_1h < e50_1h: s += 25
            elif price < e20_1h: s += 12
            if regime_4h == 'BEAR_TREND': s = int(s * 1.6)
            elif regime_4h == 'CHOP_MID': s = int(s * 0.88)
            if bbw < 0.03 and vol_ratio > 1.5: s += 20
        if s > score: score = s; direction = 'SHORT'

    # 信号门槛
    if score < 55 or direction is None: return None

    return {
        'direction': direction,
        'score': score,
        'rsi_1h': rsi_1h,
        'e20_1h': e20_1h,
        'atr_1h': atr_1h,
        'bbw': bbw,
        'vol_ratio': vol_ratio,
        'regime_4h': regime_4h,
        'source': '1H',
    }

def gen_signal_15m(c15m: list, h15m: list, l15m: list, v15m: list,
                   regime_4h: str, e20_1h: float) -> dict | None:
    """15m 精确入场（在 1H 已确认趋势方向的基础上）"""
    if len(c15m) < 50: return None
    rsi_15m = rsi(c15m[-20:])
    e8_15m = ema(c15m[-8:], 8)
    e21_15m = ema(c15m[-21:], 21)
    price = c15m[-1]
    atr_15m = atr(h15m[-20:], l15m[-20:], c15m[-20:])
    bbw_15m = bb_width(c15m[-20:])

    avg_v = sum(v15m[-21:-1]) / 20 if len(v15m) >= 21 else v15m[-1]
    vol_ratio = v15m[-1] / avg_v if avg_v > 0 else 1.0

    score = 0; direction = None

    # LONG：价格 > e8 且 e8 刚穿越 e21 向上（金叉后）
    if regime_4h in ('BULL_TREND', 'BULL_EARLY'):
        s = 0
        golden = e8_15m > e21_15m
        if golden: s += 30
        if rsi_15m < 50 and rsi_15m > 35: s += 25  # RSI 回踩不过热
        if price > e20_1h: s += 20  # 1H 趋势方向确认
        if vol_ratio > 1.3: s += 15
        if bbw_15m < 0.025: s += 10
        if regime_4h == 'BULL_TREND': s = int(s * 1.6)
        if s > score: score = s; direction = 'LONG'

    # SHORT：价格 < e20_1h（1H EMA20 门控）
    if regime_4h in ('BEAR_TREND', 'BEAR_EARLY'):
        s = 0
        if price < e20_1h:  # 门控
            dead = e8_15m < e21_15m
            if dead: s += 30
            if rsi_15m > 50 and rsi_15m < 65: s += 25
            if vol_ratio > 1.3: s += 15
            if bbw_15m < 0.025: s += 10
            if regime_4h == 'BEAR_TREND': s = int(s * 1.6)
        if s > score: score = s; direction = 'SHORT'

    if score < 55 or direction is None: return None

    return {
        'direction': direction,
        'score': score,
        'rsi_15m': rsi_15m,
        'e8_15m': e8_15m,
        'e21_15m': e21_15m,
        'atr_15m': atr_15m,
        'regime_4h': regime_4h,
        'source': '15M',
    }

# ══════════════════════════════════════════════════════════════
# 仓位管理（梵天宪法）
# ══════════════════════════════════════════════════════════════
REGIME_MUL = {
    'BULL_TREND': {'LONG': 1.6, 'SHORT': 0.15},
    'BULL_EARLY': {'LONG': 0.8, 'SHORT': 0.35},
    'CHOP_MID':   {'LONG': 0.5, 'SHORT': 0.88},
    'BEAR_EARLY': {'LONG': 0.35, 'SHORT': 1.2},
    'BEAR_TREND': {'LONG': 0.10, 'SHORT': 1.6},
}
DEAD_ZONES = {('BEAR_TREND', 'LONG'), ('BULL_TREND', 'SHORT'),
              ('BEAR_EARLY', 'SHORT'), ('BULL_EARLY', 'LONG')}

SL_PCT = {
    ('BEAR_TREND', 'SHORT'): 0.020,
    ('CHOP_MID',   'SHORT'): 0.025,
    ('BULL_TREND', 'LONG'):  0.020,
    ('CHOP_MID',   'LONG'):  0.025,
    ('BEAR_EARLY', 'SHORT'): 0.020,
    ('BULL_EARLY', 'LONG'):  0.025,
}
DEFAULT_SL = 0.025

def get_pos_pct(regime: str, direction: str, score: int, nav: float) -> float:
    if (regime, direction) in DEAD_ZONES: return 0.0
    mul = REGIME_MUL.get(regime, {}).get(direction, 0.5)
    base = 0.05  # 5% NAV 基础仓位
    # 评分加成
    if score >= 90: base *= 1.5
    elif score >= 75: base *= 1.2
    pos = base * mul
    return min(pos, 0.10)  # 单笔最大 10%NAV

# ══════════════════════════════════════════════════════════════
# 主回测引擎
# ══════════════════════════════════════════════════════════════
def run_backtest(sym: str, source: str = '1H') -> dict:
    """
    source: '1H' 或 '15M'
    """
    print(f'\n{"="*65}')
    print(f'  达摩院外测 · {sym} · 信号源={source} · 无上帝视角')
    print(f'{"="*65}')

    # 加载数据
    k1d  = json.load(open(DATA / f'{sym}_1d.json'))
    k4h  = json.load(open(DATA / f'{sym}_4h.json'))
    k1h  = json.load(open(DATA / f'{sym}_1h.json'))
    k15m = json.load(open(DATA / f'{sym}_15m.json')) if source == '15M' else None

    FEE = 0.0008  # 0.04% × 2 = 0.08% 往返
    NAV_START = 10000.0
    nav = NAV_START
    LEV = 5.0

    trades = []
    open_pos = None
    skipped = {'dead': 0, 'score_low': 0, 'ema_gate': 0, 'duplicate': 0}

    # 以 1H 为时间轴驱动
    total_1h = len(k1h)

    for i in range(300, total_1h - 1):
        ts_1h = k1h[i][0]

        # ── 对齐其他周期索引（严格只用 <= ts_1h 的 bar）──
        # 4H: 每 4 根 1H 对应 1 根 4H
        i4h = min(int(i * len(k4h) / total_1h), len(k4h) - 2)
        # 1D
        i1d = min(int(i * len(k1d) / total_1h), len(k1d) - 2)
        # 15M: 每 1 根 1H = 4 根 15M
        i15m = min(int(i * 4 * len(k15m) / total_1h), len(k15m) - 2) if k15m else 0

        # 截取历史窗口（无未来）
        c1d  = [float(k[4]) for k in k1d[:i1d+1][-60:]]
        c4h  = [float(k[4]) for k in k4h[:i4h+1][-100:]]
        c1h  = [float(k[4]) for k in k1h[:i+1][-100:]]
        h1h  = [float(k[2]) for k in k1h[:i+1][-100:]]
        l1h  = [float(k[3]) for k in k1h[:i+1][-100:]]
        v1h  = [float(k[5]) for k in k1h[:i+1][-100:]]

        # 体制判断（因果）
        regime_1d = detect_regime_1d(c1d)
        regime_4h = detect_regime_4h(c4h, regime_1d)
        e20_1h    = ema(c1h[-20:], 20) if len(c1h) >= 20 else c1h[-1]

        price = c1h[-1]

        # ── 持仓管理 ──
        if open_pos:
            p = open_pos
            bar_h = float(k1h[i][2])
            bar_l = float(k1h[i][3])

            closed = False; close_price = price; close_reason = 'hold'

            if p['direction'] == 'LONG':
                if bar_l <= p['sl']:
                    close_price = p['sl']; close_reason = 'SL'; closed = True
                elif bar_h >= p['tp1'] and not p['tp1_hit']:
                    p['tp1_hit'] = True
                    # 部分平仓 50%@TP1
                    partial_pnl = (p['tp1'] - p['entry']) / p['entry'] - FEE
                    nav += nav * p['pos_pct'] * 0.5 * LEV * partial_pnl
                    p['pos_pct'] *= 0.5  # 剩余 50%
                    # 移动止损到成本
                    p['sl'] = p['entry']
                # 追踪止损（剩余仓位）
                if p['tp1_hit'] and not closed:
                    trailing = price * 0.985  # 1.5% 追踪
                    p['sl'] = max(p['sl'], trailing)
                    if bar_l <= p['sl']:
                        close_price = p['sl']; close_reason = 'TRAIL'; closed = True
            else:  # SHORT
                if bar_h >= p['sl']:
                    close_price = p['sl']; close_reason = 'SL'; closed = True
                elif bar_l <= p['tp1'] and not p['tp1_hit']:
                    p['tp1_hit'] = True
                    partial_pnl = (p['entry'] - p['tp1']) / p['entry'] - FEE
                    nav += nav * p['pos_pct'] * 0.5 * LEV * partial_pnl
                    p['pos_pct'] *= 0.5
                    p['sl'] = p['entry']
                if p['tp1_hit'] and not closed:
                    trailing = price * 1.015
                    p['sl'] = min(p['sl'], trailing)
                    if bar_h >= p['sl']:
                        close_price = p['sl']; close_reason = 'TRAIL'; closed = True

            # 超时平仓（持仓 > 48H = 48根 1H bar）
            if not closed and (i - p['open_i']) > 48:
                close_price = price; close_reason = 'TIMEOUT'; closed = True

            if closed:
                if p['direction'] == 'LONG':
                    final_pnl = (close_price - p['entry']) / p['entry'] - FEE
                else:
                    final_pnl = (p['entry'] - close_price) / p['entry'] - FEE

                nav_delta = nav * p['pos_pct'] * LEV * final_pnl
                nav = max(nav + nav_delta, nav * 0.01)  # 防爆仓归零

                trades.append({
                    'ts': ts_1h,
                    'direction': p['direction'],
                    'entry': p['entry'],
                    'close': close_price,
                    'pnl_pct': final_pnl * 100,
                    'reason': close_reason,
                    'regime': p['regime'],
                    'score': p['score'],
                    'source': p['source'],
                    'nav_after': nav,
                })
                open_pos = None

        # ── 信号生成（无持仓时） ──
        if open_pos is not None:
            continue

        sig = None
        if source == '1H':
            sig = gen_signal_1h(c1h, h1h, l1h, v1h, regime_4h)
        elif source == '15M' and k15m:
            c15m = [float(k[4]) for k in k15m[:i15m+1][-80:]]
            h15m = [float(k[2]) for k in k15m[:i15m+1][-80:]]
            l15m = [float(k[3]) for k in k15m[:i15m+1][-80:]]
            v15m = [float(k[5]) for k in k15m[:i15m+1][-80:]]
            sig_15 = gen_signal_15m(c15m, h15m, l15m, v15m, regime_4h, e20_1h)
            sig_1h = gen_signal_1h(c1h, h1h, l1h, v1h, regime_4h)
            # 两个信号方向一致才入场（过滤）
            if sig_15 and sig_1h and sig_15['direction'] == sig_1h['direction']:
                sig = sig_15
                sig['score'] = max(sig_15['score'], sig_1h['score'])
            elif sig_15: sig = sig_15

        if sig is None: continue

        direction = sig['direction']
        regime = sig.get('regime_4h', regime_4h)

        # 死穴过滤
        if (regime, direction) in DEAD_ZONES:
            skipped['dead'] += 1; continue

        pos_pct = get_pos_pct(regime, direction, sig['score'], nav)
        if pos_pct == 0: skipped['dead'] += 1; continue

        # 止损计算（铁律）
        atr_val = atr(h1h[-20:], l1h[-20:], c1h[-20:])
        sl_pct = SL_PCT.get((regime, direction), DEFAULT_SL)
        # 确保 SL >= 1.5×ATR
        min_sl = 1.5 * atr_val / price if price > 0 else sl_pct
        sl_pct = max(sl_pct, min_sl)

        if direction == 'LONG':
            sl    = price * (1 - sl_pct)
            tp1   = price * (1 + sl_pct * 1.5)
            tp2   = price * (1 + sl_pct * 3.0)
        else:
            sl    = price * (1 + sl_pct)
            tp1   = price * (1 - sl_pct * 1.5)
            tp2   = price * (1 - sl_pct * 3.0)

        open_pos = {
            'direction': direction,
            'entry': price,
            'sl': sl, 'tp1': tp1, 'tp2': tp2,
            'tp1_hit': False,
            'pos_pct': pos_pct,
            'regime': regime,
            'score': sig['score'],
            'source': sig.get('source', source),
            'open_i': i,
        }

    # ══ 统计 ══
    if not trades:
        print('  无交易记录')
        return {}

    n = len(trades)
    wins = sum(1 for t in trades if t['pnl_pct'] > 0)
    wr = wins / n * 100
    avg_win  = sum(t['pnl_pct'] for t in trades if t['pnl_pct'] > 0) / max(wins, 1)
    avg_loss = sum(t['pnl_pct'] for t in trades if t['pnl_pct'] <= 0) / max(n - wins, 1)
    total_ret = (nav - NAV_START) / NAV_START * 100

    # 最大回撤
    peak = NAV_START
    max_dd = 0
    for t in trades:
        peak = max(peak, t['nav_after'])
        dd = (peak - t['nav_after']) / peak * 100
        max_dd = max(max_dd, dd)

    # 体制分层
    regime_stats = defaultdict(lambda: [0, 0, 0.0])
    for t in trades:
        k = f"{t['regime']}_{t['direction']}"
        regime_stats[k][0] += 1 if t['pnl_pct'] > 0 else 0
        regime_stats[k][1] += 1
        regime_stats[k][2] += t['pnl_pct']

    # 平仓原因统计
    reason_stats = defaultdict(int)
    for t in trades: reason_stats[t['reason']] += 1

    # 信号源统计
    source_stats = defaultdict(lambda: [0, 0])
    for t in trades:
        src = t.get('source', source)
        source_stats[src][0] += 1 if t['pnl_pct'] > 0 else 0
        source_stats[src][1] += 1

    print(f'\n  ✅ 总交易: {n} 笔 | 胜率: {wr:.1f}% | 总收益: {total_ret:+.1f}%')
    print(f'  MaxDD: {max_dd:.1f}% | 平均盈利: {avg_win:+.2f}% | 平均亏损: {avg_loss:+.2f}%')
    print(f'  Profit Factor: {abs(avg_win)/abs(avg_loss):.2f} | 净收益: {nav-NAV_START:+.0f}U')
    dead_cnt=skipped["dead"]; low_cnt=skipped["score_low"]; print(f"  跳过: 死穴={dead_cnt} 评分低={low_cnt}")

    print(f'\n  ── 体制×方向 分层 ──')
    for k, (w, total, pnl) in sorted(regime_stats.items(), key=lambda x:-x[1][0]/max(1,x[1][1])):
        if total < 3: continue
        wr_k = w / total * 100
        ev = pnl / total
        flag = '✅' if wr_k >= 52 else ('⚠️' if wr_k >= 45 else '❌')
        print(f'    {k:25s}: WR={wr_k:.1f}% n={total:4d} EV={ev:+.2f}%/笔 {flag}')

    print(f'\n  ── 平仓原因 ──')
    for reason, cnt in sorted(reason_stats.items(), key=lambda x:-x[1]):
        print(f'    {reason:12s}: {cnt:4d} 笔 ({cnt/n*100:.1f}%)')

    print(f'\n  ── 信号源 ──')
    for src, (w, t) in source_stats.items():
        print(f'    {src:8s}: WR={w/max(1,t)*100:.1f}% n={t}')

    return {
        'sym': sym, 'source': source, 'n': n, 'wr': wr,
        'total_ret': total_ret, 'max_dd': max_dd,
        'avg_win': avg_win, 'avg_loss': avg_loss,
        'regime_stats': dict(regime_stats),
        'nav_final': nav,
    }

# ══════════════════════════════════════════════════════════════
# 运行
# ══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    results = {}
    for sym in ['BTCUSDT', 'ETHUSDT']:
        for src in ['1H', '15M']:
            r = run_backtest(sym, src)
            results[f'{sym}_{src}'] = r

    print('\n' + '='*65)
    print('  达摩院外测汇总对比')
    print('='*65)
    print(f'  {"标的+信号源":20s} | {"n":5s} | {"WR":6s} | {"总收益":8s} | {"MaxDD":7s} | {"PF":5s}')
    print('  ' + '-'*60)
    for key, r in results.items():
        if not r: continue
        pf = abs(r.get('avg_win', 0)) / max(abs(r.get('avg_loss', 0.001)), 0.001)
        print(f'  {key:20s} | {r["n"]:5d} | {r["wr"]:5.1f}% | {r["total_ret"]:+6.1f}% | {r["max_dd"]:5.1f}% | {pf:.2f}')
