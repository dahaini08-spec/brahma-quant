# ⚠️ Brahma-Quant Open Source v3.0
# PRO私有内容: 5-regime分类器阈值（实盘精调值，Pro私有）
# 开源版：框架公开，参数需自行调参或获取Pro版

#!/usr/bin/env python3
"""
regime_scorer.py — 梵天三层体制概率评估 v1.0
设计院 2026-06-10

【大道至简】
  输入：symbol
  输出：bull_prob / bear_prob / chop_prob（三者之和=1.0）
         + phase（4H阶段）+ momentum（1H动量）+ 置信度

【三层体制】
  第一层 日线主趋势：RSI + 价格结构 + EMA斜率
  第二层 4H阶段：高低点序列 + EMA位置
  第三层 1H动量：连续K线方向 + RSI动量

【设计原则】
  - 独立于brahma_core，零依赖
  - 结果缓存30分钟（同symbol同interval不重复调用）
  - 只用公开可靠的指标，不用复杂模型
  - 输出概率而非分数（更直观，便于体制权重计算）
"""


from brahma_brain.math_utils import _ema, _rsi, calc_rsi, rsi, ema  # 统一数学库

import json
import time
import urllib.request
from pathlib import Path

FAPI   = 'https://fapi.binance.com'
_CACHE = {}          # {symbol: {ts, result}} | 缓存结构：标的 → {时间戳, 结果}
_TTL   = 1800        # 30分钟缓存


# ══════════════════════════════════════════════════════════════
# 基础工具
# ══════════════════════════════════════════════════════════════

def _klines(symbol: str, interval: str, limit: int = 100) -> list:  # [FIX 2026-06-14] 30→100 保证Wilder RSI初始化稳定
    url = f'{FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=6) as r:
        raw = json.loads(r.read())
        return [{'o': float(k[1]), 'h': float(k[2]), 'l': float(k[3]),
                 'c': float(k[4]), 'v': float(k[5])} for k in raw]


def _rsi(closes: list, period: int = 14) -> float:
    """Wilder RSI — 与 market_state.rsi 算法对齐（修复根因：旧SMA简化版与Wilder相差最大13点）"""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    # Wilder平滑EMA：前period根初始化均值，然后逐根EMA更新
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    return round(100 - 100 / (1 + ag / max(al, 1e-9)), 2)


def _ema(values: list, period: int) -> float:
    if len(values) < period:
        return values[-1] if values else 0
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return round(e, 6)


def _higher_highs(klines: list, n: int = 5) -> bool:
    highs = [k['h'] for k in klines[-n:]]
    return highs[-1] > highs[0] and highs[-1] > highs[-2]


def _lower_lows(klines: list, n: int = 5) -> bool:
    lows = [k['l'] for k in klines[-n:]]
    return lows[-1] < lows[0] and lows[-1] < lows[-2]


# ══════════════════════════════════════════════════════════════
# 第一层：日线主趋势
# ══════════════════════════════════════════════════════════════

def _score_daily(kd: list) -> dict:
    """
    返回 bull_pts / bear_pts，各维度得分
    """
    bull, bear = 0, 0
    closes = [k['c'] for k in kd]
    price  = closes[-1]

    # RSI | RSI指标评分
    rsi_1d = _rsi(closes)
    if rsi_1d > 60:
        bull += 25
    elif rsi_1d > 50:
        bull += 10
    elif rsi_1d < 30:
        bear += 25
    elif rsi_1d < 40:
        bear += 10

    # 价格 vs EMA50
    ema50 = _ema(closes, 50)
    if price > ema50 * 1.01:
        bull += 20
    elif price > ema50:
        bull += 8
    elif price < ema50 * 0.99:
        bear += 20
    else:
        bear += 8

    # 价格 vs EMA20
    ema20 = _ema(closes, 20)
    if price > ema20:
        bull += 15
    else:
        bear += 15

    # 高低点结构（最近10根日线）
    if _higher_highs(kd, 8):
        bull += 20
    if _lower_lows(kd, 8):
        bear += 20

    # 动量：最近3根日线方向
    last3 = kd[-3:]
    bull_candles = sum(1 for k in last3 if k['c'] > k['o'])
    bear_candles = sum(1 for k in last3 if k['c'] < k['o'])
    if bull_candles >= 2:
        bull += 10
    if bear_candles >= 2:
        bear += 10

    return {'bull': bull, 'bear': bear, 'rsi_1d': rsi_1d, 'ema20': ema20, 'ema50': ema50}


# ══════════════════════════════════════════════════════════════
# 第二层：4H阶段
# ══════════════════════════════════════════════════════════════

def _score_4h(k4: list) -> dict:
    """
    输出：phase + bull_pts + bear_pts
    phase: DOWNTREND(下跌趋势) / UPTREND(上涨趋势) / PULLBACK_UP(上升途中回调) / PULLBACK_DN(下降途中反弹) / BOTTOMING(筑底) / TOPPING(顶部) / CHOP(震荡)
    """
    bull, bear = 0, 0
    closes = [k['c'] for k in k4]
    price  = closes[-1]

    rsi_4h = _rsi(closes)
    ema20  = _ema(closes, 20)
    ema9   = _ema(closes, 9)

    # EMA位置
    above_ema20 = price > ema20
    above_ema9  = price > ema9

    # 结构
    hh = _higher_highs(k4, 6)
    ll = _lower_lows(k4, 6)

    # 确定4H阶段
    if ll and rsi_4h < 45 and not above_ema20:
        phase = 'DOWNTREND'     # 下跌趋势：低点持续走低 + RSI弱 + 价格在EMA20下方
        bear += 35
    elif hh and rsi_4h > 55 and above_ema20:
        phase = 'UPTREND'       # 上涨趋势：高点持续走高 + RSI强 + 价格在EMA20上方
        bull += 35
    elif not ll and rsi_4h > 40 and rsi_4h < 60 and above_ema20:
        phase = 'PULLBACK_UP'   # 上升途中回调：EMA20支撑，主趋势仍向上
        bull += 20
        bear += 10
    elif not hh and rsi_4h > 40 and rsi_4h < 60 and not above_ema20:
        phase = 'PULLBACK_DN'   # 下降途中反弹：EMA20压制，主趋势仍向下
        bull += 10
        bear += 20
    elif rsi_4h < 35 and not ll:
        phase = 'BOTTOMING'     # 筑底：RSI超卖但低点未继续走低，可能反转
        bull += 25
        bear += 5
    elif rsi_4h > 70 and not hh:
        phase = 'TOPPING'       # 顶部：RSI超买但高点未继续走高，可能回落
        bull += 5
        bear += 25
    else:
        phase = 'CHOP'          # 震荡：方向不明，多空均衡
        bull += 5
        bear += 5

    # RSI附加分
    if rsi_4h > 60: bull += 10
    elif rsi_4h < 40: bear += 10

    # EMA附加
    if above_ema9 and above_ema20: bull += 10
    elif not above_ema9 and not above_ema20: bear += 10

    return {'bull': bull, 'bear': bear, 'phase': phase,
            'rsi_4h': rsi_4h, 'ema20_4h': ema20}


# ══════════════════════════════════════════════════════════════
# 第三层：1H动量
# ══════════════════════════════════════════════════════════════

def _score_1h(k1: list) -> dict:
    bull, bear = 0, 0
    closes = [k['c'] for k in k1]
    price  = closes[-1]

    rsi_1h = _rsi(closes)
    ema20  = _ema(closes, 20)

    # RSI | RSI指标评分
    if rsi_1h > 55:   bull += 20
    elif rsi_1h > 50: bull += 8
    elif rsi_1h < 45: bear += 20
    elif rsi_1h < 50: bear += 8

    # 连续K线
    last4 = k1[-4:]
    bull_c = sum(1 for k in last4 if k['c'] > k['o'])
    bear_c = sum(1 for k in last4 if k['c'] < k['o'])
    if bull_c >= 3:   bull += 20
    elif bull_c >= 2: bull += 8
    if bear_c >= 3:   bear += 20
    elif bear_c >= 2: bear += 8

    # Higher High / Higher Low | 高点抬升/低点抬升结构判断
    highs = [k['h'] for k in k1[-6:]]
    lows  = [k['l'] for k in k1[-6:]]
    hh_1h = highs[-1] > highs[-3]
    hl_1h = lows[-1] > lows[-3]
    ll_1h = lows[-1] < lows[-3]
    lh_1h = highs[-1] < highs[-3]

    if hh_1h and hl_1h: bull += 15
    if ll_1h and lh_1h: bear += 15

    # EMA位置
    if price > ema20: bull += 10
    else:             bear += 10

    momentum = 'BULLISH' if bull > bear + 10 else ('BEARISH' if bear > bull + 10 else 'NEUTRAL')  # BULLISH=偏多 / BEARISH=偏空 / NEUTRAL=中性

    return {'bull': bull, 'bear': bear, 'momentum': momentum,
            'rsi_1h': rsi_1h, 'hh': hh_1h, 'hl': hl_1h}


# ══════════════════════════════════════════════════════════════
# 主入口：三层综合
# ══════════════════════════════════════════════════════════════

def score(symbol: str, force: bool = False, vol_ratio: float = None) -> dict:
    """
    三层体制概率评估

    返回：
      bull_prob   : float 0.0~1.0
      bear_prob   : float 0.0~1.0
      chop_prob   : float 0.0~1.0
      primary     : 'BULL'|'BEAR'|'CHOP'
      confidence  : float 0.0~1.0（最高概率 vs 第二高概率的差距）
      phase       : 4H阶段
      momentum    : 1H动量方向
      rsi_1d/4h/1h: RSI参考
      multiplier  : dict SHORT/LONG 体制乘数（0.5~1.5）
    """
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'

    # 缓存检查
    now = time.time()
    if not force and sym in _CACHE and now - _CACHE[sym]['ts'] < _TTL:
        return _CACHE[sym]['result']

    # 拉数据
    kd = _klines(sym, '1d', 60)
    k4 = _klines(sym, '4h', 100)  # [FIX 2026-06-14] Wilder RSI需要足够K线
    k1 = _klines(sym, '1h', 100)  # [FIX 2026-06-14] Wilder RSI需要足够K线

    d = _score_daily(kd)
    h = _score_4h(k4)
    m = _score_1h(k1)

    # 三层加权合并（日线权重最高）
    # 权重：日线40% / 4H35% / 1H25%
    bull_raw = d['bull'] * 0.40 + h['bull'] * 0.35 + m['bull'] * 0.25
    bear_raw = d['bear'] * 0.40 + h['bear'] * 0.35 + m['bear'] * 0.25

    # 震荡判断：多空势均力敌
    total    = bull_raw + bear_raw
    diff_pct = abs(bull_raw - bear_raw) / max(total, 1)
    chop_raw = max(0, 30 * (1 - diff_pct * 3))  # 差距越小，震荡分越高

    # 归一化为概率
    grand_total = bull_raw + bear_raw + chop_raw
    bull_prob = round(bull_raw / max(grand_total, 1), 3)
    bear_prob = round(bear_raw / max(grand_total, 1), 3)
    chop_prob = round(1 - bull_prob - bear_prob, 3)
    chop_prob = max(chop_prob, 0)

    # [v25.2 网格最优 2026-06-14] vol降权 v_looser
    # 依据：167,200组合全搜，v_looser Top20 100%占位
    # 阈值：vl=0.20（原0.30），vvl=0.05（原0.10），收敛比20%/50%（原30%/60%）
    if vol_ratio is not None and vol_ratio > 0:
        if vol_ratio < 0.05:
            bull_prob = round(bull_prob * 0.50 + 0.333 * 0.50, 3)
            bear_prob = round(bear_prob * 0.50 + 0.333 * 0.50, 3)
        elif vol_ratio < 0.20:
            bull_prob = round(bull_prob * 0.80 + 0.333 * 0.20, 3)
            bear_prob = round(bear_prob * 0.80 + 0.333 * 0.20, 3)
        chop_prob = max(round(1 - bull_prob - bear_prob, 3), 0)

    # 主体制
    probs   = {'BULL': bull_prob, 'BEAR': bear_prob, 'CHOP': chop_prob}
    primary = max(probs, key=probs.get)
    sorted_probs = sorted(probs.values(), reverse=True)
    confidence = round(sorted_probs[0] - sorted_probs[1], 3)

    # 体制乘数（用于仓位/权重修正）
    # 顺势=1.5，中性=1.0，逆势=0.5
    def _mult(direction: str) -> float:
        # [大样本修正 2026-06-11]
        # 旧: ≥55%→1.5 / ≥40%→1.0 / else→0.5（熊市35-45%时SHORT永远×0.5，无法过门槛）
        # 新: ≥50%→1.5 / ≥33%→1.0 / 震荡→0.7 / 逆势→0.5
        # 依据: 大样本12万笔验证，BEAR_TREND(熊市趋势) SHORT WR=54%，不应被体制乘数封死
        if direction == 'LONG':  # 做多乘数
            # 铁证：熊市做多是宪法级死穴（225K+样本验证）
            # BEAR_EARLY_LONG WR=49.9% avgPnL=-0.139 / BEAR_TREND_LONG WR=45.6% avgPnL=-0.218
            if bear_prob >= 0.55: return 0.0    # 熊市初期/趋势→做多硬封禁，乘数归零
            if bull_prob >= 0.50: return 1.5    # 牛市趋势（BULL_TREND）→强顺势，满仓加速
            if bull_prob >= 0.40: return 1.2    # 牛市偏强→较强顺势
            if bull_prob >= 0.33: return 1.0    # 弱牛市→中性顺势
            if chop_prob >= 0.40: return 0.7    # 震荡区间→降权
            return 0.5                          # 逆势→减半
        else:  # SHORT 做空乘数
            if bear_prob >= 0.50: return 1.5    # 熊市趋势（BEAR_TREND）→强顺势，满仓加速
            if bear_prob >= 0.42: return 1.0    # 熊市初期（BEAR_EARLY）→中性顺势
            if chop_prob >= 0.40: return 0.7    # 震荡
            return 0.5                          # 逆势

    # 计算regime标签（统一字符串，与market_state对齐）
    # [v25.2 网格最优 2026-06-14] 全门槛更新，alpha体制占比 70%→92.1%
    # bear: hi=0.60(不变) / mid=0.55(↑+0.10) / lo=0.42(↑+0.09)
    # bull: hi=0.50(↓-0.10) / mid=0.38(↓-0.07)
    # 依据：167,200组合盲测，BTC+ETH 19000条采样，Top20 100%收敛
    if bear_prob >= 0.60:   _regime_label = 'BEAR_TREND'     # 熊市趋势：bear_prob≥60%，做空最佳体制 EV+0.182
    elif bear_prob >= 0.55: _regime_label = 'BEAR_EARLY'     # 熊市初期：bear_prob 55~60%，趋势形成中
    elif bear_prob >= 0.42: _regime_label = 'BEAR_RECOVERY'  # 熊市反弹：bear_prob 42~55%，做多反直觉alpha EV+0.255
    elif bull_prob >= 0.50: _regime_label = 'BULL_TREND'     # 牛市趋势：bull_prob≥50%，做多最佳体制 EV+0.242
    elif bull_prob >= 0.38: _regime_label = 'BULL_EARLY'     # 牛市初期：bull_prob 38~50%，趋势形成中
    elif chop_prob >= 0.40: _regime_label = 'CHOP_HIGH'      # 强震荡：方向极不明确，避免交易
    else:                   _regime_label = 'CHOP_MID'       # 弱震荡：多空均衡，方向待确认

    result = {
        'symbol':     sym,
        'regime':     _regime_label,   # [Fix-Regime-SSOT] 统一字段
        'bull_prob':  bull_prob,
        'bear_prob':  bear_prob,
        'chop_prob':  chop_prob,
        'primary':    primary,
        'confidence': confidence,
        'phase':      h['phase'],
        'momentum':   m['momentum'],
        'rsi_1d':     d['rsi_1d'],
        'rsi_4h':     h['rsi_4h'],
        'rsi_1h':     m['rsi_1h'],
        'hh_1h':      m['hh'],
        'hl_1h':      m['hl'],
        'multiplier': {
            'LONG':  _mult('LONG'),
            'SHORT': _mult('SHORT'),
        },
        'ts': now,
    }

    _CACHE[sym] = {'ts': now, 'result': result}
    return result


def format_regime(r: dict) -> str:
    bull_bar = '█' * int(r['bull_prob'] * 20)
    bear_bar = '█' * int(r['bear_prob'] * 20)
    chop_bar = '█' * int(r['chop_prob'] * 20)
    return (
        f"体制评估 {r['symbol']}\n"
        f"  🟢 牛市 {r['bull_prob']:.1%} {bull_bar}\n"
        f"  🔴 熊市 {r['bear_prob']:.1%} {bear_bar}\n"
        f"  🟡 震荡 {r['chop_prob']:.1%} {chop_bar}\n"
        f"  主体制={r['primary']} 置信={r['confidence']:.1%}\n"
        f"  4H阶段={r['phase']} 1H动量={r['momentum']}\n"
        f"  RSI 1D={r['rsi_1d']:.1f} 4H={r['rsi_4h']:.1f} 1H={r['rsi_1h']:.1f}\n"
        f"  乘数 LONG×{r['multiplier']['LONG']} SHORT×{r['multiplier']['SHORT']}"
    )


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTC'
    r = score(sym, force=True)
    print(format_regime(r))
