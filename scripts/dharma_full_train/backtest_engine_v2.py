#!/usr/bin/env python3
"""
达摩院 · 全周期回测引擎 v2.0  (Phase2a结构信号版)
设计院 2026-06-02

v2.0 核心改进：
  OB/FVG/背离/区间 四层结构共振
  体制过滤：BEAR顺势，BULL顺势，RANGE区间极值
  实时计算结构特征（训练集+OOS均适用，无穿越）
  仓位控制：单笔最大亏损2%，复利上限×20
"""
import json, datetime, os, math
from pathlib import Path
from collections import defaultdict

BASE     = Path(__file__).parent.parent.parent
DATA_DIR = BASE / 'data' / 'dharma_8y'
OUT_DIR  = BASE / 'data' / 'dharma_backtest_v2'
OUT_DIR.mkdir(exist_ok=True)

CUTOFF_MS    = int(datetime.datetime(2025,1,1,tzinfo=datetime.timezone.utc).timestamp()*1000)
INIT_CAPITAL = 5000.0
RISK_PCT     = 0.015   # 每笔1.5%风险（比v1.0更保守）
MAX_LEVERAGE = 5.0     # 最大5倍杠杆

# ─── 技术指标 ─────────────────────────────────────────────────
def rsi(closes, n=14):
    # [FIX-RSI-WILDER 2026-06-14] Wilder EMA算法
    if len(closes) < n+1: return 50.0
    g=[]; l=[]
    for i in range(1, len(closes)):
        d = closes[i]-closes[i-1]
        g.append(max(d,0)); l.append(max(-d,0))
    ag=sum(g[:n])/n; al=sum(l[:n])/n
    for i in range(n, len(g)):
        ag=(ag*(n-1)+g[i])/n; al=(al*(n-1)+l[i])/n
    return 100-100/(1+ag/al) if al>0 else 100.0

def atr(klines, n=14):
    trs=[]
    for i in range(1, len(klines)):
        h,l,pc = float(klines[i][2]),float(klines[i][3]),float(klines[i-1][4])
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs[-n:])/min(n,len(trs)) if trs else 0

# ─── 结构识别（实时，无前视偏差）───────────────────────────────
def detect_ob_short(klines):
    """OB看空：下跌前最后一根有力上涨K线 + 当前价在OB区域内"""
    if len(klines) < 6: return False
    cur = float(klines[-1][4])
    avg_body = sum(abs(float(k[4])-float(k[1])) for k in klines[-20:]) / 20
    for i in range(max(0,len(klines)-5), len(klines)-1):
        o,c,h,l = float(klines[i][1]),float(klines[i][4]),float(klines[i][2]),float(klines[i][3])
        if c > o and abs(c-o) > avg_body*0.8:
            nxt = klines[i+1:min(i+4,len(klines))]
            if sum(1 for k in nxt if float(k[4])<float(k[1])) >= 1:
                if l <= cur <= h*1.003:
                    return True
    return False

def detect_ob_long(klines):
    if len(klines) < 6: return False
    cur = float(klines[-1][4])
    avg_body = sum(abs(float(k[4])-float(k[1])) for k in klines[-20:]) / 20
    for i in range(max(0,len(klines)-5), len(klines)-1):
        o,c,h,l = float(klines[i][1]),float(klines[i][4]),float(klines[i][2]),float(klines[i][3])
        if c < o and abs(c-o) > avg_body*0.8:
            nxt = klines[i+1:min(i+4,len(klines))]
            if sum(1 for k in nxt if float(k[4])>float(k[1])) >= 1:
                if l*0.997 <= cur <= h:
                    return True
    return False

def detect_bearish_div(closes, n=20):
    """RSI看空背离：价格高位，RSI未创新高"""
    if len(closes) < n+2: return False
    rsi_cur = rsi(closes)
    rsi_prev = max(rsi(closes[:-i]) for i in range(3, min(n,10)))
    p_cur = closes[-1]; p_prev = max(closes[-n:-3]) if len(closes)>n+3 else closes[-3]
    return p_cur >= p_prev*0.995 and rsi_cur < rsi_prev*0.93

def detect_range_premium(klines, lookback=48):
    """区间高位：价格在区间70%以上"""
    if len(klines) < lookback: return False, 0.5
    win = klines[-lookback:]
    hs = [float(k[2]) for k in win]; ls = [float(k[3]) for k in win]
    rh = max(hs); rl = min(ls)
    if rh == rl: return False, 0.5
    rng_pct = (rh-rl)/rl*100
    if rng_pct < 1.5 or rng_pct > 20: return False, 0.5
    th = sum(1 for h in hs if h >= rh*0.99)
    tl = sum(1 for l in ls if l <= rl*1.01)
    if th < 2 or tl < 2: return False, 0.5
    cur = float(klines[-1][4])
    pos = (cur-rl)/(rh-rl)
    return pos >= 0.70, pos

def get_regime(klines, lb=80):
    """市场性格：BEAR / BULL / RANGE"""
    if len(klines) < lb: return 'UNKNOWN'
    closes = [float(k[4]) for k in klines[-lb:]]
    trend = (closes[-1]-closes[0])/closes[0]*100
    atr_v = atr(klines[-20:])
    price = closes[-1]
    atr_pct = atr_v/price*100 if price>0 else 0
    if abs(trend) > max(atr_pct*3, 3):
        return 'BEAR' if trend < 0 else 'BULL'
    return 'RANGE'

# ─── 信号生成 ────────────────────────────────────────────────
def generate_signal(klines, idx):
    """
    Phase2a 四层共振信号
    只用 idx 及之前的数据（无穿越）
    """
    if idx < 80: return None
    window = klines[max(0,idx-100):idx+1]
    closes = [float(k[4]) for k in window]
    price  = closes[-1]

    atr_v   = atr(window[-20:])
    atr_pct = atr_v/price*100 if price>0 else 0
    if atr_pct < 0.1: return None  # 数据异常

    regime = get_regime(window)

    # 评分
    sc_short = sc_long = 0

    # Layer1: OB结构（基础分15分）
    if detect_ob_short(window[-30:]): sc_short += 15
    if detect_ob_long(window[-30:]):  sc_long  += 15

    # Layer2: 背离（Phase2a权重18分）
    if detect_bearish_div(closes): sc_short += 18

    # Layer2b: RSI状态
    rsi_v = rsi(closes)
    if rsi_v > 65: sc_short += 8
    if rsi_v < 35: sc_long  += 8

    # Layer3: 区间位置（Phase2a新增12分）
    is_prem, pos = detect_range_premium(window)
    if is_prem:   sc_short += 12
    if pos < 0.3 and sc_long > 0: sc_long += 12

    # Layer0: 体制顺势
    if regime == 'BEAR':
        sc_short += 15; sc_long  -= 15
    elif regime == 'BULL':
        sc_long  += 15; sc_short -= 15
    elif regime == 'RANGE':
        if not (is_prem or pos < 0.3): return None

    # 决策
    MIN = 40
    if sc_short >= MIN and sc_short > sc_long:
        sl = price * (1 + max(atr_pct*1.8, 1.2)/100)
        return {'dir':'SHORT','entry':price,'sl':sl,'atr_pct':atr_pct,'score':sc_short,'regime':regime}
    if sc_long >= MIN and sc_long > sc_short:
        sl = price * (1 - max(atr_pct*1.8, 1.2)/100)
        return {'dir':'LONG','entry':price,'sl':sl,'atr_pct':atr_pct,'score':sc_long,'regime':regime}
    return None

# ─── 回测核心 ────────────────────────────────────────────────
def run_backtest(symbol, interval, split='OOS'):
    fname = DATA_DIR / f'{symbol}_{interval}_pure.json'
    if not fname.exists(): return None

    klines = json.load(open(fname))
    if split == 'OOS':
        klines = [k for k in klines if int(k[0]) >= CUTOFF_MS]
    else:
        klines = [k for k in klines if int(k[0]) < CUTOFF_MS]
    if len(klines) < 100: return None

    capital = INIT_CAPITAL
    trades  = []
    pos     = None
    peak    = capital

    for idx in range(80, len(klines)-20):
        if capital <= 0: break
        price = float(klines[idx][4])

        # 持仓结算
        if pos:
            f_high = max(float(klines[min(len(klines)-1,idx+i)][2]) for i in range(1,21))
            f_low  = min(float(klines[min(len(klines)-1,idx+i)][3]) for i in range(1,21))
            tp1 = (pos['entry']*(1-pos['atr_pct']*3/100)
                   if pos['dir']=='SHORT'
                   else pos['entry']*(1+pos['atr_pct']*3/100))

            result = None
            if pos['dir']=='SHORT':
                if f_low  <= tp1:           result='TP1'; pnl=(pos['entry']-tp1)/pos['entry']
                elif f_high >= pos['sl']:   result='SL';  pnl=-(pos['sl']-pos['entry'])/pos['entry']
            else:
                if f_high >= tp1:           result='TP1'; pnl=(tp1-pos['entry'])/pos['entry']
                elif f_low  <= pos['sl']:   result='SL';  pnl=-(pos['entry']-pos['sl'])/pos['entry']

            if result:
                capital = max(capital*(1+pnl*pos['sz']), 0.01)
                trades.append({'dir':pos['dir'],'result':result,'pnl':round(pnl*100,2)})
                peak = max(peak, capital); pos = None
            continue

        # 开仓
        sig = generate_signal(klines, idx)
        if not sig: continue

        sl_pct = abs(sig['entry']-sig['sl'])/sig['entry']
        if sl_pct < 0.001: continue
        sz = min(RISK_PCT/sl_pct, MAX_LEVERAGE)
        pos = {**sig, 'sz':sz}

    if not trades:
        return {'symbol':symbol,'interval':interval,'split':split,
                'n':0,'wr':0,'pf':0,'roi':0,'dd':0,'final':INIT_CAPITAL}

    n    = len(trades)
    wins = [t for t in trades if t['result']=='TP1']
    loss = [t for t in trades if t['result']=='SL']
    wr   = len(wins)/n*100
    aw   = sum(t['pnl'] for t in wins)/len(wins)   if wins else 0
    al   = sum(abs(t['pnl']) for t in loss)/len(loss) if loss else 0
    pf   = (aw*len(wins))/(al*len(loss)) if loss and al>0 else 99.0
    roi  = (capital-INIT_CAPITAL)/INIT_CAPITAL*100

    # 最大回撤
    cap2=INIT_CAPITAL; pk2=cap2; dd=0
    for t in trades:
        cap2 = max(cap2*(1+t['pnl']/100), 0.01)
        pk2  = max(pk2,cap2)
        dd   = max(dd, (pk2-cap2)/pk2*100)

    return {'symbol':symbol,'interval':interval,'split':split,
            'n':n,'wr':round(wr,1),'pf':round(pf,2),
            'roi':round(roi,1),'dd':round(dd,1),
            'aw':round(aw,2),'al':round(al,2),'final':round(capital,2)}

# ─── 主入口 ──────────────────────────────────────────────────
if __name__ == '__main__':
    targets = [
        ('BTCUSDT','4h'),('BTCUSDT','1h'),('BTCUSDT','15m'),
        ('ETHUSDT','4h'),('ETHUSDT','1h'),('ETHUSDT','15m'),
    ]
    all_results = []
    print(f'{"标的":<12}{"周期":<6}{"集合":<6}{"笔数":>6}{"WR":>7}{"PF":>6}{"ROI":>9}{"DD":>7}')
    print('─'*58)

    for sym, itv in targets:
        for split in ['TRAIN','OOS']:
            print(f'  计算 {sym} {itv} {split}...', flush=True)
            r = run_backtest(sym, itv, split)
            if r:
                all_results.append(r)
                roi_s = f'+{r["roi"]:.1f}%' if r['roi']>=0 else f'{r["roi"]:.1f}%'
                mk = '🏆' if r['roi']>100 else ('✅' if r['roi']>0 else ('⚠️' if r['roi']>-50 else '❌'))
                print(f'{mk} {r["symbol"][-3:]}{r["interval"]:<6}{r["split"]:<6}'
                      f'{r["n"]:>6}{r["wr"]:>6.1f}%{r["pf"]:>6.2f}{roi_s:>9} -{r["dd"]:.1f}%')

    print()
    json.dump(all_results, open(OUT_DIR/'full_report_v2.json','w'), indent=2)

    # 与v1.0对比
    v1f = BASE/'data'/'dharma_backtest'/'full_report.json'
    if v1f.exists():
        v1 = {f'{r["symbol"]}_{r["interval"]}_{r["split"]}':r
              for r in json.load(open(v1f))}
        print('=== Phase2a vs v1.0 对比（OOS集合）===')
        print(f'{"标的/周期":<18}{"v1.0":>8}{"v2.0":>8}{"变化":>8}{"WR":>7}{"PF":>6}')
        print('─'*55)
        for r in [x for x in all_results if x['split']=='OOS']:
            k=f'{r["symbol"]}_{r["interval"]}_OOS'
            v=v1.get(k,{})
            v1r=v.get('roi',0); v2r=r['roi']; d=v2r-v1r
            ar='📈' if d>10 else('📉' if d<-10 else '➡️')
            v1s=f'+{v1r:.0f}%' if v1r>=0 else f'{v1r:.0f}%'
            v2s=f'+{v2r:.0f}%' if v2r>=0 else f'{v2r:.0f}%'
            ds =f'+{d:.0f}%'   if d>=0   else f'{d:.0f}%'
            print(f'{ar} {r["symbol"][-3:]}_{r["interval"]:<12}{v1s:>8}{v2s:>8}{ds:>8}'
                  f'{r["wr"]:>6.1f}%{r["pf"]:>6.2f}')
