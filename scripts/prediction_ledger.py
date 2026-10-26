#!/usr/bin/env python3
"""
梵天预测记录器 [封印 2026-10-26 苏摩111]
接入位置：brahma_deep_analysis.py format_vip() 末尾调用
功能：每次VIP输出自动写入预测记录，4H后由prediction_settler结算
= 自我进化闭环起点
"""
import json, time, hashlib
from pathlib import Path
from datetime import datetime, timezone

LEDGER_PATH = Path(__file__).parent.parent / 'data' / 'prediction_log.jsonl'

def record_prediction(symbol: str, direction: str, entry_lo: float, entry_hi: float,
                       sl: float, tp1: float, tp2: float, rr: float,
                       signals: dict = None, regime: str = '') -> str:
    """
    记录一次VIP预测到 data/prediction_log.jsonl
    返回：prediction_id（用于后续结算匹配）
    """
    ts = time.time()
    pid = hashlib.md5(f"{symbol}{ts}".encode()).hexdigest()[:8]
    record = {
        'pid': pid,
        'symbol': symbol,
        'ts': ts,
        'dt': datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'direction': direction,           # LONG / SHORT / WAIT
        'entry_lo': entry_lo,
        'entry_hi': entry_hi,
        'sl': sl,
        'tp1': tp1,
        'tp2': tp2,
        'rr': rr,
        'regime': regime,
        'signals': signals or {},         # lsr/oi/taker/bb_squeeze等核心信号快照
        'status': 'OPEN',                 # OPEN → HIT_ENTRY / HIT_SL / HIT_TP1 / HIT_TP2 / EXPIRED
        'settle_ts': None,
        'pnl_pct': None,
    }
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH, 'a') as f:
        f.write(json.dumps(record) + '\n')
    return pid

def query_open(symbol: str = None) -> list:
    """查询未结算预测"""
    if not LEDGER_PATH.exists():
        return []
    records = [json.loads(l) for l in LEDGER_PATH.read_text().splitlines() if l.strip()]
    open_r = [r for r in records if r.get('status') == 'OPEN']
    if symbol:
        open_r = [r for r in open_r if r.get('symbol') == symbol]
    return open_r

def query_stats(symbol: str = None, last_n: int = 50) -> dict:
    """统计预测准确率"""
    if not LEDGER_PATH.exists():
        return {'n': 0, 'wr': 0, 'ev': 0}
    records = [json.loads(l) for l in LEDGER_PATH.read_text().splitlines() if l.strip()]
    settled = [r for r in records if r.get('status') not in ('OPEN', 'EXPIRED')]
    if symbol:
        settled = [r for r in settled if r.get('symbol') == symbol]
    settled = settled[-last_n:]
    if not settled:
        return {'n': 0, 'wr': 0.0, 'ev': 0.0}
    wins = [r for r in settled if r.get('pnl_pct', 0) > 0]
    wr = len(wins) / len(settled)
    ev = sum(r.get('pnl_pct', 0) for r in settled) / len(settled)
    return {'n': len(settled), 'wr': round(wr, 3), 'ev': round(ev, 3)}

if __name__ == '__main__':
    # 测试
    pid = record_prediction(
        symbol='ETHUSDT', direction='SHORT',
        entry_lo=2387.0, entry_hi=2395.0,
        sl=2457.0, tp1=2307.0, tp2=2272.0, rr=1.92,
        regime='CHOP_MID',
        signals={'lsr_retail': 3.388, 'lsr_top': 3.052, 'oi_8h': 1.496,
                 'bb_squeeze': True, 'taker': 0.761}
    )
    print(f'记录成功 pid={pid}')
    print(f'未结算: {len(query_open())}条')
    print(f'统计: {query_stats()}')
