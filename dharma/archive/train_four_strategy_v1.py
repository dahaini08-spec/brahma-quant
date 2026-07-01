#!/usr/bin/env python3
"""
达摩院 · 四策略矩阵完整实训
============================
BTC SHORT + ETH SHORT + BTC LONG + ETH LONG
全量WFV(6窗口) + 1000U×5x回测 + 组合资金曲线

最优参数（已通过参数搜索确认）：
  BTC SHORT: SL=1.2×ATR  TP=2.5×ATR  hold=48H  RSI>60  体制=BEAR_TREND/EARLY
  ETH SHORT: SL=1.0×ATR  TP=1.6×ATR  hold=72H  RSI>60  体制=BEAR_TREND/EARLY
  BTC LONG:  SL=1.0×ATR  TP=2.5×ATR  hold=72H  RSI<35  体制=BULL_TREND/EARLY
  ETH LONG:  观察中（暂不激活）
"""
import sys, json, gc, warnings, math, itertools
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
warnings.filterwarnings('ignore')

BASE = Path('/root/.openclaw/workspace/trading-system')
RESULTS = BASE / 'dharma/results'
RESULTS.mkdir(exist_ok=True)
sys.path.insert(0, str(BASE))
from dharma.backtest_1000u import load_and_build, INIT_CAPITAL, LEVERAGE, COST_RATE, RISK_PER_TRADE

TAG = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')

WF_WINDOWS = [
    ("2020-01-01","2021-01-01"),
    ("2021-01-01","2022-01-01"),
    ("2022-01-01","2023-01-01"),
    ("2023-01-01","2024-01-01"),
    ("2024-01-01","2025-01-01"),
    ("2025-01-01","2026-06-01"),
]

# ── 四策略配置 ──
STRATEGIES = [
    dict(id='BTC_SHORT', sym='BTCUSDT', direction='SHORT',
         sl=1.0, tp=1.6, hold=72, rsi=55,
         regimes={'BEAR_TREND','BEAR_EARLY'}),
    dict(id='ETH_SHORT', sym='ETHUSDT', direction='SHORT',
         sl=1.0, tp=1.6, hold=72, rsi=60,
         regimes={'BEAR_TREND','BEAR_EARLY'}),
    dict(id='BTC_LONG', sym='BTCUSDT', direction='LONG',
         sl=1.0, tp=2.5, hold=72, rsi=35,
         regimes={'BULL_TREND','BULL_EARLY'}),
    dict(id='ETH_LONG', sym='ETHUSDT', direction='LONG',
         sl=0.8, tp=2.0, hold=72, rsi=30,
         regimes={'BULL_TREND','BULL_EARLY'}),
]


def run_single(df, cfg, capital_init=1000.0):
    """通用单策略回测，返回逐笔trades列表和资金曲线"""
    direction = cfg['direction']
    if direction == 'SHORT':
        sig = (df['regime'].isin(cfg['regimes']) &
               (df['rsi'] > cfg['rsi']) &
               (df['close'] < df['ema200']))
    else:
        sig = (df['regime'].isin(cfg['regimes']) &
               (df['rsi'] < cfg['rsi']) &
               (df['close'] > df['ema200']))

    sig_idx = np.where(sig.values)[0]
    if len(sig_idx) < 3:
        return [], []

    closes = df['close'].values; highs = df['high'].values
    lows = df['low'].values;     atrs  = df['atr'].values
    n = len(df)
    capital = capital_init; peak = capital_init; max_dd = 0.0
    trades = []; equity = [(df.index[0], capital)]; last_exit = -1

    for i in sig_idx:
        if i <= last_exit: continue
        entry = closes[i]; atr_i = atrs[i]
        if atr_i <= 0 or capital <= 0: continue
        if direction == 'SHORT':
            sl = entry + atr_i * cfg['sl'];  tp = entry - atr_i * cfg['tp']
            risk_pct = (sl - entry) / entry
        else:
            sl = entry - atr_i * cfg['sl'];  tp = entry + atr_i * cfg['tp']
            risk_pct = (entry - sl) / entry
        if risk_pct <= 0: continue
        notional = min((capital * RISK_PER_TRADE) / risk_pct, capital * LEVERAGE * 0.5)

        res = 'TO'; pnl_pct = -COST_RATE
        end_j = min(i + cfg['hold'] + 1, n)
        for j in range(i + 1, end_j):
            h, l = highs[j], lows[j]
            if direction == 'SHORT':
                if h >= sl: res='SL'; pnl_pct=(entry-sl)/entry-COST_RATE; last_exit=j; break
                if l <= tp: res='TP'; pnl_pct=(entry-tp)/entry-COST_RATE; last_exit=j; break
            else:
                if l <= sl: res='SL'; pnl_pct=(sl-entry)/entry-COST_RATE; last_exit=j; break
                if h >= tp: res='TP'; pnl_pct=(tp-entry)/entry-COST_RATE; last_exit=j; break
        else:
            last_exit = min(i + cfg['hold'], n - 1)

        pnl_usd = pnl_pct * notional
        capital = max(0.0, capital + pnl_usd)
        peak = max(peak, capital)
        dd = (peak - capital) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

        exit_ts = df.index[last_exit if last_exit > i else min(i+cfg['hold'],n-1)]
        equity.append((exit_ts, round(capital, 2)))
        trades.append({
            'ts': str(df.index[i]), 'yr': df.index[i].year,
            'regime': df['regime'].iloc[i],
            'res': res, 'pnl_pct': round(pnl_pct*100,4),
            'pnl_usd': round(pnl_usd,4),
            'capital': round(capital,2), 'max_dd': round(max_dd*100,2)
        })
    return trades, equity


def calc_stats(trades, equity, capital_init=1000.0):
    if not trades:
        return {'n':0,'error':'no_trades'}
    wins   = [t for t in trades if t['res']=='TP']
    losses = [t for t in trades if t['res']=='SL']
    wr     = len(wins)/(len(wins)+len(losses)) if (wins or losses) else 0
    pf_n   = sum(t['pnl_pct'] for t in wins)
    pf_d   = abs(sum(t['pnl_pct'] for t in losses))
    pf     = pf_n/pf_d if pf_d > 0 else 0
    final  = trades[-1]['capital']
    max_dd = max(t['max_dd'] for t in trades)
    # 年限和Sharpe（equity存在时才计算）
    years  = 1.0
    sharpe = 0.0
    if equity and len(equity) >= 2:
        try:
            eq_ts  = pd.Series([c for _,c in equity],
                               index=pd.DatetimeIndex([t for t,_ in equity]))
            years  = max((eq_ts.index[-1] - eq_ts.index[0]).days / 365.25, 0.1)
            daily  = eq_ts.resample('D').last().ffill().pct_change().dropna()
            if len(daily) > 5 and daily.std() > 0:
                sharpe = daily.mean() / daily.std() * math.sqrt(252)
        except Exception:
            pass
    cagr   = (final/capital_init)**(1/years)-1 if final>0 else -1
    calmar = (cagr*100)/max_dd if max_dd>0 else 0
    # 逐年
    yearly = {}
    for t in trades:
        yr = str(t['yr'])
        yearly.setdefault(yr, {'pnl':0,'n':0,'w':0})
        yearly[yr]['pnl'] += t['pnl_usd']
        yearly[yr]['n']   += 1
        if t['res']=='TP': yearly[yr]['w'] += 1
    for v in yearly.values():
        v['pnl'] = round(v['pnl'],2)
    # 最大连败
    streak = max_streak = 0
    for t in trades:
        streak = (streak+1) if t['res']=='SL' else 0
        max_streak = max(max_streak, streak)
    return {
        'n':len(trades), 'wins':len(wins), 'losses':len(losses),
        'wr':round(wr,4), 'pf':round(pf,3),
        'cagr':round(cagr*100,2), 'mdd':round(max_dd,2),
        'sharpe':round(sharpe,3), 'calmar':round(calmar,2),
        'final':round(final,2), 'max_streak':max_streak, 'yearly':yearly
    }


def run_wfv(df, cfg):
    passed = 0; wf_rows = []
    for w_i,(oos_s,oos_e) in enumerate(WF_WINDOWS):
        ts_s = pd.Timestamp(oos_s, tz='UTC')
        ts_e = pd.Timestamp(oos_e, tz='UTC')
        df_tr  = df[df.index < ts_s]
        df_oos = df[(df.index >= ts_s) & (df.index < ts_e)]
        if len(df_tr) < 1000 or len(df_oos) < 100:
            wf_rows.append({'w':w_i+1,'skip':True}); continue
        tr_is,  _  = run_single(df_tr, cfg)
        tr_oos, _  = run_single(df_oos, cfg)
        st_is  = calc_stats(tr_is,  [], 1000)
        st_oos = calc_stats(tr_oos, [], 1000)
        ok = (st_oos.get('pf',0) >= 1.0 and st_oos.get('n',0) >= 15)
        if ok: passed += 1
        wf_rows.append({
            'w':w_i+1, 'oos_s':oos_s[:7], 'oos_e':oos_e[:7],
            'is_pf':st_is.get('pf',0), 'is_wr':st_is.get('wr',0), 'is_n':st_is.get('n',0),
            'oos_pf':st_oos.get('pf',0), 'oos_wr':st_oos.get('wr',0), 'oos_n':st_oos.get('n',0),
            'pass':ok
        })
    return passed, wf_rows


# ════════════════════════════════════════════════════════════════
print("=" * 62)
print("  梵天达摩院 · 四策略矩阵完整实训")
print("=" * 62)

data_cache = {}
all_results = []

for cfg in STRATEGIES:
    sym = cfg['sym']
    print(f"\n{'─'*62}")
    print(f"  策略: {cfg['id']}  SL={cfg['sl']}×ATR  TP={cfg['tp']}×ATR  hold={cfg['hold']}H  RSI{'<' if cfg['direction']=='LONG' else '>'}{cfg['rsi']}")
    print(f"  体制: {sorted(cfg['regimes'])}")

    if sym not in data_cache:
        print(f"  加载 {sym} ...")
        data_cache[sym] = load_and_build(sym)
    df = data_cache[sym]

    # ── WFV ──
    print(f"  WFV 6窗口验证 ...")
    passed, wf_rows = run_wfv(df, cfg)
    wfv_ok = passed >= 4
    oos_pfs = [r['oos_pf'] for r in wf_rows if not r.get('skip')]
    avg_oos_pf = round(sum(oos_pfs)/len(oos_pfs),3) if oos_pfs else 0
    for r in wf_rows:
        if r.get('skip'):
            print(f"    W{r['w']} 跳过(数据不足)")
        else:
            flag = "✅" if r['pass'] else "❌"
            print(f"    W{r['w']} {r['oos_s']}~{r['oos_e']}: IS_PF={r['is_pf']:.3f} OOS_PF={r['oos_pf']:.3f} WR={r['oos_wr']:.3f} n={r['oos_n']} {flag}")
    print(f"  WFV: {passed}/6通过 OOS_PF均值={avg_oos_pf} {'✅ 有效' if wfv_ok else '❌ 无效'}")

    # ── 全量1000U ──
    print(f"  1000U × 5x 全量回测 ...")
    trades, equity = run_single(df, cfg)
    st = calc_stats(trades, equity)
    goal_ok = st.get('cagr',0) >= 30 and st.get('mdd',0) <= 30
    flag = "✅" if goal_ok else ("⚠️" if st.get('cagr',0)>0 else "❌")
    print(f"  {flag} CAGR={st.get('cagr',0):+.1f}%  MDD={st.get('mdd',0):.1f}%  Sharpe={st.get('sharpe',0):.2f}  Calmar={st.get('calmar',0):.2f}  PF={st.get('pf',0):.3f}  n={st.get('n',0)}")
    yearly_str = "  ".join(f"{yr}:{v['pnl']:+.0f}U(WR={v['w']}/{v['n']})"
                           for yr,v in sorted(st.get('yearly',{}).items()))
    print(f"  逐年: {yearly_str}")

    all_results.append({
        'id': cfg['id'], 'cfg': {k:list(v) if isinstance(v,set) else v for k,v in cfg.items()},
        'wfv': {'passed':passed,'total':6,'avg_oos_pf':avg_oos_pf,'pass':wfv_ok,'windows':wf_rows},
        'bt1000u': st, 'goal_pass': goal_ok and wfv_ok
    })

    gc.collect()

# ── 汇总 ──
print(f"\n{'='*62}")
print("  四策略矩阵 · 实训汇总")
print(f"{'='*62}")
print(f"  {'策略':<12} {'WFV':>6} {'CAGR%':>7} {'MDD%':>6} {'Sharpe':>7} {'PF':>6}  达标")
print(f"  {'-'*56}")
for r in all_results:
    s = r['bt1000u']
    flag = "✅" if r['goal_pass'] else ("⚠️" if s.get('cagr',0)>=20 else "❌")
    wfv_s = f"{r['wfv']['passed']}/6"
    print(f"  {r['id']:<12} {wfv_s:>6} {s.get('cagr',0):>+6.1f}% {s.get('mdd',0):>5.1f}% "
          f"{s.get('sharpe',0):>7.3f} {s.get('pf',0):>6.3f}  {flag}")
print(f"{'='*62}")
print("  目标: CAGR≥30%  MDD≤30%  WFV≥4/6")

# 保存
out_path = RESULTS / f'four_strategy_training_{TAG}.json'
out = {
    '_meta': {'ts':TAG,'version':'four_strategy_v1',
              'capital':INIT_CAPITAL,'leverage':LEVERAGE},
    'results': all_results
}
out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
print(f"\n  结果→ {out_path.name}")
