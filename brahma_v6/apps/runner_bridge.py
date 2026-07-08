"""
brahma_v6/apps/runner_bridge.py — brahma_v6全链路接入适配器
设计院 × 顶级评估v6.0 | 2026-07-08

零侵入原则：
  - 不修改 brahma_analysis_runner.py
  - 在 run_analysis() 输出上后置注入 v6.0 组件
  - 现有所有 cron / 推送 / 执行链路 完全不受影响

接入的 v6.0 组件：
  1. SignalScoredEvent   — 统一事件Schema + trace_id
  2. 12层 Risk Kernel    — 风控决策
  3. Regime v2.0        — entropy + transition_risk
  4. Dharma2 成本模型   — 净EV计算
  5. FileEventBus       — 事件发布
  6. Paper Portfolio    — paper trade自动入账
"""
from __future__ import annotations
import sys
import os
import uuid
import time
from pathlib import Path
from typing import Dict, Optional, List

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

# ── 加载 v6.0 组件（懒加载，失败不崩现有系统）──────────────────
try:
    from brahma_v6.schemas.events import make_signal_event, SignalScoredEvent
    from brahma_v6.risk.kernel import RiskKernel
    from brahma_v6.regime.regime_v2 import RegimeV2Adapter
    from brahma_v6.dharma2.cost_model import compute_ev
    from brahma_v6.bus.file_bus import get_bus, SignalBusAdapter
    from brahma_v6.paper.paper_forward import get_portfolio, PaperExecutor
    _V6_OK = True
except ImportError as e:
    _V6_OK = False
    print(f"[RunnerBridge] v6.0组件加载失败（非致命）: {e}")

_kernel: Optional[RiskKernel] = None
_regime_adapter: Optional[RegimeV2Adapter] = None
_paper_executor: Optional[PaperExecutor] = None
_bus_adapter: Optional[SignalBusAdapter] = None


def _get_kernel() -> Optional[RiskKernel]:
    global _kernel
    if _V6_OK and _kernel is None:
        _kernel = RiskKernel()
    return _kernel


def _get_regime_adapter() -> Optional[RegimeV2Adapter]:
    global _regime_adapter
    if _V6_OK and _regime_adapter is None:
        _regime_adapter = RegimeV2Adapter()
    return _regime_adapter


def _get_bus_adapter() -> Optional[SignalBusAdapter]:
    global _bus_adapter
    if _V6_OK and _bus_adapter is None:
        _bus_adapter = SignalBusAdapter(get_bus())
    return _bus_adapter


def _get_paper_executor() -> Optional[PaperExecutor]:
    global _paper_executor
    if _V6_OK and _paper_executor is None:
        _paper_executor = PaperExecutor(get_portfolio())
    return _paper_executor


# ══════════════════════════════════════════════════════
#  核心接入函数
# ══════════════════════════════════════════════════════
def enrich_with_v6(
    result: Dict,
    account_nav: float = 100.0,
    paper_mode: bool = False,
    skip_live_checks: bool = True,
) -> Dict:
    """
    在 run_analysis() 输出上后置注入 brahma_v6 组件结果。
    原始 result 所有字段保持不变，新增 _v6 子对象。

    Args:
        result: brahma_analysis_runner.run_analysis() 返回值
        account_nav: 当前账户NAV（用于风控仓位计算）
        paper_mode: True=自动执行 paper trade
        skip_live_checks: True=跳过L5/L7/L9/L10实时行情检查（省API调用）
    """
    if not _V6_OK:
        result["_v6"] = {"available": False, "reason": "v6组件未加载"}
        return result

    v6 = {"available": True, "ts": time.time()}

    try:
        # ── 提取关键字段 ────────────────────────────────────────
        symbol = result.get("symbol", "")
        direction = result.get("signal_dir") or result.get("direction") or "LONG"
        score = float(result.get("score") or result.get("score_final") or 0)
        regime = result.get("regime") or result.get("confirmed") or "CHOP_MID"
        blocked = bool(result.get("blocked", True))
        valid = bool(result.get("valid_signal") or result.get("valid") or False)
        price = float(result.get("price") or 0)
        stop_loss = float(result.get("stop_loss") or 0)
        tp1 = float(result.get("tp1") or result.get("take_profit_1") or 0)
        rsi_1h = float(result.get("rsi_1h") or 50)
        confidence = float(result.get("regime_confidence") or 0.5)
        bull_prob = float(result.get("bull_prob") or 0)
        bear_prob = float(result.get("bear_prob") or 0)
        chop_prob = float(result.get("chop_prob") or 0)

        # ── P0: 统一事件Schema ────────────────────────────────
        trace_id = result.get("trace_id") or str(uuid.uuid4())
        sig_event = make_signal_event(
            symbol=symbol,
            direction=direction,
            raw_score=max(score - 15, 0),
            final_score=score,
            regime=regime,
            grade=str(result.get("grade") or ""),
            blocked=blocked,
            block_reason=str(result.get("block_reason") or ""),
            confidence=confidence,
            source="brahma_analysis_runner",
        )
        sig_event.trace_id = trace_id
        v6["trace_id"] = trace_id
        v6["event_id"] = sig_event.event_id

        # ── P1: Regime v2.0 ──────────────────────────────────
        ra = _get_regime_adapter()
        if ra:
            result = ra.enrich(result)
            rv2 = result.get("regime_v2", {})
            v6["regime_v2"] = {
                "entropy": rv2.get("entropy"),
                "transition_risk": rv2.get("transition_risk"),
                "leverage_multiplier": rv2.get("leverage_multiplier"),
                "liquidity_regime": rv2.get("liquidity_regime"),
                "funding_regime": rv2.get("funding_regime"),
                "volatility_regime": rv2.get("volatility_regime"),
            }

        # ── P2: 12层 Risk Kernel ──────────────────────────────
        kernel = _get_kernel()
        risk_decision = None
        if kernel and not blocked and score > 0:
            risk_decision = kernel.evaluate(
                sig_event,
                account_nav=account_nav,
                skip_live_checks=skip_live_checks,
            )
            v6["risk_kernel"] = {
                "decision": risk_decision.decision,
                "final_size_nav": risk_decision.final_size_nav,
                "max_leverage": risk_decision.max_leverage,
                "order_style": risk_decision.order_style,
                "blocked_layers": risk_decision.blocked_layers,
                "warnings": risk_decision.warnings,
                "reason": risk_decision.reason,
            }

        # ── P3: Dharma2 净EV ─────────────────────────────────
        if price > 0 and stop_loss > 0:
            try:
                sl_pct = abs(price - stop_loss) / price * 100
                tp_pct = abs(tp1 - price) / price * 100 if tp1 > 0 else sl_pct * 1.5
                gross_win_pct = tp_pct * 3  # 乘以杠杆估算
                gross_loss_pct = sl_pct * 3
                ev = compute_ev(
                    symbol=symbol,
                    direction=direction,
                    win_rate=0.62,  # 铁证WR
                    avg_win_pct=gross_win_pct,
                    avg_loss_pct=gross_loss_pct,
                    holding_hours=8.0,
                    leverage=3,
                )
                v6["dharma2_ev"] = {
                    "net_ev_pct": ev["net_ev_pct"],
                    "profit_factor": ev["profit_factor"],
                    "breakeven_wr": ev["breakeven_wr"],
                    "is_ev_positive": ev["is_ev_positive"],
                    "net_avg_win_pct": ev["net_avg_win_pct"],
                    "net_avg_loss_pct": ev["net_avg_loss_pct"],
                }
            except Exception:
                pass

        # ── P4: 事件总线发布 ─────────────────────────────────
        try:
            ba = _get_bus_adapter()
            if ba:
                ba.emit_signal(result)
                v6["bus_published"] = True
        except Exception:
            v6["bus_published"] = False

        # ── P5: Paper trade 自动入账 ─────────────────────────
        if paper_mode and valid and not blocked and price > 0 and stop_loss > 0:
            try:
                pe = _get_paper_executor()
                rk = v6.get("risk_kernel", {})
                if pe and rk.get("decision") in ("APPROVE", "REDUCE"):
                    size_nav = rk.get("final_size_nav", 0.005) * 100
                    trade = pe.execute_signal(
                        symbol=symbol,
                        direction=direction,
                        signal_score=score,
                        regime=regime,
                        entry_price=price,
                        stop_loss=stop_loss,
                        take_profit=tp1 if tp1 > 0 else None,
                        size_nav_pct=size_nav,
                        leverage=rk.get("max_leverage", 3),
                        trace_id=trace_id,
                    )
                    v6["paper_trade"] = {
                        "trade_id": trade.trade_id,
                        "entry_price": trade.entry_price,
                        "quantity": trade.quantity,
                        "size_nav_pct": trade.size_nav_pct,
                        "status": "OPEN",
                    }
            except Exception as e:
                v6["paper_trade_error"] = str(e)[:100]

    except Exception as e:
        v6["error"] = str(e)[:200]

    result["_v6"] = v6
    return result


# ══════════════════════════════════════════════════════
#  批量接入
# ══════════════════════════════════════════════════════
def enrich_batch(results: List[Dict], account_nav: float = 100.0,
                 paper_mode: bool = False) -> List[Dict]:
    return [enrich_with_v6(r, account_nav, paper_mode) for r in results]


# ══════════════════════════════════════════════════════
#  v6 增强版 run_analysis（直接可用）
# ══════════════════════════════════════════════════════
def run_analysis_v6(
    symbol: str,
    account_nav: float = 100.0,
    paper_mode: bool = False,
    skip_live_checks: bool = True,
) -> Dict:
    """
    run_analysis() + brahma_v6 全链路增强。
    直接替代 brahma_analysis_runner.run_analysis() 使用。
    """
    try:
        from brahma_brain.brahma_analysis_runner import run_analysis
        result = run_analysis(symbol)
    except Exception as e:
        result = {"symbol": symbol, "error": str(e), "score": 0, "blocked": True}

    return enrich_with_v6(result, account_nav, paper_mode, skip_live_checks)


if __name__ == "__main__":
    print("=== brahma_v6 全链路接入 自检 ===\n")
    print(f"v6组件: {'✅ 已加载' if _V6_OK else '❌ 加载失败'}")

    if _V6_OK:
        # 构造模拟结果（不触发真实API）
        mock_result = {
            "symbol": "BTCUSDT",
            "signal_dir": "LONG",
            "direction": "LONG",
            "score": 162.0,
            "regime": "BULL_TREND",
            "grade": "🔴神级",
            "blocked": False,
            "valid_signal": True,
            "action": "ENTER_FULL",
            "price": 62000.0,
            "stop_loss": 60500.0,
            "tp1": 64500.0,
            "rsi_1h": 65.0,
            "confidence": 0.80,
            "bull_prob": 0.65, "bear_prob": 0.20, "chop_prob": 0.15,
            "trace_id": str(uuid.uuid4()),
        }
        enriched = enrich_with_v6(mock_result, account_nav=100.0, skip_live_checks=True)
        v6 = enriched.get("_v6", {})

        print(f"\n trace_id: {v6.get('trace_id','')[:12]}...")
        if "regime_v2" in v6:
            rv2 = v6["regime_v2"]
            print(f" Regime v2.0: entropy={rv2.get('entropy')}  transition_risk={rv2.get('transition_risk')}  lev_mult={rv2.get('leverage_multiplier')}")
        if "risk_kernel" in v6:
            rk = v6["risk_kernel"]
            print(f" Risk Kernel: {rk.get('decision')}  size={rk.get('final_size_nav',0)*100:.2f}%NAV  style={rk.get('order_style')}")
        if "dharma2_ev" in v6:
            ev = v6["dharma2_ev"]
            print(f" Dharma2 EV: {ev.get('net_ev_pct'):+.4f}%/笔  PF={ev.get('profit_factor'):.2f}  {'✅ EV+' if ev.get('is_ev_positive') else '❌ EV-'}")
        print(f" Bus: {'✅ 已发布' if v6.get('bus_published') else '⚠️ 未发布'}")
        print("\n✅ 全链路接入自检完成")
