"""
upgrade_v2/regime_health_guard.py
体制健康守护 — 记录每笔信号的胜负结果，为达摩院统计体制胜率提供数据源

接口：
  record_outcome(symbol, regime, direction, outcome) → None
  get_regime_stats(regime, direction, window=200) → dict
"""

import json, time, os
from pathlib import Path

_BASE = Path(__file__).parent.parent
_DATA = _BASE / 'data'
_GUARD_FILE = _DATA / 'regime_health_outcomes.jsonl'


def record_outcome(
    symbol: str,
    regime: str,
    direction: str,
    outcome: str,          # 'WIN' | 'LOSS' | 'BREAKEVEN'
    pnl_pct: float = 0.0,
    score: float = 0.0,
    extra: dict = None,
) -> None:
    """
    记录一笔信号结果到 regime_health_outcomes.jsonl
    被 live_signal_settler.py 在结算时调用
    """
    _DATA.mkdir(exist_ok=True)
    record = {
        'ts':        time.time(),
        'symbol':    symbol,
        'regime':    regime,
        'direction': direction,
        'outcome':   outcome,     # WIN / LOSS / BREAKEVEN
        'pnl_pct':   round(pnl_pct, 4),
        'score':     round(score, 1),
    }
    if extra:
        record['extra'] = extra

    with open(_GUARD_FILE, 'a') as f:
        f.write(json.dumps(record) + '\n')


def get_regime_stats(regime: str = None, direction: str = None, window: int = 200) -> dict:
    """
    统计指定体制+方向的胜率/EV（最近window笔）
    regime=None → 全量统计
    """
    if not _GUARD_FILE.exists():
        return {'n': 0, 'win_rate': 0.0, 'ev': 0.0, 'regime': regime, 'direction': direction}

    records = []
    with open(_GUARD_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if regime and r.get('regime') != regime:
                    continue
                if direction and r.get('direction') != direction:
                    continue
                records.append(r)
            except Exception:
                continue

    records = records[-window:]   # 取最近window笔
    if not records:
        return {'n': 0, 'win_rate': 0.0, 'ev': 0.0}

    wins   = sum(1 for r in records if r.get('outcome') == 'WIN')
    losses = sum(1 for r in records if r.get('outcome') == 'LOSS')
    ev     = sum(r.get('pnl_pct', 0) for r in records) / len(records)

    return {
        'n':          len(records),
        'wins':       wins,
        'losses':     losses,
        'win_rate':   round(wins / len(records), 4),
        'ev':         round(ev, 4),
        'regime':     regime,
        'direction':  direction,
    }


if __name__ == '__main__':
    # 测试
    record_outcome('BTCUSDT', 'BEAR_TREND', 'SHORT', 'WIN', pnl_pct=1.23, score=142)
    stats = get_regime_stats('BEAR_TREND', 'SHORT')
    print(f'BEAR_TREND SHORT 统计: {stats}')
