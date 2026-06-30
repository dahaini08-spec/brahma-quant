#!/usr/bin/env python3
"""
梵天 Monte Carlo 引擎 v2
双模式:
  Mode A - Bootstrap: 重放武曲Paper真实交易（评估资金曲线分布）
  Mode B - Path:      GBM+跳跃扩散生成价格路径（评估市场极端风险）

用法:
  python3 arch/simulation/monte_carlo_engine.py             # bootstrap模式
  python3 arch/simulation/monte_carlo_engine.py --path      # 路径模式
  python3 arch/simulation/monte_carlo_engine.py --full      # 双模式完整报告
"""
import numpy as np
import json, argparse
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent.parent
np.random.seed(42)


# ─────────────────────────────────────────────────────────────────
# 数据加载
# ─────────────────────────────────────────────────────────────────
def load_wuqu_pnls():
    """武曲Paper实际收益率序列（已结算，排除TIMEOUT）"""
    records = []
    with open(ROOT / 'data/wuqu_paper_settled.jsonl') as f:
        for line in f:
            try: records.append(json.loads(line))
            except: pass

    pnls = []
    for r in records:
        outcome = r.get('outcome', '')
        if outcome not in ('TP1', 'TP2', 'SL'):
            continue
        v = float(r.get('pnl_pct', 0))
        pnls.append(v / 10000 if abs(v) > 10 else v)
    return np.array(pnls)


# ─────────────────────────────────────────────────────────────────
# Mode A: Bootstrap Monte Carlo（基于真实交易）
# ─────────────────────────────────────────────────────────────────
def run_bootstrap_mc(pnls: np.ndarray,
                     n_sims:     int   = 30000,
                     n_trades:   int   = None,
                     capital:    float = 10000.0,
                     risk_frac:  float = 0.02) -> dict:
    """
    Bootstrap重采样武曲Paper真实PnL序列
    每次模拟随机抽n_trades笔交易，复利计算资金曲线
    """
    if len(pnls) == 0:
        return {'error': '无数据'}
    if n_trades is None:
        n_trades = len(pnls)

    final_caps  = np.zeros(n_sims)
    max_dds     = np.zeros(n_sims)
    ruin_count  = 0
    sharpes     = []

    for i in range(n_sims):
        # 有放回重采样
        sample  = np.random.choice(pnls, size=n_trades, replace=True)
        cap     = capital
        peak    = capital
        max_dd  = 0.0

        for r in sample:
            # 固定风险比例：每笔动用 risk_frac 本金
            cap  = cap * (1.0 + risk_frac * r / max(abs(pnls.min()), 1e-4))
            cap  = max(cap, 0.0)
            peak = max(peak, cap)
            dd   = (peak - cap) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
            if cap < capital * 0.1:
                ruin_count += 1
                break

        final_caps[i] = cap
        max_dds[i]    = max_dd

        # Sharpe of this path
        if n_trades > 1:
            sr = sample.mean() / (sample.std() + 1e-9) * np.sqrt(252)
            sharpes.append(sr)

    def pct(arr, p): return float(np.percentile(arr, p))

    return {
        'mode':         'bootstrap',
        'n_sims':       n_sims,
        'n_trades':     n_trades,
        'source_n':     len(pnls),
        'capital': {
            'start':  capital,
            'p5':     round(pct(final_caps,  5)),
            'p25':    round(pct(final_caps, 25)),
            'p50':    round(pct(final_caps, 50)),
            'p75':    round(pct(final_caps, 75)),
            'p95':    round(pct(final_caps, 95)),
            'mean':   round(float(final_caps.mean())),
        },
        'max_drawdown': {
            'p50': round(pct(max_dds, 50) * 100, 2),
            'p75': round(pct(max_dds, 75) * 100, 2),
            'p95': round(pct(max_dds, 95) * 100, 2),
        },
        'sharpe': {
            'p25': round(pct(sharpes, 25), 2) if sharpes else 0,
            'p50': round(pct(sharpes, 50), 2) if sharpes else 0,
            'p75': round(pct(sharpes, 75), 2) if sharpes else 0,
        },
        'ruin_rate':    round(ruin_count / n_sims * 100, 3),
        'profit_prob':  round(float((final_caps > capital).mean() * 100), 2),
    }


# ─────────────────────────────────────────────────────────────────
# Mode B: GBM + 跳跃扩散 价格路径
# ─────────────────────────────────────────────────────────────────
def run_path_mc(n_sims:     int   = 10000,
                days:       int   = 252,
                capital:    float = 10000.0,
                leverage:   float = 15.0,
                volatility: float = 0.32,
                drift:      float = 0.10,
                jump_intensity: float = 0.025,
                win_rate:   float = 0.58,
                risk_frac:  float = 0.008) -> dict:
    """
    GBM + Poisson跳跃扩散模拟市场路径
    在路径上模拟梵天策略的执行
    """
    dt     = 1.0 / 252
    mu     = drift * dt
    sigma  = volatility * np.sqrt(dt)

    final_caps  = np.zeros(n_sims)
    max_dds     = np.zeros(n_sims)

    # 批量生成（内存优化）
    batch = min(n_sims, 5000)
    processed = 0

    while processed < n_sims:
        cur_batch = min(batch, n_sims - processed)

        # 布朗运动
        dW = np.random.normal(0, sigma, (cur_batch, days))
        # Poisson跳跃（清算瀑布）
        jumps = (np.random.poisson(jump_intensity, (cur_batch, days)) *
                 np.random.normal(-0.04, 0.10, (cur_batch, days)))

        # 每日对数收益
        log_ret = mu - 0.5 * sigma**2 + dW + jumps
        daily_ret = np.expm1(log_ret)

        # 策略模拟：梵天做反方向（空头），赢率 win_rate
        position = np.where(np.random.rand(cur_batch, days) < win_rate, 1.0, -1.0)
        strat_ret = position * daily_ret * leverage * risk_frac

        # 资金曲线（复利）
        caps = np.zeros((cur_batch, days + 1))
        caps[:, 0] = capital
        for t in range(days):
            caps[:, t+1] = caps[:, t] * (1 + strat_ret[:, t])
            caps[:, t+1] = np.maximum(caps[:, t+1], 0)

        # 最大回撤
        roll_max = np.maximum.accumulate(caps, axis=1)
        dds = (roll_max - caps) / np.maximum(roll_max, 1e-6)
        max_dds[processed:processed+cur_batch] = dds.max(axis=1)
        final_caps[processed:processed+cur_batch] = caps[:, -1]
        processed += cur_batch

    def pct(arr, p): return float(np.percentile(arr, p))

    # VaR / CVaR
    returns_pct = (final_caps - capital) / capital
    var_95  = pct(returns_pct, 5)
    cvar_95 = float(returns_pct[returns_pct <= var_95].mean()) if (returns_pct <= var_95).any() else var_95

    return {
        'mode':        'gbm_jump',
        'n_sims':      n_sims,
        'days':        days,
        'leverage':    leverage,
        'volatility':  volatility,
        'capital': {
            'start': capital,
            'p5':    round(pct(final_caps,  5)),
            'p50':   round(pct(final_caps, 50)),
            'p95':   round(pct(final_caps, 95)),
            'mean':  round(float(final_caps.mean())),
        },
        'max_drawdown': {
            'p50': round(pct(max_dds, 50) * 100, 2),
            'p75': round(pct(max_dds, 75) * 100, 2),
            'p95': round(pct(max_dds, 95) * 100, 2),
        },
        'risk': {
            'var_95_pct':  round(var_95 * 100, 2),
            'cvar_95_pct': round(cvar_95 * 100, 2),
        },
        'ruin_rate':   round(float((final_caps < capital * 0.1).mean() * 100), 3),
        'profit_prob': round(float((final_caps > capital).mean() * 100), 2),
    }


# ─────────────────────────────────────────────────────────────────
# 杠杆敏感性扫描
# ─────────────────────────────────────────────────────────────────
def leverage_sensitivity(pnls: np.ndarray, n_sims=5000) -> list:
    rows = []
    for lev in [5, 10, 15, 20, 25]:
        r = run_bootstrap_mc(pnls, n_sims=n_sims, risk_frac=lev * 0.001)
        rows.append({
            'leverage': lev,
            'p50':      r['capital']['p50'],
            'max_dd_p95': r['max_drawdown']['p95'],
            'ruin_pct': r['ruin_rate'],
            'profit_prob': r['profit_prob'],
        })
    return rows


# ─────────────────────────────────────────────────────────────────
# 报告打印
# ─────────────────────────────────────────────────────────────────
def print_bootstrap(r):
    c = r['capital']
    d = r['max_drawdown']
    s = r['sharpe']
    print(f'\n{"="*56}')
    print(f'  Mode A · Bootstrap MC（{r["n_sims"]:,}次 / {r["source_n"]}条真实交易）')
    print(f'{"="*56}')
    print(f'  资金曲线（起始 ${c["start"]:,}）:')
    print(f'    悲观 P5  : ${c["p5"]:>10,}')
    print(f'    中位 P50 : ${c["p50"]:>10,}')
    print(f'    乐观 P95 : ${c["p95"]:>10,}')
    print(f'    期望均值 : ${c["mean"]:>10,}')
    print(f'  最大回撤: P50={d["p50"]}%  P75={d["p75"]}%  P95={d["p95"]}%')
    print(f'  Sharpe:  P25={s["p25"]}  P50={s["p50"]}  P75={s["p75"]}')
    print(f'  破产概率: {r["ruin_rate"]}%  | 盈利概率: {r["profit_prob"]}%')


def print_path(r):
    c = r['capital']
    d = r['max_drawdown']
    rk = r['risk']
    print(f'\n{"="*56}')
    print(f'  Mode B · GBM+跳跃路径（{r["n_sims"]:,}条 / {r["days"]}天 / {r["leverage"]}x）')
    print(f'{"="*56}')
    print(f'  资金曲线（起始 ${c["start"]:,}）:')
    print(f'    悲观 P5  : ${c["p5"]:>10,}')
    print(f'    中位 P50 : ${c["p50"]:>10,}')
    print(f'    乐观 P95 : ${c["p95"]:>10,}')
    print(f'  最大回撤: P50={d["p50"]}%  P75={d["p75"]}%  P95={d["p95"]}%')
    print(f'  VaR(95%): {rk["var_95_pct"]}%  CVaR(95%): {rk["cvar_95_pct"]}%')
    print(f'  破产概率: {r["ruin_rate"]}%  | 盈利概率: {r["profit_prob"]}%')


def print_leverage(rows):
    print(f'\n{"="*56}')
    print(f'  杠杆敏感性分析（5,000次模拟）')
    print(f'{"="*56}')
    print(f'  {"杠杆":>6}  {"P50资金":>10}  {"极端回撤P95":>12}  {"破产率":>8}  {"盈利率":>8}')
    for row in rows:
        flag = '⚠️' if row['ruin_pct'] > 5 else '✅'
        print(f'  {row["leverage"]:>5}x  ${row["p50"]:>9,}  '
              f'{row["max_dd_p95"]:>11.1f}%  '
              f'{row["ruin_pct"]:>7.2f}%  '
              f'{row["profit_prob"]:>7.1f}%  {flag}')


# ─────────────────────────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--path', action='store_true', help='GBM路径模式')
    parser.add_argument('--full', action='store_true', help='双模式+杠杆扫描')
    parser.add_argument('--sims', type=int, default=20000)
    args = parser.parse_args()

    print(f'\n🏯 梵天 Monte Carlo 引擎 v2')
    print(f'   {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')

    pnls = load_wuqu_pnls()
    print(f'\n武曲Paper有效交易: {len(pnls)}条')
    if len(pnls) > 0:
        wins = (pnls > 0).sum()
        print(f'WR={wins/len(pnls):.1%}  平均盈={pnls[pnls>0].mean()*100:.2f}%  平均亏={pnls[pnls<0].mean()*100:.2f}%')

    do_bootstrap = not args.path or args.full
    do_path      = args.path or args.full

    if do_bootstrap and len(pnls) >= 5:
        r = run_bootstrap_mc(pnls, n_sims=args.sims)
        print_bootstrap(r)

    if do_path:
        print('\n（GBM路径模式使用理论参数，不依赖武曲Paper数据）')
        r2 = run_path_mc(n_sims=min(args.sims, 10000))
        print_path(r2)

    if args.full and len(pnls) >= 5:
        rows = leverage_sensitivity(pnls)
        print_leverage(rows)

    # 保存
    out = ROOT / 'data/mc_v2_report.json'
    report = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'n_pnls': len(pnls),
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(f'\n✅ 报告已保存: {out}')


if __name__ == '__main__':
    main()
