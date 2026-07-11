"""
cost_model.py — 真实交易成本模型
封印: Brahma 2.0 P0-Plus 2026-07-11

说明:
  - taker_fee: 吃单手续费率（单边）
  - slippage:  滑点估算（单边）
  - funding:   资金费率 8H 均值（合约持仓成本）
  cost_per_trade = 2 × (taker_fee + slippage) + funding × hold_bars / bars_per_8h
"""

COST_MODEL: dict[str, dict[str, float]] = {
    "BTCUSDT": {
        "15m": 0.00125,
        "1h":  0.00161,
        "4h":  0.00165,
        "1d":  0.00170,
    },
    "ETHUSDT": {
        "15m": 0.00150,
        "1h":  0.00201,
        "4h":  0.00205,
        "1d":  0.00210,
    },
}

_DEFAULT_COST = 0.0025  # 未收录标的默认成本


def get_trade_cost(
    symbol: str,
    timeframe: str,
    multiplier: float = 1.0,
) -> float:
    """返回单笔交易往返成本（占名义价值比例）"""
    base = COST_MODEL.get(symbol, {}).get(timeframe, _DEFAULT_COST)
    return base * multiplier


def apply_cost(
    gross_return: float,
    symbol: str,
    timeframe: str,
    multiplier: float = 1.0,
) -> float:
    """从毛收益中扣除交易成本，返回净收益"""
    return gross_return - get_trade_cost(symbol, timeframe, multiplier)
