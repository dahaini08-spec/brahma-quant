#!/usr/bin/env python3
"""
梵天 v12 · 体制感知引擎 state_engine.py
基于EXP-02结论：体制×方向是系统最强特征（IG=0.00222）

11态精确路由 · 三维识别 · 零API（用本地K线）
2026-05-20 设计院修复: 补全 BEAR_EARLY/CHOP_LOW/CHOP_MID/BULL_TREND
         与 brahma_brain / hunter_filter 体制名称完全对齐
         补强：版本标注/日志/异常处理
历史：
  v11 - 初始8态版本
  v12 - 2026-05-20 扩至11态，全面补强
"""

import math
import logging
from typing import Tuple

VERSION = 'v12'

log = logging.getLogger('state_engine')
if not log.handlers:
    import sys as _sys
    _h = logging.StreamHandler(_sys.stdout)
    _h.setFormatter(logging.Formatter('[StateEngine] %(message)s'))
    log.addHandler(_h)
log.setLevel(logging.INFO)

# ─── 体制定义（v12：11态全覆盖）────────────────────────────────────
STATE = {
    # ── 熊市系列 ────────────────────────────────────────────────────
    'BEAR_TREND':      {'label':'🐻熊市趋势',    'wr':0.244, 'action':'SHUTDOWN',      'kelly_mul':0.0},
    'BEAR_EARLY':      {'label':'🐻熊市初期',    'wr':0.319, 'action':'HALF',          'kelly_mul':0.5},
    'BEAR_CRASH':      {'label':'💥极端崩塌',    'wr':0.350, 'action':'BLACKSWAN',     'kelly_mul':0.0},
    'BEAR_RECOVERY':   {'label':'🔄熊市复苏',    'wr':0.360, 'action':'CAUTIOUS_LONG', 'kelly_mul':0.7},
    # ── 震荡系列 ────────────────────────────────────────────────────
    'CHOP_HIGH':       {'label':'🔀高位震荡',    'wr':0.360, 'action':'SHORT_BIAS_48H','kelly_mul':1.1},
    'CHOP_MID':        {'label':'🔀中位震荡',    'wr':0.350, 'action':'NEUTRAL',       'kelly_mul':1.0},
    'CHOP_LOW':        {'label':'🔀低位震荡',    'wr':0.340, 'action':'LONG_BIAS',     'kelly_mul':0.9},
    # ── 牛市系列 ────────────────────────────────────────────────────
    'BULL_TREND':      {'label':'🐂牛市趋势',    'wr':0.420, 'action':'FULL_LONG',     'kelly_mul':1.5},
    'BULL_EARLY':      {'label':'🐂牛市启动',    'wr':0.329, 'action':'LONG_BIAS',     'kelly_mul':0.8},
    'BULL_PEAK':       {'label':'🐂牛市巅峰',    'wr':0.360, 'action':'SHORT_BIAS',    'kelly_mul':1.0},
    'RECOVERY':        {'label':'🔄底部复苏',    'wr':0.380, 'action':'FULL_LONG',     'kelly_mul':1.5},
}

PREFERRED_DIR = {
    'BEAR_TREND':      None,   # 不做反转
    'BEAR_EARLY':      '空',   # 熊市初期空头优势
    'BEAR_CRASH':      None,   # 黑天鹅不操作
    'BEAR_RECOVERY':   '多',   # 熊市复苏多头介入
    'CHOP_HIGH':       '空',   # 高位震荡做空优势明显
    'CHOP_MID':        None,   # 中位均衡
    'CHOP_LOW':        '多',   # 低位震荡多头支撑
    'BULL_TREND':      '多',   # 牛市趋势强多
    'BULL_EARLY':      '多',   # 牛市启动做多
    'BULL_PEAK':       '空',   # 牛市顶部做空
    'RECOVERY':        '多',   # 底部复苏做多
}

OPTIMAL_HOLD = {
    # 来自EXP-06：CHOP_HIGH体制下EXTREME信号最优48H
    'BEAR_TREND':      {'EXTREME':'1H',  'STRONG':'1H',  'MODERATE':'1H'},
    'BEAR_EARLY':      {'EXTREME':'4H',  'STRONG':'48H', 'MODERATE':'4H'},
    'BEAR_CRASH':      {'EXTREME':'12H', 'STRONG':'24H', 'MODERATE':'4H'},
    'BEAR_RECOVERY':   {'EXTREME':'4H',  'STRONG':'8H',  'MODERATE':'4H'},
    'CHOP_HIGH':       {'EXTREME':'48H', 'STRONG':'48H', 'MODERATE':'48H'},  # EXP-06核心发现
    'CHOP_MID':        {'EXTREME':'8H',  'STRONG':'8H',  'MODERATE':'4H'},
    'CHOP_LOW':        {'EXTREME':'8H',  'STRONG':'4H',  'MODERATE':'4H'},
    'BULL_TREND':      {'EXTREME':'48H', 'STRONG':'24H', 'MODERATE':'8H'},
    'BULL_EARLY':      {'EXTREME':'1H',  'STRONG':'1H',  'MODERATE':'4H'},
    'BULL_PEAK':       {'EXTREME':'4H',  'STRONG':'4H',  'MODERATE':'1H'},
    'RECOVERY':        {'EXTREME':'1H',  'STRONG':'4H',  'MODERATE':'4H'},
}


# ─── 体制识别（三维）────────────────────────────────────────────────

def _ema(closes, n):
    n = min(n, len(closes))
    k = 2 / (n + 1)
    e = closes[-n]
    for v in closes[-n+1:]: e = v * k + e * (1 - k)
    return e

def _rsi(closes, n=14):
    if len(closes) < n + 1: return 50.0
    g = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
    l = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
    ag = sum(g[:n]) / n; al = sum(l[:n]) / n
    for i in range(n, len(g)):
        ag = ag * (n-1) / n + g[i] / n
        al = al * (n-1) / n + l[i] / n
    return round(100 - 100 / (1 + ag / al), 2) if al > 0 else 100.0

def _bb_width(closes, n=20):
    if len(closes) < n: return 0.05
    mid = sum(closes[-n:]) / n
    sd = math.sqrt(sum((x - mid)**2 for x in closes[-n:]) / n)
    return (2 * sd * 2) / mid if mid > 0 else 0.05

def _pct_change(closes, n):
    if len(closes) <= n or closes[-n-1] <= 0: return 0.0
    return (closes[-1] - closes[-n-1]) / closes[-n-1] * 100

def detect_state(btc_daily_closes: list, btc_daily_highs: list = None,
                 btc_daily_lows: list = None) -> dict:
    """
    三维体制识别
    输入：BTC日线收盘价（至少200根）
    输出：{state, label, action, wr, kelly_mul, preferred_dir, optimal_hold, detail}
    """
    try:
        c = list(btc_daily_closes)
    except Exception as e:
        log.error(f'detect_state 输入数据异常: {e}')
        return _state_result('CHOP_MID', {'note': f'数据异常降级: {e}'})

    if len(c) < 50:
        log.warning(f'detect_state 数据不足({len(c)}<50)，降级CHOP_MID')
        return _state_result('CHOP_MID', {'note': '数据不足，默认中位震荡'})

    # 维度1：RSI（趋势强度）
    rsi_d = _rsi(c, 14)

    # 维度2：20日价格变化率（动量）
    chg_20 = _pct_change(c, 20)
    chg_60 = _pct_change(c, min(60, len(c)-1))

    # 维度3：BB宽度（波动率结构）
    bbw = _bb_width(c, 20)
    # 计算BB宽度的历史分位
    bbw_history = [_bb_width(c[:i], 20) for i in range(30, len(c), 5)]
    bbw_rank = sum(1 for x in bbw_history if x <= bbw) / len(bbw_history) if bbw_history else 0.5

    # EMA位置
    ema50  = _ema(c, min(50, len(c)))
    ema200 = _ema(c, min(200, len(c)))
    price  = c[-1]

    detail = {
        'rsi_d': rsi_d, 'chg_20': chg_20, 'chg_60': chg_60,
        'bbw': bbw, 'bbw_rank': bbw_rank,
        'vs_ema50': (price - ema50) / ema50 * 100,
        'vs_ema200': (price - ema200) / ema200 * 100,
    }

    # ─── 黑天鹅优先检测 ───────────────────────────────────────────
    if chg_20 < -20 or chg_60 < -35:
        return _state_result('BEAR_CRASH', detail)

    # ─── 熊市系列 ────────────────────────────────────────────────
    if rsi_d < 35 and chg_20 < -10 and price < ema200 * 0.92:
        return _state_result('BEAR_TREND', detail)

    if rsi_d < 42 and chg_20 < -8:
        return _state_result('BEAR_EARLY', detail)      # 熊市初期（原BEAR_TRANSITION）

    if rsi_d < 45 and chg_20 < -3 and price < ema50:
        return _state_result('BEAR_EARLY', detail)      # 弱势偏空初期

    # ─── 熊市复苏（超卖反弹）────────────────────────────────────
    if rsi_d < 45 and chg_20 > 3 and price < ema200 * 0.95:
        return _state_result('BEAR_RECOVERY', detail)

    # ─── 牛市系列 ────────────────────────────────────────────────
    if rsi_d > 70 and chg_20 > 15 and price > ema200 * 1.10:
        return _state_result('BULL_PEAK', detail)

    if rsi_d > 65 and chg_20 > 20 and price > ema50 and price > ema200:
        return _state_result('BULL_TREND', detail)      # 强趋势牛市

    if rsi_d > 58 and chg_60 > 15 and price > ema50 and price > ema200:
        return _state_result('BULL_EARLY', detail)      # 牛市启动

    # ─── 底部复苏 ─────────────────────────────────────────────────
    if 40 <= rsi_d <= 55 and chg_20 > 2 and price > ema200 * 0.88 and bbw_rank < 0.4:
        return _state_result('RECOVERY', detail)

    # ─── 震荡三分位（按价格与EMA位置区分）──────────────────────────
    vs_ema200 = detail['vs_ema200']
    if 42 <= rsi_d <= 72 and abs(chg_20) < 12:
        if vs_ema200 > 5:
            _st = _state_result('CHOP_HIGH', detail)
        elif vs_ema200 > -5:
            _st = _state_result('CHOP_MID', detail)
        else:
            _st = _state_result('CHOP_LOW', detail)
        log.info(f'体制识别: {_st["state"]} RSI={rsi_d:.1f} chg20={chg_20:.1f}% ema200_pct={vs_ema200:.1f}%')
        return _st

    # ─── 默认：中位震荡 ───────────────────────────────────────────────
    _default = _state_result('CHOP_MID', detail)
    log.info(f'体制识别(默认): {_default["state"]} RSI={rsi_d:.1f} chg20={chg_20:.1f}%')
    return _default


def _state_result(state_key: str, detail: dict) -> dict:
    s = STATE[state_key]
    return {
        'state':         state_key,
        'label':         s['label'],
        'action':        s['action'],
        'baseline_wr':   s['wr'],
        'kelly_mul':     s['kelly_mul'],
        'preferred_dir': PREFERRED_DIR[state_key],
        'optimal_hold':  OPTIMAL_HOLD[state_key],
        'detail':        detail,
    }


def signal_allowed(state_result: dict, signal_dir: str) -> bool:
    """
    判断当前体制下该方向信号是否允许开仓
    """
    action = state_result['action']
    if action in ('SHUTDOWN', 'BLACKSWAN'):
        return False  # 真技术红线：黑天鹅保留
    preferred = state_result['preferred_dir']
    if preferred is None:
        return True  # 均衡体制，双向都行
    # [v24.3-fix] 偏向体制逆向信号 → 不拒绝，返回True
    # 哲学: 降权在brahma_core regime_mult层处理，这里不二次封禁
    if action == 'SHORT_BIAS' and signal_dir == '做多':
        return True   # 偏空但做多：已在regime_mult中降权，不再封锁
    if action == 'LONG_BIAS' and signal_dir == '做空':
        return True   # 偏多但做空：已在regime_mult中降权，不再封锁
    return True


def get_optimal_track(state_result: dict, signal_strength: str) -> str:
    """
    根据体制+信号强度确定双轨出场策略
    返回：'TRACK_A'（快出1-4H）或 'TRACK_B'（长持48H）
    """
    hold_h = state_result['optimal_hold'].get(signal_strength, '8H')
    if hold_h in ('48H', '24H'):
        return 'TRACK_B'
    return 'TRACK_A'


# ─── 测试 ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    import random
    random.seed(42)

    # 模拟高位震荡（当前体制）
    closes = [50000 + random.gauss(0, 2000) for _ in range(200)]
    for i in range(1, len(closes)):
        closes[i] = closes[i-1] * (1 + random.gauss(0, 0.01))
    closes = [max(1000, c) for c in closes]

    result = detect_state(closes)
    print("梵天 v11 · 体制识别测试")
    print(f"  体制：{result['label']} ({result['state']})")
    print(f"  基准胜率：{result['baseline_wr']*100:.1f}%")
    print(f"  Kelly系数：×{result['kelly_mul']}")
    print(f"  优先方向：{result['preferred_dir'] or '均衡'}")
    print(f"  最优持仓：EXTREME={result['optimal_hold']['EXTREME']}  STRONG={result['optimal_hold']['STRONG']}")
    print(f"  详情：RSI={result['detail']['rsi_d']:.1f}  20日涨跌={result['detail']['chg_20']:+.1f}%")
    print()

    # 测试信号过滤
    for dir_ in ['做多', '做空']:
        allowed = signal_allowed(result, dir_)
        print(f"  {dir_}信号：{'✅允许' if allowed else '❌拒绝'}")

    print()
    for strength in ['EXTREME', 'STRONG', 'MODERATE']:
        track = get_optimal_track(result, strength)
        print(f"  {strength}信号 → {track}")

# ── 自检 ──
if __name__ == "__main__":
    assert len(STATE) == 11, f"STATE count={len(STATE)} != 11"
    assert "BEAR_EARLY" in STATE
    assert "CHOP_MID" in STATE
    print("✅ state_engine 自检通过")
