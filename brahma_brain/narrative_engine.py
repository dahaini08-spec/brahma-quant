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


# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/macro_calendar.py ══
"""
macro_calendar.py — 宏观事件日历引擎
设计院 P3修复 · 2026-07-12

职责：
  实时返回近期高影响宏观事件
  CPI / FOMC / 非农 / GDP发布日提前预警

数据源：
  - 硬编码2026全年日历（每季更新）
  - F&G指数：alternative.me（免费）
  - BTC主导率：CoinGecko /global（免费）
"""

import json
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

CACHE_FILE = Path(__file__).parent.parent / 'data' / 'macro_cal_cache.json'
CACHE_TTL  = 1800  # 30分钟

# ── 2026全年重要宏观事件硬编码 ──────────────────────────
MACRO_EVENTS_2026 = [
    # FOMC
    {'date': '2026-01-29', 'event': 'FOMC', 'impact': 'CRITICAL', 'cat': 'fed'},
    {'date': '2026-03-18', 'event': 'FOMC', 'impact': 'CRITICAL', 'cat': 'fed'},
    {'date': '2026-04-29', 'event': 'FOMC', 'impact': 'CRITICAL', 'cat': 'fed'},
    {'date': '2026-06-17', 'event': 'FOMC', 'impact': 'CRITICAL', 'cat': 'fed'},
    {'date': '2026-07-29', 'event': 'FOMC', 'impact': 'CRITICAL', 'cat': 'fed'},
    {'date': '2026-09-16', 'event': 'FOMC', 'impact': 'CRITICAL', 'cat': 'fed'},
    {'date': '2026-11-04', 'event': 'FOMC', 'impact': 'CRITICAL', 'cat': 'fed'},
    {'date': '2026-12-16', 'event': 'FOMC', 'impact': 'CRITICAL', 'cat': 'fed'},
    # CPI
    {'date': '2026-01-14', 'event': 'CPI',  'impact': 'HIGH',     'cat': 'inflation'},
    {'date': '2026-02-11', 'event': 'CPI',  'impact': 'HIGH',     'cat': 'inflation'},
    {'date': '2026-03-11', 'event': 'CPI',  'impact': 'HIGH',     'cat': 'inflation'},
    {'date': '2026-04-10', 'event': 'CPI',  'impact': 'HIGH',     'cat': 'inflation'},
    {'date': '2026-05-13', 'event': 'CPI',  'impact': 'HIGH',     'cat': 'inflation'},
    {'date': '2026-06-10', 'event': 'CPI',  'impact': 'HIGH',     'cat': 'inflation'},
    {'date': '2026-07-15', 'event': 'CPI',  'impact': 'HIGH',     'cat': 'inflation'},
    {'date': '2026-08-12', 'event': 'CPI',  'impact': 'HIGH',     'cat': 'inflation'},
    {'date': '2026-09-09', 'event': 'CPI',  'impact': 'HIGH',     'cat': 'inflation'},
    {'date': '2026-10-14', 'event': 'CPI',  'impact': 'HIGH',     'cat': 'inflation'},
    {'date': '2026-11-12', 'event': 'CPI',  'impact': 'HIGH',     'cat': 'inflation'},
    {'date': '2026-12-09', 'event': 'CPI',  'impact': 'HIGH',     'cat': 'inflation'},
    # 非农 NFP（每月第一个周五）
    {'date': '2026-01-02', 'event': 'NFP',  'impact': 'HIGH',     'cat': 'employment'},
    {'date': '2026-02-06', 'event': 'NFP',  'impact': 'HIGH',     'cat': 'employment'},
    {'date': '2026-03-06', 'event': 'NFP',  'impact': 'HIGH',     'cat': 'employment'},
    {'date': '2026-04-03', 'event': 'NFP',  'impact': 'HIGH',     'cat': 'employment'},
    {'date': '2026-05-01', 'event': 'NFP',  'impact': 'HIGH',     'cat': 'employment'},
    {'date': '2026-06-05', 'event': 'NFP',  'impact': 'HIGH',     'cat': 'employment'},
    {'date': '2026-07-10', 'event': 'NFP',  'impact': 'HIGH',     'cat': 'employment'},
    {'date': '2026-08-07', 'event': 'NFP',  'impact': 'HIGH',     'cat': 'employment'},
    {'date': '2026-09-04', 'event': 'NFP',  'impact': 'HIGH',     'cat': 'employment'},
    {'date': '2026-10-02', 'event': 'NFP',  'impact': 'HIGH',     'cat': 'employment'},
    {'date': '2026-11-06', 'event': 'NFP',  'impact': 'HIGH',     'cat': 'employment'},
    {'date': '2026-12-04', 'event': 'NFP',  'impact': 'HIGH',     'cat': 'employment'},
    # PCE（每月月末）
    {'date': '2026-01-30', 'event': 'PCE',  'impact': 'MED',      'cat': 'inflation'},
    {'date': '2026-02-27', 'event': 'PCE',  'impact': 'MED',      'cat': 'inflation'},
    {'date': '2026-03-27', 'event': 'PCE',  'impact': 'MED',      'cat': 'inflation'},
    {'date': '2026-04-30', 'event': 'PCE',  'impact': 'MED',      'cat': 'inflation'},
    {'date': '2026-05-29', 'event': 'PCE',  'impact': 'MED',      'cat': 'inflation'},
    {'date': '2026-06-26', 'event': 'PCE',  'impact': 'MED',      'cat': 'inflation'},
    {'date': '2026-07-31', 'event': 'PCE',  'impact': 'MED',      'cat': 'inflation'},
    {'date': '2026-08-28', 'event': 'PCE',  'impact': 'MED',      'cat': 'inflation'},
    {'date': '2026-09-25', 'event': 'PCE',  'impact': 'MED',      'cat': 'inflation'},
]


def _load_cache() -> dict:
    try:
        if CACHE_FILE.exists():
            c = json.loads(CACHE_FILE.read_text())
            if time.time() - c.get('ts', 0) < CACHE_TTL:
                return c
    except Exception:
        pass
    return {}


def _save_cache(data: dict):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({**data, 'ts': time.time()}))
    except Exception:
        pass


def _get_fng() -> dict:
    """恐贪指数"""
    cached = _load_cache()
    if cached.get('fng'):
        return cached['fng']
    try:
        r = requests.get('https://api.alternative.me/fng/?limit=1', timeout=6).json()
        val = int(r['data'][0]['value'])
        label = r['data'][0]['value_classification']
        result = {'value': val, 'label': label}
        _save_cache({'fng': result})
        return result
    except Exception:
        return {'value': 50, 'label': 'Neutral'}


def _get_btc_dominance() -> float:
    """BTC主导率"""
    try:
        r = requests.get('https://api.coingecko.com/api/v3/global', timeout=6).json()
        return round(r['data']['market_cap_percentage']['btc'], 1)
    except Exception:
        return 0.0


def get_upcoming_events(days_ahead: int = 7) -> list:
    """返回未来N天内的重要事件"""
    now = datetime.now(timezone.utc).date()
    upcoming = []
    for ev in MACRO_EVENTS_2026:
        ev_date = datetime.strptime(ev['date'], '%Y-%m-%d').date()
        days_to = (ev_date - now).days
        if -1 <= days_to <= days_ahead:   # 包含昨天（可能今天还有影响）
            upcoming.append({
                **ev,
                'days_to': days_to,
                'today': days_to == 0,
                'tomorrow': days_to == 1,
            })
    upcoming.sort(key=lambda x: x['days_to'])
    return upcoming


def get_active_risk() -> dict:
    """
    返回当前宏观风险状态
    供 brahma_core extra_data['macro_calendar'] 使用
    """
    upcoming = get_upcoming_events(7)
    fng = _get_fng()
    btc_dom = _get_btc_dominance()

    # 风险等级
    risk_score = 0
    risk_events = []

    for ev in upcoming:
        days = ev.get('days_to', 99)
        impact = ev.get('impact', 'LOW')
        if impact == 'CRITICAL':
            if days <= 1:
                risk_score -= 15
            elif days <= 3:
                risk_score -= 10
            elif days <= 7:
                risk_score -= 5
        elif impact == 'HIGH':
            if days <= 1:
                risk_score -= 8
            elif days <= 3:
                risk_score -= 5
            elif days <= 7:
                risk_score -= 3
        elif impact == 'MED':
            if days <= 1:
                risk_score -= 4
        if abs(risk_score) >= 3:
            risk_events.append(f"{ev['event']} T{days:+d}d")

    # F&G极端值
    fng_val = fng.get('value', 50)
    fng_note = ''
    if fng_val <= 20:
        risk_score += 5   # 极恐→做多有利
        fng_note = f'极恐({fng_val})→反弹信号'
    elif fng_val >= 80:
        risk_score -= 5   # 极贪→做空有利
        fng_note = f'极贪({fng_val})→风险偏高'

    risk_score = max(-20, min(5, risk_score))

    return {
        'score': risk_score,
        'total': risk_score,
        'upcoming_events': upcoming[:5],
        'risk_events': risk_events,
        'event': upcoming[0] if upcoming else {},
        'fng': fng,
        'btc_dominance': btc_dom,
        'fng_note': fng_note,
        'summary': f'宏观风险={risk_score} | FNG={fng_val}({fng.get("label","")}) | BTC.D={btc_dom}% | 近期事件={",".join(risk_events) or "无"}',
        'available': True,
        'ts': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
    }

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/macro_engine.py ══
#!/usr/bin/env python3
# ponytail: macro_engine 418行，独立计算引擎，功能内聚，拆分条件: 单引擎>3000行且有完整测试
"""
# ╔══ INTERFACE CONTRACT ════════════════════════════════════════════╗
# 入口: get_macro_score(symbol) -> dict
# 输出: {score, fear_greed, btc_dominance, source, detail}
# 依赖: 公开API (alternative.me / coinglass)
# 设计院封印 2026-07-02
# ╚═══════════════════════════════════════════════════════════════════╝
╔══════════════════════════════════════════════════════════════════╗
║  梵天大脑 · macro_engine.py  · P2d 宏观量化引擎                  ║
║  数据源：免费公开 API（无需KEY）                                  ║
║  覆盖：恐惧贪婪指数 / 比特币主导率 / 市场资金体量                  ║
║  评分贡献：宏观环境 0~10分                                        ║
╚══════════════════════════════════════════════════════════════════╝
"""
import urllib.request, json, time

_cache: dict = {}
_TTL = 300  # 5分钟

def _get(url: str, headers: dict = None):
    now = time.time()
    if url in _cache and now - _cache[url]['ts'] < _TTL:
        return _cache[url]['data']
    try:
        h = {'User-Agent': 'brahma/4.0'}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read())
            _cache[url] = {'ts': now, 'data': data}
            return data
    except Exception:
        return None


def get_fear_greed() -> dict:
    """Alternative.me 恐惧贪婪指数（免费）"""
    data = _get("https://api.alternative.me/fng/?limit=3&format=json")
    if not data or 'data' not in data:
        return {'value': 50, 'label': 'Neutral', 'trend': 'FLAT'}

    vals = [int(d['value']) for d in data['data'][:3]]
    current = vals[0]
    prev    = vals[-1]

    if current <= 20:
        label = 'Extreme Fear'
    elif current <= 40:
        label = 'Fear'
    elif current <= 60:
        label = 'Neutral'
    elif current <= 80:
        label = 'Greed'
    else:
        label = 'Extreme Greed'

    trend = 'RISING' if current > prev + 5 else ('FALLING' if current < prev - 5 else 'FLAT')

    return {'value': current, 'label': label, 'trend': trend, 'prev': prev}


def get_btc_dominance() -> dict:
    """
    BTC主导率（从 CoinGecko 公开 /global 端点）
    BTC主导率高→资金集中BTC，山寨币弱
    BTC主导率低→资金外溢山寨，高弹性交易机会
    """
    data = _get("https://api.coingecko.com/api/v3/global")
    if not data:
        return {'btc_dom': 50.0, 'signal': 'NEUTRAL'}

    btc_dom = data.get('data', {}).get('market_cap_percentage', {}).get('btc', 50.0)

    if btc_dom > 58:
        signal = 'BTC_DOMINANT'   # 避险模式，山寨弱
    elif btc_dom < 45:
        signal = 'ALTCOIN_SEASON'  # 山寨季，高β品种机会多
    else:
        signal = 'BALANCED'

    return {'btc_dom': round(btc_dom, 2), 'signal': signal}


def macro_score(symbol: str, signal_dir: str, fg_data: dict = None) -> dict:
    """
    宏观评分 0~10分
    恐惧贪婪指数配合信号方向 + BTC主导率环境

    哲学：极度恐惧 + 做多 = 最佳宏观环境（机构抄底时机）
          极度贪婪 + 做空 = 最佳宏观环境（顶部做空时机）
    """
    is_long = signal_dir in ('LONG', '做多')
    is_btc  = 'BTC' in symbol.upper()

    fg  = fg_data if fg_data else get_fear_greed()
    dom = get_btc_dominance()

    fv = fg['value']
    s_fg = 0
    fg_note = ''

    if is_long:
        if fv <= 20:   s_fg = 10; fg_note = f'极度恐惧({fv})→底部抄多✅'
        elif fv <= 35: s_fg = 7;  fg_note = f'恐惧({fv})→做多友好'
        elif fv <= 50: s_fg = 5;  fg_note = f'中性({fv})'
        elif fv <= 70: s_fg = 3;  fg_note = f'贪婪({fv})→上方有阻力'
        else:          s_fg = 1;  fg_note = f'极度贪婪({fv})→顶部风险高'
    else:
        if fv >= 80:   s_fg = 10; fg_note = f'极度贪婪({fv})→顶部做空✅'
        elif fv >= 65: s_fg = 7;  fg_note = f'贪婪({fv})→做空友好'
        elif fv >= 50: s_fg = 5;  fg_note = f'中性({fv})'
        elif fv >= 35: s_fg = 3;  fg_note = f'恐惧({fv})→下方有支撑'
        else:          s_fg = 1;  fg_note = f'极度恐惧({fv})→反弹风险高'

    # BTC主导率调整（影响山寨机会）
    dom_bonus = 0
    dom_note  = ''
    if not is_btc:
        ds = dom['signal']
        if is_long and ds == 'ALTCOIN_SEASON':
            dom_bonus = 2; dom_note = f'山寨季({dom["btc_dom"]:.0f}%BTC)→山寨强'
        elif not is_long and ds == 'BTC_DOMINANT':
            dom_bonus = 1; dom_note = f'BTC主导({dom["btc_dom"]:.0f}%)→山寨弱'

    total = min(s_fg + dom_bonus, 10)
    notes = [fg_note]
    if dom_note:
        notes.append(dom_note)

    return {
        'score':  total,
        'notes':  notes,
        'raw': {
            'fear_greed':    fg,
            'btc_dominance': dom,
        }
    }


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    dr  = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'
    r = macro_score(sym, dr)
    print(f"\n=== 宏观引擎 · {sym} {dr} ===")
    print(f"宏观总分: {r['score']}/10")
    for n in r['notes']:
        print(f"  {n}")
    fg = r['raw']['fear_greed']
    dom = r['raw']['btc_dominance']
    print(f"恐惧贪婪: {fg['value']} {fg['label']}  趋势:{fg['trend']}")
    print(f"BTC主导率: {dom['btc_dom']}%  {dom['signal']}")


# ═══════════════════════════════════════════════════════════════
# [s_macro_v2 2026-07-01] DXY实时 + BTC.D精准加权
# 设计院·四方共识落地：全要素宏观层升级
# ═══════════════════════════════════════════════════════════════

def get_dxy_realtime() -> dict:
    """
    DXY 美元指数实时（Yahoo Finance /v8，免费）
    返回：price, chg_1h_pct, chg_24h_pct, direction
    """
    default = {'price': 0.0, 'chg_1h_pct': 0.0, 'chg_24h_pct': 0.0, 'direction': 'NEUTRAL'}
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1h&range=2d'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
        closes = [x for x in closes if x is not None]
        if len(closes) < 2:
            return default
        now_p  = closes[-1]
        prev1h = closes[-2]
        prev24 = closes[-25] if len(closes) >= 25 else closes[0]
        chg1h  = round((now_p - prev1h) / prev1h * 100, 3)
        chg24  = round((now_p - prev24) / prev24 * 100, 3)
        direction = 'UP' if chg1h > 0.05 else ('DOWN' if chg1h < -0.05 else 'FLAT')
        return {'price': round(now_p, 2), 'chg_1h_pct': chg1h, 'chg_24h_pct': chg24, 'direction': direction}
    except Exception:
        return default


def get_nasdaq_realtime() -> dict:
    """
    纳指期货 NQ=F 实时（Yahoo Finance，免费）
    BTC与纳指相关系数≈0.7，宏观共振确认
    """
    default = {'price': 0.0, 'chg_1h_pct': 0.0, 'direction': 'NEUTRAL'}
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/NQ=F?interval=1h&range=2d'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
        closes = [x for x in closes if x is not None]
        if len(closes) < 2:
            return default
        chg = round((closes[-1] - closes[-2]) / closes[-2] * 100, 3)
        direction = 'UP' if chg > 0.1 else ('DOWN' if chg < -0.1 else 'FLAT')
        return {'price': round(closes[-1], 0), 'chg_1h_pct': chg, 'direction': direction}
    except Exception:
        return default


def macro_score_v2(symbol: str, signal_dir: str) -> dict:
    """
    全要素宏观评分 v2（在原 macro_score 基础上叠加）

    新增维度：
      DXY实时方向（Yahoo Finance）
      纳指期货方向（Yahoo Finance）
      BTC.D精准阈值加权（CoinGecko已有，精细化）

    返回：
      score_addon   : 新增宏观加分（叠加到原 macro_score 上）
      dxy           : DXY数据
      nasdaq        : 纳指数据
      notes         : 描述列表
    """
    is_short = (signal_dir == 'SHORT')
    is_btc   = ('BTC' in symbol.upper())
    score    = 0
    notes    = []

    # ── DXY 实时 ──
    dxy = get_dxy_realtime()
    if dxy['price'] > 0:
        if is_short and dxy['direction'] == 'UP':
            pts = 3 if abs(dxy['chg_1h_pct']) >= 0.15 else 2
            score += pts; notes.append(f'DXY={dxy["price"]:.2f}(+{dxy["chg_1h_pct"]:.2f}%) 美元走强→加密承压 +{pts}')
        elif is_short and dxy['direction'] == 'DOWN':
            score -= 1; notes.append(f'DXY={dxy["price"]:.2f}({dxy["chg_1h_pct"]:.2f}%) 美元走弱→做空逆风 -1')
        elif not is_short and dxy['direction'] == 'DOWN':
            pts = 2
            score += pts; notes.append(f'DXY={dxy["price"]:.2f}({dxy["chg_1h_pct"]:.2f}%) 美元走弱→加密利好 +{pts}')

    # ── 纳指期货 ──
    nq = get_nasdaq_realtime()
    if nq['price'] > 0:
        if is_short and nq['direction'] == 'DOWN':
            score += 2; notes.append(f'NQ={nq["price"]:.0f}({nq["chg_1h_pct"]:.2f}%) 纳指下跌→BTC共振 +2')
        elif is_short and nq['direction'] == 'UP':
            score -= 1; notes.append(f'NQ={nq["price"]:.0f}({nq["chg_1h_pct"]:.2f}%) 纳指上涨→做空逆风 -1')
        elif not is_short and nq['direction'] == 'UP':
            score += 2; notes.append(f'NQ={nq["price"]:.0f}({nq["chg_1h_pct"]:.2f}%) 纳指上涨→BTC利好 +2')

    # ── BTC.D 精准加权（山寨做空叠加）──
    if not is_btc and is_short:
        try:
            dom_data = get_btc_dominance()
            btc_d = dom_data.get('btc_dom', 0)
            if btc_d >= 56:
                score += 4; notes.append(f'BTC.D={btc_d:.1f}% 高位吸血→山寨更弱 +4')
            elif btc_d >= 53:
                score += 2; notes.append(f'BTC.D={btc_d:.1f}% 偏高→山寨承压 +2')
        except Exception:
            pass

    return {
        'score_addon': min(max(score, -3), 6),  # 限制范围 -3~+6
        'dxy':    dxy,
        'nasdaq': nq,
        'notes':  notes,
    }


# ══════════════════════════════════════════════════════════════════
# [P2 设计院 2026-07-13] macro_state.json 定时写入
# 将宏观数据快照持久化，供 brahma_engine / 全景报告读取
# ══════════════════════════════════════════════════════════════════

def write_macro_state() -> dict:
    """
    写入 macro_state.json 宏观快照
    调用: python3 -c "from brahma_brain.macro_engine import write_macro_state; write_macro_state()"
    """
    import json, time
    from pathlib import Path
    from datetime import datetime, timezone

    BASE_DIR = Path(__file__).parent.parent
    OUT_PATH = BASE_DIR / 'data' / 'macro_state.json'

    snap = {'ts': datetime.now(timezone.utc).isoformat(), 'epoch': time.time()}

    # DXY — 字段映射修正 (2026-07-14 科学落地)
    try:
        dxy = get_dxy_realtime()
        direction = dxy.get('direction', 'FLAT')
        chg1h     = dxy.get('chg_1h_pct', 0.0)
        chg24     = dxy.get('chg_24h_pct', 0.0)
        # 方向性评分：DXY涨→加密承压(-3)，DXY跌→加密利好(+3)
        dxy_score = -3 if direction == 'UP' else (3 if direction == 'DOWN' else 0)
        snap['dxy'] = {
            'value':  dxy.get('price'),
            'change': chg24,
            'chg_1h': chg1h,
            'signal': direction,
            'score':  dxy_score,
        }
    except Exception as e:
        snap['dxy'] = {'error': str(e)}

    # BTC.D
    try:
        import urllib.request
        _r = json.loads(urllib.request.urlopen(
            'https://api.coingecko.com/api/v3/global', timeout=8).read())
        btcd = _r.get('data', {}).get('market_cap_percentage', {}).get('btc', 0)
        snap['btc_dominance'] = round(btcd, 2)
        snap['btc_d_signal']  = 'BTC_STRONG' if btcd > 55 else ('ALT_SEASON' if btcd < 48 else 'NEUTRAL')
    except Exception as e:
        snap['btc_dominance'] = None
        snap['btc_d_error']   = str(e)

    # Fear & Greed
    try:
        _fg = json.loads(urllib.request.urlopen(
            'https://api.alternative.me/fng/?limit=1', timeout=6).read())
        fng_val = int(_fg['data'][0]['value'])
        fng_lbl = _fg['data'][0]['value_classification']
        snap['fear_greed'] = {'value': fng_val, 'label': fng_lbl}
        snap['fng_score']  = 10 if fng_val < 20 else (5 if fng_val < 30 else (
                            -5 if fng_val > 75 else (-10 if fng_val > 85 else 0)))
    except Exception as e:
        snap['fear_greed'] = {'error': str(e)}
        snap['fng_score']  = 0

    # NQ期货（纳指，BTC相关系数≈0.7）+ QQQ vs MA20
    # [2026-08-11 设计院封印] 为 macro_link TRADFI门控提供数据
    try:
        nq = get_nasdaq_realtime()
        nq_chg = nq.get('chg_1h_pct', 0.0)
        nq_price = nq.get('price', 0.0)
        # QQQ实时（Yahoo Finance，免费，不用额外依赖）
        import urllib.request as _ur
        _qqq_url = 'https://query1.finance.yahoo.com/v8/finance/chart/QQQ?interval=1d&range=30d'
        _req = _ur.Request(_qqq_url, headers={'User-Agent': 'Mozilla/5.0'})
        with _ur.urlopen(_req, timeout=8) as _rp:
            _qd = __import__('json').loads(_rp.read())
        _qqq_closes = [x for x in _qd['chart']['result'][0]['indicators']['quote'][0]['close'] if x]
        qqq_price = _qqq_closes[-1]
        qqq_ma20  = sum(_qqq_closes[-20:]) / min(20, len(_qqq_closes))
        qqq_vs_ma = (qqq_price - qqq_ma20) / qqq_ma20 * 100  # % 偏离MA20
        # SPX日变化（用ES=F期货代替）
        _spx_url = 'https://query1.finance.yahoo.com/v8/finance/chart/ES=F?interval=1d&range=3d'
        _req2 = _ur.Request(_spx_url, headers={'User-Agent': 'Mozilla/5.0'})
        with _ur.urlopen(_req2, timeout=8) as _rp2:
            _sd = __import__('json').loads(_rp2.read())
        _spx_cls = [x for x in _sd['chart']['result'][0]['indicators']['quote'][0]['close'] if x]
        spx_chg_1d = (_spx_cls[-1] - _spx_cls[-2]) / _spx_cls[-2] * 100 if len(_spx_cls) >= 2 else 0.0
        snap['nq']  = {'price': round(nq_price, 0), 'chg_1h_pct': round(nq_chg, 2)}
        snap['qqq'] = {
            'price':    round(qqq_price, 2),
            'ma20':     round(qqq_ma20, 2),
            'vs_ma20_pct': round(qqq_vs_ma, 2),    # 正数=在MA20上方
            'above_ma20': qqq_price > qqq_ma20,
        }
        snap['spx'] = {'chg_1d_pct': round(spx_chg_1d, 2)}  # 日涨跌%
        # [2026-08-11 设计院修复] spx_chg_1d=0时用^GSPC现货补充（ES=F非交易时段返回空）
        if spx_chg_1d == 0.0:
            try:
                _gspc_url = 'https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?interval=1d&range=3d'
                _req3 = _ur.Request(_gspc_url, headers={'User-Agent': 'Mozilla/5.0'})
                with _ur.urlopen(_req3, timeout=8) as _rp3:
                    _gd = __import__('json').loads(_rp3.read())
                _gspc_cls = [x for x in _gd['chart']['result'][0]['indicators']['quote'][0]['close'] if x]
                spx_chg_1d = (_gspc_cls[-1] - _gspc_cls[-2]) / _gspc_cls[-2] * 100 if len(_gspc_cls) >= 2 else 0.0
                snap['spx'] = {'chg_1d_pct': round(spx_chg_1d, 2), 'source': 'GSPC'}
            except Exception:
                pass
    except Exception as _nq_e:
        snap['nq']  = {'error': str(_nq_e)}
        snap['qqq'] = {}
        snap['spx'] = {}

    # 宏观综合分
    macro_score = (snap.get('dxy', {}).get('score', 0) or 0)
    macro_score += (snap.get('fng_score', 0) or 0)
    # DXY FLAT + 极度恐慌 → 加密反弹窗口 +2（低波动环境下做多机会）
    dxy_sig = snap.get('dxy', {}).get('signal', '')
    fng_val = snap.get('fear_greed', {}).get('value', 50)
    if isinstance(fng_val, int) and dxy_sig == 'FLAT':
        if fng_val < 25:
            macro_score += 2
            snap['macro_note'] = 'DXY平稳+极度恐慌→反弹窗口 +2'
        elif fng_val < 35:
            macro_score += 1
            snap['macro_note'] = 'DXY平稳+恐慌→轻度反弹信号 +1'
    # BTC.D > 55% 山寨承压额外记录
    btc_d = snap.get('btc_dominance', 0) or 0
    if btc_d > 55:
        macro_score -= 2
        snap.setdefault('macro_note', '')
        snap['macro_note'] += f' BTC.D={btc_d:.1f}%高位吸血-2'
    snap['macro_score'] = int(macro_score)
    snap['macro_bias']  = 'RISK_ON' if macro_score > 5 else ('RISK_OFF' if macro_score < -5 else 'NEUTRAL')

    try:
        OUT_PATH.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        snap['write_error'] = str(e)

    return snap


if __name__ == '__main__':
    result = write_macro_state()
    print(f"macro_state写入完成: score={result.get('macro_score')} bias={result.get('macro_bias')}")
    print(f"  DXY: {result.get('dxy')}")
    print(f"  BTC.D: {result.get('btc_dominance')}%  {result.get('btc_d_signal')}")
    print(f"  F&G: {result.get('fear_greed')}")