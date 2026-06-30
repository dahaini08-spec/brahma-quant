#!/usr/bin/env python3
"""
达摩院 · 实验模块集
所有验证实验统一在此注册
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dharma.dharma_core_v2 import (
    DharmaExperiment, NodeDB, FeatureKit, StatEngine,
    YEAR_REGIME, REGIME_LABEL, SYMBOLS_20
)
from collections import defaultdict
from itertools import combinations
import statistics, math, random

# ═══════════════════════════════════════════════════════════════════
#  EXP-01  信号质量三分类 · 止损结构分析
# ═══════════════════════════════════════════════════════════════════

class Exp01_SignalQuality(DharmaExperiment):
    name = "exp01_signal_quality"
    description = "止损结构三分类：三都赢/三都输/混合 · 信号真假识别"
    version = "1.0"

    def run(self):
        nodes = self.db.nodes
        triple_win = triple_lose = fast_rev = need_space = mixed = unknown = 0
        sym_stats = defaultdict(lambda: {'tw':0,'tl':0,'total':0})

        for n in nodes:
            t = FeatureKit.get_win(n, '紧止损')
            s = FeatureKit.get_win(n, '标准')
            w = FeatureKit.get_win(n, '宽止损')
            if any(x is None for x in [t,s,w]):
                unknown += 1; continue

            sym_stats[n['_sym']]['total'] += 1
            if t==1 and s==1 and w==1:
                triple_win += 1; sym_stats[n['_sym']]['tw'] += 1
            elif t==0 and s==0 and w==0:
                triple_lose += 1; sym_stats[n['_sym']]['tl'] += 1
            elif t==1 and w==0:
                fast_rev += 1   # 快进快出才赢
            elif t==0 and w==1:
                need_space += 1 # 需要给空间
            else:
                mixed += 1

        total = triple_win+triple_lose+fast_rev+need_space+mixed
        print(f"\n  全量信号质量分布 (n={total+unknown:,}):")
        print(f"  {'三配置都赢（真信号）':16s}: {triple_win:6,}  ({triple_win/total*100:.1f}%) ✅")
        print(f"  {'三配置都输（假信号）':16s}: {triple_lose:6,}  ({triple_lose/total*100:.1f}%) ❌")
        print(f"  {'快速反转型':10s}        : {fast_rev:6,}  ({fast_rev/total*100:.1f}%)  紧止损才赢")
        print(f"  {'需要空间型':10s}        : {need_space:6,}  ({need_space/total*100:.1f}%)  宽止损才赢")
        print(f"  {'混合型':6s}            : {mixed:6,}  ({mixed/total*100:.1f}%)")

        print(f"\n  按币种 三都赢率排行:")
        sym_list = [(sym, d['tw']/d['total']*100, d['tl']/d['total']*100, d['total'])
                    for sym,d in sym_stats.items() if d['total']>50]
        sym_list.sort(key=lambda x: x[1], reverse=True)
        print(f"  {'币种':12s} {'三都赢%':7s} {'三都输%':7s} n")
        for sym,tw,tl,tot in sym_list:
            bar = "█"*int(tw/2)
            print(f"  {sym.upper():12s} {tw:5.1f}%   {tl:5.1f}%  {tot:4d}  {bar}")

        return {
            'total': total,
            'triple_win': triple_win, 'triple_win_pct': round(triple_win/total,4),
            'triple_lose': triple_lose, 'triple_lose_pct': round(triple_lose/total,4),
            'fast_rev': fast_rev, 'need_space': need_space, 'mixed': mixed,
        }


# ═══════════════════════════════════════════════════════════════════
#  EXP-02  Bootstrap胜率基准 · 全维度
# ═══════════════════════════════════════════════════════════════════

class Exp02_BootstrapBaseline(DharmaExperiment):
    name = "exp02_bootstrap_baseline"
    description = "各维度Bootstrap胜率基准 · RSI/BB/ATR/体制/方向/年份"
    version = "1.0"

    def run(self):
        nodes = self.db.nodes
        atr_idx = self.get_atr_index()
        results = {}

        # --- 整体基准 ---
        all_wins = [FeatureKit.get_win(n) for n in nodes]
        all_wins = [w for w in all_wins if w is not None]
        baseline = self.stat.bootstrap_wr(all_wins, n_iter=1000)
        print(f"\n  整体基准:")
        self.print_wr("全量信号", baseline)
        results['baseline'] = baseline

        # --- 方向 ---
        print(f"\n  方向维度:")
        for d_label, d_kw in [('做多', '多'), ('做空', '空')]:
            sub = [FeatureKit.get_win(n) for n in nodes if d_kw in n.get('方向','')]
            sub = [w for w in sub if w is not None]
            st = self.stat.bootstrap_wr(sub, n_iter=1000)
            self.print_wr(d_label, st)
            results[f'dir_{d_label}'] = st

        # --- 体制 ---
        print(f"\n  体制维度:")
        for regime, label in REGIME_LABEL.items():
            sub = [FeatureKit.get_win(n) for n in nodes if n['_regime']==regime]
            sub = [w for w in sub if w is not None]
            st = self.stat.bootstrap_wr(sub, n_iter=500)
            self.print_wr(f"{label}({regime})", st)
            results[f'regime_{regime}'] = st

        # --- 标的类型 ---
        print(f"\n  标的类型维度:")
        for ctype in ['trend','hybrid','revert']:
            sub = [FeatureKit.get_win(n) for n in nodes if n['_meta'].get('type')==ctype]
            sub = [w for w in sub if w is not None]
            st = self.stat.bootstrap_wr(sub, n_iter=1000)
            self.print_wr(f"type={ctype}", st)
            results[f'type_{ctype}'] = st

        # --- RSI区间 ---
        print(f"\n  RSI_1H区间维度:")
        rsi_bands = [('<20',0,20),('20-25',20,25),('25-30',25,30),
                     ('30-50',30,50),('50-70',50,70),('70-75',70,75),
                     ('75-80',75,80),('>80',80,100)]
        for lbl,lo,hi in rsi_bands:
            sub = []
            for n in nodes:
                r = FeatureKit.get(n,'RSI_1H')
                if r is None: continue
                if lo<=r<hi:
                    w = FeatureKit.get_win(n)
                    if w is not None: sub.append(w)
            st = self.stat.bootstrap_wr(sub, n_iter=500)
            self.print_wr(f"RSI_1H {lbl}", st)
            results[f'rsi_{lbl}'] = st

        # --- ATR分位 ---
        print(f"\n  ATR分位维度:")
        for lbl,lo,hi in [('<33%',0,0.33),('33-67%',0.33,0.67),('>67%',0.67,1.0)]:
            sub = []
            for n in nodes:
                ar = FeatureKit.atr_rank(n, atr_idx)
                w = FeatureKit.get_win(n)
                if w is not None and lo<=ar<hi: sub.append(w)
            st = self.stat.bootstrap_wr(sub, n_iter=500)
            self.print_wr(f"ATR分位 {lbl}", st)
            results[f'atr_{lbl}'] = st

        return results


# ═══════════════════════════════════════════════════════════════════
#  EXP-03  Framework-K升级版 · 多维过滤链穷举
# ═══════════════════════════════════════════════════════════════════

class Exp03_FilterChain(DharmaExperiment):
    name = "exp03_filter_chain"
    description = "复合过滤链穷举 · 当前体制2022+ · Bootstrap验证"
    version = "2.0"

    def run(self):
        nodes = self.db.filter(year_from=2022)
        atr_idx = self.get_atr_index()
        print(f"\n  当前体制(2022+)节点: {len(nodes):,}")

        # 准备特征向量
        prepared = []
        for n in nodes:
            vec = FeatureKit.extract_vector(n, atr_idx)
            if vec is None: continue
            w = FeatureKit.get_win(n)
            if w is None: continue
            prepared.append((vec, w))
        print(f"  特征完整节点: {len(prepared):,}")

        # 过滤条件库
        filters = {
            'RSI极端':       lambda v: v['rsi1h']<25 or v['rsi1h']>75,
            'RSI强超买卖':   lambda v: v['rsi1h']<30 or v['rsi1h']>70,
            '4H中性':        lambda v: 40<=v['rsi4h']<=60,
            '4H逆向做多':    lambda v: v['rsi1h']<30 and v['rsi4h']>50,
            '4H逆向做空':    lambda v: v['rsi1h']>70 and v['rsi4h']<50,
            'BB极端':        lambda v: v['bb']<0.10 or v['bb']>0.90,
            'BB强位置':      lambda v: v['bb']<0.20 or v['bb']>0.80,
            '低ATR(<33%)':   lambda v: v['atr_rank']<0.33,
            '中ATR(<67%)':   lambda v: v['atr_rank']<0.67,
            '宽BB':          lambda v: v['bb_width']>0.05,
            '排除BTC/ETH':   lambda v: v['sym'] not in ['btcusdt','ethusdt'],
            '排除趋势型':    lambda v: v['coin_type'] != 'trend',
            '均值回归标的':  lambda v: v['coin_type'] == 'revert',
        }

        # 穷举2-4个条件组合
        best = []
        fnames = list(filters.keys())
        total_combos = sum(len(list(combinations(fnames,r))) for r in range(2,5))
        checked = 0

        for r in range(2, 5):
            for combo in combinations(fnames, r):
                checked += 1
                samples = [w for vec,w in prepared
                           if all(filters[f](vec) for f in combo)]
                if len(samples) < 50: continue
                st = self.stat.bootstrap_wr(samples, n_iter=500)
                if st['ci_low'] > 0.34:
                    best.append({
                        'filters': list(combo),
                        'wr': st['mean'], 'ci_low': st['ci_low'],
                        'ci_high': st['ci_high'], 'n': st['n'], 'grade': st['grade']
                    })

        best.sort(key=lambda x: x['ci_low'], reverse=True)
        print(f"\n  穷举: {checked:,}个组合 → 有效: {len(best)}个 (CI下界>34%)")

        print(f"\n  🏆 Top15 最优过滤链:")
        print(f"  {'组合':52s} {'胜率':6s} CI下界  n")
        print("  " + "-"*80)
        for b in best[:15]:
            label = " + ".join(b['filters'])
            print(f"  {b['grade']} {label[:50]:50s} {b['wr']*100:5.1f}%  {b['ci_low']*100:5.1f}%  n={b['n']}")

        if best:
            print(f"\n  🏆 最优组合：{' + '.join(best[0]['filters'])}")
            print(f"     胜率 {best[0]['wr']*100:.1f}%  CI95=[{best[0]['ci_low']*100:.1f}%,{best[0]['ci_high']*100:.1f}%]  n={best[0]['n']}")

        return {'best_chains': best[:20], 'total_checked': checked, 'valid_count': len(best)}


# ═══════════════════════════════════════════════════════════════════
#  EXP-04  特征重要性 · 信息增益完整排序
# ═══════════════════════════════════════════════════════════════════

class Exp04_FeatureImportance(DharmaExperiment):
    name = "exp04_feature_importance"
    description = "全维度特征信息增益排序 + 最优/最差分桶胜率"
    version = "1.0"

    def run(self):
        nodes = self.db.nodes
        atr_idx = self.get_atr_index()
        prepared = []
        for n in nodes:
            vec = FeatureKit.extract_vector(n, atr_idx)
            if vec is None: continue
            w = FeatureKit.get_win(n)
            if w is None: continue
            prepared.append((vec, w))

        labels_all = [w for _,w in prepared]
        print(f"\n  有效样本: {len(prepared):,}")

        feature_defs = {
            'RSI_1H(极端/强/中)':      lambda v: '<25' if v['rsi1h']<25 else '>75' if v['rsi1h']>75 else '25-30' if v['rsi1h']<30 else '70-75' if v['rsi1h']>70 else '中性',
            'RSI_4H(强/中/弱)':        lambda v: '>65' if v['rsi4h']>65 else '<35' if v['rsi4h']<35 else '中',
            'BB位置(低/中/高)':        lambda v: '<0.1' if v['bb']<0.1 else '<0.2' if v['bb']<0.2 else '>0.9' if v['bb']>0.9 else '>0.8' if v['bb']>0.8 else '中',
            'ATR分位(低/中/高)':       lambda v: '低<33%' if v['atr_rank']<0.33 else '高>67%' if v['atr_rank']>0.67 else '中',
            'BB宽度(窄/中/宽)':        lambda v: '窄<3%' if v['bb_width']<0.03 else '宽>8%' if v['bb_width']>0.08 else '中',
            '方向':                    lambda v: v['dir'],
            '标的类型':                lambda v: v['coin_type'],
            '体制':                    lambda v: v['regime'],
            'RSI1H×4H共振':           lambda v: ('超卖+4H弱' if v['rsi1h']<30 and v['rsi4h']<35
                                                 else '超买+4H强' if v['rsi1h']>70 and v['rsi4h']>65
                                                 else '逆向' if (v['rsi1h']<30 and v['rsi4h']>55) or (v['rsi1h']>70 and v['rsi4h']<45)
                                                 else '其他'),
            'BB×ATR复合':             lambda v: ('低BB+低ATR' if v['bb']<0.2 and v['atr_rank']<0.33
                                                 else '高BB+低ATR' if v['bb']>0.8 and v['atr_rank']<0.33
                                                 else '其他'),
            'RSI×标的类型':           lambda v: f"{'极端' if v['rsi1h']<25 or v['rsi1h']>75 else '中'}+{v['coin_type']}",
            '体制×方向':              lambda v: f"{v['regime'][:8]}+{v['dir'][:2]}",
        }

        ig_results = []
        for feat_name, fn in feature_defs.items():
            splits = defaultdict(list)
            for vec, w in prepared:
                try: splits[fn(vec)].append(w)
                except: pass
            ig = FeatureKit.information_gain(labels_all, splits)
            bucket_wrs = {k: (sum(v)/len(v)*100, len(v)) for k,v in splits.items() if len(v)>=30}
            best_b = max(bucket_wrs.items(), key=lambda x:x[1][0]) if bucket_wrs else ('?',(0,0))
            worst_b = min(bucket_wrs.items(), key=lambda x:x[1][0]) if bucket_wrs else ('?',(0,0))
            ig_results.append((feat_name, ig, best_b, worst_b))

        ig_results.sort(key=lambda x: x[1], reverse=True)

        print(f"\n  特征重要性排序（信息增益）:")
        print(f"  {'特征':22s} {'IG':8s} {'最优桶→胜率(n)':28s} {'最差桶→胜率(n)'}")
        print("  " + "-"*90)
        for feat, ig, (bk,bv), (wk,wv) in ig_results:
            bar = "█" * max(1, int(ig * 8000))
            print(f"  {feat:22s} {ig:.5f} {bk:20s}→{bv[0]:4.1f}%({bv[1]:4d})  {wk}→{wv[0]:.1f}%")

        return {'features': [(f,ig) for f,ig,_,_ in ig_results]}


# ═══════════════════════════════════════════════════════════════════
#  EXP-05  双周期RSI完整枚举 + Bootstrap
# ═══════════════════════════════════════════════════════════════════

class Exp05_DualRSI(DharmaExperiment):
    name = "exp05_dual_rsi"
    description = "双周期RSI(1H×4H)完整组合空间枚举 · Bootstrap CI排序"
    version = "1.0"

    def run(self):
        nodes = self.db.nodes
        rsi1h_bands = [(0,20,'<20'),(20,25,'20-25'),(25,30,'25-30'),(30,50,'30-50'),
                       (50,70,'50-70'),(70,75,'70-75'),(75,80,'75-80'),(80,100,'>80')]
        rsi4h_bands = [(0,35,'<35'),(35,50,'35-50'),(50,65,'50-65'),(65,80,'65-80'),(80,100,'>80')]

        combo = defaultdict(lambda: {'long':[],'short':[]})
        for n in nodes:
            r1 = FeatureKit.get(n,'RSI_1H')
            r4 = FeatureKit.get(n,'RSI_4H')
            if r1 is None or r4 is None: continue
            b1 = next((l for lo,hi,l in rsi1h_bands if lo<=r1<hi), '>80')
            b4 = next((l for lo,hi,l in rsi4h_bands if lo<=r4<hi), '>80')
            key = f"{b1}|{b4}"
            d = n.get('方向','')
            w = FeatureKit.get_win(n)
            if w is None: continue
            if '多' in d: combo[key]['long'].append(w)
            elif '空' in d: combo[key]['short'].append(w)

        def rank(pairs, n_iter=500):
            results = []
            for key, samples in pairs:
                if len(samples) < 50: continue
                st = self.stat.bootstrap_wr(samples, n_iter=n_iter)
                results.append((key, st))
            return sorted(results, key=lambda x: x[1]['ci_low'], reverse=True)

        long_pairs = [(k,d['long']) for k,d in combo.items()]
        short_pairs = [(k,d['short']) for k,d in combo.items()]

        print(f"\n  做多 Top10（CI下界排序）:")
        print(f"  {'1H|4H':22s} {'胜率':6s}  CI95              n     强度")
        results_long = rank(long_pairs)
        for key, st in results_long[:10]:
            self.print_wr(key, st)

        print(f"\n  做空 Top10（CI下界排序）:")
        results_short = rank(short_pairs)
        for key, st in results_short[:10]:
            self.print_wr(key, st)

        print(f"\n  ❌ 永久禁止做多组合（CI上界<30%）:")
        for key, st in results_long:
            if st['ci_high'] < 0.30 and st['n'] >= 50:
                print(f"     {key:22s} 上界{st['ci_high']*100:.1f}%  n={st['n']}")

        return {
            'top_long': [(k, {'wr':st['mean'],'ci_low':st['ci_low'],'n':st['n']}) for k,st in results_long[:10]],
            'top_short': [(k, {'wr':st['mean'],'ci_low':st['ci_low'],'n':st['n']}) for k,st in results_short[:10]],
        }


# ═══════════════════════════════════════════════════════════════════
#  EXP-06  体制×持仓时间 最优矩阵
# ═══════════════════════════════════════════════════════════════════

class Exp06_HoldingTime(DharmaExperiment):
    name = "exp06_holding_time"
    description = "体制×信号强度×持仓时间 三维矩阵 · 最优出场时机"
    version = "1.0"

    def run(self):
        nodes = self.db.nodes
        horizons = ['1根后收益%','4根后收益%','8根后收益%','12根后收益%','24根后收益%','48根后收益%']
        h_labels  = ['1H','4H','8H','12H','24H','48H']

        def sig_strength(r1, bb):
            if r1 is None or bb is None: return 'WEAK'
            if (r1<25 or r1>75) and (bb<0.1 or bb>0.9): return 'EXTREME'
            if r1<30 or r1>70: return 'STRONG'
            return 'MODERATE'

        matrix = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        for n in nodes:
            regime = n['_regime']
            r1 = FeatureKit.get(n,'RSI_1H')
            bb = FeatureKit.get(n,'BB位置')
            ss = sig_strength(r1, bb)
            j = n.get('结局',{}).get('标准',{})
            if not j: continue
            for h,hl in zip(horizons,h_labels):
                v = j.get(h)
                if v is not None:
                    matrix[regime][ss][hl].append(float(v))

        print(f"\n  体制×信号强度 最优持仓时间:")
        print(f"  {'体制':22s} {'强度':8s} {'最优H':5s} {'均收益':8s} {'最差H':5s} 样本n")
        print("  " + "-"*65)
        results = {}
        for regime in ['BEAR_TREND','BEAR_TRANSITION','BULL_EARLY','BULL_PEAK',
                       'BEAR_CRASH','RECOVERY','BULL_ETF','CHOP_HIGH']:
            label = REGIME_LABEL.get(regime, regime)
            for ss in ['EXTREME','STRONG','MODERATE']:
                hdata = matrix[regime][ss]
                if not hdata: continue
                means = {hl: statistics.mean(v) for hl,v in hdata.items() if len(v)>=10}
                if not means: continue
                best  = max(means.items(), key=lambda x:x[1])
                worst = min(means.items(), key=lambda x:x[1])
                n_tot = sum(len(v) for v in hdata.values())
                print(f"  {label:22s} {ss:8s}  {best[0]:5s} {best[1]:+7.3f}%  {worst[0]:5s}  n={n_tot}")
                results[f"{regime}_{ss}"] = {'best_h': best[0], 'best_pnl': best[1], 'n': n_tot}

        return results


# ═══════════════════════════════════════════════════════════════════
#  EXP-07  Alpha腐烂检测 · Walk-Forward
# ═══════════════════════════════════════════════════════════════════

class Exp07_AlphaDecay(DharmaExperiment):
    name = "exp07_alpha_decay"
    description = "Alpha腐烂检测：各规律按时间窗口的有效性变化"
    version = "1.0"

    def run(self):
        nodes = self.db.nodes
        atr_idx = self.get_atr_index()
        windows = [('2018-19',['2018','2019']),('2020-21',['2020','2021']),
                   ('2022-23',['2022','2023']),('2024-26',['2024','2025','2026'])]

        strategies = {
            '黄金组合(RSI极端+4H中性+低ATR)': lambda n,ar: (
                (FeatureKit.get(n,'RSI_1H') or 50) < 25 or (FeatureKit.get(n,'RSI_1H') or 50) > 75
            ) and (40 <= (FeatureKit.get(n,'RSI_4H') or 50) <= 60) and ar < 0.33,
            'RSI极端反转':    lambda n,ar: (FeatureKit.get(n,'RSI_1H') or 50) < 25 or (FeatureKit.get(n,'RSI_1H') or 50) > 75,
            'BB极端反转':     lambda n,ar: (FeatureKit.get(n,'BB位置') or 0.5) < 0.1 or (FeatureKit.get(n,'BB位置') or 0.5) > 0.9,
            '低ATR入场':      lambda n,ar: ar < 0.33,
            '4H中性入场':     lambda n,ar: 40 <= (FeatureKit.get(n,'RSI_4H') or 50) <= 60,
        }

        print(f"\n  Alpha腐烂检测（各策略按时间窗口胜率变化）:")
        print(f"  {'策略':22s}", end='')
        for wlbl,_ in windows: print(f"  {wlbl:10s}", end='')
        print(f"  {'趋势':8s}")
        print("  " + "-"*80)

        results = {}
        for sname, sfn in strategies.items():
            row = {}
            print(f"  {sname:22s}", end='')
            prev_wr = None; trend_ok = True
            for wlbl, yrs in windows:
                samples = []
                for n in nodes:
                    if n['_year'] not in yrs: continue
                    ar = FeatureKit.atr_rank(n, atr_idx)
                    try:
                        if sfn(n, ar):
                            w = FeatureKit.get_win(n)
                            if w is not None: samples.append(w)
                    except: pass
                if not samples:
                    print(f"  {'N/A':10s}", end='')
                    row[wlbl] = None; continue
                wr = sum(samples)/len(samples)*100
                row[wlbl] = {'wr': round(wr,1), 'n': len(samples)}
                marker = '📈' if (prev_wr and wr > prev_wr+1) else '📉' if (prev_wr and wr < prev_wr-1) else '➡️'
                print(f"  {wr:5.1f}%{marker:4s}", end='')
                prev_wr = wr
            print()
            results[sname] = row

        return results


# ═══════════════════════════════════════════════════════════════════
#  EXP-08  PF利润因子分析 · 止盈革命验证
# ═══════════════════════════════════════════════════════════════════

class Exp08_ProfitFactor(DharmaExperiment):
    name = "exp08_profit_factor"
    description = "利润因子(PF)分析 · 止盈截断vs持有到底 · 胜率vs PF关系"
    version = "1.0"

    def run(self):
        nodes = self.db.nodes
        atr_idx = self.get_atr_index()
        configs = ['紧止损','标准','宽止损']
        horizons = ['1根后收益%','4根后收益%','8根后收益%','12根后收益%','24根后收益%','48根后收益%']

        print(f"\n  三止损配置 PF 对比:")
        pf_results = {}
        for cfg in configs:
            wins, losses = [], []
            for n in nodes:
                j = n.get('结局',{}).get(cfg,{})
                if not j: continue
                pnl = j.get('最大盈利%')
                res = FeatureKit.get_result(n, cfg)
                if pnl is None: continue
                if res == '目标1': wins.append(float(pnl))
                elif res == '止损': losses.append(-abs(float(j.get('最大亏损%', 1.0))))
            pf = self.stat.profit_factor(wins, losses)
            wr = len(wins)/(len(wins)+len(losses)) if wins or losses else 0
            avg_win = statistics.mean(wins) if wins else 0
            avg_loss = abs(statistics.mean(losses)) if losses else 0
            kelly = self.stat.kelly_fraction(wr, avg_win, avg_loss)
            print(f"  {cfg}: PF={pf:.3f}  胜率={wr*100:.1f}%  均盈={avg_win:.2f}%  均亏={avg_loss:.2f}%  Kelly={kelly*100:.1f}%")
            pf_results[cfg] = {'pf':round(pf,3),'wr':round(wr,4),'avg_win':round(avg_win,3),'avg_loss':round(avg_loss,3)}

        print(f"\n  持仓时间 × PF（若持有到各时间点）:")
        for h in horizons:
            wins, losses = [], []
            for n in nodes:
                pnl = FeatureKit.get_pnl(n, h, '标准')
                if pnl is None: continue
                if pnl > 0: wins.append(pnl)
                else: losses.append(pnl)
            if not wins or not losses: continue
            pf = self.stat.profit_factor(wins, losses)
            wr = len(wins)/(len(wins)+len(losses))
            avg_w = statistics.mean(wins); avg_l = abs(statistics.mean(losses))
            bar = "█" * int(pf * 5) if pf < 10 else "████████████"
            print(f"  持有至{h[:2]:4s}: PF={pf:.3f}  胜率={wr*100:.1f}%  均盈={avg_w:.2f}%  均亏={avg_l:.2f}%  {bar}")

        # 最优过滤链下的PF
        print(f"\n  最优过滤链（RSI极端+4H中性+低ATR+非趋势型）下的PF:")
        filtered_wins, filtered_losses = [], []
        for n in self.db.filter(year_from=2022):
            r1 = FeatureKit.get(n,'RSI_1H')
            r4 = FeatureKit.get(n,'RSI_4H')
            ar = FeatureKit.atr_rank(n, atr_idx)
            if r1 is None or r4 is None: continue
            if not ((r1<25 or r1>75) and (40<=r4<=60) and ar<0.33): continue
            if n['_meta'].get('type') == 'trend': continue
            for h in ['8根后收益%','24根后收益%']:
                pnl = FeatureKit.get_pnl(n, h, '标准')
                if pnl is None: continue
                if pnl > 0: filtered_wins.append(pnl)
                else: filtered_losses.append(pnl)
                break
        if filtered_wins and filtered_losses:
            pf_f = self.stat.profit_factor(filtered_wins, filtered_losses)
            wr_f = len(filtered_wins)/(len(filtered_wins)+len(filtered_losses))
            print(f"  过滤后: PF={pf_f:.3f}  胜率={wr_f*100:.1f}%  n={len(filtered_wins)+len(filtered_losses)}")

        return pf_results
