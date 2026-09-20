"""
bollinger_engine.py — 布林带偏离度评分引擎
[2026-09-18 FIX-4] ms-dict接口，从 momentum.bb.pos 读取

接口：bollinger_score(ms: dict, direction: str) -> int
"""
import sys
import statistics


def _calc_bollinger_pos(closes: list) -> float:
    """从closes列表计算Bollinger %B position"""
    if not closes or len(closes) < 20:
        return 0.5
    period = min(20, len(closes))
    sample = closes[-period:]
    mean = statistics.mean(sample)
    std = statistics.stdev(sample) if len(sample) > 1 else 0.0
    if std == 0:
        return 0.5
    price = closes[-1]
    upper = mean + 2 * std
    lower = mean - 2 * std
    pos = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
    return max(0.0, min(1.0, pos))


def bollinger_score(ms: dict, direction: str) -> int:
    """
    布林带偏离度评分

    ms: MarketSnapshot dict，读取 ms['momentum']['bb']['pos']
        如果没有bb字段，从 ms['closes_1h'] 或 ms['raw_closes'] 自动计算
    direction: 'LONG' or 'SHORT'

    返回: int score
    """
    # 尝试从momentum.bb.pos读取
    bb_pos = None
    try:
        bb_pos = ms.get('momentum', {}).get('bb', {}).get('pos')
    except (AttributeError, TypeError) as _e:
        print(f"[WARN] bollinger_engine: _e", file=sys.stderr)

    # 如果没有bb.pos，从closes计算
    if bb_pos is None:
        closes = ms.get('closes_1h') or ms.get('raw_closes') or []
        if not closes:
            return 0
        bb_pos = _calc_bollinger_pos(closes)

    # 评分逻辑
    if direction in ('SHORT', '做空'):
        if bb_pos > 0.9:
            return 8  # 超买做空
        elif bb_pos > 0.75:
            return 4
        elif bb_pos < 0.1:
            return -6  # 超卖做空危险
        elif bb_pos < 0.25:
            return -3
        return 0
    else:  # LONG
        if bb_pos < 0.1:
            return 8  # 超卖做多
        elif bb_pos < 0.25:
            return 4
        elif bb_pos > 0.9:
            return -6  # 超买卖多危险
        elif bb_pos > 0.75:
            return -3
        return 0
