#!/usr/bin/env python3
"""
dharma_nodes.py — 达摩院6节点预测验证引擎 v2.1
设计院重建 · 2026-07-09

6节点验证体系（每节点 pass/fail）:
  N1: 体制一致性 (regime alignment)
  N2: 资金费率方向 (funding_rate direction)
  N3: 多空比支撑 (LSR support)
  N4: OI方向 (open interest momentum)
  N5: 恐慌贪婪 (fear & greed)
  N6: 动量共振 (momentum consensus)

返回:
  nodes_pass: int (0-6)
  verdict: 'HIGH_CONF'(>=5) | 'PASS'(>=3) | 'WEAK'(2) | 'FAIL'(0-1)
  score_mult: float (1.05/1.0/0.85/0.0)
  detail: str
"""

def evaluate_nodes(ms: dict, signal_dir: str, fg: int = 50) -> dict:
    """
    ms: market_state dict (from ms_analyze)
    signal_dir: 'LONG' or 'SHORT'
    fg: fear & greed index (0-100, default 50)
    """
    nodes_pass = 0
    details = []
    is_long = signal_dir.upper() == 'LONG'

    try:
        # ── N1: 体制一致性 ──────────────────────────────────────────
        regime = ms.get('regime', 'CHOP_MID')
        n1 = False
        if is_long and regime in ('BULL_TREND', 'BEAR_RECOVERY', 'CHOP_MID'):
            n1 = True
        elif not is_long and regime in ('BEAR_TREND', 'BEAR_EARLY', 'CHOP_MID'):
            n1 = True
        if n1:
            nodes_pass += 1
            details.append('N1✓')
        else:
            details.append('N1✗')

        # ── N2: 资金费率方向 ────────────────────────────────────────
        try:
            fr = ms['sentiment']['funding_rate']
            # 正费率 → 多头付空头，空头有利；负费率 → 空头付多头，多头有利
            n2 = (is_long and fr < 0.0005) or (not is_long and fr > -0.0005)
            if n2:
                nodes_pass += 1
                details.append('N2✓')
            else:
                details.append('N2✗')
        except Exception:
            details.append('N2?')

        # ── N3: 多空比 ──────────────────────────────────────────────
        try:
            lsr = ms['sentiment']['long_short_ratio']
            # LSR>1 → 多头偏多；做多时 LSR 不能过高（拥挤）
            if is_long:
                n3 = 0.8 <= lsr <= 2.0
            else:
                n3 = lsr > 1.2  # 多头拥挤 → 空头机会
            if n3:
                nodes_pass += 1
                details.append('N3✓')
            else:
                details.append('N3✗')
        except Exception:
            details.append('N3?')

        # ── N4: OI方向 ──────────────────────────────────────────────
        try:
            oi_mom = ms['sentiment'].get('oi_momentum', 'NEUTRAL')
            oi_chg = ms['sentiment'].get('oi_change_pct', 0.0)
            if is_long:
                n4 = oi_mom in ('UP', 'RISING') or oi_chg > 0.5
            else:
                n4 = oi_mom in ('DOWN', 'FALLING') or oi_chg < -0.5
            if n4:
                nodes_pass += 1
                details.append('N4✓')
            else:
                details.append('N4✗')
        except Exception:
            details.append('N4?')

        # ── N5: 恐慌贪婪 ────────────────────────────────────────────
        # 极度恐慌(<25)→多头机会；极度贪婪(>75)→空头机会
        n5 = False
        if is_long and fg < 45:
            n5 = True
        elif not is_long and fg > 55:
            n5 = True
        elif 35 <= fg <= 65:
            n5 = True  # 中性区域不惩罚
        if n5:
            nodes_pass += 1
            details.append('N5✓')
        else:
            details.append('N5✗')

        # ── N6: 动量共振 ────────────────────────────────────────────
        try:
            consensus = ms['trend']['consensus']['consensus']
            if is_long:
                n6 = consensus in ('BULLISH', 'MILD_BULLISH')
            else:
                n6 = consensus in ('BEARISH', 'MILD_BEARISH')
            if n6:
                nodes_pass += 1
                details.append('N6✓')
            else:
                details.append('N6✗')
        except Exception:
            details.append('N6?')

    except Exception as e:
        return {
            'nodes_pass': 0,
            'verdict': 'ERROR',
            'score_mult': 1.0,
            'detail': f'node_err:{str(e)[:40]}'
        }

    # ── 判定 ────────────────────────────────────────────────────────
    detail_str = ' '.join(details)
    if nodes_pass >= 5:
        verdict = 'HIGH_CONF'
        score_mult = 1.0  # 主流程会额外×1.05
    elif nodes_pass >= 3:
        verdict = 'PASS'
        score_mult = 1.0
    elif nodes_pass == 2:
        verdict = 'WEAK'
        score_mult = 0.85
    else:
        verdict = 'FAIL'
        score_mult = 0.0  # 主流程会-30分

    return {
        'nodes_pass': nodes_pass,
        'verdict': verdict,
        'score_mult': score_mult,
        'detail': detail_str,
    }


if __name__ == '__main__':
    # 单元测试
    ms_mock = {
        'regime': 'BEAR_TREND',
        'sentiment': {
            'funding_rate': -0.0002,
            'long_short_ratio': 1.5,
            'oi_momentum': 'DOWN',
            'oi_change_pct': -1.2,
        },
        'trend': {
            'consensus': {'consensus': 'BEARISH'},
        },
    }
    result = evaluate_nodes(ms_mock, 'SHORT', fg=68)
    print(f"节点: {result['nodes_pass']}/6  verdict={result['verdict']}  mult={result['score_mult']}")
    print(f"detail: {result['detail']}")
