#!/usr/bin/env python3
# STATUS: ACTIVE
# B3叙事识别引擎 — 梵天设计院 2026-08-25
# 功能: 市场叙事强度/方向、多空情绪量化、仓位修正
"""
narrative_engine.py — B3市场叙事识别引擎
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 2026-08-25 | 苏摩111批准封印

功能：
  1. get_narrative_score(symbol)  → 叙事强度/方向
  2. get_crowd_sentiment(symbol)  → 多空情绪 -100~+100
  3. format_narrative_report(symbol) → 可推送格式报告

叙事修正接入 position_sizer.get_position_pct()。
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

BASE = Path(__file__).parent.parent

# ── 历史FG缓存（叙事破裂检测用，内存级）─────────────────────────
_FG_HISTORY: list[int] = []
_FG_HISTORY_MAX = 12  # 最近12个读取点


def _load_macro_state() -> dict:
    """读取 data/macro_state.json，失败返回空dict"""
    try:
        path = BASE / 'data' / 'macro_state.json'
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}


def _update_fg_history(fg_val: int) -> None:
    """维护内存级FG历史，用于叙事破裂检测"""
    global _FG_HISTORY
    if not _FG_HISTORY or _FG_HISTORY[-1] != fg_val:
        _FG_HISTORY.append(fg_val)
        if len(_FG_HISTORY) > _FG_HISTORY_MAX:
            _FG_HISTORY = _FG_HISTORY[-_FG_HISTORY_MAX:]


def _detect_narrative_break(fg_now: int) -> str | None:
    """
    叙事破裂信号检测：
      FG从>75急跌到<60 → 贪婪崩溃，叙事反转
      FG从<25急涨到>40 → 恐慌结束，叙事反转
    返回: 'GREED_COLLAPSE' | 'FEAR_RELIEF' | None
    """
    if len(_FG_HISTORY) < 2:
        return None
    fg_prev_high = max(_FG_HISTORY[:-1])
    fg_prev_low  = min(_FG_HISTORY[:-1])
    if fg_prev_high > 75 and fg_now < 60:
        return 'GREED_COLLAPSE'
    if fg_prev_low < 25 and fg_now > 40:
        return 'FEAR_RELIEF'
    return None


def get_narrative_score(symbol: str) -> dict:
    """
    返回当前市场叙事强度和方向。

    Returns:
    {
      'fg':             int,           # 恐贪指数 0-100
      'fg_label':       str,           # Extreme Fear / Fear / Neutral / Greed / Extreme Greed
      'fg_trend':       str,           # RISING / FALLING / FLAT
      'macro_bias':     str,           # RISK_ON / RISK_OFF / NEUTRAL
      'macro_note':     str,           # 宏观备注
      'intensity':      str,           # STRONG / MODERATE / WEAK
      'direction':      str,           # BULL / BEAR / NEUTRAL
      'break_signal':   str | None,    # GREED_COLLAPSE / FEAR_RELIEF / None
      'note':           str,
    }
    """
    # ── 获取FG指数 ────────────────────────────────────────────────
    fg_val = 50
    fg_label = 'Neutral'
    fg_trend = 'FLAT'
    try:
        from macro_engine import get_fear_greed as _get_fg
        fg_data = _get_fg()
        fg_val   = int(fg_data.get('value', 50))
        fg_label = fg_data.get('label', 'Neutral')
        fg_trend = fg_data.get('trend', 'FLAT')
    except Exception:
        # fallback: macro_state.json
        try:
            ms = _load_macro_state()
            _fg = ms.get('fear_greed', 50)
            if isinstance(_fg, dict):
                fg_val = int(_fg.get('value', 50))
            elif _fg is not None:
                fg_val = int(_fg)
        except Exception:
            pass

    # 更新FG历史（叙事破裂检测）
    _update_fg_history(fg_val)

    # ── 读取 macro_state ─────────────────────────────────────────
    macro_bias = 'NEUTRAL'
    macro_note = ''
    try:
        ms = _load_macro_state()
        macro_bias = ms.get('macro_bias', 'NEUTRAL') or 'NEUTRAL'
        macro_note = ms.get('macro_note', '') or ''
    except Exception:
        pass

    # ── 叙事方向 ─────────────────────────────────────────────────
    # FG极值解读（逆向思维）
    if fg_val <= 20:
        # 极度恐惧 → 反向做多背景
        fg_direction = 'BULL'       # 市场超卖，叙事方向利多
        fg_dir_note = f'FG={fg_val}极度恐惧→反向做多背景'
    elif fg_val >= 80:
        # 极度贪婪 → 反向做空背景
        fg_direction = 'BEAR'       # 市场超买，叙事方向利空
        fg_dir_note = f'FG={fg_val}极度贪婪→反向做空背景'
    elif fg_val <= 35:
        fg_direction = 'BULL'       # 偏恐惧，轻度反向利多
        fg_dir_note = f'FG={fg_val}偏恐惧→轻度利多背景'
    elif fg_val >= 65:
        fg_direction = 'BEAR'       # 偏贪婪，轻度反向利空
        fg_dir_note = f'FG={fg_val}偏贪婪→轻度利空背景'
    else:
        fg_direction = 'NEUTRAL'
        fg_dir_note = f'FG={fg_val}中性区'

    # 宏观偏向修正
    if macro_bias == 'RISK_ON' and fg_direction == 'NEUTRAL':
        fg_direction = 'BULL'
    elif macro_bias == 'RISK_OFF' and fg_direction == 'NEUTRAL':
        fg_direction = 'BEAR'

    # ── 叙事强度 ─────────────────────────────────────────────────
    # FG越极端 → 叙事信号越强（逆向机会明显）
    fg_extreme = max(abs(fg_val - 50), 0)  # 偏离中性程度
    if fg_extreme >= 30:                    # FG≤20 或 FG≥80
        intensity = 'STRONG'
    elif fg_extreme >= 15:                  # FG≤35 或 FG≥65
        intensity = 'MODERATE'
    else:
        intensity = 'WEAK'

    # 叙事破裂检测
    break_signal = _detect_narrative_break(fg_val)

    note_parts = [fg_dir_note]
    if macro_note:
        note_parts.append(macro_note)
    if break_signal:
        note_parts.append(f'⚡叙事破裂:{break_signal}')

    return {
        'fg':           fg_val,
        'fg_label':     fg_label,
        'fg_trend':     fg_trend,
        'macro_bias':   macro_bias,
        'macro_note':   macro_note,
        'intensity':    intensity,
        'direction':    fg_direction,
        'break_signal': break_signal,
        'note':         ' | '.join(note_parts),
    }


def get_crowd_sentiment(symbol: str) -> dict:
    """
    多空情绪量化综合评分 (-100 ~ +100)
    正值 = 多头情绪, 负值 = 空头情绪

    Returns:
    {
      'score':      int,     # -100 ~ +100
      'lsr_pct':    float,   # 多头占比 %
      'fr':         float,   # 资金费率 %
      'oi_change':  float,   # OI变化 %
      'oi_momentum':str,     # INCREASING / DECREASING / NEUTRAL
      'lsr_signal': str,     # LONG_CROWDED / SHORT_CROWDED / NEUTRAL
      'fr_signal':  str,     # LONG_EXPENSIVE / SHORT_EXPENSIVE / NEUTRAL
      'oi_signal':  str,     # INFLOW / OUTFLOW / NEUTRAL
      'note':       str,
    }
    """
    score = 0
    notes = []

    # ── 1. LSR多空比 ─────────────────────────────────────────────
    lsr_pct = 50.0
    lsr_signal = 'NEUTRAL'
    try:
        from data_cache import get_long_short_ratio as _get_lsr
        lsr_pct = float(_get_lsr(symbol))
    except Exception:
        pass

    if lsr_pct >= 70:
        # 多头极拥挤（散户做多超70%） → 反向看空
        lsr_score = -40
        lsr_signal = 'LONG_CROWDED'
        notes.append(f'LSR={lsr_pct:.1f}%多头极拥挤(反空信号)')
    elif lsr_pct >= 60:
        lsr_score = -20
        lsr_signal = 'LONG_CROWDED'
        notes.append(f'LSR={lsr_pct:.1f}%偏多拥挤')
    elif lsr_pct <= 30:
        # 空头极拥挤（散户做空超70%） → 反向看多
        lsr_score = +40
        lsr_signal = 'SHORT_CROWDED'
        notes.append(f'LSR={lsr_pct:.1f}%空头极拥挤(反多信号)')
    elif lsr_pct <= 40:
        lsr_score = +20
        lsr_signal = 'SHORT_CROWDED'
        notes.append(f'LSR={lsr_pct:.1f}%偏空拥挤')
    else:
        lsr_score = 0
        notes.append(f'LSR={lsr_pct:.1f}%中性')

    score += lsr_score

    # ── 2. 资金费率 FR ───────────────────────────────────────────
    fr = 0.0
    fr_signal = 'NEUTRAL'
    try:
        from data_cache import get_funding_rate as _get_fr
        fr = float(_get_fr(symbol))
    except Exception:
        pass

    if fr > 0.01:
        # 多头付钱偏贵 → 多头过热
        fr_score = -30
        fr_signal = 'LONG_EXPENSIVE'
        notes.append(f'FR={fr:.4f}%多头付钱偏贵(过热)')
    elif fr > 0.005:
        fr_score = -15
        fr_signal = 'LONG_EXPENSIVE'
        notes.append(f'FR={fr:.4f}%多头略贵')
    elif fr < -0.01:
        # 空头付钱偏贵 → 空头过热（反向利多）
        fr_score = +30
        fr_signal = 'SHORT_EXPENSIVE'
        notes.append(f'FR={fr:.4f}%空头付钱偏贵(过热做空)')
    elif fr < -0.005:
        fr_score = +15
        fr_signal = 'SHORT_EXPENSIVE'
        notes.append(f'FR={fr:.4f}%空头略贵')
    else:
        fr_score = 0
        notes.append(f'FR={fr:.4f}%正常')

    score += fr_score

    # ── 3. OI趋势 ────────────────────────────────────────────────
    oi_change = 0.0
    oi_momentum = 'NEUTRAL'
    oi_signal = 'NEUTRAL'
    try:
        from data_cache import get_open_interest as _get_oi
        oi_data = _get_oi(symbol)
        oi_change = float(oi_data.get('oi_change_pct', 0.0))
        oi_momentum = oi_data.get('oi_momentum', 'NEUTRAL')
    except Exception:
        pass

    if oi_change >= 3.0:
        # OI大幅上升 → 新资金涌入，趋势加强
        oi_score = +20
        oi_signal = 'INFLOW'
        notes.append(f'OI+{oi_change:.1f}%新资金涌入')
    elif oi_change >= 1.0:
        oi_score = +10
        oi_signal = 'INFLOW'
        notes.append(f'OI+{oi_change:.1f}%轻微增仓')
    elif oi_change <= -3.0:
        # OI大幅下降 → 资金撤退，行情可能反转
        oi_score = -20
        oi_signal = 'OUTFLOW'
        notes.append(f'OI{oi_change:.1f}%资金撤退')
    elif oi_change <= -1.0:
        oi_score = -10
        oi_signal = 'OUTFLOW'
        notes.append(f'OI{oi_change:.1f}%轻微减仓')
    else:
        oi_score = 0
        notes.append(f'OI变化{oi_change:.1f}%平稳')

    score += oi_score

    # 裁剪到 [-100, +100]
    score = max(-100, min(100, score))

    return {
        'score':       score,
        'lsr_pct':     lsr_pct,
        'fr':          fr,
        'oi_change':   oi_change,
        'oi_momentum': oi_momentum,
        'lsr_signal':  lsr_signal,
        'fr_signal':   fr_signal,
        'oi_signal':   oi_signal,
        'note':        ' | '.join(notes),
    }


def format_narrative_report(symbol: str) -> str:
    """
    生成可推送的市场叙事报告字符串。
    """
    ns = {}
    cs = {}
    try:
        ns = get_narrative_score(symbol)
    except Exception as e:
        ns = {'error': str(e)}
    try:
        cs = get_crowd_sentiment(symbol)
    except Exception as e:
        cs = {'error': str(e)}

    fg       = ns.get('fg', '?')
    fg_label = ns.get('fg_label', '?')
    fg_trend = ns.get('fg_trend', '?')
    intensity = ns.get('intensity', '?')
    direction = ns.get('direction', '?')
    macro_bias = ns.get('macro_bias', '?')
    break_sig  = ns.get('break_signal')
    ns_note    = ns.get('note', '')

    sent_score = cs.get('score', 0)
    lsr_pct    = cs.get('lsr_pct', 50)
    fr         = cs.get('fr', 0)
    oi_change  = cs.get('oi_change', 0)
    cs_note    = cs.get('note', '')

    direction_emoji = {'BULL': '🟢', 'BEAR': '🔴', 'NEUTRAL': '⚪'}.get(direction, '⚪')
    intensity_emoji = {'STRONG': '🔥', 'MODERATE': '⚡', 'WEAK': '💤'}.get(intensity, '')
    sent_sign = '+' if sent_score > 0 else ''

    lines = [
        f'📊 [{symbol}] 市场叙事报告',
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━',
        f'恐贪指数: {fg} ({fg_label}) {fg_trend}',
        f'宏观偏向: {macro_bias}',
        f'叙事方向: {direction_emoji} {direction}  强度: {intensity_emoji} {intensity}',
    ]
    if break_sig:
        lines.append(f'⚡ 叙事破裂信号: {break_sig}')
    lines += [
        f'',
        f'📉 多空情绪: {sent_sign}{sent_score}/100',
        f'  LSR多头占比: {lsr_pct:.1f}%',
        f'  资金费率: {fr:.4f}%',
        f'  OI变化: {oi_change:+.2f}%',
        f'',
        f'📝 叙事: {ns_note}',
        f'📝 情绪: {cs_note}',
    ]
    return '\n'.join(lines)


def get_narrative_position_mult(fg_val: int, direction: str) -> tuple[float, str]:
    """
    叙事仓位修正乘数（供 position_sizer.get_position_pct 调用）

    规则：
      FG<20（极度恐惧）+ LONG  → ×1.15 (反向机会加持)
      FG>80（极度贪婪）+ SHORT → ×1.15
      FG<20 + SHORT            → ×0.80 (逆向风险)
      FG>80 + LONG             → ×0.80

    返回: (multiplier, note)
    """
    dir_upper = (direction or '').upper()
    if fg_val < 20:
        if 'LONG' in dir_upper:
            return 1.15, f'FG={fg_val}极度恐惧+做多→反向加持×1.15'
        elif 'SHORT' in dir_upper:
            return 0.80, f'FG={fg_val}极度恐惧+做空→逆向风险×0.80'
    elif fg_val > 80:
        if 'SHORT' in dir_upper:
            return 1.15, f'FG={fg_val}极度贪婪+做空→反向加持×1.15'
        elif 'LONG' in dir_upper:
            return 0.80, f'FG={fg_val}极度贪婪+做多→逆向风险×0.80'
    return 1.0, ''


# ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('=== B3叙事识别引擎 冒烟测试 ===')

    # 1. 叙事评分
    print('\n[1] get_narrative_score(BTCUSDT)')
    try:
        ns = get_narrative_score('BTCUSDT')
        print(f"  FG={ns['fg']} ({ns['fg_label']}) trend={ns['fg_trend']}")
        print(f"  macro_bias={ns['macro_bias']}  intensity={ns['intensity']}  direction={ns['direction']}")
        print(f"  break_signal={ns['break_signal']}")
        print(f"  note: {ns['note']}")
        assert ns['intensity'] in ('STRONG', 'MODERATE', 'WEAK'), 'intensity invalid'
        assert ns['direction'] in ('BULL', 'BEAR', 'NEUTRAL'),    'direction invalid'
        print('  ✅ get_narrative_score OK')
    except Exception as e:
        print(f'  ⚠️  get_narrative_score error: {e}')

    # 2. 多空情绪
    print('\n[2] get_crowd_sentiment(BTCUSDT)')
    try:
        cs = get_crowd_sentiment('BTCUSDT')
        print(f"  score={cs['score']}  lsr={cs['lsr_pct']:.1f}%  fr={cs['fr']:.4f}%  oi={cs['oi_change']:+.2f}%")
        print(f"  signals: lsr={cs['lsr_signal']} fr={cs['fr_signal']} oi={cs['oi_signal']}")
        print(f"  note: {cs['note']}")
        assert -100 <= cs['score'] <= 100, 'score out of range'
        print('  ✅ get_crowd_sentiment OK')
    except Exception as e:
        print(f'  ⚠️  get_crowd_sentiment error: {e}')

    # 3. 格式报告
    print('\n[3] format_narrative_report(BTCUSDT)')
    try:
        report = format_narrative_report('BTCUSDT')
        print(report)
        print('  ✅ format_narrative_report OK')
    except Exception as e:
        print(f'  ⚠️  format_narrative_report error: {e}')

    # 4. 叙事乘数
    print('\n[4] get_narrative_position_mult 逻辑验证')
    cases = [
        (15,  'LONG',  1.15, 'FG<20+LONG→1.15'),
        (15,  'SHORT', 0.80, 'FG<20+SHORT→0.80'),
        (85,  'SHORT', 1.15, 'FG>80+SHORT→1.15'),
        (85,  'LONG',  0.80, 'FG>80+LONG→0.80'),
        (50,  'LONG',  1.00, 'FG=50中性→1.00'),
    ]
    all_ok = True
    for fg, d, expected, desc in cases:
        mult, note = get_narrative_position_mult(fg, d)
        ok = abs(mult - expected) < 0.001
        flag = '✅' if ok else '❌'
        print(f'  {flag} {desc}: got {mult} | {note}')
        if not ok:
            all_ok = False
    if all_ok:
        print('  ✅ get_narrative_position_mult OK')

    print('\nB3完成 ✅')
