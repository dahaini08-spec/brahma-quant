#!/usr/bin/env python3
"""
brahma_events.py — 梵天2.0 L-1证据中枢：append-only事件流 [苏摩111 2026-09-28]

宪法铁律（W0新增）:
1. events.jsonl 只许APPEND禁止UPDATE——修正=追加correction事件（corr_of指向原事件id）
2. 每行一个不可变事件: signal_fired/gate_blocked/order_opened/order_closed/pnl_settled/correction
3. 单一写入者: 结算链(paper_ledger/paper_executor/paper_tp_monitor)经此模块写，其他进程只读
4. 翻案从物理上不可能: 本模块无任何UPDATE/REWRITE函数（append_only强制）

接入位置: scripts/paper_ledger.py（open/close记账点同步append事件）
          scripts/brahma_events.py（新, 本文件）
消费方:  IC周审(ic_weekly) / 桶效力月审 / daily_review / divergence_report(只读)
"""
from pathlib import Path
import json, time, threading

EVENTS_FILE = Path(__file__).parent.parent / 'data' / 'events.jsonl'
_lock = threading.Lock()  # 进程内锁; 跨进程由单一写入者铁律保证

_EVENT_TYPES = {
    'signal_fired', 'gate_blocked', 'order_opened', 'order_closed',
    'pnl_settled', 'correction', 'circuit_breaker',
    # [P1决策生命周期 2026-09-28 苏摩111] D-10深度决策引擎事件（append-only同设计）
    'decision_made', 'decision_upgrade', 'decision_invalidated',
}


def append(event_type: str, payload: dict, corr_of: str = '') -> str:
    """追加一个不可变事件，返回event_id。禁止的事件类型或UPDATE路径直接raise。"""
    if event_type not in _EVENT_TYPES:
        raise ValueError(f'illegal event_type: {event_type} (allowed: {sorted(_EVENT_TYPES)})')
    eid = f'EV-{int(time.time()*1000)}-{event_type[:4].upper()}'
    rec = {
        'event_id': eid,
        'ts': int(time.time() * 1000),
        'ts_iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'type': event_type,
        'payload': payload,
    }
    if corr_of:
        rec['corr_of'] = corr_of  # 修正事件指向原事件，原事件永远保留
    line = json.dumps(rec, ensure_ascii=False)
    _lock.acquire()
    try:
        with EVENTS_FILE.open('a', encoding='utf-8') as f:
            f.write(line + '\n')
    finally:
        _lock.release()
    return eid


def read_all(event_type: str = '') -> list:
    """只读接口：按类型过滤读全部事件（消费方专用）。"""
    if not EVENTS_FILE.exists():
        return []
    out = []
    for line in EVENTS_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if event_type and rec.get('type') != event_type:
            continue
        out.append(rec)
    return out


def corrections_of(event_id: str) -> list:
    """读某事件的所有correction（结算视角=最新correction生效，原始事件留证）。"""
    return [r for r in read_all('correction') if r.get('corr_of') == event_id]


def stats() -> dict:
    by = {}
    for r in read_all():
        by[r['type']] = by.get(r['type'], 0) + 1
    return {'total': sum(by.values()), 'by_type': by}


if __name__ == '__main__':
    print(json.dumps(stats(), ensure_ascii=False))
