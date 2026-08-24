# ponytail: brahma_intel_layer 315行，有意为之，重构前先 grep 所有调用方
"""
brahma_intel_layer.py — 梵天智慧层 v1.0
设计院太极封印 2026-08-18 苏摩111

职责：
  1. 情境识别：识别当前市场操盘手法类型（6种）
  2. 三线策略自动生成：短线/中线/长线完整可执行策略
  3. 连续记忆：追踪今日分析演变，识别主力意图轨迹

太极原则：指标（现在）× 经验（历史）= 智慧（行动）
"""

from __future__ import annotations
from typing import Optional
import json, time, os
from pathlib import Path

# ── 情境类型定义 ─────────────────────────────────────────────────
PATTERN_INDUCING_LONG   = '诱多派发型'   # 主力拉高出货
PATTERN_INDUCING_SHORT  = '诱空吸筹型'   # 主力压低吸筹
PATTERN_REAL_BREAKOUT   = '真突破延续型' # 趋势延续
PATTERN_FAKE_BREAKOUT   = '假突破猎杀型' # 猎杀止损
PATTERN_BULL_WAVE       = '主升浪加速型' # 趋势加速
PATTERN_BEAR_WAVE       = '主跌浪加速型' # 崩盘加速
PATTERN_UNKNOWN         = '方向待定'

# ── 情境识别引擎 ──────────────────────────────────────────────────
def identify_pattern(
    oi_1h: float,        # OI 1H变化%
    oi_8h: float,        # OI 8H变化%
    hcme_wr: float,      # HCME胜率 0~100
    hcme_wr_prev: float, # 上次HCME胜率（用于突变检测）
    l2_ratio: float,     # L2买卖比
    long_pct: float,     # 多头占比%
    pd_zone: str,        # PREMIUM/DISCOUNT/NEUTRAL
    bos_type: str,       # BULL_BOS/BEAR_BOS/无
    regime: str,         # 体制
    price_vs_ema: str,   # ABOVE/BELOW EMA20_1H
) -> dict:
    """
    识别当前市场情境类型
    返回：pattern_type, confidence, intent, best_strategy
    """
    short_pct = 100 - long_pct
    wr_dropped = (hcme_wr_prev - hcme_wr) > 30  # HCME突降>30%

    # ── 诱多派发型判断 ──────────────────────────────────────────
    if (oi_8h < -3 and hcme_wr <= 40 and
            short_pct >= 70 and pd_zone == 'PREMIUM'):
        return {
            'pattern': PATTERN_INDUCING_LONG,
            'confidence': 'HIGH',
            'intent': '主力高位出货，利用空头拥挤制造假象',
            'best_strategy': '等轧空完成后扎空',
            'warning': '⚠️ 追多高危区，历史75%失败',
        }

    # ── 诱空吸筹型判断 ──────────────────────────────────────────
    if (oi_1h > 10 and hcme_wr >= 80 and
            long_pct >= 70 and pd_zone == 'DISCOUNT'):
        return {
            'pattern': PATTERN_INDUCING_SHORT,
            'confidence': 'HIGH',
            'intent': '主力低位吸筹，利用多头恐慌制造假象',
            'best_strategy': '等洗盘确认站稳后做多',
            'warning': '✅ 底部信号，HCME铁证支持',
        }

    # ── 真突破延续型 ──────────────────────────────────────────
    if (bos_type == 'BULL_BOS' and oi_1h > 5 and
            hcme_wr >= 80 and price_vs_ema == 'ABOVE'):
        return {
            'pattern': PATTERN_REAL_BREAKOUT,
            'confidence': 'MED',
            'intent': '主力推动趋势延续',
            'best_strategy': '突破后回踩OB顺势做多',
            'warning': '✅ 结构支持，等回踩确认',
        }

    # ── 主升浪加速型 ──────────────────────────────────────────
    if (hcme_wr >= 80 and oi_8h > 8 and
            regime in ('BULL_TREND',) and pd_zone != 'PREMIUM'):
        return {
            'pattern': PATTERN_BULL_WAVE,
            'confidence': 'MED',
            'intent': '主升浪加速，每次回调都是机会',
            'best_strategy': '回调轻仓买，持有不做空',
            'warning': '✅ 主升浪特征，回调即买',
        }

    # ── 主跌浪加速型 ──────────────────────────────────────────
    if (hcme_wr <= 30 and oi_8h < -5 and
            regime in ('BEAR_TREND', 'BEAR_EARLY')):
        return {
            'pattern': PATTERN_BEAR_WAVE,
            'confidence': 'MED',
            'intent': '主跌浪加速，反弹即出货',
            'best_strategy': '反弹扎空，不抄底',
            'warning': '🔴 主跌特征，禁止做多',
        }

    # ── 假突破猎杀型 ──────────────────────────────────────────
    if wr_dropped and oi_1h < 0 and l2_ratio < 0.5:
        return {
            'pattern': PATTERN_FAKE_BREAKOUT,
            'confidence': 'MED',
            'intent': 'HCME突变，可能是假突破猎杀',
            'best_strategy': '等反转确认后做反向',
            'warning': '⚠️ HCME突变预警，谨慎追势',
        }

    return {
        'pattern': PATTERN_UNKNOWN,
        'confidence': 'LOW',
        'intent': '方向不明，等待更清晰结构',
        'best_strategy': '观望，等HCME WR≥80%',
        'warning': '🟡 信号不足，不操作',
    }


# ── 三线策略生成器 ─────────────────────────────────────────────────
def generate_three_line_strategy(
    symbol: str,
    direction: str,          # LONG/SHORT
    price: float,
    pattern: dict,
    bull_ob_lo: float,
    bull_ob_hi: float,
    bear_ob_lo: float,
    bear_ob_hi: float,
    fvg_target: float,
    liq_100x_up: float,      # 100x空头清算
    liq_50x_up: float,       # 50x空头清算
    liq_20x_up: float,       # 20x空头清算
    liq_100x_dn: float,      # 100x多头清算
    liq_stop_pool: float,    # 止损池密集区
    hcme_wr: float,
    grade: float,
    regime: str,
    sl_pct_limit: float = 2.5,
) -> str:
    """
    生成短线/中线/长线三线策略文本
    """
    pt = pattern.get('pattern', PATTERN_UNKNOWN)
    intent = pattern.get('intent', '')
    warning = pattern.get('warning', '')

    lines = []
    lines.append(f'\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    lines.append(f'  🏛️ 梵天三线策略 · {symbol}  ${price:.2f}')
    lines.append(f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    lines.append(f'【情境识别】{pt}')
    lines.append(f'【主力意图】{intent}')
    lines.append(f'【风险警示】{warning}')
    lines.append('')

    # ── 短线策略（0~4H）──────────────────────────────────────
    if pt == PATTERN_INDUCING_LONG:
        # 诱多派发：等轧空后扎空
        short_trigger  = f'价格拉至 ${liq_100x_up:.0f}（轧空完成信号）'
        short_entry_lo = liq_100x_up
        short_entry_hi = liq_50x_up
        short_sl       = short_entry_hi * 1.02
        short_tp1      = bull_ob_hi
        short_tp2      = bull_ob_lo
        short_dir      = 'SHORT'
        rr1 = abs(short_entry_lo - short_tp1) / abs(short_sl - short_entry_lo) if abs(short_sl - short_entry_lo) > 0 else 0
    elif pt == PATTERN_INDUCING_SHORT:
        # 诱空吸筹：等洗盘后做多
        short_trigger  = f'价格回踩 ${bull_ob_lo:.0f}~${bull_ob_hi:.0f} 站稳'
        short_entry_lo = bull_ob_lo
        short_entry_hi = bull_ob_hi
        short_sl       = short_entry_lo * 0.98
        short_tp1      = liq_100x_up
        short_tp2      = liq_50x_up
        short_dir      = 'LONG'
        rr1 = abs(short_tp1 - short_entry_hi) / abs(short_entry_lo - short_sl) if abs(short_entry_lo - short_sl) > 0 else 0
    else:
        # 默认：清算驱动短线
        if direction == 'LONG':
            short_trigger  = f'价格回踩 ${bull_ob_lo:.0f}~${bull_ob_hi:.0f}'
            short_entry_lo, short_entry_hi = bull_ob_lo, bull_ob_hi
            short_sl       = liq_100x_dn * 0.995
            short_tp1      = liq_100x_up
            short_dir      = 'LONG'
        else:
            short_trigger  = f'价格拉至 ${liq_100x_up:.0f} 后反转'
            short_entry_lo = liq_100x_up
            short_entry_hi = liq_50x_up
            short_sl       = short_entry_hi * 1.025
            short_tp1      = bull_ob_hi
            short_dir      = 'SHORT'
        rr1 = 1.0

    lines.append(f'📌 短线（0~4H）| HCME={hcme_wr:.0f}% | 情境胜率参考')
    lines.append(f'   触发：{short_trigger}')
    lines.append(f'   方向：{short_dir}  入场：${short_entry_lo:.2f}~${short_entry_hi:.2f}')
    lines.append(f'   止损：${short_sl:.2f}  TP1：${short_tp1:.2f}')
    lines.append(f'   仓位：2% NAV | 3x | RR≈{rr1:.1f}')
    lines.append('')

    # ── 中线策略（1~3天）──────────────────────────────────────
    # 基于Bull OB回调区 + HCME历史胜率
    mid_entry_lo = bull_ob_lo
    mid_entry_hi = bull_ob_hi
    mid_sl       = liq_stop_pool * 0.995 if liq_stop_pool > 0 else mid_entry_lo * 0.97
    mid_tp1      = liq_20x_up
    mid_tp2      = mid_tp1 * 1.05
    mid_rr       = abs(mid_tp1 - mid_entry_hi) / abs(mid_entry_lo - mid_sl) if abs(mid_entry_lo - mid_sl) > 0 else 0

    lines.append(f'📌 中线（1~3天）| HCME铁证 | 等Bull OB站稳')
    lines.append(f'   触发：回踩 ${mid_entry_lo:.2f}~${mid_entry_hi:.2f} 站稳 + RSI 1H回落<60')
    lines.append(f'   方向：LONG  入场：${mid_entry_lo:.2f}~${mid_entry_hi:.2f}')
    lines.append(f'   止损：${mid_sl:.2f}  TP1：${mid_tp1:.2f}  TP2：${mid_tp2:.2f}')
    lines.append(f'   仓位：5% NAV | 5x | RR≈{mid_rr:.1f}')
    lines.append('')

    # ── 长线策略（1~2周）──────────────────────────────────────
    # 基于FVG磁铁 + 主升浪逻辑
    long_entry_lo = fvg_target * 0.99 if fvg_target > 0 else mid_entry_lo * 0.96
    long_entry_hi = fvg_target * 1.01 if fvg_target > 0 else mid_entry_hi * 0.97
    long_sl       = long_entry_lo * 0.96
    long_tp1      = liq_20x_up * 1.02
    long_tp2      = liq_20x_up * 1.08
    long_rr       = abs(long_tp1 - long_entry_hi) / abs(long_entry_lo - long_sl) if abs(long_entry_lo - long_sl) > 0 else 0

    lines.append(f'📌 长线（1~2周）| 主升浪逻辑 | 深度回调布局')
    lines.append(f'   触发：FVG磁铁 ${fvg_target:.2f} 附近大回调确认站稳')
    lines.append(f'   方向：LONG  入场：${long_entry_lo:.2f}~${long_entry_hi:.2f}')
    lines.append(f'   止损：${long_sl:.2f}  TP1：${long_tp1:.2f}  TP2：${long_tp2:.2f}')
    lines.append(f'   仓位：3% NAV | 3x | RR≈{long_rr:.1f}（低杠杆持有）')
    lines.append('')
    lines.append(f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')

    return '\n'.join(lines)


# ── 连续记忆层 ─────────────────────────────────────────────────────
_MEMORY_FILE = Path(__file__).parent.parent / 'data' / 'intraday_memory.json'

def record_analysis(symbol: str, price: float, hcme_wr: float,
                    oi_1h: float, pattern: str, decision: str):
    """记录今日分析轨迹，用于主力意图追踪"""
    try:
        memory = {}
        if _MEMORY_FILE.exists():
            try:
                memory = json.loads(_MEMORY_FILE.read_text())
            except Exception:
                memory = {}

        today = time.strftime('%Y-%m-%d')
        if today not in memory:
            memory[today] = {}
        if symbol not in memory[today]:
            memory[today][symbol] = []

        memory[today][symbol].append({
            'ts': time.strftime('%H:%M'),
            'price': round(price, 2),
            'hcme_wr': hcme_wr,
            'oi_1h': oi_1h,
            'pattern': pattern,
            'decision': decision,
        })

        # 只保留最近3天
        keys = sorted(memory.keys())
        for old_key in keys[:-3]:
            del memory[old_key]

        _MEMORY_FILE.parent.mkdir(exist_ok=True)
        _MEMORY_FILE.write_text(json.dumps(memory, ensure_ascii=False, indent=2))
    except Exception:
        pass


def get_today_timeline(symbol: str) -> list:
    """获取今日该标的的分析时间线"""
    try:
        if not _MEMORY_FILE.exists():
            return []
        memory = json.loads(_MEMORY_FILE.read_text())
        today = time.strftime('%Y-%m-%d')
        return memory.get(today, {}).get(symbol, [])
    except Exception:
        return []


def summarize_intent(symbol: str) -> str:
    """根据今日时间线推断主力意图"""
    timeline = get_today_timeline(symbol)
    if len(timeline) < 2:
        return '数据不足，无法判断主力意图'

    wrs    = [t['hcme_wr'] for t in timeline]
    ois    = [t['oi_1h'] for t in timeline]
    prices = [t['price'] for t in timeline]

    wr_trend  = wrs[-1] - wrs[0]   # WR变化
    oi_trend  = sum(ois) / len(ois) # 平均OI
    price_chg = (prices[-1] - prices[0]) / prices[0] * 100

    if wr_trend < -40 and price_chg > 3:
        return f'⚠️ 诱多出货：价格+{price_chg:.1f}%但HCME WR暴跌{wr_trend:.0f}%，主力高位出货信号'
    elif wr_trend > 40 and price_chg < -3:
        return f'✅ 诱空吸筹：价格{price_chg:.1f}%但HCME WR暴涨+{wr_trend:.0f}%，主力低位吸筹信号'
    elif wr_trend > 0 and oi_trend > 3:
        return f'✅ 趋势延续：WR+{wr_trend:.0f}% OI持续流入，主升浪特征'
    elif wr_trend < -20 and oi_trend < 0:
        return f'🔴 派发离场：WR{wr_trend:.0f}% OI撤退，小心回调'
    else:
        return f'🟡 方向未明：WR变化{wr_trend:.0f}%，继续观察'
