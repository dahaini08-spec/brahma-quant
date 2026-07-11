"""
confluence_tf_weights.py — 多周期评分权重路由配置
封印: 设计院六方联合 P2-A 2026-07-11

核心原则:
  不同合约周期对信号的贡献权重不同
  - 流动性越低 → 短周期权重越高（高周期K线噪音大）
  - 趋势型信号 → 4H权重更高
  - 轧空型信号(pump) → 15M权重更高

调用方式:
  from confluence_tf_weights import get_tf_weights, TF_WEIGHT_REGISTRY
  weights = get_tf_weights(symbol='REUSDT', primary_tf='1h', signal_source='pump_auto')
"""

from __future__ import annotations

# ── 流动性层级定义 ──────────────────────────────────────────
LIQUIDITY_TIER: dict[str, int] = {
    # L1 主流（ATR大，1H/4H均有效）
    'BTCUSDT': 1, 'ETHUSDT': 1,
    # L2 次主流
    'SOLUSDT': 2, 'BNBUSDT': 2, 'XRPUSDT': 2, 'ADAUSDT': 2,
    'DOGEUSDT': 2, 'TRXUSDT': 2, 'TONUSDT': 2,
    # L3 中等流动性
    'LINKUSDT': 3, 'UNIUSDT': 3, 'AAVEUSDT': 3, 'DOTUSDT': 3,
    'AVAXUSDT': 3, 'LTCUSDT': 3, 'ATOMUSDT': 3, 'NEARUSDT': 3,
}

def _get_tier(symbol: str) -> int:
    """根据合约名估算流动性层级 L1~L5"""
    if symbol in LIQUIDITY_TIER:
        return LIQUIDITY_TIER[symbol]
    sym = symbol.upper().replace('USDT', '').replace('PERP', '')
    # 长度层级默认判断（未在字典中的标的）
    if len(sym) <= 3:   return 3   # 3字符L3（如SOL在字典里，未在的也是L3级别）
    if len(sym) <= 4:   return 4   # 4字符L4
    if len(sym) <= 5:   return 4   # 5字符L4
    return 5                        # 超长名L5超小庁


# ── 周期权重矩阵 [15M, 1H, 4H, 1D] ────────────────────────
# 归一化为加权系数（总和不需要=1，用于对各维度分数加权）
TF_WEIGHT_REGISTRY: dict[str, dict[str, list[float]]] = {
    # signal_source → tier → [w_15m, w_1h, w_4h, w_1d]
    'default': {
        1: [0.10, 0.40, 0.40, 0.10],  # L1: 1H/4H并重
        2: [0.15, 0.40, 0.35, 0.10],  # L2: 略偏1H
        3: [0.20, 0.38, 0.30, 0.12],  # L3: 1H为主
        4: [0.30, 0.38, 0.22, 0.10],  # L4: 15M+1H为主，4H降权
        5: [0.40, 0.38, 0.15, 0.07],  # L5: 15M主导，4H噪音忽略
    },
    # 轧空型信号（pump_hunter / oi_hunter）短周期触发
    'pump_auto': {
        1: [0.25, 0.45, 0.25, 0.05],
        2: [0.30, 0.42, 0.22, 0.06],
        3: [0.35, 0.40, 0.18, 0.07],
        4: [0.45, 0.38, 0.12, 0.05],
        5: [0.55, 0.35, 0.07, 0.03],
    },
    # OB趋势型信号（brahma主脑 1H/4H OB入场）
    'ob_trend': {
        1: [0.05, 0.40, 0.45, 0.10],  # L1: 4H结构为主
        2: [0.10, 0.38, 0.42, 0.10],
        3: [0.15, 0.38, 0.35, 0.12],
        4: [0.20, 0.40, 0.28, 0.12],
        5: [0.25, 0.42, 0.22, 0.11],
    },
    # 宏观趋势型信号（market_screener触发）
    'macro': {
        1: [0.05, 0.30, 0.45, 0.20],  # L1: 4H+1D宏观主导
        2: [0.08, 0.32, 0.42, 0.18],
        3: [0.10, 0.35, 0.38, 0.17],
        4: [0.15, 0.38, 0.32, 0.15],
        5: [0.20, 0.40, 0.28, 0.12],
    },
}


def get_tf_weights(
    symbol: str,
    primary_tf: str = '1h',
    signal_source: str = 'default',
) -> dict[str, float]:
    """
    返回该合约在指定信号类型下的多周期评分权重。

    返回格式:
        {'15m': 0.30, '1h': 0.40, '4h': 0.22, '1d': 0.08}
    """
    tier = _get_tier(symbol)

    # 信号来源映射
    if signal_source in ('pump_auto', 'pump_hunter'):
        src = 'pump_auto'
    elif signal_source in ('ob_trend', 'OB_1H', 'OB_4H', 'brahma'):
        src = 'ob_trend'
    elif signal_source in ('macro', 'market_screener', 'brahma_scan'):
        src = 'macro'
    else:
        src = 'default'

    weights_list = TF_WEIGHT_REGISTRY.get(src, TF_WEIGHT_REGISTRY['default']).get(
        tier, TF_WEIGHT_REGISTRY['default'][3]
    )

    return {
        '15m': weights_list[0],
        '1h':  weights_list[1],
        '4h':  weights_list[2],
        '1d':  weights_list[3],
    }


def get_score_multiplier(
    symbol: str,
    score: float,
    primary_tf: str = '1h',
    signal_source: str = 'default',
) -> float:
    """
    根据合约流动性层级和信号周期，对评分进行最终调整。
    用于 brahma_core.py confluence_score 输出后的标准化。

    规则:
      - L1主流 + 4H信号 → 系数1.0（基准）
      - L4/L5小币 + 1H信号 → 系数0.85（降权，高周期噪音）
      - L4/L5小币 + 15M信号 → 系数1.05（短周期才准，轻度提升）
    """
    tier = _get_tier(symbol)
    weights = get_tf_weights(symbol, primary_tf, signal_source)
    tf_key = primary_tf.lower().replace('m', 'm').replace('h', 'h')

    if tier <= 2:
        return 1.0   # L1/L2主流：不做额外调整
    if tier >= 4:
        # 小币：高周期信号降权，短周期信号轻微加权
        if primary_tf in ('4h', '4H', '1d', '1D'):
            return 0.85
        if primary_tf in ('15m', '15M'):
            return 1.05
    return 1.0   # 其他情况不调整


# ── 快速验证 ──────────────────────────────────────────────
if __name__ == '__main__':
    test_cases = [
        ('BTCUSDT', '4h',  'ob_trend'),
        ('ETHUSDT', '1h',  'ob_trend'),
        ('REUSDT',  '15m', 'pump_auto'),
        ('XPINUSDT','1h',  'default'),
        ('PARTIUSDT','15m','pump_auto'),
    ]
    print(f"{'Symbol':14s} {'TF':4s} {'Source':14s} {'15M':5s} {'1H':5s} {'4H':5s} {'1D':5s} {'mult':6s}")
    print('-' * 65)
    for sym, tf, src in test_cases:
        w = get_tf_weights(sym, tf, src)
        m = get_score_multiplier(sym, 140, tf, src)
        print(f"{sym:14s} {tf:4s} {src:14s} {w['15m']:.2f}  {w['1h']:.2f}  {w['4h']:.2f}  {w['1d']:.2f}  {m:.2f}x")
