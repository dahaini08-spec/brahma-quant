#!/usr/bin/env python3
"""
dharma_2025_live_test.py v1.0 — 2025年实战数据回测
达摩院 · 设计院裁决 2026-05-31

核心设计：
  - 时间范围：2025-01-01 至今（严格OOS，无穿越）
  - 周期：15m / 1h / 4h / 1d
  - 品种：BTC / ETH / SOL / BNB / DOGE
  - 过滤器：grade≥60 + gap≥1%（铁证双过滤）
  - 信号扫描：每N根K线调用梵天大脑评分
  - 结算：TP1/SL/TIMEOUT 按体制动态持仓窗口

与旧回测的关键区别：
  - 旧版：无grade/gap过滤 → WR=30%
  - 新版：grade≥60 + gap≥1% → 预期WR≥70%
"""

import sys, json, time, warnings
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

BASE    = Path(__file__).parent.parent
RESULTS = Path(__file__).parent / 'results'
RESULTS.mkdir(exist_ok=True)

# ── 配置 ─────────────────────────────────────────────────────────
OOS_START   = pd.Timestamp('2025-01-01', tz='UTC')
SYMBOLS     = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'DOGEUSDT']
TIMEFRAMES  = ['15m', '1h', '4h', '1d']
MIN_SCORE   = 145       # S1实盘门槛
MIN_GRADE   = 60        # 铁证：grade<60 WR=29%
MIN_GAP     = 1.0       # 铁证：gap<1% WR=0%
RISK_PCT    = 0.02      # 每笔风险2%
COMMISSION  = 0.0004    # 手续费0.04%
INITIAL_NAV = 10_000.0

# 体制持仓窗口（K线根数）
HOLD_BARS = {
    '15m': {'BEAR_TREND':144,'BEAR_EARLY':96,'CHOP':64,'default':80},
    '1h':  {'BEAR_TREND':36, 'BEAR_EARLY':24,'CHOP':16,'default':20},
    '4h':  {'BEAR_TREND':9,  'BEAR_EARLY':6, 'CHOP':4, 'default':5},
    '1d':  {'BEAR_TREND':3,  'BEAR_EARLY':2, 'CHOP':1, 'default':2},
}

# 扫描间隔（每N根K线扫描一次，避免过拟合）
SCAN_EVERY = {'15m':4, '1h':1, '4h':1, '1d':1}

def _hold_bars(tf, regime):
    hb = HOLD_BARS.get(tf, HOLD_BARS['1h'])
    for key in ['BEAR_TREND','BEAR_EARLY','CHOP']:
        if key in regime:
            return hb[key]
    return hb['default']

def disable_side_effects():
    """回测模式：禁用所有写文件副作用"""
    try:
        import dharma_data_bridge as ddb
        ddb.log_signal = lambda *a, **kw: None
    except: pass
    try:
        import signal_queue as sq
        sq.add_signal  = lambda *a, **kw: {'status':'ok','queued':False}
        sq.get_status  = lambda *a, **kw: {'queue':[]}
    except: pass

def load_parquet(sym, tf):
    """加载parquet历史数据，返回DataFrame"""
    fp = Path(__file__).parent / 'data' / f'{sym.lower()}_{tf}_2018_2026.parquet'
    if not fp.exists():
        return None
    df = pd.read_parquet(fp)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()

def run_backtest_single(sym, tf, verbose=False):
    """单标的单周期回测"""
    from dharma.historical_adapter import HistoricalAdapter

    df = load_parquet(sym, tf)
    if df is None:
        return {'status':'no_data', 'sym':sym, 'tf':tf}

    df_oos = df[df.index >= OOS_START].copy()
    if len(df_oos) < 50:
        return {'status':'insufficient', 'sym':sym, 'tf':tf, 'n':len(df_oos)}

    # 导入梵天大脑
    try:
        from brahma_brain import analyze
    except ImportError:
        return {'status':'import_error', 'sym':sym, 'tf':tf}

    disable_side_effects()

    trades   = []
    nav      = INITIAL_NAV
    open_pos = None   # 当前持仓
    scan_n   = SCAN_EVERY.get(tf, 1)
    total_bars = len(df_oos)

    for i, (ts, row) in enumerate(df_oos.iterrows()):
        current_price = float(row.get('close', row.iloc[3]))

        # ── 持仓管理 ──────────────────────────────────
        if open_pos:
            bars_held = i - open_pos['bar_open']
            max_bars  = open_pos['hold_bars']
            entry     = open_pos['entry']
            sl        = open_pos['sl']
            tp1       = open_pos['tp1']
            direction = open_pos['direction']

            high = float(row.get('high', row.iloc[1]))
            low  = float(row.get('low',  row.iloc[2]))

            outcome = None
            exit_price = current_price

            if direction == 'SHORT':
                if low <= tp1:
                    outcome, exit_price = 'TP1', tp1
                elif high >= sl:
                    outcome, exit_price = 'SL', sl
                elif bars_held >= max_bars:
                    outcome, exit_price = 'TIMEOUT', current_price
            else:  # LONG
                if high >= tp1:
                    outcome, exit_price = 'TP1', tp1
                elif low <= sl:
                    outcome, exit_price = 'SL', sl
                elif bars_held >= max_bars:
                    outcome, exit_price = 'TIMEOUT', current_price

            if outcome:
                if direction == 'SHORT':
                    pnl_pct = (entry - exit_price) / entry
                else:
                    pnl_pct = (exit_price - entry) / entry

                risk_amt = nav * RISK_PCT
                pnl_usdt = risk_amt * (pnl_pct / open_pos['sl_pct']) - nav * COMMISSION * 2
                nav += pnl_usdt

                trades.append({
                    'sym': sym, 'tf': tf,
                    'open_ts': open_pos['ts'].isoformat(),
                    'close_ts': ts.isoformat(),
                    'direction': direction,
                    'entry': entry,
                    'exit': exit_price,
                    'outcome': outcome,
                    'pnl_pct': round(pnl_pct * 100, 3),
                    'pnl_usdt': round(pnl_usdt, 2),
                    'nav': round(nav, 2),
                    'score': open_pos['score'],
                    'grade': open_pos['grade'],
                    'regime': open_pos['regime'],
                    'bars_held': bars_held,
                    'gap_pct': open_pos['gap_pct'],
                })
                open_pos = None

        # ── 信号扫描 ──────────────────────────────────
        if open_pos is not None:
            continue  # 已有持仓，不开新仓
        if i % scan_n != 0:
            continue  # 按间隔扫描
        if i < 50:
            continue  # 需要足够历史数据

        try:
            adapter = HistoricalAdapter(sym, ts)
            adapter.inject()
            result = analyze(sym, 'SHORT')
            adapter.restore()
        except Exception as e:
            continue

        score  = result.get('confluence', {}).get('total', 0) or 0
        grade  = int(result.get('structure_grade') or 0)
        params = result.get('params', {})
        regime = result.get('regime', '')
        valid  = result.get('valid', False)

        entry_lo = float(params.get('entry_lo', 0) or 0)
        entry_hi = float(params.get('entry_hi', 0) or 0)
        sl       = float(params.get('stop_loss', 0) or 0)
        tp1      = float(params.get('tp1', 0) or 0)
        sl_pct   = float(params.get('sl_pct', 0) or 0)

        if not entry_lo or not sl or not tp1:
            continue

        # ── 核心过滤器（铁证双过滤）──────────────────
        if score < MIN_SCORE:
            continue
        if grade < MIN_GRADE:
            continue

        gap_pct = (entry_lo - current_price) / current_price * 100
        if gap_pct < MIN_GAP:
            continue  # gap<1% 噪音区，WR=0%

        # ── 开仓 ─────────────────────────────────────
        entry_price = (entry_lo + entry_hi) / 2
        open_pos = {
            'ts': ts,
            'bar_open': i,
            'entry': entry_price,
            'sl': sl,
            'tp1': tp1,
            'sl_pct': sl_pct / 100 if sl_pct > 1 else sl_pct,
            'direction': 'SHORT',
            'score': score,
            'grade': grade,
            'regime': regime,
            'gap_pct': round(gap_pct, 2),
            'hold_bars': _hold_bars(tf, regime),
        }

        if verbose:
            print(f"  [{ts.strftime('%Y-%m-%d %H:%M')}] 开仓 {sym} SHORT "
                  f"score={score} grade={grade} gap={gap_pct:.1f}%")

    # ── 统计 ─────────────────────────────────────────
    if not trades:
        return {
            'status': 'ok', 'sym': sym, 'tf': tf,
            'n': 0, 'wr': 0, 'pf': 0,
            'nav_final': nav, 'return_pct': 0,
            'total_bars': total_bars,
        }

    wins     = [t for t in trades if t['outcome'] in ('TP1','TP2')]
    losses   = [t for t in trades if t['outcome'] == 'SL']
    timeouts = [t for t in trades if t['outcome'] == 'TIMEOUT']
    wr       = len(wins) / len(trades) * 100

    win_pnl  = sum(t['pnl_usdt'] for t in wins)
    loss_pnl = abs(sum(t['pnl_usdt'] for t in losses))
    pf       = win_pnl / loss_pnl if loss_pnl > 0 else float('inf')

    avg_win  = sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0
    ev       = (wr/100 * avg_win) + ((1-wr/100) * avg_loss)

    return {
        'status': 'ok', 'sym': sym, 'tf': tf,
        'n': len(trades), 'n_win': len(wins), 'n_sl': len(losses),
        'n_timeout': len(timeouts),
        'wr': round(wr, 1),
        'pf': round(pf, 2),
        'ev': round(ev, 3),
        'avg_win_pct': round(avg_win, 3),
        'avg_loss_pct': round(avg_loss, 3),
        'nav_final': round(nav, 2),
        'return_pct': round((nav - INITIAL_NAV) / INITIAL_NAV * 100, 1),
        'total_bars': total_bars,
        'trades': trades,
    }


def main():
    tag   = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    print(f"╔══════════════════════════════════════════════════════╗")
    print(f"  达摩院 · 2025年实战数据回测  {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')} BJ")
    print(f"  过滤器：grade≥{MIN_GRADE} + gap≥{MIN_GAP}% + score≥{MIN_SCORE}")
    print(f"  数据：2025-01-01 至今  周期：15m/1h/4h/1d")
    print(f"╠══════════════════════════════════════════════════════╣")

    all_results = {}
    summary_rows = []

    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            t0 = time.time()
            print(f"\n  ▶ {sym} {tf} ...", end='', flush=True)
            r = run_backtest_single(sym, tf)
            elapsed = time.time() - t0

            if r['status'] != 'ok' or r['n'] == 0:
                print(f" 跳过({r['status']}, n={r.get('n',0)})")
                continue

            all_results[f"{sym}_{tf}"] = r
            bar = '█'*int(r['wr']/10) + '░'*(10-int(r['wr']/10))
            print(f" WR={r['wr']:.0f}% n={r['n']} PF={r['pf']:.2f} "
                  f"EV={r['ev']:+.2f}%  {bar}  ({elapsed:.0f}s)")

            summary_rows.append({
                'sym': sym, 'tf': tf,
                'n': r['n'], 'wr': r['wr'],
                'pf': r['pf'], 'ev': r['ev'],
                'return_pct': r['return_pct'],
            })

    # ── 汇总输出 ─────────────────────────────────────
    print(f"\n╠══════════════════════════════════════════════════════╣")
    print(f"  {'品种':<10} {'周期':<5} {'n':>4} {'WR':>6} {'PF':>6} {'EV/笔':>7} {'收益%':>7}")
    print(f"  {'─'*52}")
    for row in sorted(summary_rows, key=lambda x: -x['wr']):
        flag = '🏆' if row['wr']>=70 else ('✅' if row['wr']>=55 else '⚠️')
        print(f"  {flag} {row['sym']:<8} {row['tf']:<5} "
              f"{row['n']:>4} {row['wr']:>5.0f}% {row['pf']:>5.2f} "
              f"{row['ev']:>+6.2f}%  {row['return_pct']:>+6.1f}%")

    # ── 最佳组合 ─────────────────────────────────────
    good = [r for r in summary_rows if r['wr'] >= 60 and r['n'] >= 5]
    if good:
        print(f"\n  ⭐ 有效组合（WR≥60% n≥5）：")
        for r in sorted(good, key=lambda x: -(x['wr'] * x['n'])):
            print(f"     {r['sym']} {r['tf']}: WR={r['wr']:.0f}% n={r['n']} PF={r['pf']:.2f}")

    # ── 保存结果 ─────────────────────────────────────
    out_path = RESULTS / f'dharma_2025_test_{tag}.json'
    save_data = {
        'tag': tag, 'oos_start': '2025-01-01',
        'filters': {'min_score': MIN_SCORE, 'min_grade': MIN_GRADE, 'min_gap': MIN_GAP},
        'summary': summary_rows,
        'details': {k: {kk: vv for kk, vv in v.items() if kk != 'trades'}
                    for k, v in all_results.items()},
    }
    out_path.write_text(json.dumps(save_data, ensure_ascii=False, indent=2, default=str))
    print(f"\n  结果已保存: {out_path.name}")
    print(f"╚══════════════════════════════════════════════════════╝")

if __name__ == '__main__':
    main()
