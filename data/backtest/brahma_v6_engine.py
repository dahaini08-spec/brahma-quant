#!/usr/bin/env python3
"""
梵天达摩院 v6.0 — 全落地回测引擎
设计院 × 达摩院 × 六方联合 | 2026-07-11

核心升级:
  1. 三层仓位架构: 15m快攻(2%NAV) + 1H主力(5%NAV) + 4H趋势(8%NAV)
  2. 死穴升级为能力门槛: 高分解锁而非永久封禁
  3. CHOP三战术: 区间狙击/压缩爆发/均值回归
  4. 仓位梯度: score分层×凯利公式×体制乘数
  5. 月历偏差: 季节性因子纳入评分
  6. 全成本: 手续费0.1%/笔 + 分批止盈50%@TP1+50%@TP2
"""
import json, statistics, time, datetime
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent

# ═══════════════════════════════════════════════════════════════
# 基础工具函数
# ═══════════════════════════════════════════════════════════════
def ema_fn(p, n):
    if len(p) < n: return p[-1]
    k = 2/(n+1); e = p[0]
    for x in p[1:]: e = e*(1-k) + x*k
    return e

def rsi_fn(c, n=14):
    if len(c) < n+1: return 50
    d = [c[i]-c[i-1] for i in range(1, len(c))]
    g = [max(0,x) for x in d[-n:]]; lo = [max(0,-x) for x in d[-n:]]
    ag, al = sum(g)/n, sum(lo)/n
    return 100 - 100/(1+ag/al) if al > 0 else 50

def atr_fn(kl, n=14):
    tr = []
    for i in range(1, min(len(kl), n*2)):
        h=kl[-i][2]; l=kl[-i][3]; pc=kl[-i-1][4]
        tr.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(tr[:n])/n if tr else 0

def bb_width(closes, n=20):
    if len(closes) < n: return 3.0
    ma = sum(closes[-n:])/n
    std = statistics.stdev(closes[-n:])
    return std*2/ma*100 if ma > 0 else 3.0

def fb(kl, ts):
    lo, hi = 0, len(kl)-1
    while lo < hi:
        mid = (lo+hi+1)//2
        if kl[mid][0] <= ts: lo = mid
        else: hi = mid-1
    return lo

# ═══════════════════════════════════════════════════════════════
# 体制判断引擎（对标regime_state_machine）
# ═══════════════════════════════════════════════════════════════
def detect_regime(c4h, c1d):
    if len(c4h) < 50 or len(c1d) < 20: return 'UNKNOWN'
    px = c4h[-1]
    e20 = ema_fn(c4h[-20:], 20); e50 = ema_fn(c4h[-50:], 50)
    ed  = ema_fn(c1d[-20:], 20)
    r4  = rsi_fn(c4h[-30:])
    ev  = (e20-e50)/e50*100; pd = (px-ed)/ed*100; p4 = (px-e20)/e20*100
    if r4>55 and ev>1.0 and pd>0:
        return 'BULL_TREND' if p4>3 and r4>65 else 'BULL_EARLY'
    elif r4<45 and ev<-1.0:
        return 'BEAR_TREND' if pd<-5 and r4<40 else 'BEAR_EARLY'
    elif r4>45 and pd>-8 and ev>-0.5:
        return 'BEAR_RECOVERY'
    else:
        return 'CHOP_MID'

# ═══════════════════════════════════════════════════════════════
# v6.0 评分引擎 — 体制驱动 + 能力门槛（非封禁）
# ═══════════════════════════════════════════════════════════════
def score_v6(c15, c1h, c4h, h1h, l1h, regime, direction, tf_layer='1h'):
    """
    v6.0三层评分:
      快攻(15m): 侧重RSI极值 + BB压缩，门槛低，快进快出
      主力(1H):  完整6因子，平衡质量
      趋势(4H):  侧重多周期共振 + 结构强度，门槛高
    """
    sc = 0
    r15 = rsi_fn(c15); r1 = rsi_fn(c1h); r4 = rsi_fn(c4h)
    px  = c1h[-1]
    e20 = ema_fn(c1h[-20:], 20)
    e50 = ema_fn(c1h[-50:], 50) if len(c1h)>=50 else e20
    e20_4h = ema_fn(c4h[-20:], 20)
    e50_4h = ema_fn(c4h[-50:], 50) if len(c4h)>=50 else e20_4h
    bbw = bb_width(c1h)

    # SMC代理因子
    ns = nr = ti = bh = bl = False
    if h1h and l1h and len(h1h) >= 12:
        h24 = max(h1h[-24:]) if len(h1h)>=24 else max(h1h)
        l24 = min(l1h[-24:]) if len(l1h)>=24 else min(l1h)
        h48 = max(h1h[-48:]) if len(h1h)>=48 else max(h1h)
        l48 = min(l1h[-48:]) if len(l1h)>=48 else min(l1h)
        rng = (h48-l48)/px*100 if h48 > l48 else 10
        ns = (px-l24)/px*100 < 1.5   # 近支撑
        nr = (h24-px)/px*100 < 1.5   # 近阻力
        ti = rng < 8.0                # TIGHT压缩
        bh = px >= h48*0.998          # 突破48H高
        bl = px <= l48*1.002          # 突破48H低

    # ── CHOP体制专属三战术 ──────────────────────────────────
    if regime == 'CHOP_MID':
        if direction == 'LONG':
            # 战术A：区间低位狙击
            if r1 < 35: sc += 40
            elif r1 < 45: sc += 28
            elif r1 < 52: sc += 15
            elif r1 > 65: sc -= 20
            if ns: sc += 32   # 近支撑位
            if ti: sc += 20   # 区间压缩
            if r15 < 30: sc += 15  # 15m超卖确认
            # 战术C（快攻层）：均值回归
            if tf_layer == '15m' and r15 < 25: sc += 10
            sc += 12  # CHOP做多基础分
        else:  # SHORT
            # 战术A：区间高位狙击
            if r1 > 65: sc += 40
            elif r1 > 58: sc += 28
            elif r1 > 52: sc += 15
            elif r1 < 38: sc -= 20
            if nr: sc += 32   # 近阻力位
            if ti: sc += 20   # 区间压缩
            if r15 > 72: sc += 15  # 15m超买确认
            if tf_layer == '15m' and r15 > 78: sc += 10
            sc += 12

    # ── 趋势体制（BULL/BEAR）──────────────────────────────
    elif regime in ('BULL_TREND', 'BULL_EARLY', 'BEAR_RECOVERY'):
        if direction == 'LONG':
            # 均线多头排列
            if e20>e50 and e20_4h>e50_4h: sc += 30
            elif e20>e50: sc += 18
            # RSI回调黄金区
            if 42<=r1<=58: sc += 30
            elif 38<=r1<42: sc += 22
            elif r1<38: sc += 14
            elif 58<r1<=68: sc += 8
            elif r1>72: sc -= 18
            if r15<45: sc += 12; 
            if ns: sc += 22
            if ti: sc += 15
            if bh: sc += 8
            sc += 20 if regime=='BULL_TREND' else 12
            if r4>55: sc += 8
            # 趋势层额外：4H强势加分
            if tf_layer == '4h' and r4 > 60: sc += 8
        else: sc = 0

    elif regime in ('BEAR_TREND', 'BEAR_EARLY'):
        if direction == 'SHORT':
            if not(e20>e50) and not(e20_4h>e50_4h): sc += 30
            elif not(e20>e50): sc += 18
            if 42<=r1<=58: sc += 30
            elif 58<r1<=68: sc += 22
            elif r1>68: sc += 14
            elif 38<=r1<42: sc += 8
            elif r1<38: sc -= 18
            if r15>55: sc += 12
            if nr: sc += 22
            if ti: sc += 15
            if bl: sc += 8
            sc += 20 if regime=='BEAR_TREND' else 12
            if r4<48: sc += 8
            if tf_layer == '4h' and r4 < 42: sc += 8
        else: sc = 0

    # ── 多周期共振加成 ─────────────────────────────────────
    if direction == 'LONG':
        align = sum([r15<52, r1<55, r4<62])
    else:
        align = sum([r15>55, r1>55, r4>55])
    sc += [0, 5, 10, 15][align]

    # ── BB压缩加成 ────────────────────────────────────────
    if bbw < 1.0: sc += 15
    elif bbw < 1.5: sc += 10
    elif bbw < 2.5: sc += 5

    # ── 月历偏差（季节性因子）────────────────────────────
    # 在score里直接体现，无需外部注入
    # 9月+8 / 10月+6 / 12月+4 / 5月-10 / 8月-6
    # （由调用方注入月份加成，此处预留接口）

    return max(0, min(sc, 165))


# ═══════════════════════════════════════════════════════════════
# v6.0 死穴 + 能力门槛系统
# ═══════════════════════════════════════════════════════════════
BASE_DEAD = {('BEAR_TREND','LONG'), ('BULL_TREND','SHORT')}

def get_threshold(regime, direction, tf_layer, sym='BTCUSDT'):
    """
    返回(是否允许开单, 最低评分门槛)
    替代永久封禁 → 高门槛+结构确认
    """
    # 原宪法死穴（精英解锁）
    if (regime, direction) in BASE_DEAD:
        return (True, 155)  # 精英解锁门槛

    # v6.0 能力门槛（非封禁）
    thresholds = {
        # (体制, 方向): {层级: 门槛}
        ('BEAR_RECOVERY', 'LONG'): {'15m': 999, '1h': 140, '4h': 145},  # 15m不允许
        ('BEAR_EARLY',    'SHORT'):{'15m': 999, '1h': 135, '4h': 140},
        ('BULL_EARLY',    'LONG'): {
            '15m': 999,
            '1h': 120 if sym=='BTCUSDT' else 140,  # ETH更严
            '4h': 130 if sym=='BTCUSDT' else 145,
        },
        ('BULL_TREND',    'LONG'): {'15m': 110, '1h': 120, '4h': 125},
        ('BEAR_TREND',    'SHORT'):{'15m': 110, '1h': 120, '4h': 125},
        ('CHOP_MID',      'LONG'): {
            '15m': 105,
            '1h': 130 if sym=='BTCUSDT' else 125,
            '4h': 135 if sym=='BTCUSDT' else 130,
        },
        ('CHOP_MID',      'SHORT'):{
            '15m': 105,
            '1h': 130 if sym=='BTCUSDT' else 125,
            '4h': 135 if sym=='BTCUSDT' else 130,
        },
    }
    key = (regime, direction)
    if key in thresholds:
        thresh = thresholds[key].get(tf_layer, 120)
        return (thresh < 999, thresh)
    return (True, 120)


# ═══════════════════════════════════════════════════════════════
# 仓位管理（三层 + score梯度 + 体制乘数）
# ═══════════════════════════════════════════════════════════════
LAYER_BASE_SIZE = {'15m': 0.02, '1h': 0.05, '4h': 0.08}  # 基础仓位比例
LAYER_TP_RR    = {'15m': 1.0,  '1h': 1.5,  '4h': 2.5}   # TP1 RR
LAYER_TP2_RR   = {'15m': 1.5,  '1h': 2.5,  '4h': 4.0}   # TP2 RR

REGIME_MULT = {
    'BULL_TREND':    {'LONG':1.0, 'SHORT':0.15},
    'BULL_EARLY':    {'LONG':0.8, 'SHORT':0.20},
    'BEAR_TREND':    {'LONG':0.1, 'SHORT':1.0},
    'BEAR_EARLY':    {'LONG':0.35,'SHORT':1.0},
    'BEAR_RECOVERY': {'LONG':1.0, 'SHORT':0.30},
    'CHOP_MID':      {'LONG':0.5, 'SHORT':0.50},
}
REGIME_SL = {
    'BULL_TREND':2.0,'BULL_EARLY':2.5,'BEAR_TREND':2.0,
    'BEAR_EARLY':2.5,'BEAR_RECOVERY':2.5,'CHOP_MID':2.0,
}

def score_size_mult(score):
    """凯利公式驱动的仓位梯度"""
    if score >= 150: return 2.0
    if score >= 140: return 1.5
    if score >= 130: return 1.2
    return 1.0

MONTH_BIAS = {9:8, 10:6, 12:4, 5:-10, 8:-6, 7:-5}  # 月历偏差（加到score）

FEE_RATE = 0.001  # 0.1% taker×2
LEV = 5

# ═══════════════════════════════════════════════════════════════
# 主回测引擎 v6.0
# ═══════════════════════════════════════════════════════════════
def run_v6(sym, nav_start=10000):
    t0 = time.time()
    print(f"\n{'='*60}")
    print(f"梵天v6.0回测 | {sym} | 三层仓位 | 2020-2026")
    print(f"{'='*60}")

    def ld(tf):
        return [(int(k[0]),float(k[1]),float(k[2]),float(k[3]),
                 float(k[4]),float(k[7]))
                for k in json.loads((DATA_DIR/f'{sym}_{tf}.json').read_text())]

    k15=ld('15m'); k1=ld('1h'); k4=ld('4h'); k1d=ld('1d')
    print(f"数据: 15m={len(k15):,} 1H={len(k1):,} 4H={len(k4):,} 1D={len(k1d):,}")

    nav = nav_start
    # 三层独立持仓池
    positions = {'15m': None, '1h': None, '4h': None}
    trades = []; fee_total = 0.0
    max_nav = nav_start; max_dd = 0.0
    prev_regime = 'UNKNOWN'
    by_layer = defaultdict(lambda:{'n':0,'w':0,'pnl':0.0})
    by_regime = defaultdict(lambda:{'n':0,'w':0,'pnl':0.0})

    # ── 主循环（每4根15min=1H一次）──────────────────────
    for i in range(240, len(k15), 4):
        ts  = k15[i][0]; px = k15[i][4]
        import datetime as _dt
        month = _dt.datetime.utcfromtimestamp(ts/1000).month
        month_adj = MONTH_BIAS.get(month, 0)

        # ── 各层持仓检查 ─────────────────────────────────
        for layer in ['15m','1h','4h']:
            pos = positions[layer]
            if pos is None: continue
            sl=pos['sl']; tp1=pos['tp1']; tp2=pos['tp2']; d=pos['dir']

            hit_sl  = (d=='LONG' and px<=sl) or (d=='SHORT' and px>=sl)
            hit_tp1 = (not pos.get('tp1_hit')) and (
                (d=='LONG' and px>=tp1) or (d=='SHORT' and px<=tp1))
            hit_tp2 = pos.get('tp1_hit') and (
                (d=='LONG' and px>=tp2) or (d=='SHORT' and px<=tp2))

            if hit_tp1:
                ep = tp1
                pp = (ep-pos['entry'])/pos['entry'] if d=='LONG' else (pos['entry']-ep)/pos['entry']
                hq = pos['qty']*0.5
                fee = hq*pos['entry']*FEE_RATE
                pnl = pp*hq*pos['entry']*LEV - fee
                nav += pnl; fee_total += fee
                by_layer[layer]['n']+=1; by_layer[layer]['pnl']+=pnl
                by_regime[pos['regime']+'_'+d]['n']+=1
                by_regime[pos['regime']+'_'+d]['pnl']+=pnl
                if pnl>0: by_layer[layer]['w']+=1; by_regime[pos['regime']+'_'+d]['w']+=1
                trades.append({'layer':layer,'pnl':pnl,'dir':d,'regime':pos['regime'],
                               'score':pos['score'],'exit':'TP1','ts':ts})
                pos['qty']=hq; pos['tp1_hit']=True; pos['sl']=pos['entry']
                positions[layer] = pos

            elif hit_tp2:
                ep = tp2
                pp = (ep-pos['entry'])/pos['entry'] if d=='LONG' else (pos['entry']-ep)/pos['entry']
                fee = pos['qty']*pos['entry']*FEE_RATE
                pnl = pp*pos['qty']*pos['entry']*LEV - fee
                nav += pnl; fee_total += fee
                by_layer[layer]['n']+=1; by_layer[layer]['pnl']+=pnl
                by_regime[pos['regime']+'_'+d]['n']+=1
                by_regime[pos['regime']+'_'+d]['pnl']+=pnl
                if pnl>0: by_layer[layer]['w']+=1; by_regime[pos['regime']+'_'+d]['w']+=1
                trades.append({'layer':layer,'pnl':pnl,'dir':d,'regime':pos['regime'],
                               'score':pos['score'],'exit':'TP2','ts':ts})
                positions[layer] = None

            elif hit_sl:
                ep = sl
                pp = (ep-pos['entry'])/pos['entry'] if d=='LONG' else (pos['entry']-ep)/pos['entry']
                fee = pos['qty']*pos['entry']*FEE_RATE
                pnl = pp*pos['qty']*pos['entry']*LEV - fee
                nav += pnl; fee_total += fee
                by_layer[layer]['n']+=1; by_layer[layer]['pnl']+=pnl
                by_regime[pos['regime']+'_'+d]['n']+=1
                by_regime[pos['regime']+'_'+d]['pnl']+=pnl
                if pnl>0: by_layer[layer]['w']+=1; by_regime[pos['regime']+'_'+d]['w']+=1
                trades.append({'layer':layer,'pnl':pnl,'dir':d,'regime':pos['regime'],
                               'score':pos['score'],'exit':'SL','ts':ts})
                positions[layer] = None

        if nav > max_nav: max_nav = nav
        dd = (max_nav-nav)/max_nav*100
        if dd > max_dd: max_dd = dd

        # ── 多周期数据准备 ───────────────────────────────
        i1  = fb(k1,  ts); i4 = fb(k4, ts); id_ = fb(k1d, ts)
        if i1<50 or i4<50 or id_<20: continue

        c1  = [k1[j][4]  for j in range(max(0,i1-99),  i1+1)]
        c4  = [k4[j][4]  for j in range(max(0,i4-99),  i4+1)]
        cd  = [k1d[j][4] for j in range(max(0,id_-29), id_+1)]
        c15 = [k15[j][4] for j in range(max(0,i-49),   i+1)]
        h1h = [k1[j][2]  for j in range(max(0,i1-55),  i1+1)]
        l1h = [k1[j][3]  for j in range(max(0,i1-55),  i1+1)]

        regime = detect_regime(c4, cd)
        if regime == 'UNKNOWN': continue

        # 体制切换 → 全平所有层（风险管理）
        if prev_regime != regime and prev_regime != 'UNKNOWN':
            for layer in ['15m','1h','4h']:
                if positions[layer]:
                    pos = positions[layer]
                    pp = (px-pos['entry'])/pos['entry'] if pos['dir']=='LONG' else (pos['entry']-px)/pos['entry']
                    fee = pos['qty']*pos['entry']*FEE_RATE
                    pnl = pp*pos['qty']*pos['entry']*LEV - fee
                    nav += pnl; fee_total += fee
                    trades.append({'layer':layer,'pnl':pnl,'dir':pos['dir'],
                                   'regime':pos['regime'],'score':pos['score'],
                                   'exit':'REGIME_CHG','ts':ts})
                    positions[layer] = None
        prev_regime = regime

        # 候选方向
        if regime in ('BULL_TREND','BULL_EARLY','BEAR_RECOVERY'): cands=['LONG']
        elif regime in ('BEAR_TREND','BEAR_EARLY'): cands=['SHORT']
        else: cands=['LONG','SHORT']

        # ── 各层扫描信号 ─────────────────────────────────
        for layer in ['15m','1h','4h']:
            if positions[layer]: continue  # 该层已有持仓

            best_score = 0; best_dir = None
            for direction in cands:
                allowed, thresh = get_threshold(regime, direction, layer, sym)
                if not allowed: continue

                raw_score = score_v6(c15, c1, c4, h1h, l1h, regime, direction, layer)
                # 月历偏差
                adj_score = raw_score + month_adj
                adj_score = max(0, min(adj_score, 165))

                if adj_score > best_score and adj_score >= thresh:
                    best_score = adj_score; best_dir = direction

            if best_dir is None: continue

            # ── 开单 ─────────────────────────────────────
            raw_kl = k1[max(0,i1-20):i1+1]
            atr = atr_fn(raw_kl)
            slb = REGIME_SL.get(regime, 2.5)
            atr_sl = atr*1.5/px*100 if px > 0 else slb
            # 趋势层止损稍宽（持仓更长）
            sl_limit = 8.0 if layer=='4h' else 6.0
            slp = min(max(slb, atr_sl), sl_limit)

            size_base = LAYER_BASE_SIZE[layer]
            regime_m  = REGIME_MULT.get(regime,{}).get(best_dir, 0.5)
            score_m   = score_size_mult(best_score)
            notional  = nav * size_base * regime_m * score_m * LEV
            if notional < nav * 0.005: continue
            qty = notional / px

            # 开仓手续费
            fee = qty * px * FEE_RATE
            nav -= fee; fee_total += fee

            tp1_rr = LAYER_TP_RR[layer]; tp2_rr = LAYER_TP2_RR[layer]
            if best_dir == 'LONG':
                sl_p  = px*(1 - slp/100)
                tp1_p = px*(1 + slp*tp1_rr/100)
                tp2_p = px*(1 + slp*tp2_rr/100)
            else:
                sl_p  = px*(1 + slp/100)
                tp1_p = px*(1 - slp*tp1_rr/100)
                tp2_p = px*(1 - slp*tp2_rr/100)

            positions[layer] = {
                'dir':best_dir,'entry':px,'qty':qty,
                'sl':sl_p,'tp1':tp1_p,'tp2':tp2_p,
                'regime':regime,'score':best_score,'ts':ts
            }

    # ── 强平剩余持仓 ─────────────────────────────────────
    pxf = k15[-1][4]
    for layer in ['15m','1h','4h']:
        pos = positions[layer]
        if pos:
            pp = (pxf-pos['entry'])/pos['entry'] if pos['dir']=='LONG' else (pos['entry']-pxf)/pos['entry']
            fee = pos['qty']*pos['entry']*FEE_RATE
            pnl = pp*pos['qty']*pos['entry']*LEV - fee
            nav += pnl; fee_total += fee
            trades.append({'layer':layer,'pnl':pnl,'dir':pos['dir'],'regime':pos['regime'],
                           'score':pos['score'],'exit':'FINAL','ts':k15[-1][0]})

    # ── 结果输出 ─────────────────────────────────────────
    n = len(trades); wins = sum(1 for t in trades if t['pnl']>0)
    elapsed = time.time() - t0

    print(f"\n耗时: {elapsed:.1f}s")
    print(f"NAV: ${nav_start:,.0f} → ${nav:,.2f} ({(nav-nav_start)/nav_start*100:+.2f}%)")
    print(f"WR:  {wins/n*100:.1f}% ({wins}/{n}) | MaxDD: {max_dd:.2f}% | 手续费: ${fee_total:,.0f}")

    # 系统使用率
    total_h = 6.5*365*24
    used_h  = sum(1 for t in trades if t['exit'] in ('TP1','TP2','SL')) * 4
    print(f"系统使用率: {used_h/total_h*100:.1f}% (三层合计)")

    print(f"\n{'─'*55}")
    print(f"层级拆分:")
    for layer in ['15m','1h','4h']:
        v=by_layer[layer]; wr=v['w']/v['n']*100 if v['n']>0 else 0
        print(f"  {layer} 层: n={v['n']:3d} WR={wr:5.1f}% PnL=${v['pnl']:+9,.0f}")

    print(f"\n体制×方向 拆分:")
    for k in sorted(by_regime):
        v=by_regime[k]; wr=v['w']/v['n']*100 if v['n']>0 else 0
        print(f"  {k:<30} n={v['n']:3d} WR={wr:5.1f}% PnL=${v['pnl']:+9,.0f}")

    return {
        'sym':sym,'nav':round(nav,2),'ret':(nav-nav_start)/nav_start*100,
        'max_dd':max_dd,'n':n,'wr':wins/n*100 if n>0 else 0,
        'fee':fee_total,'trades':trades,'by_layer':dict(by_layer),'by_regime':dict(by_regime)
    }


if __name__ == '__main__':
    print("梵天达摩院 v6.0 — 三层仓位 + 能力门槛 + CHOP三战术")
    print("开始回测...\n")
    
    btc_r = run_v6('BTCUSDT')
    print()
    eth_r = run_v6('ETHUSDT')
    
    print(f"\n{'='*60}")
    print(f"双标的汇总:")
    print(f"  BTC: ${btc_r['nav']:>10,.0f} {btc_r['ret']:>+8.1f}% MaxDD={btc_r['max_dd']:.1f}% WR={btc_r['wr']:.1f}%")
    print(f"  ETH: ${eth_r['nav']:>10,.0f} {eth_r['ret']:>+8.1f}% MaxDD={eth_r['max_dd']:.1f}% WR={eth_r['wr']:.1f}%")
    print(f"  均值: 收益={(btc_r['ret']+eth_r['ret'])/2:>+8.1f}% WR={(btc_r['wr']+eth_r['wr'])/2:.1f}%")
    
    # 保存结果
    for r in [btc_r, eth_r]:
        sym = r['sym'].lower()
        Path(f'data/backtest/{sym}_v6.json').write_text(
            json.dumps({k:v for k,v in r.items() if k!='trades'}, 
                      ensure_ascii=False, default=str))
    print(f"\n✅ 结果已保存")
