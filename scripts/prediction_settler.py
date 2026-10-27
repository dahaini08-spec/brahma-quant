#!/usr/bin/env python3
"""
梵天预测结算器 [封印 2026-10-26 苏摩111]
接入位置：brahma_crontab.txt → 每4H运行
功能：检查prediction_log.jsonl中OPEN记录，对照实时价格结算
结算结果写入prediction_settled.jsonl，并触发方仓自动入库
"""
import sys, json, time, urllib.request
from pathlib import Path

BASE = Path(__file__).parent.parent
LEDGER  = BASE / 'data' / 'prediction_log.jsonl'
SETTLED = BASE / 'data' / 'prediction_settled.jsonl'
EXPIRE_HOURS = 16   # [P1-A 2026-10-27 苏摩111] 24H→16H，加快WR积累

def _price(symbol: str) -> float:
    try:
        sym = symbol if symbol.endswith('USDT') else symbol + 'USDT'
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}'
        with urllib.request.urlopen(url, timeout=5) as r:
            return float(json.loads(r.read())['price'])
    except Exception:
        return 0.0

def _settle_one(record: dict, price: float) -> dict:
    """判断一条预测记录的结算状态"""
    r = record.copy()
    now = time.time()
    age_h = (now - r['ts']) / 3600

    direction = r['direction']
    entry_lo  = r.get('entry_lo', 0)
    entry_hi  = r.get('entry_hi', 0)
    sl        = r.get('sl', 0)
    tp1       = r.get('tp1', 0)
    tp2       = r.get('tp2', 0)

    # 超时：16H未触发 → EXPIRED
    if age_h > EXPIRE_HOURS:
        r['status'] = 'EXPIRED'
        r['settle_ts'] = now
        r['pnl_pct'] = 0.0
        return r

    # [P1-A 2026-10-27 苏摩111] MISS判断：价格已穿越入场区但未入场
    # SHORT: 价格跌破entry_lo且低于tp1 → 实际空单应该盈利，记录为MISS_WIN
    # SHORT: 价格涨过entry_hi且高于sl → 应止损，记录为MISS_LOSS
    if entry_lo > 0 and entry_hi > 0:
        if direction == 'SHORT':
            if price < entry_lo and tp1 > 0 and price <= tp1:
                r['status'] = 'MISS_WIN'; r['settle_ts'] = now
                r['pnl_pct'] = round((entry_lo - price) / entry_lo * 100, 3)
                return r
            elif price > entry_hi and sl > 0 and price >= sl:
                r['status'] = 'MISS_LOSS'; r['settle_ts'] = now
                r['pnl_pct'] = round((entry_hi - sl) / entry_hi * 100, 3)
                return r
        elif direction == 'LONG':
            if price > entry_hi and tp1 > 0 and price >= tp1:
                r['status'] = 'MISS_WIN'; r['settle_ts'] = now
                r['pnl_pct'] = round((price - entry_hi) / entry_hi * 100, 3)
                return r
            elif price < entry_lo and sl > 0 and price <= sl:
                r['status'] = 'MISS_LOSS'; r['settle_ts'] = now
                r['pnl_pct'] = round((sl - entry_lo) / entry_lo * 100, 3)
                return r

    # 价格在入场区间内 → 已入场，检查SL/TP
    if entry_lo <= price <= entry_hi or entry_lo == 0:
        in_entry = True
    else:
        in_entry = False

    if direction == 'LONG':
        if sl > 0 and price <= sl:
            r['status'] = 'HIT_SL'
            r['settle_ts'] = now
            mid = (entry_lo + entry_hi) / 2 if entry_lo > 0 else price
            r['pnl_pct'] = round((sl - mid) / mid * 100, 3) if mid > 0 else 0
        elif tp2 > 0 and price >= tp2:
            r['status'] = 'HIT_TP2'
            r['settle_ts'] = now
            mid = (entry_lo + entry_hi) / 2 if entry_lo > 0 else price
            r['pnl_pct'] = round((tp2 - mid) / mid * 100, 3) if mid > 0 else 0
        elif tp1 > 0 and price >= tp1:
            r['status'] = 'HIT_TP1'
            r['settle_ts'] = now
            mid = (entry_lo + entry_hi) / 2 if entry_lo > 0 else price
            r['pnl_pct'] = round((tp1 - mid) / mid * 100, 3) if mid > 0 else 0

    elif direction == 'SHORT':
        if sl > 0 and price >= sl:
            r['status'] = 'HIT_SL'
            r['settle_ts'] = now
            mid = (entry_lo + entry_hi) / 2 if entry_lo > 0 else price
            r['pnl_pct'] = round((mid - sl) / mid * 100, 3) if mid > 0 else 0
        elif tp2 > 0 and price <= tp2:
            r['status'] = 'HIT_TP2'
            r['settle_ts'] = now
            mid = (entry_lo + entry_hi) / 2 if entry_lo > 0 else price
            r['pnl_pct'] = round((mid - tp2) / mid * 100, 3) if mid > 0 else 0
        elif tp1 > 0 and price <= tp1:
            r['status'] = 'HIT_TP1'
            r['settle_ts'] = now
            mid = (entry_lo + entry_hi) / 2 if entry_lo > 0 else price
            r['pnl_pct'] = round((mid - tp1) / mid * 100, 3) if mid > 0 else 0

    return r

def run_settler(verbose: bool = True) -> dict:
    """主结算逻辑"""
    if not LEDGER.exists():
        if verbose: print('prediction_log.jsonl不存在，跳过')
        return {'settled': 0, 'open': 0, 'expired': 0}

    records = [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]
    open_records = [r for r in records if r.get('status') == 'OPEN']

    if not open_records:
        if verbose: print(f'无OPEN记录，跳过')
        return {'settled': 0, 'open': 0, 'expired': 0}

    # 批量拉取涉及的标的价格
    symbols = list(set(r['symbol'] for r in open_records))
    prices  = {sym: _price(sym) for sym in symbols}

    settled_count = 0; expired_count = 0
    updated = []

    for r in records:
        if r.get('status') != 'OPEN':
            updated.append(r)
            continue
        sym   = r['symbol']
        price = prices.get(sym, 0)
        if price <= 0:
            updated.append(r)
            continue
        r2 = _settle_one(r, price)
        updated.append(r2)
        if r2['status'] != 'OPEN':
            if verbose:
                print(f"  结算: {r2['symbol']} {r2['direction']} → {r2['status']} "
                      f"pnl={r2.get('pnl_pct',0):+.3f}%")
            if r2['status'] != 'EXPIRED':
                settled_count += 1
                # 追加到settled文件
                with open(SETTLED, 'a') as f:
                    f.write(json.dumps(r2) + '\n')
                # 触发方仓自动入库
                try:
                    from fangcang_auto_ingest import ingest_from_prediction
                    ingest_from_prediction(r2)
                except Exception:
                    pass
            else:
                expired_count += 1

    # 回写ledger
    with open(LEDGER, 'w') as f:
        for r in updated:
            f.write(json.dumps(r) + '\n')

    open_remaining = sum(1 for r in updated if r.get('status') == 'OPEN')
    if verbose:
        print(f"结算完成: settled={settled_count} expired={expired_count} open={open_remaining}")
    return {'settled': settled_count, 'expired': expired_count, 'open': open_remaining}

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--quiet', action='store_true')
    args = p.parse_args()
    result = run_settler(verbose=not args.quiet)
    print(json.dumps(result))
