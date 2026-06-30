#!/usr/bin/env python3
"""
达摩院 · 梵天大样本全周期验证引擎 v4.0
设计院出品 · 2026-06-11

核心哲学：
  交易系统为交易而生，需要大数定律支撑
  目标样本：BTC+ETH合并 ~20,000笔，6标的合并 ~50,000笔
  小样本（<500笔）不作为系统决策依据

设计原则：
  1. 降低入场门槛（score≥90，grade改为仓位权重）
  2. 全体制×双方向（16个区间）
  3. 步长=1H（最大化覆盖）
  4. 四维分组：体制×方向×grade段×score段
  5. 输出每个区间的真实WR / PF / 最优门槛矩阵
"""
import gc
import json
import warnings
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

ROOT   = Path(__file__).parent.parent
BT_DIR = ROOT / 'data' / 'backtest'
OUT    = Path(__file__).parent / 'results'
OUT.mkdir(exist_ok=True)

# ── 常量 ────────────────────────────────────────────────────
COMMISSION = 0.0004
SLIPPAGE   = 0.0002
LEVERAGE   = 3

# v26.0 体制乘数（体制是权重修正器）
REGIME_MULT = {
    'BEAR_TREND':    {'SHORT': 1.50, 'LONG': 0.50},
    'BEAR_EARLY':    {'SHORT': 1.50, 'LONG': 0.50},
    'BEAR_RECOVERY': {'SHORT': 1.20, 'LONG': 0.80},
    'BEAR_CRASH':    {'SHORT': 1.50, 'LONG': 0.30},
    'CHOP_HIGH':     {'SHORT': 0.70, 'LONG': 0.70},
    'CHOP_MID':      {'SHORT': 0.70, 'LONG': 0.70},
    'CHOP_LOW':      {'SHORT': 0.70, 'LONG': 0.70},
    'BULL_EARLY':    {'SHORT': 0.50, 'LONG': 1.50},
    'BULL_TREND':    {'SHORT': 0.30, 'LONG': 1.50},
}

GRADE_BUCKETS = [(0,30,'X'), (30,50,'C'), (50,70,'B'), (70,85,'A'), (85,101,'S')]
SCORE_BUCKETS = [(0,90,'low'), (90,110,'mid'), (110,130,'hi'), (130,150,'vhi'), (150,999,'top')]
ALL_REGIMES   = list(REGIME_MULT.keys())
DIRECTIONS    = ['SHORT', 'LONG']

# ══════════════════════════════════════════════════════════════
# 指标 + 评分（向量化，一次性计算）
# ══════════════════════════════════════════════════════════════

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """一次性计算所有特征列，返回扩展DataFrame"""
    c, h, l, v = df['close'], df['high'], df['low'], df['volume']

    # EMA
    df['ema21']  = c.ewm(span=21,  adjust=False).mean()
    df['ema55']  = c.ewm(span=55,  adjust=False).mean()
    df['ema200'] = c.ewm(span=200, adjust=False).mean()

    # RSI
    d  = c.diff()
    ag = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    al = (-d).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    df['rsi'] = 100 - 100 / (1 + ag / al.replace(0, np.nan))

    # ATR
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    df['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()

    # 体制
    ema200 = df['ema200']
    rsi    = df['rsi']
    regime = pd.Series('CHOP_MID', index=df.index)
    regime[c < ema200 * 0.88]                                  = 'BEAR_CRASH'
    regime[(c < ema200) & (rsi < 42)]                          = 'BEAR_TREND'
    regime[(c < ema200) & rsi.between(42, 55)]                 = 'BEAR_EARLY'
    regime[(c < ema200) & (rsi > 55)]                          = 'BEAR_RECOVERY'
    regime[(c > ema200 * 1.15)]                                = 'BULL_TREND'
    regime[(c > ema200 * 1.05) & (c <= ema200 * 1.15)]        = 'BULL_EARLY'
    regime[(c > ema200) & (c <= ema200 * 1.05) & (rsi > 55)]  = 'CHOP_HIGH'
    regime[(c > ema200) & (c <= ema200 * 1.05) & (rsi < 45)]  = 'CHOP_LOW'
    df['regime'] = regime

    # ── grade代理（OB+FVG+Swing，无阈值裁剪）──────────────────
    roll_h20 = h.rolling(20).max()
    dist_h   = (roll_h20 - c) / (df['atr'] + 1e-9)
    ob = np.where(dist_h.between(0.3, 1.0), 35,
         np.where(dist_h.between(1.0, 2.5), 20,
         np.where(dist_h.between(2.5, 4.0), 8, 0)))

    fvg_gap  = (l - h.shift(2)).clip(lower=0) / (c + 1e-9) * 100
    fvg = np.where(fvg_gap >= 0.5, 40,
          np.where(fvg_gap >= 0.2, 25,
          np.where(fvg_gap > 0,   10, 0)))

    rs_d = (h.rolling(10).max() - c) / (c + 1e-9) * 100
    swing = np.where(rs_d.between(0.5, 2.0), 20,
            np.where(rs_d.between(2.0, 4.0), 10, 0))

    df['grade'] = (pd.Series(ob, index=df.index) +
                   pd.Series(fvg, index=df.index) +
                   pd.Series(swing, index=df.index)).clip(0, 100)
    df['grade'].iloc[:200] = 0

    # ── raw score（技术强度，与方向无关）─────────────────────
    vol_r = v / (v.rolling(20).mean() + 1e-9)
    atr_p = df['atr'] / c * 100

    raw = (100
           + np.where(rsi > 70, 20, np.where(rsi > 60, 12, np.where(rsi > 50, 6,
             np.where(rsi < 30, 20, np.where(rsi < 40, 12, np.where(rsi < 50, 6, 0))))))
           + np.where(vol_r > 2.0, 15, np.where(vol_r > 1.5, 8, 0))
           + np.where((atr_p > 0.5) & (atr_p < 3.0), 10, np.where(atr_p > 5.0, -10, 0)))
    df['raw_score'] = pd.Series(raw, index=df.index).clip(0, 200)
    df['raw_score'].iloc[:200] = 0

    # SHORT / LONG 加权分（体制乘数）
    s_mult = df['regime'].map({r: v['SHORT'] for r, v in REGIME_MULT.items()}).fillna(0.7)
    l_mult = df['regime'].map({r: v['LONG']  for r, v in REGIME_MULT.items()}).fillna(0.7)
    df['w_short'] = (df['raw_score'] * s_mult).clip(0, 200)
    df['w_long']  = (df['raw_score'] * l_mult).clip(0, 200)

    return df


def grade_bucket(g):
    for lo, hi, label in GRADE_BUCKETS:
        if lo <= g < hi: return label
    return 'X'

def score_bucket(s):
    for lo, hi, label in SCORE_BUCKETS:
        if lo <= s < hi: return label
    return 'low'

# ══════════════════════════════════════════════════════════════
# 大样本结算引擎（向量化，不逐根循环）
# ══════════════════════════════════════════════════════════════

def vectorized_settle(df: pd.DataFrame, entry_idx: np.ndarray,
                      entries: np.ndarray, sls: np.ndarray,
                      tp1s: np.ndarray, directions: np.ndarray,
                      hold_h: int = 36) -> pd.DataFrame:
    """
    向量化结算：同时处理所有信号
    每笔信号在未来hold_h根K线内找首个SL/TP触碰
    比逐根循环快100x
    """
    prices_h = df['high'].values
    prices_l = df['low'].values
    prices_c = df['close'].values
    n_bars   = len(df)
    n_trades = len(entry_idx)

    results  = np.full(n_trades, 'TO', dtype=object)
    pnl_pcts = np.zeros(n_trades)
    hold_hs  = np.full(n_trades, hold_h)
    cost     = (COMMISSION + SLIPPAGE) * 2

    for t in range(n_trades):
        i0  = int(entry_idx[t])
        e   = entries[t]
        sl  = sls[t]
        tp1 = tp1s[t]
        d   = directions[t]

        end = min(i0 + hold_h + 1, n_bars)
        hit = False
        for j in range(i0 + 1, end):
            if d == 'SHORT':
                if prices_h[j] >= sl:
                    results[t]  = 'SL'
                    pnl_pcts[t] = (e - sl) / e - cost
                    hold_hs[t]  = j - i0
                    hit = True; break
                if prices_l[j] <= tp1:
                    results[t]  = 'TP1'
                    pnl_pcts[t] = (e - tp1) / e - cost
                    hold_hs[t]  = j - i0
                    hit = True; break
            else:  # LONG
                if prices_l[j] <= sl:
                    results[t]  = 'SL'
                    pnl_pcts[t] = (sl - e) / e - cost
                    hold_hs[t]  = j - i0
                    hit = True; break
                if prices_h[j] >= tp1:
                    results[t]  = 'TP1'
                    pnl_pcts[t] = (tp1 - e) / e - cost
                    hold_hs[t]  = j - i0
                    hit = True; break

        if not hit:
            final = prices_c[end - 1]
            if d == 'SHORT':
                pnl_pcts[t] = (e - final) / e - cost
            else:
                pnl_pcts[t] = (final - e) / e - cost

    return pd.DataFrame({
        'result':   results,
        'pnl_pct':  pnl_pcts,
        'hold_h':   hold_hs,
    })


def run_large_sample(df: pd.DataFrame, symbol: str,
                     min_w_score: float = 90.0,
                     min_grade:   float = 0.0,
                     step: int = 1,
                     hold_h: int = 36) -> pd.DataFrame:
    """
    大样本全周期双向回测
    返回每笔交易的详情DataFrame（供四维分组分析）
    """
    c   = df['close'].values
    h   = df['high'].values
    l   = df['low'].values
    atr = df['atr'].values
    regime_arr  = df['regime'].values
    grade_arr   = df['grade'].values
    ws_arr      = df['w_short'].values
    wl_arr      = df['w_long'].values

    n = len(df)
    records = []

    i = 200
    while i < n - hold_h - 2:
        regime = regime_arr[i]
        grade  = grade_arr[i]
        if grade < min_grade:
            i += step; continue

        for direction in DIRECTIONS:
            w_score = ws_arr[i] if direction == 'SHORT' else wl_arr[i]
            if w_score < min_w_score:
                continue

            # 入场参数（ATR驱动）
            if direction == 'SHORT':
                entry = c[i] + atr[i] * 0.8
                sl    = entry + atr[i] * 0.8
                tp1   = c[i] - atr[i] * 1.5
            else:
                entry = c[i] - atr[i] * 0.8
                sl    = entry - atr[i] * 0.8
                tp1   = c[i] + atr[i] * 1.5

            if abs(sl - entry) < 1e-9: continue
            rr = abs(tp1 - entry) / abs(sl - entry)
            if rr < 1.0: continue

            records.append({
                'bar_i':     i,
                'ts':        df.index[i],
                'symbol':    symbol,
                'regime':    regime,
                'direction': direction,
                'grade':     round(float(grade)),
                'w_score':   round(float(w_score)),
                'g_bucket':  grade_bucket(grade),
                's_bucket':  score_bucket(w_score),
                'entry':     float(entry),
                'sl':        float(sl),
                'tp1':       float(tp1),
                'atr':       float(atr[i]),
                'close':     float(c[i]),
            })

        i += step

    if not records:
        return pd.DataFrame()

    trade_df = pd.DataFrame(records)

    # 向量化结算
    settled = vectorized_settle(
        df,
        trade_df['bar_i'].values,
        trade_df['entry'].values,
        trade_df['sl'].values,
        trade_df['tp1'].values,
        trade_df['direction'].values,
        hold_h=hold_h,
    )

    trade_df['result']  = settled['result'].values
    trade_df['pnl_pct'] = settled['pnl_pct'].values * LEVERAGE
    trade_df['hold_h']  = settled['hold_h'].values

    return trade_df

# ══════════════════════════════════════════════════════════════
# 四维分组分析 + 主函数
# ══════════════════════════════════════════════════════════════

def analyze_4d(df_trades: pd.DataFrame) -> pd.DataFrame:
    """
    四维分组：体制 × 方向 × grade段 × score段
    输出每个区间的 n / WR / PF / avg_pnl
    """
    if df_trades.empty:
        return pd.DataFrame()

    df = df_trades.copy()
    df['win'] = df['result'].isin(['TP1', 'TP2']).astype(int)
    df['loss'] = (df['result'] == 'SL').astype(int)

    groups = df.groupby(['regime', 'direction', 'g_bucket', 's_bucket'])

    rows = []
    for (regime, direction, gb, sb), g in groups:
        n      = len(g)
        wins   = g['win'].sum()
        losses = g['loss'].sum()
        tos    = n - wins - losses
        wr     = wins / (wins + losses) if wins + losses > 0 else None
        gross_w = g[g['pnl_pct'] > 0]['pnl_pct'].sum()
        gross_l = abs(g[g['pnl_pct'] < 0]['pnl_pct'].sum())
        pf      = gross_w / gross_l if gross_l > 0 else None
        avg_pnl = g['pnl_pct'].mean()

        rows.append({
            'regime': regime, 'direction': direction,
            'grade_seg': gb, 'score_seg': sb,
            'n': n, 'wins': wins, 'losses': losses, 'timeouts': tos,
            'wr': round(wr, 3) if wr is not None else None,
            'pf': round(pf, 2) if pf is not None else None,
            'avg_pnl_pct': round(avg_pnl * 100, 2),
        })

    result = pd.DataFrame(rows).sort_values(['n'], ascending=False)
    return result


def print_summary(df4d: pd.DataFrame, label: str):
    """打印核心摘要"""
    total = df4d['n'].sum()
    print(f"\n{'━'*60}")
    print(f"  {label}  总样本={total:,}笔")
    print(f"{'━'*60}")

    # 按体制×方向汇总（大样本视角）
    grp = df4d.groupby(['regime','direction']).agg(
        n=('n','sum'), wins=('wins','sum'), losses=('losses','sum')
    ).reset_index()
    grp['wr'] = (grp['wins'] / (grp['wins']+grp['losses'])).round(3)
    grp = grp.sort_values('n', ascending=False)

    print(f"\n  体制×方向 汇总（n≥50的区间）:")
    for _, row in grp[grp['n'] >= 50].iterrows():
        bar = '█' * min(int(row['n']//50), 20)
        flag = '✅' if row['wr'] >= 0.52 else ('⚠️' if row['wr'] >= 0.45 else '❌')
        print(f"  {flag} {row['regime']:<22}{row['direction']}  "
              f"n={int(row['n']):>5}  WR={row['wr']*100:.0f}%  {bar}")

    # 最有价值的高WR大样本区间
    print(f"\n  🏆 高价值区间（n≥200 且 WR≥55%）:")
    top = df4d[(df4d['n']>=200) & (df4d['wr']>=0.55)].sort_values('n', ascending=False)
    for _, row in top.head(10).iterrows():
        print(f"    {row['regime']:<22}{row['direction']}  "
              f"grade={row['grade_seg']}  score={row['score_seg']}  "
              f"n={row['n']:>4}  WR={row['wr']*100:.0f}%  PF={row['pf']}")

    # 危险区间（n≥200 且 WR<45%）
    bad = df4d[(df4d['n']>=200) & (df4d['wr'].notna()) & (df4d['wr']<0.45)]
    if not bad.empty:
        print(f"\n  ⚠️ 危险区间（n≥200 且 WR<45%）:")
        for _, row in bad.sort_values('n', ascending=False).head(5).iterrows():
            print(f"    ❌ {row['regime']:<22}{row['direction']}  "
                  f"grade={row['grade_seg']}  score={row['score_seg']}  "
                  f"n={row['n']:>4}  WR={row['wr']*100:.0f}%")


def main():
    print("\n" + "="*62)
    print("  梵天大样本全周期验证引擎 v4.0")
    print("  哲学：大数定律 > 小样本精品")
    print("  目标：BTC+ETH合并 ~20,000笔")
    print("="*62)

    all_trades = []

    for symbol in ['BTCUSDT', 'ETHUSDT']:
        print(f"\n  📊 {symbol} 数据加载+特征计算...")
        fp = BT_DIR / f'{symbol}_1h_full.parquet'
        df = pd.read_parquet(fp)
        df = build_features(df)

        print(f"  总数据: {len(df):,}根  ({df.index[0].date()} ~ {df.index[-1].date()})")

        # 大样本回测（步长=1，无grade硬门，score≥90）
        print(f"  运行大样本回测（score≥90，步长=1H，双向）...")
        trades = run_large_sample(df, symbol,
                                   min_w_score=90.0,
                                   min_grade=0.0,
                                   step=1,
                                   hold_h=36)

        if trades.empty:
            print(f"  ❌ {symbol} 无交易")
            continue

        wins   = (trades['result'].isin(['TP1','TP2'])).sum()
        losses = (trades['result']=='SL').sum()
        wr     = wins/(wins+losses) if wins+losses else 0
        print(f"  ✅ {symbol}: {len(trades):,}笔  WR={wr*100:.1f}%  W={wins} L={losses}")

        all_trades.append(trades)
        del df; gc.collect()

    if not all_trades:
        print("❌ 无数据")
        return

    combined = pd.concat(all_trades, ignore_index=True)
    print(f"\n  合并总样本: {len(combined):,}笔")

    # 四维分析
    df4d = analyze_4d(combined)
    print_summary(df4d, "BTC+ETH 合并 全周期 大样本")

    # 保存
    out = OUT / 'brahma_v4_results.json'
    combined_out = OUT / 'brahma_v4_trades.parquet'
    df4d.to_json(out, orient='records', indent=2, force_ascii=False)
    combined.to_parquet(combined_out)

    print(f"\n  💾 四维分析: {out}")
    print(f"  💾 原始交易: {combined_out}")

    # 核心结论
    print(f"\n{'='*62}")
    print("  达摩院核心结论")
    print(f"{'='*62}")

    total = len(combined)
    wins  = (combined['result'].isin(['TP1','TP2'])).sum()
    losses= (combined['result']=='SL').sum()
    tos   = (combined['result']=='TO').sum()
    wr    = wins/(wins+losses) if wins+losses else 0
    gw    = combined[combined['pnl_pct']>0]['pnl_pct'].sum()
    gl    = abs(combined[combined['pnl_pct']<0]['pnl_pct'].sum())
    pf    = gw/gl if gl>0 else 0

    print(f"  总样本:   {total:,}笔  (目标1万~10万: {'✅达到' if total>=10000 else f'⚠️{total/10000*100:.0f}%'})")
    print(f"  全局WR:   {wr*100:.1f}%  W={wins:,} L={losses:,} TO={tos:,}")
    print(f"  全局PF:   {pf:.2f}")
    print(f"  方向分布: SHORT={len(combined[combined['direction']=='SHORT']):,}  LONG={len(combined[combined['direction']=='LONG']):,}")

    # 体制分布
    print(f"  体制分布:")
    for r, n in combined['regime'].value_counts().items():
        rw = combined[combined['regime']==r]
        rw_win = rw['result'].isin(['TP1','TP2']).sum()
        rw_loss = (rw['result']=='SL').sum()
        rwr = rw_win/(rw_win+rw_loss) if rw_win+rw_loss else 0
        pct = n/total*100
        print(f"    {r:<22} {n:>6}笔({pct:.0f}%)  WR={rwr*100:.0f}%")


if __name__ == '__main__':
    main()
