"""
signal_queue_writer.py — 统一信号写入接口
所有信号源调用此函数写入 data/signal_queue.jsonl
paper_engine.py 统一消费
2026-08-26 苏摩111
"""
import json, time
from pathlib import Path

SIGNAL_QUEUE = Path(__file__).parent.parent / 'data' / 'signal_queue.jsonl'

def push_signal(symbol: str, source: str, meta: dict = None):
    """写入信号队列"""
    SIGNAL_QUEUE.parent.mkdir(exist_ok=True)
    record = {
        'symbol': symbol,
        'source': source,
        'ts': int(time.time()),
        'meta': meta or {},
    }
    with open(SIGNAL_QUEUE, 'a') as f:
        f.write(json.dumps(record) + '\n')

def push_signals(symbols: list, source: str, meta: dict = None):
    """批量写入"""
    for sym in symbols:
        push_signal(sym, source, meta)
