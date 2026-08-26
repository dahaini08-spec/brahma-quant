#!/usr/bin/env python3
"""
sentiment_engine.py — 资金费率+多空比情绪评分 (s17)
[设计院封印 2026-08-11 苏摩111]

重新实现：从 ms.sentiment 读取，无网络依赖
输出：int -8 ~ +8
"""


def get_sentiment_score(ms: dict, signal_dir: str = 'SHORT') -> tuple[int, dict]:
    """
    资金费率 + 多空比 + OI动量 综合情绪评分

    SHORT顺势加分规则（资金费高=多头拥挤=空单顺势）:
      funding_rate > 0.03%/8h   → +4   (多头付费，空单顺势)
      funding_rate > 0.01%      → +2
      funding_rate < -0.01%     → -4   (空头付费，空单逆势)
      LSR > 1.5 (多头占优)      → +3
      LSR < 0.7 (空头占优)      → -3
      OI动量 RISING             → +2 (SHORT) / -2 (LONG)

    LONG: 镜像
    """
    try:
        sent = ms.get('sentiment', {}) or {}
        fr   = float(sent.get('funding_rate') or sent.get('funding') or ms.get('funding_rate', 0))
        lsr  = float(sent.get('long_short_ratio') or sent.get('lsr', 1.0))
        oi_m = str(sent.get('oi_momentum', 'NEUTRAL')).upper()

        is_short = 'SHORT' in signal_dir.upper()
        score = 0
        detail = {}

        # 资金费率评分
        if is_short:
            if fr > 0.0003:    score += 4;  detail['funding'] = f'+4(高FR={fr:.4f})'
            elif fr > 0.0001:  score += 2;  detail['funding'] = f'+2(中FR={fr:.4f})'
            elif fr < -0.0001: score -= 4;  detail['funding'] = f'-4(负FR={fr:.4f})'
        else:
            if fr < -0.0003:   score += 4;  detail['funding'] = f'+4(低FR={fr:.4f}多单顺势)'
            elif fr < -0.0001: score += 2;  detail['funding'] = f'+2(负FR多单顺势)'
            elif fr > 0.0003:  score -= 4;  detail['funding'] = f'-4(高FR多单逆势)'

        # 多空比评分
        if is_short:
            if lsr > 1.5:    score += 3;  detail['lsr'] = f'+3(LSR={lsr:.2f}多头拥挤)'
            elif lsr < 0.7:  score -= 3;  detail['lsr'] = f'-3(LSR={lsr:.2f}空头拥挤)'
        else:
            if lsr < 0.7:    score += 3;  detail['lsr'] = f'+3(LSR={lsr:.2f}空头拥挤→多单顺势)'
            elif lsr > 1.5:  score -= 3;  detail['lsr'] = f'-3(LSR={lsr:.2f}多头拥挤→多单危险)'

        # OI动量
        if is_short and 'RISING' in oi_m:
            score += 1;  detail['oi_momentum'] = '+1(OI增多→空单顺势)'
        elif not is_short and 'FALLING' in oi_m:
            score += 1;  detail['oi_momentum'] = '+1(OI减少→多单顺势)'

        score = max(-8, min(8, score))
        return score, detail

    except Exception:
        return 0, {}
