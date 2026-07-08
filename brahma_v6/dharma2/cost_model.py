"""
brahma_v6/dharma2/cost_model.py — 真实成本模型
设计院 × 顶级评估v6.0建议 2026-07-08

净收益 = 毛收益 - 手续费损耗 - 滑点损耗 - 资金费损耗 - 冲击成本
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional


# ══════════════════════════════════════════════════════
#  Binance Futures 成本参数（真实数据）
# ══════════════════════════════════════════════════════
BINANCE_FEES = {
    # Taker费 maker费（VIP0）
    "BTCUSDT":  {"maker": 0.0002, "taker": 0.0005},
    "ETHUSDT":  {"maker": 0.0002, "taker": 0.0005},
    "SOLUSDT":  {"maker": 0.0002, "taker": 0.0005},
    "_default": {"maker": 0.0002, "taker": 0.0005},
}

# 典型滑点（bps）
TYPICAL_SLIPPAGE_BPS = {
    "BTCUSDT":  1.5,
    "ETHUSDT":  2.5,
    "SOLUSDT":  4.0,
    "_default": 8.0,
}

# 资金费率平均（年化%）
AVG_FUNDING_ANNUAL_PCT = {
    "BTCUSDT":  15.0,
    "ETHUSDT":  18.0,
    "SOLUSDT":  25.0,
    "_default": 30.0,
}


@dataclass
class CostBreakdown:
    symbol: str
    direction: str
    holding_hours: float
    gross_pnl_pct: float        # 毛收益（%）
    fee_drag_pct: float         # 手续费（入+出，%）
    slippage_drag_pct: float    # 滑点（入+出，%）
    funding_drag_pct: float     # 资金费（持仓期间，%）
    impact_drag_pct: float      # 冲击成本（%）
    net_pnl_pct: float          # 净收益（%）
    cost_to_gross_ratio: float  # 成本/毛收益比（>0.3警告）
    is_ev_positive: bool        # 期望值为正？

    def summary(self) -> str:
        return (
            f"[Dharma2 成本分解] {self.symbol} {self.direction} {self.holding_hours:.1f}H\n"
            f"  毛收益:   {self.gross_pnl_pct:+.3f}%\n"
            f"  手续费:  -{self.fee_drag_pct:.3f}%\n"
            f"  滑点:    -{self.slippage_drag_pct:.3f}%\n"
            f"  资金费:  -{self.funding_drag_pct:.3f}%\n"
            f"  冲击:    -{self.impact_drag_pct:.3f}%\n"
            f"  ───────────────────\n"
            f"  净收益:   {self.net_pnl_pct:+.3f}%  "
            f"  成本占比: {self.cost_to_gross_ratio*100:.1f}%  "
            f"  {'✅ EV+' if self.is_ev_positive else '❌ EV-'}"
        )


def compute_cost(
    symbol: str,
    direction: str,
    gross_pnl_pct: float,
    holding_hours: float,
    leverage: int = 3,
    use_market_order: bool = False,
    position_size_usdt: float = 100.0,
) -> CostBreakdown:
    """
    计算真实净收益，所有百分比均为仓位名义值的%

    Args:
        gross_pnl_pct: 毛收益（标的价格变动 × 杠杆的%）
        holding_hours: 持仓小时数
        leverage: 杠杆倍数
        use_market_order: 是否使用市价单（更高滑点+taker费）
        position_size_usdt: 持仓名义值（用于计算冲击成本）
    """
    fees = BINANCE_FEES.get(symbol, BINANCE_FEES["_default"])
    slip_bps = TYPICAL_SLIPPAGE_BPS.get(symbol, TYPICAL_SLIPPAGE_BPS["_default"])
    fund_annual = AVG_FUNDING_ANNUAL_PCT.get(symbol, AVG_FUNDING_ANNUAL_PCT["_default"])

    # 手续费（入+出，名义值的%，乘以杠杆因为我们用杠杆计）
    fee_rate = fees["taker"] if use_market_order else (fees["maker"] + fees["taker"]) / 2
    fee_drag = fee_rate * 2 * leverage * 100  # 入+出，转%

    # 滑点（入+出，bps→%）
    slip_mult = 1.5 if use_market_order else 1.0
    slippage_drag = slip_bps * slip_mult * 2 / 100 * leverage  # 乘以杠杆

    # 资金费（每8小时，年化→持仓期间）
    funding_per_8h = fund_annual / (365 * 3)
    funding_drag = abs(funding_per_8h * (holding_hours / 8)) * leverage

    # 冲击成本（大单才有，小账户基本可忽略，估算）
    impact_drag = 0.0
    if position_size_usdt > 50_000:
        impact_drag = math.log10(position_size_usdt / 10_000) * 0.01 * leverage

    total_cost = fee_drag + slippage_drag + funding_drag + impact_drag
    net_pnl = gross_pnl_pct - total_cost
    cost_ratio = total_cost / abs(gross_pnl_pct) if gross_pnl_pct != 0 else 999.0

    return CostBreakdown(
        symbol=symbol,
        direction=direction,
        holding_hours=holding_hours,
        gross_pnl_pct=gross_pnl_pct,
        fee_drag_pct=round(fee_drag, 4),
        slippage_drag_pct=round(slippage_drag, 4),
        funding_drag_pct=round(funding_drag, 4),
        impact_drag_pct=round(impact_drag, 4),
        net_pnl_pct=round(net_pnl, 4),
        cost_to_gross_ratio=round(cost_ratio, 3),
        is_ev_positive=net_pnl > 0,
    )


# ══════════════════════════════════════════════════════
#  EV计算器（结合胜率矩阵）
# ══════════════════════════════════════════════════════
def compute_ev(
    symbol: str,
    direction: str,
    win_rate: float,
    avg_win_pct: float,     # 平均盈利（毛，%）
    avg_loss_pct: float,    # 平均亏损（毛，%，正数）
    holding_hours: float,
    leverage: int = 3,
    use_market_order: bool = False,
) -> Dict:
    """
    净EV = WR × net_avg_win - (1-WR) × net_avg_loss
    """
    win_cost = compute_cost(symbol, direction, avg_win_pct, holding_hours, leverage, use_market_order)
    loss_cost = compute_cost(symbol, direction, -avg_loss_pct, holding_hours, leverage, use_market_order)

    net_win = win_cost.net_pnl_pct
    net_loss = abs(loss_cost.net_pnl_pct)

    ev = win_rate * net_win - (1 - win_rate) * net_loss
    profit_factor = (win_rate * net_win) / ((1 - win_rate) * net_loss) if net_loss > 0 else 999.0
    breakeven_wr = net_loss / (net_win + net_loss) if (net_win + net_loss) > 0 else 0.5

    return {
        "symbol": symbol,
        "direction": direction,
        "win_rate": win_rate,
        "gross_avg_win_pct": avg_win_pct,
        "gross_avg_loss_pct": avg_loss_pct,
        "net_avg_win_pct": round(net_win, 4),
        "net_avg_loss_pct": round(net_loss, 4),
        "net_ev_pct": round(ev, 4),
        "profit_factor": round(profit_factor, 3),
        "breakeven_wr": round(breakeven_wr, 3),
        "is_ev_positive": ev > 0,
        "cost_breakdown_win": win_cost,
        "cost_breakdown_loss": loss_cost,
    }


if __name__ == "__main__":
    from typing import Dict
    print("=== Dharma2 成本模型 自检 ===\n")

    # BTC LONG 典型场景
    scenarios = [
        ("BTCUSDT", "LONG",  2.5, -2.0, 4.0,  3, False, "BTC LONG 趋势限价"),
        ("ETHUSDT", "SHORT", 3.0, -2.5, 6.0,  3, False, "ETH SHORT 趋势限价"),
        ("SOLUSDT", "LONG",  4.0, -3.0, 2.0,  3, True,  "SOL LONG 市价追单"),
    ]

    for sym, direction, win_pct, loss_pct, hold_h, lev, mkt, label in scenarios:
        cost = compute_cost(sym, direction, win_pct, hold_h, lev, mkt)
        print(f"【{label}】")
        print(cost.summary())
        print()

    # EV计算
    print("─"*50)
    print("EV期望值计算（BTC LONG WR=65%）：")
    ev = compute_ev("BTCUSDT", "LONG", 0.65, 2.5, 2.0, 4.0, 3)
    print(f"  净EV={ev['net_ev_pct']:+.4f}%/笔")
    print(f"  净利润因子={ev['profit_factor']:.2f}")
    print(f"  盈亏平衡WR={ev['breakeven_wr']*100:.1f}%")
    print(f"  {'✅ EV正期望' if ev['is_ev_positive'] else '❌ EV负期望'}")
