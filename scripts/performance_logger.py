#!/usr/bin/env python3
"""
performance_logger.py — 梵天交易结果持久化
设计院 · 苏摩111批准 · 2026-07-15

每笔交易从开仓到平仓完整记录到 live_performance_log.jsonl
供 online_learner_v2 读取做闭环校准
"""

import json, time
from pathlib import Path

BASE     = Path(__file__).parent.parent
PERF_LOG = BASE / 'data' / 'live_performance_log.jsonl'
WUQU     = BASE / 'data' / 'wuqu_positions.json'


def log_trade(record: dict):
    """写入一条交易记录（dedup by order_id/signal_id）"""
    PERF_LOG.parent.mkdir(exist_ok=True)
    record.setdefault('ts', time.time())
    record.setdefault('ts_iso', time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    # [fix 2026-07-18 苏摩111] 写入前检查重复，防止同一笔重复入账
    _dedup_key = str(record.get('order_id') or record.get('signal_id') or '')
    if _dedup_key and PERF_LOG.exists():
        for _line in PERF_LOG.read_text().strip().splitlines():
            try:
                _r = json.loads(_line)
                _k = str(_r.get('order_id') or _r.get('signal_id') or '')
                if _k and _k == _dedup_key:
                    return  # 已记录，不重复写入
            except Exception:
                pass
    with open(PERF_LOG, 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def sync_closed_positions():
    """
    扫描 wuqu_positions.json 中的 closed 仓位
    写入尚未记录到 performance_log 的交易
    """
    if not WUQU.exists():
        return 0

    raw = json.loads(WUQU.read_text())
    positions = raw if isinstance(raw, list) else []

    # 读取已记录的order_id集合
    logged_ids = set()
    if PERF_LOG.exists():
        for line in PERF_LOG.read_text().strip().splitlines():
            try:
                r = json.loads(line)
                oid = r.get('order_id') or r.get('signal_id')
                if oid:
                    logged_ids.add(str(oid))
            except Exception:
                pass

    synced = 0
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        if pos.get('status') != 'closed':
            continue

        oid = str(pos.get('order_id', pos.get('signal_id', '')))
        if oid and oid in logged_ids:
            continue

        # 计算结果
        entry  = float(pos.get('entry_price', 0))
        exit_  = float(pos.get('exit_price', 0))
        pnl    = float(pos.get('realized_pnl', 0))
        sl     = float(pos.get('sl_price', 0))

        if entry > 0 and exit_ > 0 and sl > 0:
            rr_actual = abs(exit_ - entry) / abs(entry - sl) if entry != sl else 0
        else:
            rr_actual = 0

        result = 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'BE')

        # MFE/MAE: 若仓位对象里有最高/最低价（由position_guardian填入）则计算
        _high = float(pos.get('max_price', 0))
        _low  = float(pos.get('min_price', 0))
        _side = pos.get('side', 'LONG')
        if entry > 0 and _high > 0 and _low > 0:
            if _side == 'LONG':
                mfe_pct = round((_high - entry) / entry * 100, 3)
                mae_pct = round((entry - _low)  / entry * 100, 3)
            else:
                mfe_pct = round((entry - _low)  / entry * 100, 3)
                mae_pct = round((_high - entry) / entry * 100, 3)
        else:
            mfe_pct = None
            mae_pct = None

        record = {
            'order_id'         : oid,
            'symbol'           : pos.get('symbol'),
            'side'             : _side,
            'entry_price'      : entry,
            'exit_price'       : exit_,
            'sl_price'         : sl,
            'qty'              : pos.get('qty', 0),
            'realized_pnl'     : pnl,
            'pnl_pct'          : pos.get('pnl_pct', 0),
            'rr_realized'      : round(rr_actual, 3),
            'result'           : result,
            'regime'           : pos.get('regime', ''),
            'score'            : pos.get('score', 0),
            'grade'            : pos.get('grade', 0),
            'direction'        : pos.get('direction', _side),
            'tp1'              : pos.get('take_profit', 0),
            'tp2'              : pos.get('tp2', 0),
            'exit_reason'      : pos.get('exit_reason', ''),
            'mfe_pct'          : mfe_pct,
            'mae_pct'          : mae_pct,
            'factors_snapshot' : pos.get('factors_snapshot', {}),
            'timing_badge'     : pos.get('timing_badge', ''),
            'hold_hours'       : round((time.time() - float(pos.get('opened_at', time.time()))) / 3600, 2),
            'settled_at'       : time.time(),
            'note'             : pos.get('note', ''),
        }
        log_trade(record)
        synced += 1
        print(f'  [perf_logger] {pos.get("symbol")} {result} pnl=${pnl:.4f} rr={rr_actual:.2f}')

    return synced


if __name__ == '__main__':
    n = sync_closed_positions()
    print(f'sync完成: {n}笔新记录写入')
    if PERF_LOG.exists():
        lines = PERF_LOG.read_text().strip().splitlines()
        print(f'总记录数: {len(lines)}')
