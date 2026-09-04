"""
llm_council.py — 梵天本地LLM Council裁决层
设计院 2026-08-06 自主创建

职责：
  接收35维矩阵breakdown，输出苏摩可读的一句话裁决
  纯规则引擎（零延迟、零成本、零外部依赖）

三专家视角：
  宏观裁判：体制+DXY+宏观方向
  结构裁判：SMC/清算位/大户方向
  量化裁判：WR矩阵+EV+历史铁证

输出格式（用于vip_template_F.md的LLM行）：
  bias: 偏多/偏空/中性
  reason: 一句话核心逻辑（<30字）
  action: ENTER/WAIT/AVOID
  confidence: HIGH/MED/LOW
"""

from __future__ import annotations
from typing import Optional


def council_verdict(
    breakdown: dict,
    signal_dir: str,        # 'LONG' or 'SHORT'
    regime: str,            # 'BULL_TREND' / 'BEAR_TREND' / 'CHOP_MID' etc.
    score: float,
    liq_data: Optional[dict] = None,   # liq_heatmap dict
    # 扩展字段（供LLM模式使用）
    fvg_dir: str = 'NONE',
    oi_signal: str = 'MIXED',
    sm_signal: str = 'NEUTRAL',
    hurst: float = 0.5,
    kappa: float = 0.0,
    entry_lo: float = 0.0,
    entry_hi: float = 0.0,
    price: float = 0.0,
    sym: str = 'BTC',
) -> dict:
    """
    返回:
        bias: '偏多' | '偏空' | '中性'
        reason: str (≤30字)
        action: 'ENTER' | 'WAIT' | 'AVOID'
        confidence: 'HIGH' | 'MED' | 'LOW'
        council_score: int  (三专家综合分, -3~+3)
    """
    # ── 优先：真实LLM裁决（OpenRouter免费模型）────────────────────────────
    # 设计院三方封印 2026-09-04 苏摩111
    if price > 0 and entry_lo > 0:
        try:
            import sys as _sys
            from pathlib import Path as _Path
            _scripts = str(_Path(__file__).parent.parent / 'scripts')
            _brain = str(_Path(__file__).parent)
            for _p in [_scripts, _brain]:
                if _p not in _sys.path:
                    _sys.path.insert(0, _p)
            from free_llm_client import council_three_way, council_llm
            liq_up = liq_data.get('nearest_short', 0) if liq_data else 0
            liq_dn = liq_data.get('nearest_long', 0)  if liq_data else 0
            # C: 三方独立投票（置信度更高）
            _llm_result = council_three_way(
                sym=sym, price=price, regime=regime, score=score,
                fvg_dir=fvg_dir, fvg_magnet=breakdown.get('_fvg_magnet', 0),
                oi_signal=oi_signal, sm_signal=sm_signal,
                big_long=breakdown.get('_big_long', 50),
                hurst=hurst, kappa=kappa,
                harv=breakdown.get('_harv', 0),
                entry_lo=entry_lo, entry_hi=entry_hi,
                liq_up=liq_up, liq_dn=liq_dn,
                macro_bias=breakdown.get('macro_bias', 'NEUTRAL'),
                fear_greed=int(breakdown.get('fear_greed', 50)),
            )
            # fallback到单次LLM
            if not _llm_result:
                _llm_result = council_llm(
                    regime=regime, bias=signal_dir, fvg_dir=fvg_dir,
                    oi_signal=oi_signal, sm_signal=sm_signal,
                    hurst=hurst, kappa=kappa, score=score,
                    entry_lo=entry_lo, entry_hi=entry_hi, price=price,
                    liq_up=liq_up, liq_dn=liq_dn, sym=sym,
                )
            if _llm_result and _llm_result.get('action'):
                _llm_result['council_score'] = 0
                _llm_result['votes'] = []
                _llm_result['source'] = 'LLM'
                return _llm_result
        except Exception:
            pass  # 降级到规则引擎

    # ── Fallback：规则引擎 ─────────────────────────────────────────────────
    votes = []   # 每项 +1/-1/0

    # ── 宏观裁判 ────────────────────────────────────────────────────────────
    macro_reasons = []

    # 体制顺势
    bull_regimes = {'BULL_TREND', 'BEAR_RECOVERY', 'BULL_CORRECTION'}
    bear_regimes = {'BEAR_TREND', 'BEAR_EARLY'}
    if signal_dir == 'LONG' and regime in bull_regimes:
        votes.append(+1); macro_reasons.append('体制顺势')
    elif signal_dir == 'LONG' and regime in bear_regimes:
        votes.append(-1); macro_reasons.append('逆体制做多')
    elif signal_dir == 'SHORT' and regime in bear_regimes:
        votes.append(+1); macro_reasons.append('体制顺势空')
    elif signal_dir == 'SHORT' and regime in bull_regimes:
        votes.append(-1); macro_reasons.append('逆体制做空')
    else:
        votes.append(0); macro_reasons.append('体制中性')

    # 宏观外部（DXY/NQ信号）
    macro_score = breakdown.get('宏观+事件', 0)
    if isinstance(macro_score, (int, float)):
        if macro_score >= 4:
            votes.append(+1); macro_reasons.append('宏观支持')
        elif macro_score <= -4:
            votes.append(-1); macro_reasons.append('宏观压制')

    # ── 结构裁判 ────────────────────────────────────────────────────────────
    struct_reasons = []

    # SMC结构
    smc_score = breakdown.get('SMC结构', 0)
    if isinstance(smc_score, str):
        try: smc_score = float(smc_score.split()[0])
        except: smc_score = 0
    if smc_score >= 12:
        votes.append(+1); struct_reasons.append('SMC结构强')
    elif smc_score <= 0:
        votes.append(-1); struct_reasons.append('SMC结构弱')
    else:
        votes.append(0)

    # 清算位裁决
    if liq_data:
        dist_short = liq_data.get('dist_to_short_liq', 99)
        dist_long  = liq_data.get('dist_to_long_liq', 99)
        if signal_dir == 'LONG':
            # 做多：上方空头墙近 = TP有磁吸，利多；下方多头墙近 = 踩踏风险
            if dist_short < 2.5:
                votes.append(+1); struct_reasons.append(f'上方清算墙近(+{dist_short:.1f}%)')
            if dist_long < 1.0:
                votes.append(-1); struct_reasons.append(f'下方踩踏风险(-{dist_long:.1f}%)')
        else:  # SHORT
            # 做空：下方多头墙近 = TP有磁吸，利空；上方空头墙近 = 被轧空风险
            if dist_long < 2.5:
                votes.append(+1); struct_reasons.append(f'下方清算墙近(-{dist_long:.1f}%)')
            if dist_short < 1.0:
                votes.append(-1); struct_reasons.append(f'上方轧空风险(+{dist_short:.1f}%)')

    # 大户方向
    sm_raw = breakdown.get('_smart_money', 0)
    if isinstance(sm_raw, str):
        try: sm_raw = float(sm_raw.split()[0].replace('+',''))
        except: sm_raw = 0
    if signal_dir == 'LONG' and sm_raw >= 3:
        votes.append(+1); struct_reasons.append('大户偏多')
    elif signal_dir == 'LONG' and sm_raw <= -3:
        votes.append(-1); struct_reasons.append('大户偏空')
    elif signal_dir == 'SHORT' and sm_raw <= -3:
        votes.append(+1); struct_reasons.append('大户偏空顺势')
    elif signal_dir == 'SHORT' and sm_raw >= 3:
        votes.append(-1); struct_reasons.append('大户偏多逆势')

    # ── 量化裁判 ────────────────────────────────────────────────────────────
    quant_reasons = []

    # MTF共振
    mtf = breakdown.get('n22a_mtf_consensus', 0)
    if isinstance(mtf, str):
        try: mtf = float(mtf.split()[0].replace('+',''))
        except: mtf = 0
    if mtf >= 3:
        votes.append(+1); quant_reasons.append('MTF共振')
    elif mtf <= -3:
        votes.append(-1); quant_reasons.append('MTF分裂')

    # 量能验证
    vol_score = breakdown.get('量能验证', 0)
    if isinstance(vol_score, (int, float)) and vol_score >= 15:
        votes.append(+1); quant_reasons.append('量能满分')
    elif isinstance(vol_score, (int, float)) and vol_score <= 0:
        votes.append(-1); quant_reasons.append('量能弱')

    # 多头拥挤风险（来自量能衰竭）
    decay = breakdown.get('量能衰竭+背离共振', 0)
    if isinstance(decay, str):
        try: decay = float(decay.split()[0].replace('+',''))
        except: decay = 0
    if signal_dir == 'LONG' and isinstance(decay, (int, float)) and decay <= -8:
        votes.append(-1); quant_reasons.append('多头拥挤')

    # ── 综合裁决 ─────────────────────────────────────────────────────────────
    council_score = sum(votes)
    all_reasons = macro_reasons + struct_reasons + quant_reasons

    # 阈值设定
    if council_score >= 3:
        bias, confidence = '偏多' if signal_dir == 'LONG' else '偏空', 'HIGH'
        action = 'ENTER'
    elif council_score >= 1:
        bias, confidence = '偏多' if signal_dir == 'LONG' else '偏空', 'MED'
        action = 'WAIT' if score < 145 else 'ENTER'
    elif council_score <= -2:
        bias = '偏空' if signal_dir == 'LONG' else '偏多'
        confidence = 'HIGH'
        action = 'AVOID'
    elif council_score == -1:
        bias, confidence = '中性', 'LOW'
        action = 'WAIT'
    else:
        bias, confidence = '中性', 'MED'
        action = 'WAIT'

    # 构建reason（取最重要的2个）
    if all_reasons:
        top = all_reasons[:2]
        reason = '，'.join(top)
    else:
        reason = '信号中性，无明显偏向'

    return {
        'bias':          bias,
        'reason':        reason,
        'action':        action,
        'confidence':    confidence,
        'council_score': council_score,
        'votes':         votes,
    }


def format_verdict_line(verdict: dict, symbol: str = '') -> str:
    """
    格式化为vip_template_F.md的LLM行：
    LLM: 偏多 — 体制顺势，SMC结构强
    """
    emoji = {'偏多': '🟢', '偏空': '🔴', '中性': '⚪'}.get(verdict['bias'], '⚪')
    conf_tag = {'HIGH': '', 'MED': ' (中等置信)', 'LOW': ' (低置信)'}.get(
        verdict.get('confidence', 'MED'), '')
    return f"LLM: {emoji}{verdict['bias']}{conf_tag} — {verdict['reason']}"
