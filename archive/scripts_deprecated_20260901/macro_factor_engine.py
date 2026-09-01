#!/usr/bin/env python3
"""
macro_factor_engine.py — 第18维度：宏观因子引擎
设计院 梵天v20.0 · 2026-06-07

数据源（全免费）：
  - F&G指数：alternative.me/fng
  - BTC主导率：CoinGecko /global
  - FOMC日历：硬编码2026全年（每季更新）
  - 宏观体制：BEAR_TREND×极恐 = 空单加分

评分范围：-15 ~ +15
注入brahma_brain第18维度（extra_data['macro']）

用法：
  from macro_factor_engine import get_macro_score
  score, report = get_macro_score(direction='SHORT', regime='BEAR_TREND')
"""

import json, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

CACHE_FILE = Path(__file__).parent.parent / 'data' / 'macro_cache.json'
CACHE_TTL  = 1800  # 30min

# FOMC 2026全年（UTC日期，决议公布当天）
FOMC_DATES_2026 = [
    '2026-01-29', '2026-03-18', '2026-04-29',
    '2026-06-17', '2026-07-29', '2026-09-16',
    '2026-11-04', '2026-12-16',
]
# CPI发布日（近似，每月第2周三）
CPI_DATES_2026 = [
    '2026-01-14', '2026-02-11', '2026-03-11',
    '2026-04-10', '2026-05-13', '2026-06-10',
    '2026-07-15', '2026-08-12', '2026-09-09',
    '2026-10-14', '2026-11-12', '2026-12-09',
]


def _fetch(url: str, timeout: int = 6):
    try:
        return json.loads(urllib.request.urlopen(url, timeout=timeout).read())
    except Exception:
        return {}


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
        data['ts'] = time.time()
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def get_fg_index() -> tuple[int, str]:
    """返回 (fg_value:0-100, fg_label)"""
    cache = _load_cache()
    if cache.get('fg_value') is not None:
        return cache['fg_value'], cache['fg_label']

    data = _fetch('https://api.alternative.me/fng/?limit=1')
    try:
        v   = int(data['data'][0]['value'])
        lbl = data['data'][0]['value_classification']
        _save_cache({'fg_value': v, 'fg_label': lbl})
        return v, lbl
    except Exception:
        return 50, 'UNKNOWN'


def get_btc_dominance() -> float:
    """返回BTC主导率（%）"""
    cache = _load_cache()
    if cache.get('btc_dom'):
        return cache['btc_dom']

    data = _fetch('https://api.coingecko.com/api/v3/global')
    try:
        dom = float(data['data']['market_cap_percentage'].get('btc', 50))
        _save_cache({'btc_dom': dom})
        return dom
    except Exception:
        return 50.0


def get_event_window() -> dict:
    """
    检查当前是否在重大事件窗口期
    返回 {'in_window': bool, 'event': str, 'days_to': int, 'action': str}
    """
    now = datetime.now(timezone.utc)
    all_events = (
        [(d, 'FOMC') for d in FOMC_DATES_2026] +
        [(d, 'CPI')  for d in CPI_DATES_2026]
    )

    for date_str, event_type in sorted(all_events):
        event_dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        diff_days = (event_dt - now).days
        diff_h    = (event_dt - now).total_seconds() / 3600

        if -1 <= diff_days <= 2:  # 事件前后窗口
            if diff_h < -24:
                continue  # 已过去超过24H，跳过
            action = 'SUSPEND' if event_type == 'FOMC' else 'REDUCE'
            return {
                'in_window': True,
                'event': event_type,
                'event_date': date_str,
                'days_to': diff_days,
                'hours_to': round(diff_h, 1),
                'action': action,
            }

    # 找最近未来事件
    future = [(d, t) for d, t in all_events
              if datetime.strptime(d, '%Y-%m-%d').replace(tzinfo=timezone.utc) > now]
    if future:
        future.sort()
        next_d, next_t = future[0]
        next_dt = datetime.strptime(next_d, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        diff = (next_dt - now).days
        return {
            'in_window': False,
            'event': next_t,
            'event_date': next_d,
            'days_to': diff,
            'hours_to': round((next_dt - now).total_seconds()/3600, 1),
            'action': 'NORMAL',
        }
    return {'in_window': False, 'event': 'NONE', 'days_to': 99, 'action': 'NORMAL'}


def get_macro_score(
    direction: str = 'SHORT',
    regime: str = 'BEAR_TREND',
    verbose: bool = False,
) -> tuple[float, dict]:
    """
    第18维度宏观因子评分
    范围：-15 ~ +15
    正值 = 顺势加分，负值 = 逆势扣分
    """
    is_long  = 'LONG'  in direction.upper() or '多' in direction
    is_short = 'SHORT' in direction.upper() or '空' in direction

    fg_val, fg_lbl  = get_fg_index()
    btc_dom         = get_btc_dominance()
    event           = get_event_window()

    score     = 0.0
    details   = []
    suspended = False

    # ── 事件窗口期（最高优先级）────────────────────────────────
    if event['in_window']:
        if event['action'] == 'SUSPEND':
            score     = -15
            suspended = True
            details.append(f"🚨 {event['event']}窗口期({event['days_to']}天)→强制降分-15")
        else:  # REDUCE
            score    -= 8
            details.append(f"⚠️ {event['event']}窗口期({event['days_to']}天)→降分-8")

    # ── F&G极端情绪 ──────────────────────────────────────────
    if not suspended:
        if fg_val <= 15:   # 极度恐慌
            if is_short:
                score  += 8
                details.append(f'F&G={fg_val}极度恐慌 → 空单顺势+8')
            else:
                score  -= 8
                details.append(f'F&G={fg_val}极度恐慌 → 多单逆市-8')
        elif fg_val <= 30:  # 恐慌
            if is_short:
                score  += 4
                details.append(f'F&G={fg_val}恐慌 → 空单+4')
            else:
                score  -= 4
                details.append(f'F&G={fg_val}恐慌 → 多单-4')
        elif fg_val >= 80:  # 极度贪婪
            if is_long:
                score  += 4
                details.append(f'F&G={fg_val}极度贪婪 → 多单顺势+4')
            else:
                score  -= 6
                details.append(f'F&G={fg_val}极度贪婪 → 空单逆市-6')
        elif fg_val >= 65:  # 贪婪
            if is_long:
                score  += 3
                details.append(f'F&G={fg_val}贪婪 → 多单+3')
            else:
                score  -= 3
                details.append(f'F&G={fg_val}贪婪 → 空单-3')
        else:
            details.append(f'F&G={fg_val}({fg_lbl}) 中性 ±0')

    # ── BTC主导率宏观风险偏好 ────────────────────────────────
    if not suspended:
        if btc_dom >= 58:   # 高主导率 = 避险 = 利空山寨/利好空单
            if is_short:
                score += 3
                details.append(f'BTC主导{btc_dom:.1f}%高(避险) → 空单+3')
            else:
                score -= 2
                details.append(f'BTC主导{btc_dom:.1f}%高(避险) → 多单山寨-2')
        elif btc_dom <= 45:  # 低主导率 = 风险偏好 = 山寨季
            if is_long:
                score += 3
                details.append(f'BTC主导{btc_dom:.1f}%低(风险偏好/山寨季) → 多单+3')
            else:
                score -= 2
                details.append(f'BTC主导{btc_dom:.1f}%低 → 做空风险-2')
        else:
            details.append(f'BTC主导{btc_dom:.1f}% 中性 ±0')

    # ── 体制×宏观双重确认 ─────────────────────────────────────
    if not suspended and 'BEAR' in regime.upper() and is_short and fg_val <= 25:
        score += 4
        details.append(f'BEAR体制×恐慌情绪×做空 三重共振+4')

    # 限制范围 -15 ~ +15
    score = max(-15, min(15, round(score, 1)))

    report = {
        'score':      score,
        'fg_value':   fg_val,
        'fg_label':   fg_lbl,
        'btc_dom':    round(btc_dom, 1),
        'event':      event,
        'suspended':  suspended,
        'direction':  direction,
        'regime':     regime,
        'details':    details,
        'label':      ('🚨事件窗口' if suspended else
                       '🔴极端' if abs(score) >= 10 else
                       '🟠强' if abs(score) >= 6 else
                       '🟡中等' if abs(score) >= 3 else '🟢中性'),
    }

    if verbose:
        pass  # [静默]
        for d in details:
            print(f'  · {d}')

    return score, report


if __name__ == '__main__':
    import sys
    direction = sys.argv[1] if len(sys.argv) > 1 else 'SHORT'
    regime    = sys.argv[2] if len(sys.argv) > 2 else 'BEAR_TREND'
    score, report = get_macro_score(direction, regime, verbose=True)
    print(f'\n第18维度宏观评分: {score:+.0f}  ({report["label"]})')
    print(f'下次重要事件: {report["event"]["event"]} {report["event"]["event_date"]} ({report["event"]["days_to"]}天后)')
