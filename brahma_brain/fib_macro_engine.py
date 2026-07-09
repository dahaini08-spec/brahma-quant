#!/usr/bin/env python3
"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# Fibonacci宏观层，入场区计算辅助
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
fib_macro_engine.py — 大级别Fib结构 + EMA200日线 + 周线RSI宏观过滤引擎
设计院 · 2026-06-07

六方辩论落地：
  今日证明ETH从$2,464→$1,504，Fib0.236=$1,730是第一道真实阻力
  当前价格$1,633在Fib0.236下方 = 做多面临阻力
  EMA200日线=$2,105 = 机构做多门槛（价格在下方 = 机构不买）
  周线RSI=50（非超卖）= 熊市未到底部，做多减权

核心规则（实证）：
  反弹目标上限 = Fib0.236（+5.9%）
  真正阻力墙  = Fib0.382（+14.7%）
  做多信号：越接近Fib支撑越好，越远离越差
  做空信号：接近Fib阻力 = 最优入场位

评分范围：-25 ~ +20
"""


from brahma_brain.math_utils import _ema, _rsi, calc_rsi, rsi, ema  # 统一数学库

import sys
import os

# ── brahma_bus 总线接入（设计院 2026-06-29）──
try:
    from brahma_brain.brahma_bus import bus as _brahma_bus
except Exception:
    _brahma_bus = None

def _bus_price(symbol: str) -> float:
    """统一价格获取：优先走 brahma_bus 缓存，降级走直接HTTP"""
    if _brahma_bus:
        return _brahma_bus.price(symbol)
    import requests
    r = requests.get(f'https://fapi.binance.com/fapi/v1/ticker/price',
                     params={'symbol': symbol}, timeout=5)
    return float(r.json()['price'])


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ── 宏观参数 ───────────────────────────────────────────────
EMA200_PENALTY_LONG  = -6   # [2026-07-06 设计院自主决策] -10→-6：RECOVERY区间有回归EMA200趋势，过重惩罚会错杀早期反弹信号
EMA200_BONUS_SHORT   = +8   # 价格低于EMA200 → 做空加分
EMA55_PENALTY_LONG   = -3   # [2026-07-06 设计院自主决策] -5→-3：EMA55低于不是强居炱空，减轻惩罚

WEEKLY_RSI_OVERSOLD     = 35   # 周线RSI≤35 = 接近底部，做多加权
WEEKLY_RSI_NEUTRAL_HIGH = 55   # 周线RSI≥55 = 非底部，做多减权
WEEKLY_RSI_OVERBOUGHT   = 70   # 周线RSI≥70 = 做多危险区

FIB_RESISTANCE_ZONE = 0.03    # 距Fib阻力±3% = 阻力区
FIB_SUPPORT_ZONE    = 0.03    # 距Fib支撑±3% = 支撑区


# [math_utils] _ema 已统一到 brahma_brain.math_utils，此处保留备用
def _ema(data: list, n: int) -> float:
    """简单EMA计算"""
    k = 2 / (n + 1)
    e = data[0]
    for v in data[1:]:
        e = v * k + e * (1 - k)
    return e


def _rsi14(closes: list) -> float:
    """RSI14计算 — Wilder EMA [FIX-RSI-WILDER 2026-06-14]"""
    if len(closes) < 15: return 50.0
    g = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    lo = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag = sum(g[:14])/14; al = sum(lo[:14])/14
    for i in range(14, len(g)):
        ag = (ag*13+g[i])/14; al = (al*13+lo[i])/14
    return round(100 - 100/(1+ag/max(al,1e-9)), 2)


def _compute_fib_levels(high: float, low: float) -> dict:
    """从高低点计算Fib反弹水平（从低点向上）"""
    swing = high - low
    return {
        '0.236': low + swing * 0.236,
        '0.382': low + swing * 0.382,
        '0.500': low + swing * 0.500,
        '0.618': low + swing * 0.618,
        '0.786': low + swing * 0.786,
    }


def _get_macro_data(symbol: str):
    """获取日线/周线数据"""
    try:
        from data_cache import get_klines
        k1d = get_klines(symbol, '1d', limit=210)
        k1w = get_klines(symbol, '1w', limit=24)
        return k1d, k1w
    except Exception:
        return None, None


def fib_macro_score(symbol: str, price: float, signal_dir: str,
                    k1d: list = None, k1w: list = None) -> dict:
    """
    宏观Fib + EMA200 + 周线RSI 联合评分

    Args:
        symbol:     交易对，如 'ETHUSDT'
        price:      当前价格
        signal_dir: 'LONG' 或 'SHORT'
        k1d:        日线K线数据（可选，缓存传入）
        k1w:        周线K线数据（可选，缓存传入）

    Returns:
        dict with keys: score, breakdown, fib_levels, weekly_rsi,
                        ema200, ema55_1d, regime_tag
    """
    result = {
        'score':       0,
        'breakdown':   {},
        'fib_levels':  {},
        'weekly_rsi':  50.0,
        'ema200':      price,
        'ema55_1d':    price,
        'regime_tag':  'UNKNOWN',
        'note':        '',
    }

    try:
        # ── 数据获取 ────────────────────────────────────────
        if k1d is None or k1w is None:
            k1d_raw, k1w_raw = _get_macro_data(symbol)
        else:
            k1d_raw, k1w_raw = k1d, k1w

        if not k1d_raw or len(k1d_raw) < 55:
            result['note'] = '日线数据不足'
            return result

        c1d = [float(k[4]) for k in k1d_raw]
        h1d = [float(k[2]) for k in k1d_raw]
        l1d = [float(k[3]) for k in k1d_raw]

        # ── EMA日线 ─────────────────────────────────────────
        ema200_1d = _ema(c1d, min(200, len(c1d)))
        ema55_1d  = _ema(c1d, min(55,  len(c1d)))
        ema21_1d  = _ema(c1d[-30:], 21)
        result['ema200']   = round(ema200_1d, 2)
        result['ema55_1d'] = round(ema55_1d, 2)

        # ── EMA200多周期支撇层（三院审核修复 2026-07-08）─────────────────
        # 核心发现：现价可能在日线EMA200下方，但已达1H/4H EMA200（短期支撇）
        # 这种情况系统之前漏掉，现在注入支撇加分
        try:
            from data_cache import get_klines as _get_kl_fib
            _k1h_fib = _get_kl_fib(symbol, '1h', 205)
            _k4h_fib = _get_kl_fib(symbol, '4h', 205)
            if _k1h_fib and len(_k1h_fib) >= 100:
                _c1h_fib = [float(k[4]) for k in _k1h_fib]
                _ema200_1h = _ema(_c1h_fib, min(200, len(_c1h_fib)))
                result['ema200_1h'] = round(_ema200_1h, 2)
                # 价格在1H EMA200上方，且在日线EMA200下方 = 短期支撇加分
                if price > _ema200_1h and price < ema200_1d:
                    result['ema200_1h_support'] = True
            if _k4h_fib and len(_k4h_fib) >= 100:
                _c4h_fib = [float(k[4]) for k in _k4h_fib]
                _ema200_4h = _ema(_c4h_fib, min(200, len(_c4h_fib)))
                result['ema200_4h'] = round(_ema200_4h, 2)
                if price > _ema200_4h and price < ema200_1d:
                    result['ema200_4h_support'] = True
        except Exception:
            pass

        # ── 周线RSI ─────────────────────────────────────────
        weekly_rsi = 50.0
        if k1w_raw and len(k1w_raw) >= 15:
            c1w = [float(k[4]) for k in k1w_raw]
            weekly_rsi = _rsi14(c1w[-15:])
        result['weekly_rsi'] = round(weekly_rsi, 1)

        # ── 大级别Fib（从90日高低点）────────────────────────
        swing_high = max(h1d[-90:]) if len(h1d) >= 90 else max(h1d)
        swing_low  = min(l1d[-60:]) if len(l1d) >= 60 else min(l1d)
        fib_levels = _compute_fib_levels(swing_high, swing_low)
        result['fib_levels'] = {k: round(v, 2) for k, v in fib_levels.items()}

        # ── 体制标签 ─────────────────────────────────────────
        if price > ema200_1d:
            regime_tag = 'BULL_EMA200'     # 牛市区间
        elif price > ema55_1d:
            regime_tag = 'RECOVERY'         # 恢复区间
        elif price > ema21_1d:
            regime_tag = 'BEAR_BOUNCE'      # 熊市反弹
        else:
            regime_tag = 'DEEP_BEAR'        # 深度熊市
        result['regime_tag'] = regime_tag

        # ── 评分计算 ─────────────────────────────────────────
        total = 0
        breakdown = {}

        # 1. EMA200日线 牛熊分界
        if price < ema200_1d:
            if signal_dir == 'LONG':
                pts = EMA200_PENALTY_LONG
                breakdown['ema200'] = f'价格低于EMA200(${ema200_1d:,.0f})做多{pts:+d}'
            else:
                pts = EMA200_BONUS_SHORT
                breakdown['ema200'] = f'价格低于EMA200(${ema200_1d:,.0f})做空{pts:+d}'
            total += pts

            # ── 1H/4H EMA200支撇层（三院审核修复 2026-07-08）─────────────────────
            # 日线EMA200下方，但已到达1H EMA200 = 短期均线支撇，不应该全局封堕
            if signal_dir == 'LONG' and result.get('ema200_1h_support'):
                _ema200_1h_v = result.get('ema200_1h', 0)
                pts_1h = +4  # 短期均线支撇，补偿部分日线惩罚
                breakdown['ema200_1h'] = f'1H EMA200(${_ema200_1h_v:,.0f})支撇做多{pts_1h:+d}'
                total += pts_1h
            if signal_dir == 'LONG' and result.get('ema200_4h_support'):
                _ema200_4h_v = result.get('ema200_4h', 0)
                pts_4h = +3  # 4H均线支撇
                breakdown['ema200_4h'] = f'4H EMA200(${_ema200_4h_v:,.0f})支撇做多{pts_4h:+d}'
                total += pts_4h
            # ──────────────────────────────────────────────────────────────────────
        else:
            if signal_dir == 'LONG':
                pts = +8
                breakdown['ema200'] = f'价格上方EMA200做多{pts:+d}'
            else:
                pts = -6
                breakdown['ema200'] = f'价格上方EMA200做空{pts:+d}'
            total += pts

        # 2. EMA55日线 趋势强度
        if price < ema55_1d:
            if signal_dir == 'LONG':
                pts = EMA55_PENALTY_LONG
                breakdown['ema55'] = f'低于EMA55(${ema55_1d:,.0f})做多{pts:+d}'
                total += pts

        # 3. 周线RSI宏观定位
        if weekly_rsi <= WEEKLY_RSI_OVERSOLD:
            if signal_dir == 'LONG':
                pts = +12
                breakdown['weekly_rsi'] = f'周线RSI={weekly_rsi:.0f}超卖接近底部做多{pts:+d}'
            else:
                pts = -8
                breakdown['weekly_rsi'] = f'周线RSI={weekly_rsi:.0f}超卖区做空{pts:+d}'
            total += pts
        elif weekly_rsi >= WEEKLY_RSI_OVERBOUGHT:
            if signal_dir == 'LONG':
                pts = +6
                breakdown['weekly_rsi'] = f'周线RSI={weekly_rsi:.0f}超买做多{pts:+d}(强势)'
            else:
                pts = -10
                breakdown['weekly_rsi'] = f'周线RSI={weekly_rsi:.0f}超买区做空{pts:+d}'
            total += pts
        elif weekly_rsi >= WEEKLY_RSI_NEUTRAL_HIGH:
            if signal_dir == 'LONG':
                pts = -8
                breakdown['weekly_rsi'] = f'周线RSI={weekly_rsi:.0f}非底部做多{pts:+d}'
                total += pts

        # 4. Fib结构定位（最关键）
        fib_note_parts = []

        # 4a. 接近Fib阻力 → 做空机会，做多惩罚
        for level_name in ['0.236', '0.382', '0.500']:
            fib_val = fib_levels[level_name]
            dist_pct = (fib_val - price) / price * 100  # 正数=上方阻力
            if 0 < dist_pct <= FIB_RESISTANCE_ZONE * 100:
                if signal_dir == 'SHORT':
                    pts = +12
                    fib_note_parts.append(f'接近Fib{level_name}阻力${fib_val:,.0f}(差{dist_pct:.1f}%)做空{pts:+d}')
                else:
                    pts = -12
                    fib_note_parts.append(f'接近Fib{level_name}阻力${fib_val:,.0f}(差{dist_pct:.1f}%)做多{pts:+d}')
                total += pts
                break  # 只取最近一条阻力

        # 4b. 价格突破Fib阻力 → 做多加分
        fib_236 = fib_levels['0.236']
        if price > fib_236:
            if signal_dir == 'LONG':
                pts = +10
                fib_note_parts.append(f'突破Fib0.236(${fib_236:,.0f})做多{pts:+d}')
                total += pts

        # 4c. 接近Fib支撑（从高点向下的Fib，即fib_levels较低的回撤线）
        # 用反向Fib：当前价格接近大跌后的支撑位
        #（这里用swing_low附近作为强支撑判断）
        if price <= swing_low * 1.05:
            if signal_dir == 'LONG':
                pts = +15
                fib_note_parts.append(f'接近历史低点${swing_low:,.0f}强支撑做多{pts:+d}')
            else:
                pts = -10
                fib_note_parts.append(f'接近历史低点做空{pts:+d}(风险高)')
            total += pts

        if fib_note_parts:
            breakdown['fib'] = ' | '.join(fib_note_parts)

        # 5. 体制标签加成
        regime_pts = {
            'BULL_EMA200':  {'LONG': +5, 'SHORT': -5},
            'RECOVERY':     {'LONG': +3, 'SHORT': -3},   # [2026-07-06] +2→+3: RECOVERY正确识别应适当加分
            'BEAR_BOUNCE':  {'LONG': -2, 'SHORT': +2},   # [2026-07-06] -3→-2: 反弹早期过度惩罚会错杀信号
            'DEEP_BEAR':    {'LONG': -8, 'SHORT': +8},
        }.get(regime_tag, {})

        if regime_pts:
            pts = regime_pts.get(signal_dir, 0)
            if pts != 0:
                breakdown['regime'] = f'{regime_tag}体制{signal_dir}{pts:+d}'
                total += pts

        # ── 汇总 ────────────────────────────────────────────
        total = int(max(-20, min(20, total)))  # [2026-07-06] 下限-25→-20：防止多项惩罚叠加极端截断错杀反弹信号
        result['score']     = total
        result['breakdown'] = breakdown

        notes = [f'{k}:{v}' for k, v in breakdown.items()]
        result['note'] = ' || '.join(notes)

        return result

    except Exception as e:
        result['note'] = f'fib_macro_engine error: {e}'
        return result


# ── 单元测试 ──────────────────────────────────────────────
if __name__ == '__main__':
    print("=== fib_macro_engine 单元测试 ===\n")

    # ETH当前场景
    import urllib.request, json

    price = float(json.loads(urllib.request.urlopen(
        'https://fapi.binance.com/fapi/v1/ticker/price?symbol=ETHUSDT', timeout=4
    ).read())['price'])

    for direction in ['LONG', 'SHORT']:
        r = fib_macro_score('ETHUSDT', price, direction)
        pass  # [静默]
        print(f"  总分: {r['score']:+d}")
        print(f"  体制: {r['regime_tag']}")
        print(f"  EMA200=${r['ema200']:,.0f}  EMA55=${r['ema55_1d']:,.0f}")
        print(f"  周线RSI={r['weekly_rsi']:.1f}")
        print(f"  Fib: {r['fib_levels']}")
        print(f"  详情:")
        for k, v in r['breakdown'].items():
            print(f"    {k}: {v}")
