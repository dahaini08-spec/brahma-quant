"""
fangcang_experience_engine.py — 40年方仓经验引擎
设计院封印 2026-08-29 苏摩111

使命：
  把20392条6.5年K线案例（BTC/ETH/SOL/美股/黄金/原油）蒸馏为
  「40年顶级交易员的条件反射式经验」，注入brahma_core评分主链。

经验矩阵来源：
  data/fangcang_experience_matrix_v2.json
  - 69条规律：体制×方向×周期×RSI×burst力度
  - 最强：BULL_EARLY:LONG:4h WR=100% n=1186
  - 最弱陷阱：BEAR_EARLY:SHORT:4h WR=0% n=1161

接入位置：
  brahma_core.py → fangcang层 → fangcang_experience_engine.get_exp_adj()

核心逻辑（40年经验的三层判断）：
  Layer1: 体制方向直觉 —— 这个体制/方向，历史WR是多少？
  Layer2: 周期共振感   —— 当前触发周期，同体制+方向历史表现？
  Layer3: RSI+burst    —— 当前技术状态与历史最相似案例的WR偏差
"""

import json
import time
import logging
from pathlib import Path
from typing import Optional

_log = logging.getLogger('brahma.fangcang_exp')

BASE      = Path(__file__).parent.parent
EXP_PATH  = BASE / 'data' / 'fangcang_experience_matrix_v2.json'

_EXP_CACHE: dict = {}
_EXP_LOADED_AT: float = 0.0
_EXP_TTL = 3600  # 1小时重载一次


def _load_matrix() -> dict:
    global _EXP_CACHE, _EXP_LOADED_AT
    if _EXP_CACHE and (time.time() - _EXP_LOADED_AT) < _EXP_TTL:
        return _EXP_CACHE
    try:
        data = json.loads(EXP_PATH.read_text())
        _EXP_CACHE = data.get('matrix', {})
        _EXP_LOADED_AT = time.time()
        _log.debug(f'[exp_engine] 经验矩阵加载: {len(_EXP_CACHE)}条规律')
    except Exception as e:
        _log.warning(f'[exp_engine] 矩阵加载失败: {e}')
        _EXP_CACHE = {}
    return _EXP_CACHE


def _rsi_bucket(rsi: float) -> str:
    if rsi < 30:   return '0_30'
    if rsi < 45:   return '30_45'
    if rsi < 55:   return '45_55'
    if rsi < 70:   return '55_70'
    return '70_100'


def _burst_bucket(burst: float) -> str:
    if burst < 0.5:  return '0_0.5'
    if burst < 1.5:  return '0.5_1.5'
    return '1.5_99'


def get_exp_adj(
    regime:      str,
    signal_dir:  str,
    timeframe:   str   = '4h',
    rsi:         float = 50.0,
    burst_mult:  float = 1.0,
    current_bbw: float = 0.01,
    n_min:       int   = 10,
) -> dict:
    """
    查询经验矩阵，返回基于40年历史的评分调整。

    参数:
      regime      : 当前体制 BULL_TREND / BEAR_TREND / CHOP_MID 等
      signal_dir  : LONG / SHORT
      timeframe   : 信号触发周期 15m/1h/4h/1d
      rsi         : 当前RSI_1H
      burst_mult  : 方仓突破ATR倍数（avg_burst_atr_mult）
      current_bbw : 当前BBW

    返回:
      {
        'adj':          float,  # 评分调整（-8 ~ +8）
        'confidence':   str,    # HIGH/MED/LOW
        'rule_hit':     str,    # 命中的规律键
        'wr':           float,  # 历史WR
        'n':            int,    # 样本数
        'reasoning':    str,    # 40年交易员的判断文字
      }
    """
    matrix = _load_matrix()
    if not matrix:
        return {'adj': 0.0, 'confidence': 'NONE', 'rule_hit': '', 'wr': 0.5, 'n': 0, 'reasoning': '矩阵未加载'}

    regime = regime.upper()
    signal_dir = signal_dir.upper()
    rsi_key    = _rsi_bucket(rsi)
    burst_key  = _burst_bucket(burst_mult)
    tf_norm    = timeframe.lower().replace('min', 'm').replace('h', 'h')

    # ── 陷阱拦截（回测铁证 2026-08-29 苏摩111 n=298）──────────────────
    # BEAR_TREND:SHORT:RSI<45 WR=35% n=298 → 严重陷阱
    # 原因：BEAR_TREND低RSI=超卖随时反弹，做空在最差位置
    if regime == 'BEAR_TREND' and signal_dir == 'SHORT' and rsi < 45:
        return {
            'adj': -6.0, 'confidence': 'HIGH',
            'rule_hit': 'TRAP:BEAR_TREND:SHORT:RSI<45',
            'wr': 0.35, 'ev': -0.3, 'n': 298,
            'reasoning': 'BEAR_TREND做空+RSI<45=超卖陷阱WR=35%(n=298铁证)，等RSI>50再进入',
        }

    # BULL_EARLY:LONG:RSI<30 小样本陷阱
    if regime == 'BULL_EARLY' and signal_dir == 'LONG' and rsi < 30:
        return {
            'adj': -4.0, 'confidence': 'LOW',
            'rule_hit': 'TRAP:BULL_EARLY:LONG:RSI<30',
            'wr': 0.25, 'ev': -0.2, 'n': 8,
            'reasoning': 'BULL_EARLY做多RSI<30=假突破风险WR=25%，小样本谨慎',
        }

    # ── 三层查询，优先级：精确 > 周期 > 体制方向 ──────────────────────
    candidate_keys = [
        # 精确：体制+方向+RSI+burst（最高权重）
        f"{regime}:{signal_dir}:RSI{rsi_key}",
        f"{regime}:{signal_dir}:burst{burst_key}",
        # 周期感知：体制+方向+时间周期
        f"{regime}:{signal_dir}:{tf_norm}",
        # 基础：体制+方向
        f"{regime}:{signal_dir}",
        # Elite精华组合
        f"ELITE:{regime}:{signal_dir}:RSI{rsi_key}:burst>1.5",
    ]

    best_hit = None
    best_wr  = 0.5
    best_n   = 0
    best_key = ''

    for key in candidate_keys:
        entry = matrix.get(key)
        if not entry:
            continue
        n  = entry.get('n', 0)
        wr = entry.get('wr', 0.5)
        if n >= n_min and abs(wr - 0.5) > abs(best_wr - 0.5):
            best_hit = entry
            best_wr  = wr
            best_n   = n
            best_key = key

    if not best_hit:
        return {'adj': 0.0, 'confidence': 'NONE', 'rule_hit': '', 'wr': 0.5, 'n': 0, 'reasoning': '无匹配规律'}

    # ── 评分计算 ──────────────────────────────────────────────────────
    # WR偏离0.5越大，adj越强
    # WR=1.0 → +8  WR=0.7 → +4.8  WR=0.5 → 0  WR=0.3 → -4.8  WR=0.0 → -8
    raw_adj = (best_wr - 0.5) * 16.0
    raw_adj = max(-8.0, min(8.0, raw_adj))

    # 样本量加权
    n_weight = min(1.0, best_n / 500.0)
    adj = round(raw_adj * n_weight, 2)

    # 置信度
    if best_n >= 500:   confidence = 'HIGH'
    elif best_n >= 100: confidence = 'MED'
    else:               confidence = 'LOW'

    # 40年交易员的判断文字
    dir_cn = '做多' if signal_dir == 'LONG' else '做空'
    if best_wr >= 0.85:
        reasoning = f'{regime}体制{dir_cn}，历史胜率极高={best_wr:.0%}(n={best_n})，机构方向明确'
    elif best_wr >= 0.65:
        reasoning = f'{regime}体制{dir_cn}，历史胜率偏高={best_wr:.0%}(n={best_n})，顺势'
    elif best_wr <= 0.15:
        reasoning = f'{regime}体制{dir_cn}，历史胜率极低={best_wr:.0%}(n={best_n})，逆势死穴'
    elif best_wr <= 0.35:
        reasoning = f'{regime}体制{dir_cn}，历史胜率偏低={best_wr:.0%}(n={best_n})，逆流'
    else:
        reasoning = f'{regime}体制{dir_cn}，历史胜率中性={best_wr:.0%}(n={best_n})'

    return {
        'adj':        adj,
        'confidence': confidence,
        'rule_hit':   best_key,
        'wr':         round(best_wr, 3),
        'ev':         round(best_hit.get('ev', 0.0), 3),
        'n':          best_n,
        'reasoning':  reasoning,
    }


# ── CLI验证 ───────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    regime    = sys.argv[1] if len(sys.argv) > 1 else 'BULL_EARLY'
    direction = sys.argv[2] if len(sys.argv) > 2 else 'LONG'
    tf        = sys.argv[3] if len(sys.argv) > 3 else '4h'
    rsi       = float(sys.argv[4]) if len(sys.argv) > 4 else 60.0
    burst     = float(sys.argv[5]) if len(sys.argv) > 5 else 1.8

    r = get_exp_adj(regime, direction, tf, rsi, burst)
    print(f'\n=== 40年经验判断 ===')
    print(f'输入: {regime} {direction} {tf} RSI={rsi} burst={burst}x')
    print(f'adj       = {r["adj"]:+.2f}')
    print(f'confidence= {r["confidence"]}')
    print(f'rule_hit  = {r["rule_hit"]}')
    print(f'WR        = {r["wr"]:.0%}  EV={r["ev"]:+.3f}  n={r["n"]}')
    print(f'判断      = {r["reasoning"]}')
