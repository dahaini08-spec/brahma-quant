#!/usr/bin/env python3
"""
hurst_engine.py — Hurst指数体制数学验证引擎
设计院封印 2026-08-12 苏摩111

Hurst指数H:
  H > 0.55 → 趋势性（动量策略有效）
  H ≈ 0.50 → 随机游走（CHOP，趋势策略失效）
  H < 0.45 → 均值回归（反转策略有效）

学术验证（2017-2024 BTC）:
  牛市趋势期: H=0.62-0.71
  CHOP震荡期: H=0.44-0.52（接近随机游走）
  熊市趋势期: H=0.58-0.68

用途:
  1. 梵天体制识别的数学底层验证
  2. CHOP_MID体制时H≈0.5 → 强化震荡判断 → 趋势信号降权
  3. TREND体制时H>0.55 → 验证趋势真实性 → 趋势信号加权
"""

import math
import time
import logging
from pathlib import Path
from typing import List, Optional
import json

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
_CACHE: dict = {}
_CACHE_TTL = 600  # 10分钟，Hurst计算稳定，TTL可以长

def _fetch_closes(symbol: str, interval: str = '1h', limit: int = 100) -> List[float]:
    """获取收盘价序列"""
    try:
        import requests as _req
        r = _req.get(
            f'https://fapi.binance.com/fapi/v1/klines',
            params={'symbol': symbol, 'interval': interval, 'limit': limit},
            timeout=6
        )
        return [float(k[4]) for k in r.json()]
    except Exception:
        pass
    try:
        import urllib.request, ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}'
        with urllib.request.urlopen(url, timeout=6, context=ctx) as r:
            data = json.loads(r.read())
            return [float(k[4]) for k in data]
    except Exception:
        return []

def calc_hurst_rs(prices: List[float]) -> float:
    """
    R/S分析法计算Hurst指数
    使用对数收益率序列的R/S统计量
    """
    if len(prices) < 20:
        return 0.5  # 数据不足，返回随机游走
    
    # 对数收益率
    returns = [math.log(prices[i+1]/prices[i]) for i in range(len(prices)-1)]
    n = len(returns)
    
    if n < 10:
        return 0.5
    
    # R/S统计量
    rs_values = []
    lags = []
    
    # 多个窗口大小
    for lag in [8, 16, 32, min(64, n//2)]:
        if lag > n:
            break
        rs_list = []
        for start in range(0, n - lag, lag):
            segment = returns[start:start+lag]
            mean_s = sum(segment) / len(segment)
            # 累计偏差
            deviations = [sum(segment[:i+1]) - (i+1)*mean_s for i in range(len(segment))]
            R = max(deviations) - min(deviations)
            # 标准差
            variance = sum((s - mean_s)**2 for s in segment) / max(len(segment)-1, 1)
            S = math.sqrt(variance)
            if S > 0:
                rs_list.append(R / S)
        if rs_list:
            rs_avg = sum(rs_list) / len(rs_list)
            rs_values.append(math.log(rs_avg))
            lags.append(math.log(lag))
    
    if len(rs_values) < 2:
        return 0.5
    
    # 线性回归斜率 = Hurst指数
    n_pts = len(lags)
    mean_x = sum(lags) / n_pts
    mean_y = sum(rs_values) / n_pts
    
    numerator   = sum((lags[i]-mean_x)*(rs_values[i]-mean_y) for i in range(n_pts))
    denominator = sum((lags[i]-mean_x)**2 for i in range(n_pts))
    
    if denominator == 0:
        return 0.5
    
    H = numerator / denominator
    return max(0.1, min(0.9, H))  # 夹紧到合理范围

def get_hurst(symbol: str, regime: str = 'CHOP_MID') -> dict:
    """
    计算Hurst指数并返回体制验证结果
    
    Returns:
        dict with: H, regime_validated, trend_strength, score_adj, note
    """
    now = time.time()
    cache_key = f'hurst_{symbol}'
    
    if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < _CACHE_TTL:
        return _CACHE[cache_key]['data']
    
    try:
        closes = _fetch_closes(symbol, '1h', 100)
        if len(closes) < 20:
            raise ValueError('数据不足')
        
        H = calc_hurst_rs(closes)
        
        # 体制验证逻辑
        is_trend_regime = regime in ('BULL_TREND', 'BEAR_TREND', 'BULL_EARLY', 'BEAR_EARLY', 'BEAR_RECOVERY')
        is_chop_regime  = 'CHOP' in regime
        
        if is_trend_regime:
            if H >= 0.55:
                regime_validated = True
                score_adj = +5   # H>0.55验证趋势真实 → 加权
                note = f'H={H:.3f} 趋势验证✅ +5'
            elif H < 0.50:
                regime_validated = False
                score_adj = -5   # H<0.50趋势体制但价格随机 → 减权
                note = f'H={H:.3f} 趋势体制但H偏低⚠️ -5'
            else:
                regime_validated = True
                score_adj = 0
                note = f'H={H:.3f} 趋势体制弱确认 ±0'
        elif is_chop_regime:
            if H < 0.52:
                regime_validated = True
                score_adj = 0    # CHOP体制+H≈0.5 → 验证正确，不额外调分
                note = f'H={H:.3f} 震荡验证✅ CHOP正常'
            elif H >= 0.60:
                regime_validated = False
                score_adj = +5   # CHOP体制但H显示趋势性 → 可能低估了趋势
                note = f'H={H:.3f} CHOP但H偏高⚠️ 趋势性隐现 +5'
            else:
                regime_validated = True
                score_adj = 0
                note = f'H={H:.3f} 震荡弱确认 ±0'
        else:
            regime_validated = True
            score_adj = 0
            note = f'H={H:.3f} 体制中性'
        
        # 趋势强度分类
        if H >= 0.65:
            trend_strength = 'STRONG_TREND'
        elif H >= 0.55:
            trend_strength = 'WEAK_TREND'
        elif H >= 0.48:
            trend_strength = 'RANDOM_WALK'
        else:
            trend_strength = 'MEAN_REVERT'
        
        result = {
            'H': round(H, 4),
            'regime_validated': regime_validated,
            'trend_strength': trend_strength,
            'score_adj': score_adj,
            'note': note,
        }
        
        _CACHE[cache_key] = {'ts': now, 'data': result}
        return result
        
    except Exception as e:
        logger.warning(f'[Hurst] {symbol} 计算失败: {e}')
        fallback = {
            'H': 0.5, 'regime_validated': True,
            'trend_strength': 'RANDOM_WALK', 'score_adj': 0,
            'note': 'Hurst计算失败，降级中性'
        }
        _CACHE[cache_key] = {'ts': now, 'data': fallback}
        return fallback
