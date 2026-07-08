"""
brahma_v6/risk/context.py — RiskContext 数据注入容器
设计院 全局修复 P0-2 | 2026-07-08

RiskKernel 纯函数化的关键：所有外部数据由上游注入，Kernel 内部零 I/O。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class RiskContext:
    """所有 live/realtime 数据由上游注入此容器，RiskKernel 只做纯函数评估。"""

    # 流动性
    orderbook_depth_usdt: float = 0.0         # ±1% 盘口深度 USDT；0 = 数据缺失 → BLOCK
    orderbook_available: bool = True           # False → L5 强制 BLOCK

    # 资金费率
    funding_rate_annual_pct: float = 0.0       # 年化 %
    funding_available: bool = True             # False → 跳过 L7（不封锁，仅 warning）

    # 账户
    account_nav: float = 100.0                 # 当前 NAV（USDT）
    account_drawdown_pct: float = 0.0          # 当前回撤 % (0~100)
    account_available: bool = True             # False → 使用默认 NAV，warning

    # 系统健康
    system_health_score: float = 100.0         # 0~100；<60 → BLOCK
    system_available: bool = True

    # 相关性暴露
    correlated_exposure_nav: float = 0.0       # 当前 BTC/ETH cluster 敞口占 NAV
    correlation_available: bool = True

    # 持仓方向（用于 L10 相关性去重）
    open_positions: Dict[str, str] = field(default_factory=dict)  # symbol → "LONG"/"SHORT"

    @classmethod
    def paper_mode(cls, account_nav: float = 100.0) -> "RiskContext":
        """Paper/回测模式：全部使用安全默认值，无网络请求。"""
        return cls(
            orderbook_depth_usdt=500_000.0,
            orderbook_available=True,
            funding_rate_annual_pct=10.0,
            funding_available=True,
            account_nav=account_nav,
            account_drawdown_pct=0.0,
            account_available=True,
            system_health_score=100.0,
            system_available=True,
            correlated_exposure_nav=0.0,
            correlation_available=True,
        )

    @classmethod
    def from_live(cls, symbol: str, account_nav: float = 100.0) -> "RiskContext":
        """
        从 Binance 公开接口采集实时数据，构建 RiskContext。
        失败时 available=False（Kernel 按 fail-closed 处理）。
        """
        import requests

        ctx = cls(account_nav=account_nav)

        # 1. Orderbook depth
        try:
            r_depth = requests.get(
                "https://fapi.binance.com/fapi/v1/depth",
                params={"symbol": symbol, "limit": 20}, timeout=5,
            ).json()
            r_price = requests.get(
                "https://fapi.binance.com/fapi/v1/ticker/price",
                params={"symbol": symbol}, timeout=5,
            ).json()
            mid = float(r_price["price"])
            total = 0.0
            for side in ("bids", "asks"):
                for p, q in r_depth.get(side, []):
                    if abs(float(p) - mid) / mid <= 0.01:
                        total += float(p) * float(q)
            ctx.orderbook_depth_usdt = total
            ctx.orderbook_available = True
        except Exception:
            ctx.orderbook_depth_usdt = 0.0
            ctx.orderbook_available = False   # ← fail-closed

        # 2. Funding rate
        try:
            data = requests.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": symbol, "limit": 1}, timeout=5,
            ).json()
            if data:
                ctx.funding_rate_annual_pct = float(data[0]["fundingRate"]) * 3 * 365 * 100
            ctx.funding_available = True
        except Exception:
            ctx.funding_available = False

        return ctx
