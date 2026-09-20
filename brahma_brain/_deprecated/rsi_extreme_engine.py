"""
rsi_extreme_engine.py — RSI极值检测评分引擎
[2026-09-18 FIX-5] ms-dict接口，从 ms['rsi_1h'] 读取

接口：rsi_extreme_score(ms: dict, direction: str) -> int
"""


import sys
def _calc_rsi(closes: list, period: int = 14) -> float:
    """从closes列表计算RSI"""
    if not closes or len(closes) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, min(len(closes), period + 1)):
        diff = closes[-i] - closes[-i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(-diff)
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_extreme_score(ms: dict, direction: str) -> int:
    """
    RSI极值评分

    ms: MarketSnapshot dict，读取 ms['rsi_1h']
        如果没有rsi_1h，从 ms['closes_1h'] 自动计算
    direction: 'LONG' or 'SHORT'

    返回: int score
    """
    # 尝试读取rsi_1h
    rsi = None
    if isinstance(ms, dict):
        rsi = ms.get('rsi_1h')
        if rsi is None:
            rsi = ms.get('rsi_14')
        if rsi is None:
            # 从momentum嵌套字段读取
            try:
                rsi = ms.get('momentum', {}).get('rsi_1h')
            except (AttributeError, TypeError) as _e:
                print(f"[WARN] rsi_extreme_engine: _e", file=sys.stderr)

    # 如果没有rsi，从closes计算
    if rsi is None:
        closes = ms.get('closes_1h') or ms.get('raw_closes') or []
        if not closes:
            return 0
        rsi = _calc_rsi(closes)

    try:
        rsi = float(rsi)
    except (TypeError, ValueError):
        return 0

    # 评分逻辑
    if direction in ('SHORT', '做空'):
        if rsi >= 75:
            return 10  # 超买做空
        elif rsi >= 65:
            return 5
        elif rsi <= 25:
            return -8  # 超卖做空危险
        elif rsi <= 35:
            return -4
        return 0
    else:  # LONG
        if rsi <= 25:
            return 10  # 超卖做多
        elif rsi <= 35:
            return 5
        elif rsi >= 75:
            return -8  # 超买卖多危险
        elif rsi >= 65:
            return -4
        return 0
