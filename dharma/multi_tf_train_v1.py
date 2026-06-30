#!/usr/bin/env python3
"""
达摩院 · 多周期精准实训 multi_tf_train_v1.0
设计院 · 2026-06-12

══════════════════════════════════════════════════════════════════
【训练目标】
  BTC+ETH × 四个周期（15m / 1H / 4H / 1D）
  每个周期独立跑一版完整的体制×方向×资金费率矩阵
  最终输出：跨周期综合视角，找出每个体制下的最佳执行周期

【周期角色（顶级合约交易员视角）】
  1D  大周期体制确认层：趋势方向，持仓1周以上
      体制判断最准（噪音少），信号少但质量最高
      
  4H  战略入场层：寻找关键OB/FVG结构，持仓1~5天
      梵天主战周期，EMA结构清晰，资金费率累积效应显著
      
  1H  战术精确层：OB确认+触发，持仓12~48H
      当前brahma主要信号周期，平衡信号频率与质量
      
  15m 精确触发层：最终入场点确认，持仓4~24H
      CHoCH+wick rejection，大幅收窄止损（ATR×0.8 vs ATR×1.5）
      牺牲部分WR换取更优RR（止损小 = 更高RR）

【体制来源：统一用4H大周期】
  无论执行周期是15m/1H/4H/1D，体制都用4H EMA200+RSI判断
  保持体制分类的一致性

【关键设计差异（跨周期）】
  周期    止损倍数   止盈倍数   RR      最大持仓
  15m     0.8×ATR   1.6×ATR   2.00    24H = 96根
  1H      1.2×ATR   2.0×ATR   1.67    48H = 48根
  4H      1.5×ATR   2.5×ATR   1.67    120H = 30根
  1D      2.0×ATR   4.0×ATR   2.00    20D = 20根

【资金费率对齐】
  所有周期统一用8H资金费率，前向填充到各执行周期
══════════════════════════════════════════════════════════════════
"""

import gc, json, math, time, warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

ROOT    = Path(__file__).parent.parent
FUT_DIR = Path(__file__).parent / 'data' / 'futures'
DATA_DIR = Path(__file__).parent / 'data'
OUT_DIR = Path(__file__).parent / 'results'
OUT_DIR.mkdir(exist_ok=True)

# ── 周期配置 ─────────────────────────────────────────────────
TF_CONFIG = {
    '15m': {'sl': 0.8, 'tp': 1.6, 'hold': 96,  'rr': 2.0,  'cost': 0.0008*2},
    '1h':  {'sl': 1.2, 'tp': 2.0, 'hold': 48,  'rr': 1.667,'cost': 0.0008*2},
    '4h':  {'sl': 1.5, 'tp': 2.5, 'hold': 30,  'rr': 1.667,'cost': 0.0008*2},
    '1d':  {'sl': 2.0, 'tp': 4.0, 'hold': 20,  'rr': 2.0,  'cost': 0.0010*2},
}

LEVERAGE = 5
MIN_N    = 50

ALL_REGIMES = [
    'BULL_TREND','BULL_EARLY',
    'CHOP_HIGH','CHOP_MID','CHOP_LOW',
    'BEAR_RECOVERY','BEAR_EARLY','BEAR_TREND','BEAR_CRASH',
]
DIRECTIONS = ['LONG','SHORT']

SYMS = ['BTCUSDT','ETHUSDT']


# ══════════════════════════════════════════════════════════════
# 1. 数据加载（自动选择期货/现货回退）
# ══════════════════════════════════════════════════════════════

def load_tf_data(sym: str, tf: str) -> pd.DataFrame | None:
    """优先期货永续，回退到现货parquet"""
    sym_lo = sym.lower()

    # 期货版
    f_fut = FUT_DIR / f'{sym_lo}_{tf}_futures.parquet'
    if f_fut.exists():
        df = pd.read_parquet(f_fut)
        df.index = pd.to_datetime(df.index, utc=True)
        df['_src'] = 'futures'
        return df.sort_index()

    # 现货回退（不含资金费率）
    f_spot = DATA_DIR / f'{sym_lo}_{tf}_2018_2026.parquet'
    if f_spot.exists():
        df = pd.read_parquet(f_spot)
        df.index = pd.to_datetime(df.index, utc=True)
        df['_src'] = 'spot_fallback'
        df['fr_raw'] = 0.0
        return df.sort_index()

    # backtest/full（BTC/ETH）
    f_bt = ROOT / 'data' / 'backtest' / f'{sym}_{tf}_full.parquet'
    if f_bt.exists():
        df = pd.read_parquet(f_bt)
        df.index = pd.to_datetime(df.index, utc=True)
        df['_src'] = 'backtest_full'
        df['fr_raw'] = 0.0
        return df.sort_index()

    return None


# ══════════════════════════════════════════════════════════════
# 2. 4H体制（基于4H期货标记价格）
# ══════════════════════════════════════════════════════════════

def build_regime_4h(sym: str) -> pd.Series:
    """计算4H大周期体制，返回与时间戳对应的Series"""
    df4 = load_tf_data(sym, '4h')
    if df4 is None:
        return pd.Series(dtype=object)

    mc = df4.get('mark_close', df4['close']).astype(float)
    e200 = mc.ewm(span=200, adjust=False).mean()
    d  = mc.diff()
    ag = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    al = (-d).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    rsi = (100 - 100/(1 + ag/al.replace(0,np.nan))).fillna(50)

    r = pd.Series('CHOP_MID', index=df4.index, dtype=object)
    r[mc < e200*0.88]                                        = 'BEAR_CRASH'
    mb = (mc >= e200*0.88) & (mc < e200)
    r[mb & (rsi < 42)]                                       = 'BEAR_TREND'
    r[mb & rsi.between(42,55)]                               = 'BEAR_EARLY'
    r[mb & (rsi > 55)]                                       = 'BEAR_RECOVERY'
    ch = (mc >= e200) & (mc < e200*1.05)
    r[ch & (rsi < 45)]                                       = 'CHOP_LOW'
    r[ch & rsi.between(45,55)]                               = 'CHOP_MID'
    r[ch & (rsi > 55)]                                       = 'CHOP_HIGH'
    r[(mc >= e200*1.05) & (mc < e200*1.15)]                  = 'BULL_EARLY'
    r[mc >= e200*1.15]                                       = 'BULL_TREND'
    return r


# ══════════════════════════════════════════════════════════════
# 3. ATR计算
# ══════════════════════════════════════════════════════════════

def calc_atr(df, span=14):
    h = df['high'].astype(float)
    l = df['low'].astype(float)
    c = df['close'].astype(float)
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/span, adjust=False).mean()


# ══════════════════════════════════════════════════════════════
# 4. 向量化结算（标记价格优先）
# ══════════════════════════════════════════════════════════════

def settle_batch(df_feat, entry_idx, entries, sls, tps, directions, hold_max, cost):
    hv = df_feat.get('mark_high', df_feat['high']).values.astype(float)
    lv = df_feat.get('mark_low',  df_feat['low'] ).values.astype(float)
    cv = df_feat.get('mark_close',df_feat['close']).values.astype(float)
    n_bars = len(hv); n_tr = len(entry_idx)
    results  = np.full(n_tr,'TO',dtype=object)
    pnl_pcts = np.zeros(n_tr)

    for t in range(n_tr):
        i0=int(entry_idx[t]); e=entries[t]; sl=sls[t]; tp=tps[t]; d=directions[t]
        end=min(i0+hold_max+1, n_bars)
        for j in range(i0+1, end):
            if d=='SHORT':
                if hv[j]>=sl: results[t]='SL'; pnl_pcts[t]=(e-sl)/e-cost; break
                if lv[j]<=tp: results[t]='TP'; pnl_pcts[t]=(e-tp)/e-cost; break
            else:
                if lv[j]<=sl: results[t]='SL'; pnl_pcts[t]=(sl-e)/e-cost; break
                if hv[j]>=tp: results[t]='TP'; pnl_pcts[t]=(tp-e)/e-cost; break
        else:
            fin=cv[min(end-1,n_bars-1)]
            pnl_pcts[t]=((e-fin)/e if d=='SHORT' else (fin-e)/e)-cost
    return results, pnl_pcts


# ══════════════════════════════════════════════════════════════
# 5. 单周期回测
# ══════════════════════════════════════════════════════════════

def run_single_tf(sym: str, tf: str, regime_series: pd.Series,
                  step: int = 1) -> pd.DataFrame:
    """
    step: 采样步长（15m=4, 1h=1, 4h=1, 1d=1）
    """
    cfg    = TF_CONFIG[tf]
    sl_m   = cfg['sl']; tp_m = cfg['tp']
    hold   = cfg['hold']; cost = cfg['cost']

    df = load_tf_data(sym, tf)
    if df is None or len(df) < 500:
        return pd.DataFrame()

    df['atr']    = calc_atr(df)
    df['regime'] = regime_series.reindex(df.index, method='ffill').fillna('CHOP_MID')
    df['fr_raw'] = df.get('fr_raw', pd.Series(0.0, index=df.index)).fillna(0)

    cv   = df['close'].values.astype(float)
    atrv = df['atr'].values.astype(float)
    regm = df['regime'].values
    frv  = df['fr_raw'].values.astype(float)
    n    = len(df)
    warmup = max(200, 50)

    records = []
    i = warmup
    while i < n - hold - 2:
        rg   = regm[i]
        atr_i = float(atrv[i])
        if atr_i <= 0 or np.isnan(atr_i):
            i += step; continue
        fr_i  = float(frv[i])
        entry = float(cv[i])

        for direction in DIRECTIONS:
            sl_p = (entry + atr_i*sl_m) if direction=='SHORT' else (entry - atr_i*sl_m)
            tp_p = (entry - atr_i*tp_m) if direction=='SHORT' else (entry + atr_i*tp_m)
            records.append({
                'idx':i, 'sym':sym, 'tf':tf,
                'regime':rg, 'direction':direction,
                'fr_raw':fr_i,
                'fr_bucket': ('极正>0.2%' if fr_i>0.002 else
                              '正0.1~0.2%' if fr_i>0.001 else
                              '负<-0.1%'   if fr_i<-0.001 else '中性'),
                'entry':entry, 'sl':sl_p, 'tp':tp_p,
            })
        i += step

    if not records:
        return pd.DataFrame()

    tdf = pd.DataFrame(records)
    results, pnls = settle_batch(
        df, tdf['idx'].values, tdf['entry'].values,
        tdf['sl'].values, tdf['tp'].values, tdf['direction'].values,
        hold, cost
    )
    tdf['result']   = results
    tdf['pnl_raw']  = pnls                          # 原始%
    tdf['pnl_lev']  = (pnls * LEVERAGE * 100).round(3)  # 含杠杆%
    del df; gc.collect()
    return tdf


# ══════════════════════════════════════════════════════════════
# 6. 统计分析（PF_pnl 金额版）
# ══════════════════════════════════════════════════════════════

def wilson_ci(w, total, z=1.96):
    if total==0: return(0.,0.)
    p=w/total; d=1+z**2/total
    c=(p+z**2/(2*total))/d
    m=z*math.sqrt(p*(1-p)/total+z**2/(4*total**2))/d
    return(round(max(0,c-m),3),round(min(1,c+m),3))

def calc_stats(sub):
    """使用PF_pnl（金额）而非PF_raw（次数）"""
    w=(sub['result']=='TP').sum(); l=(sub['result']=='SL').sum(); n=w+l
    if n<5: return None
    wr   = w/n
    pnl_w = sub[sub['result']=='TP']['pnl_raw'].sum()
    pnl_l = abs(sub[sub['result']=='SL']['pnl_raw'].sum())
    pf_pnl = pnl_w/pnl_l if pnl_l>0 else (9.99 if pnl_w>0 else 0.)
    avg_pnl= sub['pnl_lev'].mean()
    ci = wilson_ci(w, n)
    verdict = ('STRONG' if pf_pnl>=1.5 else 'POSITIVE' if pf_pnl>=1.3
               else 'MARGINAL' if pf_pnl>=1.0 else 'NEGATIVE')
    return dict(n=int(n),wins=int(w),losses=int(l),
                wr=round(wr,4),pf_pnl=round(pf_pnl,3),
                avg_pnl=round(float(avg_pnl),3),
                ci=list(ci),verdict=verdict)

def analyze_tf(trades: pd.DataFrame) -> dict:
    out = {}
    # 体制×方向
    rd=[]
    for (rg,d),sub in trades.groupby(['regime','direction']):
        s=calc_stats(sub)
        if s and s['n']>=MIN_N: s['regime']=rg; s['direction']=d; rd.append(s)
    out['regime_dir'] = sorted(rd, key=lambda x:-x['n'])

    # 资金费率效果
    fr_eff=[]
    for (fr_b,rg,d),sub in trades.groupby(['fr_bucket','regime','direction']):
        s=calc_stats(sub)
        if s and s['n']>=30: s['fr_bucket']=fr_b; s['regime']=rg; s['direction']=d; fr_eff.append(s)
    out['fr_effect'] = sorted(fr_eff, key=lambda x:-x.get('pf_pnl',0))

    # 总体
    total=len(trades); w=(trades['result']=='TP').sum(); l=(trades['result']=='SL').sum()
    pnl_w=trades[trades['result']=='TP']['pnl_raw'].sum()
    pnl_l=abs(trades[trades['result']=='SL']['pnl_raw'].sum())
    out['summary'] = dict(total=int(total),settled=int(w+l),wins=int(w),losses=int(l),
                          wr=round(float(w/(w+l)) if w+l>0 else 0,4),
                          pf_pnl=round(float(pnl_w/pnl_l) if pnl_l>0 else 0,3),
                          avg_pnl_lev=round(float(trades['pnl_lev'].mean()),3))
    return out


# ══════════════════════════════════════════════════════════════
# 7. 主入口：多周期对比训练
# ══════════════════════════════════════════════════════════════

TF_STEP = {'15m':4, '1h':1, '4h':1, '1d':1}

def main():
    ts_str = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    t0     = time.time()

    print(); print('═'*68)
    print('  达摩院 · 多周期精准实训 multi_tf_train_v1.0')
    print('  BTC+ETH × 15m/1H/4H/1D  四周期独立矩阵')
    print('  体制: 4H大周期(标记价) PF: 盈亏金额(PF_pnl)  结算: 标记价H/L')
    print('═'*68)

    all_results = {}
    ICONS = {'STRONG':'✅✅','POSITIVE':'✅ ','MARGINAL':'⚠️ ','NEGATIVE':'❌ '}

    for sym in SYMS:
        print(f'\n【{sym}】计算4H体制...', flush=True)
        regime_4h = build_regime_4h(sym)
        if regime_4h.empty:
            print(f'  ⚠️ 无4H数据，跳过'); continue

        regime_dist = regime_4h.value_counts()
        print(f'  4H体制分布: ', end='')
        for r in ALL_REGIMES:
            n = regime_dist.get(r,0)
            if n: print(f'{r}={n}', end=' ')
        print()

        all_results[sym] = {}

        for tf in ['15m','1h','4h','1d']:
            t_tf = time.time()
            cfg  = TF_CONFIG[tf]
            step = TF_STEP[tf]
            print(f'\n  [{sym} {tf}] SL={cfg["sl"]}×ATR  TP={cfg["tp"]}×ATR  hold={cfg["hold"]}根', flush=True)

            trades = run_single_tf(sym, tf, regime_4h, step=step)
            if trades.empty:
                print(f'    ❌ 无数据（期货{tf}未下载？）'); continue

            res = analyze_tf(trades)
            s   = res['summary']
            elapsed_tf = time.time()-t_tf

            print(f'    总样本={s["total"]:,}  WR={s["wr"]*100:.1f}%  PF_pnl={s["pf_pnl"]:.3f}  avgPNL(5x)={s["avg_pnl_lev"]:.2f}%  {elapsed_tf:.1f}s')
            print(f'    {"体制":<20} {"方向":<7} {"n":>7} {"WR":>7} {"PF_pnl":>8}  评级')
            print(f'    '+'-'*60)
            for row in res['regime_dir']:
                if row['n'] < MIN_N: continue
                icon=ICONS.get(row['verdict'],'?')
                print(f"    {row['regime']:<20} {row['direction']:<7} {row['n']:>7,} "
                      f"{row['wr']*100:>6.1f}% {row['pf_pnl']:>8.3f}  {icon}{row['verdict']}")

            # 资金费率Top效果
            top_fr = [r for r in res['fr_effect']
                      if r['n']>=50 and r['pf_pnl']>=1.3 and r['fr_bucket']!='中性']
            if top_fr:
                print(f'    资金费率加成: ', end='')
                for r in sorted(top_fr, key=lambda x:-x['pf_pnl'])[:3]:
                    print(f"[{r['fr_bucket']} {r['regime']} {r['direction']} PF={r['pf_pnl']:.2f}]", end=' ')
                print()

            all_results[sym][tf] = {'summary':res['summary'],'regime_dir':res['regime_dir'],
                                     'fr_effect':res['fr_effect']}
            del trades; gc.collect()

    # ── 跨周期比较（BEAR_TREND SHORT，核心优势）──────────────
    print()
    print('═'*68)
    print('  跨周期比较：BEAR_TREND SHORT（核心优势）')
    print('═'*68)
    print(f'  {"周期":<6} {"sym":<8} {"n":>8} {"WR":>8} {"PF_pnl":>9} {"avgPNL/笔":>11} {"评级"}')
    print('  '+'-'*62)
    for sym in SYMS:
        for tf in ['15m','1h','4h','1d']:
            res = all_results.get(sym,{}).get(tf,{})
            for row in res.get('regime_dir',[]):
                if row['regime']=='BEAR_TREND' and row['direction']=='SHORT':
                    icon=ICONS.get(row['verdict'],'?')
                    print(f"  {tf:<6} {sym:<8} {row['n']:>8,} {row['wr']*100:>7.1f}% "
                          f"{row['pf_pnl']:>9.3f} {row['avg_pnl']:>+10.3f}%  {icon}{row['verdict']}")

    print()
    print('  跨周期比较：BULL_TREND LONG')
    print('  '+'-'*62)
    for sym in SYMS:
        for tf in ['15m','1h','4h','1d']:
            res = all_results.get(sym,{}).get(tf,{})
            for row in res.get('regime_dir',[]):
                if row['regime']=='BULL_TREND' and row['direction']=='LONG':
                    icon=ICONS.get(row['verdict'],'?')
                    print(f"  {tf:<6} {sym:<8} {row['n']:>8,} {row['wr']*100:>7.1f}% "
                          f"{row['pf_pnl']:>9.3f} {row['avg_pnl']:>+10.3f}%  {icon}{row['verdict']}")

    # ── 保存 ──────────────────────────────────────────────────
    elapsed = time.time()-t0
    def _j(o):
        if isinstance(o,np.integer): return int(o)
        if isinstance(o,np.floating): return float(o)
        raise TypeError(f'{type(o)}')

    out_path = OUT_DIR / f'multi_tf_v1_{ts_str}.json'
    out_path.write_text(json.dumps({
        '_meta':{'ts':ts_str,'version':'multi_tf_v1',
                 'syms':SYMS,'tfs':list(TF_CONFIG.keys()),
                 'regime_src':'4H标记价EMA200+RSI',
                 'pf_method':'PF_pnl(盈亏金额)',
                 'settle':'标记价H/L',
                 'tf_config':{k:{kk:vv for kk,vv in v.items()} for k,v in TF_CONFIG.items()},
                 'elapsed_s':round(elapsed,1)},
        'results': all_results,
    }, ensure_ascii=False, indent=2, default=_j))

    print()
    print(f'  ✅ 保存: {out_path.name}  总耗时{elapsed:.0f}s')
    print('═'*68)
    return all_results


if __name__ == '__main__':
    main()
