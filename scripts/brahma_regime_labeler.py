#!/usr/bin/env python3
"""
梵天设计院 · 6.5年体制标注引擎
基于历史K线，对每根K线标注：
  - regime: BULL_TREND / BEAR_TREND / CHOP_MID / BEAR_RECOVERY / BULL_EARLY
  - rsi_4h / rsi_1d
  - ema_structure: EMA20 vs EMA50 vs EMA200
  - atr_regime: HIGH / NORMAL / LOW
  - pd_zone: PREMIUM / DISCOUNT / NEUTRAL

输出: data/historical/{sym}_regime_labels.jsonl.gz

封印: 2026-08-07 设计院自主
"""
import json, gzip, math
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / 'data' / 'historical'

def load_jsonl_gz(path):
    rows = []
    with gzip.open(path, 'rt') as f:
        for line in f:
            rows.append(json.loads(line.strip()))
    return rows

def ema_series(closes, n):
    result = []
    e = closes[0]; k = 2/(n+1)
    for c in closes:
        e = c*k + e*(1-k)
        result.append(e)
    return result

def rsi_series(closes, n=14):
    result = [50.0] * n
    gains  = [max(closes[i]-closes[i-1],0) for i in range(1,len(closes))]
    losses = [max(closes[i-1]-closes[i],0) for i in range(1,len(closes))]
    ag = sum(gains[:n])/n
    al = sum(losses[:n])/n
    for i in range(n, len(closes)-1):
        rs = ag/al if al > 0 else 100
        result.append(100 - 100/(1+rs))
        ag = (ag*(n-1) + gains[i])/n
        al = (al*(n-1) + losses[i])/n
    result.append(100 - 100/(1+(ag/al if al>0 else 100)))
    return result

def atr_series(highs, lows, closes, n=14):
    trs = [highs[0]-lows[0]]
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    result = []
    atr = sum(trs[:n])/n
    result.extend([atr]*n)
    for i in range(n, len(trs)):
        atr = (atr*(n-1) + trs[i])/n
        result.append(atr)
    return result

def classify_regime(rsi_4h, rsi_1d, ema20, ema50, ema200, price, atr_pct):
    """核心体制分类逻辑（基于梵天MEMORY.md宪法）"""
    bull_ema = (ema20 > ema50) and (price > ema200)
    bear_ema = (ema20 < ema50)

    if bull_ema and rsi_1d > 55 and rsi_4h > 50:
        if atr_pct > 1.5:
            return 'BULL_TREND'
        else:
            return 'BULL_TREND'  # 低波动牛市也是BULL_TREND
    elif bull_ema and rsi_1d < 45 and rsi_4h < 45:
        return 'BEAR_RECOVERY'
    elif bull_ema and (rsi_4h < 40 or rsi_1d < 40):
        return 'BEAR_RECOVERY'
    elif not bear_ema and 40 <= rsi_4h <= 60 and 40 <= rsi_1d <= 60:
        return 'CHOP_MID'
    elif bear_ema and rsi_1d < 40 and rsi_4h < 40:
        return 'BEAR_TREND'
    elif bear_ema and rsi_1d > 55:
        return 'BEAR_EARLY'
    elif bear_ema:
        return 'BEAR_TREND'
    else:
        return 'CHOP_MID'

def label_symbol(symbol):
    print(f'\n[{symbol}] 体制标注中...')

    # 加载日线（用于rsi_1d, ema_1d）
    d1_path = DATA_DIR / f'{symbol}_1d.jsonl.gz'
    h4_path = DATA_DIR / f'{symbol}_4h.jsonl.gz'
    h1_path = DATA_DIR / f'{symbol}_1h.jsonl.gz'
    m15_path = DATA_DIR / f'{symbol}_15m.jsonl.gz'

    if not d1_path.exists() or not h4_path.exists():
        print(f'  ⚠️ 缺少数据文件，跳过')
        return

    d1 = load_jsonl_gz(d1_path)
    h4 = load_jsonl_gz(h4_path)

    # 日线指标
    d1_closes = [r['c'] for r in d1]
    d1_highs  = [r['h'] for r in d1]
    d1_lows   = [r['l'] for r in d1]
    d1_ema20  = ema_series(d1_closes, 20)
    d1_ema50  = ema_series(d1_closes, 50)
    d1_ema200 = ema_series(d1_closes, 200)
    d1_rsi    = rsi_series(d1_closes, 14)
    d1_atr    = atr_series(d1_highs, d1_lows, d1_closes, 14)

    # 4H指标
    h4_closes = [r['c'] for r in h4]
    h4_highs  = [r['h'] for r in h4]
    h4_lows   = [r['l'] for r in h4]
    h4_rsi    = rsi_series(h4_closes, 14)
    h4_atr    = atr_series(h4_highs, h4_lows, h4_closes, 14)

    # 建立时间戳索引（日线/4H）
    d1_idx = {r['ts']: i for i,r in enumerate(d1)}
    h4_idx = {r['ts']: i for i,r in enumerate(h4)}

    def find_closest_before(idx_dict, ts):
        """找到 <= ts 的最近索引"""
        best = None
        for k in idx_dict:
            if k <= ts:
                if best is None or k > best:
                    best = k
        return idx_dict.get(best)

    # 对4H K线进行标注（主要标注层）
    labels = []
    for i, bar in enumerate(h4):
        ts = bar['ts']
        # 找最近日线数据
        d1_i = find_closest_before(d1_idx, ts)
        if d1_i is None or d1_i < 14:
            continue

        rsi1d = d1_rsi[d1_i]
        ema20_d = d1_ema20[d1_i]
        ema50_d = d1_ema50[d1_i]
        ema200_d = d1_ema200[d1_i]
        atr_d_pct = d1_atr[d1_i] / d1_closes[d1_i] * 100

        rsi4h = h4_rsi[i] if i < len(h4_rsi) else 50
        atr4h_pct = h4_atr[i] / h4_closes[i] * 100 if i < len(h4_atr) and h4_closes[i] > 0 else 0

        regime = classify_regime(rsi4h, rsi1d, ema20_d, ema50_d, ema200_d, bar['c'], atr4h_pct)

        # PD Zone（基于过去50日区间）
        lookback = 50
        recent_highs = [d1['h'] for d1 in [d1[max(0,d1_i-lookback):d1_i+1]][0]]
        recent_lows  = [d1['l'] for d1 in [d1[max(0,d1_i-lookback):d1_i+1]][0]]
        if recent_highs and recent_lows:
            rng_h = max(recent_highs); rng_l = min(recent_lows)
            rng = rng_h - rng_l
            pos = (bar['c'] - rng_l) / rng * 100 if rng > 0 else 50
            pd_zone = 'PREMIUM' if pos > 70 else ('DISCOUNT' if pos < 30 else 'NEUTRAL')
        else:
            pd_zone = 'NEUTRAL'
            pos = 50

        labels.append({
            'ts':       ts,
            'dt':       datetime.utcfromtimestamp(ts/1000).strftime('%Y-%m-%d %H:%M'),
            'c':        bar['c'],
            'regime':   regime,
            'rsi_4h':   round(rsi4h, 1),
            'rsi_1d':   round(rsi1d, 1),
            'ema20_d':  round(ema20_d, 2),
            'ema50_d':  round(ema50_d, 2),
            'ema200_d': round(ema200_d, 2),
            'atr_4h_pct': round(atr4h_pct, 3),
            'pd_zone':  pd_zone,
            'pd_pos':   round(pos, 1),
        })

    # 统计体制分布
    from collections import Counter
    regime_dist = Counter(l['regime'] for l in labels)
    print(f'  标注完成: {len(labels)}条 4H记录')
    print(f'  体制分布:')
    for regime, cnt in sorted(regime_dist.items(), key=lambda x: -x[1]):
        pct = cnt/len(labels)*100
        print(f'    {regime}: {cnt}条 ({pct:.1f}%)')

    # 保存
    out_path = DATA_DIR / f'{symbol}_regime_labels.jsonl.gz'
    with gzip.open(out_path, 'wt') as f:
        for l in labels:
            f.write(json.dumps(l) + '\n')
    print(f'  ✅ 保存 → {out_path} ({out_path.stat().st_size//1024}KB)')

def main():
    print('=' * 60)
    print('梵天设计院 · 体制标注引擎')
    print('=' * 60)
    for sym in ['BTCUSDT', 'ETHUSDT']:
        label_symbol(sym)
    print('\n✅ 体制标注完成')

if __name__ == '__main__':
    main()
