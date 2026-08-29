"""
signal_queue_writer.py — 统一信号写入接口
所有信号源调用此函数写入 data/signal_queue.jsonl
paper_engine.py 统一消费
2026-08-26 苏摩111
[P0修复 2026-08-26] push_signal补全score/regime/direction字段，修复470条空壳根因
"""
import json, time
from pathlib import Path

SIGNAL_QUEUE = Path(__file__).parent.parent / 'data' / 'signal_queue.jsonl'

def push_signal(symbol: str, source: str, meta: dict = None,
                score: float = None, regime: str = None,
                direction: str = None, signal_id: str = None,
                grade: float = None, sl_pct: float = None,
                entry_lo: float = None, entry_hi: float = None):
    """写入信号队列（完整字段版）"""
    # [2026-08-29 苏摩111修复] 最后一道防线：全局体制死穴封禁
    _DEAD_SHORT = {'BEAR_RECOVERY', 'BULL_EARLY', 'BULL_TREND'}  # BULL_TREND SHORT 实盘WR=0% n=4
    _DEAD_LONG  = {'BEAR_TREND'}
    _regime = (regime or '').upper()
    _dir    = (direction or '').upper()
    if _dir == 'SHORT' and _regime in _DEAD_SHORT:
        return  # 封禁：{symbol} {_regime} SHORT 死穴 WR=0%
    if _dir == 'LONG' and _regime in _DEAD_LONG:
        return  # 封禁：{symbol} {_regime} LONG 死穴
    SIGNAL_QUEUE.parent.mkdir(exist_ok=True)
    record = {
        'symbol':    symbol,
        'source':    source,
        'ts':        time.time(),
        'ts_iso':    __import__('datetime').datetime.utcnow().isoformat(),
        'score':     score,
        'regime':    regime,
        'direction': direction,
        'signal_id': signal_id,
        'grade':     grade,
        'sl_pct':    sl_pct,
        'entry_lo':  entry_lo,
        'entry_hi':  entry_hi,
        'status':    'PENDING',
        'meta':      meta or {},
    }
    with open(SIGNAL_QUEUE, 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')

def push_signals(symbols: list, source: str, meta: dict = None):
    """批量写入（兼容旧接口）"""
    for sym in symbols:
        push_signal(sym, source, meta)

def push_signal_full(signal: dict):
    """从完整信号字典写入队列（主要入口）"""
    push_signal(
        symbol    = signal.get('symbol',''),
        source    = signal.get('source', 'brahma'),
        meta      = signal.get('meta'),
        score     = float(signal.get('score_final') or signal.get('score') or 0),
        regime    = signal.get('regime',''),
        direction = signal.get('direction') or signal.get('signal_dir',''),
        signal_id = signal.get('signal_id',''),
        grade     = signal.get('grade_num') or signal.get('grade'),
        sl_pct    = signal.get('sl_pct'),
        entry_lo  = signal.get('entry_lo'),
        entry_hi  = signal.get('entry_hi'),
    )
