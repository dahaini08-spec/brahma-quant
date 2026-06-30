#!/usr/bin/env python3
"""
news_event_guard.py — 第19维度：新闻事件黑洞保护引擎
设计院 梵天v20.0 · 2026-06-07

职责：
  监控市场重大事件，在事件前后黑洞窗口内降低信号评分
  防止在FOMC/CPI/ETH升级/交易所危机等窗口开新仓被割

数据源：
  - 宏观事件：macro_factor_engine.py（FOMC/CPI日历）
  - Binance公告：/support/announcement（定期检查）
  - 链上事件：ETH升级/BTC减半（手动维护）
  - 市场极端事件：实时检测（价格暴跌/暴涨）

评分范围：-20（黑洞封锁）~ 0（最多扣分，不加分）

用法：
  from news_event_guard import check_event_risk
  score, report = check_event_risk(symbol='ETH', direction='SHORT')
"""

import json, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

GUARD_FILE = Path(__file__).parent.parent / 'data' / 'news_event_state.json'
CACHE_TTL  = 900  # 15min

# 已知重大链上事件（手动维护，按需更新）
KNOWN_EVENTS = [
    # {'date': '2026-07-15', 'event': 'ETH_UPGRADE', 'name': 'ETH网络升级', 'window_days': 3},
    # {'date': '2028-04-20', 'event': 'BTC_HALVING',  'name': 'BTC减半',     'window_days': 14},
]

# 价格极端变动检测阈值
EXTREME_MOVE_1H  = 4.0   # 1H内变动>4% → 极端事件
EXTREME_MOVE_4H  = 8.0   # 4H内变动>8% → 极端事件


def _fetch(url, timeout=6):
    try:
        return json.loads(urllib.request.urlopen(url, timeout=timeout).read())
    except Exception:
        return {}


def _load_state() -> dict:
    try:
        if GUARD_FILE.exists():
            s = json.loads(GUARD_FILE.read_text())
            if time.time() - s.get('ts', 0) < CACHE_TTL:
                return s
    except Exception:
        pass
    return {}


def _save_state(data: dict):
    try:
        GUARD_FILE.parent.mkdir(parents=True, exist_ok=True)
        data['ts'] = time.time()
        GUARD_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


def detect_extreme_move(symbol: str) -> dict:
    """检测价格极端变动（闪崩/闪涨）"""
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'

    try:
        # 获取1H和4H K线
        k1h = _fetch(f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1h&limit=5')
        k4h = _fetch(f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=4h&limit=3')

        if not k1h or not k4h:
            return {'extreme': False}

        # 1H变动
        open_1h  = float(k1h[-1][1])
        close_1h = float(k1h[-1][4])
        move_1h  = abs(close_1h - open_1h) / open_1h * 100

        # 4H变动
        open_4h  = float(k4h[-1][1])
        close_4h = float(k4h[-1][4])
        move_4h  = abs(close_4h - open_4h) / open_4h * 100

        # 方向判断
        direction_1h = 'UP' if close_1h > open_1h else 'DOWN'
        direction_4h = 'UP' if close_4h > open_4h else 'DOWN'

        extreme = move_1h >= EXTREME_MOVE_1H or move_4h >= EXTREME_MOVE_4H

        return {
            'extreme':      extreme,
            'move_1h':      round(move_1h, 2),
            'move_4h':      round(move_4h, 2),
            'direction_1h': direction_1h,
            'direction_4h': direction_4h,
            'trigger':      '1H' if move_1h >= EXTREME_MOVE_1H else ('4H' if move_4h >= EXTREME_MOVE_4H else None),
        }
    except Exception as e:
        return {'extreme': False, 'error': str(e)}


def check_known_events() -> dict:
    """检查已知链上/宏观事件窗口"""
    now = datetime.now(timezone.utc)
    for ev in KNOWN_EVENTS:
        ev_dt = datetime.strptime(ev['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        diff  = (ev_dt - now).days
        if -1 <= diff <= ev.get('window_days', 3):
            return {'in_window': True, **ev, 'days_to': diff}
    return {'in_window': False}


def check_event_risk(
    symbol:    str   = 'BTC',
    direction: str   = 'SHORT',
    verbose:   bool  = False,
) -> tuple[float, dict]:
    """
    第19维度：新闻事件黑洞评分
    范围：-20 ~ 0（只扣分，不加分）
    """
    score   = 0.0
    details = []
    blocked = False

    # ── 1. 宏观事件窗口（FOMC/CPI）──────────────────────────
    try:
        from macro_factor_engine import get_event_window
        ev = get_event_window()
        if ev['in_window']:
            if ev['action'] == 'SUSPEND':
                score   -= 15
                blocked  = True
                details.append(f"🚨 {ev['event']}窗口期(T{ev['days_to']:+d}天) → -15封锁")
            else:
                score -= 8
                details.append(f"⚠️ {ev['event']}窗口期 → -8")
    except Exception as e:
        details.append(f'宏观日历获取失败: {e}')

    # ── 2. 价格极端变动检测 ──────────────────────────────────
    if not blocked:
        sym = symbol.upper()
        # 主要监控BTC + 该品种
        symbols_check = list({sym, 'BTC'})
        for chk_sym in symbols_check:
            em = detect_extreme_move(chk_sym)
            if em.get('extreme'):
                adj   = -12 if em['move_1h'] >= EXTREME_MOVE_1H else -8
                score += adj
                details.append(
                    f'⚡ {chk_sym} {em["trigger"]}极端变动'
                    f' {em.get("move_1h",0):.1f}%({em.get("direction_1h","?")}) → {adj}分'
                )
                if abs(score) >= 15:
                    blocked = True
                    break

    # ── 3. 已知重大事件 ──────────────────────────────────────
    if not blocked:
        kev = check_known_events()
        if kev.get('in_window'):
            score -= 10
            details.append(f"📅 {kev.get('name','重大事件')}窗口期(T{kev.get('days_to',0):+d}天) → -10")

    # ── 4. 极端F&G检测（补充：急速变化）────────────────────────
    # F&G急速下跌（>20点/天）通常伴随事件驱动大跌
    state = _load_state()
    if state.get('fg_prev') and state.get('fg_cur'):
        fg_drop = state['fg_prev'] - state['fg_cur']
        if fg_drop >= 20 and not blocked:
            score -= 6
            details.append(f'F&G急跌{fg_drop}点/天 → 市场恐慌加剧-6')

    # 限制范围
    score = max(-20, min(0, round(score, 1)))

    if not details:
        details.append('✅ 无重大事件窗口，正常交易')

    report = {
        'score':    score,
        'blocked':  blocked,
        'details':  details,
        'symbol':   symbol,
        'direction': direction,
        'label':    ('🚨封锁' if blocked else
                     '🔴高危' if score <= -10 else
                     '🟠预警' if score <= -5 else '🟢正常'),
    }

    if verbose:
        status = '🚨BLOCKED' if blocked else f'score={score:+.0f}'
        print(f'[NewsEventGuard] {symbol} {direction} {status} ({report["label"]})')
        for d in details:
            print(f'  · {d}')

    return score, report


def get_combined_guard_score(
    symbol:    str,
    direction: str,
    regime:    str = 'BEAR_TREND',
    verbose:   bool = False,
) -> tuple[float, dict]:
    """
    第18+19维度合并调用，返回总扣分
    macro_factor(±15) + news_event(≤0) = 总宏观分
    """
    try:
        from macro_factor_engine import get_macro_score
        macro_score, macro_report = get_macro_score(direction, regime, verbose=verbose)
    except Exception as e:
        macro_score, macro_report = 0, {'error': str(e)}

    news_score, news_report = check_event_risk(symbol, direction, verbose=verbose)

    total = macro_score + news_score

    return total, {
        'total':        round(total, 1),
        'macro_score':  macro_score,
        'news_score':   news_score,
        'macro_report': macro_report,
        'news_report':  news_report,
        'blocked':      news_report.get('blocked', False) or macro_report.get('suspended', False),
    }


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETH'
    direction = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'
    regime    = sys.argv[3] if len(sys.argv) > 3 else 'BEAR_TREND'

    total, combined = get_combined_guard_score(sym, direction, regime, verbose=True)
    print(f'\n第18+19维度合并: {total:+.0f}分')
    print(f'宏观={combined["macro_score"]:+.0f}  事件={combined["news_score"]:+.0f}  封锁={combined["blocked"]}')
