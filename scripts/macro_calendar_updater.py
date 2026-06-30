#!/usr/bin/env python3
"""
macro_calendar_updater.py — 宏观日历自动更新器 v1.0
设计院封印 · 2026-06-29

职责：
  每周一凌晨自动补录下一周宏观事件
  数据源：Investing.com Economic Calendar (公开JSON)
  零成本，无需API Key

运行方式：
  python3 scripts/macro_calendar_updater.py
  → 自动追加未来4周内未录入的事件
  → 若请求失败，静默退出（HEARTBEAT_OK）
"""
import json, time, urllib.request, urllib.parse, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE      = Path(__file__).parent.parent
CAL_PATH  = BASE / 'data' / 'macro_calendar.json'

# 高影响力事件关键词 → impact级别
IMPACT_MAP = {
    'FOMC':            ('FOMC', 'EXTREME', 4, 2),
    'Federal':         ('FOMC', 'EXTREME', 4, 2),
    'Non-Farm':        ('NFP',  'EXTREME', 2, 1),
    'Nonfarm':         ('NFP',  'EXTREME', 2, 1),
    'CPI':             ('CPI',  'HIGH',    2, 1),
    'Consumer Price':  ('CPI',  'HIGH',    2, 1),
    'PCE':             ('PCE',  'HIGH',    1, 1),
    'Personal Consum': ('PCE',  'HIGH',    1, 1),
    'GDP':             ('GDP',  'HIGH',    1, 1),
    'PPI':             ('PPI',  'MEDIUM',  1, 1),
    'Retail Sales':    ('RETAIL','MEDIUM', 1, 1),
    'ISM':             ('ISM',  'MEDIUM',  1, 1),
    'Jackson Hole':    ('FOMC', 'EXTREME', 6, 3),
}

def _fetch_investing_calendar(from_date: str, to_date: str) -> list:
    """从Investing.com抓取经济日历（公开JSON接口）"""
    url = (
        'https://economic-calendar.tradingview.com/events'
        f'?from={from_date}T00%3A00%3A00.000Z'
        f'&to={to_date}T23%3A59%3A59.000Z'
        '&countries=us'
        '&importance=3'      # 3=HIGH以上
    )
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read()).get('result', [])
    except Exception:
        return []


def _load_calendar() -> list:
    if CAL_PATH.exists():
        try:
            d = json.loads(CAL_PATH.read_text())
            return d if isinstance(d, list) else []
        except Exception:
            return []
    return []


def run():
    """主逻辑：补录未来4周宏观事件"""
    now   = datetime.now(timezone.utc)
    end   = now + timedelta(weeks=4)
    from_s = now.strftime('%Y-%m-%d')
    to_s   = end.strftime('%Y-%m-%d')

    existing = _load_calendar()
    existing_ts = {e['ts_utc'] for e in existing}

    # 拉取TradingView经济日历（美国，高影响）
    events_raw = _fetch_investing_calendar(from_s, to_s)
    added = 0

    for ev in events_raw:
        title   = ev.get('title', '') or ev.get('name', '')
        ts_utc  = ev.get('date', '') or ev.get('time', '')
        # 标准化ts_utc格式
        if not ts_utc:
            continue
        ts_utc = ts_utc.replace(' ', 'T')
        if not ts_utc.endswith('Z') and '+' not in ts_utc:
            ts_utc += 'Z'

        if ts_utc in existing_ts:
            continue

        # 匹配关键词
        matched = None
        for kw, (etype, impact, win_b, win_a) in IMPACT_MAP.items():
            if kw.lower() in title.lower():
                matched = (etype, impact, win_b, win_a)
                break
        if not matched:
            continue

        etype, impact, win_b, win_a = matched
        existing.append({
            'name':            etype,
            'type':            etype,
            'impact':          impact,
            'ts_utc':          ts_utc,
            'window_before_h': win_b,
            'window_after_h':  win_a,
            'title':           title[:80],
            'auto_added':      True,
        })
        existing_ts.add(ts_utc)
        added += 1

    if added > 0:
        existing.sort(key=lambda x: x.get('ts_utc', ''))
        CAL_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2))
        print(f'[MacroCal] 自动补录 +{added}个事件 → 总计{len(existing)}个')
        for e in existing:
            if e.get('auto_added') and e['ts_utc'] > now.isoformat()[:10]:
                print(f'  {e["name"]:<8} {e["ts_utc"]} impact={e["impact"]}')
    else:
        print(f'[MacroCal] 无新增事件（已有{len(existing)}个，覆盖至{to_s}）')

    # 未来事件计数验证
    future = [e for e in existing if e.get('ts_utc', '') > now.isoformat()[:19]]
    print(f'[MacroCal] 未来事件: {len(future)}个')
    return added


if __name__ == '__main__':
    result = run()
    if result == 0:
        print('HEARTBEAT_OK')
