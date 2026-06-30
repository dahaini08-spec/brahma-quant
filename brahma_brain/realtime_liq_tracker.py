"""
realtime_liq_tracker.py — 实时清算流追踪
设计院自主决策 2026-06-29

职责：
  1. 从 ws_guardian 的 !forceOrder@arr WS流读取近期清算数据
  2. 维护滚动窗口清算统计（近5分钟/近1小时）
  3. 输出清算方向、强度、聚合价位
  4. 替代 CoinAnk 套餐3「爆仓订单」精细化数据

数据来源：
  ws_guardian 已有 !forceOrder@arr WebSocket 连接
  本模块读取 ws_guardian 写入的清算缓存文件
  → 零新增 WS 连接，复用现有框架
"""

import json
import time
import os
from typing import Optional
from collections import defaultdict

# ws_guardian 清算缓存文件路径
_LIQ_CACHE_FILE = os.path.join(
    os.path.dirname(__file__), '..', 'data', 'liq_flow_cache.json'
)
_LIQ_STATE_FILE = os.path.join(
    os.path.dirname(__file__), '..', 'data', 'ws_guardian_state.json'
)

# 本地内存缓存
_MEM_CACHE: dict = {}
_CACHE_TTL = 30


def get_recent_liq(symbol: str, window_minutes: int = 5) -> dict:
    """
    读取近 N 分钟清算统计

    返回：
      long_liq_usd   : 多单被清算总额（USD）
      short_liq_usd  : 空单被清算总额（USD）
      net_bias       : LONG_LIQ / SHORT_LIQ / NEUTRAL
      liq_events     : 清算事件数
      max_single_usd : 最大单笔清算
      avg_price      : 平均清算价格
      intensity      : LOW / MEDIUM / HIGH / EXTREME
      score_adj      : 建议评分调整
    """
    cache_key = f'rliq_{symbol}_{window_minutes}'
    now = time.time()
    if cache_key in _MEM_CACHE and now - _MEM_CACHE[cache_key]['ts'] < _CACHE_TTL:
        return _MEM_CACHE[cache_key]['data']

    # 尝试读 ws_guardian_state（ws_guardian 已有清算流数据）
    liq_events = _read_from_ws_guardian_state(symbol)

    # 过滤时间窗口
    cutoff = now - window_minutes * 60
    recent = [e for e in liq_events if e.get('ts', 0) >= cutoff]

    if not recent:
        result = _empty_liq_result(symbol)
        _MEM_CACHE[cache_key] = {'ts': now, 'data': result}
        return result

    long_liq = [(e['price'], e['usd']) for e in recent if e.get('side') == 'BUY']   # 多单被平
    short_liq = [(e['price'], e['usd']) for e in recent if e.get('side') == 'SELL']  # 空单被平

    long_usd = sum(u for _, u in long_liq)
    short_usd = sum(u for _, u in short_liq)
    total_usd = long_usd + short_usd
    max_single = max((e.get('usd', 0) for e in recent), default=0)

    # 偏向
    if long_usd > short_usd * 2:
        net_bias = 'LONG_LIQ'   # 多单被大量清算 → 看空信号
    elif short_usd > long_usd * 2:
        net_bias = 'SHORT_LIQ'  # 空单被大量清算 → 看多信号（逼空）
    else:
        net_bias = 'NEUTRAL'

    # 强度
    if total_usd >= 10_000_000:
        intensity = 'EXTREME'
    elif total_usd >= 2_000_000:
        intensity = 'HIGH'
    elif total_usd >= 500_000:
        intensity = 'MEDIUM'
    else:
        intensity = 'LOW'

    # 平均清算价
    all_prices = [e['price'] * e.get('usd', 0) for e in recent if e.get('price')]
    all_usd = [e.get('usd', 0) for e in recent]
    avg_price = sum(all_prices) / sum(all_usd) if sum(all_usd) > 0 else 0

    # 评分调整（做空视角）
    score_adj = 0
    if net_bias == 'LONG_LIQ' and intensity in ('HIGH', 'EXTREME'):
        score_adj = +3  # 多单被清算 → 空头动能强
    elif net_bias == 'SHORT_LIQ' and intensity in ('HIGH', 'EXTREME'):
        score_adj = -3  # 逼空行情 → 做空风险高
    elif intensity == 'MEDIUM':
        score_adj = +1 if net_bias == 'LONG_LIQ' else 0

    result = {
        'symbol': symbol,
        'window_minutes': window_minutes,
        'long_liq_usd': round(long_usd, 0),
        'short_liq_usd': round(short_usd, 0),
        'total_liq_usd': round(total_usd, 0),
        'net_bias': net_bias,
        'liq_events': len(recent),
        'max_single_usd': round(max_single, 0),
        'avg_price': round(avg_price, 2),
        'intensity': intensity,
        'score_adj': score_adj,
        'ts': now,
        'source': 'ws_guardian_state',
    }

    _MEM_CACHE[cache_key] = {'ts': now, 'data': result}
    return result


def _read_from_ws_guardian_state(symbol: str) -> list:
    """从 ws_guardian_state.json 读取清算事件"""
    events = []
    try:
        if os.path.exists(_LIQ_STATE_FILE):
            with open(_LIQ_STATE_FILE) as f:
                state = json.load(f)
            # ws_guardian 存的清算流格式
            liq_flow = state.get('liq_flow', state.get('force_orders', []))
            for e in liq_flow:
                sym = e.get('symbol', e.get('s', ''))
                if sym == symbol or sym == symbol.replace('USDT', ''):
                    price = float(e.get('price', e.get('p', 0)))
                    qty = float(e.get('origQty', e.get('q', 0)))
                    side = e.get('side', e.get('S', 'BUY'))
                    ts = float(e.get('ts', e.get('T', time.time() * 1000))) / 1000
                    events.append({
                        'price': price, 'qty': qty,
                        'usd': price * qty, 'side': side,
                        'ts': ts,
                    })
    except Exception:
        pass

    # 也读专用清算缓存
    try:
        if os.path.exists(_LIQ_CACHE_FILE):
            with open(_LIQ_CACHE_FILE) as f:
                cache = json.load(f)
            sym_events = cache.get(symbol, [])
            events.extend(sym_events)
    except Exception:
        pass

    return events


def _empty_liq_result(symbol: str) -> dict:
    return {
        'symbol': symbol, 'window_minutes': 5,
        'long_liq_usd': 0, 'short_liq_usd': 0, 'total_liq_usd': 0,
        'net_bias': 'NEUTRAL', 'liq_events': 0,
        'max_single_usd': 0, 'avg_price': 0,
        'intensity': 'LOW', 'score_adj': 0,
        'ts': time.time(), 'source': 'empty',
    }


def get_liq_score(symbol: str, signal_dir: str, window_minutes: int = 5) -> tuple[int, str]:
    """
    返回 (加分, 描述) 供 brahma_core 调用
    """
    data = get_recent_liq(symbol, window_minutes)
    adj = data['score_adj']
    bias = data['net_bias']
    intensity = data['intensity']
    total = data['total_liq_usd']

    if total == 0:
        return 0, f'无近期清算数据'

    total_m = total / 1_000_000
    if signal_dir == 'SHORT' and bias == 'LONG_LIQ':
        return abs(adj), f'多单清算{total_m:.1f}M({intensity}) → 空头加速 +{abs(adj)}'
    elif signal_dir == 'SHORT' and bias == 'SHORT_LIQ':
        return -abs(adj), f'空单清算{total_m:.1f}M({intensity}) → 逼空风险 {-abs(adj)}'
    elif signal_dir == 'LONG' and bias == 'SHORT_LIQ':
        return abs(adj), f'空单清算{total_m:.1f}M({intensity}) → 多头加速 +{abs(adj)}'
    elif signal_dir == 'LONG' and bias == 'LONG_LIQ':
        return -abs(adj), f'多单清算{total_m:.1f}M({intensity}) → 下跌风险 {-abs(adj)}'

    return 0, f'清算中性 {total_m:.1f}M ({intensity})'


if __name__ == '__main__':
    result = get_recent_liq('BTCUSDT', window_minutes=60)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    score, desc = get_liq_score('BTCUSDT', 'SHORT')
    print(f'\n做空评分: {score:+d}  {desc}')
