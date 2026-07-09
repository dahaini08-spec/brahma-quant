#!/usr/bin/env python3
"""
decision_log.py — P1 决策日志（TradingAgents启发）
每次send_strategy_dd1时自动写入data/decision_log.jsonl
格式：时间/评分/各维度/体制/gap/grade/方向/入场区/RR

用法：
  from decision_log import log_decision
  log_decision(symbol, direction, score, regime, entry_lo, entry_hi,
               stop_loss, tp1, rr1, rr2, grade=None, breakdown=None)
"""
import json, time
from pathlib import Path
from datetime import datetime, timezone

LOG_FILE = Path(__file__).parent.parent / 'data' / 'decision_log.jsonl'

def log_decision(
    symbol: str,
    direction: str,
    score: float,
    regime: str,
    entry_lo: float,
    entry_hi: float,
    stop_loss: float,
    tp1: float,
    tp2: float = 0,
    rr1: float = 0,
    rr2: float = 0,
    grade: str = None,
    grade_score: int = None,
    breakdown: dict = None,
    bull_score: float = 0,
    bear_score: float = 0,
    sentiment_score: float = 0,
    signal_label: str = None,
    task_id: str = None,
    extra: dict = None,
) -> dict:
    """写入一条决策日志，返回写入的记录"""
    import urllib.request
    price = 0.0
    try:
        sym_api = symbol.upper()
        if not sym_api.endswith('USDT'):
            sym_api += 'USDT'
        r = json.loads(urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym_api}', timeout=4
        ).read())
        price = float(r.get('price', 0))
    except Exception:
        pass

    entry_mid = (entry_lo + entry_hi) / 2 if entry_hi else entry_lo
    gap_pct = round((entry_lo - price) / price * 100, 3) if price else 0

    # 自动计算rr1/rr2
    risk = abs(entry_mid - stop_loss) if stop_loss else 1
    if rr1 == 0 and tp1 and entry_mid:
        rr1 = round(abs(tp1 - entry_mid) / risk, 2) if risk else 0
    if rr2 == 0 and tp2 and entry_mid:
        rr2 = round(abs(tp2 - entry_mid) / risk, 2) if risk else 0

    # 自动生成信号等级标签
    if signal_label is None:
        if score >= 170:
            signal_label = '神级🏆'
        elif score >= 155:
            signal_label = 'A级✅'
        elif score >= 140:
            signal_label = 'B级📌'
        elif score >= 120:
            signal_label = 'C级👀'
        else:
            signal_label = 'D级⚠️'

    record = {
        'ts': int(time.time()),
        'dt': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        'symbol': symbol.replace('USDT', ''),
        'direction': direction,
        'score': round(score, 1),
        'signal_label': signal_label,
        'regime': regime,
        'grade': grade,
        'grade_score': grade_score,
        'price_at_decision': round(price, 4),
        'entry_lo': entry_lo,
        'entry_hi': entry_hi,
        'entry_mid': round(entry_mid, 4),
        'stop_loss': stop_loss,
        'tp1': tp1,
        'tp2': tp2,
        'rr1': rr1,
        'rr2': rr2,
        'gap_pct': gap_pct,
        'bull_score': bull_score,
        'bear_score': bear_score,
        'sentiment_score': sentiment_score,
        'breakdown': breakdown or {},
        'task_id': task_id,
        'extra': extra or {},
    }

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')

    pass  # [静默]
    return record


def read_recent(n: int = 20) -> list:
    """读取最近n条决策日志"""
    if not LOG_FILE.exists():
        return []
    lines = LOG_FILE.read_text(encoding='utf-8').strip().split('\n')
    records = []
    for line in lines[-n:]:
        try:
            rec = json.loads(line)
            if 'symbol' in rec:  # 只读有效记录
                records.append(rec)
        except Exception:
            pass
    return records


def stats() -> dict:
    """简单统计"""
    records = read_recent(500)
    if not records:
        return {'total': 0}
    by_sym = {}
    for r in records:
        k = f"{r['symbol']}_{r['direction']}"
        by_sym.setdefault(k, []).append(r)
    return {
        'total': len(records),
        'by_symbol_dir': {k: len(v) for k, v in by_sym.items()},
        'avg_score': round(sum(r['score'] for r in records) / len(records), 1),
        'avg_rr1': round(sum(r['rr1'] for r in records if r['rr1']) / max(sum(1 for r in records if r['rr1']), 1), 2),
        'label_dist': {
            l: sum(1 for r in records if r.get('signal_label', '').startswith(l))
            for l in ['神级', 'A级', 'B级', 'C级', 'D级']
        },
    }


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'stats':
        import pprint
        pprint.pprint(stats())
    elif len(sys.argv) > 1 and sys.argv[1] == 'recent':
        for r in read_recent(10):
            print(f"{r['dt']} {r['symbol']} {r['direction']} score={r['score']} {r['signal_label']} rr1={r['rr1']}")
    else:
        print('用法: python3 decision_log.py stats|recent')
