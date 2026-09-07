#!/usr/bin/env python3
"""
paper_orders_to_settled.py — IC数据打通桥
把 data/paper_orders.jsonl 的CLOSED记录转换为 wuqu_paper_settled.jsonl 格式
接入位置：brahma_crontab.txt（每4小时）
2026-09-07 设计院封印
"""
import json, time, sys
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
SRC  = DATA / 'paper_orders.jsonl'
DST  = DATA / 'wuqu_paper_settled.jsonl'

def _outcome(close_reason: str, pnl: float) -> str:
    r = str(close_reason).upper()
    if 'TP' in r or pnl > 0: return 'TP1'
    if 'TIMEOUT' in r:        return 'TIMEOUT'
    return 'SL'

def bridge():
    if not SRC.exists():
        print('[bridge] paper_orders.jsonl 不存在')
        return 0

    # 读取已存在的signal_ids，防止重复写入
    existing_ids = set()
    if DST.exists():
        for line in DST.read_text().splitlines():
            try: existing_ids.add(json.loads(line).get('signal_id',''))
            except: pass

    orders = []
    for line in SRC.read_text().splitlines():
        try:
            d = json.loads(line)
            if d.get('status') == 'CLOSED' and d.get('pnl') is not None:
                orders.append(d)
        except: pass

    written = 0
    new_lines = []
    for o in orders:
        sid = o.get('id', o.get('signal_id', ''))
        if sid in existing_ids:
            continue

        fill_price = float(o.get('fill_price') or o.get('entry') or 0)
        close_price = float(o.get('close_price') or 0)
        pnl = float(o.get('pnl', 0))
        open_ts  = float(o.get('filled_at') or o.get('ts') or time.time())
        close_ts = float(o.get('close_at') or time.time())
        hold_hours = round((close_ts - open_ts) / 3600, 2)
        pnl_pct = round((pnl / float(o.get('notional', 1))) * 100, 4) if o.get('notional') else 0.0
        outcome = _outcome(o.get('close_reason', ''), pnl)

        record = {
            'signal_id':    sid,
            'symbol':       o.get('symbol', ''),
            'signal_dir':   o.get('side', ''),
            'score':        float(o.get('score') or 0),
            'grade':        float(o.get('grade') or 0),
            'gap_pct':      0.0,
            'entry_price':  fill_price,
            'stop_loss':    float(o.get('sl', 0)),
            'tp1':          float(o.get('tp', 0)),
            'hold_hours':   hold_hours,
            'open_ts':      open_ts,
            'source':       o.get('source', 'paper_engine'),
            'regime':       o.get('regime', 'UNKNOWN'),
            'result':       'WIN' if pnl > 0 else 'LOSS',
            'close_price':  close_price,
            'pnl_pct':      pnl_pct,
            'close_ts':     close_ts,
            'outcome':      outcome,
            '_system_version': 'bridge-v1',
            '_data_quality': 'paper_orders_bridge',
        }
        new_lines.append(json.dumps(record))
        existing_ids.add(sid)
        written += 1

    if new_lines:
        with open(DST, 'a') as f:
            f.write('\n'.join(new_lines) + '\n')

    print(f'[bridge] 写入 {written} 条新记录 → wuqu_paper_settled.jsonl (总计 {len(existing_ids)} 条)')
    return written

if __name__ == '__main__':
    bridge()
