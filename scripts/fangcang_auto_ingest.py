#!/usr/bin/env python3
"""
梵天方仓自动入库 [封印 2026-10-26 苏摩111]
接入位置：prediction_settler.py → 每次HIT_TP/HIT_SL后调用
功能：把结算完成的预测记录写入方仓（fangcang_merged_v2.json）
= 梵天自我学习闭环终点：预测→验证→学习
"""
import json, time
from pathlib import Path
from datetime import datetime, timezone

BASE     = Path(__file__).parent.parent
FC_PATH  = BASE / 'data' / 'fangcang_merged_v2.json'
FC_CRYPTO = BASE / 'data' / 'fangcang_merged_v2_crypto.json'
INGEST_LOG = BASE / 'data' / 'fangcang_ingest_log.jsonl'

# 状态→future_ret方向映射
STATUS_SIGN = {
    'HIT_TP1': +1,
    'HIT_TP2': +1,
    'HIT_SL':  -1,
}

def ingest_from_prediction(record: dict) -> bool:
    """
    将一条结算预测转换为方仓案例格式并写入
    方仓字段：symbol/timeframe/ts_squeeze_start/ts_burst/direction/
              min_bb_width/future_ret/future_max/future_min/regime/source
    """
    status = record.get('status', '')
    if status not in STATUS_SIGN:
        return False  # OPEN/EXPIRED不入库

    direction_map = {'LONG': 'UP', 'SHORT': 'DOWN'}
    direction = direction_map.get(record.get('direction', ''), 'UP')
    pnl       = record.get('pnl_pct', 0) or 0
    sign      = STATUS_SIGN[status]
    rr        = record.get('rr', 1.0) or 1.0
    regime    = record.get('regime', 'CHOP_MID')

    # 构造方仓案例
    ts = record.get('ts', time.time())
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    signals = record.get('signals', {})

    case = {
        'symbol':           record.get('symbol', 'BTCUSDT').replace('USDT', ''),
        'timeframe':        '1h',
        'ts_squeeze_start': dt,
        'ts_burst':         dt,
        'direction':        direction,
        'min_bb_width':     2.0 - signals.get('confluence', 0) / 100,  # 越高置信越窄
        'future_ret':       round(abs(pnl) * sign, 3),
        'future_max':       round(abs(pnl) * rr * 0.6, 3) if sign > 0 else 0,
        'future_min':       round(-abs(pnl) * 0.3, 3),
        'regime':           regime,
        'source':           'brahma_prediction_ledger',  # 标记来源
        'settle_status':    status,
        'original_pid':     record.get('pid', ''),
        '_ts':              ts,
    }

    # 追加到两个方仓文件
    for fc_path in [FC_PATH, FC_CRYPTO]:
        if not fc_path.exists():
            continue
        try:
            cases = json.loads(fc_path.read_text())
            cases.append(case)
            fc_path.write_text(json.dumps(cases, ensure_ascii=False))
        except Exception:
            pass

    # 写入入库日志
    with open(INGEST_LOG, 'a') as f:
        log = {'ts': ts, 'pid': record.get('pid'), 'symbol': case['symbol'],
               'direction': direction, 'pnl': pnl, 'status': status, 'regime': regime}
        f.write(json.dumps(log) + '\n')

    return True

def query_ingest_stats() -> dict:
    """查询入库统计"""
    if not INGEST_LOG.exists():
        return {'total': 0, 'wins': 0, 'wr': 0.0}
    logs = [json.loads(l) for l in INGEST_LOG.read_text().splitlines() if l.strip()]
    wins = [l for l in logs if l.get('pnl', 0) > 0]
    return {
        'total': len(logs),
        'wins':  len(wins),
        'wr':    round(len(wins)/len(logs), 3) if logs else 0.0,
        'avg_pnl': round(sum(l.get('pnl',0) for l in logs)/len(logs), 3) if logs else 0.0
    }

if __name__ == '__main__':
    # 测试：模拟一条结算记录入库
    test_record = {
        'pid': 'test0001',
        'symbol': 'ETHUSDT',
        'direction': 'SHORT',
        'ts': time.time() - 3600,
        'entry_lo': 2387.0, 'entry_hi': 2395.0,
        'sl': 2457.0, 'tp1': 2307.0, 'tp2': 2272.0,
        'rr': 1.92, 'regime': 'CHOP_MID',
        'status': 'HIT_TP1',
        'pnl_pct': 3.35,
        'signals': {'lsr_retail': 3.388, 'confluence': 168.0}
    }
    ok = ingest_from_prediction(test_record)
    print(f'入库: {"✅" if ok else "❌"}')
    print(f'统计: {query_ingest_stats()}')

    # 验证方仓新增
    import json
    fc = json.loads(Path(FC_PATH).read_text()) if FC_PATH.exists() else []
    brahma_cases = [c for c in fc if c.get('source') == 'brahma_prediction_ledger']
    print(f'方仓中梵天自生成案例: {len(brahma_cases)}条')
