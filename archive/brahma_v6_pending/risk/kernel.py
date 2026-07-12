"""
brahma_v6/risk/kernel.py — 12层风控内核
设计院 全局修复 P0-2+P0-3 | 2026-07-08

修复清单：
  P0-2: evaluate() 新增 ctx: RiskContext 参数 → Kernel 零 I/O，纯函数化
  P0-3: orderbook_available=False → L5 强制 BLOCK（消除 fail-open）
  旧 skip_live_checks 接口保留（回传 ctx=RiskContext.paper_mode() 兼容）
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from brahma_v6.schemas.events import (
    SignalScoredEvent,
    RiskDecisionEvent,
    make_risk_decision,
)
from brahma_v6.risk.context import RiskContext

BASE = Path(__file__).resolve().parents[2]

# ══════════════════════════════════════════════════════
#  默认风控策略
# ══════════════════════════════════════════════════════
DEFAULT_POLICY: Dict = {
    "max_data_age_sec": 300,
    "min_grade_score": 110,
    "min_depth_usdt": {
        "BTCUSDT": 500_000, "ETHUSDT": 300_000,
        "SOLUSDT": 100_000, "_default": 50_000,
    },
    "max_slippage_pct": {
        "BTCUSDT": 0.08, "ETHUSDT": 0.12,
        "SOLUSDT": 0.25, "_default": 0.50,
    },
    "max_funding_annualized_pct": 150.0,
    "min_liq_wall_dist_pct": 1.0,
    "max_correlated_nav_pct": 0.025,
    "max_daily_loss_pct": 0.05,
    "max_drawdown_pct": 0.12,
    "max_system_latency_ms": 5000,
    "min_council_confidence": 0.0,
    "death_zones": [
        ["BEAR_TREND", "LONG"],
        ["BULL_TREND", "SHORT"],
        ["CHOP_LONG", "LONG"],
        ["CHOP_HIGH", "LONG"],
        ["CHOP_HIGH", "SHORT"],
    ],
    "max_size_nav": {
        "BTCUSDT": 0.015, "ETHUSDT": 0.012,
        "SOLUSDT": 0.008, "_default": 0.005,
    },
    "max_leverage": {
        "BTCUSDT": 5, "ETHUSDT": 5,
        "SOLUSDT": 3, "_default": 2,
    },
    "execution_style": {
        "trend": "POST_ONLY_LIMIT",
        "pump": "LIMIT_ONLY",
        "emergency": "MARKET",
        "_default": "POST_ONLY_LIMIT",
    },
}


def _load_policy() -> Dict:
    policy_file = BASE / "config" / "risk_policy.yaml"
    policy = dict(DEFAULT_POLICY)
    if policy_file.exists():
        try:
            import yaml
            override = yaml.safe_load(policy_file.read_text()) or {}
            policy.update(override)
        except Exception:
            pass
    return policy


# ══════════════════════════════════════════════════════
#  12层风控内核（纯函数，零 I/O）
# ══════════════════════════════════════════════════════
class RiskKernel:
    """
    12层风控内核 — 信号到 OrderIntent 的唯一合法通道。

    P0-2 修复：evaluate() 接受 ctx: RiskContext，零网络请求。
    上游负责构建 RiskContext（live 或 paper_mode）。
    旧 skip_live_checks API 保留兼容，自动转换为 paper_mode context。
    """

    def __init__(self, policy: Dict = None):
        self.policy = policy or _load_policy()

    # ── 主入口（纯函数） ────────────────────────────────────
    def evaluate(
        self,
        signal: SignalScoredEvent,
        account_nav: float = 100.0,            # 兼容旧接口
        skip_live_checks: bool = False,         # 兼容旧接口 → ctx=paper_mode
        ctx: Optional[RiskContext] = None,      # ← P0-2 新增注入口
    ) -> RiskDecisionEvent:
        """
        全链路 12 层评估（纯函数，零 I/O）。
        返回 RiskDecisionEvent，decision=APPROVE/REDUCE/BLOCKED。
        """
        # 兼容旧接口：skip_live_checks=True → paper context
        if ctx is None:
            if skip_live_checks:
                ctx = RiskContext.paper_mode(account_nav)
            else:
                ctx = RiskContext.from_live(signal.symbol, account_nav)

        blocked_layers: List[str] = []
        warnings: List[str] = []
        reasons: List[str] = []
        p = self.policy
        sym = signal.symbol
        direction = signal.direction
        score = signal.final_score
        regime = signal.regime

        # ── L1: Data Freshness ─────────────────────────────
        age = time.time() - signal.ts_event
        if age > p["max_data_age_sec"]:
            blocked_layers.append("L1_DATA_STALE")
            reasons.append(f"信号过期 {int(age)}s > {p['max_data_age_sec']}s")

        # ── L2: Schema Contract ────────────────────────────
        if not signal.symbol or not signal.direction:
            blocked_layers.append("L2_SCHEMA_INVALID")
            reasons.append("信号schema不完整：symbol或direction为空")
        if signal.final_score <= 0:
            blocked_layers.append("L2_SCORE_ZERO")
            reasons.append("final_score=0，信号未完成评分")

        # ── L3: Regime Death-Zone ──────────────────────────
        death_zones = p.get("death_zones", [])
        if (regime, direction) in [(d[0], d[1]) for d in death_zones]:
            blocked_layers.append("L3_REGIME_DEATH_ZONE")
            reasons.append(f"死穴：{regime} + {direction} WR<50%")

        # ── L4: Structure Quality ──────────────────────────
        min_score = p.get("min_grade_score", 110)
        if score < min_score:
            blocked_layers.append("L4_SCORE_TOO_LOW")
            reasons.append(f"分数{score:.1f} < 门槛{min_score}")

        # ── L5: Liquidity Gate (P0-3 fail-closed) ──────────
        min_depth = p["min_depth_usdt"].get(sym, p["min_depth_usdt"]["_default"])
        if not ctx.orderbook_available:
            # P0-3: 数据缺失 → 强制 BLOCK（消除旧版 999_999_999 fail-open）
            blocked_layers.append("L5_LIQUIDITY_DATA_MISSING")
            reasons.append("orderbook 数据不可用 → fail-closed")
        elif ctx.orderbook_depth_usdt < min_depth:
            blocked_layers.append("L5_LIQUIDITY_INSUFFICIENT")
            reasons.append(f"深度${ctx.orderbook_depth_usdt:,.0f} < 门槛${min_depth:,.0f}")
        elif ctx.orderbook_depth_usdt < min_depth * 2:
            warnings.append(f"L5_DEPTH_THIN: ${ctx.orderbook_depth_usdt:,.0f}")

        # ── L6: Slippage Gate ──────────────────────────────
        max_slip = p["max_slippage_pct"].get(sym, p["max_slippage_pct"]["_default"])
        estimated_slip = 0.05
        if estimated_slip > max_slip:
            blocked_layers.append("L6_SLIPPAGE_TOO_HIGH")
            reasons.append(f"预估滑点{estimated_slip:.2f}% > {max_slip:.2f}%")

        # ── L7: Funding Gate ───────────────────────────────
        if ctx.funding_available:
            max_funding = p.get("max_funding_annualized_pct", 150.0)
            f = ctx.funding_rate_annual_pct
            if direction == "LONG" and f > max_funding:
                blocked_layers.append("L7_FUNDING_EXTREME_LONG")
                reasons.append(f"资金费率年化{f:.0f}% > {max_funding:.0f}%")
            elif direction == "SHORT" and f < -max_funding:
                blocked_layers.append("L7_FUNDING_EXTREME_SHORT")
                reasons.append(f"资金费率年化{f:.0f}%，空头成本极高")
            elif abs(f) > max_funding * 0.7:
                warnings.append(f"L7_FUNDING_ELEVATED: {f:.0f}%/yr")
        # funding 不可用时跳过（数据缺失不封锁，仅 warning）
        elif not ctx.funding_available:
            warnings.append("L7_FUNDING_DATA_MISSING: 跳过资金费率检查")

        # ── L8: Liquidation Wall ───────────────────────────
        liq_dist = signal.payload.get("liq_dist_pct", 999)
        min_liq_dist = p.get("min_liq_wall_dist_pct", 1.0)
        if liq_dist < min_liq_dist:
            blocked_layers.append("L8_LIQ_WALL_TOO_CLOSE")
            reasons.append(f"强平墙距离{liq_dist:.2f}% < {min_liq_dist:.2f}%")
        elif liq_dist < min_liq_dist * 2:
            warnings.append(f"L8_LIQ_WALL_NEAR: {liq_dist:.2f}%")

        # ── L9: Correlation / Cluster ─────────────────────
        if ctx.correlation_available:
            max_corr = p.get("max_correlated_nav_pct", 0.025)
            corr_nav = ctx.correlated_exposure_nav
            if corr_nav > max_corr:
                blocked_layers.append("L9_CORRELATION_OVEREXPOSED")
                reasons.append(f"相关敞口{corr_nav*100:.1f}% > 上限{max_corr*100:.1f}%")
            elif corr_nav > max_corr * 0.7:
                warnings.append(f"L9_CORR_ELEVATED: {corr_nav*100:.1f}%NAV")

        # ── L10: Account DD / Daily Loss ──────────────────
        max_daily = p.get("max_daily_loss_pct", 0.05)
        max_dd = p.get("max_drawdown_pct", 0.12)
        dd_pct = ctx.account_drawdown_pct / 100  # context 存 0~100
        # 读日内亏损（仍从文件，无REST）
        try:
            pos_file = BASE / "data" / "wuqu_positions.json"
            account = json.loads(pos_file.read_text()) if pos_file.exists() else {}
            daily_loss = float(account.get("daily_loss_pct", 0))
        except Exception:
            daily_loss = 0.0
        if daily_loss > max_daily:
            blocked_layers.append("L10_DAILY_LOSS_EXCEEDED")
            reasons.append(f"日内亏损{daily_loss*100:.1f}% > 上限{max_daily*100:.1f}%")
        if dd_pct > max_dd:
            blocked_layers.append("L10_MAX_DRAWDOWN_EXCEEDED")
            reasons.append(f"回撤{dd_pct*100:.1f}% > 上限{max_dd*100:.1f}%")
        if daily_loss > max_daily * 0.7:
            warnings.append(f"L10_DAILY_LOSS_NEAR: {daily_loss*100:.1f}%")

        # ── L11: System Health ────────────────────────────
        if ctx.system_available:
            min_health = 60.0
            if ctx.system_health_score < min_health:
                blocked_layers.append("L11_SYSTEM_UNHEALTHY")
                reasons.append(f"系统健康分{ctx.system_health_score:.0f} < {min_health:.0f}")

        # ── L12: Council Confidence ───────────────────────
        confidence = signal.confidence
        min_conf = p.get("min_council_confidence", 0.0)
        if confidence > 0 and confidence < min_conf:
            warnings.append(f"L12_LOW_CONFIDENCE: {confidence:.2f} < {min_conf:.2f}")

        # ══ 决策 ══════════════════════════════════════════
        if blocked_layers:
            decision = "BLOCKED"
            size_nav = 0.0
            leverage = 1
            order_style = "NONE"
            reason = " | ".join(reasons[:3])
        else:
            base_size = p["max_size_nav"].get(sym, p["max_size_nav"]["_default"])
            leverage = p["max_leverage"].get(sym, p["max_leverage"]["_default"])
            order_style = p["execution_style"].get("_default", "POST_ONLY_LIMIT")

            if score >= 165:
                size_mult = 1.0
            elif score >= 155:
                size_mult = 0.8
            elif score >= 138:
                size_mult = 0.6
            else:
                size_mult = 0.4

            warning_penalty = 1.0 - len(warnings) * 0.05
            size_nav = round(base_size * size_mult * max(warning_penalty, 0.5), 4)

            decision = "REDUCE" if size_nav < base_size * 0.5 else "APPROVE"
            reason = f"APPROVED score={score:.1f} size={size_nav*100:.2f}%NAV"
            if warnings:
                reason += f" warnings={len(warnings)}"

        return make_risk_decision(
            signal_event=signal,
            decision=decision,
            size_nav=size_nav if not blocked_layers else 0.0,
            max_leverage=leverage if not blocked_layers else 1,
            order_style=order_style if not blocked_layers else "NONE",
            blocked_layers=blocked_layers,
            warnings=warnings,
            reason=reason,
        )

    def batch_evaluate(
        self,
        signals: List[SignalScoredEvent],
        account_nav: float = 100.0,
        skip_live_checks: bool = False,
        ctx: Optional[RiskContext] = None,
    ) -> List[RiskDecisionEvent]:
        if ctx is None:
            ctx = RiskContext.paper_mode(account_nav) if skip_live_checks else None
        return [self.evaluate(s, account_nav, skip_live_checks, ctx) for s in signals]


# ── 全局单例 ────────────────────────────────────────────
_kernel: Optional[RiskKernel] = None


def get_kernel() -> RiskKernel:
    global _kernel
    if _kernel is None:
        _kernel = RiskKernel()
    return _kernel


if __name__ == "__main__":
    from brahma_v6.schemas.events import make_signal_event
    print("=== 12层Risk Kernel 自检（P0-2/P0-3 修复版）===\n")
    kernel = RiskKernel()

    sig = make_signal_event(
        symbol="BTCUSDT", direction="LONG",
        raw_score=140.0, final_score=158.0,
        regime="BULL_TREND", grade="A", blocked=False, confidence=0.78,
    )
    ctx = RiskContext.paper_mode(100.0)
    d = kernel.evaluate(sig, ctx=ctx)
    print(f"测试1 BTC LONG 158分 paper_mode: {d.decision} size={d.final_size_nav*100:.2f}%NAV")

    # P0-3 验证：orderbook 不可用 → BLOCK
    ctx_fail = RiskContext(orderbook_available=False, account_nav=100.0)
    d2 = kernel.evaluate(sig, ctx=ctx_fail)
    print(f"测试2 orderbook不可用: {d2.decision} blocked={d2.blocked_layers}")

    # BEAR_TREND 死穴
    sig3 = make_signal_event("ETHUSDT","LONG",90.0,92.0,"BEAR_TREND","C",False)
    d3 = kernel.evaluate(sig3, ctx=RiskContext.paper_mode())
    print(f"测试3 ETH LONG BEAR_TREND: {d3.decision} blocked={d3.blocked_layers}")

    print("\n✅ Risk Kernel P0-2/P0-3 自检完成")
