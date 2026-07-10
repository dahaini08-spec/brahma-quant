"""
brahma_v6/portfolio/position_sizer.py
仓位定量器 v1.0 — 封装 brahma_brain/position_sizer.py 逻辑

设计院 2026-07-10 自主补全（v6.5断点修复）

职责：
  - 接收信号(score/direction/regime/symbol)和账户NAV
  - 返回建议仓位百分比和USDT金额
  - 集成 EVBucketRegistry 动态调整（可选）
  - 支持7月减半仓临时策略
"""

import sys, os
from pathlib import Path
from typing import Optional

# 确保可以import brahma_brain
_BASE = Path(__file__).parent.parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

# ─── 常量 ────────────────────────────────────────────────────────────────────

# 7月减半仓临时策略（2026-07-01~07-15，苏摩111批准）
JULY_HALF_POSITION = True
JULY_HALF_SCORE_RANGE = (160, 169)   # score 160~169 → 1%NAV
JULY_HALF_NAV_PCT = 1.0

# 标准仓位表
STANDARD_POSITIONS = {
    # score范围: (min_score, max_score, nav_pct)
    "elite":    (170, 999, 5.0),   # score≥170 → 标准仓位
    "standard": (155, 169, 5.0),   # score155-169 → 5%（7月临时1%）
    "btc_eth_dynamic": 10.0,       # BTC/ETH最大10%NAV
}

BTC_ETH_SYMS = {'BTCUSDT', 'ETHUSDT'}


def get_position_pct(symbol: str, score: float, direction: str,
                     nav: float = 0.0,
                     ev_registry=None) -> dict:
    """
    计算建议仓位

    Args:
        symbol:    标的（如 'BTCUSDT'）
        score:     梵天评分
        direction: 'LONG' | 'SHORT'
        nav:       账户净值（USDT），传0则只返回百分比
        ev_registry: EVBucketRegistry实例（可选，用于动态调整）

    Returns:
        dict: {
            'pct': float,     # 建议仓位%
            'usdt': float,    # 对应USDT金额（nav>0时有效）
            'reason': str,    # 决策原因
            'blocked': bool,  # 是否被风控封禁
        }
    """
    if score < 155:
        return {'pct': 0.0, 'usdt': 0.0, 'reason': f'score={score:.0f}<155，低于入场门槛', 'blocked': True}

    # 7月减半仓临时策略
    import datetime
    today = datetime.datetime.utcnow()
    is_july_half = (
        JULY_HALF_POSITION
        and today.month == 7
        and today.day <= 15
        and JULY_HALF_SCORE_RANGE[0] <= score <= JULY_HALF_SCORE_RANGE[1]
    )

    if is_july_half:
        base_pct = JULY_HALF_NAV_PCT
        reason = f'7月减半仓策略: score={score:.0f}∈[160,169] → {base_pct}%NAV'
    elif symbol.upper() in BTC_ETH_SYMS:
        # BTC/ETH 动态仓位5-10%
        if score >= 170:
            base_pct = 10.0
            reason = f'BTC/ETH精英信号: score={score:.0f}≥170 → 10%NAV'
        else:
            base_pct = 5.0
            reason = f'BTC/ETH标准信号: score={score:.0f} → 5%NAV'
    else:
        base_pct = 5.0
        reason = f'小币标准信号: score={score:.0f} → 5%NAV'

    # EV桶动态调整（如果传入ev_registry）
    ev_mult = 1.0
    if ev_registry is not None:
        try:
            action = ev_registry.get_action_for(symbol, direction)
            if action == 'BLOCK':
                return {'pct': 0.0, 'usdt': 0.0, 'reason': f'EVBucket封禁: {symbol} {direction}', 'blocked': True}
            elif action == 'REDUCE':
                ev_mult = 0.5
                reason += ' [EVBucket→减半]'
            elif action == 'APPROVE':
                ev_mult = 1.0
        except Exception:
            pass  # ev_registry不可用时跳过

    final_pct = base_pct * ev_mult
    final_usdt = nav * final_pct / 100 if nav > 0 else 0.0

    return {
        'pct': final_pct,
        'usdt': final_usdt,
        'reason': reason,
        'blocked': False,
    }


class PositionSizer:
    """
    面向对象封装版，供signal_consumer.py使用

    Usage:
        sizer = PositionSizer(nav=127.37)
        result = sizer.calc(signal)
        size_usdt = result['usdt']
    """

    def __init__(self, nav: float = 0.0, ev_registry=None):
        self.nav = nav
        self.ev_registry = ev_registry

    def set_nav(self, nav: float):
        self.nav = nav

    def calc(self, signal: dict) -> dict:
        """
        从信号字典计算仓位

        Args:
            signal: 包含 symbol/score/direction/regime 字段的信号字典

        Returns:
            同 get_position_pct() 返回格式
        """
        symbol = signal.get('symbol', '')
        score = float(signal.get('score', 0))
        direction = signal.get('direction', 'LONG')

        return get_position_pct(
            symbol=symbol,
            score=score,
            direction=direction,
            nav=self.nav,
            ev_registry=self.ev_registry,
        )


# ─── 自测 ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    sizer = PositionSizer(nav=127.37)

    test_cases = [
        {'symbol': 'BTCUSDT', 'score': 162, 'direction': 'SHORT'},
        {'symbol': 'ETHUSDT', 'score': 175, 'direction': 'LONG'},
        {'symbol': 'HYPEUSDT', 'score': 158, 'direction': 'SHORT'},
        {'symbol': 'BTCUSDT', 'score': 140, 'direction': 'LONG'},   # 低分→封禁
    ]

    for sig in test_cases:
        r = sizer.calc(sig)
        print(f"{sig['symbol']:12} {sig['direction']:5} score={sig['score']:3}"
              f" → pct={r['pct']:.1f}% usdt=${r['usdt']:.2f} blocked={r['blocked']}"
              f" [{r['reason'][:50]}]")
