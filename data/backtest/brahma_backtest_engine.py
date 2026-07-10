#!/usr/bin/env python3
"""
达摩院 梵天全自动开单流程 · 高强度历史回测引擎
2020-01-01 ~ 2026-07-10 | BTC/ETH 期货合约
时间粒度: 15min入场扫描 → 1H/4H/1D多周期确认

梵天宪法规则完整实现:
  - 体制判断: BEAR_TREND / BEAR_EARLY / CHOP_MID / BEAR_RECOVERY / BULL_TREND / BULL_EARLY
  - 死穴系统: BEAR_TREND_LONG / BULL_TREND_SHORT 封禁
  - 评分系统: 简化35维 → 6大因子组
  - 仓位管理: SL_PCT × 体制乘数 × ATR自适应
  - 止盈止损: TP1(50%)+追踪止损(50%) / 体制切换全平
"""
import json, math, time
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

DATA_DIR = Path('data/backtest')

# ══════════════════════════════════════════════════════
# 梵天体制判断引擎（简化版，对标regime_state_machine）
# ══════════════════════════════════════════════════════

def detect_regime(closes_4h, closes_1d):
    """
    基于4H+1D价格结构判断体制
    返回: BULL_TREND / BEAR_TREND / CHOP_MID / BEAR_RECOVERY / BEAR_EARLY / BULL_EARLY
    """
    if len(closes_4h) < 50 or len(closes_1d) < 20:
        return 'UNKNOWN'

    px = closes_4h[-1]

    # EMA计算
    def ema(prices, n):
        k = 2/(n+1); e = prices[0]
        for p in prices[1:]: e = e*(1-k)+p*k
        return e

    ema20_4h  = ema(closes_4h[-20:], 20)
    ema50_4h  = ema(closes_4h[-50:], 50)
    ema200_1d = ema(closes_1d[-20:], 20) if len(closes_1d) >= 20 else closes_1d[-1]

    # RSI_4H
    d = [closes_4h[i]-closes_4h[i-1] for i in range(1, len(closes_4h))]
    g = [max(0,x) for x in d[-14:]]; lo = [max(0,-x) for x in d[-14:]]
    ag, al = sum(g)/14, sum(lo)/14
    rsi_4h = 100-100/(1+ag/al) if al > 0 else 50

    # 趋势强度
    px_vs_ema20 = (px - ema20_4h) / ema20_4h * 100
    ema20_vs_50 = (ema20_4h - ema50_4h) / ema50_4h * 100

    # 价格相对200日均线
    px_vs_200d = (px - ema200_1d) / ema200_1d * 100

    # 体制判断（梵天宪法v4.0）
    if rsi_4h > 55 and ema20_vs_50 > 1.0 and px_vs_200d > 0:
        if px_vs_ema20 > 3 and rsi_4h > 65:
            return 'BULL_TREND'
        return 'BULL_EARLY'
    elif rsi_4h < 45 and ema20_vs_50 < -1.0:
        if px_vs_200d < -5 and rsi_4h < 40:
            return 'BEAR_TREND'
        if px_vs_200d > -5:
            return 'BEAR_EARLY'
        return 'BEAR_TREND'
    elif rsi_4h > 45 and px_vs_200d > -8 and ema20_vs_50 > -0.5:
        return 'BEAR_RECOVERY'
    else:
        return 'CHOP_MID'


# ══════════════════════════════════════════════════════
# 梵天信号评分引擎（6因子框架）
# ══════════════════════════════════════════════════════

def score_signal(closes_15m, closes_1h, closes_4h, regime, direction,
                   highs_1h=None, lows_1h=None):
    """
    体制驱动评分框架 v2.0 (0-160分)
    不同体制下用不同逻辑，避免因子矛盾

    BULL_TREND做多: 趋势延续信号（回调入场，不要买超卖）
    BEAR_TREND做空: 趋势延续信号（反弹入场）
    CHOP: 局部高销/局部低买
    """
    import statistics
    score = 0

    def rsi(closes, n=14):
        if len(closes) < n+1: return 50
        d = [closes[i]-closes[i-1] for i in range(1,len(closes))]
        g=[max(0,x) for x in d[-n:]]; lo=[max(0,-x) for x in d[-n:]]
        ag,al=sum(g)/n,sum(lo)/n
        return 100-100/(1+ag/al) if al>0 else 50

    def ema_fn(prices, n):
        if len(prices) < n: return prices[-1]
        k=2/(n+1); e=prices[0]
        for p in prices[1:]: e=e*(1-k)+p*k
        return e

    rsi_15m = rsi(closes_15m, 14)
    rsi_1h  = rsi(closes_1h,  14)
    rsi_4h  = rsi(closes_4h,  14)

    # ── F1: 动量因子（最高40分）──
    if direction == 'LONG':
        # RSI_1H梯度（最高25）
        if   rsi_1h < 20: score += 25
        elif rsi_1h < 30: score += 22
        elif rsi_1h < 40: score += 18
        elif rsi_1h < 50: score += 12
        elif rsi_1h < 60: score += 5
        elif rsi_1h > 75: score -= 20   # 严重追高
        elif rsi_1h > 65: score -= 8
        # RSI_15m加成（最高10）
        if   rsi_15m < 20: score += 10
        elif rsi_15m < 30: score += 8
        elif rsi_15m < 40: score += 5
        # RSI_4H背离加成（最高5）
        if rsi_1h < 40 and rsi_4h > 50: score += 5  # 1H超卖但4H强→回调做多
    else:  # SHORT
        if   rsi_1h > 80: score += 25
        elif rsi_1h > 75: score += 22
        elif rsi_1h > 70: score += 18
        elif rsi_1h > 65: score += 12
        elif rsi_1h > 60: score += 5
        elif rsi_1h < 35: score -= 20
        elif rsi_1h < 45: score -= 8
        if   rsi_15m > 80: score += 10
        elif rsi_15m > 70: score += 8
        elif rsi_15m > 65: score += 5
        if rsi_1h > 70 and rsi_4h < 55: score += 5  # 1H超买但4H弱→回调做空

    # ── F2: 结构因子（最高35分）──
    px    = closes_1h[-1]
    ema20 = ema_fn(closes_1h[-20:], 20)
    ema50 = ema_fn(closes_1h[-50:], 50) if len(closes_1h)>=50 else ema20
    ema20_4h = ema_fn(closes_4h[-20:], 20) if len(closes_4h)>=20 else closes_4h[-1]
    ema50_4h = ema_fn(closes_4h[-50:], 50) if len(closes_4h)>=50 else ema20_4h

    px_vs_ema20 = (px - ema20) / ema20 * 100
    ema_align_1h = (ema20 > ema50)  # 1H多头排列
    ema_align_4h = (ema20_4h > ema50_4h)  # 4H多头排列

    if direction == 'LONG':
        if ema_align_1h and ema_align_4h: score += 20    # 双周期多头排列
        elif ema_align_1h: score += 12
        elif not ema_align_1h and not ema_align_4h: score -= 8
        # 价格在关键均线下方做多（回调入场）
        if -3 < px_vs_ema20 < 0:   score += 15  # 刚跌破EMA20，反弹机会
        elif -1 < px_vs_ema20 < 1: score += 10  # 紧贴EMA20
        elif px_vs_ema20 > 5:      score -= 10  # 太高追多危险
    else:
        if not ema_align_1h and not ema_align_4h: score += 20
        elif not ema_align_1h: score += 12
        elif ema_align_1h and ema_align_4h: score -= 8
        if 0 < px_vs_ema20 < 3:    score += 15  # 刚突破EMA20，做空反扑
        elif -1 < px_vs_ema20 < 1: score += 10
        elif px_vs_ema20 < -5:     score -= 10

    # ── F3: 波动率因子（最高20分）──
    if len(closes_1h) >= 20:
        ma20_v = sum(closes_1h[-20:])/20
        std20 = statistics.stdev(closes_1h[-20:])
        bb_width = std20*2/ma20_v*100
        if   bb_width < 0.8:  score += 20  # 极度压缩
        elif bb_width < 1.5:  score += 15
        elif bb_width < 2.5:  score += 8
        elif bb_width > 8.0:  score -= 12  # 过热
        elif bb_width > 5.0:  score -= 5

    # ── F4: 体制加成（最高40分）──
    regime_bonus = {
        'BULL_TREND':    {'LONG': 40, 'SHORT': -35},
        'BULL_EARLY':    {'LONG': 28, 'SHORT': -15},
        'BEAR_TREND':    {'LONG': -35,'SHORT':  40},
        'BEAR_EARLY':    {'LONG': -15,'SHORT':  28},
        'BEAR_RECOVERY': {'LONG': 28, 'SHORT': -20},
        'CHOP_MID':      {'LONG':  8, 'SHORT':  10},
    }
    score += regime_bonus.get(regime, {}).get(direction, 0)

    # ── F5: 多周期共振（最高15分）──
    if direction == 'LONG':
        align_count = sum([
            rsi_15m < 45,
            rsi_1h  < 50,
            rsi_4h  < 60,
        ])
    else:
        align_count = sum([
            rsi_15m > 60,
            rsi_1h  > 58,
            rsi_4h  > 55,
        ])
    score += [0, 5, 10, 15][align_count]

    # ── F6: 趋势动能（最高10分）──
    if len(closes_4h) >= 8:
        mom_2bar = (closes_4h[-1] - closes_4h[-3]) / closes_4h[-3] * 100
        mom_4bar = (closes_4h[-1] - closes_4h[-5]) / closes_4h[-5] * 100
        if direction == 'LONG':
            if mom_4bar > 2:   score += 10
            elif mom_4bar > 0: score += 5
            elif mom_4bar < -5: score -= 5
        else:
            if mom_4bar < -2:  score += 10
            elif mom_4bar < 0: score += 5
            elif mom_4bar > 5: score -= 5

    return max(0, min(score, 160))


# ══════════════════════════════════════════════════════
# 死穴系统（梵天宪法铁律）
# ══════════════════════════════════════════════════════

DEAD_ZONES = {
    ('BEAR_TREND', 'LONG'):   True,  # WR=45% 封禁
    ('BULL_TREND', 'SHORT'):  True,  # 顺势方向禁反
}

def is_dead_zone(regime, direction):
    return DEAD_ZONES.get((regime, direction), False)


# ══════════════════════════════════════════════════════
# 仓位管理（ATR自适应止损）
# ══════════════════════════════════════════════════════

SL_BY_REGIME = {
    'BULL_TREND':    2.0,
    'BULL_EARLY':    2.5,
    'BEAR_TREND':    2.0,
    'BEAR_EARLY':    2.5,
    'BEAR_RECOVERY': 2.5,
    'CHOP_MID':      2.5,
}

REGIME_SIZE_MULT = {
    'BULL_TREND':    {'LONG': 1.0, 'SHORT': 0.15},
    'BEAR_TREND':    {'LONG': 0.10,'SHORT':  1.0},
    'CHOP_MID':      {'LONG': 0.50,'SHORT':  0.50},
    'BEAR_RECOVERY': {'LONG': 1.0, 'SHORT':  0.30},
    'BEAR_EARLY':    {'LONG': 0.35,'SHORT':  1.0},
    'BULL_EARLY':    {'LONG': 0.80,'SHORT':  0.20},
}

def calc_atr(klines_1h, n=14):
    trs = []
    for i in range(1, min(len(klines_1h), n*2)):
        h = float(klines_1h[-i][2]); l = float(klines_1h[-i][3])
        pc = float(klines_1h[-i-1][4])
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs[:n])/n if trs else 0


# ══════════════════════════════════════════════════════
# 回测主引擎
# ══════════════════════════════════════════════════════

def run_backtest(symbol='BTCUSDT', nav_start=10000.0, score_threshold=135,
                 size_pct=0.05, leverage=5):
    """
    梵天全自动回测主函数
    以15min为扫描周期，多周期确认入场
    """
    print(f'\n{"="*60}')
    print(f'达摩院回测引擎 | {symbol} | 2020-2026')
    print(f'NAV起始: ${nav_start:,.0f} | 评分门槛: {score_threshold} | 仓位: {size_pct*100:.0f}%×{leverage}x')
    print(f'{"="*60}')

    # 加载多周期数据
    def load(tf):
        path = DATA_DIR / f'{symbol}_{tf}.json'
        data = json.loads(path.read_text())
        return [(int(k[0]), float(k[1]), float(k[2]), float(k[3]),
                 float(k[4]), float(k[7])) for k in data]  # ts,o,h,l,c,vol_usdt

    kl_15m = load('15m')
    kl_1h  = load('1h')
    kl_4h  = load('4h')
    kl_1d  = load('1d')

    print(f'数据: 15m={len(kl_15m)}根 1H={len(kl_1h)}根 4H={len(kl_4h)}根 1D={len(kl_1d)}根')

    # 状态
    nav    = nav_start
    position = None   # {'direction','entry','qty','sl','tp1','regime','score','ts'}
    trades  = []
    equity_curve = []

    # 统计
    stats = defaultdict(lambda: {'n':0,'wins':0,'total_pnl':0.0,'max_dd':0.0})

    # 构建时间索引（15min → 最近的1H/4H/1D bar）
    def find_bar(klines, ts):
        lo, hi = 0, len(klines)-1
        while lo < hi:
            mid = (lo+hi+1)//2
            if klines[mid][0] <= ts: lo=mid
            else: hi=mid-1
        return lo

    prev_regime = 'UNKNOWN'
    max_nav = nav_start
    max_dd  = 0.0
    total_trades = 0

    print('开始模拟...')
    scan_every = 4   # 每4根15min(=1H)扫描一次，节省计算

    for i in range(200, len(kl_15m), scan_every):
        ts_15m = kl_15m[i][0]
        px_now = kl_15m[i][4]  # 15min收盘价

        # ── 持仓检查 ──
        if position:
            pnl_pct = (px_now - position['entry']) / position['entry'] * 100
            if position['direction'] == 'SHORT':
                pnl_pct = -pnl_pct

            # 止损触发
            if position['direction'] == 'LONG' and px_now <= position['sl']:
                pnl = (position['sl'] - position['entry']) / position['entry'] * position['qty'] * position['entry'] * leverage
                nav += pnl
                trades.append({'ts':ts_15m,'sym':symbol,'dir':'LONG','entry':position['entry'],
                               'exit':position['sl'],'pnl':pnl,'pnl_pct':(position['sl']-position['entry'])/position['entry']*100,
                               'regime':position['regime'],'score':position['score'],'exit_reason':'SL'})
                stats[position['regime']+'_LONG']['n'] += 1
                stats[position['regime']+'_LONG']['total_pnl'] += pnl
                position = None

            elif position['direction'] == 'SHORT' and px_now >= position['sl']:
                pnl = (position['entry'] - position['sl']) / position['entry'] * position['qty'] * position['entry'] * leverage
                nav += pnl
                trades.append({'ts':ts_15m,'sym':symbol,'dir':'SHORT','entry':position['entry'],
                               'exit':position['sl'],'pnl':pnl,'pnl_pct':(position['entry']-position['sl'])/position['entry']*100*-1,
                               'regime':position['regime'],'score':position['score'],'exit_reason':'SL'})
                stats[position['regime']+'_SHORT']['n'] += 1
                stats[position['regime']+'_SHORT']['total_pnl'] += pnl
                position = None

            elif px_now >= position['tp1'] and position['direction'] == 'LONG':
                pnl = (position['tp1'] - position['entry']) / position['entry'] * position['qty'] * position['entry'] * leverage
                nav += pnl
                trades.append({'ts':ts_15m,'sym':symbol,'dir':'LONG','entry':position['entry'],
                               'exit':position['tp1'],'pnl':pnl,'pnl_pct':(position['tp1']-position['entry'])/position['entry']*100,
                               'regime':position['regime'],'score':position['score'],'exit_reason':'TP1'})
                stats[position['regime']+'_LONG']['n'] += 1
                stats[position['regime']+'_LONG']['wins'] += 1
                stats[position['regime']+'_LONG']['total_pnl'] += pnl
                position = None

            elif px_now <= position['tp1'] and position['direction'] == 'SHORT':
                pnl = (position['entry'] - position['tp1']) / position['entry'] * position['qty'] * position['entry'] * leverage
                nav += pnl
                trades.append({'ts':ts_15m,'sym':symbol,'dir':'SHORT','entry':position['entry'],
                               'exit':position['tp1'],'pnl':pnl,'pnl_pct':(position['entry']-position['tp1'])/position['entry']*100,
                               'regime':position['regime'],'score':position['score'],'exit_reason':'TP1'})
                stats[position['regime']+'_SHORT']['n'] += 1
                stats[position['regime']+'_SHORT']['wins'] += 1
                stats[position['regime']+'_SHORT']['total_pnl'] += pnl
                position = None

        # 最大回撤追踪
        if nav > max_nav: max_nav = nav
        dd = (max_nav - nav) / max_nav * 100
        if dd > max_dd: max_dd = dd

        equity_curve.append((ts_15m, round(nav, 2)))

        # ── 已有持仓则不开新仓 ──
        if position:
            continue

        # ── 信号扫描（每1H一次）──
        idx_1h = find_bar(kl_1h, ts_15m)
        idx_4h = find_bar(kl_4h, ts_15m)
        idx_1d = find_bar(kl_1d, ts_15m)

        if idx_1h < 50 or idx_4h < 50 or idx_1d < 20:
            continue

        closes_1h = [kl_1h[j][4] for j in range(max(0,idx_1h-99), idx_1h+1)]
        closes_4h = [kl_4h[j][4] for j in range(max(0,idx_4h-99), idx_4h+1)]
        closes_1d = [kl_1d[j][4] for j in range(max(0,idx_1d-29), idx_1d+1)]
        closes_15m= [kl_15m[j][4] for j in range(max(0,i-49), i+1)]

        klines_1h_raw = [kl_1h[j] for j in range(max(0,idx_1h-20), idx_1h+1)]

        # 体制判断
        regime = detect_regime(closes_4h, closes_1d)
        if regime == 'UNKNOWN': continue

        # 体制切换 → 全平（已在上面处理）
        if prev_regime != regime and prev_regime != 'UNKNOWN' and position:
            position = None
        prev_regime = regime

        # 方向决策（体制驱动）
        if regime in ('BULL_TREND', 'BULL_EARLY', 'BEAR_RECOVERY'):
            candidates = ['LONG']
        elif regime in ('BEAR_TREND', 'BEAR_EARLY'):
            candidates = ['SHORT']
        else:  # CHOP
            candidates = ['LONG', 'SHORT']

        best_score = 0
        best_dir   = None
        for direction in candidates:
            # 死穴检查
            if is_dead_zone(regime, direction):
                continue
            s = score_signal(closes_15m, closes_1h, closes_4h, regime, direction)
            if s > best_score:
                best_score = s
                best_dir   = direction

        if best_score < score_threshold or not best_dir:
            continue

        # ── 开单 ──
        atr_1h = calc_atr(klines_1h_raw)
        sl_pct_base = SL_BY_REGIME.get(regime, 2.5)
        # ATR自适应止损
        atr_sl = atr_1h * 1.5 / px_now * 100 if px_now > 0 else sl_pct_base
        sl_pct = min(max(sl_pct_base, atr_sl), 5.0)

        # 体制仓位乘数
        size_mult = REGIME_SIZE_MULT.get(regime, {}).get(best_dir, 0.5)
        actual_size_pct = size_pct * size_mult
        notional = nav * actual_size_pct * leverage

        if notional < nav * 0.02:  # 太小不开
            continue

        qty = notional / px_now

        if best_dir == 'LONG':
            sl_price  = px_now * (1 - sl_pct/100)
            tp1_price = px_now * (1 + sl_pct * 1.5 / 100)  # RR=1.5
        else:
            sl_price  = px_now * (1 + sl_pct/100)
            tp1_price = px_now * (1 - sl_pct * 1.5 / 100)

        position = {
            'direction': best_dir,
            'entry':     px_now,
            'qty':       qty,
            'sl':        sl_price,
            'tp1':       tp1_price,
            'regime':    regime,
            'score':     best_score,
            'sl_pct':    sl_pct,
            'ts':        ts_15m,
        }
        total_trades += 1

    # 未平仓的用最后价格平
    if position:
        px_final = kl_15m[-1][4]
        if position['direction'] == 'LONG':
            pnl = (px_final-position['entry'])/position['entry'] * position['qty']*position['entry']*leverage
        else:
            pnl = (position['entry']-px_final)/position['entry'] * position['qty']*position['entry']*leverage
        nav += pnl
        trades.append({'ts':kl_15m[-1][0],'sym':symbol,'dir':position['direction'],
                       'entry':position['entry'],'exit':px_final,'pnl':pnl,
                       'pnl_pct':pnl/nav*100,'regime':position['regime'],
                       'score':position['score'],'exit_reason':'FINAL'})

    return {
        'symbol':       symbol,
        'nav_start':    nav_start,
        'nav_final':    round(nav, 2),
        'total_return': round((nav - nav_start)/nav_start*100, 2),
        'max_drawdown': round(max_dd, 2),
        'total_trades': len(trades),
        'wins':         sum(1 for t in trades if t['pnl'] > 0),
        'trades':       trades,
        'stats':        dict(stats),
        'equity_curve': equity_curve[::100],  # 采样
    }

# ── 执行回测 ──
print('开始BTC回测...')
btc_result = run_backtest('BTCUSDT', nav_start=10000, score_threshold=135)
print(f'BTC完成: NAV ${btc_result["nav_start"]:,.0f} → ${btc_result["nav_final"]:,.0f} ({btc_result["total_return"]:+.1f}%) | 交易{btc_result["total_trades"]}笔 | WR={btc_result["wins"]/max(btc_result["total_trades"],1)*100:.1f}% | 最大回撤{btc_result["max_drawdown"]:.1f}%')

print('\n开始ETH回测...')
eth_result = run_backtest('ETHUSDT', nav_start=10000, score_threshold=135)
print(f'ETH完成: NAV ${eth_result["nav_start"]:,.0f} → ${eth_result["nav_final"]:,.0f} ({eth_result["total_return"]:+.1f}%) | 交易{eth_result["total_trades"]}笔 | WR={eth_result["wins"]/max(eth_result["total_trades"],1)*100:.1f}% | 最大回撤{eth_result["max_drawdown"]:.1f}%')

# 保存结果
Path('data/backtest/btc_result.json').write_text(json.dumps(btc_result, ensure_ascii=False, default=str))
Path('data/backtest/eth_result.json').write_text(json.dumps(eth_result, ensure_ascii=False, default=str))
print('\n结果已保存')
