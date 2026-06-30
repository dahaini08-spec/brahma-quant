#!/usr/bin/env python3
"""
达摩院 · 组合穷举测试器 v1.0
终极训练体系 P0b

输入：structure_scanner.py 输出的结构特征向量
输出：每种信号组合的 WR / PF / 样本数 / 跨年稳定性

测试矩阵：
  结构类型 × 市场性格 × 指标共振 = 数百种组合
  找出 WR≥60% 且样本≥200 的高质量组合
"""
import json, sys, datetime, itertools
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
STRUCT_DIR = BASE / 'data' / 'dharma_structures'
OUT_DIR    = BASE / 'data' / 'dharma_combo_results'
OUT_DIR.mkdir(exist_ok=True)

# ─── 胜率判断（做空方向：未来价格下跌超过目标） ──────────────
def is_win_short(record, rr=2.0):
    """做空胜率：未来5根K线下跌 > ATR * rr"""
    target = record['atr_pct'] * rr
    return record['move_down_5'] >= target

def is_win_long(record, rr=2.0):
    target = record['atr_pct'] * rr
    return record['move_up_5'] >= target

def get_year(record):
    ts = record['ts'] / 1000
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).year

# ─── 单组合测试 ───────────────────────────────────────────
def test_combo(records, filters, direction='SHORT', min_samples=50):
    """
    filters: dict，如 {'has_ob_short':1, 'regime':'STRONG_TREND_DOWN'}
    """
    # 过滤
    matched = records
    for key, val in filters.items():
        if key == 'regime':
            matched = [r for r in matched if val in r.get('regime','')]
        elif key == 'rsi_lt':
            matched = [r for r in matched if r.get('rsi',50) < val]
        elif key == 'rsi_gt':
            matched = [r for r in matched if r.get('rsi',50) > val]
        elif key == 'vol_ratio_gt':
            matched = [r for r in matched if r.get('vol_ratio',1) > val]
        elif key == 'macd_bear':
            matched = [r for r in matched if r.get('macd',0) < r.get('macd_sig',0)]
        elif key == 'macd_bull':
            matched = [r for r in matched if r.get('macd',0) > r.get('macd_sig',0)]
        else:
            matched = [r for r in matched if r.get(key) == val]

    n = len(matched)
    if n < min_samples:
        return None

    win_fn = is_win_short if direction == 'SHORT' else is_win_long
    wins = sum(1 for r in matched if win_fn(r))
    wr = wins / n

    # 盈亏比
    win_moves  = [r['move_down_5'] if direction=='SHORT' else r['move_up_5']
                  for r in matched if win_fn(r)]
    lose_moves = [r['move_up_5'] if direction=='SHORT' else r['move_down_5']
                  for r in matched if not win_fn(r)]
    avg_win  = sum(win_moves)  / len(win_moves)  if win_moves  else 0
    avg_lose = sum(lose_moves) / len(lose_moves) if lose_moves else 0
    pf = (avg_win * wins) / (avg_lose * (n-wins)) if (avg_lose * (n-wins)) > 0 else 99

    # 跨年稳定性
    by_year = defaultdict(list)
    for r in matched:
        by_year[get_year(r)].append(win_fn(r))
    year_wrs = {}
    for yr, wins_list in by_year.items():
        if len(wins_list) >= 10:
            year_wrs[yr] = round(sum(wins_list)/len(wins_list)*100, 1)
    stable_years = sum(1 for wr_y in year_wrs.values() if wr_y >= 50)

    return {
        'n': n, 'wr': round(wr*100, 1), 'pf': round(pf, 2),
        'avg_win': round(avg_win, 3), 'avg_lose': round(avg_lose, 3),
        'year_wrs': year_wrs, 'stable_years': stable_years,
        'direction': direction
    }

# ─── 穷举所有组合 ─────────────────────────────────────────
def run_combo_test(symbol, interval):
    fname = STRUCT_DIR / f'{symbol}_{interval}_structures.json'
    if not fname.exists():
        print(f'[SKIP] {fname.name} 不存在')
        return []

    print(f'\n[COMBO] {symbol} {interval}')
    records = json.load(open(fname))
    n_total = len(records)
    print(f'  加载: {n_total:,}条结构点')

    # 只测做空（当前体制BEAR_TREND）
    # 做多版本后续加
    results = []

    # ── 维度1：单一结构 ──────────────────────────────────
    single_structs = [
        ('OB空单独',     {'has_ob_short': 1}),
        ('FVG熊单独',    {'has_fvg': 1, 'fvg_type': 'BEARISH_FVG'}),
        ('BOS熊单独',    {'has_bos': 1, 'bos_type': 'BEARISH_BOS'}),
        ('CHoCH熊单独',  {'has_choch': 1}),
        ('区间高点',      {'has_range': 1}),  # range_pos>0.7 后续细化
        ('背离熊单独',    {'has_div': 1, 'div_type': 'BEARISH_DIV'}),
    ]

    # ── 维度2：市场性格叠加 ──────────────────────────────
    regimes = [
        ('全市场',        {}),
        ('强趋势空',      {'regime': 'STRONG_TREND_DOWN'}),
        ('弱趋势空',      {'regime': 'WEAK_TREND_DOWN'}),
        ('宽幅震荡',      {'regime': 'RANGE_WIDE'}),
        ('窄幅压缩',      {'regime': 'RANGE_TIGHT'}),
    ]

    # ── 维度3：指标共振 ──────────────────────────────────
    indicators = [
        ('无共振',   {}),
        ('RSI>65',  {'rsi_gt': 65}),
        ('RSI>70',  {'rsi_gt': 70}),
        ('MACD空',  {'macd_bear': True}),
        ('量能放大', {'vol_ratio_gt': 1.5}),
        ('RSI+MACD',{'rsi_gt': 60, 'macd_bear': True}),
    ]

    combo_count = 0
    for s_name, s_filter in single_structs:
        for r_name, r_filter in regimes:
            for i_name, i_filter in indicators:
                filters = {**s_filter, **r_filter, **i_filter}
                combo_name = f'{s_name} | {r_name} | {i_name}'
                res = test_combo(records, filters, direction='SHORT', min_samples=30)
                combo_count += 1
                if res and res['wr'] >= 55 and res['n'] >= 50:
                    res['combo'] = combo_name
                    res['filters'] = filters
                    results.append(res)

    print(f'  测试组合: {combo_count}种')
    print(f'  WR≥55% 且 n≥50: {len(results)}种')

    # 排序
    results.sort(key=lambda x: (x['wr'], x['n']), reverse=True)

    # 展示Top10
    print(f'\n  Top10高胜率组合（做空）:')
    print(f'  {"组合":<40} {"WR":>6} {"PF":>6} {"n":>6} {"稳定年"}')
    print('  ' + '-'*70)
    for r in results[:10]:
        print(f'  {r["combo"]:<40} {r["wr"]:>5.1f}% {r["pf"]:>6.2f} {r["n"]:>6} {r["stable_years"]}年')

    return results

# ─── 主入口 ──────────────────────────────────────────────
if __name__ == '__main__':
    all_results = {}

    for symbol, interval in [('BTCUSDT','4h'),('ETHUSDT','4h'),
                               ('BTCUSDT','1h'),('ETHUSDT','1h')]:
        res = run_combo_test(symbol, interval)
        if res:
            key = f'{symbol}_{interval}'
            all_results[key] = res
            # 保存
            out_f = OUT_DIR / f'{key}_combo.json'
            json.dump(res[:100], open(out_f,'w'), ensure_ascii=False, indent=2)
            print(f'  保存Top100: {out_f.name}')

    # 全局汇总：找到跨品种都高胜率的通用组合
    print('\n\n=== 全局汇总：通用高胜率组合 ===')
    combo_scores = defaultdict(list)
    for key, res_list in all_results.items():
        for r in res_list[:20]:
            combo_scores[r['combo']].append(r['wr'])

    universal = [(name, wrs) for name, wrs in combo_scores.items() if len(wrs) >= 2]
    universal.sort(key=lambda x: sum(x[1])/len(x[1]), reverse=True)

    print(f'{"组合":<45} {"出现次数":>6} {"平均WR":>8}')
    print('-'*65)
    for name, wrs in universal[:15]:
        avg_wr = sum(wrs)/len(wrs)
        print(f'{name:<45} {len(wrs):>6} {avg_wr:>7.1f}%')

    # 保存全局汇总
    summary = [{'combo':n,'count':len(w),'avg_wr':round(sum(w)/len(w),1),'wrs':w}
               for n,w in universal]
    json.dump(summary, open(OUT_DIR/'universal_combos.json','w'),
              ensure_ascii=False, indent=2)
    print(f'\n全局汇总: data/dharma_combo_results/universal_combos.json')
