#!/usr/bin/env python3
"""
达摩院 · 真梵天 8年大周期全能力回测 v2
设计院 · 2026-05-31

核心突破：
  - monkey-patch get_klines → parquet历史数据注入
  - 禁用 dharma_data_bridge（不写 live_signal_log）
  - 禁用 signal_queue cooldown（不影响历史回测）
  - 只在 BEAR_EARLY 体制采样（BTC主Alpha）

标的: BTC / ETH
时间: 2018~2026（8年）
初始资金: $10,000
"""
import sys, json, time, warnings
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

from dharma.historical_adapter import HistoricalAdapter
from dharma.dharma_system_backtest import add_indicators

DATA_DIR = Path(__file__).parent / "data"
RESULTS  = Path(__file__).parent / "results"
RESULTS.mkdir(exist_ok=True)

INITIAL_NAV    = 10_000.0
RISK_PER_TRADE = 0.02
MAX_POS_PCT    = 0.15
COMMISSION     = 0.0004
SLIPPAGE       = 0.0002
IS_END         = pd.Timestamp('2025-01-01', tz='UTC')

# 回测模式：禁用所有副作用
BACKTEST_MODE = True


def disable_side_effects():
    """禁用回测中不需要的副作用（写文件、cooldown等）"""
    # 禁用 dharma_data_bridge
    try:
        import dharma_data_bridge as ddb
        ddb.log_signal = lambda *a, **kw: True
    except Exception:
        pass

    # 禁用 signal_queue cooldown
    try:
        import signal_queue as sq
        def _always_accept(symbol, signal_dir, score, regime, **kw):
            return {'accepted': True, 'reason': 'backtest_mode'}
        sq.add_signal = _always_accept
    except Exception:
        pass

    # 禁用 capital_allocator（不限制仓位）
    try:
        import capital_allocator as ca
        def _always_allowed(*a, **kw):
            return {'allowed': True, 'size_usd': 200, 'reason': 'backtest_mode'}
        ca.compute = _always_allowed
    except Exception:
        pass


def calc_regime(row):
    rsi    = float(row.get('rsi', 50) or 50)
    close  = float(row['close'])
    ema200 = float(row.get('ema200', close) or close)
    if close < ema200 * 0.88: return 'BEAR_CRASH'
    if close < ema200:
        return 'BEAR_TREND' if rsi < 42 else 'BEAR_EARLY'
    if close > ema200 * 1.15: return 'BULL_TREND'
    if close > ema200 * 1.05: return 'BULL_EARLY'
    return 'CHOP_MID'


def run_backtest(sym: str) -> dict:
    print(f"\n{'='*55}")
    print(f"  🔱 {sym} 真梵天 8年回测")
    print(f"{'='*55}")

    df = pd.read_parquet(DATA_DIR / f"{sym.lower()}_1h_2018_2026.parquet")
    df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()
    df = add_indicators(df)

    # 禁用副作用
    disable_side_effects()

    # 采样点：BEAR_EARLY 体制，每 24H 一次
    sample_points = []
    for i in range(250, len(df) - 50, 24):
        regime = calc_regime(df.iloc[i])
        if regime == 'BEAR_EARLY':
            sample_points.append(i)

    print(f"  BEAR_EARLY 采样点: {len(sample_points)} 个")
    print(f"  预计耗时: ~{len(sample_points)*2.5/60:.0f} 分钟")

    nav          = INITIAL_NAV
    trades       = []
    is_trades    = []
    oos_trades   = []
    in_trade_end = 0  # 上一笔交易结束的 bar 索引

    t0 = time.time()

    for idx_pos, i in enumerate(sample_points):
        if i < in_trade_end:
            continue

        ts  = df.index[i]
        row = df.iloc[i]

        # 注入历史数据，调用真梵天
        adapter = HistoricalAdapter(sym, ts)
        adapter.inject()
        disable_side_effects()  # 每次注入后重新禁用

        try:
            from brahma_brain import analyze
            result = analyze(sym)
        except Exception as e:
            adapter.restore()
            continue
        finally:
            adapter.restore()

        sig_dir = result.get('signal_dir', 'NEUTRAL')
        regime  = result.get('regime', '')
        score   = float(result.get('score_final',
                  result.get('confluence', {}).get('total', 0)) or 0)
        price   = float(result.get('price', row['close']) or row['close'])

        # 拦截条件
        if sig_dir == 'NEUTRAL' or score <= 0:
            continue
        if (regime, sig_dir) in {('BEAR_TREND','SHORT'), ('BEAR_CRASH','LONG')}:
            continue

        # 参数
        params   = result.get('params', {})
        entry_lo = float(params.get('entry_lo', 0) or 0)
        sl_price = float(params.get('stop_loss', 0) or 0)
        tp_price = float(params.get('tp1', 0) or 0)

        if not (entry_lo and sl_price and tp_price):
            atr = adapter.get_atr_at('1h')
            if not atr or atr <= 0:
                continue
            if sig_dir == 'SHORT':
                entry_lo = price + atr * 0.5
                sl_price = price + atr * 2.0
                tp_price = price - atr * 2.0 * 3.0
            else:
                entry_lo = price - atr * 0.5
                sl_price = price - atr * 2.0
                tp_price = price + atr * 2.0 * 3.0

        # 仓位
        sl_dist = abs(sl_price - price)
        if sl_dist <= 0:
            continue
        pos_size  = (nav * RISK_PER_TRADE) / sl_dist
        if pos_size * price > nav * MAX_POS_PCT:
            pos_size = (nav * MAX_POS_PCT) / price
        if pos_size * price < 10:
            continue

        entry_exec = price * (1 + SLIPPAGE if sig_dir == 'SHORT' else 1 - SLIPPAGE)
        fee_open   = pos_size * entry_exec * COMMISSION

        # 模拟持仓（最多 hold 48 根 1H K）
        outcome    = 'TIMEOUT'
        hold       = 48
        exit_price = float(df.iloc[min(i + hold, len(df)-1)]['close'])
        exit_bar   = min(i + hold, len(df)-1)

        for j in range(1, hold + 1):
            if i + j >= len(df): break
            bar = df.iloc[i + j]
            hi, lo = float(bar['high']), float(bar['low'])
            if sig_dir == 'SHORT':
                if hi >= sl_price:
                    outcome = 'SL'; exit_price = sl_price; exit_bar = i+j; break
                if lo <= tp_price:
                    outcome = 'TP1'; exit_price = tp_price; exit_bar = i+j; break
            else:
                if lo <= sl_price:
                    outcome = 'SL'; exit_price = sl_price; exit_bar = i+j; break
                if hi >= tp_price:
                    outcome = 'TP1'; exit_price = tp_price; exit_bar = i+j; break

        fee_close = pos_size * exit_price * COMMISSION
        if sig_dir == 'SHORT':
            pnl = (entry_exec - exit_price) * pos_size - fee_open - fee_close
        else:
            pnl = (exit_price - entry_exec) * pos_size - fee_open - fee_close

        nav = max(nav + pnl, 1.0)
        in_trade_end = exit_bar + 1

        t = {
            'ts':      ts.isoformat(), 'sym': sym, 'dir': sig_dir,
            'regime':  regime, 'score': round(score, 1),
            'price':   round(price, 2), 'pnl': round(pnl, 4),
            'nav':     round(nav, 2), 'outcome': outcome,
            'period':  'OOS' if ts >= IS_END else 'IS',
        }
        trades.append(t)
        (oos_trades if ts >= IS_END else is_trades).append(t)

        elapsed = time.time() - t0
        pct = (idx_pos + 1) / len(sample_points) * 100
        if len(trades) % 5 == 0 or True:
            print(f"  [{pct:4.0f}%] {ts.date()} {sig_dir} score={score:.0f} "
                  f"{outcome:<8} pnl=${pnl:+.2f}  NAV=${nav:,.0f}  ({elapsed:.0f}s)", flush=True)

    def stats(tlist, label):
        if not tlist:
            return {'label': label, 'n': 0, 'wr': 0, 'pf': 0,
                    'total_pnl': 0, 'max_dd': 0}
        tp_  = [t for t in tlist if t['outcome'] == 'TP1']
        sl_  = [t for t in tlist if t['outcome'] == 'SL']
        pnls = [t['pnl'] for t in tlist]
        gains  = sum(p for p in pnls if p > 0)
        losses = abs(sum(p for p in pnls if p < 0))
        pf = round(gains / losses, 3) if losses else 0
        wr = round(len(tp_) / len(tlist) * 100, 1)
        nav_arr = np.array([t['nav'] for t in tlist])
        peak = nav_arr[0]; max_dd = 0.0
        for n in nav_arr:
            if n > peak: peak = n
            dd = (peak - n) / peak
            if dd > max_dd: max_dd = dd
        return {
            'label': label, 'n': len(tlist),
            'n_tp': len(tp_), 'n_sl': len(sl_),
            'wr': wr, 'pf': pf,
            'total_pnl': round(sum(pnls), 2),
            'max_dd': round(max_dd * 100, 1),
        }

    elapsed = time.time() - t0
    return {
        'sym': sym, 'status': 'ok',
        'initial_nav': INITIAL_NAV,
        'final_nav': round(nav, 2),
        'total_return': round((nav / INITIAL_NAV - 1) * 100, 1),
        'n_trades': len(trades),
        'elapsed_s': round(elapsed, 0),
        'ALL': stats(trades, 'ALL'),
        'IS':  stats(is_trades, 'IS'),
        'OOS': stats(oos_trades, 'OOS'),
        'trades': trades,
    }


def main():
    tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    t0  = time.time()

    print("=" * 65)
    print("🔱 真梵天 8年大周期全能力回测 v2  · 设计院达摩院")
    print("   38引擎完整运行 | BEAR_EARLY体制 | IS/OOS严格切割")
    print(f"   初始: ${INITIAL_NAV:,.0f}  风险/笔: {RISK_PER_TRADE*100:.0f}%")
    print("=" * 65)

    all_results = {'tag': tag, 'system': 'true_brahma_v6.1', 'results': {}}

    for sym in ['BTCUSDT', 'ETHUSDT']:
        r = run_backtest(sym)
        all_results['results'][sym] = r
        if r.get('status') == 'ok':
            is_ = r['IS']; oos = r['OOS']
            print(f"\n  ── {sym} 汇总 ──")
            print(f"  最终 NAV: ${r['final_nav']:,.0f}  总收益: {r['total_return']:+.1f}%  笔数: {r['n_trades']}")
            print(f"  IS  → n={is_['n']}  WR={is_['wr']:.0f}%  PF={is_['pf']:.2f}  PnL=${is_['total_pnl']:+,.0f}  MDD={is_['max_dd']:.1f}%")
            print(f"  OOS → n={oos['n']}  WR={oos['wr']:.0f}%  PF={oos['pf']:.2f}  PnL=${oos['total_pnl']:+,.0f}  MDD={oos['max_dd']:.1f}%")

    print(f"\n  总耗时: {time.time()-t0:.0f}s")

    out = RESULTS / f"backtest_true_v2_{tag}.json"
    save = json.loads(json.dumps(all_results))
    for sym in save['results']:
        save['results'][sym].pop('trades', None)
    out.write_text(json.dumps(save, ensure_ascii=False, indent=2))
    print(f"  已保存: {out.name}")

    return all_results


if __name__ == '__main__':
    main()
