#!/usr/bin/env python3
"""
达摩院四周期训练 M2~M5
M2: Score分段 × 周期联合分析
M3: grade分段 × 体制联合分析
M4: WFV 年度滚动验证
M5: 15M触发精度分析
"""
import numpy as np
import pandas as pd
from pathlib import Path
import json
from datetime import datetime

FIXED = Path(__file__).parent.parent / 'data/backtest/fixed'
RESULTS = Path(__file__).parent / 'results'
RESULTS.mkdir(exist_ok=True)

# ── 工具函数 ──────────────────────────────────────────────
def ema(arr, p):
    out = np.full(len(arr), np.nan)
    if len(arr) < p: return out
    out[p-1] = arr[:p].mean()
    k = 2/(p+1)
    for i in range(p, len(arr)):
        out[i] = arr[i]*k + out[i-1]*(1-k)
    return out

def rsi(close, p=14):
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta>0, delta, 0.0)
    loss = np.where(delta<0, -delta, 0.0)
    ag = pd.Series(gain).ewm(alpha=1/p, min_periods=p).mean().values
    al = pd.Series(loss).ewm(alpha=1/p, min_periods=p).mean().values
    rs = np.where(al==0, 100, ag/np.where(al==0,1e-9,al))
    return np.where(al==0, 100, 100-100/(1+rs))

def atr(high, low, close, p=14):
    tr = np.maximum(high-low, np.maximum(abs(high-np.roll(close,1)),abs(low-np.roll(close,1))))
    tr[0] = high[0]-low[0]
    return pd.Series(tr).ewm(alpha=1/p, min_periods=p).mean().values

def get_regime(df4h, idx):
    if idx < 10: return 'CHOP_MID'
    row = df4h.iloc[idx]
    price = float(row['close'])
    ema200 = float(row.get('ema200', price))
    ema55  = float(row.get('ema55', price))
    rsi14  = float(row.get('rsi14', 50))
    w = df4h.iloc[max(0,idx-20):idx+1]
    hi20 = float(w['high'].max()); lo20 = float(w['low'].min())
    rng  = hi20 - lo20
    rng_pct = rng/price*100

    above200 = price > ema200
    above55  = price > ema55
    bull_rsi = rsi14 > 52
    bear_rsi = rsi14 < 48

    if above200 and above55 and bull_rsi and rng_pct > 15:
        return 'BULL_TREND'
    elif above200 and bull_rsi:
        return 'BULL_EARLY'
    elif above200 and above55:
        return 'BULL_CORRECTION'
    elif not above200 and not above55 and bear_rsi and rng_pct > 15:
        return 'BEAR_TREND'
    elif not above200 and bear_rsi:
        return 'BEAR_EARLY'
    elif not above200 and not bear_rsi:
        return 'BEAR_RECOVERY'
    else:
        return 'CHOP_MID'

def enrich(df):
    c = df['close'].values.astype(np.float64)
    h = df['high'].values.astype(np.float64)
    l = df['low'].values.astype(np.float64)
    df = df.copy()
    df['ema200'] = ema(c, 200)
    df['ema55']  = ema(c, 55)
    df['ema21']  = ema(c, 21)
    df['rsi14']  = rsi(c, 14)
    df['atr14']  = atr(h, l, c, 14)
    return df

def fake_score(row_1h, row_4h, direction):
    """简化评分：趋势+位置+RSI"""
    price  = float(row_1h['close'])
    ema200 = float(row_4h.get('ema200', price))
    ema55  = float(row_4h.get('ema55', price))
    rsi14  = float(row_1h.get('rsi14', 50))
    atr14  = float(row_1h.get('atr14', price*0.01))
    score  = 0
    if direction == 'LONG':
        score += 20 if price > ema200 else -10
        score += 15 if price > ema55 else -8
        score += 20 if rsi14 > 50 else -10
        score += 10 if rsi14 < 70 else -5   # 不超买
        score += 15 if rsi14 > 40 else -5   # 有动量
        score += max(-10, min(20, (price - ema55)/atr14*3))
    else:
        score += 20 if price < ema200 else -10
        score += 15 if price < ema55 else -8
        score += 20 if rsi14 < 50 else -10
        score += 10 if rsi14 > 30 else -5
        score += 15 if rsi14 < 60 else -5
        score += max(-10, min(20, (ema55 - price)/atr14*3))
    return max(-20, min(100, score + 50))

def fake_grade(row_1h, direction):
    """简化grade：ATR偏离 → 结构代理"""
    price = float(row_1h['close'])
    ema21 = float(row_1h.get('ema21', price))
    atr   = float(row_1h.get('atr14', price*0.01))
    dist_pct = abs(price - ema21) / atr
    if dist_pct >= 1.5: return 80
    elif dist_pct >= 0.8: return 70
    elif dist_pct >= 0.3: return 55
    else: return 40

def simulate_trade(df1h, entry_idx, direction, sl_mult=1.5, tp_mult=2.5, hold_bars=32):
    """模拟持仓结果：WIN/LOSS/TIMEOUT"""
    entry_row = df1h.iloc[entry_idx]
    price = float(entry_row['close'])
    atr   = float(entry_row.get('atr14', price*0.01))
    if direction == 'LONG':
        sl = price - atr * sl_mult
        tp = price + atr * tp_mult
    else:
        sl = price + atr * sl_mult
        tp = price - atr * tp_mult
    for j in range(1, hold_bars+1):
        if entry_idx + j >= len(df1h): break
        hi = float(df1h.iloc[entry_idx+j]['high'])
        lo = float(df1h.iloc[entry_idx+j]['low'])
        if direction == 'LONG':
            if lo <= sl: return 'LOSS', j
            if hi >= tp: return 'WIN',  j
        else:
            if hi >= sl: return 'LOSS', j
            if lo <= tp: return 'WIN',  j
    return 'TIMEOUT', hold_bars

def run_scan(sym, df1h, df4h, df1d, year_start=None, year_end=None):
    """扫描信号，返回 records 列表"""
    if year_start:
        df1h = df1h[df1h.index >= year_start]
    if year_end:
        df1h = df1h[df1h.index < year_end]

    records = []
    last_sig = {'LONG': -100, 'SHORT': -100}
    COOL = 16

    for i in range(50, len(df1h)):
        ts = df1h.index[i]
        idx4 = df4h.index.searchsorted(ts, side='right') - 1
        if idx4 < 10: continue
        regime = get_regime(df4h, idx4)

        for direction in ['LONG', 'SHORT']:
            if i - last_sig[direction] < COOL: continue
            # 体制×方向过滤
            if 'BEAR' in regime and direction == 'LONG': continue
            if 'BULL' in regime and direction == 'SHORT': continue
            if 'CHOP' in regime: continue

            row_1h = df1h.iloc[i]
            row_4h = df4h.iloc[idx4]
            score = fake_score(row_1h, row_4h, direction)
            grade = fake_grade(row_1h, direction)

            if score < 60 or grade < 40: continue

            outcome, bars = simulate_trade(df1h, i, direction)
            records.append({
                'ts': str(ts.date()),
                'regime': regime,
                'direction': direction,
                'score': round(score, 1),
                'grade': grade,
                'outcome': outcome,
                'bars': bars,
            })
            last_sig[direction] = i

    return records

# ── M2: Score分段分析 ─────────────────────────────────────
def m2_score_bands(records, label=''):
    bands = [(0,40),(40,55),(55,70),(70,85),(85,101)]
    rows = []
    for lo,hi in bands:
        sub = [r for r in records if lo <= r['score'] < hi]
        if not sub: continue
        wins   = sum(1 for r in sub if r['outcome']=='WIN')
        losses = sum(1 for r in sub if r['outcome']=='LOSS')
        tos    = sum(1 for r in sub if r['outcome']=='TIMEOUT')
        n = wins+losses+tos
        wr = wins/(wins+losses) if wins+losses>0 else 0
        rows.append({'band':f'{lo}~{hi}','n':n,'wr':round(wr,3),'to_rate':round(tos/n,3),'wins':wins,'losses':losses,'tos':tos})
    return rows

# ── M3: grade分段分析 ─────────────────────────────────────
def m3_grade_bands(records, label=''):
    bands = [(40,55),(55,70),(70,80),(80,101)]
    rows = []
    for lo,hi in bands:
        sub = [r for r in records if lo <= r['grade'] < hi]
        if not sub: continue
        wins   = sum(1 for r in sub if r['outcome']=='WIN')
        losses = sum(1 for r in sub if r['outcome']=='LOSS')
        tos    = sum(1 for r in sub if r['outcome']=='TIMEOUT')
        n = wins+losses+tos
        wr = wins/(wins+losses) if wins+losses>0 else 0
        rows.append({'band':f'{lo}~{hi}','n':n,'wr':round(wr,3),'to_rate':round(tos/n,3)})
    return rows

# ── M4: WFV年度验证 ──────────────────────────────────────
def m4_wfv(sym, df1h, df4h):
    windows = [
        ('2019-11','2020-11','BEAR+BULL早期'),
        ('2020-11','2021-11','BULL_TREND顶峰'),
        ('2021-11','2022-11','BEAR_TREND'),
        ('2022-11','2023-11','CHOP+RECOVERY'),
        ('2023-11','2024-11','BULL_EARLY+TREND'),
        ('2024-11','2025-11','BEAR_EARLY'),
        ('2025-11','2026-06','最新半年'),
    ]
    rows = []
    for start,end,desc in windows:
        recs = run_scan(sym, df1h, df4h, None, year_start=start, year_end=end)
        if not recs: continue
        wins = sum(1 for r in recs if r['outcome']=='WIN')
        losses = sum(1 for r in recs if r['outcome']=='LOSS')
        tos = sum(1 for r in recs if r['outcome']=='TIMEOUT')
        n = wins+losses+tos
        wr = wins/(wins+losses) if wins+losses>0 else 0
        # 简化 OOS_PF（总盈亏/总亏损，正规应用 atr倍数计算）
        pf = (wins*2.5)/(losses*1.5+0.001) if losses>0 else wins*2.5
        rows.append({
            'window': f'{start}~{end}',
            'desc': desc,
            'n': n,
            'wr': round(wr,3),
            'pf': round(pf,2),
            'to_rate': round(tos/n,3) if n>0 else 0,
        })
    return rows

# ── M5: 体制×方向精细统计 ────────────────────────────────
def m5_regime_direction(records):
    from collections import defaultdict
    stats = defaultdict(lambda: {'wins':0,'losses':0,'tos':0})
    for r in records:
        key = f'{r["regime"]}_{r["direction"]}'
        stats[key][{
            'WIN':'wins','LOSS':'losses','TIMEOUT':'tos'
        }[r['outcome']]] += 1
    rows = []
    for key, d in sorted(stats.items()):
        n = d['wins']+d['losses']+d['tos']
        wr = d['wins']/(d['wins']+d['losses']) if d['wins']+d['losses']>0 else 0
        rows.append({'key':key,'n':n,'wr':round(wr,3),'to_rate':round(d['tos']/n,3) if n>0 else 0})
    return sorted(rows, key=lambda x: -x['wr'])

# ── MAIN ─────────────────────────────────────────────────
def main():
    report = {
        'generated_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'data_range': '2019-11-01 ~ 2026-05-30',
        'symbols': {},
    }

    for sym in ['BTCUSDT','ETHUSDT']:
        s = sym.lower()
        print(f'\n[{sym}] 加载数据...')
        df1h = enrich(pd.read_parquet(FIXED / f'{s}_1h_fixed.parquet'))
        df4h = enrich(pd.read_parquet(FIXED / f'{s}_4h_fixed.parquet'))

        print(f'[{sym}] 扫描信号...')
        records = run_scan(sym, df1h, df4h, None)
        print(f'[{sym}] 信号总数: {len(records)}')

        print(f'[{sym}] M2 Score分段...')
        m2 = m2_score_bands(records)

        print(f'[{sym}] M3 grade分段...')
        m3 = m3_grade_bands(records)

        print(f'[{sym}] M4 WFV年度...')
        m4 = m4_wfv(sym, df1h, df4h)

        print(f'[{sym}] M5 体制×方向...')
        m5 = m5_regime_direction(records)

        report['symbols'][sym] = {
            'n_signals': len(records),
            'M2_score_bands': m2,
            'M3_grade_bands': m3,
            'M4_wfv_windows': m4,
            'M5_regime_direction': m5,
        }

        # 打印摘要
        print(f'\n── {sym} M2 Score分段 ──')
        for r in m2:
            print(f'  score {r["band"]:6s}: n={r["n"]:5d}  WR={r["wr"]:.1%}  TO={r["to_rate"]:.1%}')

        print(f'\n── {sym} M3 grade分段 ──')
        for r in m3:
            print(f'  grade {r["band"]:6s}: n={r["n"]:5d}  WR={r["wr"]:.1%}  TO={r["to_rate"]:.1%}')

        print(f'\n── {sym} M4 WFV年度 ──')
        for r in m4:
            print(f'  {r["window"]:22s} ({r["desc"][:12]:12s}): n={r["n"]:4d}  WR={r["wr"]:.1%}  PF={r["pf"]:.2f}')

        print(f'\n── {sym} M5 体制×方向（WR≥60% n≥50）──')
        for r in [x for x in m5 if x['wr']>=0.60 and x['n']>=50]:
            print(f'  {r["key"]:28s}: n={r["n"]:5d}  WR={r["wr"]:.1%}  TO={r["to_rate"]:.1%}')
        print(f'── 死穴（WR<50% n≥50）──')
        for r in [x for x in m5 if x['wr']<0.50 and x['n']>=50]:
            print(f'  {r["key"]:28s}: n={r["n"]:5d}  WR={r["wr"]:.1%}')

    # 保存报告
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    out = RESULTS / f'train_m2m5_report_{ts}.json'
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f'\n✅ 报告已保存: {out}')

if __name__ == '__main__':
    main()
