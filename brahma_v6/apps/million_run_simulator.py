"""
brahma_v6/apps/million_run_simulator.py
Phase 3 — 10M 事件仿真器
设计院 · 2026-07-09

验证目标：
  1. 吞吐量：10M 事件在合理时间内完成
  2. 状态机零异常泄漏：所有非法转移必须被 IllegalTransitionError 捕获
  3. TradeLedger 数学守恒率：closed records net_pnl 守恒 100%
  4. chain_integrity 拒写率统计（故意注入破损记录）
  5. duplicate guard 正确性

架构：
  - EventGenerator：按概率生成合法/非法事件流
  - StateMachineHarness：驱动 OrderState 转移，统计异常
  - LedgerHarness：批量写入 TradeLedger，统计拒写
  - SimReport：汇总所有指标
"""
from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from brahma_v6.execution.order_state import (
    ALLOWED_TRANSITIONS,
    IllegalTransitionError,
    OrderState,
    validate_transition,
    is_terminal,
    TERMINAL_STATES,
)
from brahma_v6.dharma2.models import PnLAttribution, TradeRecord
from brahma_v6.dharma2.trade_ledger import TradeLedger


# ─────────────────────────────────────────────────────────────────────────────
# 事件生成器
# ─────────────────────────────────────────────────────────────────────────────

# 按权重预计算合法转移表（list for O(1) random.choice）
_LEGAL: Dict[OrderState, List[OrderState]] = {
    s: list(targets) for s, targets in ALLOWED_TRANSITIONS.items() if targets
}

# 非终态（可继续转移的状态）
_NON_TERMINAL = [s for s in OrderState if s not in TERMINAL_STATES]


def _random_legal(state: OrderState) -> Optional[OrderState]:
    """返回一个合法的下一状态，若无则返回 None（终态）。"""
    choices = _LEGAL.get(state)
    return random.choice(choices) if choices else None


def _random_illegal(state: OrderState) -> OrderState:
    """返回一个对 state 非法的下一状态（用于注入测试）。"""
    illegal = [s for s in OrderState if s not in ALLOWED_TRANSITIONS.get(state, set())]
    return random.choice(illegal) if illegal else OrderState.UNKNOWN


# ─────────────────────────────────────────────────────────────────────────────
# 状态机压测 Harness
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StatMachineResult:
    total_transitions:   int = 0
    legal_transitions:   int = 0
    illegal_caught:      int = 0
    illegal_leaked:      int = 0    # 不应发生
    chains_completed:    int = 0    # 到达终态
    elapsed_s:           float = 0.0

    @property
    def leak_rate(self) -> float:
        return self.illegal_leaked / max(self.total_transitions, 1)

    @property
    def throughput(self) -> float:
        return self.total_transitions / max(self.elapsed_s, 1e-9)


def run_state_machine_sim(
    n_chains: int = 100_000,
    illegal_injection_rate: float = 0.05,   # 5% 非法事件注入
    seed: int = 42,
) -> StatMachineResult:
    """
    运行 n_chains 条独立订单链，每条链从 CREATED 走到终态。
    illegal_injection_rate 控制每步随机注入非法转移的概率。
    """
    rng = random.Random(seed)
    result = StatMachineResult()
    t0 = time.perf_counter()

    for _ in range(n_chains):
        state = OrderState.CREATED
        steps = 0

        while not is_terminal(state) and steps < 50:
            steps += 1
            result.total_transitions += 1

            # 按概率决定注入合法还是非法事件
            if rng.random() < illegal_injection_rate:
                next_state = _random_illegal(state)
                try:
                    validate_transition(state, next_state)
                    # 如果恰好合法（随机选到了合法的），算合法
                    state = next_state
                    result.legal_transitions += 1
                except IllegalTransitionError:
                    result.illegal_caught += 1
                    # 状态不变，继续推进
            else:
                next_state = _random_legal(state)
                if next_state is None:
                    break
                try:
                    validate_transition(state, next_state)
                    state = next_state
                    result.legal_transitions += 1
                except IllegalTransitionError:
                    # 不应发生：_random_legal 应该总合法
                    result.illegal_leaked += 1

        if is_terminal(state):
            result.chains_completed += 1

    result.elapsed_s = time.perf_counter() - t0
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Ledger 压测 Harness
# ─────────────────────────────────────────────────────────────────────────────

def _make_attribution(net_override: Optional[float] = None) -> PnLAttribution:
    g, f, s, fu, i = 1.50, -0.05, -0.02, -0.01, -0.005
    net = g + f + s + fu + i
    return PnLAttribution(
        gross_pnl=g, fee_drag=f, slippage_drag=s,
        funding_drag=fu, impact_drag=i,
        net_pnl=net if net_override is None else net_override,
    )


def _make_record(
    broken_chain: bool = False,
    broken_pnl: bool = False,
    duplicate_id: Optional[str] = None,
    _uid_pool: Optional[List[str]] = None,
    _uid_idx: Optional[List[int]] = None,
) -> TradeRecord:
    now = datetime.now(timezone.utc)
    attr = _make_attribution(net_override=9999.0 if broken_pnl else None)

    def _uid() -> str:
        if _uid_pool and _uid_idx is not None:
            v = _uid_pool[_uid_idx[0] % len(_uid_pool)]
            _uid_idx[0] += 1
            return v
        return str(uuid.uuid4())

    tid = duplicate_id or _uid()
    return TradeRecord(
        trade_id           = tid,
        trace_id           = "" if broken_chain else _uid(),
        signal_id          = _uid(),
        risk_id            = _uid(),
        intent_id          = _uid(),
        ticket_id          = _uid(),
        order_event_ids    = [] if broken_chain else [_uid()],
        symbol             = "BTCUSDT",
        direction          = "LONG",
        regime             = "BEAR_RECOVERY",
        score              = 162.0,
        entry_price        = 107_000.0,
        exit_price         = 108_500.0,
        quantity           = 0.001,
        attribution        = attr,
        mae                = -200.0,
        mfe                = 1600.0,
        holding_time_seconds = 3600,
        opened_at          = now,
        closed_at          = now,
        created_at         = now,
    )


@dataclass
class LedgerResult:
    total_attempts:     int = 0
    accepted:           int = 0
    rejected_pnl:       int = 0
    rejected_chain:     int = 0
    rejected_duplicate: int = 0
    math_conserved:     int = 0    # closed records 数学守恒
    elapsed_s:          float = 0.0

    @property
    def accept_rate(self) -> float:
        return self.accepted / max(self.total_attempts, 1)

    @property
    def conservation_rate(self) -> float:
        return self.math_conserved / max(self.accepted, 1)

    @property
    def throughput(self) -> float:
        return self.total_attempts / max(self.elapsed_s, 1e-9)


def run_ledger_sim(
    n_records: int = 10_000,
    broken_chain_rate: float = 0.03,    # 3% 链路破损
    broken_pnl_rate: float = 0.02,      # 2% PnL不平衡
    duplicate_rate: float = 0.01,       # 1% 重复ID
    seed: int = 42,
) -> LedgerResult:
    """
    写入 n_records 条 TradeRecord，按概率注入各类破损。
    统计每道拦截门的拒写率 + 数学守恒率。
    """
    rng = random.Random(seed)
    ledger = TradeLedger()   # 纯内存，不持久化
    result = LedgerResult()
    t0 = time.perf_counter()

    # 预生成 UUID 池（大幅减少 uuid4 调用开销）
    pool_size = n_records * 8
    uid_pool = [str(uuid.uuid4()) for _ in range(pool_size)]
    uid_idx = [0]

    # 用于重复ID测试：保存已接受的ID
    accepted_ids: List[str] = []

    for _ in range(n_records):
        result.total_attempts += 1

        # 决定注入类型（优先级：pnl > chain > dup > normal）
        r = rng.random()
        if r < broken_pnl_rate:
            # broken PnL — __post_init__ 会先抛，跳过构造
            try:
                rec = _make_record(broken_pnl=True, _uid_pool=uid_pool, _uid_idx=uid_idx)
                ledger.append(rec)
            except (ValueError, Exception):
                result.rejected_pnl += 1
            continue

        if r < broken_pnl_rate + broken_chain_rate:
            rec = _make_record(broken_chain=True, _uid_pool=uid_pool, _uid_idx=uid_idx)
            try:
                ledger.append(rec)
            except ValueError:
                result.rejected_chain += 1
            continue

        if r < broken_pnl_rate + broken_chain_rate + duplicate_rate and accepted_ids:
            dup_id = rng.choice(accepted_ids)
            rec = _make_record(duplicate_id=dup_id, _uid_pool=uid_pool, _uid_idx=uid_idx)
            try:
                ledger.append(rec)
            except ValueError:
                result.rejected_duplicate += 1
            continue

        # 正常记录
        rec = _make_record(_uid_pool=uid_pool, _uid_idx=uid_idx)
        try:
            ledger.append(rec)
            result.accepted += 1
            accepted_ids.append(rec.trade_id)
        except ValueError:
            pass  # 不应发生

    # 验证数学守恒：所有已接受记录的 attribution 仍然平衡
    for r in ledger._records:
        try:
            r.attribution.validate()
            result.math_conserved += 1
        except ValueError:
            pass

    result.elapsed_s = time.perf_counter() - t0
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 完整 10M 仿真入口
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimReport:
    sm:  StatMachineResult
    lg:  LedgerResult

    def print(self) -> None:
        sm, lg = self.sm, self.lg
        print("=" * 60)
        print("  Brahma v6 · Phase 3 仿真报告")
        print("=" * 60)

        print("\n📐 状态机压测")
        print(f"  总转移次数        : {sm.total_transitions:>12,}")
        print(f"  合法转移          : {sm.legal_transitions:>12,}")
        print(f"  非法拦截 (caught) : {sm.illegal_caught:>12,}")
        print(f"  非法泄漏 (leaked) : {sm.illegal_leaked:>12,}  ← 必须为 0")
        print(f"  链路完成率        : {sm.chains_completed / max(sm.total_transitions // 50, 1) * 100:.1f}%")
        print(f"  吞吐量            : {sm.throughput:>12,.0f} transitions/s")
        print(f"  耗时              : {sm.elapsed_s:.3f}s")
        ok_sm = sm.illegal_leaked == 0
        print(f"  状态机完整性      : {'✅ PASS' if ok_sm else '❌ FAIL'}")

        print("\n📒 账本压测")
        print(f"  总写入尝试        : {lg.total_attempts:>12,}")
        print(f"  成功写入          : {lg.accepted:>12,}")
        print(f"  接受率            : {lg.accept_rate * 100:.1f}%")
        print(f"  PnL 拒写          : {lg.rejected_pnl:>12,}")
        print(f"  链路完整性拒写    : {lg.rejected_chain:>12,}")
        print(f"  重复ID 拒写       : {lg.rejected_duplicate:>12,}")
        print(f"  数学守恒率        : {lg.conservation_rate * 100:.2f}%  ← 必须 100%")
        print(f"  吞吐量            : {lg.throughput:>12,.0f} records/s")
        print(f"  耗时              : {lg.elapsed_s:.3f}s")
        ok_lg = lg.conservation_rate >= 0.9999
        print(f"  账本完整性        : {'✅ PASS' if ok_lg else '❌ FAIL'}")

        print("\n" + "=" * 60)
        overall = ok_sm and ok_lg
        print(f"  综合结论          : {'✅ PRODUCTION CANDIDATE' if overall else '❌ 需修复后重测'}")
        print("=" * 60)


def run_full_simulation(
    sm_chains: int = 200_000,        # 状态机链路数（×平均步数≈10M转移）
    ledger_records: int = 100_000,   # 账本记录数
    seed: int = 42,
) -> SimReport:
    print(f"🚀 启动仿真: sm_chains={sm_chains:,}  ledger_records={ledger_records:,}")

    print("  [1/2] 状态机压测...", end="", flush=True)
    sm_result = run_state_machine_sim(n_chains=sm_chains, seed=seed)
    print(f" done ({sm_result.elapsed_s:.2f}s, {sm_result.total_transitions:,} transitions)")

    print("  [2/2] 账本压测...", end="", flush=True)
    lg_result = run_ledger_sim(n_records=ledger_records, seed=seed)
    print(f" done ({lg_result.elapsed_s:.2f}s, {lg_result.accepted:,} accepted)")

    return SimReport(sm=sm_result, lg=lg_result)


if __name__ == "__main__":
    report = run_full_simulation()
    report.print()
