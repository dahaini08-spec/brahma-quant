#!/usr/bin/env python3
"""
Block Bootstrap Monte Carlo — 保留波动聚集 + 杠杆 + 资金费
================================================================
Phase B · 2026-09-13 三方联合深度评估

核心修复（vs旧mega_mc.py）:
1. Block bootstrap: 保留逐笔收益的时序依赖（波动聚集）
   - 旧版: i.i.d.有放回采样 → 破坏自相关结构
   - 新版: block size = ceil(n^(1/3)) → 保留局部时序
2. 杠杆复利: nav *= (1 + pnl × leverage) 而非 nav += pnl
   - 旧版: nav += pnl → 加法 → 不会爆仓
   - 新版: nav *= (1 + pnl × lev) → 乘法 → 会爆仓
3. 资金费路径: 每8h扣一次funding rate
   - 旧版: 无资金费
   - 新版: 0.01% per 8h × leverage (历史平均)
4. 爆仓路径: nav <= 0 → ruin = True
   - 旧版: ruin = nav < 0.7 × start → 70%回撤算破产
   - 新版: nav <= 0 → 真破产 + 多级回撤阈值
5. 最大回撤: 路径内部peak-to-trough
"""
import json, math, random, argparse
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

# ── 成本模型 ──────────────────────────────────────────────
TAKER_FEE = 0.0005
SLIPPAGE = 0.0002
FUNDING_RATE = 0.0001  # 0.01% per 8h (历史平均)
FUNDING_INTERVAL_HOURS = 8

# ── 数据结构 ──────────────────────────────────────────────
@dataclass
class BlockMCResult:
    n_simulations: int
    block_size: int
    leverage: float
    n_trades: int
    start_nav: float
    nav_p01: float
    nav_p05: float
    nav_p25: float
    nav_p50: float
    nav_p75: float
    nav_p95: float
    nav_p99: float
    ruin_rate: float       # nav <= 0
    dd50_rate: float       # 50%回撤
    dd70_rate: float       # 70%回撤
    dd90_rate: float       # 90%回撤
    median_max_dd: float
    p95_max_dd: float
    p99_max_dd: float
    median_sharpe: float
    p5_sharpe: float
    p95_sharpe: float
    funding_cost_total: float  # 总资金费成本

# ── Block Bootstrap MC ─────────────────────────────────────
def block_bootstrap_mc(
    pnls: list[float],
    *,
    leverage: float = 5.0,
    start_nav: float = 10000.0,
    n_simulations: int = 100_000,
    block_size: int = 0,  # 0=auto
    seed: int = 42,
    apply_funding: bool = True,
    hold_hours_per_trade: float = 10.0,  # 平均持仓时间
) -> BlockMCResult:
    """
    Block Bootstrap Monte Carlo
    
    1. block_size = ceil(n^(1/3)) → 保留时序依赖
    2. nav *= (1 + pnl × leverage - costs) → 乘法复利
    3. 每8h扣funding rate × leverage
    4. nav <= 0 → ruin
    """
    n = len(pnls)
    if n < 5:
        raise ValueError(f"样本不足: {n}")
    
    # 自动block size: ceil(n^(1/3))
    if block_size <= 0:
        block_size = max(2, math.ceil(n ** (1/3)))
    
    # 成本: 每笔2×taker+slippage × leverage
    cost_per_trade = 2 * (TAKER_FEE + SLIPPAGE) * leverage * 100  # 百分比
    
    # 资金费: 每8h扣 funding_rate × leverage
    funding_per_period = FUNDING_RATE * leverage * 100 if apply_funding else 0
    # 每笔持仓期间的funding次数
    funding_periods_per_trade = hold_hours_per_trade / FUNDING_INTERVAL_HOURS
    funding_cost_per_trade = funding_per_period * funding_periods_per_trade
    
    total_cost_per_trade = cost_per_trade + funding_cost_per_trade
    
    rng = random.Random(seed)
    
    # 生成block索引池
    def sample_block_sequence(length: int) -> list[float]:
        """有放回采样block，直到达到指定长度"""
        result = []
        while len(result) < length:
            start = rng.randint(0, n - block_size)
            block = pnls[start:start + block_size]
            result.extend(block)
        return result[:length]
    
    finals = []
    max_dds = []
    sharpes = []
    ruins = 0
    dd50 = 0
    dd70 = 0
    dd90 = 0
    total_funding = 0
    
    for sim in range(n_simulations):
        sampled = sample_block_sequence(n)
        
        nav = start_nav
        peak = start_nav
        max_dd = 0.0
        
        sim_pnls = []
        
        for pnl in sampled:
            # 乘法复利: nav *= (1 + (pnl × leverage - cost) / 100)
            net_return = (pnl * leverage - total_cost_per_trade) / 100
            nav *= (1 + net_return)
            
            sim_pnls.append(net_return)
            
            # 爆仓
            if nav <= 0:
                nav = 0
                ruins += 1
                break
            
            # 回撤
            if nav > peak:
                peak = nav
            if peak > 0:
                dd = (peak - nav) / peak
                if dd > max_dd:
                    max_dd = dd
        
        finals.append(nav)
        max_dds.append(max_dd)
        
        # Sharpe
        if len(sim_pnls) > 1:
            mean_r = sum(sim_pnls) / len(sim_pnls)
            var_r = sum((r - mean_r) ** 2 for r in sim_pnls) / (len(sim_pnls) - 1)
            std_r = math.sqrt(var_r) if var_r > 0 else 0
            if std_r > 0:
                sharpe = mean_r / std_r * math.sqrt(156)
            else:
                sharpe = 0
        else:
            sharpe = 0
        sharpes.append(sharpe)
        
        if max_dd >= 0.50: dd50 += 1
        if max_dd >= 0.70: dd70 += 1
        if max_dd >= 0.90: dd90 += 1
        
        if apply_funding:
            total_funding += funding_cost_per_trade * n
    
    finals.sort()
    max_dds.sort()
    sharpes.sort()
    
    def pct(sorted_list, p):
        if not sorted_list:
            return 0
        idx = int(len(sorted_list) * p / 100)
        idx = min(idx, len(sorted_list) - 1)
        return sorted_list[idx]
    
    return BlockMCResult(
        n_simulations=n_simulations,
        block_size=block_size,
        leverage=leverage,
        n_trades=n,
        start_nav=start_nav,
        nav_p01=round(pct(finals, 1), 2),
        nav_p05=round(pct(finals, 5), 2),
        nav_p25=round(pct(finals, 25), 2),
        nav_p50=round(pct(finals, 50), 2),
        nav_p75=round(pct(finals, 75), 2),
        nav_p95=round(pct(finals, 95), 2),
        nav_p99=round(pct(finals, 99), 2),
        ruin_rate=round(ruins / n_simulations, 4),
        dd50_rate=round(dd50 / n_simulations, 4),
        dd70_rate=round(dd70 / n_simulations, 4),
        dd90_rate=round(dd90 / n_simulations, 4),
        median_max_dd=round(pct(max_dds, 50), 4),
        p95_max_dd=round(pct(max_dds, 95), 4),
        p99_max_dd=round(pct(max_dds, 99), 4),
        median_sharpe=round(pct(sharpes, 50), 4),
        p5_sharpe=round(pct(sharpes, 5), 4),
        p95_sharpe=round(pct(sharpes, 95), 4),
        funding_cost_total=round(total_funding / n_simulations, 2),
    )

# ── 数据加载 ──────────────────────────────────────────────
def load_pnls(path: str) -> list[float]:
    """从live_signal_log加载成本后逐笔收益率"""
    pnls = []
    with open(path) as f:
        for line in f:
            try:
                s = json.loads(line)
            except:
                continue
            if not s.get('settled') and s.get('status') not in ('TP1','TP2','SL','TIMEOUT','EXPIRED','EXPIRED_NO_TOUCH'):
                continue
            pnl = s.get('pnl_pct')
            if pnl is None:
                outcome = s.get('outcome', '')
                sl = s.get('sl_pct', 2.0)
                rr = s.get('rr1', 1.0)
                if outcome == 'TP1':
                    pnl = rr * sl
                elif outcome == 'TP2':
                    pnl = rr * sl * 2
                elif outcome == 'SL':
                    pnl = -sl
                else:
                    continue
            pnls.append(pnl)
    return pnls

# ── 主入口 ────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Block Bootstrap Monte Carlo')
    parser.add_argument('--signals', default='data/live_signal_log.jsonl')
    parser.add_argument('--leverage', type=float, default=5.0)
    parser.add_argument('--sims', type=int, default=100000)
    parser.add_argument('--start-nav', type=float, default=10000.0)
    parser.add_argument('--no-funding', action='store_true')
    parser.add_argument('--report', action='store_true')
    args = parser.parse_args()
    
    pnls = load_pnls(args.signals)
    print(f"加载 {len(pnls)} 笔已结算收益")
    
    if len(pnls) < 10:
        print("⚠️ 样本不足10笔，结果不可靠")
    
    # 多杠杆扫描
    print(f"\n{'='*70}")
    print(f"Block Bootstrap Monte Carlo (杠杆扫描)")
    print(f"{'='*70}")
    
    results = {}
    for lev in [1.0, 3.0, 5.0, 10.0, 20.0]:
        print(f"\n── 杠杆 {lev}x ──")
        r = block_bootstrap_mc(
            pnls, leverage=lev, start_nav=args.start_nav,
            n_simulations=min(args.sims, 50000),  # 扫描用较少模拟
            apply_funding=not args.no_funding,
        )
        results[f'lev_{lev}'] = asdict(r)
        
        print(f"  Block size: {r.block_size}")
        print(f"  P50 NAV:   ${r.nav_p50:,.0f} (起始 ${r.start_nav:,.0f})")
        print(f"  P5 NAV:    ${r.nav_p05:,.0f}")
        print(f"  P95 NAV:   ${r.nav_p95:,.0f}")
        print(f"  Ruin率:    {r.ruin_rate:.2%} (nav≤0)")
        print(f"  50%回撤:   {r.dd50_rate:.2%}")
        print(f"  70%回撤:   {r.dd70_rate:.2%}")
        print(f"  90%回撤:   {r.dd90_rate:.2%}")
        print(f"  中位MaxDD: {r.median_max_dd:.1%}")
        print(f"  P95 MaxDD: {r.p95_max_dd:.1%}")
        print(f"  中位Sharpe:{r.median_sharpe}")
    
    # 主杠杆5x详细模拟
    print(f"\n{'='*70}")
    print(f"主杠杆 5x × {args.sims} 次模拟")
    print(f"{'='*70}")
    main_result = block_bootstrap_mc(
        pnls, leverage=args.leverage, start_nav=args.start_nav,
        n_simulations=args.sims,
        apply_funding=not args.no_funding,
    )
    results['main'] = asdict(main_result)
    
    print(f"  P1 NAV:    ${main_result.nav_p01:,.0f}")
    print(f"  P5 NAV:    ${main_result.nav_p05:,.0f}")
    print(f"  P50 NAV:   ${main_result.nav_p50:,.0f}")
    print(f"  P95 NAV:   ${main_result.nav_p95:,.0f}")
    print(f"  P99 NAV:   ${main_result.nav_p99:,.0f}")
    print(f"  Ruin:      {main_result.ruin_rate:.2%}")
    print(f"  中位Sharpe:{main_result.median_sharpe}")
    print(f"  P5 Sharpe: {main_result.p5_sharpe}")
    print(f"  P95 Sharpe:{main_result.p95_sharpe}")
    print(f"  资金费:    ${main_result.funding_cost_total:,.0f}/sim")
    
    out = Path('data/phase_b_block_mc.json')
    out.write_text(json.dumps(results, indent=2))
    print(f"\n保存: {out}")
    
    if args.report:
        report_path = Path('docs/phase_b_block_mc_report.md')
        lines = [
            "# Block Bootstrap Monte Carlo Report",
            f"\n生成时间: {datetime.now(timezone.utc).isoformat()}",
            f"\n## 主杠杆 {args.leverage}x ({args.sims}次模拟)\n",
            f"| 指标 | 值 |",
            f"|------|-----|",
            f"| P1 NAV | ${main_result.nav_p01:,.0f} |",
            f"| P5 NAV | ${main_result.nav_p05:,.0f} |",
            f"| P50 NAV | ${main_result.nav_p50:,.0f} |",
            f"| P95 NAV | ${main_result.nav_p95:,.0f} |",
            f"| P99 NAV | ${main_result.nav_p99:,.0f} |",
            f"| Ruin率 | {main_result.ruin_rate:.2%} |",
            f"| 50%回撤概率 | {main_result.dd50_rate:.2%} |",
            f"| 70%回撤概率 | {main_result.dd70_rate:.2%} |",
            f"| 90%回撤概率 | {main_result.dd90_rate:.2%} |",
            f"| 中位MaxDD | {main_result.median_max_dd:.1%} |",
            f"| P95 MaxDD | {main_result.p95_max_dd:.1%} |",
            f"| 中位Sharpe | {main_result.median_sharpe} |",
            f"| Block size | {main_result.block_size} |",
            f"| 杠杆 | {main_result.leverage}x |",
        ]
        report_path.write_text('\n'.join(lines))
        print(f"报告: {report_path}")

if __name__ == '__main__':
    main()
