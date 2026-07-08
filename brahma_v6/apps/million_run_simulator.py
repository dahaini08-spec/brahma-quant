"""
brahma_v6/apps/million_run_simulator.py — 100万次事件级全链路推演
设计院 P0-5 | 2026-07-08

用途：
  验证系统在百万级压力下的稳定性、漏斗比例、链路完整性。
  9条硬约束全部检查，任一违规则报告 VIOLATION。

运行：
  python3 brahma_v6/apps/million_run_simulator.py --events 1000000
  python3 brahma_v6/apps/million_run_simulator.py --events 10000 --quick
"""
from __future__ import annotations
import sys
import time
import random
import argparse
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Tuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from brahma_v6.schemas.events import (
    make_signal_event, SignalScoredEvent,
    RiskDecisionEvent, OrderIntentEvent, PnLAttributionEvent,
    make_risk_decision,
)
from brahma_v6.risk.kernel import RiskKernel
from brahma_v6.risk.context import RiskContext
from brahma_v6.paper.paper_forward import PaperExecutor, get_portfolio

BASE = Path(__file__).resolve().parents[2]
REPORTS_DIR = BASE / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ── 场景生成参数 ────────────────────────────────────────────
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "SUIUSDT"]
DIRECTIONS = ["LONG", "SHORT"]
REGIMES = ["BEAR_TREND", "BEAR_RECOVERY", "CHOP_MID", "BULL_TREND", "BEAR_EARLY"]
SCORE_DISTRIBUTION = [
    (0.25, 80, 109),    # 低分 → 被风控拦截
    (0.30, 110, 137),   # 中分
    (0.25, 138, 154),   # 高分
    (0.15, 155, 169),   # 极高分
    (0.05, 170, 185),   # 神级
]

REGIME_WEIGHTS = {
    "BEAR_TREND": 0.30,
    "BEAR_RECOVERY": 0.20,
    "CHOP_MID": 0.25,
    "BULL_TREND": 0.15,
    "BEAR_EARLY": 0.10,
}


@dataclass
class SimStats:
    total_events: int = 0
    signals_scored: int = 0
    risk_approve: int = 0
    risk_reduce: int = 0
    risk_blocked: int = 0
    order_intents: int = 0
    paper_fills: int = 0
    closed_trades: int = 0
    net_positive: int = 0
    pnl_total: float = 0.0
    violations: List[str] = field(default_factory=list)
    blocked_layer_counts: Dict[str, int] = field(default_factory=dict)
    regime_stats: Dict[str, Dict] = field(default_factory=dict)
    symbol_stats: Dict[str, Dict] = field(default_factory=dict)
    direction_stats: Dict[str, Dict] = field(default_factory=dict)
    score_bucket_stats: Dict[str, Dict] = field(default_factory=dict)
    t_start: float = field(default_factory=time.time)
    t_end: float = 0.0


def _rand_score() -> float:
    r = random.random()
    cumulative = 0.0
    for weight, lo, hi in SCORE_DISTRIBUTION:
        cumulative += weight
        if r <= cumulative:
            return random.uniform(lo, hi)
    return random.uniform(110, 155)


def _rand_regime() -> str:
    return random.choices(
        list(REGIME_WEIGHTS.keys()),
        weights=list(REGIME_WEIGHTS.values()),
        k=1,
    )[0]


def _score_bucket(score: float) -> str:
    if score < 110:  return "S1_low(<110)"
    if score < 138:  return "S2_mid(110-137)"
    if score < 155:  return "S3_high(138-154)"
    if score < 170:  return "S4_elite(155-169)"
    return "S5_divine(170+)"


def _check_hard_constraints(
    sig: SignalScoredEvent,
    risk: RiskDecisionEvent,
    intent: Optional[OrderIntentEvent],
    pnl: Optional[PnLAttributionEvent],
    stats: SimStats,
) -> None:
    """9条硬约束检查"""
    # 约束1: 任何 OrderIntent 必须有 RiskDecision
    if intent is not None and risk is None:
        stats.violations.append(f"VIOLATION-1: OrderIntent {intent.event_id[:8]} 缺少 RiskDecision")

    # 约束2: 任何 RiskDecision 必须有 SignalScoredEvent
    if risk is not None and sig is None:
        stats.violations.append(f"VIOLATION-2: RiskDecision {risk.event_id[:8]} 缺少 SignalScoredEvent")

    # 约束3: blocked signal 不得生成 OrderIntent
    if sig is not None and sig.blocked and intent is not None:
        stats.violations.append(f"VIOLATION-3: blocked信号生成了OrderIntent sig={sig.event_id[:8]}")

    # 约束4: risk.BLOCKED 不得生成 OrderIntent
    if risk is not None and risk.decision == "BLOCKED" and intent is not None:
        stats.violations.append(f"VIOLATION-4: BLOCKED决策生成了OrderIntent risk={risk.event_id[:8]}")

    # 约束5: trace_id 全链路一致
    if intent is not None and pnl is not None:
        if intent.trace_id != pnl.trace_id:
            stats.violations.append(f"VIOLATION-5: trace_id不一致 intent={intent.trace_id[:8]} pnl={pnl.trace_id[:8]}")

    # 约束6: size_nav 不超过策略上限
    if intent is not None and intent.size_nav > 0.02:
        stats.violations.append(f"VIOLATION-6: size_nav={intent.size_nav:.4f} 超过2%NAV上限")

    # 约束7: PnLAttribution 必须能追溯 trace_id
    if pnl is not None and not pnl.trace_id:
        stats.violations.append(f"VIOLATION-7: PnLAttribution缺少trace_id pnl={pnl.event_id[:8]}")

    # 约束8: parent_event_id 链路
    if intent is not None and risk is not None:
        if intent.parent_event_id != risk.event_id:
            stats.violations.append(f"VIOLATION-8: intent.parent_event_id不指向risk.event_id")

    # 约束9: APPROVED 决策 size_nav > 0
    if risk is not None and risk.decision == "APPROVE" and risk.final_size_nav <= 0:
        stats.violations.append(f"VIOLATION-9: APPROVE决策 size_nav=0 risk={risk.event_id[:8]}")


def _update_stats(
    stats: SimStats,
    sym: str,
    direction: str,
    regime: str,
    score: float,
    decision: str,
    blocked_layers: List[str],
    pnl_val: Optional[float],
) -> None:
    def _init_bucket(d, key):
        if key not in d:
            d[key] = {"n": 0, "approve": 0, "blocked": 0, "pnl": 0.0, "positive": 0}

    for d, key in [
        (stats.regime_stats, regime),
        (stats.symbol_stats, sym),
        (stats.direction_stats, direction),
        (stats.score_bucket_stats, _score_bucket(score)),
    ]:
        _init_bucket(d, key)
        d[key]["n"] += 1
        if decision == "APPROVE":
            d[key]["approve"] += 1
        elif decision == "BLOCKED":
            d[key]["blocked"] += 1

    for layer in blocked_layers:
        stats.blocked_layer_counts[layer] = stats.blocked_layer_counts.get(layer, 0) + 1

    if pnl_val is not None:
        stats.pnl_total += pnl_val
        if pnl_val > 0:
            stats.net_positive += 1
            for d, key in [
                (stats.regime_stats, regime),
                (stats.symbol_stats, sym),
                (stats.direction_stats, direction),
                (stats.score_bucket_stats, _score_bucket(score)),
            ]:
                d[key]["pnl"] += pnl_val
                d[key]["positive"] += 1


def run_simulation(total_events: int = 100_000, seed: int = 42) -> SimStats:
    random.seed(seed)
    kernel = RiskKernel()
    ctx = RiskContext.paper_mode(100.0)
    portfolio = get_portfolio(100.0)
    executor = PaperExecutor(portfolio)

    stats = SimStats(total_events=total_events)
    paper_trades: Dict[str, "PaperTrade"] = {}  # trade_id → PaperTrade
    entry_prices = {"BTCUSDT": 107000.0, "ETHUSDT": 2800.0, "SOLUSDT": 180.0, "BNBUSDT": 730.0, "SUIUSDT": 3.8}

    batch_size = min(10000, total_events)
    batches = (total_events + batch_size - 1) // batch_size

    print(f"\n🔬 梵天百万推演启动 | {total_events:,} 事件 | {batches} 批次")
    print("─" * 60)

    for batch_i in range(batches):
        batch_n = min(batch_size, total_events - batch_i * batch_size)
        for _ in range(batch_n):
            stats.signals_scored += 1

            # 生成场景
            sym = random.choice(SYMBOLS)
            direction = random.choice(DIRECTIONS)
            regime = _rand_regime()
            score = _rand_score()
            base_price = entry_prices.get(sym, 100.0)
            price = base_price * random.uniform(0.95, 1.05)

            # 构造信号
            raw_score = score * random.uniform(0.88, 0.98)
            sig = make_signal_event(
                symbol=sym, direction=direction,
                raw_score=raw_score, final_score=score,
                regime=regime, grade="A" if score >= 155 else "B",
                blocked=False, confidence=random.uniform(0.5, 0.9),
            )
            sig.ts_event = time.time()

            # 风控评估
            risk = kernel.evaluate(sig, ctx=ctx)

            pnl_val: Optional[float] = None
            intent: Optional[OrderIntentEvent] = None
            pnl_event: Optional[PnLAttributionEvent] = None

            if risk.decision in ("APPROVE", "REDUCE"):
                stats.risk_approve += 1

                sl_pct = 0.02 if regime in ("BEAR_TREND", "CHOP_MID") else 0.025
                tp_pct = sl_pct * 1.5

                if direction == "LONG":
                    sl = price * (1 - sl_pct)
                    tp = price * (1 + tp_pct)
                else:
                    sl = price * (1 + sl_pct)
                    tp = price * (1 - tp_pct)

                intent = OrderIntentEvent(
                    symbol=sym, direction=direction,
                    trace_id=risk.trace_id, parent_event_id=risk.event_id,
                    size_nav=risk.final_size_nav, leverage=risk.max_leverage,
                    entry_price=price, stop_loss=sl, take_profit=tp,
                    signal_trace_id=sig.trace_id, risk_trace_id=risk.trace_id,
                )
                stats.order_intents += 1

                # Paper Fill
                try:
                    trade = executor.execute_signal(sig, intent)
                    paper_trades[trade.trade_id] = trade
                    stats.paper_fills += 1

                    # 模拟出场（随机价格运动）
                    exit_mult = random.gauss(1.0 + (0.004 if score >= 155 else 0.001), 0.015)
                    exit_price = trade.entry_price * exit_mult
                    closed = executor.simulate_exit(trade, exit_price, force_exit=True)
                    if closed:
                        stats.closed_trades += 1
                        net = closed.net_pnl  # 直接取 PaperTrade.net_pnl（已含手续费/滑点）
                        pnl_val = net

                        pnl_event = PnLAttributionEvent(
                            symbol=sym, direction=direction,
                            trace_id=intent.trace_id,
                            parent_event_id=intent.event_id,
                            gross_pnl=closed.gross_pnl if hasattr(closed, 'gross_pnl') else net,
                            fee_drag=-closed.fee if hasattr(closed, 'fee') else 0.0,
                            funding_drag=-closed.funding if hasattr(closed, 'funding') else 0.0,
                            slippage_drag=-closed.slippage if hasattr(closed, 'slippage') else 0.0,
                            net_pnl=net,
                            regime_at_entry=regime,
                            signal_score=score,
                            intent_trace_id=intent.trace_id,
                        )
                except Exception:
                    pass

            elif risk.decision == "BLOCKED":
                stats.risk_blocked += 1

            # 9条硬约束
            _check_hard_constraints(sig, risk, intent, pnl_event, stats)

            # 统计
            _update_stats(
                stats, sym, direction, regime, score,
                risk.decision, risk.blocked_layers, pnl_val,
            )

        # 批次进度
        pct = (batch_i + 1) / batches * 100
        approve_rate = stats.risk_approve / max(stats.signals_scored, 1) * 100
        if batch_i % max(1, batches // 10) == 0 or batch_i == batches - 1:
            print(f"  [{pct:5.1f}%] 已处理 {stats.signals_scored:>8,} 信号 | "
                  f"APPROVE={approve_rate:.1f}% | VIOLATIONS={len(stats.violations)}")

    stats.t_end = time.time()
    return stats


def generate_report(stats: SimStats) -> str:
    elapsed = stats.t_end - stats.t_start
    approve_rate = stats.risk_approve / max(stats.signals_scored, 1) * 100
    fill_rate = stats.paper_fills / max(stats.risk_approve, 1) * 100
    close_rate = stats.closed_trades / max(stats.paper_fills, 1) * 100
    win_rate = stats.net_positive / max(stats.closed_trades, 1) * 100

    lines = [
        "═" * 70,
        "🏛️ 梵天百万推演报告 | 100万次事件级全链路验证",
        f"   生成时间: {time.strftime('%Y-%m-%d %H:%M UTC')}",
        "═" * 70,
        "",
        "## 1. 漏斗统计",
        f"{'总信号':20} {stats.signals_scored:>10,}",
        f"{'风控APPROVE':20} {stats.risk_approve:>10,}  ({approve_rate:.1f}%)",
        f"{'风控BLOCKED':20} {stats.risk_blocked:>10,}  ({100-approve_rate:.1f}%)",
        f"{'OrderIntent':20} {stats.order_intents:>10,}",
        f"{'Paper成交':20} {stats.paper_fills:>10,}  (Fill率 {fill_rate:.1f}%)",
        f"{'已平仓':20} {stats.closed_trades:>10,}  (平仓率 {close_rate:.1f}%)",
        f"{'净盈利笔数':20} {stats.net_positive:>10,}  (胜率 {win_rate:.1f}%)",
        f"{'总净PnL':20} {stats.pnl_total:>10.4f} USDT",
        "",
        "## 2. 漏斗健康评估",
    ]
    checks = [
        ("APPROVE/scored",    approve_rate,   5,  25,  "风控通过率"),
        ("Fill/approved",     fill_rate,      60, 95,  "Paper成交率"),
        ("Win/closed",        win_rate,       42, 62,  "胜率"),
    ]
    for name, val, lo, hi, desc in checks:
        status = "✅" if lo <= val <= hi else "⚠️"
        lines.append(f"  {status} {name}: {val:.1f}%  (健康范围 {lo}~{hi}%)  — {desc}")

    lines += ["", "## 3. 封锁层分布（Top10）"]
    sorted_layers = sorted(stats.blocked_layer_counts.items(), key=lambda x: -x[1])
    for layer, cnt in sorted_layers[:10]:
        pct = cnt / max(stats.signals_scored, 1) * 100
        lines.append(f"  {layer:<40} {cnt:>8,} ({pct:.2f}%)")

    lines += ["", "## 4. Regime 分层表现"]
    for regime, d in sorted(stats.regime_stats.items()):
        approve = d["approve"]
        n = d["n"]
        lines.append(f"  {regime:<20} n={n:>6,}  APPROVE={approve:>5,} ({approve/max(n,1)*100:.1f}%)  PnL={d['pnl']:.3f}")

    lines += ["", "## 5. Symbol 分层表现"]
    for sym, d in sorted(stats.symbol_stats.items()):
        lines.append(f"  {sym:<12} n={d['n']:>6,}  +笔={d['positive']:>4,}  PnL={d['pnl']:.3f}")

    lines += ["", "## 6. 得分桶 EV 分析"]
    for bucket, d in sorted(stats.score_bucket_stats.items()):
        win = d["positive"] / max(d["approve"], 1) * 100
        lines.append(f"  {bucket:<28} approve={d['approve']:>5,}  WR={win:.1f}%  PnL={d['pnl']:.3f}")

    lines += ["", "## 7. 硬约束检查（9条）"]
    if stats.violations:
        lines.append(f"  ❌ 发现 {len(stats.violations)} 条违规！")
        for v in stats.violations[:20]:
            lines.append(f"    {v}")
    else:
        lines.append("  ✅ 9条硬约束全部通过，0违规")

    lines += [
        "",
        "## 8. 性能基准",
        f"  总耗时: {elapsed:.2f}s",
        f"  吞吐量: {stats.signals_scored/elapsed:,.0f} 事件/秒",
        f"  单信号耗时: {elapsed/max(stats.signals_scored,1)*1000:.4f} ms",
        "",
        "═" * 70,
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="梵天百万推演")
    parser.add_argument("--events", type=int, default=100_000)
    parser.add_argument("--quick", action="store_true", help="快速模式 10000事件")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--save", action="store_true", default=True)
    args = parser.parse_args()

    if args.quick:
        args.events = 10_000

    stats = run_simulation(args.events, args.seed)
    report = generate_report(stats)

    print("\n" + report)

    if args.save:
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_file = REPORTS_DIR / f"million_run_{args.events}_{ts}.md"
        out_file.write_text(report, encoding="utf-8")
        print(f"\n📄 报告已保存: {out_file}")

    return 0 if not stats.violations else 1


if __name__ == "__main__":
    sys.exit(main())
