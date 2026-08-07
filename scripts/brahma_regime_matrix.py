#!/usr/bin/env python3
"""
梵天设计院 · 6.5年体制矩阵复盘引擎
功能:
  1. 三种体制深度统计 (BULL/BEAR/CHOP)
  2. 关键指标 × 体制 × WR 矩阵
  3. 牛熊转换节点识别
  4. 最优入场条件提炼

封印: 2026-08-07 设计院自主
"""
import json, gzip
from collections import defaultdict
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent.parent / 'data' / 'historical'

def load_gz(path):
    rows = []
    with gzip.open(path, 'rt') as f:
        for line in f:
            rows.append(json.loads(line.strip()))
    return rows

def analyze_regime_matrix(symbol):
    label_path = DATA_DIR / f'{symbol}_regime_labels.jsonl.gz'
    if not label_path.exists():
        print(f'{symbol}: 缺少体制标注文件，先运行 brahma_regime_labeler.py')
        return

    labels = load_gz(label_path)
    print(f'\n{"="*60}')
    print(f'{symbol} · 6.5年体制矩阵分析 ({len(labels)}条4H记录)')
    print(f'{"="*60}')

    # ── 1. 体制时长分布 ──
    from collections import Counter
    regime_dist = Counter(l['regime'] for l in labels)
    print('\n【体制时长分布】')
    for regime, cnt in sorted(regime_dist.items(), key=lambda x: -x[1]):
        days = cnt * 4 / 24  # 4H × 条数 / 24 = 天数
        pct  = cnt / len(labels) * 100
        print(f'  {regime:<20} {cnt:>5}条  {days:>6.0f}天  ({pct:.1f}%)')

    # ── 2. RSI_4H 在各体制下的分布 ──
    print('\n【RSI_4H × 体制 平均值】')
    regime_rsi = defaultdict(list)
    for l in labels:
        regime_rsi[l['regime']].append(l['rsi_4h'])
    for regime in sorted(regime_rsi):
        vals = regime_rsi[regime]
        avg  = sum(vals)/len(vals)
        lo   = min(vals); hi = max(vals)
        print(f'  {regime:<20} avg={avg:.1f}  范围[{lo:.0f}~{hi:.0f}]')

    # ── 3. 牛熊转换节点 ──
    print('\n【牛熊转换节点（近20次）】')
    transitions = []
    prev = labels[0]['regime'] if labels else None
    for l in labels:
        if l['regime'] != prev:
            transitions.append({
                'from': prev, 'to': l['regime'],
                'dt': l['dt'], 'price': l['c'],
                'rsi_4h': l['rsi_4h'], 'rsi_1d': l['rsi_1d']
            })
            prev = l['regime']
    for t in transitions[-20:]:
        print(f"  {t['dt']}  {t['from']:<18} → {t['to']:<18}  \${t['price']:,.0f}  RSI4H={t['rsi_4h']}")

    # ── 4. ATR体制分析 ──
    print('\n【ATR_4H 在各体制下的特征】')
    regime_atr = defaultdict(list)
    for l in labels:
        regime_atr[l['regime']].append(l['atr_4h_pct'])
    for regime in sorted(regime_atr):
        vals = regime_atr[regime]
        avg  = sum(vals)/len(vals)
        print(f'  {regime:<20} ATR_4H均值={avg:.3f}%  最高={max(vals):.3f}%  最低={min(vals):.3f}%')

    # ── 5. PD Zone × 体制 ──
    print('\n【PD Zone × 体制 分布】')
    pd_regime = defaultdict(Counter)
    for l in labels:
        pd_regime[l['regime']][l['pd_zone']] += 1
    for regime in sorted(pd_regime):
        total = sum(pd_regime[regime].values())
        dist  = {k: f"{v/total*100:.0f}%" for k,v in pd_regime[regime].items()}
        print(f'  {regime:<20} {dist}')

    # ── 6. 关键识别规则提炼 ──
    print('\n【体制识别铁律（从6.5年提炼）】')
    # BULL_TREND 特征
    bull = [l for l in labels if l['regime']=='BULL_TREND']
    bear = [l for l in labels if l['regime']=='BEAR_TREND']
    chop = [l for l in labels if l['regime']=='CHOP_MID']

    def stats(lst, key):
        vals = [l[key] for l in lst if l.get(key) is not None]
        if not vals: return (0,0,0)
        return round(sum(vals)/len(vals),1), round(min(vals),1), round(max(vals),1)

    for name, lst in [('BULL_TREND',bull),('BEAR_TREND',bear),('CHOP_MID',chop)]:
        if not lst: continue
        r4h_avg, r4h_lo, r4h_hi = stats(lst,'rsi_4h')
        r1d_avg, r1d_lo, r1d_hi = stats(lst,'rsi_1d')
        atr_avg,_,_ = stats(lst,'atr_4h_pct')
        print(f'  {name}:')
        print(f'    RSI_4H: avg={r4h_avg}  范围[{r4h_lo}~{r4h_hi}]')
        print(f'    RSI_1D: avg={r1d_avg}  范围[{r1d_lo}~{r1d_hi}]')
        print(f'    ATR_4H: avg={atr_avg}%')

    return transitions

def main():
    for sym in ['BTCUSDT', 'ETHUSDT']:
        analyze_regime_matrix(sym)
    print('\n✅ 体制矩阵分析完成')

if __name__ == '__main__':
    main()
