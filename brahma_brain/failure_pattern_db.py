#!/usr/bin/env python3
"""
failure_pattern_db.py — 梵天大脑 Layer A1: 失败模式数据库
设计院 2026-08-25 苏摩111立项封印

使命: 每次信号结算后自动分析失败原因
     积累后自动识别「这种RSI+LSR+体制组合历史上80%亏损」
     不是WR矩阵，是「失败原因分析库」

数据流:
  信号发出 → analyze() → 执行 → 结算
  结算 → record_outcome() → 存入failure_db.jsonl
  查询 → get_failure_patterns() → 返回当前组合的历史失败率

存储格式 (failure_db.jsonl):
  {"ts":..,"sym":..,"regime":..,"dir":..,"score":..,"rsi_1h":..,"lsr":..,"fr":..,"outcome":"WIN|LOSS|TIMEOUT","failure_dims":[...]}
"""
from __future__ import annotations
import os, sys, json, time, logging
from pathlib import Path
from typing import Optional

_BB = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BB)
if _BB not in sys.path: sys.path.insert(0, _BB)

logger = logging.getLogger('failure_pattern_db')

_DB_PATH = Path(_ROOT) / 'data' / 'failure_db.jsonl'
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# 失败维度标签
FAILURE_DIMS = {
    'rsi_overbought':   lambda r: r.get('rsi_1h', 50) > 70 and r.get('dir') == 'SHORT',
    'rsi_oversold':     lambda r: r.get('rsi_1h', 50) < 30 and r.get('dir') == 'LONG',
    'lsr_crowded_long': lambda r: r.get('lsr', 50) > 65 and r.get('dir') == 'LONG',
    'lsr_crowded_short':lambda r: r.get('lsr', 50) < 35 and r.get('dir') == 'SHORT',
    'fr_expensive_long':lambda r: r.get('fr', 0) > 0.01 and r.get('dir') == 'LONG',
    'bear_trend_long':  lambda r: 'BEAR_TREND' in r.get('regime','') and r.get('dir') == 'LONG',
    'chop_long':        lambda r: 'CHOP' in r.get('regime','') and r.get('dir') == 'LONG',
    'bull_trend_short': lambda r: 'BULL_TREND' in r.get('regime','') and r.get('dir') == 'SHORT',
    'score_below_120':  lambda r: r.get('score', 0) < 120,
    'high_atr_low_rr':  lambda r: r.get('atr_pct', 0) > 3.0,
}


def record_outcome(
    symbol: str,
    direction: str,
    score: float,
    regime: str,
    outcome: str,          # 'WIN' | 'LOSS' | 'TIMEOUT'
    rsi_1h: float = 50,
    lsr: float = 50,
    fr: float = 0.0,
    atr_pct: float = 2.0,
    extra: dict = None,
) -> dict:
    """记录一笔信号的结算结果并分析失败维度"""
    record = {
        'ts':      time.time(),
        'sym':     symbol.upper(),
        'dir':     direction.upper(),
        'score':   score,
        'regime':  regime,
        'outcome': outcome.upper(),
        'rsi_1h':  rsi_1h,
        'lsr':     lsr,
        'fr':      fr,
        'atr_pct': atr_pct,
    }

    # 分析失败维度
    failure_dims = []
    if outcome.upper() == 'LOSS':
        for dim_name, check_fn in FAILURE_DIMS.items():
            try:
                if check_fn(record):
                    failure_dims.append(dim_name)
            except Exception:
                pass
    record['failure_dims'] = failure_dims

    if extra:
        record.update({k: v for k, v in extra.items() if k not in record})

    # 追加写入
    try:
        with open(_DB_PATH, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as e:
        logger.warning(f'write failure_db: {e}')

    return record


def get_failure_patterns(
    symbol: str = '',
    direction: str = '',
    regime: str = '',
    min_n: int = 5,
) -> dict:
    """
    查询当前组合的历史失败模式
    返回: {
      'total': int, 'loss_n': int, 'loss_rate': float,
      'top_dims': [(dim_name, count, rate), ...],
      'warning': str,   # 如果失败率>60%给出警告
    }
    """
    records = _load_records()
    # 过滤
    filtered = records
    if symbol:
        filtered = [r for r in filtered if r.get('sym','').upper() == symbol.upper()]
    if direction:
        filtered = [r for r in filtered if r.get('dir','').upper() == direction.upper()]
    if regime:
        filtered = [r for r in filtered if regime.upper() in r.get('regime','').upper()]

    if len(filtered) < min_n:
        return {'total': len(filtered), 'loss_n': 0, 'loss_rate': 0.0,
                'top_dims': [], 'warning': f'样本不足({len(filtered)}<{min_n})'}

    losses    = [r for r in filtered if r.get('outcome') == 'LOSS']
    loss_n    = len(losses)
    loss_rate = loss_n / len(filtered)

    # 统计失败维度
    dim_counts: dict = {}
    for r in losses:
        for d in r.get('failure_dims', []):
            dim_counts[d] = dim_counts.get(d, 0) + 1

    top_dims = sorted(
        [(d, cnt, cnt / loss_n) for d, cnt in dim_counts.items()],
        key=lambda x: -x[1]
    )[:5]

    warning = ''
    if loss_rate > 0.6 and len(filtered) >= min_n:
        top_dim_str = top_dims[0][0] if top_dims else '未知'
        warning = (f'⚠️ {symbol}{direction} 历史失败率={loss_rate:.0%}(n={len(filtered)})，'
                   f'主因: {top_dim_str}')

    return {
        'total':     len(filtered),
        'loss_n':    loss_n,
        'loss_rate': round(loss_rate, 3),
        'top_dims':  top_dims,
        'warning':   warning,
    }


def get_current_risk_score(signal: dict) -> dict:
    """
    实时风险评分: 当前信号组合的历史失败率查询
    供analyze()注入，让梵天在信号发出前知道历史失败率
    """
    sym  = signal.get('symbol', '')
    dir_ = signal.get('signal_dir', signal.get('direction', ''))
    reg  = signal.get('regime', '')
    rsi  = signal.get('rsi_1h', 50)
    lsr_v = signal.get('lsr', 50)

    # 组合查询
    pattern = get_failure_patterns(symbol=sym, direction=dir_, regime=reg, min_n=3)

    # 失败维度实时匹配
    active_dims = []
    record_check = {'dir': dir_.upper(), 'regime': reg, 'rsi_1h': rsi, 'lsr': lsr_v,
                    'fr': signal.get('fr', 0), 'score': signal.get('score', 0)}
    for dim_name, check_fn in FAILURE_DIMS.items():
        try:
            if check_fn(record_check):
                active_dims.append(dim_name)
        except Exception:
            pass

    risk_note = ''
    if pattern['warning']:
        risk_note = pattern['warning']
    elif active_dims:
        risk_note = f'失败维度激活: {", ".join(active_dims[:3])}'

    return {
        'failure_pattern': pattern,
        'active_dims':     active_dims,
        'risk_note':       risk_note,
        'historical_loss_rate': pattern['loss_rate'],
    }


def _load_records() -> list:
    """加载所有结算记录"""
    records = []
    if not _DB_PATH.exists():
        return records
    try:
        with open(_DB_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f'read failure_db: {e}')
    return records


def get_stats() -> dict:
    """全局统计"""
    records = _load_records()
    total  = len(records)
    wins   = sum(1 for r in records if r.get('outcome') == 'WIN')
    losses = sum(1 for r in records if r.get('outcome') == 'LOSS')
    return {
        'total': total, 'wins': wins, 'losses': losses,
        'wr': round(wins/total, 3) if total else 0,
        'db_path': str(_DB_PATH),
    }


if __name__ == '__main__':
    # 冒烟测试
    print('=== 失败模式数据库冒烟测试 ===')
    r = record_outcome('ETHUSDT','SHORT',125,'CHOP_MID','LOSS',rsi_1h=72,lsr=71,fr=0.009)
    print(f'记录: {r["failure_dims"]}')
    p = get_failure_patterns('ETHUSDT','SHORT','CHOP')
    print(f'查询: {p}')
    s = get_stats()
    print(f'统计: {s}')
    print('✅ 冒烟测试通过')
