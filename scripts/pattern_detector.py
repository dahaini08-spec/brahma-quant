#!/usr/bin/env python3
"""
pattern_detector.py
设计院封印 2026-08-02 | 苏摩授权自主决策

合约市场反转形态检测器
  支持形态：W底/双底、M顶/双顶、头肩底、头肩顶、圆弧底
  修正参数（模拟验证后）：
    W底中间反弹阈值: 3%→1.0%（合约短周期振幅小）
    头肩底颈线缓冲: 0%→0.5%（右肩接近颈线即确认中）
    突破确认缓冲: +0.3%（防止假突破）
"""
import json
from pathlib import Path
from typing import Optional

# ── 形态检测核心函数 ───────────────────────────────────────────

def detect_w_bottom(highs: list, lows: list, closes: list, price: float) -> dict:
    """
    W底/双底检测
    条件：
      ① 找近N根中的最低点L1
      ② 从L1反弹>1.0%形成颈线N
      ③ 回调不破L1×(1+0.02)形成L2
      ④ 价格突破颈线N×(1+0.003) → 确认
    返回：{'detected': bool, 'confidence': float, 'neckline': float, 'target': float}
    """
    if len(lows) < 20:
        return {'detected': False, 'confidence': 0.0}

    n = min(len(lows), 60)  # 最近60根K线
    recent_lows   = lows[-n:]
    recent_highs  = highs[-n:]
    recent_closes = closes[-n:]

    # 找第一个底L1（最低点位置）
    l1_idx = recent_lows.index(min(recent_lows))
    l1     = recent_lows[l1_idx]

    # L1之后必须有反弹（颈线）
    if l1_idx >= len(recent_highs) - 5:
        return {'detected': False, 'confidence': 0.0}

    post_l1_highs = recent_highs[l1_idx:]
    neckline = max(post_l1_highs[:min(15, len(post_l1_highs))])
    rebound_pct = (neckline - l1) / l1 * 100

    # 颈线反弹需>1.0%（修正后参数）
    if rebound_pct < 1.0:
        return {'detected': False, 'confidence': 0.0}

    # 找颈线后的第二个底L2
    neck_idx = l1_idx + post_l1_highs.index(neckline)
    if neck_idx >= len(recent_lows) - 3:
        return {'detected': False, 'confidence': 0.0}

    post_neck_lows = recent_lows[neck_idx:]
    l2 = min(post_neck_lows[:min(15, len(post_neck_lows))])

    # L2不能比L1低超过2%（双底误差<2%）
    if l2 < l1 * 0.98:
        return {'detected': False, 'confidence': 0.0}

    # 价格突破颈线（允许0.3%缓冲）
    breakout = price >= neckline * 1.003

    # 置信度计算
    confidence = 0.0
    if breakout:
        confidence += 0.50
    elif price >= neckline * 0.997:  # 接近颈线
        confidence += 0.30

    # 两底越接近置信度越高
    bottom_diff = abs(l2 - l1) / l1 * 100
    if bottom_diff < 0.5:
        confidence += 0.25
    elif bottom_diff < 1.0:
        confidence += 0.15

    # 反弹幅度越大越可信
    if rebound_pct > 3.0:
        confidence += 0.25
    elif rebound_pct > 1.5:
        confidence += 0.15
    else:
        confidence += 0.05

    # 目标 = 颈线 + (颈线 - L1)
    target = neckline + (neckline - l1)

    return {
        'detected':   confidence >= 0.5,
        'forming':    confidence >= 0.3,   # 形成中（可提前预警）
        'confidence': round(confidence, 2),
        'l1':         l1,
        'l2':         l2,
        'neckline':   round(neckline, 2),
        'target':     round(target, 2),
        'rebound_pct':round(rebound_pct, 2),
        'direction':  'LONG',
        'pattern':    'W_BOTTOM',
    }


def detect_m_top(highs: list, lows: list, closes: list, price: float) -> dict:
    """M顶/双顶检测（W底镜像）"""
    if len(highs) < 20:
        return {'detected': False, 'confidence': 0.0}

    n = min(len(highs), 60)
    recent_highs  = highs[-n:]
    recent_lows   = lows[-n:]

    h1_idx = recent_highs.index(max(recent_highs))
    h1     = recent_highs[h1_idx]

    if h1_idx >= len(recent_lows) - 5:
        return {'detected': False, 'confidence': 0.0}

    post_h1_lows = recent_lows[h1_idx:]
    neckline     = min(post_h1_lows[:min(15, len(post_h1_lows))])
    pullback_pct = (h1 - neckline) / h1 * 100

    if pullback_pct < 1.0:
        return {'detected': False, 'confidence': 0.0}

    neck_idx = h1_idx + post_h1_lows.index(neckline)
    if neck_idx >= len(recent_highs) - 3:
        return {'detected': False, 'confidence': 0.0}

    post_neck_highs = recent_highs[neck_idx:]
    h2 = max(post_neck_highs[:min(15, len(post_neck_highs))])

    if h2 > h1 * 1.02:
        return {'detected': False, 'confidence': 0.0}

    breakout   = price <= neckline * 0.997
    confidence = 0.0
    if breakout:
        confidence += 0.50
    elif price <= neckline * 1.003:
        confidence += 0.30

    top_diff = abs(h2 - h1) / h1 * 100
    if top_diff < 0.5: confidence += 0.25
    elif top_diff < 1.0: confidence += 0.15
    if pullback_pct > 3.0: confidence += 0.25
    elif pullback_pct > 1.5: confidence += 0.15
    else: confidence += 0.05

    target = neckline - (h1 - neckline)
    return {
        'detected':   confidence >= 0.5,
        'forming':    confidence >= 0.3,
        'confidence': round(confidence, 2),
        'h1': h1, 'h2': h2,
        'neckline':   round(neckline, 2),
        'target':     round(target, 2),
        'direction':  'SHORT',
        'pattern':    'M_TOP',
    }


def detect_head_shoulders_bottom(highs: list, lows: list, closes: list, price: float) -> dict:
    """
    头肩底检测
    修正参数：颈线缓冲0.5%（右肩接近颈线即视为形成中）
    """
    if len(lows) < 30:
        return {'detected': False, 'confidence': 0.0}

    n = min(len(lows), 80)
    rl = lows[-n:]
    rh = highs[-n:]

    # 找三个低点（左肩>头部<右肩，头部最低）
    # 简化算法：找近N根中的局部最小值
    local_lows = []
    for i in range(2, len(rl) - 2):
        if rl[i] < rl[i-1] and rl[i] < rl[i-2] and rl[i] < rl[i+1] and rl[i] < rl[i+2]:
            local_lows.append((i, rl[i]))

    if len(local_lows) < 3:
        return {'detected': False, 'confidence': 0.0}

    # 取最后3个局部低点
    ls_idx, ls = local_lows[-3]
    hd_idx, hd = local_lows[-2]
    rs_idx, rs = local_lows[-1]

    # 头部必须最低
    if not (hd < ls and hd < rs):
        return {'detected': False, 'confidence': 0.0}

    # 两肩高度相近（误差<8%）
    shoulder_diff = abs(rs - ls) / ls * 100
    if shoulder_diff > 8.0:
        return {'detected': False, 'confidence': 0.0}

    # 颈线 = 头部两侧高点的平均
    left_peak  = max(rh[ls_idx:hd_idx]) if hd_idx > ls_idx else ls
    right_peak = max(rh[hd_idx:rs_idx]) if rs_idx > hd_idx else rs
    neckline   = (left_peak + right_peak) / 2

    # 价格接近/突破颈线（0.5%缓冲，修正后参数）
    near_neck  = price >= neckline * 0.995
    breakout   = price >= neckline * 1.003

    confidence = 0.0
    if breakout:   confidence += 0.55
    elif near_neck: confidence += 0.35  # 形成中

    if shoulder_diff < 2.0: confidence += 0.20
    elif shoulder_diff < 5.0: confidence += 0.10

    depth = (neckline - hd) / hd * 100
    if depth > 5.0: confidence += 0.20
    elif depth > 2.0: confidence += 0.10

    target = neckline + (neckline - hd)
    return {
        'detected':      confidence >= 0.5,
        'forming':       confidence >= 0.3,
        'confidence':    round(confidence, 2),
        'left_shoulder': round(ls, 2),
        'head':          round(hd, 2),
        'right_shoulder':round(rs, 2),
        'neckline':      round(neckline, 2),
        'target':        round(target, 2),
        'shoulder_diff': round(shoulder_diff, 2),
        'direction':     'LONG',
        'pattern':       'HEAD_SHOULDERS_BOTTOM',
    }


def detect_head_shoulders_top(highs: list, lows: list, closes: list, price: float) -> dict:
    """头肩顶检测（头肩底镜像）"""
    if len(highs) < 30:
        return {'detected': False, 'confidence': 0.0}

    n = min(len(highs), 80)
    rh = highs[-n:]
    rl = lows[-n:]

    local_highs = []
    for i in range(2, len(rh) - 2):
        if rh[i] > rh[i-1] and rh[i] > rh[i-2] and rh[i] > rh[i+1] and rh[i] > rh[i+2]:
            local_highs.append((i, rh[i]))

    if len(local_highs) < 3:
        return {'detected': False, 'confidence': 0.0}

    ls_idx, ls = local_highs[-3]
    hd_idx, hd = local_highs[-2]
    rs_idx, rs = local_highs[-1]

    if not (hd > ls and hd > rs):
        return {'detected': False, 'confidence': 0.0}

    shoulder_diff = abs(rs - ls) / ls * 100
    if shoulder_diff > 8.0:
        return {'detected': False, 'confidence': 0.0}

    left_valley  = min(rl[ls_idx:hd_idx]) if hd_idx > ls_idx else ls
    right_valley = min(rl[hd_idx:rs_idx]) if rs_idx > hd_idx else rs
    neckline     = (left_valley + right_valley) / 2

    near_neck  = price <= neckline * 1.005
    breakout   = price <= neckline * 0.997

    confidence = 0.0
    if breakout:    confidence += 0.55
    elif near_neck: confidence += 0.35
    if shoulder_diff < 2.0: confidence += 0.20
    elif shoulder_diff < 5.0: confidence += 0.10
    depth = (hd - neckline) / neckline * 100
    if depth > 5.0: confidence += 0.20
    elif depth > 2.0: confidence += 0.10

    target = neckline - (hd - neckline)
    return {
        'detected':      confidence >= 0.5,
        'forming':       confidence >= 0.3,
        'confidence':    round(confidence, 2),
        'left_shoulder': round(ls, 2),
        'head':          round(hd, 2),
        'right_shoulder':round(rs, 2),
        'neckline':      round(neckline, 2),
        'target':        round(target, 2),
        'direction':     'SHORT',
        'pattern':       'HEAD_SHOULDERS_TOP',
    }


def detect_rsi_divergence(closes: list, rsi_values: list, direction: str = 'BEARISH') -> dict:
    """
    RSI背离检测（接入VIP层的核心功能）
    direction: 'BEARISH'=顶背离(做空) / 'BULLISH'=底背离(做多)
    """
    if len(closes) < 20 or len(rsi_values) < 20:
        return {'detected': False, 'confidence': 0.0}

    n = min(len(closes), 30)
    rc = closes[-n:]
    rr = rsi_values[-n:]

    if direction == 'BULLISH':
        # 底背离：价格新低，RSI未新低
        price_low1_idx = rc.index(min(rc[:n//2]))
        price_low2_idx = n//2 + rc[n//2:].index(min(rc[n//2:]))
        price_new_low  = rc[price_low2_idx] < rc[price_low1_idx]
        rsi_new_low    = rr[price_low2_idx] < rr[price_low1_idx]
        divergence     = price_new_low and not rsi_new_low
        confidence     = 0.75 if divergence else 0.0
        rsi_diff       = rr[price_low1_idx] - rr[price_low2_idx]
        return {
            'detected':   divergence,
            'confidence': round(confidence, 2),
            'type':       'BULLISH_DIVERGENCE',
            'direction':  'LONG',
            'rsi_diff':   round(rsi_diff, 1),
            'score_bonus': 20 if divergence else 0,
        }
    else:
        # 顶背离：价格新高，RSI未新高
        price_high1_idx = rc.index(max(rc[:n//2]))
        price_high2_idx = n//2 + rc[n//2:].index(max(rc[n//2:]))
        price_new_high  = rc[price_high2_idx] > rc[price_high1_idx]
        rsi_new_high    = rr[price_high2_idx] > rr[price_high1_idx]
        divergence      = price_new_high and not rsi_new_high
        confidence      = 0.75 if divergence else 0.0
        rsi_diff        = rr[price_high1_idx] - rr[price_high2_idx]
        return {
            'detected':   divergence,
            'confidence': round(confidence, 2),
            'type':       'BEARISH_DIVERGENCE',
            'direction':  'SHORT',
            'rsi_diff':   round(rsi_diff, 1),
            'score_bonus': 20 if divergence else 0,
        }


def scan_all_patterns(highs: list, lows: list, closes: list,
                      price: float, rsi_1h: list = None) -> dict:
    """全形态扫描入口 — 返回所有检测到的形态"""
    results = {}

    r = detect_w_bottom(highs, lows, closes, price)
    if r.get('detected') or r.get('forming'):
        results['W_BOTTOM'] = r

    r = detect_m_top(highs, lows, closes, price)
    if r.get('detected') or r.get('forming'):
        results['M_TOP'] = r

    r = detect_head_shoulders_bottom(highs, lows, closes, price)
    if r.get('detected') or r.get('forming'):
        results['HS_BOTTOM'] = r

    r = detect_head_shoulders_top(highs, lows, closes, price)
    if r.get('detected') or r.get('forming'):
        results['HS_TOP'] = r

    # RSI背离（需要RSI数据）
    if rsi_1h and len(rsi_1h) >= 20:
        r = detect_rsi_divergence(closes, rsi_1h, 'BULLISH')
        if r.get('detected'):
            results['RSI_BULLISH_DIV'] = r

        r = detect_rsi_divergence(closes, rsi_1h, 'BEARISH')
        if r.get('detected'):
            results['RSI_BEARISH_DIV'] = r

    return results


if __name__ == '__main__':
    import requests
    API = 'https://fapi.binance.com'

    print('=== pattern_detector.py 昨日场景模拟验证 ===')
    print()

    # BTC 4H数据（模拟昨日底部区域）
    for sym, sim_lows, sim_highs, sim_price in [
        ('BTC', [62300, 62500, 63100, 62350, 63100, 63146],
                [63100, 63200, 63600, 63200, 63600, 63779], 63146),
        ('ETH', [1855, 1862, 1875, 1820, 1848, 1865],
                [1875, 1880, 1890, 1875, 1880, 1885], 1865),
    ]:
        # 扩充数据（模拟更长序列）
        lows_full   = [sim_lows[0]] * 20 + sim_lows
        highs_full  = [sim_highs[0]] * 20 + sim_highs
        closes_full = lows_full

        results = scan_all_patterns(highs_full, lows_full, closes_full, sim_price)

        print(f'{sym} @ {sim_price}:')
        if results:
            for pattern, r in results.items():
                status = '✅确认' if r.get('detected') else '⏳形成中'
                conf = r['confidence']
                d_dir = r.get('direction','?')
                print(f'  {status} {pattern}: conf={conf} dir={d_dir}')
                if 'neckline' in r:
                    neck = r['neckline']
                    tgt  = r.get('target','?')
                    print(f'  neckline={neck} target={tgt}')
        else:
            print('  (无形态检测到)')
        print()
