#!/usr/bin/env python3
"""
梵天全自动回测引擎 v2.0 — 达摩院联合验证版
2020-01-01 ~ 2026-07-10 | BTC/ETH
评分逻辑: 体制驱动 + SMC代理因子(高低点/支撑测试/压缩)
"""
import json, statistics, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path('data/backtest')

# ── 工具函数 ──────────────────────────────────────────────
def ema_fn(prices, n):
    if len(prices) < n: return prices[-1]
    k = 2/(n+1); e = prices[0]
    for p in prices[1:]: e = e*(1-k)+p*k
    return e

def rsi(closes, n=14):
    if len(closes) < n+1: return 50
    d = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    g=[max(0,x) for x in d[-n:]]; lo=[max(0,-x) for x in d[-n:]]
    ag, al = sum(g)/n, sum(lo)/n
    return 100-100/(1+ag/al) if al > 0 else 50

def find_bar(klines, ts):
    lo, hi = 0, len(klines)-1
    while lo < hi:
        mid = (lo+hi+1)//2
        if klines[mid][0] <= ts: lo = mid
        else: hi = mid-1
    return lo

# ── 体制判断 ──────────────────────────────────────────────
def detect_regime(closes_4h, closes_1d):
    if len(closes_4h) < 50 or len(closes_1d) < 20: return 'UNKNOWN'
    px = closes_4h[-1]
    e20_4h  = ema_fn(closes_4h[-20:], 20)
    e50_4h  = ema_fn(closes_4h[-50:], 50)
    e200_1d = ema_fn(closes_1d[-20:], 20)
    r4 = rsi(closes_4h[-30:])
    ema20_vs_50 = (e20_4h - e50_4h) / e50_4h * 100
    px_vs_200d  = (px - e200_1d) / e200_1d * 100
    px_vs_4h20  = (px - e20_4h) / e20_4h * 100
    if r4 > 55 and ema20_vs_50 > 1.0 and px_vs_200d > 0:
        return 'BULL_TREND' if px_vs_4h20 > 3 and r4 > 65 else 'BULL_EARLY'
    elif r4 < 45 and ema20_vs_50 < -1.0:
        return 'BEAR_TREND' if px_vs_200d < -5 and r4 < 40 else 'BEAR_EARLY'
    elif r4 > 45 and px_vs_200d > -8 and ema20_vs_50 > -0.5:
        return 'BEAR_RECOVERY'
    else:
        return 'CHOP_MID'

# ── 死穴系统 ──────────────────────────────────────────────
DEAD_ZONES = {('BEAR_TREND','LONG'), ('BULL_TREND','SHORT')}
def is_dead_zone(regime, direction): return (regime, direction) in DEAD_ZONES

# ── 体制驱动评分 v2.0 ──────────────────────────────────────
def score_signal(c15, c1h, c4h, highs1h, lows1h, regime, direction):
    """体制驱动评分 — 避免因子矛盾，最高160分"""
    sc = 0
    r15 = rsi(c15); r1 = rsi(c1h); r4 = rsi(c4h)
    px  = c1h[-1]
    e20 = ema_fn(c1h[-20:], 20)
    e50 = ema_fn(c1h[-50:], 50) if len(c1h) >= 50 else e20
    e20_4h = ema_fn(c4h[-20:], 20) if len(c4h) >= 20 else px
    e50_4h = ema_fn(c4h[-50:], 50) if len(c4h) >= 50 else e20_4h

    # SMC代理因子
    near_support = near_resist = tight = broke_high = broke_low = False
    if highs1h and lows1h and len(highs1h) >= 12:
        h24 = max(highs1h[-24:]) if len(highs1h) >= 24 else max(highs1h)
        l24 = min(lows1h[-24:])  if len(lows1h)  >= 24 else min(lows1h)
        h48 = max(highs1h[-48:]) if len(highs1h) >= 48 else max(highs1h)
        l48 = min(lows1h[-48:])  if len(lows1h)  >= 48 else min(lows1h)
        rng48 = (h48 - l48) / px * 100 if h48 > l48 else 10
        near_support = (px - l24) / px * 100 < 1.5
        near_resist  = (h24 - px) / px * 100 < 1.5
        tight        = rng48 < 8.0
        broke_high   = px >= h48 * 0.998
        broke_low    = px <= l48 * 1.002

    if regime in ('BULL_TREND', 'BULL_EARLY', 'BEAR_RECOVERY'):
        if direction == 'LONG':
            # 均线多头排列
            if e20 > e50 and e20_4h > e50_4h: sc += 30
            elif e20 > e50: sc += 18
            # RSI回调到黄金区（不追高）
            if   42 <= r1 <= 58: sc += 30
            elif 38 <= r1 < 42:  sc += 22
            elif r1 < 38:        sc += 14
            elif 58 < r1 <= 68:  sc += 8
            elif r1 > 72:        sc -= 15
            # 15min确认
            if r15 < 45: sc += 12
            elif r15 < 55: sc += 5
            # SMC
            if near_support: sc += 22
            if tight:        sc += 15
            if broke_high:   sc += 8
            # 体制基础分
            sc += 20 if regime == 'BULL_TREND' else 12
            # 4H强势
            if r4 > 55: sc += 8
        else:
            sc = 0  # BULL体制不做空

    elif regime in ('BEAR_TREND', 'BEAR_EARLY'):
        if direction == 'SHORT':
            # 均线空头排列
            if not(e20>e50) and not(e20_4h>e50_4h): sc += 30
            elif not(e20 > e50): sc += 18
            # RSI反弹到中轨做空（不追低）
            if   42 <= r1 <= 58: sc += 30
            elif 58 < r1 <= 68:  sc += 22
            elif r1 > 68:        sc += 14
            elif 38 <= r1 < 42:  sc += 8
            elif r1 < 38:        sc -= 15
            if r15 > 55: sc += 12
            elif r15 > 45: sc += 5
            if near_resist: sc += 22
            if tight:       sc += 15
            if broke_low:   sc += 8
            sc += 20 if regime == 'BEAR_TREND' else 12
            if r4 < 48: sc += 8
        else:
            sc = 0  # BEAR体制不做多

    else:  # CHOP / BEAR_RECOVERY
        if direction == 'LONG':
            if r1 < 40:          sc += 35
            elif r1 < 50:        sc += 20
            elif r1 > 65:        sc -= 15
            if near_support:     sc += 28
            if tight:            sc += 18
            if r15 < 40:         sc += 12
            sc += 15
        else:
            if r1 > 60:          sc += 35
            elif r1 > 50:        sc += 20
            elif r1 < 38:        sc -= 15
            if near_resist:      sc += 28
            if tight:            sc += 18
            if r15 > 60:         sc += 12
            sc += 15

    # 多周期RSI共振
    if direction == 'LONG':
        align = sum([r15 < 52, r1 < 55, r4 < 62])
    else:
        align = sum([r15 > 55, r1 > 55, r4 > 55])
    sc += [0, 5, 10, 15][align]

    # BB压缩加成
    if len(c1h) >= 20:
        ma20 = sum(c1h[-20:])/20
        bbw  = statistics.stdev(c1h[-20:]) * 2 / ma20 * 100
        if   bbw < 1.5: sc += 12
        elif bbw < 2.5: sc += 5

    return max(0, min(sc, 160))


# ── 仓位管理 ──────────────────────────────────────────────
SL_BY_REGIME = {
    'BULL_TREND':2.0,'BULL_EARLY':2.5,'BEAR_TREND':2.0,
    'BEAR_EARLY':2.5,'BEAR_RECOVERY':2.5,'CHOP_MID':2.5,
}
REGIME_SIZE = {
    'BULL_TREND':   {'LONG':1.0,'SHORT':0.15},
    'BULL_EARLY':   {'LONG':0.80,'SHORT':0.20},
    'BEAR_TREND':   {'LONG':0.10,'SHORT':1.0},
    'BEAR_EARLY':   {'LONG':0.35,'SHORT':1.0},
    'BEAR_RECOVERY':{'LONG':1.0,'SHORT':0.30},
    'CHOP_MID':     {'LONG':0.50,'SHORT':0.50},
}

def calc_atr(klines_1h, n=14):
    trs = []
    for i in range(1, min(len(klines_1h), n*2)):
        h=klines_1h[-i][2]; l=klines_1h[-i][3]; pc=klines_1h[-i-1][4]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs[:n])/n if trs else 0


# ── 主回测 ──────────────────────────────────────────────
def run_backtest(symbol, nav_start=10000.0, score_threshold=120,
                 size_pct=0.05, leverage=5):
    t0 = time.time()
    print(f'\n{"="*60}')
    print(f'梵天回测 v2.0 | {symbol} | 门槛={score_threshold}')
    print(f'{"="*60}')

    def load(tf):
        data = json.loads((DATA_DIR/f'{symbol}_{tf}.json').read_text())
        return [(int(k[0]),float(k[1]),float(k[2]),float(k[3]),float(k[4]),float(k[7])) for k in data]

    kl_15m=load('15m'); kl_1h=load('1h'); kl_4h=load('4h'); kl_1d=load('1d')
    print(f'数据: 15m={len(kl_15m)} 1H={len(kl_1h)} 4H={len(kl_4h)} 1D={len(kl_1d)}')

    nav = nav_start; position = None; trades = []
    max_nav = nav_start; max_dd = 0.0
    prev_regime = 'UNKNOWN'
    scan_every = 4  # 每1H扫描一次

    for i in range(240, len(kl_15m), scan_every):
        ts  = kl_15m[i][0]
        px  = kl_15m[i][4]

        # 持仓检查
        if position:
            sl = position['sl']; tp = position['tp1']
            dir_ = position['direction']
            hit_sl = (dir_=='LONG' and px<=sl) or (dir_=='SHORT' and px>=sl)
            hit_tp = (dir_=='LONG' and px>=tp) or (dir_=='SHORT' and px<=tp)
            if hit_sl or hit_tp:
                exit_px = sl if hit_sl else tp
                if dir_ == 'LONG':
                    pnl_pct = (exit_px - position['entry']) / position['entry']
                else:
                    pnl_pct = (position['entry'] - exit_px) / position['entry']
                pnl = pnl_pct * position['qty'] * position['entry'] * leverage
                nav += pnl
                trades.append({
                    'ts':ts,'sym':symbol,'dir':dir_,
                    'entry':position['entry'],'exit':exit_px,
                    'pnl':round(pnl,4),'pnl_pct':round(pnl_pct*100,3),
                    'regime':position['regime'],'score':position['score'],
                    'exit_reason':'SL' if hit_sl else 'TP1'
                })
                position = None

        if nav > max_nav: max_nav = nav
        dd = (max_nav - nav) / max_nav * 100
        if dd > max_dd: max_dd = dd

        if position: continue  # 已持仓不开新仓

        # 体制判断（每4根=1H判断一次，节省时间）
        i1  = find_bar(kl_1h, ts)
        i4  = find_bar(kl_4h, ts)
        id_ = find_bar(kl_1d, ts)
        if i1 < 50 or i4 < 50 or id_ < 20: continue

        c1h = [kl_1h[j][4] for j in range(max(0,i1-99), i1+1)]
        c4h = [kl_4h[j][4] for j in range(max(0,i4-99), i4+1)]
        c1d = [kl_1d[j][4] for j in range(max(0,id_-29), id_+1)]
        c15 = [kl_15m[j][4] for j in range(max(0,i-49), i+1)]
        h1h = [kl_1h[j][2] for j in range(max(0,i1-55), i1+1)]
        l1h = [kl_1h[j][3] for j in range(max(0,i1-55), i1+1)]

        regime = detect_regime(c4h, c1d)
        if regime == 'UNKNOWN': continue

        # 体制切换→强制平仓（已处理）
        if prev_regime != regime and prev_regime != 'UNKNOWN' and position:
            position = None
        prev_regime = regime

        # 候选方向
        if regime in ('BULL_TREND','BULL_EARLY','BEAR_RECOVERY'):
            candidates = ['LONG']
        elif regime in ('BEAR_TREND','BEAR_EARLY'):
            candidates = ['SHORT']
        else:
            candidates = ['LONG','SHORT']

        best_score = 0; best_dir = None
        for direction in candidates:
            if is_dead_zone(regime, direction): continue
            s = score_signal(c15, c1h, c4h, h1h, l1h, regime, direction)
            if s > best_score: best_score=s; best_dir=direction

        if best_score < score_threshold or not best_dir: continue

        # 开单
        kl_1h_raw = [kl_1h[j] for j in range(max(0,i1-20), i1+1)]
        atr_1h = calc_atr(kl_1h_raw)
        sl_base = SL_BY_REGIME.get(regime, 2.5)
        atr_sl  = atr_1h * 1.5 / px * 100 if px > 0 else sl_base
        sl_pct  = min(max(sl_base, atr_sl), 6.0)

        size_mult   = REGIME_SIZE.get(regime, {}).get(best_dir, 0.5)
        actual_size = size_pct * size_mult
        notional    = nav * actual_size * leverage
        if notional < nav * 0.01: continue
        qty = notional / px

        if best_dir == 'LONG':
            sl_price  = px * (1 - sl_pct/100)
            tp1_price = px * (1 + sl_pct * 1.5 / 100)  # RR=1.5
        else:
            sl_price  = px * (1 + sl_pct/100)
            tp1_price = px * (1 - sl_pct * 1.5 / 100)

        position = {
            'direction': best_dir, 'entry': px, 'qty': qty,
            'sl': sl_price, 'tp1': tp1_price,
            'regime': regime, 'score': best_score, 'ts': ts,
        }

    # 强平未平仓
    if position:
        px_f = kl_15m[-1][4]
        pnl_pct = (px_f-position['entry'])/position['entry']
        if position['direction']=='SHORT': pnl_pct = -pnl_pct
        pnl = pnl_pct * position['qty'] * position['entry'] * leverage
        nav += pnl
        trades.append({'ts':kl_15m[-1][0],'sym':symbol,'dir':position['direction'],
                       'entry':position['entry'],'exit':px_f,'pnl':round(pnl,4),
                       'pnl_pct':round(pnl_pct*100,3),'regime':position['regime'],
                       'score':position['score'],'exit_reason':'FINAL'})

    wins = sum(1 for t in trades if t['pnl'] > 0)
    n = len(trades)
    elapsed = time.time() - t0

    print(f'耗时: {elapsed:.1f}s | 交易笔数: {n}')
    print(f'NAV: ${nav_start:,.0f} → ${nav:,.2f} ({(nav-nav_start)/nav_start*100:+.2f}%)')
    print(f'胜率: {wins/n*100:.1f}% ({wins}/{n}) | 最大回撤: {max_dd:.2f}%')
    if n > 0:
        avg_win  = sum(t['pnl'] for t in trades if t['pnl']>0) / max(wins,1)
        avg_loss = sum(t['pnl'] for t in trades if t['pnl']<0) / max(n-wins,1)
        print(f'平均盈利: ${avg_win:.2f} | 平均亏损: ${avg_loss:.2f}')
        # 按体制分类
        by_regime = defaultdict(lambda:{'n':0,'wins':0,'pnl':0.0})
        for t in trades:
            k=f"{t['regime']}_{t['dir']}"
            by_regime[k]['n']+=1
            by_regime[k]['pnl']+=t['pnl']
            if t['pnl']>0: by_regime[k]['wins']+=1
        print('\n体制×方向 统计:')
        for k in sorted(by_regime):
            v=by_regime[k]
            wr=v['wins']/v['n']*100 if v['n']>0 else 0
            print(f'  {k:<30} n={v["n"]:3d} WR={wr:5.1f}% PnL=${v["pnl"]:+,.2f}')

    return {
        'symbol':symbol,'nav_start':nav_start,'nav_final':round(nav,2),
        'total_return':round((nav-nav_start)/nav_start*100,2),
        'max_drawdown':round(max_dd,2),'total_trades':n,'wins':wins,
        'trades':trades,
    }


# ── 执行 ──────────────────────────────────────────────────
for sym in ['BTCUSDT','ETHUSDT']:
    result = run_backtest(sym, nav_start=10000, score_threshold=120)
    (DATA_DIR/f'{sym.lower()}_result_v2.json').write_text(
        json.dumps(result, ensure_ascii=False, default=str))
    print(f'\n结果已保存: {sym.lower()}_result_v2.json')

print('\n=== 全部完成 ===')
