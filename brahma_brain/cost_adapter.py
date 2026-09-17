"""
成本模型接入器 — 把 brahma_os/costs.py 接入主分析链路
[9.17 苏摩111] 信号生成时计算净EV，扣除交易摩擦

接入位置: brahma_core.py analyze() 信号生成后、推送前
调用方式: from brahma_brain.cost_adapter import calc_net_ev
"""
from __future__ import annotations
import sys
import os

# 默认成本参数（与 brahma_os/config.py 一致）
_TAKER_FEE_BPS = 4.0      # 0.04% taker
_MAKER_FEE_BPS = 2.0      # 0.02% maker
_SLIPPAGE_BPS = 3.0        # 0.03% BTC, altcoin需放大
_FUNDING_BPS_8H = 1.0     # 0.01% per 8h
_ALT_SLIPPAGE_MULT = 3.0  # 非BTC/ETH滑点放大3倍

# ATR_pct → 滑点调整（高波动时滑点更大）
def _get_slippage_bps(symbol: str, atr_pct: float = 0.0) -> float:
    """根据标的和波动率调整滑点"""
    sym = symbol.upper().replace('USDT', '')
    base = _SLIPPAGE_BPS
    if sym not in ('BTC', 'ETH'):
        base *= _ALT_SLIPPAGE_MULT
    if atr_pct > 0.02:  # ATR>2%时滑点翻倍
        base *= 1.5
    return base


def calc_round_trip_cost(notional: float, symbol: str = 'BTCUSDT',
                        atr_pct: float = 0.0, hours_held: float = 12.0,
                        taker: bool = True) -> dict:
    """
    计算单笔交易往返成本
    返回: {fee_bps, slip_bps, funding_bps, total_bps, total_cost, net_ev_adjust}
    """
    fee_bps = (_TAKER_FEE_BPS if taker else _MAKER_FEE_BPS) * 2  # 开+平
    slip_bps = _get_slippage_bps(symbol, atr_pct) * 2  # 开+平
    funding_bps = _FUNDING_BPS_8H * (hours_held / 8.0)
    total_bps = fee_bps + slip_bps + funding_bps
    total_cost = abs(notional) * total_bps / 10_000.0
    
    return {
        'fee_bps': fee_bps,
        'slip_bps': slip_bps,
        'funding_bps': funding_bps,
        'total_bps': total_bps,
        'total_cost': total_cost,
        'total_pct': total_bps / 100.0,  # 百分比
    }


def calc_net_ev(gross_ev: float, notional: float, symbol: str = 'BTCUSDT',
                atr_pct: float = 0.0, hours_held: float = 12.0) -> dict:
    """
    从毛EV计算净EV
    gross_ev: 未扣成本的期望收益百分比（如+0.266%）
    返回: {gross_ev, cost_pct, net_ev, cost_ratio}
    """
    cost = calc_round_trip_cost(notional, symbol, atr_pct, hours_held)
    cost_pct = cost['total_pct']
    net_ev = gross_ev - cost_pct
    cost_ratio = cost_pct / abs(gross_ev) if gross_ev != 0 else float('inf')
    
    return {
        'gross_ev': gross_ev,
        'cost_pct': cost_pct,
        'net_ev': net_ev,
        'cost_ratio': cost_ratio,
        'cost_detail': cost,
    }


def is_ev_positive_after_cost(gross_ev: float, notional: float,
                               symbol: str = 'BTCUSDT', atr_pct: float = 0.0,
                               hours_held: float = 12.0) -> bool:
    """扣成本后EV是否为正"""
    result = calc_net_ev(gross_ev, notional, symbol, atr_pct, hours_held)
    return result['net_ev'] > 0


if __name__ == '__main__':
    # 测试
    print("=== 成本模型测试 ===")
    for sym in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']:
        r = calc_round_trip_cost(10000, sym, atr_pct=0.01, hours_held=12)
        print(f"{sym}: fee={r['fee_bps']:.1f}bps slip={r['slip_bps']:.1f}bps "
              f"funding={r['funding_bps']:.1f}bps total={r['total_bps']:.1f}bps "
              f"= {r['total_pct']:.3f}%")
    print()
    # 净EV测试
    for gross in [0.266, 0.1, 0.5]:
        r = calc_net_ev(gross, 10000, 'BTCUSDT', atr_pct=0.01, hours_held=12)
        print(f"gross_ev={gross:.3f}% → net_ev={r['net_ev']:.3f}% "
              f"(cost={r['cost_pct']:.3f}%, ratio={r['cost_ratio']:.1f}%)")
