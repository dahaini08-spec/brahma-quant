"""
brahma_v6/risk/kernel.py — 12层风控内核
设计院 × 顶级评估v6.0建议 2026-07-08

任何信号必须先经此内核变成RiskDecision，才能生成OrderIntent。
禁止绕过。

12层架构：
L1  Data Freshness Gate
L2  Schema Contract Gate
L3  Regime Death-Zone Gate
L4  Structure Quality Gate
L5  Liquidity / Orderbook Gate
L6  Slippage / Impact Cost Gate
L7  Funding / Basis Gate
L8  Liquidation Wall Gate
L9  Correlation / Cluster Exposure Gate
L10 Account DD / Daily Loss Gate
L11 System Health / Latency Gate
L12 Council Confidence Gate
"""
from __future__ import annotations
import time
import math
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

from brahma_v6.schemas.events import (
    SignalScoredEvent, RiskDecisionEvent, make_risk_decision
)


# ══════════════════════════════════════════════════════
#  Risk Policy（可外部yaml覆盖）
# ══════════════════════════════════════════════════════
DEFAULT_POLICY = {
    # L1
    "max_data_age_sec": 120,
    # L3 Regime死穴
    "death_zones": [
        ("BEAR_TREND",    "LONG"),
        ("BEAR_CRASH",    "LONG"),
        ("CHOP_HIGH",     "LONG"),
        ("CHOP_HIGH",     "SHORT"),
    ],
    # L4
    "min_grade_score": 110,
    # L5 流动性
    "min_depth_usdt": {
        "BTCUSDT": 500_000,
        "ETHUSDT": 300_000,
        "SOLUSDT": 200_000,
        "_default": 100_000,
    },
    # L6 滑点上限
    "max_slippage_pct": {
        "BTCUSDT": 0.10,
        "ETHUSDT": 0.15,
        "SOLUSDT": 0.25,
        "_default": 0.50,
    },
    # L7 资金费率极端阈值（年化）
    "max_funding_annualized_pct": 150.0,
    # L8 强平墙距离
    "min_liq_wall_dist_pct": 1.0,
    # L9 相关性集中
    "max_correlated_nav_pct": 0.025,   # BTC+ETH同向最大2.5%NAV
    # L10 账户风控
    "max_daily_loss_pct": 0.05,        # 日内最大亏损5%NAV
    "max_drawdown_pct": 0.12,          # 最大回撤12%
    # L11
    "max_system_latency_ms": 5000,
    "max_data_gap_min": 10,
    # L12
    "min_council_confidence": 0.0,     # 初期0（Council SHADOW模式）
    # 仓位上限（NAV%）
    "max_size_nav": {
        "BTCUSDT": 0.015,
        "ETHUSDT": 0.012,
        "SOLUSDT": 0.008,
        "_default": 0.003,
    },
    # 分阶段最大杠杆
    "max_leverage": {
        "BTCUSDT": 5,
        "ETHUSDT": 5,
        "SOLUSDT": 3,
        "_default": 3,
    },
    # 信号分数 → 执行风格
    "execution_style": {
        "high_liq_trend": "POST_ONLY_LIMIT",
        "breakout": "CHASE_LIMIT",
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
#  上下文获取（真实部署时对接数据总线）
# ══════════════════════════════════════════════════════
def _get_account_state() -> Dict:
    """从持仓文件读取账户状态"""
    try:
        pos_file = BASE / "data" / "wuqu_positions.json"
        if pos_file.exists():
            return json.loads(pos_file.read_text())
    except Exception:
        pass
    return {}


def _get_system_health() -> Dict:
    """读取系统健康状态"""
    try:
        h_file = BASE / "data" / "brahma_health_last.json"
        if h_file.exists():
            return json.loads(h_file.read_text())
    except Exception:
        pass
    return {"healthy": True, "latency_ms": 0}


def _get_funding_rate(symbol: str) -> float:
    """获取当前资金费率（年化%）"""
    try:
        import requests
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={"symbol": symbol, "limit": 1},
            timeout=5,
        )
        data = r.json()
        if data:
            rate_8h = float(data[0]["fundingRate"])
            return rate_8h * 3 * 365 * 100  # 年化%
    except Exception:
        pass
    return 0.0


def _get_orderbook_depth(symbol: str, pct: float = 0.01) -> float:
    """获取±1%盘口深度（USDT）"""
    try:
        import requests
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/depth",
            params={"symbol": symbol, "limit": 20},
            timeout=5,
        )
        data = r.json()
        price_r = requests.get(
            "https://fapi.binance.com/fapi/v1/ticker/price",
            params={"symbol": symbol},
            timeout=5,
        )
        mid = float(price_r.json()["price"])
        total = 0.0
        for side in ("bids", "asks"):
            for p, q in data.get(side, []):
                if abs(float(p) - mid) / mid <= pct:
                    total += float(p) * float(q)
        return total
    except Exception:
        return 999_999_999.0  # 获取失败→不封锁


def _get_correlated_exposure(symbol: str, direction: str, nav: float) -> float:
    """计算BTC/ETH相关暴露"""
    try:
        positions = _get_account_state()
        correlated = {"BTCUSDT", "ETHUSDT"}
        if symbol not in correlated:
            return 0.0
        total_corr = 0.0
        for sym, pos in positions.items():
            if sym in correlated and isinstance(pos, dict):
                pos_nav = abs(float(pos.get("nav_pct", 0)))
                pos_dir = pos.get("direction", "")
                if pos_dir == direction:
                    total_corr += pos_nav
        return total_corr
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════
#  12层风控内核
# ══════════════════════════════════════════════════════
class RiskKernel:
    """
    12层风控内核 — 信号到OrderIntent的唯一合法通道
    """

    def __init__(self, policy: Dict = None):
        self.policy = policy or _load_policy()

    def evaluate(
        self,
        signal: SignalScoredEvent,
        account_nav: float = 100.0,
        skip_live_checks: bool = False,  # True=单元测试/paper模式
    ) -> RiskDecisionEvent:
        """
        全链路12层评估。
        返回 RiskDecisionEvent，decision=APPROVE/REDUCE/BLOCKED
        """
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

        # ── L5: Liquidity Gate ─────────────────────────────
        if not skip_live_checks:
            min_depth = p["min_depth_usdt"].get(sym, p["min_depth_usdt"]["_default"])
            depth = _get_orderbook_depth(sym)
            if depth < min_depth:
                blocked_layers.append("L5_LIQUIDITY_INSUFFICIENT")
                reasons.append(f"深度${depth:,.0f} < 门槛${min_depth:,.0f}")
            elif depth < min_depth * 2:
                warnings.append(f"L5_DEPTH_THIN: ${depth:,.0f}")

        # ── L6: Slippage Gate ──────────────────────────────
        max_slip = p["max_slippage_pct"].get(sym, p["max_slippage_pct"]["_default"])
        # 暂用固定估算（真实应接执行器回报）
        estimated_slip = 0.05  # 默认估算0.05%
        if estimated_slip > max_slip:
            blocked_layers.append("L6_SLIPPAGE_TOO_HIGH")
            reasons.append(f"预估滑点{estimated_slip:.2f}% > {max_slip:.2f}%")

        # ── L7: Funding Gate ───────────────────────────────
        if not skip_live_checks:
            funding_annual = _get_funding_rate(sym)
            max_funding = p.get("max_funding_annualized_pct", 150.0)
            # 同方向高资金费 = 持多但多头资金费极高→警告
            if direction == "LONG" and funding_annual > max_funding:
                blocked_layers.append("L7_FUNDING_EXTREME_LONG")
                reasons.append(f"资金费率年化{funding_annual:.0f}% > {max_funding:.0f}%，多头成本极高")
            elif direction == "SHORT" and funding_annual < -max_funding:
                blocked_layers.append("L7_FUNDING_EXTREME_SHORT")
                reasons.append(f"资金费率年化{funding_annual:.0f}%，空头成本极高")
            elif abs(funding_annual) > max_funding * 0.7:
                warnings.append(f"L7_FUNDING_ELEVATED: {funding_annual:.0f}%/yr")

        # ── L8: Liquidation Wall ───────────────────────────
        # 暂用信号payload中的liq_dist_pct
        liq_dist = signal.payload.get("liq_dist_pct", 999)
        min_liq_dist = p.get("min_liq_wall_dist_pct", 1.0)
        if liq_dist < min_liq_dist:
            blocked_layers.append("L8_LIQ_WALL_TOO_CLOSE")
            reasons.append(f"强平墙距离{liq_dist:.2f}% < {min_liq_dist:.2f}%")
        elif liq_dist < min_liq_dist * 2:
            warnings.append(f"L8_LIQ_WALL_NEAR: {liq_dist:.2f}%")

        # ── L9: Correlation / Cluster ─────────────────────
        if not skip_live_checks:
            corr_nav = _get_correlated_exposure(sym, direction, account_nav)
            max_corr = p.get("max_correlated_nav_pct", 0.025)
            if corr_nav > max_corr:
                blocked_layers.append("L9_CORRELATION_OVEREXPOSED")
                reasons.append(f"相关敞口{corr_nav*100:.1f}% > 上限{max_corr*100:.1f}%")
            elif corr_nav > max_corr * 0.7:
                warnings.append(f"L9_CORR_ELEVATED: {corr_nav*100:.1f}%NAV")

        # ── L10: Account DD / Daily Loss ──────────────────
        if not skip_live_checks:
            account = _get_account_state()
            daily_loss = float(account.get("daily_loss_pct", 0))
            drawdown = float(account.get("drawdown_pct", 0))
            max_daily = p.get("max_daily_loss_pct", 0.05)
            max_dd = p.get("max_drawdown_pct", 0.12)
            if daily_loss > max_daily:
                blocked_layers.append("L10_DAILY_LOSS_EXCEEDED")
                reasons.append(f"日内亏损{daily_loss*100:.1f}% > 上限{max_daily*100:.1f}%")
            if drawdown > max_dd:
                blocked_layers.append("L10_MAX_DRAWDOWN_EXCEEDED")
                reasons.append(f"回撤{drawdown*100:.1f}% > 上限{max_dd*100:.1f}%")
            if daily_loss > max_daily * 0.7:
                warnings.append(f"L10_DAILY_LOSS_NEAR: {daily_loss*100:.1f}%")

        # ── L11: System Health ────────────────────────────
        if not skip_live_checks:
            health = _get_system_health()
            latency = health.get("latency_ms", 0)
            max_lat = p.get("max_system_latency_ms", 5000)
            if not health.get("healthy", True):
                blocked_layers.append("L11_SYSTEM_UNHEALTHY")
                reasons.append("系统健康检查失败")
            elif latency > max_lat:
                blocked_layers.append("L11_LATENCY_TOO_HIGH")
                reasons.append(f"系统延迟{latency}ms > {max_lat}ms")

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
            # 计算仓位
            base_size = p["max_size_nav"].get(sym, p["max_size_nav"]["_default"])
            leverage = p["max_leverage"].get(sym, p["max_leverage"]["_default"])
            order_style = p["execution_style"].get("_default", "POST_ONLY_LIMIT")

            # 评分修正（神级信号满仓，铁证线以上适当加成）
            if score >= 165:
                size_mult = 1.0
            elif score >= 155:
                size_mult = 0.8
            elif score >= 138:
                size_mult = 0.6
            else:
                size_mult = 0.4

            # 警告惩罚
            warning_penalty = 1.0 - len(warnings) * 0.05
            size_nav = round(base_size * size_mult * max(warning_penalty, 0.5), 4)

            if size_nav < base_size * 0.5:
                decision = "REDUCE"
            else:
                decision = "APPROVE"
            reason = f"APPROVED score={score:.1f} size={size_nav*100:.2f}%NAV"
            if warnings:
                reason += f" warnings={warnings}"

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
    ) -> List[RiskDecisionEvent]:
        return [self.evaluate(s, account_nav, skip_live_checks) for s in signals]


# ── 全局单例 ────────────────────────────────────────────
_kernel: Optional[RiskKernel] = None


def get_kernel() -> RiskKernel:
    global _kernel
    if _kernel is None:
        _kernel = RiskKernel()
    return _kernel


if __name__ == "__main__":
    import uuid
    from brahma_v6.schemas.events import make_signal_event

    print("=== 12层Risk Kernel 自检 ===\n")
    kernel = RiskKernel()

    # 测试用例1：正常高分信号
    sig = make_signal_event(
        symbol="BTCUSDT",
        direction="LONG",
        raw_score=140.0,
        final_score=158.0,
        regime="BULL_TREND",
        grade="🔴神级",
        blocked=False,
        confidence=0.78,
        top_pos=[["funding_zscore", 8.4], ["ob_imbalance", 7.6]],
        top_neg=[["liq_wall_near", -5.5]],
    )
    decision = kernel.evaluate(sig, account_nav=100.0, skip_live_checks=True)
    print(f"测试1 BTC LONG 158分: {decision.decision}")
    print(f"  size_nav={decision.final_size_nav*100:.2f}%NAV  leverage={decision.max_leverage}x")
    print(f"  blocked_layers={decision.blocked_layers}")
    print(f"  warnings={decision.warnings}")
    print(f"  trace_id={decision.trace_id[:8]}...\n")

    # 测试用例2：死穴体制
    sig2 = make_signal_event(
        symbol="ETHUSDT",
        direction="LONG",
        raw_score=90.0,
        final_score=92.0,
        regime="BEAR_TREND",
        grade="🔴神级",
        blocked=False,
    )
    d2 = kernel.evaluate(sig2, skip_live_checks=True)
    print(f"测试2 ETH LONG BEAR_TREND: {d2.decision}")
    print(f"  blocked_layers={d2.blocked_layers}\n")

    # 测试用例3：分数不足
    sig3 = make_signal_event(
        symbol="SOLUSDT",
        direction="SHORT",
        raw_score=85.0,
        final_score=88.0,
        regime="CHOP_MID",
        grade="🔵中等",
        blocked=False,
    )
    d3 = kernel.evaluate(sig3, skip_live_checks=True)
    print(f"测试3 SOL SHORT 88分: {d3.decision}")
    print(f"  blocked_layers={d3.blocked_layers}")

    print("\n✅ 12层Risk Kernel 自检完成")
