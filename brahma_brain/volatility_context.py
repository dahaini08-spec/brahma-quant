"""
volatility_context.py — HCME M5波动率历史分位评估
设计院封印 2026-09-03 苏摩111

接入位置：brahma_core.py line 4369（C2: volatility_context块）

功能：根据当前ATR和BBW判断波动率所处历史分位，
     ULTRA_LOW时压缩仓位×0.7，HIGH时降低仓位。

不依赖外部API，用固定历史基准（来自6.5年历史数据统计）做分位判断。
"""

from typing import Optional

# ── 历史基准（来自6.5年BTC/ETH数据，ATR为价格占比，BBW为BB宽度比） ──
# ATR1H占价格比：p10=0.0008, p25=0.0012, p50=0.0020, p75=0.0035, p90=0.0060
# BBW（BB宽度/价格）：p10=0.006, p25=0.010, p50=0.018, p75=0.030, p90=0.050
_ATR_HIST = {
    'p10': 0.0008, 'p25': 0.0012, 'p50': 0.0020, 'p75': 0.0035, 'p90': 0.0060,
}
_BBW_HIST = {
    'p10': 0.006, 'p25': 0.010, 'p50': 0.018, 'p75': 0.030, 'p90': 0.050,
}

# 不同标的ATR基准倍数（相对BTC，粗略修正）
_SYMBOL_ATR_SCALE = {
    'ETHUSDT':  1.15,
    'SOLUSDT':  1.40,
    'BNBUSDT':  1.05,
    'BTCUSDT':  1.00,
}

def get_volatility_context(
    symbol: str,
    current_atr: float,
    current_bbw: float,
) -> dict:
    """
    评估当前波动率所处历史分位，输出vol_regime和pos_scale建议。

    Args:
        symbol:      交易对，如'BTCUSDT'
        current_atr: ATR1H / 当前价格（无量纲比值）
        current_bbw: BB宽度 / 当前价格（无量纲比值）

    Returns:
        {
            'vol_regime':  'ULTRA_LOW' | 'LOW' | 'NORMAL' | 'HIGH' | 'EXTREME',
            'atr_pct':     float,    # ATR百分位 0~1
            'bbw_pct':     float,    # BBW百分位 0~1
            'combined_pct': float,   # 综合百分位
            'pos_scale':   float,    # 建议仓位乘数 (0.7 ULTRA_LOW, 0.85 LOW, 1.0 NORMAL, 0.9 HIGH)
            'note':        str,
        }
    """
    # 标的修正
    scale = _SYMBOL_ATR_SCALE.get(symbol, 1.0)
    atr_ref = current_atr / scale  # 归一化到BTC基准

    # ATR百分位估算（线性插值）
    atr_pct = _percentile(atr_ref, _ATR_HIST)
    bbw_pct = _percentile(current_bbw, _BBW_HIST)

    combined = (atr_pct * 0.6 + bbw_pct * 0.4)

    if combined < 0.10:
        regime    = 'ULTRA_LOW'
        pos_scale = 0.70
        note      = '波动率极低(<p10)，可能盘整压缩，仓位压缩×0.7'
    elif combined < 0.25:
        regime    = 'LOW'
        pos_scale = 0.85
        note      = '波动率偏低(<p25)，趋势信号可信度降低，仓位×0.85'
    elif combined < 0.75:
        regime    = 'NORMAL'
        pos_scale = 1.00
        note      = '波动率正常区间，仓位正常'
    elif combined < 0.90:
        regime    = 'HIGH'
        pos_scale = 0.90
        note      = '波动率偏高(>p75)，滑点风险上升，仓位×0.9'
    else:
        regime    = 'EXTREME'
        pos_scale = 0.70
        note      = '波动率极高(>p90)，极端行情，仓位压缩×0.7'

    return {
        'vol_regime':   regime,
        'atr_pct':      round(atr_pct, 3),
        'bbw_pct':      round(bbw_pct, 3),
        'combined_pct': round(combined, 3),
        'pos_scale':    pos_scale,
        'note':         note,
        'symbol':       symbol,
    }


def _percentile(value: float, hist: dict) -> float:
    """用历史分位点做线性插值，返回0~1的分位数估算。"""
    anchors = [
        (0.00, 0.0),
        (hist['p10'], 0.10),
        (hist['p25'], 0.25),
        (hist['p50'], 0.50),
        (hist['p75'], 0.75),
        (hist['p90'], 0.90),
        (hist['p90'] * 3, 1.00),
    ]
    if value <= anchors[0][0]:
        return 0.0
    if value >= anchors[-1][0]:
        return 1.0
    for i in range(1, len(anchors)):
        lo_v, lo_p = anchors[i - 1]
        hi_v, hi_p = anchors[i]
        if lo_v <= value <= hi_v:
            if hi_v == lo_v:
                return lo_p
            t = (value - lo_v) / (hi_v - lo_v)
            return lo_p + t * (hi_p - lo_p)
    return 0.5
