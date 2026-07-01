#!/usr/bin/env python3
"""
达摩院 · 梵天开单体制实测验证框架 v3.0
设计院出品 · 2026-06-11

架构：路A 纯pandas向量化（内存安全，~80MB峰值，<30秒）
数据：BTC/ETH 1H 8.5年（2017-08~2026-05）已体制标注

七命题验证：
  Q1: BEAR_TREND体制下grade≥50的WR
  Q2: 三维矩阵 vs 单维score矩阵
  Q3: grade50-59 TTL收紧效果
  Q4: 质检门②c过滤效果
  Q5: DYNAMIC_GATE历史WR
  Q6: 各体制最优score门槛
  Q7: 1H vs 4H周期频率/WR权衡

Walk-Forward：6窗口 IS/OOS分割（防穿越）
"""
import gc
import json
import warnings
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ── 路径 ────────────────────────────────────────────────────
ROOT    = Path(__file__).parent.parent
BT_DIR  = ROOT / 'data' / 'backtest'
OUT_DIR = Path(__file__).parent / 'results'
OUT_DIR.mkdir(exist_ok=True)

# ── 常量（与brahma_commander.py完全对齐）─────────────────────
COMMISSION  = 0.0004   # 0.04% 吃单
SLIPPAGE    = 0.0002   # 0.02%
LEVERAGE    = 3
INITIAL_NAV = 10_000.0

# 体制乘数（与brahma_commander REGIME_MULT一致）
REGIME_MULT = {
    'BEAR_EARLY':    1.10,
    'BEAR_TREND':    0.85,
    'CHOP_LOW':      0.85,
    'CHOP_MID':      0.75,
    'CHOP_HIGH':     0.60,
    'BULL_EARLY':    0.70,
    'BULL_TREND':    0.70,
    'BEAR_RECOVERY': 0.25,
    'BEAR_CRASH':    0.00,
}

# grade乘数+cap（与brahma_commander GRADE_CONFIG一致）
# (grade_min, grade_max, mult, cap_pct)
GRADE_CONFIG = [
    (80, 999, 1.20, 0.030),
    (70,  80, 1.00, 0.999),
    (60,  70, 0.50, 0.015),
    (50,  60, 0.25, 0.010),
    ( 0,  50, 0.00, 0.000),
]

# score最低门槛（与brahma_commander SCORE_MIN一致）
SCORE_MIN = {
    'BEAR_EARLY':    140,
    'BEAR_TREND':    150,
    'CHOP_LOW':      115,
    'CHOP_MID':      148,
    'CHOP_HIGH':     160,
    '_default':      150,
}

# Walk-Forward 6窗口
WF_WINDOWS = [
    ('W1', '2017-08', '2019-12', '2020-01', '2020-06'),
    ('W2', '2019-01', '2021-06', '2021-07', '2021-12'),
    ('W3', '2020-06', '2022-06', '2022-07', '2022-12'),
    ('W4', '2021-07', '2023-06', '2023-07', '2023-12'),
    ('W5', '2022-07', '2024-06', '2024-07', '2024-12'),
    ('W6', '2023-07', '2025-06', '2025-07', '2026-05'),
]

print("✅ P0骨架+常量加载完成")


# ══════════════════════════════════════════════════════════════
# P1: grade代理评分 — 纯pandas向量化
# ══════════════════════════════════════════════════════════════

def compute_grade_proxy(df: pd.DataFrame) -> pd.Series:
    """
    grade代理评分（0~100）纯pandas向量化
    复刻 structure_quality_engine 核心逻辑：
      - OB强度：价格距近期高低点的相对位置
      - FVG代理：K线跳空（high[i-1] < low[i+1] 或反向）
      - 摆动结构：近期swing高低点
      - 入场区宽度代理：ATR相对宽度
    """
    c = df['close']
    h = df['high']
    l = df['low']
    atr = df['atr14']

    grade = pd.Series(0.0, index=df.index)

    # ── OB强度代理（40分）──────────────────────────────────────
    # SHORT信号：价格在近20根高点下方，高点越近越强
    roll_high_20 = h.rolling(20).max()
    roll_low_20  = l.rolling(20).min()

    # 距近期高点的相对距离（SHORT方向）
    dist_from_high = (roll_high_20 - c) / (atr + 1e-9)
    # dist 0.5~2.0 = OB区间（完美入场）
    ob_score = pd.Series(0.0, index=df.index)
    ob_score[dist_from_high.between(0.3, 1.0)] = 35   # 完美OB区间
    ob_score[dist_from_high.between(1.0, 2.5)] = 20   # 近似OB
    ob_score[dist_from_high.between(2.5, 4.0)] = 8    # 弱OB
    grade += ob_score

    # ── FVG代理（40分）────────────────────────────────────────
    # 上方FVG：前根high < 后根low（向上跳空），价格回测该区间
    fvg_bear = h.shift(2) < l  # 向上跳空（空头FVG）
    fvg_gap  = (l - h.shift(2)).clip(lower=0) / (c + 1e-9) * 100

    fvg_score = pd.Series(0.0, index=df.index)
    fvg_score[fvg_bear & (fvg_gap >= 0.5)] = 40  # 大FVG完美
    fvg_score[fvg_bear & (fvg_gap >= 0.2) & (fvg_gap < 0.5)] = 25  # 中FVG
    fvg_score[fvg_bear & (fvg_gap > 0) & (fvg_gap < 0.2)] = 10     # 小FVG
    grade += fvg_score

    # ── 摆动结构代理（20分）───────────────────────────────────
    # 近10根内出现过明显高点（swing high）
    local_max = h.rolling(5, center=True).max()
    is_swing_high = (h == local_max)
    swing_score = pd.Series(0.0, index=df.index)
    # 近10根内有swing high且价格在其下方0.5~2%
    recent_swing = h.rolling(10).max()
    dist_swing = (recent_swing - c) / (c + 1e-9) * 100
    swing_score[dist_swing.between(0.5, 2.0)] = 20
    swing_score[dist_swing.between(2.0, 4.0)] = 10
    grade += swing_score

    # 裁剪到0~100
    grade = grade.clip(0, 100)

    # 前200根warmup期设为0（指标未稳定）
    grade.iloc[:200] = 0

    return grade


def compute_score_proxy(df: pd.DataFrame) -> pd.Series:
    """
    综合评分代理（0~200）
    复刻brahma_core confluence_score核心维度：
      s1 RSI超卖/超买
      s2 趋势方向
      s3 CVD方向代理（用成交量方向）
      s4 体制匹配加分
      s5 ATR波动率
    """
    c = df['close']
    v = df['volume']
    rsi = df['rsi']
    ema200 = df['ema200']
    atr = df['atr14']

    score = pd.Series(100.0, index=df.index)  # 基础100分

    # s1: RSI维度（SHORT信号下RSI高 = 好信号）
    rsi_score = pd.Series(0.0, index=df.index)
    rsi_score[rsi > 70] = 25
    rsi_score[rsi.between(60, 70)] = 15
    rsi_score[rsi.between(50, 60)] = 8
    rsi_score[rsi < 40] = -10  # 超卖不利于SHORT
    score += rsi_score

    # s2: 趋势方向（在EMA200下方做空 = 顺势）
    trend_score = pd.Series(0.0, index=df.index)
    trend_score[c < ema200] = 20   # 趋势顺势
    trend_score[c > ema200] = -10  # 逆势
    score += trend_score

    # s3: 成交量放大（SHORT时放量下跌）
    vol_ma = v.rolling(20).mean()
    vol_ratio = v / (vol_ma + 1e-9)
    vol_score = pd.Series(0.0, index=df.index)
    vol_score[vol_ratio > 2.0] = 15
    vol_score[vol_ratio.between(1.5, 2.0)] = 8
    score += vol_score

    # s4: 体制匹配（BEAR体制做空最优）
    regime_score_map = {
        'BEAR_TREND': 25, 'BEAR_EARLY': 20, 'BEAR_RECOVERY': 10,
        'CHOP_LOW': 5, 'CHOP_MID': 0, 'CHOP_HIGH': -5,
        'BULL_EARLY': -15, 'BULL_TREND': -20, 'BEAR_CRASH': 15,
    }
    r_score = df['regime'].map(regime_score_map).fillna(0)
    score += r_score

    # s5: ATR适中（不太窄不太宽）
    atr_pct = atr / c * 100
    atr_score = pd.Series(0.0, index=df.index)
    atr_score[atr_pct.between(0.5, 2.0)] = 10
    atr_score[atr_pct > 4.0] = -10  # 过度波动
    score += atr_score

    score = score.clip(0, 200)
    score.iloc[:200] = 0
    return score


print("✅ P1 grade/score代理函数定义完成")


# ══════════════════════════════════════════════════════════════
# P2: 质检门复刻 + P3: 三维矩阵仓位
# ══════════════════════════════════════════════════════════════

def quality_gates(grade: float, score: float, regime: str,
                  signal_age_h: float, gap_pct: float) -> tuple:
    """
    5道质检门完整复刻 (返回: passed, reject_reason)
    gate0: grade<50
    gate1: age>24H
    gate2a: gap<-1%（入场区失效）
    gate2b: age>8H and gap>3%
    gate2c: gap>0.5%（价格未触达）
    """
    if grade < 50:
        return False, 'gate0_grade<50'
    if signal_age_h > 24:
        return False, 'gate1_过期'
    if gap_pct < -1.0:
        return False, 'gate2a_入场区失效'
    if signal_age_h > 8 and gap_pct > 3.0:
        return False, 'gate2b_高龄远距'
    if gap_pct > 0.5:
        return False, 'gate2c_未触达'
    return True, 'OK'


def get_notional(regime: str, score: float, grade: float,
                 nav: float, symbol: str = 'BTCUSDT') -> tuple:
    """
    三维积分矩阵仓位计算（与brahma_commander完全对齐）
    返回 (notional_usd, tier_str)
    """
    # LTC永久禁止
    if 'LTC' in symbol:
        return 0.0, 'X-LTC'

    # DYNAMIC_GATE
    DYNAMIC_GATE = {
        ('ETHUSDT', 'BEAR_TREND'):  {'min_grade': 70, 'min_score': 158},
        ('SOLUSDT', 'BEAR_EARLY'):  {'min_grade': 72, 'min_score': 158},
        ('SOLUSDT', 'BEAR_TREND'):  {'min_grade': 70, 'min_score': 155},
    }
    dg = DYNAMIC_GATE.get((symbol, regime))
    if dg and (grade < dg['min_grade'] or score < dg['min_score']):
        return 0.0, f'X-DGate'

    # BEAR_RECOVERY识别门
    if regime == 'BEAR_RECOVERY' and (grade < 75 or score < 165):
        return 0.0, 'X-BR门'

    # 体制乘数
    r_mult = REGIME_MULT.get(regime, 0.0)
    if r_mult == 0.0:
        return 0.0, f'X-{regime}禁止'

    # score最低门槛
    s_min = SCORE_MIN.get(regime, SCORE_MIN['_default'])
    if score < s_min:
        return 0.0, f'X-score<{s_min}'

    # grade乘数+cap
    g_mult, g_cap = 0.0, 0.0
    tier = 'X'
    for (g_lo, g_hi, gm, cap) in GRADE_CONFIG:
        if g_lo <= grade < g_hi:
            g_mult, g_cap = gm, cap
            tier = 'S' if g_lo >= 80 else ('A' if g_lo >= 70 else ('B' if g_lo >= 60 else 'C'))
            break
    if g_mult == 0.0:
        return 0.0, 'X-grade不足'

    # 标的base仓位
    BASE = {'BTCUSDT': 0.05, 'ETHUSDT': 0.05, 'SOLUSDT': 0.03,
            'BNBUSDT': 0.03, 'DOGEUSDT': 0.02, '_default': 0.02}
    base_pct = BASE.get(symbol, BASE['_default'])

    raw_pct   = base_pct * r_mult * g_mult
    final_pct = min(raw_pct, g_cap, 0.04)
    notional  = nav * final_pct

    return notional, f'{tier}({final_pct*100:.1f}%NAV)'


print("✅ P2质检门 + P3三维矩阵 定义完成")


# ══════════════════════════════════════════════════════════════
# P4: 结算引擎 + 主回测函数
# ══════════════════════════════════════════════════════════════

def settle_trade(entry: float, sl: float, tp1: float, tp2: float,
                 future_candles: pd.DataFrame, direction: str = 'SHORT',
                 hold_hours: int = 48) -> dict:
    """
    逐根K线结算：SL / TP1 / TP2 / TIMEOUT
    future_candles: 入场后的K线（按时间顺序）
    """
    for i, (ts, row) in enumerate(future_candles.iterrows()):
        if i >= hold_hours:
            # TIMEOUT
            final_price = float(row['close'])
            cost = (COMMISSION + SLIPPAGE) * 2
            if direction == 'SHORT':
                pnl_pct = (entry - final_price) / entry - cost
            else:
                pnl_pct = (final_price - entry) / entry - cost
            return {'result': 'TIMEOUT', 'pnl_pct': pnl_pct,
                    'exit_price': final_price, 'hold_h': i, 'exit_ts': ts}

        low_h  = float(row['low'])
        high_h = float(row['high'])

        if direction == 'SHORT':
            # SL检查（价格上破sl）
            if high_h >= sl:
                cost = (COMMISSION + SLIPPAGE) * 2
                pnl_pct = (entry - sl) / entry - cost
                return {'result': 'SL', 'pnl_pct': pnl_pct,
                        'exit_price': sl, 'hold_h': i, 'exit_ts': ts}
            # TP2检查
            if tp2 > 0 and low_h <= tp2:
                cost = (COMMISSION + SLIPPAGE) * 2
                pnl_pct = (entry - tp2) / entry - cost
                return {'result': 'TP2', 'pnl_pct': pnl_pct,
                        'exit_price': tp2, 'hold_h': i, 'exit_ts': ts}
            # TP1检查
            if low_h <= tp1:
                cost = (COMMISSION + SLIPPAGE) * 2
                pnl_pct = (entry - tp1) / entry - cost
                return {'result': 'TP1', 'pnl_pct': pnl_pct,
                        'exit_price': tp1, 'hold_h': i, 'exit_ts': ts}
        else:  # LONG
            if low_h <= sl:
                cost = (COMMISSION + SLIPPAGE) * 2
                pnl_pct = (sl - entry) / entry - cost
                return {'result': 'SL', 'pnl_pct': pnl_pct,
                        'exit_price': sl, 'hold_h': i, 'exit_ts': ts}
            if tp2 > 0 and high_h >= tp2:
                cost = (COMMISSION + SLIPPAGE) * 2
                pnl_pct = (tp2 - entry) / entry - cost
                return {'result': 'TP2', 'pnl_pct': pnl_pct,
                        'exit_price': tp2, 'hold_h': i, 'exit_ts': ts}
            if high_h >= tp1:
                cost = (COMMISSION + SLIPPAGE) * 2
                pnl_pct = (tp1 - entry) / entry - cost
                return {'result': 'TP1', 'pnl_pct': pnl_pct,
                        'exit_price': tp1, 'hold_h': i, 'exit_ts': ts}

    # 数据不足
    return {'result': 'TIMEOUT', 'pnl_pct': 0.0,
            'exit_price': entry, 'hold_h': hold_hours, 'exit_ts': None}


def run_backtest_window(df: pd.DataFrame, symbol: str,
                        start: str, end: str,
                        score_threshold: float = 140,
                        grade_threshold: float = 50,
                        ttl_grade50_mult: float = 0.75,
                        use_quality_gates: bool = True,
                        label: str = '') -> dict:
    """
    单窗口回测主函数
    df: 已含grade/score/regime的1H DataFrame
    """
    mask = (df.index >= pd.Timestamp(start, tz='UTC')) & \
           (df.index <= pd.Timestamp(end,   tz='UTC'))
    sub = df[mask].copy()
    if len(sub) < 200:
        return {}

    trades  = []
    nav     = INITIAL_NAV
    in_trade_until = None

    # 每4H扫描一次（减少计算量，模拟Cron触发频率）
    scan_indices = range(200, len(sub) - 72, 4)

    for i in scan_indices:
        row     = sub.iloc[i]
        ts      = sub.index[i]
        regime  = str(row['regime'])
        grade   = float(row['_grade'])
        score   = float(row['_score'])
        c       = float(row['close'])
        atr     = float(row['atr14'])

        # 持仓中跳过
        if in_trade_until and ts < in_trade_until:
            continue

        # grade/score基础门
        if grade < grade_threshold or score < score_threshold:
            continue

        # 体制禁止
        if REGIME_MULT.get(regime, 0.0) == 0.0:
            continue

        # 构建入场参数（ATR驱动）
        entry_lo = c + atr * 0.5    # SHORT入场区下沿
        entry_hi = c + atr * 1.2    # SHORT入场区上沿
        entry    = (entry_lo + entry_hi) / 2
        sl       = entry_hi + atr * 0.8
        tp1      = c - atr * 1.5
        tp2      = c - atr * 3.0
        rr       = (entry - tp1) / (sl - entry) if sl > entry else 0

        if rr < 1.2:  # 最低RR要求
            continue

        # 质检门gap模拟（入场时假设gap=0，即已触达）
        gap_pct = 0.0  # 向量化回测假设扫描时价格在入场区内
        if use_quality_gates:
            passed, reason = quality_gates(grade, score, regime, 0.0, gap_pct)
            if not passed:
                continue

        # 三维矩阵仓位
        notional, tier = get_notional(regime, score, grade, nav, symbol)
        if notional <= 0:
            continue

        # 动态TTL
        base_ttl = {'BEAR_TREND': 48, 'BEAR_EARLY': 36, 'CHOP_LOW': 16,
                    'BEAR_RECOVERY': 20}.get(regime, 24)
        if grade >= 80:   hold_h = int(base_ttl * 1.5)
        elif grade >= 70: hold_h = int(base_ttl * 1.3)
        elif grade >= 50: hold_h = int(base_ttl * ttl_grade50_mult)
        else:             hold_h = base_ttl
        hold_h = max(12, min(hold_h, 72))

        # 结算
        future = sub.iloc[i+1: i+1+hold_h+1]
        if len(future) < 3:
            continue

        result = settle_trade(entry, sl, tp1, tp2, future, 'SHORT', hold_h)

        # PnL with leverage
        pnl_pct_lev = result['pnl_pct'] * LEVERAGE
        pnl_usd     = notional * pnl_pct_lev
        nav        += pnl_usd
        nav         = max(nav, 0.01)

        in_trade_until = ts + pd.Timedelta(hours=result['hold_h'] + 1)

        trades.append({
            'ts':      str(ts),
            'regime':  regime,
            'grade':   round(grade),
            'score':   round(score),
            'result':  result['result'],
            'pnl_pct': round(result['pnl_pct'] * 100, 3),
            'pnl_usd': round(pnl_usd, 2),
            'hold_h':  result['hold_h'],
            'notional':round(notional, 2),
            'tier':    tier,
            'nav':     round(nav, 2),
        })

    if not trades:
        return {'label': label, 'n': 0, 'wr': 0, 'pf': 0,
                'final_nav': nav, 'trades': []}

    # 统计
    df_t  = pd.DataFrame(trades)
    wins  = df_t[df_t['result'].isin(['TP1','TP2'])].shape[0]
    losses= df_t[df_t['result'] == 'SL'].shape[0]
    tos   = df_t[df_t['result'] == 'TIMEOUT'].shape[0]
    wr    = wins / (wins + losses) if wins + losses > 0 else 0
    gross_win  = df_t[df_t['pnl_usd'] > 0]['pnl_usd'].sum()
    gross_loss = abs(df_t[df_t['pnl_usd'] < 0]['pnl_usd'].sum())
    pf = gross_win / gross_loss if gross_loss > 0 else 999.0

    return {
        'label':     label,
        'n':         len(trades),
        'wins':      wins,
        'losses':    losses,
        'timeouts':  tos,
        'wr':        round(wr, 4),
        'pf':        round(pf, 3),
        'final_nav': round(nav, 2),
        'return_pct':round((nav - INITIAL_NAV) / INITIAL_NAV * 100, 2),
        'trades':    trades,
    }


print("✅ P4结算引擎 + run_backtest_window 定义完成")


# ══════════════════════════════════════════════════════════════
# P5: 数据准备 + Walk-Forward主循环 + 七命题验证
# ══════════════════════════════════════════════════════════════

def prepare_df(symbol: str) -> pd.DataFrame:
    """加载parquet，计算grade/score代理，返回完整DataFrame"""
    fp = BT_DIR / f'{symbol}_1h_full.parquet'
    if not fp.exists():
        raise FileNotFoundError(f"数据不存在: {fp}")
    df = pd.read_parquet(fp)
    df.sort_index(inplace=True)
    df['_grade'] = compute_grade_proxy(df)
    df['_score'] = compute_score_proxy(df)
    gc.collect()
    return df


def walk_forward(symbol: str, df: pd.DataFrame) -> list:
    """Walk-Forward 6窗口验证"""
    results = []
    print(f"\n  {'='*50}")
    print(f"  {symbol} Walk-Forward 6窗口")
    print(f"  {'='*50}")

    for name, is_s, is_e, oos_s, oos_e in WF_WINDOWS:
        # IS（训练窗口）
        is_r = run_backtest_window(df, symbol, is_s, is_e,
                                   label=f'{name}-IS')
        # OOS（验证窗口）
        oos_r = run_backtest_window(df, symbol, oos_s, oos_e,
                                    label=f'{name}-OOS')
        if is_r.get('n', 0) == 0 and oos_r.get('n', 0) == 0:
            continue

        results.append({'window': name, 'IS': is_r, 'OOS': oos_r})

        oos_wr  = oos_r.get('wr', 0) * 100
        oos_pf  = oos_r.get('pf', 0)
        oos_n   = oos_r.get('n', 0)
        oos_ret = oos_r.get('return_pct', 0)
        is_n    = is_r.get('n', 0)

        status = '✅' if oos_wr >= 55 and oos_pf >= 1.2 else '⚠️'
        print(f"  {status} {name}  IS(n={is_n}) → OOS(n={oos_n} WR={oos_wr:.0f}% PF={oos_pf:.2f} ret={oos_ret:+.1f}%)")

    return results


def q_analysis(df: pd.DataFrame, symbol: str) -> dict:
    """七命题逐一分析"""
    print(f"\n  📐 七命题分析 {symbol}")
    answers = {}

    # Q1: BEAR_TREND体制WR
    bt_mask = df['regime'] == 'BEAR_TREND'
    bt_g50  = df[bt_mask & (df['_grade'] >= 50) & (df['_score'] >= 140)]
    bt_g0   = df[bt_mask & (df['_grade'] < 50)]
    answers['Q1'] = {
        'bear_trend_rows': int(bt_mask.sum()),
        'grade_ge50_rows': len(bt_g50),
        'grade_lt50_rows': len(bt_g0),
        'pct_qualified':   round(len(bt_g50) / max(bt_mask.sum(), 1) * 100, 1),
        'conclusion': f'BEAR_TREND中grade≥50占比={len(bt_g50)/max(bt_mask.sum(),1)*100:.1f}%'
    }

    # Q2: 三维矩阵 vs 单维score
    r2_3d   = run_backtest_window(df, symbol, '2022-01', '2025-01',
                                   score_threshold=140, grade_threshold=50,
                                   label='Q2-3D矩阵')
    r2_1d   = run_backtest_window(df, symbol, '2022-01', '2025-01',
                                   score_threshold=150, grade_threshold=0,
                                   label='Q2-单维score')
    answers['Q2'] = {
        '3D_n': r2_3d.get('n',0), '3D_wr': r2_3d.get('wr',0),
        '3D_pf': r2_3d.get('pf',0), '3D_ret': r2_3d.get('return_pct',0),
        '1D_n': r2_1d.get('n',0), '1D_wr': r2_1d.get('wr',0),
        '1D_pf': r2_1d.get('pf',0), '1D_ret': r2_1d.get('return_pct',0),
    }

    # Q3: grade50-59 TTL收紧 vs 宽松
    r3_tight = run_backtest_window(df, symbol, '2022-01', '2025-01',
                                    ttl_grade50_mult=0.75, label='Q3-TTL收紧×0.75')
    r3_wide  = run_backtest_window(df, symbol, '2022-01', '2025-01',
                                    ttl_grade50_mult=1.20, label='Q3-TTL宽松×1.20')
    g50_59_tight = [t for t in r3_tight.get('trades',[]) if 50 <= t['grade'] < 60]
    g50_59_wide  = [t for t in r3_wide.get('trades',[])  if 50 <= t['grade'] < 60]
    def to_rate(ts):
        n = len(ts); t = sum(1 for x in ts if x['result']=='TIMEOUT')
        return round(t/n*100,1) if n else 0
    answers['Q3'] = {
        'tight_n': len(g50_59_tight), 'tight_to_rate': to_rate(g50_59_tight),
        'wide_n':  len(g50_59_wide),  'wide_to_rate':  to_rate(g50_59_wide),
    }

    # Q4: 质检门效果（有门 vs 无门）
    r4_with  = run_backtest_window(df, symbol, '2022-01', '2025-01',
                                    use_quality_gates=True,  label='Q4-有质检门')
    r4_without = run_backtest_window(df, symbol, '2022-01', '2025-01',
                                      use_quality_gates=False, label='Q4-无质检门')
    answers['Q4'] = {
        'with_n': r4_with.get('n',0),    'with_wr': r4_with.get('wr',0),
        'without_n': r4_without.get('n',0), 'without_wr': r4_without.get('wr',0),
    }

    # Q5: BEAR_TREND score门槛扫描（140/150/160/170）
    q5_results = {}
    for thresh in [130, 140, 150, 160, 170]:
        r = run_backtest_window(df, symbol, '2020-01', '2025-01',
                                 score_threshold=thresh, label=f'Q5-score≥{thresh}')
        bt_trades = [t for t in r.get('trades',[]) if t['regime']=='BEAR_TREND']
        wins  = sum(1 for t in bt_trades if t['result'] in ('TP1','TP2'))
        losses= sum(1 for t in bt_trades if t['result']=='SL')
        wr    = wins/(wins+losses) if wins+losses else 0
        q5_results[thresh] = {'n': len(bt_trades), 'wr': round(wr,3),
                               'wins': wins, 'losses': losses}
    answers['Q5'] = q5_results

    # Q6: 各体制最优score门槛
    q6 = {}
    for regime in ['BEAR_TREND','BEAR_EARLY','CHOP_LOW','BEAR_RECOVERY']:
        best = {'thresh': 140, 'wr': 0, 'n': 0}
        for thresh in [120, 130, 140, 150, 160]:
            r = run_backtest_window(df, symbol, '2020-01', '2025-01',
                                     score_threshold=thresh, label=f'Q6')
            rt = [t for t in r.get('trades',[]) if t['regime']==regime]
            wins = sum(1 for t in rt if t['result'] in ('TP1','TP2'))
            losses = sum(1 for t in rt if t['result']=='SL')
            wr = wins/(wins+losses) if wins+losses else 0
            if len(rt) >= 10 and wr > best['wr']:
                best = {'thresh': thresh, 'wr': round(wr,3), 'n': len(rt)}
        q6[regime] = best
    answers['Q6'] = q6

    # Q7: 1H vs 4H频率（1H=每4根扫一次≈4H频率，这里用不同step对比）
    r7_1h = run_backtest_window(df, symbol, '2022-01', '2025-01', label='Q7-标准')
    answers['Q7'] = {'1h_n': r7_1h.get('n',0), '1h_wr': r7_1h.get('wr',0),
                     'note': '4H数据验证需单独加载BTCUSDT_4h_full.parquet'}

    return answers


def main():
    print("\n" + "="*60)
    print("  梵天开单体制实测验证 v3.0")
    print("  达摩院 × 设计院 × 梵天 × 顶级交易员")
    print("="*60)

    all_results = {}

    for symbol in ['BTCUSDT', 'ETHUSDT']:
        print(f"\n{'─'*60}")
        print(f"  📊 加载 {symbol} 数据...")
        df = prepare_df(symbol)
        valid_rows = (df['_grade'] >= 50) & (df['_score'] >= 140)
        print(f"  总1H数据: {len(df):,}根  grade≥50+score≥140: {valid_rows.sum():,}根({valid_rows.mean()*100:.1f}%)")

        # 体制分布中满足条件的
        print(f"  体制×条件分布:")
        for r in ['BEAR_TREND','BEAR_EARLY','CHOP_LOW','CHOP_MID','BEAR_RECOVERY']:
            n_r = (df['regime']==r).sum()
            n_q = ((df['regime']==r) & valid_rows).sum()
            print(f"    {r:<20} 总={n_r:>5}  满足条件={n_q:>4}  ({n_q/max(n_r,1)*100:.1f}%)")

        # Walk-Forward
        wf_results = walk_forward(symbol, df)

        # 七命题
        qa = q_analysis(df, symbol)

        all_results[symbol] = {'wf': wf_results, 'qa': qa}

        # 打印七命题摘要
        print(f"\n  📋 七命题摘要 [{symbol}]")
        q1 = qa['Q1']
        print(f"  Q1 BEAR_TREND grade≥50占比: {q1['pct_qualified']}%  ({q1['grade_ge50_rows']}行)")

        q2 = qa['Q2']
        print(f"  Q2 三维矩阵: n={q2['3D_n']} WR={q2['3D_wr']*100:.0f}% PF={q2['3D_pf']:.2f}  vs  单维: n={q2['1D_n']} WR={q2['1D_wr']*100:.0f}%")

        q3 = qa['Q3']
        print(f"  Q3 grade50-59 TO率: 收紧TTL={q3['tight_to_rate']}%  宽松TTL={q3['wide_to_rate']}%")

        q4 = qa['Q4']
        print(f"  Q4 质检门: 有门 n={q4['with_n']} WR={q4['with_wr']*100:.0f}%  无门 n={q4['without_n']} WR={q4['without_wr']*100:.0f}%")

        print(f"  Q5 BEAR_TREND score门槛:")
        for thresh, v in qa['Q5'].items():
            print(f"      score≥{thresh}: n={v['n']:3d} WR={v['wr']*100:.0f}% W={v['wins']} L={v['losses']}")

        print(f"  Q6 各体制最优score门槛:")
        for regime, v in qa['Q6'].items():
            print(f"      {regime:<20} 最优thresh={v['thresh']} WR={v['wr']*100:.0f}% n={v['n']}")

        del df
        gc.collect()

    # 保存结果
    out_path = OUT_DIR / 'brahma_v3_results.json'
    # 清理不可序列化的对象
    def clean(obj):
        if isinstance(obj, dict): return {k: clean(v) for k,v in obj.items()}
        if isinstance(obj, list): return [clean(v) for v in obj]
        if isinstance(obj, (np.int64, np.float64)): return float(obj)
        if isinstance(obj, pd.Timestamp): return str(obj)
        return obj
    out_path.write_text(json.dumps(clean(all_results), indent=2, ensure_ascii=False))
    print(f"\n✅ 结果已保存: {out_path}")
    print("="*60)


if __name__ == '__main__':
    main()


# ══════════════════════════════════════════════════════════════
# v26.0 全周期架构回测补丁
# 修复：代理评分对CHOP/BULL体制的误杀
# 升级：体制是权重修正器（非封闭器），全周期双向分析
# ══════════════════════════════════════════════════════════════

# v26.0 体制乘数（signal_selector.py对齐）
REGIME_MULT_V26 = {
    # BEAR体制：做空顺势
    'BEAR_TREND':    {'SHORT': 1.50, 'LONG': 0.50},
    'BEAR_EARLY':    {'SHORT': 1.50, 'LONG': 0.50},
    'BEAR_RECOVERY': {'SHORT': 1.20, 'LONG': 0.80},
    'BEAR_CRASH':    {'SHORT': 1.50, 'LONG': 0.30},
    # CHOP体制：双向0.7
    'CHOP_HIGH':     {'SHORT': 0.70, 'LONG': 0.70},
    'CHOP_MID':      {'SHORT': 0.70, 'LONG': 0.70},
    'CHOP_LOW':      {'SHORT': 0.70, 'LONG': 0.70},
    # BULL体制：做多顺势
    'BULL_EARLY':    {'SHORT': 0.50, 'LONG': 1.50},
    'BULL_TREND':    {'SHORT': 0.30, 'LONG': 1.50},
}

MIN_WEIGHTED_V26 = 110  # 加权后最低门槛（原始140×0.5=70，但需要更高原始分）


def compute_score_v26(df: pd.DataFrame, direction: str = 'SHORT') -> pd.Series:
    """
    v26.0 全周期score代理：不再惩罚体制方向，改为输出体制乘数加权后的分数
    direction: 'SHORT' or 'LONG'
    """
    c   = df['close']
    v   = df['volume']
    rsi = df['rsi']
    ema200 = df['ema200']
    atr = df['atr14']

    # 基础技术评分（不含体制方向偏差）
    raw = pd.Series(100.0, index=df.index)

    # RSI（SHORT：高RSI好，LONG：低RSI好）
    if direction == 'SHORT':
        raw += np.where(rsi > 70, 25, np.where(rsi > 60, 15, np.where(rsi > 50, 8, -5)))
    else:
        raw += np.where(rsi < 30, 25, np.where(rsi < 40, 15, np.where(rsi < 50, 8, -5)))

    # 成交量放大（方向无关）
    vol_ma = v.rolling(20).mean()
    vol_r  = v / (vol_ma + 1e-9)
    raw += np.where(vol_r > 2.0, 15, np.where(vol_r > 1.5, 8, 0))

    # ATR适中
    atr_pct = atr / c * 100
    raw += np.where((atr_pct > 0.5) & (atr_pct < 2.0), 10,
                    np.where(atr_pct > 4.0, -10, 0))

    # 趋势方向奖励（顺势+10，不惩罚逆势 → 交给体制乘数处理）
    if direction == 'SHORT':
        raw += np.where(c < ema200, 15, 5)   # 趋势做空+15，逆势做空+5（非0）
    else:
        raw += np.where(c > ema200, 15, 5)

    raw = raw.clip(0, 200)

    # 应用体制乘数（v26.0核心：体制是权重修正器）
    regime_mult = df['regime'].map(
        {r: v[direction] for r, v in REGIME_MULT_V26.items()}
    ).fillna(0.7)  # 未知体制默认0.7

    weighted = (raw * regime_mult).clip(0, 200)
    weighted.iloc[:200] = 0

    return weighted


def run_backtest_v26(df: pd.DataFrame, symbol: str,
                     start: str, end: str,
                     label: str = '') -> dict:
    """
    v26.0 全周期双向回测
    SHORT + LONG 两个方向同时扫描，各自用体制乘数加权
    """
    df = df.copy()
    df['_score_short'] = compute_score_v26(df, 'SHORT')
    df['_score_long']  = compute_score_v26(df, 'LONG')

    mask = (df.index >= pd.Timestamp(start, tz='UTC')) & \
           (df.index <= pd.Timestamp(end,   tz='UTC'))
    sub = df[mask]
    if len(sub) < 200:
        return {}

    trades   = []
    nav      = INITIAL_NAV
    in_trade_until = None

    for i in range(200, len(sub) - 72, 4):
        row    = sub.iloc[i]
        ts     = sub.index[i]
        regime = str(row['regime'])
        grade  = float(row['_grade'])
        c      = float(row['close'])
        atr    = float(row['atr14'])
        s_short = float(row['_score_short'])
        s_long  = float(row['_score_long'])

        if in_trade_until and ts < in_trade_until:
            continue
        if grade < 50:
            continue

        # 方向裁决（v26.0 signal_selector逻辑）
        mult = REGIME_MULT_V26.get(regime, {'SHORT': 0.7, 'LONG': 0.7})
        diff = abs(s_short - s_long)
        chop = regime in ('CHOP_HIGH', 'CHOP_MID', 'CHOP_LOW')

        if diff >= 15:
            # 单向：选高分方向
            directions = ['SHORT' if s_short > s_long else 'LONG']
        elif chop:
            # 震荡双向推送
            directions = ['SHORT', 'LONG']
        else:
            directions = ['SHORT' if s_short > s_long else 'LONG']

        for direction in directions:
            w_score = s_short if direction == 'SHORT' else s_long
            if w_score < MIN_WEIGHTED_V26:
                continue

            # 入场参数
            if direction == 'SHORT':
                entry = c + atr * 0.8
                sl    = entry + atr * 0.8
                tp1   = c - atr * 1.5
                tp2   = c - atr * 3.0
            else:  # LONG
                entry = c - atr * 0.8
                sl    = entry - atr * 0.8
                tp1   = c + atr * 1.5
                tp2   = c + atr * 3.0

            rr = abs(tp1 - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0
            if rr < 1.2:
                continue

            # 仓位：base × regime_mult，逆势自动缩减
            d_mult  = mult[direction]
            base_pct = {'BTCUSDT': 0.05, 'ETHUSDT': 0.05}.get(symbol, 0.02)
            pos_pct  = min(base_pct * d_mult, 0.04)
            # grade cap
            if grade >= 80:   pos_pct = min(pos_pct, 0.03)
            elif grade >= 70: pos_pct = pos_pct
            elif grade >= 50: pos_pct = min(pos_pct, 0.01)
            notional = nav * pos_pct

            # 动态TTL（逆势缩短）
            base_ttl = 36 if d_mult >= 1.2 else (24 if d_mult >= 0.7 else 12)
            if grade >= 80:   hold_h = int(base_ttl * 1.5)
            elif grade >= 70: hold_h = int(base_ttl * 1.3)
            else:             hold_h = int(base_ttl * 0.75)
            hold_h = max(12, min(hold_h, 72))

            future = sub.iloc[i+1: i+1+hold_h+1]
            if len(future) < 3:
                continue

            result = settle_trade(entry, sl, tp1, tp2, future, direction, hold_h)
            pnl_pct_lev = result['pnl_pct'] * LEVERAGE
            pnl_usd     = notional * pnl_pct_lev
            nav        += pnl_usd
            nav         = max(nav, 0.01)

            in_trade_until = ts + pd.Timedelta(hours=result['hold_h'] + 1)

            trades.append({
                'ts':       str(ts),
                'regime':   regime,
                'direction':direction,
                'grade':    round(grade),
                'score':    round(w_score),
                'regime_mult': d_mult,
                'result':   result['result'],
                'pnl_pct':  round(result['pnl_pct'] * 100, 3),
                'pnl_usd':  round(pnl_usd, 2),
                'hold_h':   result['hold_h'],
                'nav':      round(nav, 2),
            })

    if not trades:
        return {'label': label, 'n': 0, 'wr': 0, 'pf': 0, 'final_nav': nav, 'trades': []}

    df_t   = pd.DataFrame(trades)
    wins   = df_t[df_t['result'].isin(['TP1','TP2'])].shape[0]
    losses = df_t[df_t['result'] == 'SL'].shape[0]
    tos    = df_t[df_t['result'] == 'TIMEOUT'].shape[0]
    wr     = wins / (wins + losses) if wins + losses > 0 else 0
    gross_win  = df_t[df_t['pnl_usd'] > 0]['pnl_usd'].sum()
    gross_loss = abs(df_t[df_t['pnl_usd'] < 0]['pnl_usd'].sum())
    pf = gross_win / gross_loss if gross_loss > 0 else 999.0

    # 体制分布
    by_regime = df_t.groupby('regime').agg(
        n=('result','count'),
        wins=('result', lambda x: (x.isin(['TP1','TP2'])).sum()),
        losses=('result', lambda x: (x=='SL').sum()),
    )
    by_regime['wr'] = (by_regime['wins'] / (by_regime['wins']+by_regime['losses'])).round(3)

    # 方向分布
    by_dir = df_t.groupby('direction').agg(
        n=('result','count'),
        wins=('result', lambda x: (x.isin(['TP1','TP2'])).sum()),
        losses=('result', lambda x: (x=='SL').sum()),
    )
    by_dir['wr'] = (by_dir['wins'] / (by_dir['wins']+by_dir['losses'])).round(3)

    return {
        'label':      label,
        'n':          len(trades),
        'wins':       wins,
        'losses':     losses,
        'timeouts':   tos,
        'wr':         round(wr, 4),
        'pf':         round(pf, 3),
        'final_nav':  round(nav, 2),
        'return_pct': round((nav - INITIAL_NAV) / INITIAL_NAV * 100, 2),
        'by_regime':  by_regime.to_dict(),
        'by_dir':     by_dir.to_dict(),
        'trades':     trades,
    }


def main_v26():
    """v26.0 全周期双向回测主函数"""
    print("\n" + "="*62)
    print("  梵天 v26.0 全周期双向回测验证")
    print("  体制是权重修正器，不是封闭器")
    print("="*62)

    for symbol in ['BTCUSDT', 'ETHUSDT']:
        print(f"\n{'─'*62}")
        df = prepare_df(symbol)

        # 全周期回测（2020~2026）
        r = run_backtest_v26(df, symbol, '2020-01', '2026-05', label=f'{symbol}-全周期')

        if not r.get('n'):
            print(f"  {symbol}: 无交易")
            continue

        print(f"  {symbol} 全周期（2020~2026）")
        print(f"  总交易: {r['n']}笔  WR={r['wr']*100:.1f}%  PF={r['pf']:.2f}")
        print(f"  收益:   ${r['final_nav']:,.0f}  (+{r['return_pct']:+.1f}%)")
        print(f"  W={r['wins']} L={r['losses']} TO={r['timeouts']}")

        # 体制分布
        print(f"  体制分布:")
        bd = r.get('by_regime', {})
        for regime in sorted(bd.get('n', {}).keys(), key=lambda x: -bd['n'].get(x,0)):
            n  = bd['n'].get(regime, 0)
            wr = bd['wr'].get(regime, 0)
            w  = bd['wins'].get(regime, 0)
            l  = bd['losses'].get(regime, 0)
            print(f"    {regime:<22} n={n:>4}  WR={wr*100:.0f}%  W={w} L={l}")

        # 方向分布
        print(f"  方向分布:")
        dd = r.get('by_dir', {})
        for d in sorted(dd.get('n', {}).keys()):
            n  = dd['n'].get(d, 0)
            wr = dd['wr'].get(d, 0)
            w  = dd['wins'].get(d, 0)
            l  = dd['losses'].get(d, 0)
            print(f"    {d:<8} n={n:>4}  WR={wr*100:.0f}%  W={w} L={l}")

        # Walk-Forward v26
        print(f"\n  Walk-Forward 6窗口（v26.0全周期）:")
        for name, is_s, is_e, oos_s, oos_e in WF_WINDOWS:
            oos_r = run_backtest_v26(df, symbol, oos_s, oos_e, label=f'{name}-OOS')
            if not oos_r.get('n'):
                continue
            wr_  = oos_r.get('wr', 0) * 100
            pf_  = oos_r.get('pf', 0)
            n_   = oos_r.get('n', 0)
            ret_ = oos_r.get('return_pct', 0)
            status = '✅' if wr_ >= 55 and pf_ >= 1.2 else '⚠️'
            print(f"    {status} {name} OOS: n={n_:>3} WR={wr_:.0f}% PF={pf_:.2f} ret={ret_:+.1f}%")

        del df
        gc.collect()

    print("\n✅ v26.0全周期回测完成")


if __name__ == '__main__':
    import sys as _sys
    if '--v26' in _sys.argv:
        main_v26()
    else:
        main()
