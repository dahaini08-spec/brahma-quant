"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 动态止损计算，执行辅助
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
dynamic_sl.py — I3 ATR自适应止损引擎 (Brahma v12.9)
═══════════════════════════════════════════════════
功能:
  1. 基于ATR动态计算止损位（替代固定百分比）
  2. 体制漂移时自动扩展SL（避免被轻易扫损）
  3. 支撑/阻力层精确止损（Key Level吸附）
  4. 移动止损进度计算（已盈利时建议跟踪）
  5. 止损建议：Conservative / Standard / Aggressive

SL公式:
  base_sl  = entry ± ATR(14) × multiplier
  drift_sl = base_sl × (1 + drift_expansion)
  key_sl   = snap_to_nearest_key_level(drift_sl, tolerance=0.3%)

体制乘数:
  BULL_TREND  LONG:  1.5×ATR (趋势宽松)
  BEAR_IMPULSE SHORT: 1.5×ATR
  CHOP_MID:          1.0×ATR (震荡紧凑)
  HIGH_VOL:          2.0×ATR (高波动保护)
"""
import json, math
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / 'data'
DHARMA   = Path(__file__).parent.parent / 'dharma' / 'data'

# ATR乘数：体制 × 方向
REGIME_MULT = {
    'BULL_TREND':    {'LONG': 1.5, 'SHORT': 1.2},
    'BULL_RECOVERY': {'LONG': 1.4, 'SHORT': 1.3},
    'BEAR_IMPULSE':  {'LONG': 1.2, 'SHORT': 1.5},
    'BEAR_RECOVERY': {'LONG': 1.3, 'SHORT': 1.4},
    'CHOP_MID':      {'LONG': 1.0, 'SHORT': 1.0},
    'CHOP_HIGH':     {'LONG': 1.8, 'SHORT': 1.8},
    'CHOP_LOW':      {'LONG': 0.9, 'SHORT': 0.9},
    'DEFAULT':       {'LONG': 1.2, 'SHORT': 1.2},
}

# 止损风格
SL_STYLES = {
    'conservative': 0.7,   # 更紧（高评分信号，减少亏损）
    'standard':     1.0,   # 标准
    'aggressive':   1.4,   # 更宽（高波动，避免扫损）
}


def _atr14_from_parquet(symbol: str, interval: str = '1h') -> float | None:
    """从达摩院Parquet读取ATR14"""
    try:
        sym_lower = symbol.lower().replace('usdt','usdt')
        fname = DHARMA / f'{sym_lower}_{interval}_2018_2026.parquet'
        if not fname.exists():
            fname = DHARMA / f'{symbol.lower()}_{interval}_2018_2026.parquet'
        if not fname.exists(): return None
        import pandas as pd
        df = pd.read_parquet(fname).tail(50)
        hi, lo, cl = df['high'].values, df['low'].values, df['close'].values
        trs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
               for i in range(1,len(cl))]
        # EMA ATR14
        atr = trs[0]
        for tr in trs[1:]:
            atr = atr * 13/14 + tr * 1/14
        return atr
    except: return None


def _snap_to_key_level(price: float, key_levels: list, side: str,
                        tolerance: float = 0.003) -> float:
    """将止损吸附到最近的关键位（如果在tolerance范围内）"""
    if not key_levels: return price
    is_long = side in ('LONG','做多')
    best = price
    best_dist = float('inf')
    for lvl in key_levels:
        dist = abs(lvl - price) / price
        if dist <= tolerance:
            # 多头止损应该在支撑位下方, 空头在阻力位上方
            if (is_long and lvl < price) or (not is_long and lvl > price):
                if dist < best_dist:
                    best_dist = dist
                    best = lvl * (0.998 if is_long else 1.002)  # 再让一点
    return best


def compute(
    symbol: str,
    entry_price: float,
    signal_dir: str,
    regime: str = 'CHOP_MID',
    score: float = 100,
    drift_alert: str = 'OK',
    current_price: float = None,
    key_levels: list = None,
    style: str = 'standard',
    interval: str = '1h',
) -> dict:
    """
    计算动态止损位

    Returns:
        {
          'sl_price':    推荐止损价
          'sl_pct':      止损幅度%
          'atr14':       ATR14绝对值
          'atr_mult':    使用的ATR乘数
          'trail_note':  移动止损建议
          'reasoning':   str
        }
    """
    is_long = signal_dir in ('LONG','做多')

    # ── 获取ATR ────────────────────────────────────────────
    atr14 = _atr14_from_parquet(symbol, interval)
    if atr14 is None or atr14 <= 0:
        # fallback: entry的1.5%估算
        atr14 = entry_price * 0.015
        atr_source = 'estimated'
    else:
        atr_source = f'parquet_{interval}'

    # ── 体制乘数 ────────────────────────────────────────────
    reg_key = regime.upper()
    if reg_key not in REGIME_MULT: reg_key = 'DEFAULT'
    dir_key = 'LONG' if is_long else 'SHORT'
    base_mult = REGIME_MULT[reg_key][dir_key]

    # ── 漂移扩展 ────────────────────────────────────────────
    drift_expansion = 0.0
    if drift_alert == 'WARN':  drift_expansion = 0.15  # SL扩展15%
    if drift_alert == 'ALERT': drift_expansion = 0.30  # SL扩展30%

    # ── 评分调整（高分信号可以更紧） ────────────────────────
    score_adj = 1.0 - max(0, score - 120) * 0.002  # score=150→0.94
    score_adj = max(0.8, min(1.1, score_adj))

    # ── 风格系数 ────────────────────────────────────────────
    style_mult = SL_STYLES.get(style, 1.0)

    # ── 最终ATR乘数 ─────────────────────────────────────────
    final_mult = base_mult * (1 + drift_expansion) * score_adj * style_mult
    final_mult = max(0.7, min(3.0, final_mult))

    # ── 止损价格 ────────────────────────────────────────────
    sl_distance = atr14 * final_mult
    if is_long:
        sl_price = entry_price - sl_distance
    else:
        sl_price = entry_price + sl_distance

    # ── 关键位吸附 ──────────────────────────────────────────
    sl_price_snapped = _snap_to_key_level(sl_price, key_levels or [], signal_dir)
    snapped = abs(sl_price_snapped - sl_price) / entry_price > 0.0005

    sl_pct = abs(entry_price - sl_price_snapped) / entry_price

    # ── 移动止损建议 ────────────────────────────────────────
    trail_note = ''
    if current_price and entry_price:
        pnl_pct = (current_price - entry_price) / entry_price
        if not is_long: pnl_pct = -pnl_pct
        if pnl_pct >= 0.015:
            trail_note = f'TRAIL: price moved +{pnl_pct:.1%} → move SL to breakeven'
        if pnl_pct >= 0.03:
            trail_note = f'TRAIL: +{pnl_pct:.1%} → move SL to +1.0%'
        if pnl_pct >= 0.05:
            trail_note = f'TRAIL: +{pnl_pct:.1%} → move SL to +2.5%'

    reasoning = (
        f'ATR14={atr14:.4f}({atr_source}) mult={final_mult:.2f} '
        f'[regime×{base_mult:.1f} drift×{1+drift_expansion:.2f} '
        f'score×{score_adj:.2f} style×{style_mult:.1f}]'
        f'{" SNAPPED" if snapped else ""}'
    )

    return {
        'sl_price':    round(sl_price_snapped, 6),
        'sl_raw':      round(sl_price, 6),
        'sl_pct':      round(sl_pct, 5),
        'sl_distance': round(sl_distance, 6),
        'atr14':       round(atr14, 6),
        'atr_mult':    round(final_mult, 3),
        'atr_source':  atr_source,
        'snapped_to_key': snapped,
        'trail_note':  trail_note,
        'reasoning':   reasoning,
        'ts': datetime.now(timezone.utc).isoformat(),
    }


if __name__ == '__main__':
    # ETH空单测试
    r = compute('ETHUSDT', 2127.65, 'SHORT', 'CHOP_MID', score=154, drift_alert='WARN')
    print(f"ETH SHORT entry=2127.65:")
    print(f"  SL={r['sl_price']:.2f}  ({r['sl_pct']:.2%})")
    print(f"  ATR14={r['atr14']:.4f}  mult={r['atr_mult']:.2f}")
    print(f"  {r['reasoning']}")
    if r['trail_note']: print(f"  {r['trail_note']}")
