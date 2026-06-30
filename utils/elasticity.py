"""
梵天工具函数 · 弹性指数
从 volatility_hunter.py 提炼 · 2026-05-20
原文件已降级，此函数供 hunter_v2 通道A 候选排序使用
"""

def elasticity_index(atr_pct: float, bb_width: float) -> float:
    """
    弹性指数 = ATR% × BB宽度
    数值越大 = 波动潜力越强
    用途：hunter_v2 通道A 候选排序加权

    Args:
        atr_pct:  ATR百分比（如 2.5 表示2.5%）
        bb_width: 布林带宽度（如 0.08）
    Returns:
        弹性指数（无量纲，用于排序）
    """
    return atr_pct * bb_width


def is_high_elasticity(atr_pct: float, bb_width: float,
                       threshold: float = 0.15) -> bool:
    """
    判断是否高弹性标的
    threshold=0.15 → hunter_v2 通道A 额外+5分加权线
    """
    return elasticity_index(atr_pct, bb_width) >= threshold
