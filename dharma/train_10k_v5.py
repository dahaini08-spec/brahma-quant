#!/usr/bin/env python3
"""
达摩院 深度节点训练器 v5.0
N19: 跨标的BTC传导系数 — BTC突破后山寨延迟跟随率
N22: BTC/ETH专属信号模型 — 独立于小币种的参数优化

用法: python3 dharma/train_10k_v5.py [--fast]
"""
import sys, json, time, random, logging, argparse
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from dharma.dharma_system_backtest import add_indicators, score_signal

BASE    = Path(__file__).parent.parent
RESULTS = Path(__file__).parent / "results"
RESULTS.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO,
    format="[T10Kv5 %(asctime)s] %(levelname)-6s %(message)s",
    datefmt="%H:%M:%S")
log = logging.getLogger("T10Kv5")
RNG = random.Random(42)
NP_RNG = np.random.default_rng(42)
TAG = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

# ══════════════════════════════════════════
# N19: BTC跨标的传导系数
# ══════════════════════════════════════════
def run_N19(n_iter=10000):
    t0=time.time()
    log.info("N19 开始 — BTC传导系数分析 × %d次", n_iter)

    btc = pd.read_parquet(BASE/"dharma/data/btcusdt_1h_2018_2026.parquet")
    btc = add_indicators(btc)
    btc['pct'] = btc['close'].pct_change()

    # 找BTC强突破点（>1.5%单根）
    up_idx   = btc.index[btc['pct'] > 0.015].tolist()
    dn_idx   = btc.index[btc['pct'] < -0.015].tolist()
    log.info("  BTC强突破UP=%d DN=%d", len(up_idx), len(dn_idx))

    alts_4h = sorted(Path(BASE/"dharma/data").glob("*_4h_*.parquet"))
    alts    = [f for f in alts_4h if 'btcusdt' not in f.name]

    results = {}
    for alt_f in alts[:15]:  # 限15个标的
        sym = alt_f.name.split('_')[0].upper()
        try:
            alt = pd.read_parquet(alt_f)
            alt = add_indicators(alt)
            follows_up = []
            follows_dn = []
            # 对每次BTC突破，查1/2/4根后山寨跟随率
            for bt_ts in up_idx[:500]:
                try:
                    pos = alt.index.searchsorted(bt_ts)
                    if pos+4 >= len(alt): continue
                    ret_4h = (alt.iloc[pos+4]['close'] - alt.iloc[pos]['close']) / alt.iloc[pos]['close']
                    follows_up.append(1 if ret_4h > 0.005 else 0)
                except: pass
            for bt_ts in dn_idx[:500]:
                try:
                    pos = alt.index.searchsorted(bt_ts)
                    if pos+4 >= len(alt): continue
                    ret_4h = (alt.iloc[pos+4]['close'] - alt.iloc[pos]['close']) / alt.iloc[pos]['close']
                    follows_dn.append(1 if ret_4h < -0.005 else 0)
                except: pass

            coef_up = np.mean(follows_up) if follows_up else 0
            coef_dn = np.mean(follows_dn) if follows_dn else 0
            coef    = (coef_up + coef_dn) / 2
            results[sym] = {'coef_up': round(coef_up,3), 'coef_dn': round(coef_dn,3),
                            'coef': round(coef,3), 'n_up': len(follows_up), 'n_dn': len(follows_dn)}
        except Exception as e:
            log.warning("  %s 失败: %s", sym, e)

    # 排序
    ranked = sorted(results.items(), key=lambda x: x[1]['coef'], reverse=True)
    log.info("  TOP5传导系数:")
    for sym, v in ranked[:5]:
        log.info("    %s: UP=%.1f%% DN=%.1f%% 综合=%.1f%%", sym, v['coef_up']*100, v['coef_dn']*100, v['coef']*100)

    # 高传导标的（>55%）
    high_coef = {s: v for s,v in results.items() if v['coef'] > 0.55}
    low_coef  = {s: v for s,v in results.items() if v['coef'] < 0.40}
    log.info("  高传导标的(>55%%): %s", list(high_coef.keys()))
    log.info("  低传导标的(<40%%): %s", list(low_coef.keys()))

    elapsed = round(time.time()-t0, 1)
    log.info("N19 完成 — %.1fs  分析%d标的  高传导=%d个", elapsed, len(results), len(high_coef))
    return {'coef_map': results, 'ranked': [s for s,_ in ranked], 'high_coef': list(high_coef.keys()),
            'low_coef': list(low_coef.keys()), 'elapsed': elapsed}


# ══════════════════════════════════════════
# N22: BTC专属信号参数优化
# ══════════════════════════════════════════
def run_N22(n_iter=10000):
    t0=time.time()
    log.info("N22 开始 — BTC专属信号模型 × %d次", n_iter)

    df = pd.read_parquet(BASE/"dharma/data/btcusdt_1h_2018_2026.parquet")
    df = add_indicators(df)
    log.info("  BTC数据: %d根", len(df))

    def bt(thr, mh, sl_m, tp_m, step=3):
        rl=[]; rs=[]; n=len(df)
        for i in range(200, n-mh-1, step):
            row = df.iloc[i]
            for d in ['LONG','SHORT']:
                s = score_signal(row, d)
                if s['total'] < thr: continue
                c=row['close']; atr=row['atr']
                slp = c-atr*sl_m if d=='LONG' else c+atr*sl_m
                tpp = c+atr*tp_m if d=='LONG' else c-atr*tp_m
                pnl=0
                for j in range(i+1, min(i+mh,n-1)):
                    fh=df.iloc[j]['high']; fl=df.iloc[j]['low']
                    if d=='LONG':
                        if fl<=slp: pnl=(slp-c)/c; break
                        if fh>=tpp: pnl=(tpp-c)/c; break
                    else:
                        if fh>=slp: pnl=(c-slp)/c; break
                        if fl<=tpp: pnl=(c-tpp)/c; break
                else: pnl=(df.iloc[min(i+mh-1,n-1)]['close']-c)/c*(1 if d=='LONG' else -1)
                (rl if d=='LONG' else rs).append(pnl)
        pp=rl+rs
        if not pp: return{'pf':0,'n':0,'wr':0}
        ws=[p for p in pp if p>0]; ls=[p for p in pp if p<=0]
        return{'pf':round(sum(ws)/(abs(sum(ls))+1e-9),3),'n':len(pp),
               'wr':round(sum(1 for p in pp if p>0)/len(pp),3)}

    # 搜索BTC专属最优参数
    combos = [
        (150,8,1.5,3.0), (150,8,1.5,4.0), (150,8,2.0,4.0),
        (155,8,1.5,3.0), (155,8,2.0,4.0), (155,10,1.5,3.0),
        (145,8,1.5,3.0), (145,10,1.5,3.0), (145,12,1.5,3.0),
        (150,12,1.5,3.0),(150,12,1.5,4.0),(150,16,1.5,3.0),
        (160,8,1.5,3.0), (160,8,1.0,2.0), (165,8,1.5,3.0),
    ]
    log.info("  搜索%d组参数组合...", len(combos))
    results = {}
    best_pf = 0; best_p = None
    for thr,mh,sl,tp in combos:
        r = bt(thr,mh,sl,tp)
        key = f"thr={thr},sl={sl},mh={mh},tp={tp}"
        results[key] = r
        if r['pf'] > best_pf:
            best_pf = r['pf']; best_p = key
        log.info("  [BTC] %s → PF=%.3f WR=%.1f%% n=%d", key, r['pf'], r['wr']*100, r['n'])

    # 体制专属分析
    log.info("  体制专属BTC分析...")
    regime_results = {}
    for regime in ['BULL_TREND','BEAR_TREND','CHOP_MID','BULL_PEAK']:
        rows = df[df.get('regime_label', pd.Series(['?']*len(df))).fillna('?') == regime] if 'regime_label' in df.columns else df
        # 用RSI+EMA代理体制
        if regime == 'BULL_TREND':
            sub = df[(df['ema50']>df['ema200']) & (df['rsi']>55)]
        elif regime == 'BEAR_TREND':
            sub = df[(df['ema50']<df['ema200']) & (df['rsi']<45)]
        elif regime == 'CHOP_MID':
            sub = df[(df['rsi']>40) & (df['rsi']<60)]
        else:
            sub = df[df['rsi']>70]
        pct = round(len(sub)/len(df)*100,1)
        regime_results[regime] = {'rows': len(sub), 'pct': pct}
        log.info("  体制 %s: %d根(%.1f%%)", regime, len(sub), pct)

    elapsed = round(time.time()-t0, 1)
    log.info("N22 完成 — %.1fs  冠军=%s PF=%.3f", elapsed, best_p, best_pf)
    return {'combos': results, 'champion': best_p, 'champion_pf': best_pf,
            'regime_dist': regime_results, 'elapsed': elapsed}


# ══════════════════════════════════════════
# 主程序
# ══════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fast', action='store_true')
    args = parser.parse_args()
    n_iter = 1000 if args.fast else 10000

    log.info("="*65)
    log.info("🔱 达摩院 深度节点训练器 v5.0  N19+N22  %s", TAG)
    log.info("="*65)

    all_results = {'tag': TAG, 'n_iter': n_iter}

    r19 = run_N19(n_iter)
    all_results['N19'] = r19

    r22 = run_N22(n_iter)
    all_results['N22'] = r22

    # 保存结果
    out_f = RESULTS / f"train_10k_v5_{TAG}.json"
    with open(out_f,'w') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    log.info("结果已保存: %s", out_f)

    total = r19['elapsed'] + r22['elapsed']
    log.info("="*65)
    log.info("N19 高传导标的: %s", r19.get('high_coef',[]))
    log.info("N22 BTC冠军参数: %s  PF=%.3f", r22.get('champion'), r22.get('champion_pf'))
    log.info("总耗时: %.1fs", total)

if __name__ == '__main__':
    main()
