"""
brahma_v6/reality/models.py — Reality Models 合集
Lean-Inspired 设计院 Phase 3 | 2026-07-08

借鉴 Lean Reality Modeling 思想，原生适配加密合约：
  FeeModel       — maker/taker 手续费
  SlippageModel  — 市场冲击 + 价差
  FundingModel   — 资金费率拖累
  ImpactModel    — 大单市场冲击
  FillModel      — 综合成交模拟（整合以上）

所有模型返回统一 FillResult，包含完整成本分解。
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional, Dict


# ══════════════════════════════════════════════════════
#  FillResult — 统一成本分解结果
# ══════════════════════════════════════════════════════
@dataclass
class FillResult:
    """
    单笔成交的完整成本分解。
    对应 Dharma2 PnL 归因的基础数据。
    """
    symbol:       str = ""
    side:         str = ""           # BUY / SELL
    quantity:     float = 0.0
    ideal_price:  float = 0.0        # 无摩擦的理想成交价
    fill_price:   float = 0.0        # 实际成交价（含所有滑点）
    partial:      bool = False       # 是否部分成交

    # 成本分解
    fee_usdt:       float = 0.0      # 手续费（USDT）
    slippage_usdt:  float = 0.0      # 滑点损失
    spread_usdt:    float = 0.0      # 价差成本
    impact_usdt:    float = 0.0      # 大单冲击成本
    funding_usdt:   float = 0.0      # 资金费率成本（按持仓时间）
    total_cost:     float = 0.0      # = fee + slippage + spread + impact + funding

    # 执行质量
    fill_ratio:     float = 1.0      # 实际成交 / 目标量
    latency_ms:     float = 0.0      # 预估延迟
    execution_mode: str = "backtest" # backtest / paper / live

    def __post_init__(self):
        self.total_cost = self.fee_usdt + self.slippage_usdt + self.spread_usdt + \
                          self.impact_usdt + self.funding_usdt

    def summary(self) -> Dict:
        return {
            "fill_price":    round(self.fill_price, 4),
            "fee_usdt":      round(self.fee_usdt, 6),
            "slippage_usdt": round(self.slippage_usdt, 6),
            "spread_usdt":   round(self.spread_usdt, 6),
            "impact_usdt":   round(self.impact_usdt, 6),
            "funding_usdt":  round(self.funding_usdt, 6),
            "total_cost":    round(self.total_cost, 6),
            "fill_ratio":    round(self.fill_ratio, 3),
        }


# ══════════════════════════════════════════════════════
#  FeeModel — maker/taker 手续费
# ══════════════════════════════════════════════════════
# 各资产手续费参数（Binance Futures VIP0）
FEE_PARAMS: Dict[str, Dict] = {
    "BTCUSDT":  {"maker": 0.0002, "taker": 0.0004},
    "ETHUSDT":  {"maker": 0.0002, "taker": 0.0004},
    "SOLUSDT":  {"maker": 0.0002, "taker": 0.0004},
    "BNBUSDT":  {"maker": 0.0002, "taker": 0.0004},
    "_default": {"maker": 0.0002, "taker": 0.0005},
}


class FeeModel:
    def calculate(
        self,
        symbol: str,
        quantity: float,
        fill_price: float,
        order_type: str = "POST_ONLY_LIMIT",
    ) -> float:
        """计算手续费（USDT）"""
        params = FEE_PARAMS.get(symbol, FEE_PARAMS["_default"])
        rate = params["maker"] if "LIMIT" in order_type or "POST" in order_type else params["taker"]
        return fill_price * quantity * rate


# ══════════════════════════════════════════════════════
#  SlippageModel — 市场冲击 + 价差
# ══════════════════════════════════════════════════════
SLIPPAGE_PARAMS: Dict[str, Dict] = {
    "BTCUSDT":  {"spread_bps": 0.5, "impact_bps_per_100k": 0.3},
    "ETHUSDT":  {"spread_bps": 0.8, "impact_bps_per_100k": 0.5},
    "SOLUSDT":  {"spread_bps": 1.5, "impact_bps_per_100k": 1.2},
    "BNBUSDT":  {"spread_bps": 1.0, "impact_bps_per_100k": 0.8},
    "_meme":    {"spread_bps": 5.0, "impact_bps_per_100k": 5.0},
    "_default": {"spread_bps": 3.0, "impact_bps_per_100k": 2.0},
}


class SlippageModel:
    def _get_params(self, symbol: str) -> Dict:
        if symbol in SLIPPAGE_PARAMS:
            return SLIPPAGE_PARAMS[symbol]
        if any(x in symbol for x in ("PEPE", "DOGE", "SHIB", "BONK", "FLOKI")):
            return SLIPPAGE_PARAMS["_meme"]
        return SLIPPAGE_PARAMS["_default"]

    def calculate(
        self,
        symbol: str,
        side: str,
        quantity: float,
        ideal_price: float,
        orderbook_depth_usdt: float = 500_000,
        order_type: str = "POST_ONLY_LIMIT",
    ) -> tuple[float, float]:
        """
        返回 (fill_price, slippage_usdt)。
        POST_ONLY_LIMIT 滑点 ≈ 0（不穿价）。
        MARKET 滑点 = 价差 + 市场冲击。
        """
        p = self._get_params(symbol)
        notional = ideal_price * quantity

        if "POST_ONLY" in order_type:
            # Post-only: 理论上无滑点（不成交就撤单）
            slip_bps = p["spread_bps"] * 0.1
        elif "LIMIT" in order_type:
            slip_bps = p["spread_bps"] * 0.5
        else:
            # MARKET: 全价差 + 冲击
            depth_factor = notional / max(orderbook_depth_usdt, 1000)
            slip_bps = p["spread_bps"] + p["impact_bps_per_100k"] * (notional / 100_000)

        slip_pct = slip_bps / 10000
        slippage_price = ideal_price * slip_pct
        fill_price = ideal_price + slippage_price if side == "BUY" else ideal_price - slippage_price
        slippage_usdt = abs(fill_price - ideal_price) * quantity

        return fill_price, slippage_usdt


# ══════════════════════════════════════════════════════
#  FundingModel — 资金费率拖累
# ══════════════════════════════════════════════════════
FUNDING_PARAMS: Dict[str, Dict] = {
    "BTCUSDT":  {"base_8h_rate": 0.0001},   # 约0.01%/8h = 13.5%/yr
    "ETHUSDT":  {"base_8h_rate": 0.00012},
    "SOLUSDT":  {"base_8h_rate": 0.00015},
    "_default": {"base_8h_rate": 0.0002},
}


class FundingModel:
    def calculate(
        self,
        symbol: str,
        direction: str,               # LONG / SHORT
        notional_usdt: float,
        holding_hours: float,
        current_funding_8h: float = None,  # 实时资金费率，None=用默认
    ) -> float:
        """
        计算资金费率成本（USDT）。
        正费率：多头付空头。负费率：空头付多头。
        返回成本（正=付出，负=收到）。
        """
        params = FUNDING_PARAMS.get(symbol, FUNDING_PARAMS["_default"])
        rate_8h = current_funding_8h if current_funding_8h is not None else params["base_8h_rate"]
        n_periods = holding_hours / 8.0
        # 多头: 正费率付出，负费率收入
        # 空头: 正费率收入，负费率付出
        sign = 1.0 if direction == "LONG" else -1.0
        cost = notional_usdt * rate_8h * n_periods * sign
        return cost  # 正=成本，负=收益


# ══════════════════════════════════════════════════════
#  ImpactModel — 大单市场冲击
# ══════════════════════════════════════════════════════
class ImpactModel:
    def calculate(
        self,
        notional_usdt: float,
        orderbook_depth_usdt: float = 500_000,
        symbol: str = "_default",
    ) -> float:
        """
        Square-root 市场冲击模型（借鉴 Almgren-Chriss）。
        impact_bps = k × sqrt(notional / depth)
        """
        p = SLIPPAGE_PARAMS.get(symbol, SLIPPAGE_PARAMS.get("_default"))
        k = p.get("impact_bps_per_100k", 1.0) * 0.5
        if orderbook_depth_usdt <= 0:
            return notional_usdt * 0.001  # 无深度数据 → 惩罚
        ratio = notional_usdt / orderbook_depth_usdt
        impact_bps = k * math.sqrt(ratio)
        return notional_usdt * impact_bps / 10000


# ══════════════════════════════════════════════════════
#  FillModel — 综合成交模拟（整合以上）
# ══════════════════════════════════════════════════════
class FillModel:
    """
    综合成交模型：整合 Fee + Slippage + Impact。
    Backtest / Paper 模式的核心现实建模组件。
    """
    def __init__(self):
        self.fee_model      = FeeModel()
        self.slippage_model = SlippageModel()
        self.funding_model  = FundingModel()
        self.impact_model   = ImpactModel()

    def simulate(
        self,
        symbol: str,
        side: str,
        quantity: float,
        ideal_price: float,
        direction: str = "LONG",
        order_type: str = "POST_ONLY_LIMIT",
        orderbook_depth_usdt: float = 500_000,
        holding_hours: float = 4.0,
        current_funding_8h: float = None,
        execution_mode: str = "backtest",
    ) -> FillResult:
        """综合模拟单笔成交，返回完整 FillResult。"""
        notional = ideal_price * quantity

        # 1. Slippage
        fill_price, slip_usdt = self.slippage_model.calculate(
            symbol, side, quantity, ideal_price,
            orderbook_depth_usdt, order_type,
        )

        # 2. Fee
        fee_usdt = self.fee_model.calculate(symbol, quantity, fill_price, order_type)

        # 3. Impact（大单额外冲击）
        impact_usdt = self.impact_model.calculate(notional, orderbook_depth_usdt, symbol)

        # 4. Funding（持仓期间）
        funding_usdt = self.funding_model.calculate(
            symbol, direction, notional, holding_hours, current_funding_8h,
        )

        # 价差拆分
        spread_params = SLIPPAGE_PARAMS.get(symbol, SLIPPAGE_PARAMS["_default"])
        spread_usdt = ideal_price * quantity * spread_params["spread_bps"] / 10000 * 0.5

        result = FillResult(
            symbol=symbol, side=side, quantity=quantity,
            ideal_price=ideal_price, fill_price=fill_price,
            fee_usdt=fee_usdt,
            slippage_usdt=max(0, slip_usdt - spread_usdt),
            spread_usdt=spread_usdt,
            impact_usdt=impact_usdt,
            funding_usdt=abs(funding_usdt) if funding_usdt > 0 else 0,
            fill_ratio=1.0,
            execution_mode=execution_mode,
        )
        result.total_cost = result.fee_usdt + result.slippage_usdt + \
                            result.spread_usdt + result.impact_usdt + result.funding_usdt
        return result


if __name__ == "__main__":
    fill_model = FillModel()
    print("=== Reality Models 自检 ===\n")

    cases = [
        ("BTCUSDT", "BUY",  0.001, 107000.0, "LONG",  "POST_ONLY_LIMIT", 800_000, 4.0),
        ("ETHUSDT", "SELL", 0.1,   1740.0,   "SHORT", "MARKET",          300_000, 8.0),
        ("SOLUSDT", "BUY",  1.0,   180.0,    "LONG",  "LIMIT",            80_000, 2.0),
    ]

    for sym, side, qty, price, direction, otype, depth, hours in cases:
        r = fill_model.simulate(
            sym, side, qty, price, direction,
            order_type=otype,
            orderbook_depth_usdt=depth,
            holding_hours=hours,
        )
        notional = price * qty
        print(f"{sym} {side} {qty}@{price:.0f} ({otype[:6]})")
        print(f"  fill={r.fill_price:.2f} notional=${notional:.2f}")
        print(f"  fee={r.fee_usdt:.4f} slip={r.slippage_usdt:.4f} "
              f"spread={r.spread_usdt:.4f} impact={r.impact_usdt:.4f} "
              f"funding={r.funding_usdt:.4f} TOTAL={r.total_cost:.4f}")
        print(f"  cost_bps={r.total_cost/notional*10000:.2f}bps\n")

    print("✅ Reality Models 自检完成")
