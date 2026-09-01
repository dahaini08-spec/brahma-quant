"""
us_session_gate.py — 美股时段门控
设计院自主决策 · 2026-07-22

职责：
  1. 判断当前美股交易时段（overnight/premarket/regular/postmarket）
  2. 结合梵天体制，返回时段修正delta
  3. 检查宏观事件日历（macro_calendar.py），重大事件日封禁信号

时段表（UTC）：
  Overnight  : 01:00 - 09:00 UTC
  Pre-market : 09:00 - 14:30 UTC
  Open window: 14:00 - 14:45 UTC  ← 高风险禁仓窗口
  Regular    : 14:30 - 21:00 UTC
  After-hours: 21:00 - 01:00 UTC

体制×时段修正矩阵：
              | 开盘窗口 | 收盘窗口 | 盘前   | 深夜   |
  BEAR_TREND  |  空+3   |  空+3   |  +0   |  +0   |
  BULL_TREND  |  多+3   |  多+3   |  +0   |  +0   |
  CHOP_MID    |  -10    |  -5     |  -5   |  -5   |
"""

from datetime import datetime, timezone
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# ── 时段分界（分钟，UTC）────────────────────────────────────────────────────
_OVERNIGHT_START  = 1 * 60       # 01:00
_PREMARKET_START  = 9 * 60       # 09:00
_OPEN_WINDOW_S    = 14 * 60      # 14:00  ← 禁仓窗口开始
_REGULAR_START    = 14 * 60 + 30 # 14:30
_OPEN_WINDOW_E    = 14 * 60 + 45 # 14:45  ← 禁仓窗口结束
_CLOSE_WINDOW_S   = 20 * 60 + 45 # 20:45
_REGULAR_END      = 21 * 60      # 21:00
_AFTERHOURS_END   = 25 * 60      # 01:00 next day


def get_us_session() -> dict:
    """
    返回当前美股时段信息。
    
    Returns:
        {
          'session': 'overnight'|'premarket'|'open_window'|'regular'|'postmarket',
          'is_high_risk': bool,       ← True=开盘/收盘冲击波
          'is_overnight': bool,
          'is_regular': bool,
          'utc_min': int,             ← 当前UTC分钟数
        }
    """
    now = datetime.now(timezone.utc)
    # 处理跨日：将 00:00~01:00 视为前一天的 24:00~25:00
    h, m = now.hour, now.minute
    utc_min = h * 60 + m
    if utc_min < _OVERNIGHT_START:
        utc_min += 24 * 60  # 00:00~01:00 → 24:00~25:00

    session = 'overnight'
    is_high_risk = False

    if utc_min < _OVERNIGHT_START or utc_min >= _AFTERHOURS_END:
        session = 'overnight'
    elif _OVERNIGHT_START <= utc_min < _PREMARKET_START:
        session = 'overnight'
    elif _PREMARKET_START <= utc_min < _OPEN_WINDOW_S:
        session = 'premarket'
    elif _OPEN_WINDOW_S <= utc_min < _OPEN_WINDOW_E:
        session = 'open_window'
        is_high_risk = True
    elif _OPEN_WINDOW_E <= utc_min < _CLOSE_WINDOW_S:
        session = 'regular'
    elif _CLOSE_WINDOW_S <= utc_min < _REGULAR_END:
        session = 'close_window'
        is_high_risk = True
    else:
        session = 'postmarket'

    return {
        'session': session,
        'is_high_risk': is_high_risk,
        'is_overnight': session == 'overnight',
        'is_regular': session in ('regular',),
        'is_open_window': session == 'open_window',
        'utc_min': utc_min % (24 * 60),
    }


def check_macro_event_block() -> Optional[str]:
    """
    检查今日是否为重大宏观事件日（FOMC/CPI/非农）。
    在事件前后2小时内封禁所有信号。
    
    Returns:
        None = 无封禁
        str  = 封禁原因
    """
    try:
        from brahma_brain.narrative_engine import get_upcoming_events
        events = get_upcoming_events(hours_ahead=2)
        critical = [e for e in events if e.get('impact') == 'CRITICAL']
        if critical:
            evt = critical[0]
            return f"宏观事件封禁: {evt.get('event','')} ({evt.get('date','')})"
    except Exception:
        pass
    return None


def get_session_regime_delta(session_info: dict, regime: str, direction: str) -> dict:
    """
    根据时段×体制×方向，返回评分修正delta。
    
    仅在 Phase B/C 时使用（Phase A 不修改score）。
    当前设计院决策：Phase A 模式，delta仅作标签，不注入score。
    
    Returns:
        {'delta': int, 'note': str, 'block': bool, 'block_reason': str}
    """
    sess = session_info.get('session', 'overnight')
    result = {'delta': 0, 'note': '', 'block': False, 'block_reason': ''}

    # ── 宏观事件封禁优先 ──────────────────────────────────────────────
    macro_block = check_macro_event_block()
    if macro_block:
        result['block'] = True
        result['block_reason'] = macro_block
        return result

    # ── 开盘冲击波窗口（最高风险，硬性封禁新仓）──────────────────────
    if sess == 'open_window':
        result['block'] = True
        result['block_reason'] = '美股开盘冲击波窗口 14:00-14:45 UTC 禁止新仓'
        return result

    # ── 体制×时段修正矩阵 ─────────────────────────────────────────────
    if sess == 'close_window':
        if 'CHOP' in regime:
            result['delta'] = -5
            result['note'] = f'CHOP收盘窗口-5'
        elif regime == 'BEAR_TREND' and direction == 'SHORT':
            result['delta'] = +3
            result['note'] = f'BEAR_TREND收盘空单+3'
        elif regime == 'BULL_TREND' and direction == 'LONG':
            result['delta'] = +3
            result['note'] = f'BULL_TREND收盘多单+3'

    elif sess == 'premarket':
        if 'CHOP' in regime:
            result['delta'] = -5
            result['note'] = f'CHOP盘前-5'

    elif sess == 'overnight':
        if 'CHOP' in regime:
            result['delta'] = -5
            result['note'] = f'CHOP深夜-5'

    return result
