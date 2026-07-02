"""
梵天 RiskGate v2.0
借鉴 vnpy RiskManager 设计，硬性风控门控
防止 PIXEL 级别重复建仓事故

苏摩111批准落地 · 2026-06-28
"""

import json
import time
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("BrahmaRiskGate")

# ═══════════════════════════════════════════════════════
#  风控规则（宪法级，修改需苏摩批准）
# ═══════════════════════════════════════════════════════
RISK_RULES = {
    # 每日最大开仓次数（不含平仓）
    "max_daily_trades": 8,

    # 日内最大亏损 % NAV → 触发熔断
    "max_daily_loss_pct": 0.08,

    # 连续亏损笔数 → 暂停4小时
    "max_consec_losses": 3,
    "consec_loss_pause_h": 4,

    # 滑点超过此比例 → 取消订单
    "max_single_slippage": 0.02,

    # 同标的重入窗口（分钟）— PIXEL教训
    "duplicate_entry_window_min": 240,

    # 单标的最大保证金占NAV比例
    "max_pos_pct_nav": 0.10,

    # 最大同时持仓数
    "max_open_positions": 5,
}

# ═══════════════════════════════════════════════════════
#  风控状态文件
# ═══════════════════════════════════════════════════════
RISK_STATE_PATH = Path("data/brahma_risk_state.json")


def _load_state() -> dict:
    if RISK_STATE_PATH.exists():
        try:
            return json.loads(RISK_STATE_PATH.read_text())
        except:
            pass
    return {
        "daily_trades": 0,
        "daily_loss_pct": 0.0,
        "consec_losses": 0,
        "paused_until": 0,
        "circuit_breaker": False,
        "last_reset_day": "",
        "recent_entries": {},   # symbol → last_entry_ts
        "daily_date": "",
    }


def _save_state(state: dict):
    RISK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    RISK_STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _reset_daily_if_needed(state: dict) -> dict:
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if state.get("daily_date") != today:
        state["daily_trades"]   = 0
        state["daily_loss_pct"] = 0.0
        state["daily_date"]     = today
        logger.info(f"RiskGate: 日计数器已重置 ({today})")
    return state


# ═══════════════════════════════════════════════════════
#  核心门控函数
# ═══════════════════════════════════════════════════════

class RiskGateResult:
    def __init__(self, allowed: bool, reason: str = ""):
        self.allowed = allowed
        self.reason  = reason

    def __bool__(self):
        return self.allowed

    def __repr__(self):
        return f"RiskGate({'✅PASS' if self.allowed else '❌BLOCK'}: {self.reason})"


def check_entry(symbol: str, nav: float, margin_required: float,
                open_positions: int, signal_id: str = "") -> RiskGateResult:
    """
    开仓前门控检查（所有检查必须全部通过）
    symbol          : 交易标的
    nav             : 当前净值 USDT
    margin_required : 本次开仓所需保证金 USDT
    open_positions  : 当前已有持仓数
    signal_id       : 信号ID（用于日志）
    """
    state = _load_state()
    state = _reset_daily_if_needed(state)

    # ─── 检查1：熔断器是否激活 ───────────────────────
    if state.get("circuit_breaker"):
        return RiskGateResult(False, "熔断器已激活，禁止开仓")

    # ─── 检查2：暂停窗口（连亏后冷却期）──────────────
    paused_until = state.get("paused_until", 0)
    if time.time() < paused_until:
        remaining = int((paused_until - time.time()) / 60)
        return RiskGateResult(False, f"连亏暂停中，剩余{remaining}分钟")

    # ─── 检查3：每日最大交易次数 ──────────────────────
    if state["daily_trades"] >= RISK_RULES["max_daily_trades"]:
        return RiskGateResult(False,
            f"已达今日最大开仓次数({RISK_RULES['max_daily_trades']}笔)")

    # ─── 检查4：最大持仓数 ────────────────────────────
    if open_positions >= RISK_RULES["max_open_positions"]:
        return RiskGateResult(False,
            f"持仓数已满({open_positions}/{RISK_RULES['max_open_positions']})")

    # ─── 检查5：单标的重复建仓窗口（PIXEL教训）────────
    window_s = RISK_RULES["duplicate_entry_window_min"] * 60
    last_entry_ts = state.get("recent_entries", {}).get(symbol, 0)
    elapsed = time.time() - last_entry_ts
    if elapsed < window_s:
        remaining_min = int((window_s - elapsed) / 60)
        return RiskGateResult(False,
            f"{symbol} 重复建仓保护，剩余{remaining_min}分钟 (PIXEL教训)")

    # ─── 检查6：单标的仓位占NAV比例 ──────────────────
    if nav > 0:
        pos_pct = margin_required / nav
        if pos_pct > RISK_RULES["max_pos_pct_nav"]:
            return RiskGateResult(False,
                f"{symbol} 保证金占NAV {pos_pct:.1%} > 上限 {RISK_RULES['max_pos_pct_nav']:.0%}")

    # ─── 全部通过 ─────────────────────────────────────
    # 写入本次开仓记录
    state["daily_trades"] += 1
    if "recent_entries" not in state:
        state["recent_entries"] = {}
    state["recent_entries"][symbol] = time.time()
    _save_state(state)

    logger.info(f"RiskGate ✅ {symbol} 通过 | 今日第{state['daily_trades']}笔 | signal={signal_id}")
    return RiskGateResult(True, f"通过 (今日第{state['daily_trades']}笔)")


def record_trade_result(symbol: str, outcome: str, pnl_pct: float, nav: float):
    """
    每笔交易结算后调用，更新风控状态
    outcome: 'WIN'/'LOSS'/'TIMEOUT'
    pnl_pct: 盈亏百分比（负数=亏损）
    """
    try:
        state = _load_state()
        state = _reset_daily_if_needed(state)

        if outcome in ("LOSS", "SL"):
            state["consec_losses"] = state.get("consec_losses", 0) + 1
            state["daily_loss_pct"] = state.get("daily_loss_pct", 0) + abs(pnl_pct)

            # 连亏熔断
            if state["consec_losses"] >= RISK_RULES["max_consec_losses"]:
                pause_s = RISK_RULES["consec_loss_pause_h"] * 3600
                state["paused_until"] = time.time() + pause_s
                logger.warning(
                    f"🚨 RiskGate 连亏{state['consec_losses']}笔 → "
                    f"暂停{RISK_RULES['consec_loss_pause_h']}小时"
                )

            # 日亏损熔断
            if state["daily_loss_pct"] >= RISK_RULES["max_daily_loss_pct"]:
                state["circuit_breaker"] = True
                logger.critical(
                    f"🚨 RiskGate 熔断器激活！日亏损 "
                    f"{state['daily_loss_pct']:.1%} ≥ {RISK_RULES['max_daily_loss_pct']:.0%}"
                )

        elif outcome in ("WIN", "TP1", "TP2"):
            state["consec_losses"] = 0  # 盈利重置连亏计数

        _save_state(state)
        logger.info(f"RiskGate 记录结果: {symbol} {outcome} pnl={pnl_pct:+.2%} "
                    f"连亏={state['consec_losses']} 日亏={state['daily_loss_pct']:.2%}")
    except Exception as _e:
        import logging as _log
        _log.getLogger('brahma.execution').error(
            f'[EXEC_GUARD] record_trade_result 执行异常: {_e}', exc_info=True)
        return {'error': str(_e), 'func': 'record_trade_result', 'status': 'FAILED'}


def reset_circuit_breaker(manual: bool = True):
    """手动重置熔断器（需人工确认）"""
    if not manual:
        raise ValueError("熔断器重置必须手动确认")
    state = _load_state()
    state["circuit_breaker"] = False
    state["paused_until"] = 0
    state["consec_losses"] = 0
    _save_state(state)
    logger.warning("⚠️  RiskGate 熔断器已手动重置")


def get_status() -> dict:
    """获取当前风控状态"""
    state = _load_state()
    state = _reset_daily_if_needed(state)
    now = time.time()
    paused = now < state.get("paused_until", 0)
    return {
        "circuit_breaker":  state.get("circuit_breaker", False),
        "paused":           paused,
        "paused_remaining_min": max(0, int((state.get("paused_until",0)-now)/60)) if paused else 0,
        "daily_trades":     state.get("daily_trades", 0),
        "daily_loss_pct":   state.get("daily_loss_pct", 0.0),
        "consec_losses":    state.get("consec_losses", 0),
        "rules":            RISK_RULES,
    }


def check_slippage(expected_price: float, actual_price: float,
                   side: str) -> RiskGateResult:
    """
    成交后滑点检查（超标则报警，不自动撤单，因为已成交）
    side: 'LONG'/'SHORT'
    """
    if expected_price <= 0:
        return RiskGateResult(True, "无预期价格，跳过滑点检查")
    slippage = abs(actual_price - expected_price) / expected_price
    if slippage > RISK_RULES["max_single_slippage"]:
        logger.warning(
            f"⚠️  滑点告警: {slippage:.2%} > {RISK_RULES['max_single_slippage']:.0%} "
            f"预期={expected_price} 实际={actual_price}"
        )
        return RiskGateResult(False, f"滑点超标 {slippage:.2%}")
    return RiskGateResult(True, f"滑点正常 {slippage:.3%}")
