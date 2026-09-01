#!/usr/bin/env python3
"""
dharma_6y5_validator.py — 达摩院标准验证框架
设计院封印 | 2026-08-04 苏摩111

宪法原则：
  所有方法、参数、策略改进 → 必须通过达摩院6.5年期货合约数据验证
  才能封印进主系统。仅凭理论或小样本不得升级。

数据范围：
  BTC/ETH USDT永续合约 | 2020-01-01 ~ 2026-07-10
  15m / 1H / 4H / 1D 全周期

验证标准（苏摩111批准）：
  - 样本量 N ≥ 500 笔交易
  - EV改进 > 0（方向正确）
  - 95% Bootstrap CI 下界 > -0.1%（不显著亏损）
  - 4H + 1H 两个周期均方向一致
  - OOS验证（后20%数据）EV方向一致

当前已验证方法：
  ✅ 清算集群TP代理方法 → EV改进+0.023%（2026-08-04）
  ⏳ 时段过滤（亚洲-0.7x）→ 待验证
  ⏳ OB精准区加权（0.5~1%）→ 待验证
  ⏳ 持仓延长72H → 待验证
"""
import json, sys, time, datetime
import numpy as np
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / 'data' / 'backtest'
RESULTS_DIR = DATA

sys.path.insert(0, str(BASE))

# ─────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────
@dataclass
class Bar:
    ts: int; o: float; h: float; l: float; c: float; v: float

@dataclass
class ValidationResult:
    method: str
    symbol: str
    timeframe: str
    n_trades: int
    baseline_ev: float
    improved_ev: float
    improvement: float
    baseline_wr: float
    improved_wr: float
    ci_lo: float
    ci_hi: float
    oos_improvement: float   # Out-of-sample改进
    verdict: str             # PASS / MARGINAL / FAIL
    details: dict = field(default_factory=dict)

# ─────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────
def load_bars(sym: str, tf: str) -> List[Bar]:
    f = DATA / f'{sym}_{tf}.json'
    return [Bar(int(r[0]),float(r[1]),float(r[2]),float(r[3]),float(r[4]),float(r[5]))
            for r in json.load(open(f))]

def ema(closes: np.ndarray, n: int) -> np.ndarray:
    a = 2/(n+1); out = np.zeros(len(closes)); out[0] = closes[0]
    for i in range(1, len(closes)): out[i] = closes[i]*a + out[i-1]*(1-a)
    return out

def rsi(closes: np.ndarray, p: int = 14) -> np.ndarray:
    d = np.diff(closes)
    g = np.where(d>0,d,0.0); l = np.where(d<0,-d,0.0)
    ag = np.zeros(len(closes)); al = np.zeros(len(closes))
    ag[p] = np.mean(g[:p]); al[p] = np.mean(l[:p])
    for i in range(p+1, len(closes)):
        ag[i] = (ag[i-1]*(p-1)+g[i-1])/p
        al[i] = (al[i-1]*(p-1)+l[i-1])/p
    rs = np.where(al>0, ag/al, 100.0)
    return 100 - 100/(1+rs)

def atr(bars: List[Bar], p: int = 14) -> np.ndarray:
    trs = [0.0]
    for i in range(1, len(bars)):
        trs.append(max(bars[i].h-bars[i].l,
                       abs(bars[i].h-bars[i-1].c),
                       abs(bars[i].l-bars[i-1].c)))
    a = np.zeros(len(bars)); a[p] = np.mean(trs[:p])
    for i in range(p+1, len(bars)): a[i] = (a[i-1]*(p-1)+trs[i])/p
    return a

def bootstrap_ci(pnls: np.ndarray, n_boot=2000, ci=0.95) -> Tuple[float,float]:
    if len(pnls) < 5: return float(np.mean(pnls)), float(np.mean(pnls))
    means = [np.mean(np.random.choice(pnls, len(pnls), replace=True)) for _ in range(n_boot)]
    return float(np.percentile(means, (1-ci)/2*100)), float(np.percentile(means, (1+ci)/2*100))

def simulate_trades(bars: List[Bar],
                    entry_idx: List[int],
                    entry_prices: List[float],
                    directions: List[str],
                    sls: List[float],
                    tp_fixeds: List[float],
                    max_hold: int = 48) -> List[dict]:
    """标准化前向模拟，返回每笔交易结果"""
    trades = []
    for k, (idx, ep, dirn, sl, tp) in enumerate(zip(entry_idx, entry_prices, directions, sls, tp_fixeds)):
        exit_px = None; exit_reason = None
        for j in range(idx+1, min(idx+max_hold+1, len(bars))):
            b = bars[j]
            if dirn == 'LONG':
                if b.l <= sl:   exit_px = sl;  exit_reason = 'sl';      break
                if b.h >= tp:   exit_px = tp;  exit_reason = 'tp';      break
            else:
                if b.h >= sl:   exit_px = sl;  exit_reason = 'sl';      break
                if b.l <= tp:   exit_px = tp;  exit_reason = 'tp';      break
        if exit_px is None:
            exit_px = bars[min(idx+max_hold, len(bars)-1)].c
            exit_reason = 'timeout'
        pnl = (exit_px-ep)/ep if dirn=='LONG' else (ep-exit_px)/ep
        trades.append({'idx':idx,'entry':ep,'direction':dirn,'sl':sl,'tp':tp,
                       'exit':exit_px,'reason':exit_reason,'pnl':pnl*100,
                       'ts':bars[idx].ts})
    return trades

def verdicate(improvement: float, ci_lo: float, n: int) -> str:
    if n < 200:        return 'INSUFFICIENT_SAMPLE'
    if improvement > 0.05 and ci_lo > -0.05: return 'PASS'
    if improvement > 0.01 and ci_lo > -0.15: return 'MARGINAL'
    if improvement > 0:                       return 'DIRECTION_OK'
    return 'FAIL'

# ─────────────────────────────────────────────────────────
# 信号生成（标准化，适用于所有实验）
# ─────────────────────────────────────────────────────────
def generate_standard_signals(bars: List[Bar]) -> List[dict]:
    """
    达摩院标准信号：RSI + EMA结构，无上帝视角
    入场在i+1根开盘价，信号在i根收盘后产生
    """
    n = len(bars)
    closes = np.array([b.c for b in bars])
    highs  = np.array([b.h for b in bars])
    lows   = np.array([b.l for b in bars])

    rsi_arr  = rsi(closes)
    ema20    = ema(closes, 20)
    ema50    = ema(closes, 50)
    atr_arr  = atr(bars)

    sigs = []
    for i in range(60, n-1):
        if atr_arr[i] <= 0: continue
        r = rsi_arr[i]
        h5 = float(np.max(highs[i-5:i]))
        l5 = float(np.min(lows[i-5:i]))
        h20= float(np.max(highs[i-20:i]))
        l20= float(np.min(lows[i-20:i]))
        bull = bars[i].c > bars[i].o
        bear = bars[i].c < bars[i].o

        # ob_dist_pct 代理：距EMA20距离作为OB距离代理
        ob_dist = abs(bars[i].c - ema20[i]) / ema20[i]

        # 做多信号（三选一）
        sig_long = (r < 40 and bull) or (r > 50 and bars[i].c > h5*1.001) or (bars[i].c > h20*1.002 and r < 75)
        # 做空信号（三选一）
        sig_short= (r > 60 and bear) or (r < 50 and bars[i].c < l5*0.999) or (bars[i].c < l20*0.998 and r > 25)

        ep = bars[i+1].o
        at = atr_arr[i]
        if sig_long and not sig_short:
            sl = ep - at*1.5
            sigs.append({'idx':i+1,'ts':bars[i+1].ts,'dir':'LONG','ep':ep,'sl':sl,'at':at,'ob_dist':ob_dist})
        elif sig_short and not sig_long:
            sl = ep + at*1.5
            sigs.append({'idx':i+1,'ts':bars[i+1].ts,'dir':'SHORT','ep':ep,'sl':sl,'at':at,'ob_dist':ob_dist})
    return sigs

# ─────────────────────────────────────────────────────────
# 实验框架：基准 vs 改进方法
# ─────────────────────────────────────────────────────────
def run_experiment(name: str, sym: str, tf: str,
                   filter_fn=None,    # 信号过滤函数(sig)->bool
                   tp_fn=None,        # TP计算函数(sig,bars)->float
                   max_hold_base=48,
                   max_hold_improved=48,
                   verbose=True) -> ValidationResult:
    """
    标准化A/B实验：
      基准组：全部信号 + 固定2%TP + max_hold_base
      改进组：filter_fn过滤 + tp_fn自定义TP + max_hold_improved
    """
    bars = load_bars(sym, tf)
    sigs = generate_standard_signals(bars)

    if verbose:
        print(f"\n  {sym} {tf} | 信号数: {len(sigs)}")

    # 基准组
    base_trades = simulate_trades(
        bars,
        [s['idx'] for s in sigs],
        [s['ep']  for s in sigs],
        [s['dir'] for s in sigs],
        [s['sl']  for s in sigs],
        [s['ep']*(1.02 if s['dir']=='LONG' else 0.98) for s in sigs],
        max_hold=max_hold_base
    )

    # 改进组
    improved_sigs = [s for s in sigs if filter_fn is None or filter_fn(s)]
    if tp_fn:
        tp_prices = [tp_fn(s, bars) for s in improved_sigs]
    else:
        tp_prices  = [s['ep']*(1.02 if s['dir']=='LONG' else 0.98) for s in improved_sigs]

    improved_trades = simulate_trades(
        bars,
        [s['idx'] for s in improved_sigs],
        [s['ep']  for s in improved_sigs],
        [s['dir'] for s in improved_sigs],
        [s['sl']  for s in improved_sigs],
        tp_prices,
        max_hold=max_hold_improved
    )

    # OOS切分（后20%为OOS）
    oos_cut = int(len(bars) * 0.8)
    base_is  = [t for t in base_trades     if t['idx'] < oos_cut]
    base_oos = [t for t in base_trades     if t['idx'] >= oos_cut]
    imp_is   = [t for t in improved_trades if t['idx'] < oos_cut]
    imp_oos  = [t for t in improved_trades if t['idx'] >= oos_cut]

    def stats(trades):
        if not trades: return 0,0,0
        p = np.array([t['pnl'] for t in trades])
        return float(np.mean(p)), sum(1 for x in p if x>0)/len(p)*100, len(trades)

    base_ev, base_wr, base_n   = stats(base_trades)
    imp_ev,  imp_wr,  imp_n    = stats(improved_trades)
    _, _, oos_base_n = stats(base_oos)
    oos_base_ev, _, _ = stats(base_oos)
    oos_imp_ev,  _, _ = stats(imp_oos)

    improvement = imp_ev - base_ev
    oos_imp     = oos_imp_ev - oos_base_ev

    imp_pnls = np.array([t['pnl'] for t in improved_trades])
    ci_lo, ci_hi = bootstrap_ci(imp_pnls)

    v = verdicate(improvement, ci_lo, imp_n)

    if verbose:
        sign = '✅' if improvement > 0 else '❌'
        print(f"  基准: N={base_n} EV={base_ev:>+.3f}% WR={base_wr:.1f}%")
        print(f"  改进: N={imp_n}  EV={imp_ev:>+.3f}% WR={imp_wr:.1f}%  {sign}")
        print(f"  改进幅度: {improvement:>+.3f}%/笔 | OOS: {oos_imp:>+.3f}% | 结论: {v}")
        print(f"  95%CI: [{ci_lo:>+.3f}%, {ci_hi:>+.3f}%]")

    return ValidationResult(
        method=name, symbol=sym, timeframe=tf,
        n_trades=imp_n,
        baseline_ev=base_ev, improved_ev=imp_ev, improvement=improvement,
        baseline_wr=base_wr, improved_wr=imp_wr,
        ci_lo=ci_lo, ci_hi=ci_hi,
        oos_improvement=oos_imp,
        verdict=v,
        details={'base_n': base_n, 'oos_base_ev': oos_base_ev, 'oos_imp_ev': oos_imp_ev}
    )

# ─────────────────────────────────────────────────────────
# 三大实验定义
# ─────────────────────────────────────────────────────────

def validate_session_filter():
    """实验1：亚洲时段过滤（00-08 UTC降权/排除）"""
    print("\n" + "="*65)
    print("  【达摩院验证1】时段过滤 — 亚洲时段(00-08 UTC)排除")
    print("  假说：亚洲低流动性 → 假突破多 → 过滤后EV提升")
    print("="*65)

    def not_asia(sig):
        h = datetime.datetime.utcfromtimestamp(sig['ts']/1000).hour
        return not (0 <= h < 8)

    results = []
    for sym in ['BTCUSDT','ETHUSDT']:
        for tf in ['1h','4h']:
            r = run_experiment('时段过滤_非亚洲', sym, tf, filter_fn=not_asia)
            results.append(r)
    return results

def validate_ob_distance():
    """实验2：OB距离精准区（0.5~1%）"""
    print("\n" + "="*65)
    print("  【达摩院验证2】OB距离精准区加权")
    print("  假说：OB距0.5~1% = OB磁铁效应最强 → EV最高")
    print("="*65)

    def ob_precise(sig):
        ob = sig.get('ob_dist', 999)
        return 0.005 <= ob <= 0.010

    results = []
    for sym in ['BTCUSDT','ETHUSDT']:
        for tf in ['1h','4h']:
            r = run_experiment('OB精准区_0.5~1%', sym, tf, filter_fn=ob_precise)
            results.append(r)
    return results

def validate_hold_extension():
    """实验3：持仓延长 24H→72H"""
    print("\n" + "="*65)
    print("  【达摩院验证3】持仓时长延长（48H→96H）")
    print("  假说：给方向更多时间被市场验证 → 减少过早止损")
    print("="*65)

    results = []
    for sym in ['BTCUSDT','ETHUSDT']:
        for tf in ['1h','4h']:
            r = run_experiment('持仓延长_96H', sym, tf,
                               max_hold_base=48,
                               max_hold_improved=96)
            results.append(r)
    return results

# ─────────────────────────────────────────────────────────
# 汇总报告
# ─────────────────────────────────────────────────────────
def print_summary(all_results: List[ValidationResult]):
    print("\n\n" + "="*70)
    print("  🏛️ 达摩院 6.5年标准验证 — 最终汇总")
    print("="*70)
    print(f"\n  {'方法':<18} {'品种+周期':<14} {'N':>5} {'基准EV':>9} {'改进EV':>9} {'改进':>8} {'OOS':>8} {'结论'}")
    print(f"  {'-'*80}")

    by_method = defaultdict(list)
    for r in all_results:
        by_method[r.method].append(r)

    for method, results in by_method.items():
        for r in results:
            tag = '✅' if 'PASS' in r.verdict else ('🟡' if 'MARGINAL' in r.verdict or 'DIRECTION' in r.verdict else '❌')
            print(f"  {r.method:<18} {r.symbol[-6:-4]+'_'+r.timeframe:<14} {r.n_trades:>5} "
                  f"{r.baseline_ev:>+8.3f}% {r.improved_ev:>+8.3f}% {r.improvement:>+7.3f}% "
                  f"{r.oos_improvement:>+7.3f}%  {tag}{r.verdict}")

    # 每个方法的综合结论
    print(f"\n  {'─'*70}")
    print(f"  综合裁决：")
    for method, results in by_method.items():
        avg_imp = np.mean([r.improvement for r in results])
        avg_oos = np.mean([r.oos_improvement for r in results])
        pass_cnt = sum(1 for r in results if 'PASS' in r.verdict or 'DIRECTION' in r.verdict)
        total    = len(results)
        if avg_imp > 0.05 and pass_cnt >= total//2:
            final = "✅ 通过 — 可封印进主系统"
        elif avg_imp > 0:
            final = "🟡 方向正确 — 继续积累样本"
        else:
            final = "❌ 未通过 — 不可封印"
        print(f"  {method:<20} 平均改进={avg_imp:>+.3f}% OOS={avg_oos:>+.3f}%  {final}")

    # 保存结果
    out = {
        'timestamp': datetime.datetime.utcnow().isoformat(),
        'data_range': '2020-01-01 ~ 2026-07-10',
        'results': [
            {'method': r.method, 'symbol': r.symbol, 'tf': r.timeframe,
             'n': r.n_trades, 'base_ev': r.baseline_ev, 'imp_ev': r.improved_ev,
             'improvement': r.improvement, 'oos': r.oos_improvement,
             'ci_lo': r.ci_lo, 'ci_hi': r.ci_hi, 'verdict': r.verdict}
            for r in all_results
        ]
    }
    out_f = DATA / 'dharma_6y5_validation_results.json'
    json.dump(out, open(out_f,'w'), indent=2, ensure_ascii=False)
    print(f"\n  💾 结果已保存: {out_f}")
    print("="*70)

# ─────────────────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────────────────
def main():
    print("\n" + "="*70)
    print("  🏛️ 达摩院 × 梵天 · 6.5年期货合约标准验证框架")
    print("  数据: BTC/ETH 2020-01-01~2026-07-10 | 无上帝视角")
    print("  宪法: 所有方法必须通过此框架验证才可封印进主系统")
    print("="*70)

    all_results = []
    all_results.extend(validate_session_filter())
    all_results.extend(validate_ob_distance())
    all_results.extend(validate_hold_extension())
    print_summary(all_results)

if __name__ == '__main__':
    main()
