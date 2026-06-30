#!/usr/bin/env python3
"""
macro_calendar.py — 宏观事件日历引擎
Phase A-5: FOMC/NFP/CPI 时段封锁与降权

能力：
  - 关键宏观事件时间表（UTC）
  - 事件前后时间窗口风险评级
  - 评分降权：事件前2H=-10，事件中=-20，事件后1H=观察
  - 支持动态更新（手动/API）
"""
import json, os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR   = Path(__file__).parent.parent / 'data'
CALENDAR_F = DATA_DIR / 'macro_calendar.json'
DATA_DIR.mkdir(exist_ok=True)

# 2026年Q2 关键宏观事件（UTC时间）
DEFAULT_EVENTS = [
    # FOMC 会议
    {'name': 'FOMC', 'type': 'FOMC', 'impact': 'EXTREME',
     'ts_utc': '2026-06-17T18:00:00Z', 'window_before_h': 4, 'window_after_h': 2},
    {'name': 'FOMC Minutes', 'type': 'FOMC', 'impact': 'HIGH',
     'ts_utc': '2026-05-21T18:00:00Z', 'window_before_h': 2, 'window_after_h': 1},
    # NFP (Non-Farm Payroll) — 每月第一个周五 12:30 UTC
    {'name': 'NFP', 'type': 'NFP', 'impact': 'EXTREME',
     'ts_utc': '2026-06-05T12:30:00Z', 'window_before_h': 2, 'window_after_h': 1},
    {'name': 'NFP', 'type': 'NFP', 'impact': 'EXTREME',
     'ts_utc': '2026-07-03T12:30:00Z', 'window_before_h': 2, 'window_after_h': 1},
    # CPI — 月度 12:30 UTC
    {'name': 'CPI', 'type': 'CPI', 'impact': 'HIGH',
     'ts_utc': '2026-05-27T12:30:00Z', 'window_before_h': 2, 'window_after_h': 1},
    {'name': 'CPI', 'type': 'CPI', 'impact': 'HIGH',
     'ts_utc': '2026-06-10T12:30:00Z', 'window_before_h': 2, 'window_after_h': 1},
    # PCE
    {'name': 'PCE', 'type': 'PCE', 'impact': 'HIGH',
     'ts_utc': '2026-05-29T12:30:00Z', 'window_before_h': 1, 'window_after_h': 1},
]

# 降权配置
IMPACT_PENALTY = {
    'EXTREME': {'before': -15, 'during': -25, 'after': -8},
    'HIGH':    {'before': -8,  'during': -15, 'after': -4},
    'MEDIUM':  {'before': -4,  'during': -8,  'after': -2},
}

def _load_events() -> list:
    if CALENDAR_F.exists():
        try:
            return json.loads(CALENDAR_F.read_text())
        except Exception:
            pass
    # 首次运行，写入默认事件
    CALENDAR_F.write_text(json.dumps(DEFAULT_EVENTS, indent=2, ensure_ascii=False))
    return DEFAULT_EVENTS

def _parse_ts(ts_str: str) -> datetime:
    ts_str = ts_str.replace('Z', '+00:00')
    return datetime.fromisoformat(ts_str)

def get_active_risk(now: datetime = None) -> dict:
    """
    检查当前时刻是否处于宏观事件风险窗口
    返回: {active, event, phase, penalty, note}
    """
    if now is None:
        now = datetime.now(timezone.utc)

    events = _load_events()
    result = {
        'active':  False,
        'event':   None,
        'phase':   None,   # 'before' | 'during' | 'after'
        'penalty': 0,
        'note':    '',
    }

    for ev in events:
        try:
            ev_ts = _parse_ts(ev['ts_utc'])
        except Exception:
            continue

        impact        = ev.get('impact', 'MEDIUM')
        before_h      = ev.get('window_before_h', 2)
        after_h       = ev.get('window_after_h', 1)
        during_min    = 30  # 事件发布后30分钟为"中"

        before_start  = ev_ts - timedelta(hours=before_h)
        during_end    = ev_ts + timedelta(minutes=during_min)
        after_end     = ev_ts + timedelta(hours=after_h)

        penalties = IMPACT_PENALTY.get(impact, IMPACT_PENALTY['MEDIUM'])

        if before_start <= now < ev_ts:
            phase   = 'before'
            penalty = penalties['before']
        elif ev_ts <= now < during_end:
            phase   = 'during'
            penalty = penalties['during']
        elif during_end <= now < after_end:
            phase   = 'after'
            penalty = penalties['after']
        else:
            continue

        # 取影响最大的事件
        if abs(penalty) > abs(result['penalty']):
            result['active']  = True
            result['event']   = ev.get('name', '?')
            result['phase']   = phase
            result['penalty'] = penalty
            mins_to = int((ev_ts - now).total_seconds() / 60)
            if phase == 'before':
                result['note'] = f'⚠️ {ev["name"]}事件{mins_to}分钟后({ev["ts_utc"][:16]})  评分{penalty}'
            elif phase == 'during':
                result['note'] = f'🚨 {ev["name"]}正在发布!  强制降权{penalty}'
            else:
                result['note'] = f'⏸ {ev["name"]}后观察期  评分{penalty}'

    return result

def get_upcoming_events(hours: int = 48) -> list:
    """获取未来N小时内的事件"""
    now = datetime.now(timezone.utc)
    events = _load_events()
    upcoming = []
    for ev in events:
        try:
            ev_ts = _parse_ts(ev['ts_utc'])
            diff_h = (ev_ts - now).total_seconds() / 3600
            if 0 <= diff_h <= hours:
                ev_copy = dict(ev)
                ev_copy['hours_until'] = round(diff_h, 1)
                upcoming.append(ev_copy)
        except Exception:
            continue
    return sorted(upcoming, key=lambda x: x['hours_until'])

def add_event(name: str, type_: str, ts_utc: str, impact: str = 'HIGH',
              before_h: int = 2, after_h: int = 1):
    """动态添加新事件"""
    events = _load_events()
    events.append({
        'name': name, 'type': type_, 'impact': impact,
        'ts_utc': ts_utc, 'window_before_h': before_h, 'window_after_h': after_h
    })
    CALENDAR_F.write_text(json.dumps(events, indent=2, ensure_ascii=False))
    return len(events)

if __name__ == '__main__':
    risk = get_active_risk()
    if risk['active']:
        print(f'🚨 宏观风险激活: {risk["note"]}')
    else:
        print('✅ 当前无宏观事件风险')
    upcoming = get_upcoming_events(48)
    if upcoming:
        print(f'\n未来48H事件:')
        for ev in upcoming:
            print(f'  {ev["name"]:8s} [{ev["impact"]:7s}] {ev["ts_utc"][:16]} (约{ev["hours_until"]:.1f}H后)')
