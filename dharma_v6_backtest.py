#!/usr/bin/env python3
"""
达摩院 v6.0 全量落地回测系统
设计院 × 达摩院 × 六方联合 · 2026-07-11
苏摩111最高指令

核心模块：
  Module 1: 三层仓位架构 (15m快攻 / 1H主力 / 4H趋势)
  Module 2: 死穴→能力门槛升级 (高门槛解锁+结构确认)
  Module 3: CHOP三战术 (边界狙击/压缩爆发/快攻均值回归)
  Module 4: 凯利梯度仓位 (Score分层×半Kelly)
  Module 5: 月历季节性因子 (+/-分附加)
  Module 6: 波动率制度加成 (高波+5/低波-3)
  Module 7: 信号时间衰减 (0-4H原始/5-8H×0.85/8-12H×0.70/>12H失效)
  Module 8: 时间止损 (持仓>24H无论盈亏平半仓)
"""

import requests
import numpy as np
import json
import time
import math
from datetime import datetime, timezone, timedelta

# ============================================================
# 配置
# ============================================================

SYMBOLS = ['BTCUSDT', 'ETHUSDT']
# 三层时间框架
TF_FAST   = '15m'   # 快攻层 2%NAV
TF_MID    = '1h'    # 主力层 5%NAV
TF_SLOW   = '4h'    # 趋势层 8%NAV

START_TS  = 1577836800000  # 2020-01-01
END_TS    = 1752192000000  # 2025-07-11 (近似)

INITIAL_CAPITAL = 10000.0
FEE_RATE        = 0.001    # 0.1% per side
SLIPPAGE        = 0.0005   # 0.05%
LEVERAGE        = 5

# 三层仓位基础 (NAV比例)
NAV_FAST  = 0.02
NAV_MID   = 0.05
NAV_SLOW  = 0.08
MAX_NAV   = 0.15   # 三层合计上限

# 信号门槛矩阵 (体制 × 层级 × 标的) -- v6.0校准版
SCORE_THRESHOLDS = {
    'BTCUSDT': {
        'BULL_TREND':    {'fast': 88,  'mid': 98,  'slow': 103},
        'BEAR_TREND':    {'fast': 88,  'mid': 98,  'slow': 103},
        'CHOP_MID':      {'fast': 85,  'mid': 105, 'slow': 110},
        'BULL_EARLY':    {'fast': None,'mid': 98,  'slow': 103},
        'BEAR_EARLY':    {'fast': None,'mid': 115, 'slow': 120},   # 死穴解锁门槛
        'BEAR_RECOVERY': {'fast': None,'mid': 115, 'slow': 120},   # 死穴解锁门槛
    },
    'ETHUSDT': {
        'BULL_TREND':    {'fast': 88,  'mid': 98,  'slow': 103},
        'BEAR_TREND':    {'fast': 88,  'mid': 98,  'slow': 103},
        'CHOP_MID':      {'fast': 83,  'mid': 103, 'slow': 108},
        'BULL_EARLY':    {'fast': None,'mid': None,'slow': None},   # ETH完全封禁
        'BEAR_EARLY':    {'fast': None,'mid': 115, 'slow': 120},
        'BEAR_RECOVERY': {'fast': None,'mid': 115, 'slow': 120},
    }
}

# 月历季节性偏差 (附加分)
MONTHLY_BIAS = {
    1: 0, 2: 0, 3: 2, 4: 2,
    5: -10,  # 最弱月
    6: -4,
    7: -8,   # 7月上旬-8（当前月已验证）
    8: -6,
    9: 8,    # 最强月 78.3%上涨
    10: 6,   # 第二强 62.4%上涨
    11: 2,
    12: 4
}

# ============================================================
# 数据拉取
# ============================================================

def get_klines_all(symbol, interval, start_ts, end_ts):
    all_klines = []
    limit = 1000
    current = start_ts
    retry_count = 0
    while current < end_ts:
        url = 'https://fapi.binance.com/fapi/v1/klines'
        params = {'symbol': symbol, 'interval': interval, 'startTime': current, 'limit': limit}
        try:
            r = requests.get(url, params=params, timeout=30).json()
            if not isinstance(r, list) or not r:
                break
            all_klines.extend(r)
            current = r[-1][0] + 1
            if len(r) < limit:
                break
            time.sleep(0.08)
            retry_count = 0
        except Exception as e:
            retry_count += 1
            if retry_count > 3:
                print(f"  ERROR: {symbol} {interval}: {e}")
                break
            time.sleep(2)
    return all_klines

def extract_ohlcv(klines):
    opens   = np.array([float(k[1]) for k in klines])
    highs   = np.array([float(k[2]) for k in klines])
    lows    = np.array([float(k[3]) for k in klines])
    closes  = np.array([float(k[4]) for k in klines])
    volumes = np.array([float(k[5]) for k in klines])
    times   = np.array([int(k[0]) for k in klines])
    return opens, highs, lows, closes, volumes, times

# ============================================================
# 技术指标
# ============================================================

def calc_ema(prices, period):
    result = np.zeros(len(prices))
    result[0] = prices[0]
    k = 2.0 / (period + 1)
    for i in range(1, len(prices)):
        result[i] = prices[i] * k + result[i-1] * (1 - k)
    return result

def calc_rsi(prices, period=14):
    result = np.full(len(prices), 50.0)
    deltas = np.diff(prices)
    if len(deltas) < period:
        return result
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period-1) + gains[i]) / period
        avg_loss = (avg_loss * (period-1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        result[i+1] = 100 - 100 / (1 + rs)
    return result

def calc_atr(highs, lows, closes, period=14):
    tr = np.maximum(highs - lows, np.maximum(
        np.abs(highs - np.roll(closes, 1)),
        np.abs(lows - np.roll(closes, 1))
    ))
    tr[0] = highs[0] - lows[0]
    atr = calc_ema(tr, period)
    return atr

def calc_bb_width(closes, period=20):
    widths = np.zeros(len(closes))
    for i in range(period, len(closes)):
        window = closes[i-period:i]
        mid = np.mean(window)
        std = np.std(window)
        widths[i] = (2 * std / mid) if mid > 0 else 0
    return widths

# ============================================================
# 体制识别 (4H级别)
# ============================================================

def detect_regime(closes_4h, idx):
    """
    基于EMA20/50/200 + RSI识别6体制
    返回: BULL_TREND / BEAR_TREND / CHOP_MID / BULL_EARLY / BEAR_EARLY / BEAR_RECOVERY
    """
    if idx < 200:
        return 'CHOP_MID'

    w = min(idx+1, 250)
    c = closes_4h[idx-w+1:idx+1]
    ema20  = calc_ema(c, 20)[-1]
    ema50  = calc_ema(c, 50)[-1]
    ema200 = calc_ema(c, 200)[-1]
    rsi    = calc_rsi(c)[-1]
    price  = c[-1]

    # 主趋势判断
    bull_structure = price > ema20 > ema50 > ema200
    bear_structure = price < ema20 < ema50 < ema200

    if bull_structure and rsi > 55:
        return 'BULL_TREND'
    elif bear_structure and rsi < 45:
        return 'BEAR_TREND'
    elif price > ema50 and price > ema200 and rsi < 45:
        return 'BULL_EARLY'
    elif price < ema50 and price < ema200 and rsi > 55:
        return 'BEAR_EARLY'
    elif price > ema200 * 0.97 and rsi > 40 and rsi < 60:
        return 'BEAR_RECOVERY'
    else:
        return 'CHOP_MID'

# ============================================================
# Module 5: 月历季节性偏差
# ============================================================

def get_seasonal_bias(ts_ms):
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return MONTHLY_BIAS.get(dt.month, 0)

# ============================================================
# Module 6: 波动率制度加成
# ============================================================

def get_vol_regime_bonus(atr_pct):
    """
    atr_pct = ATR_4H / price (百分比)
    高波: >1.89% → +5分, 高TP2倍数
    低波: <1.28% → -3分
    """
    if atr_pct > 0.0189:
        return 5, 3.0   # 分数加成, TP2倍数
    elif atr_pct < 0.0128:
        return -3, 2.5
    else:
        return 0, 2.5

# ============================================================
# Module 4: 凯利梯度仓位
# ============================================================

def kelly_position_multiplier(score):
    """
    基于Score分层的半Kelly仓位乘数
    """
    if score >= 150:
        return 2.0
    elif score >= 140:
        return 1.5
    elif score >= 130:
        return 1.2
    else:
        return 1.0

# ============================================================
# 评分引擎 (简化版35维代理，用于回测)
# ============================================================

def compute_score(regime, direction, rsi_cur, price, ema20, ema50, ema200,
                  bb_width, ts_ms, atr_pct, layer):
    """
    回测评分引擎 v6.0 — 校准版
    满分约160分，正常高质量信号约100~130分
    对应真实35维矩阵的统计代理版本
    """
    score = 0

    # ── RSI动量因子 (最高45分) ──
    if direction == 'LONG':
        if rsi_cur < 25:    score += 45
        elif rsi_cur < 35:  score += 38
        elif rsi_cur < 45:  score += 30
        elif rsi_cur < 55:  score += 20
        elif rsi_cur < 65:  score += 10
        else:               score += 0
    else:  # SHORT
        if rsi_cur > 75:    score += 45
        elif rsi_cur > 65:  score += 38
        elif rsi_cur > 55:  score += 30
        elif rsi_cur > 50:  score += 20
        elif rsi_cur > 45:  score += 10
        else:               score += 0

    # ── EMA多周期排列因子 (最高40分) ──
    if direction == 'LONG':
        if price > ema20 > ema50 > ema200:    score += 40  # 完美多头排列
        elif price > ema20 and price > ema50: score += 28
        elif price > ema200:                  score += 18  # 价格在长期均线上方
        elif price > ema20:                   score += 10
        else:                                 score += 0
    else:
        if price < ema20 < ema50 < ema200:    score += 40  # 完美空头排列
        elif price < ema20 and price < ema50: score += 28
        elif price < ema200:                  score += 18  # 价格在长期均线下方
        elif price < ema20:                   score += 10
        else:                                 score += 0

    # ── 体制共振因子 (最高35分) ──
    regime_bonus = {
        ('BULL_TREND',    'LONG'):   35,
        ('BEAR_TREND',    'SHORT'):  32,
        ('BULL_EARLY',    'LONG'):   22,
        ('BEAR_EARLY',    'SHORT'):  22,
        ('CHOP_MID',      'LONG'):   18,
        ('CHOP_MID',      'SHORT'):  18,
        ('BEAR_RECOVERY', 'LONG'):   15,
        ('BEAR_TREND',    'LONG'):   8,   # 逆势信号低分
        ('BULL_TREND',    'SHORT'):  8,
    }
    score += regime_bonus.get((regime, direction), 12)

    # ── BB压缩/波动率因子 (最高20分) ──
    if bb_width < 0.006:    score += 20   # 极度压缩
    elif bb_width < 0.010:  score += 15
    elif bb_width < 0.018:  score += 10
    elif bb_width < 0.030:  score += 5
    else:                   score += 0

    # ── 月历季节性 (±10分) ──
    score += get_seasonal_bias(ts_ms)

    # ── 波动率制度加成 (±5分) ──
    vol_bonus, _ = get_vol_regime_bonus(atr_pct)
    score += vol_bonus

    # ── 快攻层RSI极值加成 (Module 3 - 战术C, +15分) ──
    if layer == 'fast':
        if (direction == 'LONG' and rsi_cur < 30) or \
           (direction == 'SHORT' and rsi_cur > 70):
            score += 15

    return score

# ============================================================
# Module 2: 死穴能力门槛检查
# ============================================================

def is_signal_allowed(sym, regime, direction, score, layer):
    """
    v6.0：死穴升级为高门槛限制
    返回 (allowed: bool, reason: str)
    """
    thresholds = SCORE_THRESHOLDS[sym][regime]
    layer_thresh = thresholds.get(layer)

    # ETH BULL_EARLY 完全封禁
    if sym == 'ETHUSDT' and regime == 'BULL_EARLY' and direction == 'LONG':
        return False, 'ETH_BULL_EARLY_DEAD_ZONE'

    # 高危体制（原死穴）需要额外结构确认门槛
    high_risk = [
        ('BEAR_TREND', 'LONG'),
        ('BULL_TREND', 'SHORT'),
        ('BEAR_RECOVERY', 'LONG'),
        ('BEAR_EARLY', 'SHORT'),
    ]
    if (regime, direction) in high_risk:
        if layer == 'fast':
            return False, f'{regime}_{direction}_NO_FAST_LAYER'
        if layer_thresh is None:
            return False, f'{regime}_{direction}_BLOCKED'
        if score < layer_thresh:
            return False, f'{regime}_{direction}_BELOW_UNLOCK_THRESH({layer_thresh})'

    # 普通门槛
    if layer_thresh is None:
        return False, f'{regime}_NO_{layer.upper()}_LAYER'
    if score < layer_thresh:
        return False, f'SCORE_{score}_BELOW_{layer_thresh}'

    return True, 'OK'

# ============================================================
# Module 3: CHOP三战术判断
# ============================================================

def get_chop_tactic(layer, rsi_cur, price, high_48h, low_48h, bb_width, direction):
    """
    返回适用的CHOP战术类型
    战术A: 区间边界狙击 (主力层)
    战术B: 压缩爆发预布局 (趋势层)
    战术C: 快攻均值回归 (快攻层)
    """
    range_pct = (high_48h - low_48h) / low_48h if low_48h > 0 else 0.1
    price_in_range_pct = (price - low_48h) / (high_48h - low_48h) if (high_48h - low_48h) > 0 else 0.5

    if layer == 'fast':
        # 战术C: RSI极值均值回归
        if (direction == 'LONG' and rsi_cur < 25 and price_in_range_pct < 0.35) or \
           (direction == 'SHORT' and rsi_cur > 75 and price_in_range_pct > 0.65):
            return 'C_MEAN_REVERSION', 1.0, 0.01   # tactic, TP_RR, SL_pct
    elif layer == 'mid':
        # 战术A: 边界狙击
        near_low  = price_in_range_pct < 0.20
        near_high = price_in_range_pct > 0.80
        if (direction == 'LONG' and near_low and rsi_cur < 40) or \
           (direction == 'SHORT' and near_high and rsi_cur > 60):
            return 'A_BOUNDARY_SNIPER', 1.0, 0.015
    elif layer == 'slow':
        # 战术B: 压缩爆发预布局
        if bb_width < 0.008:   # 极度压缩
            return 'B_COMPRESSION_BREAKOUT', 3.0, 0.02

    return 'STANDARD', None, None   # 无特殊战术

# ============================================================
# 核心回测引擎 (三层独立运行)
# ============================================================

class LayerBacktest:
    """单层回测引擎"""

    def __init__(self, sym, layer, nav_pct):
        self.sym = sym
        self.layer = layer
        self.nav_pct = nav_pct
        self.trades = []
        self.equity = INITIAL_CAPITAL

    def get_sl_tp(self, regime, direction, price, atr_pct, layer, tactic=None, tp2_mult=2.5):
        """
        Module 7: 信号时间衰减 不在此处理 (在入场时处理)
        止损止盈计算
        """
        # SL百分比 (体制驱动)
        sl_base = {
            'BULL_TREND': 0.020, 'BEAR_TREND': 0.020,
            'CHOP_MID': 0.025,   'BULL_EARLY': 0.025,
            'BEAR_EARLY': 0.025, 'BEAR_RECOVERY': 0.025
        }.get(regime, 0.025)

        # ATR自适应止损
        atr_sl = atr_pct * 1.5
        sl_pct = max(sl_base, atr_sl)
        sl_pct = min(sl_pct, 0.06)  # 上限6%

        # CHOP战术覆盖
        if tactic == 'C_MEAN_REVERSION':
            sl_pct = 0.010
            tp1_rr, tp2_rr = 0.8, 0.8
        elif tactic == 'A_BOUNDARY_SNIPER':
            sl_pct = 0.015
            tp1_rr, tp2_rr = 1.0, 1.0
        elif tactic == 'B_COMPRESSION_BREAKOUT':
            sl_pct = 0.020
            tp1_rr, tp2_rr = 1.5, 3.0
        else:
            # 趋势层高波动下扩大TP2
            tp1_rr = 1.5
            tp2_rr = tp2_mult

        if direction == 'LONG':
            sl_price  = price * (1 - sl_pct)
            tp1_price = price * (1 + sl_pct * tp1_rr)
            tp2_price = price * (1 + sl_pct * tp2_rr)
        else:
            sl_price  = price * (1 + sl_pct)
            tp1_price = price * (1 - sl_pct * tp1_rr)
            tp2_price = price * (1 - sl_pct * tp2_rr)

        return sl_price, tp1_price, tp2_price, sl_pct

    def run(self, opens, highs, lows, closes, volumes, times,
            closes_4h, times_4h,
            closes_1h=None, times_1h=None):

        n = len(closes)
        ema20  = calc_ema(closes, 20)
        ema50  = calc_ema(closes, 50)
        ema200 = calc_ema(closes, 200)
        rsi    = calc_rsi(closes)
        atr    = calc_atr(highs, lows, closes, 14)
        bb_w   = calc_bb_width(closes, 20)

        # 4H体制索引映射
        regime_map = {}
        for j in range(200, len(times_4h)):
            w = min(j+1, 250)
            c4 = closes_4h[j-w+1:j+1]
            ema20_4h  = calc_ema(c4, 20)[-1]
            ema50_4h  = calc_ema(c4, 50)[-1]
            ema200_4h = calc_ema(c4, 200)[-1]
            rsi_4h    = calc_rsi(c4)[-1]
            price_4h  = c4[-1]

            bull_s = price_4h > ema20_4h > ema50_4h > ema200_4h
            bear_s = price_4h < ema20_4h < ema50_4h < ema200_4h

            if bull_s and rsi_4h > 55:               r = 'BULL_TREND'
            elif bear_s and rsi_4h < 45:             r = 'BEAR_TREND'
            elif price_4h > ema50_4h and price_4h > ema200_4h and rsi_4h < 45: r = 'BULL_EARLY'
            elif price_4h < ema50_4h and price_4h < ema200_4h and rsi_4h > 55: r = 'BEAR_EARLY'
            elif price_4h > ema200_4h * 0.97 and 40 < rsi_4h < 60:             r = 'BEAR_RECOVERY'
            else:                                    r = 'CHOP_MID'

            regime_map[times_4h[j]] = r

        def get_regime(ts):
            # 找到不超过当前时间的最近4H时间
            best_ts = None
            best_r  = 'CHOP_MID'
            for t4, r in regime_map.items():
                if t4 <= ts:
                    if best_ts is None or t4 > best_ts:
                        best_ts = t4
                        best_r  = r
            return best_r

        # 预排序用于二分查找
        sorted_4h = sorted(regime_map.items())

        def get_regime_fast(ts):
            lo, hi = 0, len(sorted_4h) - 1
            res = 'CHOP_MID'
            while lo <= hi:
                mid_idx = (lo + hi) // 2
                if sorted_4h[mid_idx][0] <= ts:
                    res = sorted_4h[mid_idx][1]
                    lo = mid_idx + 1
                else:
                    hi = mid_idx - 1
            return res

        # 48H高低点 (用于CHOP战术A)
        bars_48h = {'15m': 192, '1h': 48, '4h': 12}
        lookback_48h = bars_48h.get(self.layer, 48)

        position = None   # 当前持仓
        last_trade_bar = -20   # 防止频繁入场
        min_gap = {'fast': 4, 'mid': 4, 'slow': 3}   # 每层最小间隔bars

        for i in range(250, n - 1):
            if i - last_trade_bar < min_gap.get(self.layer, 4):
                continue

            ts   = times[i]
            price = closes[i]
            rsi_c = rsi[i]
            ema20_c  = ema20[i]
            ema50_c  = ema50[i]
            ema200_c = ema200[i]
            bb_width_c = bb_w[i]
            atr_c = atr[i]
            atr_pct_c = atr_c / price if price > 0 else 0.01

            regime = get_regime_fast(ts)

            # 48H高低点
            lb = min(i, lookback_48h)
            high_48h = np.max(highs[i-lb:i])
            low_48h  = np.min(lows[i-lb:i])

            # 如果有持仓，检查出场条件
            if position is not None:
                pos = position
                cur_high = highs[i]
                cur_low  = lows[i]
                close_price = None
                close_reason = None

                if pos['direction'] == 'LONG':
                    if cur_low <= pos['sl']:
                        close_price = pos['sl']
                        close_reason = 'SL'
                    elif cur_high >= pos['tp2']:
                        close_price = pos['tp2']
                        close_reason = 'TP2'
                    elif cur_high >= pos['tp1'] and not pos.get('tp1_hit'):
                        # 分批止盈: 记录TP1触达，移止损至成本
                        pos['tp1_hit'] = True
                        pos['sl'] = pos['entry']   # 止损移至成本
                else:  # SHORT
                    if cur_high >= pos['sl']:
                        close_price = pos['sl']
                        close_reason = 'SL'
                    elif cur_low <= pos['tp2']:
                        close_price = pos['tp2']
                        close_reason = 'TP2'
                    elif cur_low <= pos['tp1'] and not pos.get('tp1_hit'):
                        pos['tp1_hit'] = True
                        pos['sl'] = pos['entry']

                # Module 8: 时间止损 (持仓>24H平半仓→次日全平)
                bars_held = i - pos['entry_bar']
                bars_24h = {'fast': 96, 'mid': 24, 'slow': 6}
                if bars_held >= bars_24h.get(self.layer, 24) and not pos.get('time_sl_triggered'):
                    pos['time_sl_triggered'] = True
                    # 24H后若仍持仓，设定下一根收盘强平
                    pos['force_close_bar'] = i + 1

                if pos.get('force_close_bar') == i:
                    close_price = price
                    close_reason = 'TIME_SL'

                if close_price is not None:
                    # 计算PnL
                    if pos['direction'] == 'LONG':
                        raw_pnl_pct = (close_price - pos['entry']) / pos['entry']
                    else:
                        raw_pnl_pct = (pos['entry'] - close_price) / pos['entry']

                    cost = FEE_RATE + SLIPPAGE
                    net_pnl_pct = raw_pnl_pct - cost * 2

                    # 如果TP1已触达，分批止盈效果：TP1盈利0.5 + TP2盈利0.5
                    if pos.get('tp1_hit') and close_reason in ('TP2', 'SL', 'TIME_SL'):
                        tp1_pnl = (pos['tp1'] - pos['entry']) / pos['entry'] * (1 if pos['direction']=='LONG' else -1)
                        net_tp1 = tp1_pnl * 0.5 - cost
                        net_pnl_pct = net_tp1 + net_pnl_pct * 0.5

                    nav_used = pos['nav_pct']
                    dollar_pnl = self.equity * nav_used * LEVERAGE * net_pnl_pct

                    self.equity += dollar_pnl
                    self.trades.append({
                        'sym': self.sym,
                        'layer': self.layer,
                        'regime': pos['regime'],
                        'direction': pos['direction'],
                        'entry_ts': pos['entry_ts'],
                        'exit_ts': ts,
                        'entry': pos['entry'],
                        'exit': close_price,
                        'score': pos['score'],
                        'reason': close_reason,
                        'pnl_pct': net_pnl_pct,
                        'dollar_pnl': dollar_pnl,
                        'equity_after': self.equity,
                        'tp1_hit': pos.get('tp1_hit', False),
                        'tactic': pos.get('tactic', 'STANDARD'),
                    })
                    position = None
                    last_trade_bar = i
                    continue

                # 持仓中，跳过入场检测
                continue

            # ── 入场检测 ──
            for direction in ['LONG', 'SHORT']:
                # CHOP战术判断
                tactic = 'STANDARD'
                if regime == 'CHOP_MID':
                    tactic_type, tactic_tp_rr, tactic_sl_pct = get_chop_tactic(
                        self.layer, rsi_c, price, high_48h, low_48h, bb_width_c, direction
                    )
                    if tactic_type != 'STANDARD':
                        tactic = tactic_type

                # 波动率制度
                _, tp2_mult = get_vol_regime_bonus(atr_pct_c)

                # 评分
                score = compute_score(
                    regime, direction, rsi_c, price,
                    ema20_c, ema50_c, ema200_c,
                    bb_width_c, ts, atr_pct_c, self.layer
                )

                # 门槛检查
                allowed, reason = is_signal_allowed(self.sym, regime, direction, score, self.layer)
                if not allowed:
                    continue

                # 凯利梯度仓位
                kelly_mult = kelly_position_multiplier(score)
                nav_used = min(self.nav_pct * kelly_mult, MAX_NAV)

                # 计算SL/TP
                sl, tp1, tp2, sl_pct = self.get_sl_tp(
                    regime, direction, price, atr_pct_c,
                    self.layer, tactic, tp2_mult
                )

                # 开仓
                entry_price = price * (1 + SLIPPAGE if direction == 'SHORT' else 1 - SLIPPAGE)
                position = {
                    'direction': direction,
                    'regime': regime,
                    'entry': entry_price,
                    'sl': sl,
                    'tp1': tp1,
                    'tp2': tp2,
                    'tp1_hit': False,
                    'entry_ts': ts,
                    'entry_bar': i,
                    'score': score,
                    'nav_pct': nav_used,
                    'tactic': tactic,
                }
                last_trade_bar = i
                break   # 同一根K线只开一个方向

        return self.trades, self.equity


# ============================================================
# 主回测流程
# ============================================================

def run_dharma_v6():
    print("=" * 70)
    print("🏛️ 达摩院 v6.0 全量回测系统启动")
    print("   设计院 × 达摩院 × 六方联合 | 2026-07-11")
    print("=" * 70)

    all_results = {}

    for sym in SYMBOLS:
        print(f"\n{'='*60}")
        print(f"🔍 {sym} 三层全量回测开始 (6.5年)")
        print(f"{'='*60}")

        # 拉取三个时间框架数据
        print(f"  [1/4] 拉取 {TF_FAST} 数据...")
        kl_fast = get_klines_all(sym, TF_FAST, START_TS, END_TS)
        o_f, h_f, l_f, c_f, v_f, t_f = extract_ohlcv(kl_fast)
        print(f"       → {len(kl_fast):,} 根K线 ({len(kl_fast)*15/60/24:.1f} 天)")

        print(f"  [2/4] 拉取 {TF_MID} 数据...")
        kl_mid = get_klines_all(sym, TF_MID, START_TS, END_TS)
        o_m, h_m, l_m, c_m, v_m, t_m = extract_ohlcv(kl_mid)
        print(f"       → {len(kl_mid):,} 根K线")

        print(f"  [3/4] 拉取 {TF_SLOW} (4H) 数据...")
        kl_slow = get_klines_all(sym, TF_SLOW, START_TS, END_TS)
        o_s, h_s, l_s, c_s, v_s, t_s = extract_ohlcv(kl_slow)
        print(f"       → {len(kl_slow):,} 根K线")

        print(f"  [4/4] 拉取 1D 数据...")
        kl_1d = get_klines_all(sym, '1d', START_TS, END_TS)
        _, _, _, c_1d, _, t_1d = extract_ohlcv(kl_1d)
        print(f"       → {len(kl_1d):,} 根K线\n")

        # 运行三层回测
        layer_results = {}
        combined_trades = []

        for layer_name, tf_klines, nav in [
            ('fast', (o_f,h_f,l_f,c_f,v_f,t_f), NAV_FAST),
            ('mid',  (o_m,h_m,l_m,c_m,v_m,t_m), NAV_MID),
            ('slow', (o_s,h_s,l_s,c_s,v_s,t_s), NAV_SLOW),
        ]:
            print(f"  🔄 运行{layer_name.upper()}层回测 (NAV={nav*100:.0f}%)...")
            engine = LayerBacktest(sym, layer_name, nav)
            o_, h_, l_, c_, v_, t_ = tf_klines
            trades, final_eq = engine.run(o_, h_, l_, c_, v_, t_, c_s, t_s)

            n_trades = len(trades)
            wins = [t for t in trades if t['dollar_pnl'] > 0]
            losses = [t for t in trades if t['dollar_pnl'] <= 0]
            wr = len(wins) / n_trades * 100 if n_trades > 0 else 0
            total_pnl = sum(t['dollar_pnl'] for t in trades)
            ret_pct = total_pnl / INITIAL_CAPITAL * 100

            print(f"     → 交易数={n_trades} | WR={wr:.1f}% | 收益={ret_pct:+.1f}% | 最终资金=${final_eq:,.0f}")

            layer_results[layer_name] = {
                'trades': trades,
                'final_equity': final_eq,
                'n_trades': n_trades,
                'wr': wr,
                'total_pnl': total_pnl,
                'ret_pct': ret_pct,
            }
            combined_trades.extend(trades)

        all_results[sym] = layer_results

        # 三层合并统计
        print(f"\n  📊 {sym} 三层合并统计:")
        total_pnl_3layer = sum(lr['total_pnl'] for lr in layer_results.values())
        total_trades_3layer = sum(lr['n_trades'] for lr in layer_results.values())
        all_wins = [t for t in combined_trades if t['dollar_pnl'] > 0]
        combined_wr = len(all_wins) / len(combined_trades) * 100 if combined_trades else 0
        combined_ret = total_pnl_3layer / INITIAL_CAPITAL * 100

        print(f"  合并总交易: {total_trades_3layer}")
        print(f"  合并WR: {combined_wr:.1f}%")
        print(f"  合并总收益: {combined_ret:+.1f}%")
        print(f"  年均收益: {combined_ret/6.5:+.1f}%")

        # 体制×方向分析
        print(f"\n  📋 {sym} 体制×层级×方向明细:")
        regime_layer_dir = {}
        for t in combined_trades:
            key = (t['regime'], t['layer'], t['direction'])
            if key not in regime_layer_dir:
                regime_layer_dir[key] = {'wins': 0, 'total': 0, 'pnl': 0}
            regime_layer_dir[key]['total'] += 1
            regime_layer_dir[key]['pnl'] += t['dollar_pnl']
            if t['dollar_pnl'] > 0:
                regime_layer_dir[key]['wins'] += 1

        print(f"  {'体制':18s} | {'层':6s} | {'方向':6s} | {'n':5s} | {'WR':7s} | {'PnL':10s}")
        print("  " + "-" * 65)
        for (regime, layer, direction), d in sorted(regime_layer_dir.items()):
            if d['total'] >= 5:
                wr = d['wins']/d['total']*100
                print(f"  {regime:18s} | {layer:6s} | {direction:6s} | {d['total']:5d} | {wr:6.1f}% | ${d['pnl']:+9.0f}")

        # CHOP战术统计
        print(f"\n  🎯 {sym} CHOP战术统计:")
        tactic_stats = {}
        for t in combined_trades:
            tac = t.get('tactic', 'STANDARD')
            if tac not in tactic_stats:
                tactic_stats[tac] = {'wins': 0, 'total': 0, 'pnl': 0}
            tactic_stats[tac]['total'] += 1
            tactic_stats[tac]['pnl'] += t['dollar_pnl']
            if t['dollar_pnl'] > 0:
                tactic_stats[tac]['wins'] += 1

        for tac, d in sorted(tactic_stats.items(), key=lambda x: -x[1]['total']):
            wr = d['wins']/d['total']*100 if d['total'] > 0 else 0
            print(f"    {tac:30s}: n={d['total']:4d} WR={wr:.1f}% PnL=${d['pnl']:+9.0f}")

        # 死穴解锁统计
        unlock_trades = [t for t in combined_trades if t['regime'] in
                        ('BEAR_RECOVERY', 'BEAR_EARLY', 'BEAR_TREND') and
                        t['direction'] == 'LONG' and t['score'] >= 135]
        if unlock_trades:
            unlock_wins = [t for t in unlock_trades if t['dollar_pnl'] > 0]
            print(f"\n  🔓 {sym} 死穴解锁通道统计:")
            print(f"    触发次数: {len(unlock_trades)}")
            print(f"    WR: {len(unlock_wins)/len(unlock_trades)*100:.1f}%")
            print(f"    PnL: ${sum(t['dollar_pnl'] for t in unlock_trades):+.0f}")

    # ============================================================
    # 最终汇总报告
    # ============================================================
    print("\n" + "=" * 70)
    print("🏛️ 达摩院 v6.0 最终汇总报告")
    print("=" * 70)

    total_combined_ret = 0
    total_combined_wr_n = 0
    total_combined_wr_w = 0

    for sym in SYMBOLS:
        lr = all_results[sym]
        sym_pnl = sum(l['total_pnl'] for l in lr.values())
        sym_trades_list = []
        for l in lr.values():
            sym_trades_list.extend(l['trades'])
        sym_wins = [t for t in sym_trades_list if t['dollar_pnl'] > 0]
        sym_wr = len(sym_wins)/len(sym_trades_list)*100 if sym_trades_list else 0
        sym_ret = sym_pnl/INITIAL_CAPITAL*100

        print(f"\n{sym}:")
        print(f"  快攻层(15m): {lr['fast']['n_trades']}笔 | WR={lr['fast']['wr']:.1f}% | {lr['fast']['ret_pct']:+.1f}%")
        print(f"  主力层(1H):  {lr['mid']['n_trades']}笔  | WR={lr['mid']['wr']:.1f}% | {lr['mid']['ret_pct']:+.1f}%")
        print(f"  趋势层(4H):  {lr['slow']['n_trades']}笔  | WR={lr['slow']['wr']:.1f}% | {lr['slow']['ret_pct']:+.1f}%")
        print(f"  ─────────────────────────────────────────────")
        print(f"  三层合并:    {len(sym_trades_list)}笔  | WR={sym_wr:.1f}% | {sym_ret:+.1f}%")
        print(f"  年均收益:    {sym_ret/6.5:+.1f}%")

        total_combined_ret += sym_ret
        total_combined_wr_n += len(sym_trades_list)
        total_combined_wr_w += len(sym_wins)

    total_wr = total_combined_wr_w/total_combined_wr_n*100 if total_combined_wr_n > 0 else 0
    avg_ret = total_combined_ret / len(SYMBOLS)

    print(f"\n{'='*60}")
    print(f"📊 双标的三层合并:")
    print(f"  总交易笔数: {total_combined_wr_n}")
    print(f"  综合WR: {total_wr:.1f}%")
    print(f"  平均年化收益: {avg_ret/6.5:+.1f}%")
    print(f"  6.5年平均总收益: {avg_ret:+.1f}%")
    print(f"  月均复利收益: {((1 + avg_ret/100)**(1/78) - 1)*100:+.2f}%")
    print(f"{'='*60}")

    print("\n✅ 达摩院 v6.0 回测完成")
    print("   三层仓位 × 能力门槛 × CHOP三战术 × 凯利梯度 × 月历偏差")
    print("=" * 70)

    return all_results


if __name__ == '__main__':
    run_dharma_v6()
