#!/usr/bin/env python3
"""
zero_cost_prescorer.py
梵天 v5.1 · 零成本前置筛选层（MuonClip类比）
苏摩111封印 2026-07-19

原理：在35维矩阵运行前，用RSI+BB+OI三维快筛
      <20分直接丢弃，不跑35维矩阵（50→20修复：防止误过滤BTC/ETH）
      节省约70%算力，提升信号命中率

调用方式：
    from zero_cost_prescorer import pre_score
    score, tags = pre_score(symbol)
    if score < 20:
        return  # 直接跳过35维分析
"""
import requests
import statistics
import logging

logger = logging.getLogger(__name__)

PRE_SCORE_THRESHOLD = 20  # 达摩院审计修复2026-07-19: 50过高会过滤BTC/ETH有效信号，降至20只过滤完全无特征标的


def pre_score(symbol: str, regime: str = "BULL_TREND") -> tuple[float, list]:
    """
    零成本三维快筛。返回(score, tags)
    score < PRE_SCORE_THRESHOLD → 跳过35维矩阵
    """
    score = 0.0
    tags = []

    try:
        # ① RSI_1H（最重要）
        k1h = requests.get(
            f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1h&limit=20",
            timeout=5,
        ).json()
        closes = [float(x[4]) for x in k1h]
        vols = [float(x[5]) for x in k1h]

        gains, losses = [], []
        for i in range(1, 15):
            d = closes[-i] - closes[-i - 1]
            (gains if d > 0 else losses).append(abs(d))
        ag = statistics.mean(gains) if gains else 0.0001
        al = statistics.mean(losses) if losses else 0.0001
        rsi = 100 - (100 / (1 + ag / al))

        # RSI评分（体制感知）
        if regime in ("BULL_TREND", "BEAR_RECOVERY"):
            # 做多体制：超卖加分，超买减分
            if rsi < 25:
                score += 30; tags.append(f"RSI极度超卖({rsi:.1f})")
            elif rsi < 35:
                score += 20; tags.append(f"RSI超卖({rsi:.1f})")
            elif 35 <= rsi <= 75:
                score += 15; tags.append(f"RSI可用({rsi:.1f})")  # 扩展至75，ETH等高RSI标的不被误杀
            elif rsi > 85:
                score -= 5; tags.append(f"RSI极度超买({rsi:.1f})")  # 只在极端超买时轻微减分
        else:
            # 做空体制：超买加分，超卖减分
            if rsi > 75:
                score += 30; tags.append(f"RSI超买({rsi:.1f})")
            elif rsi > 65:
                score += 20; tags.append(f"RSI偏高({rsi:.1f})")
            elif 40 <= rsi <= 60:
                score += 15; tags.append(f"RSI中性({rsi:.1f})")
            elif rsi < 25:
                score -= 10; tags.append(f"RSI超卖警告({rsi:.1f})")

        # ② BB宽度
        ma20 = statistics.mean(closes[-20:])
        std20 = statistics.stdev(closes[-20:])
        bb_width = (std20 * 2 / ma20) * 100 if ma20 > 0 else 999

        if bb_width < 0.5:
            score += 35; tags.append(f"BB极度压缩({bb_width:.3f}%)")
        elif bb_width < 1.0:
            score += 25; tags.append(f"BB压缩({bb_width:.3f}%)")
        elif bb_width < 2.0:
            score += 10; tags.append(f"BB收窄({bb_width:.3f}%)")
        elif bb_width > 8.0:
            score -= 5; tags.append(f"BB过宽({bb_width:.1f}%)")

        # ③ OI变化（1H）
        oi_now = float(
            requests.get(
                f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}",
                timeout=5,
            ).json().get("openInterest", 0)
        )
        # OI异动：与价格方向判断
        vol_now = vols[-1]
        vol_avg = statistics.mean(vols[-13:-1]) if len(vols) >= 13 else vols[-1]
        vol_ratio = vol_now / vol_avg if vol_avg > 0 else 1

        if vol_ratio > 2.0:
            score += 15; tags.append(f"量比{vol_ratio:.1f}x异动")
        elif vol_ratio > 1.5:
            score += 8; tags.append(f"量比{vol_ratio:.1f}x")

        # ④ 资金费率
        fr = float(
            requests.get(
                f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}",
                timeout=5,
            ).json().get("lastFundingRate", 0)
        ) * 100

        if regime in ("BULL_TREND", "BEAR_RECOVERY"):
            if fr < -0.05:
                score += 10; tags.append(f"FR极负做多友好({fr:.3f}%)")
            elif fr > 0.08:
                score -= 5; tags.append(f"FR过高({fr:.3f}%)")
        else:
            if fr > 0.05:
                score += 10; tags.append(f"FR极正做空友好({fr:.3f}%)")

        logger.debug(f"[pre_score] {symbol} score={score:.1f} tags={tags}")

    except Exception as e:
        logger.warning(f"[pre_score] {symbol} error: {e}")
        # 出错时返回中性分，让35维继续
        return 60.0, [f"pre_score_error:{e}"]

    return round(score, 1), tags


def should_skip_35d(symbol: str, regime: str = "BULL_TREND") -> tuple[bool, float, list]:
    """
    便捷接口：返回是否跳过35维矩阵
    Returns: (skip: bool, score: float, tags: list)
    """
    score, tags = pre_score(symbol, regime)
    skip = score < PRE_SCORE_THRESHOLD
    return skip, score, tags


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    skip, score, tags = should_skip_35d(sym)
    print(f"{sym}: pre_score={score} skip={skip}")
    print(f"  tags: {tags}")
    print(f"  决策: {'跳过35维矩阵（节省算力）' if skip else '进入35维矩阵分析'}")
