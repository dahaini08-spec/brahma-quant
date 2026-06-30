"""
upgrade_v2/v2_integrator.py
v2信号增强集成器 — 在 brahma_core.py 基础评分上叠加 v2 层增强

接口：
  v2_enhance_signal(symbol, direction, score, ms, breakdown, nav, interval) → dict

v2增强内容：
  1. 自适应门槛检查（当前门槛 vs 信号评分）
  2. 体制胜率注入（近期BEAR_TREND SHORT实际WR）
  3. MTF多时间框架一致性注释
  4. 仓位建议（基于NAV+Kelly）
"""

import json, time
from pathlib import Path

_BASE = Path(__file__).parent.parent
_DATA = _BASE / 'data'


def v2_enhance_signal(
    symbol:    str,
    direction: str,
    score:     float,
    ms:        dict,
    breakdown: dict,
    nav:       float = 127.62,
    interval:  str   = '1h',
) -> dict:
    """
    v2 信号增强 — 在 brahma_core confluence_score 之后调用
    返回增强字段，写入 cf['v2_*'] 供日志和推送使用
    """
    result = {
        'mode':         'v2_integrator',
        'audit':        {},
        'mtf_note':     '',
        'pos_pct':      0.0,
        'breakdown_ext': {},
    }

    audit = {}

    # ── 1. 自适应门槛检查 ────────────────────────────────────────
    try:
        from upgrade_v2.adaptive_threshold import get_threshold
        threshold = get_threshold(symbol, ms.get('regime', ''), direction)
        audit['adaptive_threshold'] = threshold
        audit['score_vs_threshold'] = round(score - threshold, 1)
        audit['threshold_pass']     = score >= threshold
        result['breakdown_ext']['自适应门槛'] = f'{score:.0f}/{threshold:.0f} {"✅" if score >= threshold else "❌"}'
    except Exception as e:
        audit['adaptive_threshold_err'] = str(e)[:40]

    # ── 2. 体制胜率注入 ──────────────────────────────────────────
    try:
        from upgrade_v2.regime_health_guard import get_regime_stats
        regime = ms.get('regime', '')
        stats  = get_regime_stats(regime, direction, window=100)
        if stats.get('n', 0) >= 10:
            audit['regime_wr_live']  = stats['win_rate']
            audit['regime_ev_live']  = stats['ev']
            audit['regime_n_live']   = stats['n']
            wr_label = f"WR={stats['win_rate']:.1%}(n={stats['n']})"
            result['mtf_note'] = f'实盘{wr_label} EV={stats["ev"]:+.3f}%'
            result['breakdown_ext']['实盘体制WR'] = wr_label
        else:
            result['mtf_note'] = f'实盘样本不足({stats.get("n",0)}笔)'
    except Exception as e:
        audit['regime_stats_err'] = str(e)[:40]

    # ── 3. 仓位建议（简化Kelly） ──────────────────────────────────
    try:
        win_rate = audit.get('regime_wr_live', 0.65)
        # 简化Kelly: f = (WR - (1-WR)/RR) × kelly_fraction
        rr       = 1.0    # v4.0铁证 RR=1.0
        kelly    = (win_rate - (1 - win_rate) / rr)
        kelly_f  = max(0.0, min(kelly * 0.5, 0.05))  # 半Kelly，上限5%
        pos_pct  = round(kelly_f * 100, 2)
        # 按评分调整
        score_mult = min(score / 140, 1.0) if score > 0 else 0
        pos_pct    = round(pos_pct * score_mult, 2)
        result['pos_pct'] = pos_pct
        audit['kelly_pos_pct'] = pos_pct
        result['breakdown_ext']['Kelly仓位'] = f'{pos_pct:.1f}%'
    except Exception as e:
        audit['kelly_err'] = str(e)[:40]

    result['audit'] = audit
    return result


if __name__ == '__main__':
    # 测试
    test_ms = {'regime': 'BEAR_TREND', 'nav': 127.62}
    r = v2_enhance_signal(
        symbol='BTCUSDT', direction='SHORT', score=142.0,
        ms=test_ms, breakdown={}, nav=127.62
    )
    print(f'v2增强结果:')
    for k, v in r.items():
        print(f'  {k}: {v}')
