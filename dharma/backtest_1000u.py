#!/usr/bin/env python3
"""
达摩院 · 1000U × 5x 8年盈利引擎 v1.0
=====================================
设计院六方辩论定稿 · 2026-06-07

规格：
  - 初始资金：1000 USDT
  - 杠杆：5x（合约永续）
  - 手续费：0.04% 双边
  - 数据：2018~2026年全量（8年）
  - 标的：BTC + ETH（第一批）
  - 方向：SHORT为主（BEAR_TREND + RSI>60）
  - 仓位管理：固定2%风险 per trade
  - 目标：年化 ≥ 30%，最大回撤 ≤ 30%

评估输出：
  - 年化收益率（CAGR）
  - 最大回撤（MDD）
  - Sharpe 比率
  - Calmar 比率
  - 逐年 PnL 分解
  - 月度盈亏热图数据

用法:
  python3 dharma/backtest_1000u.py
  python3 dharma/backtest_1000u.py --sym BTCUSDT
  python3 dharma/backtest_1000u.py --sl 1.2 --tp 2.5   # 指定参数
"""
import sys, json, time, gc, warnings, argparse, math
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

BASE    = Path(__file__).parent.parent
RESULTS = Path(__file__).parent / "results"
RESULTS.mkdir(exist_ok=True)

TAG = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

# ── 策略参数（默认值，可被 CLI 覆盖）──
INIT_CAPITAL  = 1000.0   # USDT
LEVERAGE      = 5.0
COST_RATE     = 0.0004   # 0.04% 双边
RISK_PER_TRADE = 0.02    # 2% 净值风险
MAX_CONCURRENT = 1       # 最大同时持仓（串行，不同时开多笔）

DEFAULT_SL_ATR = 1.2
DEFAULT_TP_ATR = 2.5

SYMS = ["BTCUSDT", "ETHUSDT"]


# ════════════════════════════════════════════════════════════════
# 数据加载 + 特征构建（与 train_wfv_v1 一致）
# ════════════════════════════════════════════════════════════════
def load_and_build(sym: str) -> pd.DataFrame:
    sym_l = sym.lower()
    f1h = BASE / f"dharma/data/{sym_l}_1h_2018_2026.parquet"
    f4h = BASE / f"dharma/data/{sym_l}_4h_2018_2026.parquet"
    if not f1h.exists() or not f4h.exists():
        raise FileNotFoundError(f"数据文件缺失: {f1h}")

    df1h = pd.read_parquet(f1h)
    df4h = pd.read_parquet(f4h)

    for df in [df1h, df4h]:
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)
        elif df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
        else:
            df.index = df.index.tz_convert('UTC')

    return _build_features(df1h, df4h)


def _build_features(df1h: pd.DataFrame, df4h: pd.DataFrame) -> pd.DataFrame:
    df = df1h.copy()

    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['atr'] = tr.ewm(span=14, min_periods=1).mean()

    delta = df['close'].diff()
    gain  = delta.clip(lower=0).ewm(span=14, min_periods=1).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=14, min_periods=1).mean()
    rs    = gain / loss.replace(0, np.nan)
    df['rsi'] = (100 - 100 / (1 + rs)).fillna(50)

    df['ema200'] = df['close'].ewm(span=200, min_periods=1).mean()

    ema12 = df['close'].ewm(span=12, min_periods=1).mean()
    ema26 = df['close'].ewm(span=26, min_periods=1).mean()
    df['macd_hist'] = ema12 - ema26

    # 4H 体制
    df4 = df4h.copy()
    e200_4 = df4['close'].ewm(span=200, min_periods=1).mean()
    d4     = df4['close'].diff()
    g4     = d4.clip(lower=0).ewm(span=14, min_periods=1).mean()
    l4     = (-d4.clip(upper=0)).ewm(span=14, min_periods=1).mean()
    rsi4   = (100 - 100 / (1 + g4 / l4.replace(0, np.nan))).fillna(50)
    mc4    = df4['close']
    mb     = mc4 < e200_4 * 0.95
    ch     = mc4.between(e200_4 * 0.95, e200_4 * 1.05)

    r4 = pd.Series('CHOP_MID', index=df4.index, dtype=object)
    r4[mc4 < e200_4 * 0.88]                           = 'BEAR_CRASH'
    r4[mb & (rsi4 < 42)]                               = 'BEAR_TREND'
    r4[mb & rsi4.between(42, 55)]                      = 'BEAR_EARLY'
    r4[mb & (rsi4 > 55)]                               = 'BEAR_RECOVERY'
    r4[ch & (rsi4 < 45)]                               = 'CHOP_LOW'
    r4[ch & rsi4.between(45, 55)]                      = 'CHOP_MID'
    r4[ch & (rsi4 > 55)]                               = 'CHOP_HIGH'
    r4[(mc4 >= e200_4 * 1.05) & (mc4 < e200_4 * 1.15)] = 'BULL_EARLY'
    r4[mc4 >= e200_4 * 1.15]                           = 'BULL_TREND'

    df['regime'] = r4.reindex(df.index, method='ffill').fillna('CHOP_MID').values
    return df.dropna(subset=['atr', 'rsi'])


# ════════════════════════════════════════════════════════════════
# 信号（与 WFV 一致，最优组合）
# ════════════════════════════════════════════════════════════════
BEAR_REGIMES = {'BEAR_TREND', 'BEAR_EARLY', 'BEAR_RECOVERY', 'CHOP_LOW'}
BULL_REGIMES = {'BULL_TREND', 'BULL_EARLY', 'CHOP_HIGH'}


def gen_signals(df: pd.DataFrame, direction: str) -> pd.Series:
    if direction == 'SHORT':
        return (
            df['regime'].isin(BEAR_REGIMES) &
            (df['rsi'] > 60) &
            (df['close'] < df['ema200'])
        )
    else:
        return (
            df['regime'].isin(BULL_REGIMES) &
            (df['rsi'] < 40) &
            (df['close'] > df['ema200'])
        )


# ════════════════════════════════════════════════════════════════
# 资金管理：固定风险仓位
# ════════════════════════════════════════════════════════════════
def calc_position_size(capital: float, entry: float, sl: float,
                       direction: str) -> float:
    """
    计算开仓数量（合约张数 in USDT notional）
    风险 = RISK_PER_TRADE × capital
    风险 = (entry - sl) / entry × notional（SHORT）
    """
    if direction == 'SHORT':
        risk_pct = (sl - entry) / entry
    else:
        risk_pct = (entry - sl) / entry

    if risk_pct <= 0:
        return 0.0
    notional = (capital * RISK_PER_TRADE) / risk_pct
    # 杠杆限制：保证金 ≤ capital × 50%（防爆仓）
    max_notional = capital * LEVERAGE * 0.5
    return min(notional, max_notional)


# ════════════════════════════════════════════════════════════════
# 回测引擎（逐笔，含资金曲线）
# ════════════════════════════════════════════════════════════════
def run_backtest_1000u(df: pd.DataFrame, sym: str,
                       direction: str,
                       sl_atr: float, tp_atr: float,
                       hold_max: int = 48) -> dict:
    """
    完整资金曲线回测，返回详细统计
    """
    sig = gen_signals(df, direction)
    sig_idx = np.where(sig.values)[0]

    closes  = df['close'].values
    highs   = df['high'].values
    lows    = df['low'].values
    atrs    = df['atr'].values
    n       = len(df)

    capital    = INIT_CAPITAL
    peak_cap   = INIT_CAPITAL
    max_dd     = 0.0
    trades     = []
    equity_ts  = [(df.index[0], INIT_CAPITAL)]
    last_exit  = -1

    for i in sig_idx:
        if i <= last_exit:
            continue
        entry = closes[i]
        atr_i = atrs[i]
        if atr_i <= 0 or capital <= 0:
            continue

        if direction == 'SHORT':
            sl = entry + atr_i * sl_atr
            tp = entry - atr_i * tp_atr
        else:
            sl = entry - atr_i * sl_atr
            tp = entry + atr_i * tp_atr

        notional = calc_position_size(capital, entry, sl, direction)
        if notional <= 0:
            continue

        result   = 'TIMEOUT'
        pnl_pct  = 0.0
        exit_ts  = df.index[min(i + hold_max, n - 1)]
        exit_idx = min(i + hold_max, n - 1)

        for j in range(i + 1, exit_idx + 1):
            h, l = highs[j], lows[j]
            if direction == 'SHORT':
                if h >= sl:
                    result   = 'SL'
                    pnl_pct  = (entry - sl) / entry - COST_RATE
                    exit_ts  = df.index[j]
                    last_exit = j
                    break
                if l <= tp:
                    result   = 'TP'
                    pnl_pct  = (entry - tp) / entry - COST_RATE
                    exit_ts  = df.index[j]
                    last_exit = j
                    break
            else:
                if l <= sl:
                    result   = 'SL'
                    pnl_pct  = (sl - entry) / entry - COST_RATE
                    exit_ts  = df.index[j]
                    last_exit = j
                    break
                if h >= tp:
                    result   = 'TP'
                    pnl_pct  = (tp - entry) / entry - COST_RATE
                    exit_ts  = df.index[j]
                    last_exit = j
                    break
        else:
            pnl_pct  = -COST_RATE
            exit_ts  = df.index[exit_idx]

        pnl_usd  = pnl_pct * notional
        capital  = max(0.0, capital + pnl_usd)
        peak_cap = max(peak_cap, capital)
        dd       = (peak_cap - capital) / peak_cap if peak_cap > 0 else 0
        max_dd   = max(max_dd, dd)

        equity_ts.append((exit_ts, round(capital, 4)))

        trades.append({
            'entry_ts':  df.index[i],
            'exit_ts':   exit_ts,
            'regime':    df['regime'].iloc[i],
            'direction': direction,
            'result':    result,
            'pnl_pct':   round(pnl_pct * 100, 4),
            'pnl_usd':   round(pnl_usd, 4),
            'notional':  round(notional, 2),
            'capital':   round(capital, 4),
        })

    if not trades:
        return {'sym': sym, 'direction': direction, 'n': 0, 'error': 'no_trades'}

    tdf = pd.DataFrame(trades)
    wins   = tdf[tdf['result'] == 'TP']
    losses = tdf[tdf['result'] == 'SL']
    wr     = len(wins) / (len(wins) + len(losses)) if (len(wins) + len(losses)) > 0 else 0
    pf_pnl = wins['pnl_usd'].sum() / abs(losses['pnl_usd'].sum()) if len(losses) > 0 and losses['pnl_usd'].sum() != 0 else 0

    # 年化收益率（CAGR）
    years = (df.index[-1] - df.index[0]).days / 365.25
    final_cap = capital
    cagr = (final_cap / INIT_CAPITAL) ** (1 / max(years, 0.1)) - 1 if final_cap > 0 else -1

    # Sharpe（用每日收益率近似）
    eq_series = pd.Series([c for _, c in equity_ts],
                          index=pd.DatetimeIndex([t for t, _ in equity_ts]))
    daily = eq_series.resample('D').last().ffill().pct_change().dropna()
    sharpe = (daily.mean() / daily.std() * math.sqrt(252)) if daily.std() > 0 else 0

    # Calmar
    calmar = cagr / max_dd if max_dd > 0 else 0

    # 逐年分解
    yearly = {}
    for yr, grp in tdf.groupby(tdf['entry_ts'].apply(lambda x: x.year)):
        yearly[str(yr)] = {
            'n':       len(grp),
            'pnl_usd': round(grp['pnl_usd'].sum(), 2),
            'wr':      round(len(grp[grp['result'] == 'TP']) /
                             max(len(grp[grp['result'].isin(['TP', 'SL'])]), 1), 4),
        }

    # 月度热图数据
    monthly = {}
    for (yr, mo), grp in tdf.groupby([tdf['entry_ts'].apply(lambda x: x.year),
                                       tdf['entry_ts'].apply(lambda x: x.month)]):
        monthly[f"{yr}-{mo:02d}"] = round(grp['pnl_usd'].sum(), 2)

    # 最大连败
    streak = max_streak = 0
    for r in tdf['result']:
        streak = (streak + 1) if r == 'SL' else 0
        max_streak = max(max_streak, streak)

    return {
        'sym':         sym,
        'direction':   direction,
        'sl_atr':      sl_atr,
        'tp_atr':      tp_atr,
        'n':           len(trades),
        'wins':        len(wins),
        'losses':      len(losses),
        'wr':          round(wr, 4),
        'pf_pnl':      round(pf_pnl, 3),
        'init_capital': INIT_CAPITAL,
        'final_capital': round(final_cap, 2),
        'total_return_pct': round((final_cap / INIT_CAPITAL - 1) * 100, 2),
        'cagr':        round(cagr * 100, 2),
        'max_dd':      round(max_dd * 100, 2),
        'sharpe':      round(sharpe, 3),
        'calmar':      round(calmar, 3),
        'max_dd_streak': max_streak,
        'years':       round(years, 1),
        'yearly':      yearly,
        'monthly':     monthly,
        'equity_curve_last20': [(str(t), c) for t, c in equity_ts[-20:]],
    }


# ════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description='梵天 1000U×5x 8年盈利引擎 v1.0')
    ap.add_argument('--sym',  default=None,               help='只跑单标的')
    ap.add_argument('--sl',   type=float, default=DEFAULT_SL_ATR, help='SL ATR倍数')
    ap.add_argument('--tp',   type=float, default=DEFAULT_TP_ATR, help='TP ATR倍数')
    ap.add_argument('--hold', type=int,   default=48,     help='最大持仓根数（1H）')
    ap.add_argument('--dir',  default='SHORT',            help='SHORT 或 LONG 或 BOTH')
    args = ap.parse_args()

    syms = [args.sym] if args.sym else SYMS
    directions = ['SHORT', 'LONG'] if args.dir == 'BOTH' else [args.dir]
    out_path = RESULTS / f"backtest_1000u_{TAG}.json"

    all_results = []
    t0 = time.time()

    for sym in syms:
        print(f"\n{'='*55}")
        print(f"  {sym} 加载数据 ...")
        try:
            df_feat = load_and_build(sym)
        except FileNotFoundError as e:
            print(f"  ❌ {e}")
            continue
        print(f"  数据: {len(df_feat)}行 {df_feat.index[0].strftime('%Y-%m')} ~ "
              f"{df_feat.index[-1].strftime('%Y-%m')}")

        for direction in directions:
            print(f"\n  {sym} {direction} sl={args.sl}×ATR tp={args.tp}×ATR ...")
            r = run_backtest_1000u(df_feat, sym, direction,
                                   args.sl, args.tp, args.hold)
            all_results.append(r)
            _print_result(r)

        del df_feat
        gc.collect()

    elapsed = time.time() - t0

    # 保存
    out = {
        '_meta': {
            'ts':        TAG,
            'version':   'backtest_1000u_v1',
            'capital':   INIT_CAPITAL,
            'leverage':  LEVERAGE,
            'cost':      COST_RATE,
            'risk_per_trade': RISK_PER_TRADE,
            'elapsed_s': round(elapsed, 1),
        },
        'results': all_results,
    }
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(f"\n✅ 完成 耗时={elapsed:.1f}s → {out_path}")

    # 汇总
    _print_full_summary(all_results)


def _print_result(r: dict):
    if r.get('error'):
        print(f"  ❌ {r['sym']} {r['direction']}: {r['error']}")
        return
    goal_cagr = r['cagr'] >= 30
    goal_dd   = r['max_dd'] <= 30
    print(f"  {'✅' if goal_cagr and goal_dd else '⚠️'}  "
          f"总收益={r['total_return_pct']:+.1f}%  "
          f"CAGR={r['cagr']:+.1f}%  "
          f"MDD={r['max_dd']:.1f}%  "
          f"Sharpe={r['sharpe']:.2f}  "
          f"Calmar={r['calmar']:.2f}")
    print(f"     n={r['n']} W={r['wins']} L={r['losses']} "
          f"WR={r['wr']*100:.1f}% PF={r['pf_pnl']:.3f} "
          f"最大连败={r['max_dd_streak']} "
          f"最终资金=${r['final_capital']:.0f}")
    print(f"     逐年PnL: " +
          " | ".join(f"{yr}:{v['pnl_usd']:+.0f}U"
                     for yr, v in sorted(r['yearly'].items())))


def _print_full_summary(results: list):
    print("\n" + "="*55)
    print("  1000U × 5x 汇总")
    print("="*55)
    print(f"  {'标的':<12} {'方向':<6} {'CAGR%':>7} {'MDD%':>6} {'Sharpe':>7} {'PF':>6} {'达标'}")
    print(f"  {'-'*52}")
    for r in results:
        if r.get('error'):
            continue
        ok = r['cagr'] >= 30 and r['max_dd'] <= 30
        print(f"  {r['sym']:<12} {r['direction']:<6} "
              f"{r['cagr']:>+6.1f}% {r['max_dd']:>5.1f}% "
              f"{r['sharpe']:>7.2f} {r['pf_pnl']:>6.3f} "
              f"{'✅' if ok else '❌'}")
    print("="*55)
    print("  目标：CAGR≥30%  MDD≤30%")


if __name__ == '__main__':
    main()
