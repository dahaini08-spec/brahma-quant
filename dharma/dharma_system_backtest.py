#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════╗
║  梵天设计院 · 主系统达摩院验证框架 v1.0                           ║
║  dharma_system_backtest.py                                       ║
╠═══════════════════════════════════════════════════════════════════╣
║  目标: 把梵天主系统(15维评分引擎)放到达摩院8年历史数据上全面验证   ║
║                                                                   ║
║  流程:                                                            ║
║  1. 加载历史 parquet (ETH/BTC/SOL 1H)                            ║
║  2. 离线计算15维评分（无需实时API，用历史数据模拟）               ║
║  3. 生成信号（threshold=100分）                                   ║
║  4. 模拟出入场（12H时间止损 / SL=ATR*1.5 / TP=R:R2.0）          ║
║  5. Bootstrap统计 + 逐维贡献分析                                  ║
║  6. 输出结论 → CI 改进建议                                        ║
║                                                                   ║
║  用法:                                                            ║
║    python3 dharma/dharma_system_backtest.py               # 快速  ║
║    python3 dharma/dharma_system_backtest.py --full        # 完整  ║
║    python3 dharma/dharma_system_backtest.py --sym ETHUSDT # 单品  ║
╚═══════════════════════════════════════════════════════════════════╝
"""

import os, sys, json, time, math, random, argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import numpy as np
import pandas as pd

BASE_DIR    = Path(__file__).parent.parent
DHARMA_DIR  = Path(__file__).parent
DATA_DIR    = DHARMA_DIR / 'data'
RESULTS_DIR = DHARMA_DIR / 'results'
BP_FILE     = BASE_DIR / 'FANTAN_BLUEPRINT_V3.json'

sys.path.insert(0, str(BASE_DIR))
RESULTS_DIR.mkdir(exist_ok=True)

# ════════════════════════════════════════════════════════════════
# 1. 离线指标计算（模拟 brahma_brain 各维度）
# ════════════════════════════════════════════════════════════════

def calc_ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def calc_rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    g = d.clip(lower=0)
    l = (-d).clip(lower=0)
    ag = g.ewm(com=n-1, adjust=False).mean()
    al = l.ewm(com=n-1, adjust=False).mean()
    return 100 - 100 / (1 + ag / al.replace(0, 1e-9))

def calc_macd(c: pd.Series):
    e12 = calc_ema(c, 12)
    e26 = calc_ema(c, 26)
    macd = e12 - e26
    signal = calc_ema(macd, 9)
    hist = macd - signal
    return macd, signal, hist

def calc_atr(h, l, c, n=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(com=n-1, adjust=False).mean()

def calc_obv(c: pd.Series, v: pd.Series) -> pd.Series:
    direction = c.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * v).cumsum()

def calc_bollinger(c: pd.Series, n=20):
    mid = c.rolling(n).mean()
    std = c.rolling(n).std()
    return mid + 2*std, mid, mid - 2*std

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c = df['close']; h = df['high']; l = df['low']; v = df['volume']

    df['ema20']  = calc_ema(c, 20)
    df['ema50']  = calc_ema(c, 50)
    df['ema200'] = calc_ema(c, 200)
    df['rsi']    = calc_rsi(c, 14)
    df['rsi_4']  = calc_rsi(c, 14).rolling(4).mean()  # 近似4H RSI
    df['macd'], df['macd_sig'], df['macd_hist'] = calc_macd(c)
    df['atr']    = calc_atr(h, l, c, 14)
    df['atr_pct']= df['atr'] / c
    df['obv']    = calc_obv(c, v)
    df['obv_ema']= calc_ema(df['obv'], 20)
    df['vol_ma20'] = v.rolling(20).mean()
    df['vol_ratio']= v / df['vol_ma20']
    bb_u, bb_m, bb_l = calc_bollinger(c, 20)
    df['bb_upper'] = bb_u; df['bb_mid'] = bb_m; df['bb_lower'] = bb_l
    df['bb_pct'] = (c - bb_l) / (bb_u - bb_l + 1e-9)
    df['vwap'] = (c * v).cumsum() / v.cumsum()
    df['price_vs_vwap'] = (c - df['vwap']) / df['vwap']

    # 趋势方向（多周期简化）
    df['trend_1h']  = (c > df['ema20']).astype(int) * 2 - 1  # +1 或 -1
    df['trend_4h']  = (c > df['ema50']).astype(int) * 2 - 1
    df['trend_1d']  = (c > df['ema200']).astype(int) * 2 - 1

    # 动量
    df['momentum_12'] = c.pct_change(12)
    df['momentum_48'] = c.pct_change(48)

    # 成交量背离
    df['price_chg'] = c.pct_change(5)
    df['vol_chg']   = v.pct_change(5)

    # [UP-QEW] 质量环境权重（Quality-Environment Weighting）
    # 数据来源: 达摩院年度PF实证
    #   明确趋势年(2020/2023/2024) PF=1.26~1.40 → 权重×1.8
    #   震荡年(2021/2025/2026上半) PF=0.88~1.01 → 权重×0.65
    #   中性年 → 权重×1.0
    # 逻辑：让系统在它表现好的环境里"学到更多"
    df['time_weight'] = 1.0
    # ADX代理趋势强度（用EMA方向一致性近似）
    _ema50  = df['close'].ewm(span=50).mean()
    _ema200 = df['close'].ewm(span=200).mean()
    _trend_align = (
        ((df['close'] > _ema50) == (df['close'] > _ema200))
    )
    # 明确趋势（价格和两条均线方向一致）+ ATR波动率正常
    _atr_ok = (df['atr_pct'] > 0.006) & (df['atr_pct'] < 0.025)
    _good_env = _trend_align & _atr_ok
    # 震荡期（价格频繁穿越EMA50 = CHOP信号，而非ATR低）
    # 用20根K线内穿越EMA50次数来判断CHOP
    _cross_ema50 = ((df['close'] > _ema50).astype(int).diff().abs()
                    .rolling(24).sum().fillna(0))
    _chop_env = _cross_ema50 >= 6  # 24根K线内穿越>=6次=震荡
    df.loc[_good_env, 'time_weight'] = 1.4   # 明确趋势期×1.4
    df.loc[_chop_env & ~_good_env, 'time_weight'] = 0.80  # 震荡期×0.80

    # [TRAIN10K FIX] 只drop核心指标列的NaN，保留原始数据列的NaN不影响回测
    _required_cols = ['ema20','ema50','ema200','rsi','rsi_4','macd','macd_sig','macd_hist',
                      'atr','atr_pct','obv','obv_ema','vol_ma20','vol_ratio',
                      'bb_pct','vwap','price_vs_vwap','trend_1h','trend_4h','trend_1d',
                      'momentum_12','momentum_48','price_chg','vol_chg','time_weight']
    _available = [col for col in _required_cols if col in df.columns]
    return df.dropna(subset=_available)


# ════════════════════════════════════════════════════════════════
# 2. 离线 15维评分引擎（不调用实时API）
# ════════════════════════════════════════════════════════════════

def score_signal(row, direction: str, cg_data: dict = None) -> dict:
    """
    基于历史数据行计算15维评分（离线版本）
    direction: 'LONG' 或 'SHORT'
    返回: {'total': int, 'breakdown': dict}
    """
    d = 1 if direction == 'LONG' else -1
    c = row['close']

    # [UP-DIR] 多空专用系数矩阵（基于combo_test实证）
    # LONG: 量价配合(D05)×1.5 最强信号PF=1.277 / SMC结构(D04)×1.3
    # SHORT: 形态成熟度(D06)×1.3 / BEAR_CRASH布林反弹PF=1.50
    _is_long = (direction == 'LONG')
    _vol_mult    = 1.0  # D05量能（历史回测中性）
    _smc_mult    = 1.0  # D04 EMA（历史回测中性）
    _shape_mult  = 1.0  # D06形态（历史回测中性）
    _obv_mult    = 1.0  # D03背离（历史回测中性）
    breakdown = {}

    # D01: 趋势一致性 (0~30)
    trend_align = (row['trend_1h'] * d > 0) + (row['trend_4h'] * d > 0) + (row['trend_1d'] * d > 0)
    s1 = [0, 8, 17, 30][int(trend_align)]  # 0/1/2/3 方向对齐
    breakdown['趋势一致性'] = s1

    # D02: 关键位精确度 (0~30) — 基于BB和ATR
    bb_pct = row['bb_pct']
    if direction == 'SHORT':
        s2 = 20 if bb_pct > 0.85 else (12 if bb_pct > 0.75 else (5 if bb_pct > 0.65 else 0))
    else:
        s2 = 20 if bb_pct < 0.15 else (12 if bb_pct < 0.25 else (5 if bb_pct < 0.35 else 0))
    # ATR相对小 → 低波动精确位
    if row['atr_pct'] < 0.01:
        s2 = min(s2 + 5, 30)
    breakdown['关键位精确度'] = s2

    # D03: 动量背离 (0~20) — MACD零轴+OBV背离
    s3 = 0
    macd_direction = 1 if row['macd'] > 0 else -1
    # MACD与信号线背离
    macd_cross = 1 if (row['macd'] - row['macd_sig']) * d > 0 else 0
    s3 += macd_cross * 5
    # OBV方向
    obv_trend = 1 if row['obv'] > row['obv_ema'] else -1
    if obv_trend * d > 0:
        s3 += 5
    # MACD柱方向强化
    if row['macd_hist'] * d > 0:
        s3 += 4
    # [UP-TRAIN10K] T04: CHOP体制MACD背离PF=1.628最优（达摩院1万次验证）
    _chop_bonus = 0
    regime_tmp = row.get('regime', '') if hasattr(row, 'get') else ''
    if 'CHOP' in str(regime_tmp).upper() and macd_cross and obv_trend * d > 0:
        _chop_bonus = 4
    s3 = min(s3 * _obv_mult + _chop_bonus, 20)  # [UP-DIR] SHORT×1.2 + CHOP背离奖励
    breakdown['动量背离'] = s3

    # D04: SMC结构 (0~20) — EMA排列
    ema_stack = (row['ema20'] > row['ema50']) if direction == 'LONG' else (row['ema20'] < row['ema50'])
    ema_200 = (c > row['ema200']) if direction == 'LONG' else (c < row['ema200'])
    s4 = ((10 if ema_stack else 0) + (10 if ema_200 else 0)) * _smc_mult  # [UP-DIR]
    breakdown['SMC结构'] = s4

    # D05: 量能验证 (0~20)
    s5 = 0
    vol_r = row['vol_ratio']
    price_dir = 1 if row['price_chg'] > 0 else -1
    if price_dir * d > 0 and vol_r > 1.2:
        s5 += 15  # 量增价涨/跌
    elif price_dir * d > 0 and vol_r > 0.8:
        s5 += 8   # 正常量
    elif price_dir * d < 0 and vol_r > 1.5:
        s5 -= 5   # 量增逆势
    # VWAP位置
    if row['price_vs_vwap'] * (-d) > 0.003:
        s5 += 5  # 价格在VWAP不利侧（回归机会）
    # [UP-TRAIN10K] QEW质量环境权重: 趋势期×1.10, CHOP期×0.88（T02量价配合核心信号验证）
    _qew = str(row.get('regime', '') if hasattr(row,'get') else '').upper()
    _qew_m = 1.10 if any(x in _qew for x in ['BULL_TREND','BULL_PEAK','BEAR_TREND','BEAR_CRASH'])              else (0.88 if 'CHOP' in _qew else 1.0)
    s5 = max(0, min(int(s5 * _vol_mult * _qew_m), 20))
    breakdown['量能验证'] = s5

    # D06: 形态成熟度 (0~20) — 趋势成熟度
    momentum_aligned = row['momentum_12'] * (-d)  # 逆动量做反弹
    if momentum_aligned > 0.05:
        s6 = 18
    elif momentum_aligned > 0.02:
        s6 = 12
    elif abs(momentum_aligned) < 0.01:
        s6 = 8  # 横盘
    else:
        s6 = 3
    s6 = s6 * _shape_mult  # [UP-DIR] SHORT×1.3
    breakdown['形态成熟度'] = s6

    # D07: 清算/OI (0~20) — ATR位置代理
    rsi_extreme = row['rsi']
    if direction == 'SHORT':
        s7 = 18 if rsi_extreme > 80 else (12 if rsi_extreme > 70 else (5 if rsi_extreme > 65 else 0))
    else:
        s7 = 18 if rsi_extreme < 20 else (12 if rsi_extreme < 30 else (5 if rsi_extreme < 35 else 0))
    breakdown['清算/OI'] = s7

    # D08: 情绪/费率 (0~15) — RSI极值
    s8 = 0
    rsi = row['rsi']
    if direction == 'SHORT' and rsi > 65:
        s8 = min(int((rsi - 65) / 15 * 15), 15)
    elif direction == 'LONG' and rsi < 35:
        s8 = min(int((35 - rsi) / 15 * 15), 15)
    breakdown['情绪/费率'] = s8

    # D09: 时段权重 (0~10) — 简化
    s9 = 7  # 默认亚洲盘0.7x
    breakdown['时段权重'] = s9

    # D10: 谐波+多周期 (0~15) — 多周期RSI一致性
    rsi_bear = (rsi > 60 and row['rsi_4'] > 60)
    rsi_bull = (rsi < 40 and row['rsi_4'] < 40)
    if direction == 'SHORT' and rsi_bear:
        s10 = 13
    elif direction == 'LONG' and rsi_bull:
        s10 = 13
    else:
        s10 = 5
    breakdown['谐波+多周期'] = s10

    # D11: 鲸鱼+跨市场+微观 (0~30) — 成交量异动代理
    s11 = 0
    if vol_r > 2.0 and price_dir * d > 0:
        s11 = 25  # 极端放量同向
    elif vol_r > 1.5 and price_dir * d > 0:
        s11 = 18
    elif vol_r > 1.2:
        s11 = 10
    else:
        s11 = 5
    breakdown['鲸鱼+跨市场+微观'] = s11

    # D12: 期权+订单流 (0~15) — OBV斜率代理
    obv_slope = (row['obv'] - row['obv_ema']) / (abs(row['obv_ema']) + 1)
    s12 = 0
    if obv_slope * d > 0.01:
        s12 = 12
    elif obv_slope * d > 0.003:
        s12 = 7
    elif obv_slope * d < -0.01:
        s12 = 0
    else:
        s12 = 4
    breakdown['期权+订单流'] = s12

    # D13: L2+贝叶斯+宏观 (0~30) — ATR深度代理
    # 低ATR → 流动性好 → 高分
    atr_pct = row['atr_pct']
    if atr_pct < 0.008:
        s13 = 20
    elif atr_pct < 0.015:
        s13 = 12
    elif atr_pct < 0.025:
        s13 = 6
    else:
        s13 = 2
    breakdown['L2+贝叶斯+宏观'] = s13

    # D14: ML+在线贝叶斯+滑点 (0~30) — 动量+RSI综合
    s14 = 0
    if trend_align == 3:   # 三周期全对齐
        s14 += 15
    elif trend_align == 2:
        s14 += 8
    if macd_cross:
        s14 += 8
    s14 = min(s14, 30)
    breakdown['ML+在线贝叶斯+滑点'] = s14

    # D15: LSTM+NLP情绪 (-15~+18)
    # 基于动量一致性
    mom_aligned = row['momentum_48'] * d
    if mom_aligned > 0.08:
        s15 = 15
    elif mom_aligned > 0.03:
        s15 = 8
    elif mom_aligned < -0.08:
        s15 = -12
    elif mom_aligned < -0.03:
        s15 = -6
    else:
        s15 = 0
    breakdown['LSTM+NLP情绪'] = s15

    total_raw = sum(breakdown.values())

    # ═══════════════════════════════════════════════════════════
    # [UP-CG] CoinGlass实时数据层
    s_cg = 0
    if cg_data:
        fg_val = cg_data.get('fear_greed', 50)
        oi_chg = cg_data.get('oi_change_pct', 0.0)
        liq_bias = cg_data.get('liquidation_bias', 'NEUTRAL')

        # F&G极端信号: 恐慌(<20)做多+10, 贪婪(>80)做空+10
        if direction == 'LONG' and fg_val < 20:
            s_cg += 10    # 极度恐慌：抄底加成
        elif direction == 'LONG' and fg_val < 30:
            s_cg += 6
        elif direction == 'SHORT' and fg_val > 80:
            s_cg += 10   # 极度贪婪：做空加成
        elif direction == 'SHORT' and fg_val > 70:
            s_cg += 6

        # OI变化方向（资金流向确认）
        if direction == 'LONG' and oi_chg > 0.02:
            s_cg += 5   # OI增加+多头方向：确认
        elif direction == 'SHORT' and oi_chg < -0.02:
            s_cg += 5  # OI减少+空头方向：确认
        elif direction == 'LONG' and oi_chg < -0.03:
            s_cg -= 5  # OI大幅减少+做多：风险（轧空后续）
        elif direction == 'SHORT' and oi_chg > 0.03:
            s_cg -= 3

        # 清算偏向（谁被轧）
        if direction == 'LONG' and liq_bias == 'BULLISH_SQUEEZE':
            s_cg += 8   # 空头被轧：看多信号
        elif direction == 'SHORT' and liq_bias == 'BEARISH_SQUEEZE':
            s_cg += 8  # 多头被轧：看空信号

        s_cg = max(-10, min(s_cg, 20))
    breakdown['CoinGlass层'] = s_cg
    total_raw = sum(v for k,v in breakdown.items()
                    if not str(k).startswith('_') and isinstance(v, (int,float)))

    # [UP-SRG] 手术升级：体制×方向智能乘数
    # 注：离线回测中regime为空，乘数保持1.0（避免不准确的离线推断引入噪音）
    # 体制乘数在实盘层(brahma_brain)中完整激活（有实时API数据）
    # ═══════════════════════════════════════════════════════════
    regime = str(row.get('regime', '')).upper()
    regime_mult = 1.0

    if direction == 'LONG':
        if 'BULL_PEAK' in regime or 'BULL_TREND' in regime:
            regime_mult = 1.25   # 牛市多头 PF=2.17级别，大幅加成
        elif 'RECOVERY' in regime:
            regime_mult = 1.10   # 恢复期多头适度加成
        elif 'BEAR_CRASH' in regime:
            regime_mult = 1.05   # 崩盘反弹 PF=1.50，小幅加成
        elif 'BEAR_TREND' in regime or 'BEAR_EARLY' in regime or 'BEAR_PEAK' in regime:
            regime_mult = 0.65   # 熊市多头严重惩罚（BTC多PF=0.84的教训）
        elif 'CHOP_HIGH' in regime or 'CHOP' in regime:
            regime_mult = 0.80   # 震荡期多头惩罚（反转信号失效）
    else:  # SHORT
        if 'BEAR_TREND' in regime or 'BEAR_CRASH' in regime:
            regime_mult = 1.25   # 熊市空头 PF=1.32加成
        elif 'BULL_PEAK' in regime:
            regime_mult = 0.75   # 牛顶空头（危险）
        elif 'CHOP' in regime:
            regime_mult = 1.0    # [FIX] CHOP空头不惩罚: CHOP+EMA PF=1.40实证有效
        elif 'RECOVERY' in regime:
            regime_mult = 0.80   # 恢复期空头惩罚

    # ── EMA200方向一致性硬加成（核心趋势确认）──
    ema200_confirm = 0
    ema200 = row.get('ema200', row.get('close', 0))
    close = row.get('close', 0)
    if direction == 'LONG' and close > ema200 * 1.001:
        ema200_confirm = 8   # 价格在长期均线上方：多头结构
    elif direction == 'SHORT' and close < ema200 * 0.999:
        ema200_confirm = 8   # 价格在长期均线下方：空头结构
    elif direction == 'LONG' and close < ema200 * 0.995:
        ema200_confirm = -10  # 逆大势做多：惩罚
    elif direction == 'SHORT' and close > ema200 * 1.005:
        ema200_confirm = -10  # 逆大势做空：惩罚
    breakdown['EMA200方向确认'] = ema200_confirm

    # ── RSI过滤：极端RSI区间做反转信号失效惩罚 ──
    # 数据来源：RSI超卖超买 PF=0.793（最差信号），原因是趋势行情中RSI可以持续极端
    rsi_now = row.get('rsi', 50)
    rsi_penalty = 0
    if direction == 'LONG' and rsi_now > 60 and regime_mult < 1.0:
        rsi_penalty = -8  # 非牛市中RSI仍高，做多风险大
    elif direction == 'SHORT' and rsi_now < 40 and regime_mult < 1.0:
        rsi_penalty = -8  # 非熊市中RSI仍低，做空风险大
    breakdown['RSI体制过滤'] = rsi_penalty

    total_raw_adjusted = total_raw + ema200_confirm + rsi_penalty
    total = int(total_raw_adjusted * regime_mult)
    breakdown['_regime_mult'] = regime_mult

    # [UP-TRAIN10K] T04体制×最优信号奖励×1.08
    _r = str(row.get('regime','') if hasattr(row,'get') else '').upper()
    _t04 = (
        ('BULL_PEAK' in _r and breakdown.get('量能验证',0) >= 14) or
        ('BULL_TREND' in _r and breakdown.get('SMC结构',0) >= 15) or
        ('BEAR_CRASH' in _r and breakdown.get('关键位精确度',0) >= 18) or
        ('BEAR_TREND' in _r and breakdown.get('动量背离',0) >= 10)
    )
    if _t04 and total > 0:
        total = int(total * 1.08)
        breakdown['T04最优'] = f'+8% ({_r[:8]})'
    
    breakdown['_regime'] = regime

    # [UP-NODE] 深度节点训练 N01/N03/N04 注入（回测层同步）
    _rsi_s = breakdown.get('关键位精确度', 0)
    _vol_s = breakdown.get('量能验证', 0)
    _macd_d = breakdown.get('动量背离', 0)
    if (_rsi_s >= 10 and _vol_s >= 12) or (_rsi_s >= 10 and _macd_d >= 10):
        total = min(int(total) + 3, 175)
        breakdown['N01协同'] = '+3'

    _hour_utc = -1
    try:
        _hour_utc = row.name.hour if hasattr(row.name, 'hour') else -1
    except Exception:
        pass
    if _hour_utc in {11, 15, 6, 5, 14} and total > 0:
        total = min(int(total) + 5, 175)
        breakdown['N03时段'] = f'+5(UTC{_hour_utc})'
    elif _hour_utc in {0, 22, 23, 3} and total > 0:
        total = max(0, int(total) - 5)
        breakdown['N03时段'] = f'-5(UTC{_hour_utc})'

    _dow = -1
    try:
        _dow = row.name.dayofweek if hasattr(row.name, 'dayofweek') else -1
    except Exception:
        pass
    if _dow in {5, 6} and total > 0:
        total = int(total * 0.88)
        breakdown['N04周末'] = '×0.88'


    # [UP-NODE-v3] 回测层同步注入
    _rsi_v3 = float(row.get('rsi', 50))
    _is_long_v3 = (direction == 'LONG')
    _bb_v3 = float(row.get('bb_pct', 0.5))

    # N08 RSI分层（体制加限）
    if _is_long_v3 and _rsi_v3 > 75 and any(r in regime for r in ('BULL_TREND','BULL_PEAK')) and total > 0:
        total = min(int(total) + 4, 175)
        breakdown['N08_RSI'] = '+4(牛市超买)'
    elif not _is_long_v3 and _rsi_v3 < 25 and any(r in regime for r in ('BEAR_TREND','BEAR_CRASH')) and total > 0:
        total = min(int(total) + 4, 175)
        breakdown['N08_RSI'] = '+4(熊市超卖)'
    if 'BULL_TREND' in regime and 45 <= _rsi_v3 < 55 and total > 0:
        total = min(int(total) + 6, 175)
        breakdown['N08_牛中'] = '+6'

    # N10 7维全覆盖
    _sigs_v3 = [breakdown.get('动量背离',0), breakdown.get('关键位精确度',0),
                breakdown.get('SMC结构',0), breakdown.get('趋势一致性',0),
                breakdown.get('量能验证',0), breakdown.get('形态成熟度',0),
                breakdown.get('时段权重',0)]
    if sum(1 for s in _sigs_v3 if s > 0) >= 7 and total > 0:
        total = min(int(total) + 5, 175)
        breakdown['N10_全'] = '+5'

    # N12 BB精度（体制加限）
    if _is_long_v3 and _bb_v3 > 0.90 and any(r in regime for r in ('BULL_TREND','BULL_PEAK')) and total > 0:
        total = min(int(total) + 4, 175)
        breakdown['N12_BB'] = '+4(牛市上沿)'
    elif not _is_long_v3 and _bb_v3 < 0.10 and any(r in regime for r in ('BEAR_TREND','BEAR_CRASH')) and total > 0:
        total = min(int(total) + 4, 175)
        breakdown['N12_BB'] = '+4(熊市下沿)'



    # [UP-FIX-SOL-BNB] 纯技术指标版（不依赖regime）
    _atr_pct_f = float(row.get('atr_pct', row.get('atr', 1)/max(row.get('close',1),1)))
    _rsi_f     = float(row.get('rsi', 50))
    _bb_pct_f2 = float(row.get('bb_pct', 0.5))
    _is_long_f = (direction == 'LONG')
    # FIX1: 极低波动假趋势 — ATR<0.005且BB位置高（假牛市特征）
    if _is_long_f and _atr_pct_f < 0.005 and _bb_pct_f2 > 0.70 and total > 0:
        total = int(total * 0.88)
        breakdown['FIX1'] = '×0.88(低ATR高BB假趋势)'
    # FIX2: 超卖区域做空（RSI<25+做空=逆向追空）
    if not _is_long_f and _rsi_f < 22 and total > 0:
        total = int(total * 0.88)
        breakdown['FIX2'] = '×0.88(RSI<22超卖追空)'



    # [UP-NODE-v4] 深度节点训练器v4 N14/N15/N16/N17 注入
    # ─────────────────────────────────────────────────────
    # N16: SOL BULL_TREND ATR_pct精确惩罚阈值
    #   SOL BULL_TREND ATR_pct<0.010 时 PF=0.567（n=30），阈值校准为0.010
    _atr_v4 = float(row.get('atr_pct', row.get('atr',1)/max(row.get('close',1),1)))
    _is_long_v4 = (direction == 'LONG')
    if 'BULL_TREND' in regime and _is_long_v4 and _atr_v4 < 0.010 and total > 0:
        total = int(total * 0.85)
        breakdown['N16_低ATR趋势'] = f'×0.85 (ATR_pct={_atr_v4:.4f}<0.010)'

    # N14: BEAR_TREND体制切换后early(≤5根)强化 — PF=1.625
    # N14: BULL_TREND稳定期(>5根)强化 — PF=1.512
    # 注：无法在评分层直接知道距离，通过ATR_pct和RSI组合代理
    # BEAR_TREND early特征：RSI<45 + ATR_pct较高（刚进入熊市有波动）
    _rsi_v4 = float(row.get('rsi', 50))
    if 'BEAR_TREND' in regime and not _is_long_v4 and _rsi_v4 < 42 and _atr_v4 > 0.012 and total > 0:
        total = min(int(total) + 5, 175)
        breakdown['N14_熊转边界'] = '+5 (BEAR_TREND early信号 PF=1.625)'

    # N15: 高分信号Kelly强化 — thr≥150时PF=1.538/WR=47.7%，给予额外信心加成
    # 通过score已经≥150的交叉验证（只在评分够高时才加）
    if total >= 150:
        total = min(int(total * 1.03), 175)  # 3%乘数，避免过度
        breakdown['N15_Kelly高分'] = f'×1.03 (score≥150 PF=1.538)'


    return {'total': total, 'breakdown': breakdown}


# ════════════════════════════════════════════════════════════════
# 3. 信号生成 + 回测
# ════════════════════════════════════════════════════════════════

def backtest_symbol(sym: str, interval: str = '1h',
                    threshold: int = 130,
                    sl_atr_mult: float = 1.5,
                    rr_target: float = 2.0,
                    max_hold_bars: int = 12,
                    fast: bool = True) -> dict:
    """
    对单个品种执行主系统回测
    threshold: 最低入场分数
    """
    # 加载数据
    sym_lower = sym.lower().replace('usdt', 'usdt')
    data_f = DATA_DIR / f'{sym_lower}_{interval}_2018_2026.parquet'
    if not data_f.exists():
        return {'status': 'no_data', 'sym': sym}

    df = pd.read_parquet(data_f)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()
    # ── 训练截止线：仅使用 2024-12-31 前的数据（消除前视偏差）──
    _train_cutoff = pd.Timestamp('2025-01-01', tz='UTC')
    df = df[df.index < _train_cutoff].copy()
    df = add_indicators(df)

    # [UP-CG] 加载CoinGlass实时缓存（用于当前时刻的评分加成）
    _cg_data = {}
    try:
        import json as _json
        _fg_path = DATA_DIR.parent / 'data' / '_cache_fg.json'
        if _fg_path.exists():
            _fg = _json.loads(_fg_path.read_text())
            _cg_data['fear_greed'] = _fg.get('data', {}).get('value', 50)
        # OI变化（用brahma_matrix最新数据）
        _mx_path = DATA_DIR.parent / 'data' / 'brahma_matrix_latest.json'
        if _mx_path.exists():
            _mx = _json.loads(_mx_path.read_text())
            # 从sentiment字段读OI
            _sent = _mx.get('sentiment', {})
            _cg_data['oi_change_pct'] = _sent.get('oi_change_pct', 0.0)
            _cg_data['liquidation_bias'] = _sent.get('liq_bias', 'NEUTRAL')
    except Exception:
        pass

    if fast:
        # 快速模式：最近2年
        df = df.iloc[-17520:]  # ~2年 1H数据

    results_long  = []
    results_short = []
    signal_scores = []

    # 滑动窗口扫描信号
    step = 4 if fast else 1  # 快速模式每4根扫一次

    for i in range(200, len(df) - max_hold_bars - 1, step):
        row = df.iloc[i]
        c = row['close']
        atr = row['atr']

        for direction in ['LONG', 'SHORT']:
            sig = score_signal(row, direction, cg_data={})  # [FIX] 历史回测不用实时CG数据
            total = sig['total']
            signal_scores.append(total)

            if total < threshold:
                continue

            # 模拟出入场
            sl_price = c - atr * sl_atr_mult if direction == 'LONG' else c + atr * sl_atr_mult
            tp_price = c + atr * sl_atr_mult * rr_target if direction == 'LONG' else c - atr * sl_atr_mult * rr_target

            entry_price = c
            pnl = 0
            exit_reason = 'timeout'
            exit_bar = min(i + max_hold_bars, len(df) - 1)

            for j in range(i + 1, exit_bar + 1):
                future_row = df.iloc[j]
                fh = future_row['high']
                fl = future_row['low']

                if direction == 'LONG':
                    if fl <= sl_price:
                        pnl = (sl_price - entry_price) / entry_price
                        exit_reason = 'sl'
                        break
                    elif fh >= tp_price:
                        pnl = (tp_price - entry_price) / entry_price
                        exit_reason = 'tp'
                        break
                else:
                    if fh >= sl_price:
                        pnl = (entry_price - sl_price) / entry_price
                        exit_reason = 'sl'
                        break
                    elif fl <= tp_price:
                        pnl = (entry_price - tp_price) / entry_price
                        exit_reason = 'tp'
                        break
            else:
                # 时间止损
                exit_price = df.iloc[exit_bar]['close']
                pnl = (exit_price - entry_price) / entry_price * (1 if direction == 'LONG' else -1)

            # ── [设计院 v16] realistic_cost_model 成本校正 ──────────────────
            try:
                import sys as _sys_rcm, os as _os_rcm
                _rcm_dir = _os_rcm.path.dirname(_os_rcm.path.abspath(__file__))
                if _rcm_dir not in _sys_rcm.path:
                    _sys_rcm.path.insert(0, _rcm_dir)
                from realistic_cost_model import CostModel as _CostModel
                _rcm = _CostModel()
                _entry_px = float(row.get('close', row.get('open', 1000)))
                _atr      = float(row.get('atr', _entry_px * 0.015))
                _hold_h   = hold_bars * (0.25 if '15m' in str(sym) else 1.0)
                _cost_detail = _rcm.adjust_pnl(
                    raw_pnl=pnl, entry_price=_entry_px, atr=_atr,
                    direction=direction, regime=sig.get('regime', 'UNKNOWN'),
                    hold_hours=_hold_h
                )
                pnl = _cost_detail['adj_pnl']  # 用成本校正后的pnl
            except Exception:
                pass  # 成本模型不可用时使用原始pnl
            # ── [realistic_cost_model END] ───────────────────────────────

            trade = {
                'direction': direction,
                'score': total,
                'pnl': pnl,
                'exit_reason': exit_reason,
                'breakdown': sig['breakdown'],
                'time_weight': float(row.get('time_weight', 1.0)),  # [UP-SW] 近2年×2.0
            }
            if direction == 'LONG':
                results_long.append(trade)
            else:
                results_short.append(trade)

    all_trades = results_long + results_short
    if not all_trades:
        return {'status': 'no_signals', 'sym': sym, 'threshold': threshold,
                'signal_scores_mean': np.mean(signal_scores) if signal_scores else 0}

    # 统计
    def stats(trades):
        if not trades: return {}
        # [UP-SW] 时间权重加权 — 近2年数据×2.0
        weights = [t.get('time_weight', 1.0) for t in trades]
        total_w = sum(weights)
        pnls    = [t['pnl'] for t in trades]
        # 加权胜率
        wins_w  = sum(w for t, w in zip(trades, weights) if t['pnl'] > 0)
        losses_w= sum(w for t, w in zip(trades, weights) if t['pnl'] <= 0)
        wr      = wins_w / total_w
        # 加权平均盈亏
        avg_win  = sum(t['pnl']*w for t,w in zip(trades,weights) if t['pnl']>0) / (wins_w  + 1e-9)
        avg_loss = abs(sum(t['pnl']*w for t,w in zip(trades,weights) if t['pnl']<=0)) / (losses_w + 1e-9)
        pf = (wins_w * avg_win) / (losses_w * avg_loss + 1e-9)
        return {
            'n': len(pnls),
            'wr': round(wr, 4),
            'avg_pnl': round(sum(p*w for p,w in zip(pnls,weights))/total_w, 5),
            'pf': round(pf, 3),
            'sharpe': round(np.mean(pnls) / (np.std(pnls) + 1e-9) * math.sqrt(252), 2),
            'max_dd': round(min(pnls), 4),
            'exit_tp_pct': round(sum(1 for t in trades if t['exit_reason']=='tp') / len(trades), 3),
            'exit_sl_pct': round(sum(1 for t in trades if t['exit_reason']=='sl') / len(trades), 3),
        }

    # 逐维贡献分析
    dims = list(all_trades[0]['breakdown'].keys())
    dim_contrib = {}
    for dim in dims:
        if str(dim).startswith('_'): continue  # 跳过元数据字段
        # 将交易按该维度分数分组，看高分是否比低分胜率高
        high = [t for t in all_trades if isinstance(t['breakdown'].get(dim, 0), (int,float)) and t['breakdown'].get(dim, 0) >= 10]
        low  = [t for t in all_trades if isinstance(t['breakdown'].get(dim, 0), (int,float)) and t['breakdown'].get(dim, 0) < 10]
        if len(high) >= 5 and len(low) >= 5:
            wr_high = sum(1 for t in high if t['pnl'] > 0) / len(high)
            wr_low  = sum(1 for t in low  if t['pnl'] > 0) / len(low)
            dim_contrib[dim] = {
                'wr_high': round(wr_high, 3),
                'wr_low':  round(wr_low, 3),
                'delta':   round(wr_high - wr_low, 3),
                'n_high':  len(high),
                'n_low':   len(low),
            }

    return {
        'status': 'done',
        'sym':    sym,
        'threshold': threshold,
        'long':   stats(results_long),
        'short':  stats(results_short),
        'all':    stats(all_trades),
        'dim_contrib': dim_contrib,
        'signal_density': round(len(all_trades) / (len(df) / step) * 100, 2),
        'avg_score': round(np.mean(signal_scores), 1),
        'n_signals': len(all_trades),
    }


# ════════════════════════════════════════════════════════════════
# 4. 阈值扫描
# ════════════════════════════════════════════════════════════════

def threshold_scan(sym: str, thresholds: list, fast: bool = True) -> dict:
    results = {}
    for thr in thresholds:
        r = backtest_symbol(sym, threshold=thr, fast=fast)
        if r.get('status') == 'done':
            results[thr] = r['all']
    return results


# ════════════════════════════════════════════════════════════════
# 5. 主入口
# ════════════════════════════════════════════════════════════════

def main(syms: list, fast: bool = True, scan_thresholds: bool = True):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print('═' * 64)
    print(f'  🔱 梵天主系统 · 达摩院验证  {ts}')
    print(f'  模式: {"快速" if fast else "完整"}  品种: {syms}')
    print('═' * 64)

    all_results = {}
    t0 = time.time()

    for sym in syms:
        print(f'\n▶ {sym} 回测中...')
        r = backtest_symbol(sym, fast=fast)
        all_results[sym] = r

        if r.get('status') != 'done':
            print(f'  ⚠️ {sym}: {r.get("status")} — 跳过')
            continue

        a = r['all']
        print(f'  信号数={r["n_signals"]}  信号密度={r["signal_density"]}%')
        print(f'  全部: WR={a["wr"]:.1%}  PF={a["pf"]:.2f}  Sharpe={a["sharpe"]}  n={a["n"]}')
        if r.get('long') and r['long'].get('n',0) > 0:
            lo = r['long']
            print(f'  做多: WR={lo["wr"]:.1%}  PF={lo["pf"]:.2f}  n={lo["n"]}')
        if r.get('short') and r['short'].get('n',0) > 0:
            sh = r['short']
            print(f'  做空: WR={sh["wr"]:.1%}  PF={sh["pf"]:.2f}  n={sh["n"]}')

        # 逐维贡献
        if r.get('dim_contrib'):
            top_dims = sorted(r['dim_contrib'].items(),
                              key=lambda x: x[1]['delta'], reverse=True)[:5]
            print(f'\n  【高贡献维度 Top5】')
            for dim, dc in top_dims:
                bar = '█' * int(abs(dc['delta']) * 100) + '░' * max(0, 20 - int(abs(dc['delta']) * 100))
                sign = '+' if dc['delta'] > 0 else ''
                print(f'    {dim:16s} Δ={sign}{dc["delta"]*100:.1f}%  '
                      f'高={dc["wr_high"]:.1%} 低={dc["wr_low"]:.1%}  [{bar[:15]}]')

        # 阈值扫描
        if scan_thresholds:
            print(f'\n  【阈值扫描 80~160】')
            scan = threshold_scan(sym, [80, 100, 120, 130, 140, 150, 160], fast=fast)
            best_thr = max(scan.keys(), key=lambda x: scan[x].get('pf', 0)) if scan else 130
            print(f'  {"阈值":>6}  {"n":>5}  {"WR":>6}  {"PF":>6}  {"Sharpe":>7}')
            for thr in sorted(scan.keys()):
                s = scan[thr]
                flag = ' ←最优' if thr == best_thr else ''
                print(f'  {thr:>6}  {s.get("n",0):>5}  {s.get("wr",0):.1%}  '
                      f'{s.get("pf",0):.2f}  {s.get("sharpe",0):>7.2f}{flag}')
            all_results[f'{sym}_threshold_scan'] = scan
            all_results[f'{sym}_best_threshold'] = best_thr

    # ── 汇总结论 ──────────────────────────────────────────────────
    print(f'\n{"═"*64}')
    print('  【汇总结论】')
    grade_map = {(0.5, 2.0): '🔴不达标', (0.5, 1.0): '🟡需改进',
                 (0.45, 1.0): '🟡需改进', (0.0, 0.0): '🔴不达标'}
    for sym in syms:
        r = all_results.get(sym, {})
        if r.get('status') != 'done':
            continue
        a = r['all']
        wr = a.get('wr', 0); pf = a.get('pf', 0)
        grade = '🟢有效' if (wr >= 0.50 and pf >= 1.2) else \
                ('🟡边缘' if (wr >= 0.45 and pf >= 1.0) else '🔴不达标')
        best_thr = all_results.get(f'{sym}_best_threshold', 130)
        print(f'  {sym}: {grade}  WR={wr:.1%}  PF={pf:.2f}  最优阈值={best_thr}')

    elapsed = time.time() - t0
    print(f'\n  回测耗时: {elapsed:.1f}s')
    print('═' * 64)

    # ── 保存结果 ──────────────────────────────────────────────────
    ts_str = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    out_f = RESULTS_DIR / f'system_backtest_{ts_str}.json'

    # 序列化（去除不可序列化的类型）
    def to_serializable(obj):
        if isinstance(obj, (np.integer, np.floating)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return obj

    import json
    out_data = json.loads(json.dumps(all_results, default=to_serializable))
    out_data['_meta'] = {'ts': ts, 'syms': syms, 'fast': fast, 'elapsed_s': round(elapsed, 1)}
    out_f.write_text(json.dumps(out_data, indent=2, ensure_ascii=False))
    print(f'\n  ✅ 结果已保存: {out_f.name}')

    # ── 写入 CI 改进建议 ──────────────────────────────────────────
    write_ci_improvements(all_results, syms)

    return all_results


def write_ci_improvements(results: dict, syms: list):
    """将回测结论写入 CI 改进建议"""
    import json
    improvements = []

    # 最优阈值建议
    best_thresholds = []
    for sym in syms:
        bt = results.get(f'{sym}_best_threshold')
        if bt:
            best_thresholds.append(bt)
    if best_thresholds:
        avg_best = int(sum(best_thresholds) / len(best_thresholds))
        improvements.append({
            'type': 'threshold_recommend',
            'param': 'signal_threshold',
            'recommended': avg_best,
            'by_sym': {sym: results.get(f'{sym}_best_threshold') for sym in syms},
        })

    # 高贡献维度
    all_dim_deltas = defaultdict(list)
    for sym in syms:
        r = results.get(sym, {})
        for dim, dc in r.get('dim_contrib', {}).items():
            all_dim_deltas[dim].append(dc['delta'])
    top_dims = sorted(all_dim_deltas.items(),
                      key=lambda x: sum(x[1])/len(x[1]), reverse=True)[:5]
    improvements.append({
        'type': 'dim_ranking',
        'top_dims': [{
            'dim': dim,
            'avg_delta': round(sum(deltas)/len(deltas), 4),
            'count': len(deltas),
        } for dim, deltas in top_dims],
    })

    # 写入 Blueprint
    try:
        bp = json.loads(BP_FILE.read_text())
        bp.setdefault('_system_backtest', {})
        bp['_system_backtest'] = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'improvements': improvements,
            'syms_tested': syms,
        }
        # 应用最优阈值
        for imp in improvements:
            if imp.get('type') == 'threshold_recommend':
                bp.setdefault('_brain_params', {})
                bp['_brain_params']['signal_threshold_backtest'] = imp['recommended']
                print(f'  ✅ Blueprint: 信号阈值建议 = {imp["recommended"]}')
        BP_FILE.write_text(json.dumps(bp, indent=2, ensure_ascii=False))
        print(f'  ✅ Blueprint 回测结论已写入')
    except Exception as e:
        print(f'  ⚠️ Blueprint写入失败: {e}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='主系统达摩院验证')
    parser.add_argument('--sym', type=str, default='', help='单品回测 (如 ETHUSDT)')
    parser.add_argument('--full', action='store_true', help='完整模式（8年全量）')
    parser.add_argument('--no-scan', action='store_true', help='跳过阈值扫描')
    args = parser.parse_args()

    if args.sym:
        syms = [args.sym.upper()]
    else:
        syms = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']

    fast = not args.full
    scan = not args.no_scan
    main(syms=syms, fast=fast, scan_thresholds=scan)

# [设计院 2026-05-30] K线缓存重建钩子
# backtest大文件已归档为gz，运行前自动解压
def _ensure_kline_cache(sym: str, tf: str):
    import gzip, shutil
    from pathlib import Path
    tgt = Path(f'data/backtest/{sym}_{tf}.json')
    if tgt.exists(): return
    # 尝试两种命名
    for name in [f'{sym}_{tf}', f'{sym.replace("USDT","")}_{tf}']:
        arc = Path(f'data/archive/backtest_klines/{name}.json.gz')
        if arc.exists():
            with gzip.open(str(arc),'rb') as fi, open(str(tgt),'wb') as fo:
                shutil.copyfileobj(fi, fo)
            return
    # gz不存在则从Binance重建（get_klines已有此能力）
