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
