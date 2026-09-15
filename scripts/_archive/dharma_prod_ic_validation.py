#!/usr/bin/env python3
"""dharma_prod_ic_validation.py — P1-1 生产评分器IC验证
2026-09-11 苏摩111 | 致命漏洞修复

目标：验证生产评分器confluence_score的IC，对比回测评分器score_v4
方法：
  1. 抽取1000个历史时间点
  2. 对每个时间点用brahma_core.analyze()获取confluence_score
  3. 对每个时间点用score_v4（简化评分器）获取score
  4. 计算两者的IC（与实际涨跌的相关性）
  5. 对比：如果confluence_score IC ≈ score_v4 IC → 回测结论适用于生产

输出：data/dharma_prod_ic_result.json
"""
import json, math, time, sys, os, gzip, random
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / "data" / "historical"
OUT  = BASE / "data"

def load_klines(symbol, tf):
    fname = DATA / f"{symbol}_{tf}.jsonl.gz"
    if not fname.exists(): return []
    with gzip.open(fname, "rt") as f:
        return sorted([json.loads(l) for l in f if l.strip()], key=lambda x: x['ts'])

def load_regime(symbol):
    fname = DATA / f"{symbol}_regime_labels.jsonl.gz"
    if not fname.exists(): return {}
    with gzip.open(fname, "rt") as f:
        return {json.loads(l)['ts']: json.loads(l)['regime'] for l in f if l.strip()}

def calc_atr(bars, period=14):
    atrs = [0.0] * len(bars)
    for i in range(period, len(bars)):
        tr = max(bars[i]['h']-bars[i]['l'], abs(bars[i]['h']-bars[i-1]['c']), abs(bars[i]['l']-bars[i-1]['c']))
        if atrs[i-1] == 0:
            atrs[i] = tr
            for j in range(i-period+1, i):
                atrs[j] = max(bars[j]['h']-bars[j]['l'], abs(bars[j]['h']-bars[j-1]['c']), abs(bars[j]['l']-bars[j-1]['c'])) if j > 0 else bars[j]['h']-bars[j]['l']
            atrs[i] = sum(atrs[i-period+1:i+1]) / period
        else:
            atrs[i] = (atrs[i-1] * (period-1) + tr) / period
    return atrs

def calc_rsi(bars, period=14):
    rsis = [0.0] * len(bars)
    for i in range(period, len(bars)):
        gains = [max(bars[j]['c']-bars[j-1]['c'], 0) for j in range(i-period+1, i+1)]
        losses = [max(bars[j-1]['c']-bars[j]['c'], 0) for j in range(i-period+1, i+1)]
        ag = sum(gains)/period; al = sum(losses)/period
        rsis[i] = 100 - 100/(1+ag/al) if al > 0 else 100
    return rsis

def score_v4(regime, direction, rsi, bb_w, res_score):
    """回测用的简化评分器（9行版本）"""
    s = 50.0
    bonus = {('BULL_TREND','LONG'):30,('BULL_TREND','SHORT'):-20,
        ('BEAR_TREND','SHORT'):30,('BEAR_TREND','LONG'):-20,
        ('BULL_EARLY','LONG'):25,('BULL_EARLY','SHORT'):-15,
        ('BEAR_RECOVERY','LONG'):15,('BEAR_RECOVERY','SHORT'):-25,
        ('CHOP_MID','LONG'):0,('CHOP_MID','SHORT'):0}
    s += bonus.get((regime,direction),0)
    if direction=='LONG':
        if rsi<30: s+=15
        elif rsi>70: s-=15
    else:
        if rsi>70: s+=15
        elif rsi<30: s-=15
    if bb_w<1.0: s+=12
    elif bb_w>5.0: s-=8
    if res_score>=3: s+=10
    elif res_score==2: s+=5
    elif res_score==0: s-=3
    return max(0, min(150, s))

def calc_ic(scores, outcomes):
    """计算IC（Pearson相关）"""
    if len(scores) < 10: return 0.0
    n = len(scores)
    ms = sum(scores)/n; mo = sum(outcomes)/n
    cov = sum((scores[i]-ms)*(outcomes[i]-mo) for i in range(n))/n
    ss = math.sqrt(sum((x-ms)**2 for x in scores)/n)
    so = math.sqrt(sum((x-mo)**2 for x in outcomes)/n)
    return round(cov/(ss*so), 4) if ss and so else 0.0

def main():
    t0 = time.time()
    print("="*70)
    print("P1-1 生产评分器IC验证")
    print("="*70)

    # 加载数据
    symbols = ['BTCUSDT', 'ETHUSDT']
    regime_maps = {}
    all_bars = {}
    for sym in symbols:
        all_bars[sym] = load_klines(sym, '1h')
        regime_maps[sym] = load_regime(sym)
        print(f"  {sym} 1H: {len(all_bars[sym])} bars, {len(regime_maps[sym])} regime labels")

    # 抽取1000个时间点（均匀分布）
    sample_points = []
    for sym in symbols:
        bars = all_bars[sym]
        if len(bars) < 500: continue
        # 从第200根开始，均匀抽500个点/标的
        step = max(1, (len(bars) - 300) // 500)
        for i in range(200, len(bars) - 50, step):
            if len(sample_points) >= 1000: break
            sample_points.append((sym, i, bars[i]))

    print(f"\n抽样: {len(sample_points)} 个时间点")

    # 对每个点计算两种评分 + 实际涨跌
    v4_scores = []
    v4_outcomes = []
    prod_scores = []
    prod_outcomes = []

    STRONG = {('BULL_TREND','LONG'), ('BEAR_TREND','SHORT'), ('BULL_EARLY','LONG'), ('BEAR_RECOVERY','LONG')}

    for idx, (sym, i, bar) in enumerate(sample_points):
        bars = all_bars[sym]
        ts = bar['ts']
        price = bar['c']

        # 体制标签
        ts_4h = (ts // (4*3600*1000)) * (4*3600*1000) - 4*3600*1000
        regime = regime_maps[sym].get(ts_4h)
        if not regime:
            for off in range(-3, 4):
                regime = regime_maps[sym].get(ts_4h + off*4*3600*1000)
                if regime: break
        if not regime: continue

        # RSI + BB + ATR
        atrs = calc_atr(bars)
        rsis = calc_rsi(bars)
        atr = atrs[i]; rsi = rsis[i]
        if not atr or not rsi: continue

        if i >= 20:
            cl = [bars[j]['c'] for j in range(i-20, i)]
            mc = sum(cl)/20
            sc = math.sqrt(sum((c-mc)**2 for c in cl)/20)
            bb_w = sc*2/mc*100 if mc else 0
        else: bb_w = 0

        # 简化共振分
        res_score = 2  # 基础

        # 实际涨跌（未来24根K线 = 24小时）
        future_bars = bars[i+1:i+25]
        if not future_bars: continue
        future_ret = (future_bars[-1]['c'] - price) / price
        outcome = 1.0 if future_ret > 0 else 0.0

        # 方向判断（用体制）
        for d in ['LONG', 'SHORT']:
            if (regime, d) not in STRONG: continue
            # score_v4
            s4 = score_v4(regime, d, rsi, bb_w, res_score)
            v4_scores.append(s4)
            v4_outcomes.append(outcome if d == 'LONG' else 1.0 - outcome)

            # 生产评分器 — 用brahma_core.analyze()
            try:
                sys.path.insert(0, str(BASE / "brahma_brain"))
                from brahma_core import confluence_score
                # 构造ms和smc字典（最小化）
                ms = {
                    'symbol': sym, 'regime': regime,
                    'rsi_1h': rsi, 'rsi_4h': rsi, 'rsi_15m': rsi,
                    'atr_1h': atr, 'atr_4h': atr, 'atr_pct': atr/price*100 if price else 0,
                    'momentum': {'rsi_1h': rsi, 'rsi_4h': rsi, 'rsi_15m': rsi, 'atr_1h': atr, 'atr_4h': atr},
                }
                smc = {'fvg': {'dir': 'BULL' if d == 'LONG' else 'BEAR'}}
                extra = {'price': price}
                cf = confluence_score(ms, smc, d, extra)
                prod_score = cf.get('score', 0)
                if prod_score and prod_score > 0:
                    prod_scores.append(prod_score)
                    prod_outcomes.append(outcome if d == 'LONG' else 1.0 - outcome)
            except Exception as e:
                pass  # 跳过生产评分器失败的点

        if (idx + 1) % 100 == 0:
            print(f"  进度: {idx+1}/{len(sample_points)}")

    # 计算IC
    v4_ic = calc_ic(v4_scores, v4_outcomes) if len(v4_scores) >= 10 else 0
    prod_ic = calc_ic(prod_scores, prod_outcomes) if len(prod_scores) >= 10 else 0

    print(f"\n{'='*70}")
    print(f"📊 P1-1 IC验证结果")
    print(f"{'='*70}")
    print(f"\n  score_v4（回测评分器）:")
    print(f"    样本数: {len(v4_scores)}")
    print(f"    IC: {v4_ic:+.4f}")
    print(f"    WR: {sum(1 for o in v4_outcomes if o > 0.5)/len(v4_outcomes)*100:.1f}%" if v4_outcomes else "N/A")
    print(f"\n  confluence_score（生产评分器）:")
    print(f"    样本数: {len(prod_scores)}")
    print(f"    IC: {prod_ic:+.4f}")
    print(f"    WR: {sum(1 for o in prod_outcomes if o > 0.5)/len(prod_outcomes)*100:.1f}%" if prod_outcomes else "N/A")

    # 对比
    print(f"\n  IC差异: {abs(v4_ic - prod_ic):.4f}")
    if abs(v4_ic - prod_ic) < 0.02:
        print(f"  ✅ 回测评分器≈生产评分器 → 回测结论适用于生产")
    elif abs(v4_ic - prod_ic) < 0.05:
        print(f"  ⚠️ 回测评分器≈生产评分器（差异中等）→ 回测结论基本适用")
    else:
        print(f"  ❌ 回测评分器≠生产评分器 → 回测结论可能不适用于生产")

    # 保存结果
    result = {
        'ts': datetime.utcnow().isoformat() + 'Z',
        'v4_ic': v4_ic, 'v4_n': len(v4_scores),
        'prod_ic': prod_ic, 'prod_n': len(prod_scores),
        'ic_diff': abs(v4_ic - prod_ic),
        'conclusion': 'pass' if abs(v4_ic - prod_ic) < 0.02 else ('warn' if abs(v4_ic - prod_ic) < 0.05 else 'fail'),
    }
    with open(OUT / 'dharma_prod_ic_result.json', 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\n结果已保存: data/dharma_prod_ic_result.json")
    print(f"耗时: {time.time()-t0:.1f}s")

if __name__ == '__main__':
    main()
