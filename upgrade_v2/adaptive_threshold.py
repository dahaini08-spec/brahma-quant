"""
upgrade_v2/adaptive_threshold.py
自适应门槛系统 — 根据近期胜率动态调整 min_score 门槛

接口：
  get_current_status(force_update=False) → dict
  get_threshold(symbol, regime, direction) → float
"""

import json, time, os
from pathlib import Path

_BASE   = Path(__file__).parent.parent
_DATA   = _BASE / 'data'
_STATUS_FILE = _DATA / 'adaptive_threshold_status.json'
_CACHE_TTL   = 1800   # 30分钟缓存

# 默认门槛（与 brahma_core.py min_score=120 对齐）
_DEFAULT_THRESHOLD = 120.0

# 胜率→门槛映射（达摩院铁证）
_WR_THRESHOLD_MAP = [
    (0.70, 115.0),   # 高胜率 → 适当放宽
    (0.65, 120.0),   # 标准胜率 → 标准门槛
    (0.60, 125.0),   # 胜率偏低 → 收紧
    (0.55, 130.0),   # 胜率差   → 大幅收紧
    (0.00, 135.0),   # 极差     → 严格限制
]


def get_current_status(force_update: bool = False) -> dict:
    """
    获取当前自适应门槛状态
    force_update=True: 强制重算（忽略缓存）
    被 live_signal_settler.py 每次结算后调用
    """
    # 读缓存
    if not force_update and _STATUS_FILE.exists():
        try:
            age = time.time() - _STATUS_FILE.stat().st_mtime
            if age < _CACHE_TTL:
                with open(_STATUS_FILE) as f:
                    return json.load(f)
        except Exception:
            pass

    # 读近期结果
    outcomes_file = _DATA / 'regime_health_outcomes.jsonl'
    recent = []
    if outcomes_file.exists():
        with open(outcomes_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        recent.append(json.loads(line))
                    except Exception:
                        pass
    recent = recent[-100:]  # 最近100笔

    # 计算综合胜率
    if recent:
        wins = sum(1 for r in recent if r.get('outcome') == 'WIN')
        win_rate = wins / len(recent)
        ev = sum(r.get('pnl_pct', 0) for r in recent) / len(recent)
    else:
        win_rate = 0.65   # 无数据时用基准
        ev = 0.0

    # 映射门槛
    threshold = _DEFAULT_THRESHOLD
    for wr_min, thr in _WR_THRESHOLD_MAP:
        if win_rate >= wr_min:
            threshold = thr
            break

    status = {
        'ts':               time.time(),
        'window_n':         len(recent),
        'recent_win_rate':  round(win_rate, 4),
        'recent_ev':        round(ev, 4),
        'current_threshold': threshold,
        'default_threshold': _DEFAULT_THRESHOLD,
        'adjustment':       round(threshold - _DEFAULT_THRESHOLD, 1),
        'note':             f'基于最近{len(recent)}笔实盘，WR={win_rate:.1%}',
    }

    # 写缓存
    _DATA.mkdir(exist_ok=True)
    try:
        with open(_STATUS_FILE, 'w') as f:
            json.dump(status, f, indent=2)
    except Exception:
        pass

    return status


def get_threshold(symbol: str = None, regime: str = None, direction: str = None) -> float:
    """
    获取指定品种/体制/方向的当前自适应门槛
    当前版本返回全局门槛；未来可按品种细分
    """
    try:
        status = get_current_status()
        return status.get('current_threshold', _DEFAULT_THRESHOLD)
    except Exception:
        return _DEFAULT_THRESHOLD


if __name__ == '__main__':
    status = get_current_status(force_update=True)
    print(f'当前自适应门槛状态: {json.dumps(status, indent=2, ensure_ascii=False)}')
