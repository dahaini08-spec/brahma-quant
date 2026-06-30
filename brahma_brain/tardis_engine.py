#!/usr/bin/env python3
"""
tardis_engine.py — 梵天星枢引擎 s20 · Tardis清算墙维度
设计院 · 2026-06-09 | 星枢引擎 Phase1

职责：
  读取 tardis_cache.db 清算墙数据，为信号评分提供第20维度支撑。

评分逻辑（SHORT方向）：
  +8：入场区上方存在大空头清算墙（$2M+）→ 做空信号强烈共振
  +5：入场区下方存在大多头清算墙（$5M+）→ 踩踏加速区
  +3：整体多头清算偏重（多/空 > 3x）→ 市场偏空倾向
   0：无明显清算墙
  -5：清算墙与信号方向矛盾

接口：
  score, detail = get_tardis_score(symbol, direction, entry_lo, entry_hi)
"""

import sqlite3, time
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
DB   = BASE / 'data' / 'tardis' / 'tardis_cache.db'

# 清算墙阈值
LIQ_WALL_MAJOR  = 5_000_000   # $5M = 主要清算墙
LIQ_WALL_MINOR  = 2_000_000   # $2M = 次要清算墙
PRICE_RANGE_PCT = 0.05        # 入场区上下5%范围内视为"近距离"清算墙

def _query_liq_walls(symbol: str) -> dict:
    """从SQLite读取清算墙摘要，按价格桶聚合"""
    if not DB.exists():
        return {}
    try:
        conn = sqlite3.connect(str(DB), timeout=3)
        # 按价格分桶（BTC:1000, ETH:50, 其他:1）
        if 'BTC' in symbol:   bucket = 1000
        elif 'ETH' in symbol: bucket = 50
        elif 'SOL' in symbol: bucket = 2
        else:                  bucket = 1

        rows = conn.execute(f"""
            SELECT ROUND(price/{bucket})*{bucket} as prc,
                   SUM(CASE WHEN side='sell' THEN usd_value ELSE 0 END) as long_liq,
                   SUM(CASE WHEN side='buy'  THEN usd_value ELSE 0 END) as short_liq,
                   SUM(usd_value) as total
            FROM liquidations WHERE symbol=?
            GROUP BY prc ORDER BY total DESC
        """, (symbol,)).fetchall()

        total_long  = conn.execute("SELECT SUM(usd_value) FROM liquidations WHERE symbol=? AND side='sell'", (symbol,)).fetchone()[0] or 0
        total_short = conn.execute("SELECT SUM(usd_value) FROM liquidations WHERE symbol=? AND side='buy'",  (symbol,)).fetchone()[0] or 0

        conn.close()
        return {
            'walls': [{'price': r[0], 'long_liq': r[1], 'short_liq': r[2], 'total': r[3]} for r in rows],
            'total_long_usd':  total_long,
            'total_short_usd': total_short,
            'available': True,
        }
    except Exception as e:
        return {'available': False, 'error': str(e)}

def get_tardis_score(symbol: str, direction: str, entry_lo: float, entry_hi: float) -> tuple:
    """
    返回 (score: float, detail: str)
    direction: 'SHORT' | 'LONG'
    """
    data = _query_liq_walls(symbol)

    if not data.get('available') or not data.get('walls'):
        return 0, 'Tardis数据不可用'

    mid_price     = (entry_lo + entry_hi) / 2
    range_band    = mid_price * PRICE_RANGE_PCT
    walls         = data['walls']
    total_long    = data['total_long_usd']
    total_short   = data['total_short_usd']

    score   = 0
    reasons = []

    if direction == 'SHORT':
        # 入场区上方的空头清算墙（价格上涨会触发，对做空不利）
        above_short = sum(w['short_liq'] for w in walls if entry_hi < w['price'] <= entry_hi * 1.05)
        # 入场区下方的多头清算墙（价格下跌踩踏，对做空有利）
        below_long  = sum(w['long_liq']  for w in walls if entry_lo * 0.95 <= w['price'] < entry_lo)
        # 整体偏向
        liq_ratio   = total_long / max(total_short, 1)

        if below_long >= LIQ_WALL_MAJOR:
            score += 5
            reasons.append(f'下方多头踩踏墙${below_long/1e6:.1f}M(+5)')
        elif below_long >= LIQ_WALL_MINOR:
            score += 3
            reasons.append(f'下方多头踩踏墙${below_long/1e6:.1f}M(+3)')

        if above_short >= LIQ_WALL_MAJOR:
            score -= 3
            reasons.append(f'上方空头反弹墙${above_short/1e6:.1f}M(-3)')

        if liq_ratio >= 3:
            score += 3
            reasons.append(f'市场多/空清算比{liq_ratio:.1f}x偏多头(+3)')
        elif liq_ratio >= 1.5:
            score += 1
            reasons.append(f'多头清算偏重{liq_ratio:.1f}x(+1)')

    elif direction == 'LONG':
        above_long  = sum(w['long_liq']  for w in walls if entry_hi < w['price'] <= entry_hi * 1.05)
        below_short = sum(w['short_liq'] for w in walls if entry_lo * 0.95 <= w['price'] < entry_lo)
        liq_ratio   = total_short / max(total_long, 1)

        if below_short >= LIQ_WALL_MAJOR:
            score += 5
            reasons.append(f'下方空头踩踏墙${below_short/1e6:.1f}M(+5)')
        if above_long >= LIQ_WALL_MAJOR:
            score -= 3
            reasons.append(f'上方多头反弹墙${above_long/1e6:.1f}M(-3)')
        if liq_ratio >= 3:
            score += 3
            reasons.append(f'市场空/多清算比{liq_ratio:.1f}x偏空头(+3)')

    score = max(-10, min(10, score))
    detail = ' | '.join(reasons) if reasons else f'无显著清算墙（多${total_long/1e6:.1f}M空${total_short/1e6:.1f}M）'
    return score, detail


def get_tardis_liq_snapshot(symbol: str) -> dict:
    """供外部调用的完整清算快照（兼容 tardis_liq_layer 接口）"""
    data = _query_liq_walls(symbol)
    if not data.get('available') or not data.get('walls'):
        return {'available': False, 'source': 'no_data'}

    walls = sorted(data['walls'], key=lambda x: -x['total'])
    long_walls  = [(w['price'], w['long_liq'])  for w in walls if w['long_liq']  > LIQ_WALL_MINOR]
    short_walls = [(w['price'], w['short_liq']) for w in walls if w['short_liq'] > LIQ_WALL_MINOR]

    dom_long  = max(long_walls,  key=lambda x: x[1], default=(0, 0))
    dom_short = max(short_walls, key=lambda x: x[1], default=(0, 0))

    total_long  = data['total_long_usd']
    total_short = data['total_short_usd']
    bias = 'BEARISH' if total_long > total_short * 2 else ('BULLISH' if total_short > total_long * 2 else 'NEUTRAL')

    return {
        'available': True,
        'source': 'tardis_csv',
        'symbol': symbol,
        'long_walls':          long_walls[:5],
        'short_walls':         short_walls[:5],
        'long_total_usd':      total_long,
        'short_total_usd':     total_short,
        'long_dominant_price': dom_long[0],
        'short_dominant_price': dom_short[0],
        'bias': bias,
    }


if __name__ == '__main__':
    import sys
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else 'BTCUSDT'
    snap = get_tardis_liq_snapshot(sym)
    print(f'\n=== {sym} Tardis清算快照 ===')
    print(f'偏向: {snap.get("bias")} | 多头清算${snap.get("long_total_usd",0)/1e6:.2f}M | 空头清算${snap.get("short_total_usd",0)/1e6:.2f}M')
    print(f'主力多头清算位: ${snap.get("long_dominant_price",0):,.0f}')
    print(f'主力空头清算位: ${snap.get("short_dominant_price",0):,.0f}')
    # 测试评分
    score, detail = get_tardis_score(sym, 'SHORT', 62000, 63000)
    print(f'\nSHORT @ 62000-63000 → s20={score:+.0f} | {detail}')
