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

    # ════════════════════════════════════════════════════════════
    # ── 复盘修复层（设计院 2026-07-03 逐笔根因封印）───────────────
    # 根因A/B/C/E 修复：三项新过滤，结果写入 result['allowed']=False 触发封锁
    # ════════════════════════════════════════════════════════════
    result['allowed'] = True
    result['block_reason'] = ''

    try:
        regime = ms.get('regime', '')
        rsi_1h = float(ms.get('rsi_1h', ms.get('rsi', 50)) or 50)
        price  = float(ms.get('price', 0) or 0)

        # ── 修复E：BTC大方向BULL时，CHOP品种禁止做空 ──────────────────
        # 根因：6/13 AVAX/LTC/SOL/BNB做空 → BTC/ETH同日大涨3%拖动全线上行
        # 规则：BTC体制=BEAR_RECOVERY或BULL_TREND 且 当前品种regime=CHOP时 → 禁止SHORT
        # 铁证：6月5笔CHOP_SHORT全止损，BTC同日均上涨1~3%
        if direction == 'SHORT' and regime in ('CHOP_MID', 'CHOP_LOW', 'CHOP_HIGH'):
            try:
                import json as _j
                from pathlib import Path as _P
                _rs = _j.load(open(_P(__file__).parent.parent / 'data' / 'regime_state.json'))
                _btc_regime = _rs.get('BTCUSDT', {}).get('confirmed', '')
                _bull_regimes = ('BULL_TREND', 'BULL_EARLY', 'BEAR_RECOVERY')
                if _btc_regime in _bull_regimes:
                    result['allowed'] = False
                    result['block_reason'] = (
                        f'[修复E] CHOP_SHORT封锁: 品种体制={regime} 但BTC={_btc_regime}(上行)'
                        f' → CHOP空单成功率<20%，拒绝'
                    )
            except Exception as _e_e:
                pass  # 静默，不阻断

        # ── 修复B：BULL_CORRECTION做空需要RSI_1H≥60 ────────────────────
        # 根因：6/13 HYPE/NEAR SHORT时RSI_1H=48~58（中性区），无超买确认就做空
        # 铁证：4次BULL_CORRECTION SHORT全止损，RSI均在45~58中性区
        # 规则：BULL_CORRECTION体制做空 → 必须RSI_1H≥60（确认超买回落）
        if direction == 'SHORT' and regime == 'BULL_CORRECTION' and result['allowed']:
            if rsi_1h < 60:
                result['allowed'] = False
                result['block_reason'] = (
                    f'[修复B] BULL_CORRECTION_SHORT: RSI_1H={rsi_1h:.1f}<60'
                    f' 无超买确认，中性RSI做空=赌博 → 封锁'
                )

        # ── 修复A：同品种同方向24H内已有有效信号去重 ───────────────────
        # 根因：6/13 BNB SHORT连发3次，进场区几乎相同（604~606），OB未失效就重复开
        # 规则：同品种同方向6H内已存在valid信号 → 拒绝重复
        if result['allowed']:
            try:
                import time as _t
                _sig_log = _P(__file__).parent.parent / 'data' / 'live_signal_log.jsonl'
                _now = _t.time()
                _dedup_window = 6 * 3600  # 6H去重窗口
                _lines = open(_sig_log).readlines() if _sig_log.exists() else []
                for _l in reversed(_lines[-50:]):
                    try:
                        _d = _j.loads(_l.strip())
                        if (_d.get('symbol') == symbol
                                and _d.get('direction') == direction
                                and _d.get('valid')
                                and _now - _d.get('ts', 0) < _dedup_window):
                            result['allowed'] = False
                            result['block_reason'] = (
                                f'[修复A] 同品种去重: {symbol} {direction}'
                                f' 在{(_now-_d["ts"])/3600:.1f}H前已有valid信号'
                                f' signal_id={_d.get("signal_id","?")[:8]}'
                            )
                            break
                    except:
                        pass
            except Exception as _e_a:
                pass  # 静默

    except Exception as _e_post:
        pass  # 修复层异常不阻断主流

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
