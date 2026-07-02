#!/usr/bin/env python3
"""
position_sizer.py — 梵天仓位计算引擎
Brahma-Quant Open Source v3.0 | 设计院封印 2026-07-02

⚠️  PRO 版说明
════════════════════════════════════════════════
本文件为框架骨架（Open Core 版本）。

核心参数（kelly分率、WR矩阵、体制乘数、v4.2铁证出场参数）
属于 Brahma-Quant Pro 私有配置，不在开源版中提供。

Pro 版获取方式：参见 CONTRIBUTING.md
════════════════════════════════════════════════

框架说明：
  - get_position_pct(): 根据信号质量 + 体制 + NAV 计算仓位百分比
  - kelly_position():   Kelly 公式仓位（需传入 WR 和 RR）
  - JULY_HALF_POSITION: 7月减半策略开关

接口完全兼容 Pro 版，替换 Pro 配置文件后即可激活完整功能。
"""
import os
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ── 仓位上限（公开配置） ─────────────────────────────────────────
MAX_POS_PCT_NAV = float(os.environ.get('MAX_POS_PCT_NAV', '10.0'))  # PIXEL教训：单笔上限
MIN_SCORE_THRESHOLD = float(os.environ.get('MIN_SCORE_THRESHOLD', '120.0'))

# ── Pro 配置占位符 ───────────────────────────────────────────────
# 以下参数在 Pro 版中由训练好的权重矩阵填充
# 开源版返回基于 Kelly 公式的理论值

# 7月减半策略（Pro版可覆盖）
JULY_HALF_POSITION = os.environ.get('JULY_HALF_POSITION', 'false').lower() == 'true'

# 体制乘数（Pro版: 从训练矩阵加载；开源版: 均等乘数）
_REGIME_MULTIPLIERS = {
    # Pro版: BEAR_TREND=0.10x(多) / 1.6x(空), BULL_TREND=1.6x(多) / 0.15x(空) 等
    # 开源版占位（替换为 Pro 配置后激活）
    'BEAR_TREND':    {'LONG': 0.10, 'SHORT': 1.0},   # TODO: Pro值
    'BULL_TREND':    {'LONG': 1.0,  'SHORT': 0.15},  # TODO: Pro值
    'CHOP_MID':      {'LONG': 0.50, 'SHORT': 0.88},  # TODO: Pro值
    'BEAR_EARLY':    {'LONG': 0.35, 'SHORT': 1.2},   # TODO: Pro值
    'BEAR_RECOVERY': {'LONG': 1.2,  'SHORT': 0.30},  # TODO: Pro值
}

# 出场参数（Pro版: v4.2铁证参数；开源版: 保守默认）
_EXIT_PARAMS_PRO = {
    # Pro版封印值 (SL/RR/EV 均为实盘统计结果，不公开)
    # 开源版使用保守默认值
    'BEAR_TREND':    {'sl_pct': 2.0, 'rr': 1.0},  # Pro: EV=+0.578%/笔
    'CHOP_MID':      {'sl_pct': 2.5, 'rr': 1.0},  # Pro: EV=+0.811%/笔
    'BULL_TREND':    {'sl_pct': 2.0, 'rr': 1.2},
    'BEAR_EARLY':    {'sl_pct': 2.2, 'rr': 1.0},
    'BEAR_RECOVERY': {'sl_pct': 2.0, 'rr': 1.1},
}


def kelly_position(wr: float, rr: float, half: bool = True,
                   max_pct: float = 25.0) -> float:
    """
    Kelly 公式仓位计算

    Args:
        wr:      胜率 (0~1)
        rr:      盈亏比
        half:    True=使用 1/2 Kelly（推荐，降低方差）
        max_pct: 最大仓位上限（%）

    Returns:
        仓位百分比（0~max_pct）

    Kelly 公式：f* = WR - (1-WR)/RR
    """
    try:
        f = wr - (1 - wr) / max(rr, 1e-6)
        f = max(f, 0.0)
        if half:
            f *= 0.5
        # 转换为百分比并应用上限
        result = min(f * 100, max_pct)
        return round(result, 2)
    except Exception as e:
        logger.warning(f"[PositionSizer] kelly_position 计算异常: {e}")
        return 0.0


def get_position_pct(symbol: str,
                     score: float,
                     direction: str,
                     nav: float = 10000.0,
                     regime: Optional[str] = None,
                     **kwargs) -> Dict[str, Any]:
    """
    计算仓位百分比

    Args:
        symbol:    交易对 (e.g. 'BTCUSDT')
        score:     梵天信号总分 (0~200+)
        direction: 'LONG' 或 'SHORT'
        nav:       账户净值 USDT
        regime:    当前体制 (可选，影响乘数)

    Returns:
        {
          'pct':   仓位百分比 (0~10.0),
          'usdt':  仓位 USDT,
          'level': 评级 ('EXPLORING'/'STANDARD'/'AGGRESSIVE'),
          'regime_multiplier': 体制乘数,
        }

    Pro 版：从训练好的 WR 矩阵 + v4.2 铁证参数计算精确仓位
    开源版：基于 score 线性映射 + Kelly 公式的理论值
    """
    try:
        if score < MIN_SCORE_THRESHOLD:
            return {'pct': 0.0, 'usdt': 0.0, 'level': 'BLOCKED',
                    'reason': f'score {score} < threshold {MIN_SCORE_THRESHOLD}'}

        # 评级映射（开源版线性映射）
        if score >= 175:
            base_pct = 3.0
            level = 'AGGRESSIVE'
        elif score >= 160:
            base_pct = 2.0
            level = 'STANDARD'
        elif score >= 145:
            base_pct = 1.5
            level = 'EXPLORING'
        elif score >= 130:
            base_pct = 1.0
            level = 'EXPLORING'
        else:
            base_pct = 0.5
            level = 'MINIMAL'

        # 体制乘数
        regime_mult = 1.0
        if regime and regime in _REGIME_MULTIPLIERS:
            dir_key = 'LONG' if direction == 'LONG' else 'SHORT'
            regime_mult = _REGIME_MULTIPLIERS[regime].get(dir_key, 1.0)

        # 7月减半
        july_mult = 0.5 if JULY_HALF_POSITION and 155 <= score <= 169 else 1.0

        pct = round(min(base_pct * regime_mult * july_mult, MAX_POS_PCT_NAV), 2)
        usdt = round(nav * pct / 100, 2)

        return {
            'pct': pct,
            'usdt': usdt,
            'level': level,
            'regime_multiplier': regime_mult,
            'july_mult': july_mult,
            'base_pct': base_pct,
            '_pro_note': 'Pro版使用v4.2铁证参数，精度更高',
        }

    except Exception as e:
        logger.error(f"[PositionSizer] get_position_pct 异常: {e}", exc_info=True)
        return {'pct': 0.0, 'usdt': 0.0, 'level': 'ERROR', 'error': str(e)}


def get_exit_params(regime: str) -> Dict[str, float]:
    """
    获取出场参数 (SL/TP)

    Pro 版：返回实盘统计的 v4.2 铁证参数
    开源版：返回保守默认值
    """
    return _EXIT_PARAMS_PRO.get(regime, {'sl_pct': 2.0, 'rr': 1.0})
