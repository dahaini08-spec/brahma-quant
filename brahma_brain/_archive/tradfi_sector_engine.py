"""
tradfi_sector_engine.py — 美股代币板块联动评分引擎
[设计院 2026-08-11 苏摩111封印] 整体落地，非补丁式

核心逻辑：sector_corr 1.8x 权重的实际计算实现
- 半导体组 / 科技巨头组 / ETF指数组 内部共振检测
- 当同组3个以上标的RSI<30(超卖)/RSI>70(超买) → 板块信号加分
- 单独标的信号不及板块共振信号可靠性，差异化权重

调用方：brahma_core.py → compute_tradfi_sector_score(symbol, rsi_1h_fn)
"""

from __future__ import annotations
import logging
from typing import Callable, Optional

_log = logging.getLogger(__name__)

# ─── 板块分组定义 ───────────────────────────────────────────────────────────────

_SEMICONDUCTOR = frozenset([
    'MUUSDT',      # 美光科技
    'SNDKUSDT',    # 闪迪
    'SKHYNIXUSDT', # SK海力士
    'SKHYUSDT',    # SK海力士低价合约
    'NVDAUSDT',    # 英伟达
    'AMDUSDT',     # AMD
    'TSMUSDT',     # 台积电
])

_TECH_GIANT = frozenset([
    'TSLAUSDT',    # 特斯拉
    'METAUSDT',    # Meta
    'MSFLUSDT',    # 微软（注：MSFT在Binance可能是MSFTUSDT）
    'MSFUSDT',     # 微软
    'MSTRUSDT',    # 微软（别名）
    'GOOGLUSDT',   # Google
    'GOOGUSDT',    # Google（别名）
    'AAPLUSDT',    # 苹果
    'MSTRUSDT',    # MSTR
])

_ETF_INDEX = frozenset([
    'SOXLUSDT',    # 半导体ETF 3x
    'SPCXUSDT',    # 标普500
    'QQQUSDT',     # 纳指100
])

# 组合映射：symbol → 组名 + 同组成员
_GROUP_MAP: dict[str, tuple[str, frozenset]] = {}
for _s in _SEMICONDUCTOR:
    _GROUP_MAP[_s] = ('semiconductor', _SEMICONDUCTOR)
for _s in _TECH_GIANT:
    _GROUP_MAP[_s] = ('tech_giant', _TECH_GIANT)
for _s in _ETF_INDEX:
    _GROUP_MAP[_s] = ('etf_index', _ETF_INDEX)

# ─── 评分参数 ────────────────────────────────────────────────────────────────────

_OVERSOLD_THRESH  = 30   # RSI < 30 = 超卖（做多信号增益）
_OVERBOUGHT_THRESH = 70  # RSI > 70 = 超买（做空信号增益）
_SCORE_PER_MEMBER = 5    # 每个同组超卖/超买成员 +5分
_MAX_SECTOR_SCORE = 20   # 板块联动最高+20分（4个成员x5）
_MIN_MEMBERS_FOR_BOOST = 2  # 至少2个同组成员达标才给分（单独不算板块信号）


def compute_tradfi_sector_score(
    symbol: str,
    direction: str,
    rsi_1h_fn: Optional[Callable[[str], Optional[float]]] = None,
) -> dict:
    """
    计算板块联动评分

    Args:
        symbol:    当前分析标的，如 'MUUSDT'
        direction: 'LONG' 或 'SHORT'
        rsi_1h_fn: 获取其他标的RSI_1H的函数 fn(symbol) -> float|None
                   如果为None，跳过联动计算返回0

    Returns:
        {
            'score': float,         # 板块联动加分（0 ~ +20）
            'group': str,           # 所属板块名
            'aligned_count': int,   # 同向对齐成员数
            'detail': str,          # 人类可读说明
        }
    """
    result = {'score': 0.0, 'group': 'none', 'aligned_count': 0, 'detail': '非TRADFI板块'}

    if symbol not in _GROUP_MAP:
        return result

    group_name, group_members = _GROUP_MAP[symbol]
    result['group'] = group_name

    if rsi_1h_fn is None:
        result['detail'] = f'{group_name}板块，无RSI数据源跳过联动'
        return result

    # 判断当前信号方向的超卖/超买逻辑
    # LONG：同组成员RSI<30越多 → 板块超卖共振 → 加分
    # SHORT：同组成员RSI>70越多 → 板块超买共振 → 加分
    aligned_count = 0
    checked = 0

    for peer in group_members:
        if peer == symbol:
            continue
        try:
            rsi = rsi_1h_fn(peer)
            if rsi is None:
                continue
            checked += 1
            if direction == 'LONG' and rsi < _OVERSOLD_THRESH:
                aligned_count += 1
            elif direction == 'SHORT' and rsi > _OVERBOUGHT_THRESH:
                aligned_count += 1
        except Exception as e:
            _log.debug(f'sector_engine: {peer} RSI查询失败 {e}')

    result['aligned_count'] = aligned_count
    result['checked'] = checked

    if aligned_count >= _MIN_MEMBERS_FOR_BOOST:
        sector_score = min(aligned_count * _SCORE_PER_MEMBER, _MAX_SECTOR_SCORE)
        result['score'] = float(sector_score)
        thresh_desc = f'RSI<{_OVERSOLD_THRESH}' if direction == 'LONG' else f'RSI>{_OVERBOUGHT_THRESH}'
        result['detail'] = (
            f'{group_name}板块共振: {aligned_count}/{checked}同组成员{thresh_desc}'
            f' → sector_corr+{sector_score:.0f}'
        )
    else:
        result['detail'] = (
            f'{group_name}板块: {aligned_count}/{checked}同组成员对齐，未达{_MIN_MEMBERS_FOR_BOOST}个阈值'
        )

    return result


def get_quick_rsi_1h(symbol: str) -> Optional[float]:
    """
    轻量级RSI_1H获取（不走brahma_bus缓存，直接取最近数据）
    用于板块联动扫描时快速获取同组成员数据
    """
    try:
        from brahma_brain.brahma_bus import get_klines_cached
        klines = get_klines_cached(symbol, '1h', limit=20)
        if not klines or len(klines) < 14:
            return None
        closes = [float(k[4]) for k in klines]
        # 计算RSI14
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[:14]) / 14
        avg_loss = sum(losses[:14]) / 14
        for i in range(14, len(gains)):
            avg_gain = (avg_gain * 13 + gains[i]) / 14
            avg_loss = (avg_loss * 13 + losses[i]) / 14
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - 100 / (1 + rs), 2)
    except Exception as e:
        _log.debug(f'quick_rsi_1h {symbol}: {e}')
        return None
