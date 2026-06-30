#!/usr/bin/env python3
"""
lsr_oi_engine.py — 多空比逆向 + OI方向 评分引擎
设计院 · 2026-06-07

六方辩论落地：今日ETH分析证明散户70%做多=反向信号，OI减少=虚假反弹
这两个维度缺失导致梵天系统在熊市反弹期间做多误判

核心逻辑：
  LSR（多空比）：散户极度拥挤 = 反向指标
  OI方向：OI减少+价格涨 = 空头回补（虚假），OI增加+价格涨 = 真实建仓

评分范围：-20 ~ +20
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 常量（达摩院校准） ────────────────────────────────────────
# LSR 阈值（实证：今日ETH 70.9%多头 = 空头收割信号）
LSR_EXTREME_LONG  = 70    # 极度多头拥挤（散户做多≥70%）→ 做多-15分，做空+15分
LSR_HIGH_LONG     = 65    # 偏多拥挤（≥65%）→ 做多-10分，做空+10分
LSR_EXTREME_SHORT = 30    # 极度空头拥挤（≤30%）→ 做多+15分，做空-15分
LSR_HIGH_SHORT    = 35    # 偏空拥挤（≤35%）→ 做多+10分，做空-10分
LSR_NEUTRAL_BAND  = (40, 60)  # 中性区，不加减分

# OI方向阈值（实证：OI减少-1%+价格涨 = 空头回补，非真实多头）
OI_STRONG_INCREASE =  2.0   # OI增加≥2% → 真实建仓
OI_WEAK_INCREASE   =  0.5   # OI增加0.5~2% → 轻微建仓
OI_STRONG_DECREASE = -2.0   # OI减少≥2% → 强烈平仓（空头回补或多头离场）
OI_WEAK_DECREASE   = -0.5   # OI减少0.5~2% → 轻微平仓

# 最大得分上下限
MAX_BONUS = 20
MAX_PENALTY = -20


def lsr_score(long_pct: float, signal_dir: str) -> tuple:
    """
    多空比逆向评分
    
    Args:
        long_pct: 多头占比（%），如 70.9
        signal_dir: 'LONG' 或 'SHORT'
    
    Returns:
        (score: int, note: str)
    """
    score = 0
    note_parts = []

    is_long  = signal_dir == 'LONG'
    is_short = signal_dir == 'SHORT'
    short_pct = 100 - long_pct

    # 极度多头拥挤
    if long_pct >= LSR_EXTREME_LONG:
        if is_long:
            score -= 15
            note_parts.append(f'多头极度拥挤{long_pct:.0f}%→做多-15(散户追多主力出货)')
        elif is_short:
            score += 15
            note_parts.append(f'多头极度拥挤{long_pct:.0f}%→做空+15(散户成燃料)')

    # 偏多拥挤
    elif long_pct >= LSR_HIGH_LONG:
        if is_long:
            score -= 10
            note_parts.append(f'多头偏多{long_pct:.0f}%→做多-10')
        elif is_short:
            score += 10
            note_parts.append(f'多头偏多{long_pct:.0f}%→做空+10')

    # 极度空头拥挤（逼空）
    elif long_pct <= LSR_EXTREME_SHORT:
        if is_long:
            score += 15
            note_parts.append(f'空头极度拥挤{short_pct:.0f}%→做多+15(逼空弹药充足)')
        elif is_short:
            score -= 15
            note_parts.append(f'空头极度拥挤{short_pct:.0f}%→做空-15(空头过拥挤)')

    # 偏空拥挤
    elif long_pct <= LSR_HIGH_SHORT:
        if is_long:
            score += 10
            note_parts.append(f'空头偏多{short_pct:.0f}%→做多+10')
        elif is_short:
            score -= 10
            note_parts.append(f'空头偏多{short_pct:.0f}%→做空-10')

    # 中性区（40~60%）
    else:
        note_parts.append(f'LSR中性{long_pct:.0f}%/{short_pct:.0f}% ±0')

    note = ' | '.join(note_parts) if note_parts else f'LSR={long_pct:.0f}% ±0'
    return int(max(MAX_PENALTY, min(MAX_BONUS, score))), note


def oi_direction_score(oi_change_pct: float, oi_momentum: str, 
                       price_change_pct: float, signal_dir: str) -> tuple:
    """
    OI方向与价格变化交叉验证评分
    
    核心洞察（今日ETH实证）：
      OI减少 + 价格上涨 = 空头回补 → 反弹质量差，不可持续
      OI增加 + 价格上涨 = 真实多头建仓 → 反弹有支撑
      OI减少 + 价格下跌 = 多头止损离场 → 下跌有动能
      OI增加 + 价格下跌 = 新空头建仓 → 做空信号强

    Args:
        oi_change_pct: OI变化%（过去5H）
        oi_momentum: 'INCREASING' / 'DECREASING' / 'NEUTRAL'
        price_change_pct: 近4H价格变化%
        signal_dir: 'LONG' 或 'SHORT'

    Returns:
        (score: int, note: str)
    """
    score = 0
    note_parts = []

    price_up   = price_change_pct > 0.3
    price_down = price_change_pct < -0.3

    # 情景1：OI增加 + 价格涨 = 真实多头建仓
    if oi_change_pct >= OI_STRONG_INCREASE and price_up:
        if signal_dir == 'LONG':
            score += 12
            note_parts.append(f'OI增{oi_change_pct:+.1f}%+价涨→真实建仓→做多+12')
        else:
            score -= 8
            note_parts.append(f'OI增{oi_change_pct:+.1f}%+价涨→真实多头→做空-8')

    elif OI_WEAK_INCREASE <= oi_change_pct < OI_STRONG_INCREASE and price_up:
        if signal_dir == 'LONG':
            score += 6
            note_parts.append(f'OI轻增{oi_change_pct:+.1f}%+价涨→做多+6')
        else:
            score -= 4
            note_parts.append(f'OI轻增{oi_change_pct:+.1f}%+价涨→做空-4')

    # 情景2：OI减少 + 价格涨 = 空头回补（虚假反弹）
    elif oi_change_pct <= OI_STRONG_DECREASE and price_up:
        if signal_dir == 'LONG':
            score -= 12
            note_parts.append(f'OI减{oi_change_pct:+.1f}%+价涨→空头回补虚假反弹→做多-12')
        else:
            score += 6
            note_parts.append(f'OI减{oi_change_pct:+.1f}%+价涨→回补完毕将转跌→做空+6')

    elif OI_STRONG_DECREASE < oi_change_pct <= OI_WEAK_DECREASE and price_up:
        if signal_dir == 'LONG':
            score -= 6
            note_parts.append(f'OI轻减{oi_change_pct:+.1f}%+价涨→做多-6')
        else:
            score += 3
            note_parts.append(f'OI轻减{oi_change_pct:+.1f}%+价涨→做空+3')

    # 情景3：OI增加 + 价格跌 = 新空头建仓（做空强信号）
    elif oi_change_pct >= OI_STRONG_INCREASE and price_down:
        if signal_dir == 'SHORT':
            score += 12
            note_parts.append(f'OI增{oi_change_pct:+.1f}%+价跌→新空仓建立→做空+12')
        else:
            score -= 8
            note_parts.append(f'OI增{oi_change_pct:+.1f}%+价跌→空头主导→做多-8')

    # 情景4：OI减少 + 价格跌 = 多头止损（做空延续）
    elif oi_change_pct <= OI_STRONG_DECREASE and price_down:
        if signal_dir == 'SHORT':
            score += 5
            note_parts.append(f'OI减{oi_change_pct:+.1f}%+价跌→多头踩踏→做空+5')
        else:
            score -= 5
            note_parts.append(f'OI减{oi_change_pct:+.1f}%+价跌→多头被迫离场→做多-5')

    else:
        note_parts.append(f'OI{oi_change_pct:+.1f}% 价{price_change_pct:+.1f}% 无明确方向 ±0')

    note = ' | '.join(note_parts) if note_parts else f'OI={oi_change_pct:+.1f}% ±0'
    return int(max(MAX_PENALTY, min(MAX_BONUS, score))), note


def lsr_oi_score(symbol: str, signal_dir: str, 
                 long_pct: float = None,
                 oi_change_pct: float = None,
                 oi_momentum: str = None,
                 price_change_pct: float = None) -> dict:
    """
    联合评分入口 — 供brahma_brain.py调用
    
    优先使用传入参数（已从market_state拉取），
    fallback到实时API拉取。
    
    Returns:
        {
          'score': int,         # -20 ~ +20
          'lsr_score': int,
          'oi_score': int,
          'lsr_pct': float,
          'oi_change_pct': float,
          'note': str,
          'breakdown': dict
        }
    """
    try:
        # ── 数据获取 fallback ──────────────────────────────
        if long_pct is None:
            try:
                from data_cache import get_long_short_ratio
                long_pct = get_long_short_ratio(symbol)
            except Exception:
                long_pct = 50.0

        if oi_change_pct is None or oi_momentum is None:
            try:
                from data_cache import get_open_interest
                oi_data = get_open_interest(symbol)
                oi_change_pct = oi_data.get('oi_change_pct', 0.0)
                oi_momentum   = oi_data.get('oi_momentum', 'NEUTRAL')
            except Exception:
                oi_change_pct = 0.0
                oi_momentum   = 'NEUTRAL'

        if price_change_pct is None:
            # 估算：用4H涨跌幅
            try:
                from data_cache import get_klines
                k4h = get_klines(symbol, '4h', limit=2)
                if k4h and len(k4h) >= 2:
                    prev_c = float(k4h[-2][4]); cur_c = float(k4h[-1][4])
                    price_change_pct = (cur_c - prev_c) / prev_c * 100
                else:
                    price_change_pct = 0.0
            except Exception:
                price_change_pct = 0.0

        # ── 计算两个子分 ───────────────────────────────────
        s_lsr, note_lsr = lsr_score(long_pct, signal_dir)
        s_oi,  note_oi  = oi_direction_score(
            oi_change_pct, oi_momentum, price_change_pct, signal_dir
        )

        # 总分（两个维度加总，上下限±20）
        total = int(max(MAX_PENALTY, min(MAX_BONUS, s_lsr + s_oi)))

        return {
            'score':         total,
            'lsr_score':     s_lsr,
            'oi_score':      s_oi,
            'lsr_pct':       round(long_pct, 1),
            'oi_change_pct': round(oi_change_pct, 2),
            'oi_momentum':   oi_momentum,
            'price_chg_4h':  round(price_change_pct, 2),
            'note':          f'LSR[{note_lsr}] OI[{note_oi}]',
            'breakdown': {
                'lsr': note_lsr,
                'oi':  note_oi,
            }
        }

    except Exception as e:
        return {
            'score': 0, 'lsr_score': 0, 'oi_score': 0,
            'lsr_pct': 50.0, 'oi_change_pct': 0.0, 'oi_momentum': 'NEUTRAL',
            'price_chg_4h': 0.0, 'note': f'lsr_oi_engine error: {e}',
            'breakdown': {}
        }


# ── 单元测试 ──────────────────────────────────────────────
if __name__ == '__main__':
    print("=== lsr_oi_engine 单元测试 ===\n")

    cases = [
        # (long_pct, oi_change_pct, oi_momentum, price_change_4h, direction, 预期)
        (70.9, -1.0, 'DECREASING', +2.0, 'SHORT', '今日ETH场景：空头做空应+15~20'),
        (70.9, -1.0, 'DECREASING', +2.0, 'LONG',  '今日ETH场景：做多应-15~-20'),
        (28.0,  3.0, 'INCREASING', +3.0, 'LONG',  '极度空头+OI增：做多应+25'),
        (55.0,  0.2, 'NEUTRAL',    +0.1, 'SHORT', '中性场景：接近0'),
        (66.0, -2.5, 'DECREASING', +1.5, 'SHORT', '偏多+OI减+价涨：做空应+10~15'),
    ]

    for long_pct, oi_chg, oi_mom, p_chg, direction, expected in cases:
        s_lsr, n_lsr = lsr_score(long_pct, direction)
        s_oi,  n_oi  = oi_direction_score(oi_chg, oi_mom, p_chg, direction)
        total = s_lsr + s_oi
        print(f"[{direction}] LSR={long_pct}% OI{oi_chg:+.1f}% 价{p_chg:+.1f}%")
        print(f"  LSR分={s_lsr:+d}: {n_lsr}")
        print(f"  OI分={s_oi:+d}:  {n_oi}")
        print(f"  总分={total:+d}  预期：{expected}")
        print()
