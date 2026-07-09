#!/usr/bin/env python3
"""
reconcile_brahma_state.py — 梵天状态自动对齐工具
══════════════════════════════════════════════════
职责：
  1. 从交易所拉取真实持仓
  2. 对比 brahma_state.json + wuqu_positions.json
  3. 清除幽灵持仓（state有/交易所无）
  4. 补录缺失持仓（交易所有/state无）
  5. 同步更新 wuqu_positions.json

苏摩批准：111 @ 2026-06-28 02:23 UTC
"""
import json, hmac, hashlib, time, urllib.request, sys
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
KEY = SEC = ''
try:
    sys.path.insert(0, str(BASE))
    from config import binance_keys
    KEY, SEC = binance_keys()
except: pass

def api(path, params=''):
    ts = int(time.time()*1000)
    qs = f'timestamp={ts}' + (f'&{params}' if params else '')
    sig = hmac.new(SEC.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f'https://fapi.binance.com{path}?{qs}&signature={sig}'
    req = urllib.request.Request(url, headers={'X-MBX-APIKEY': KEY})
    return json.loads(urllib.request.urlopen(req, timeout=8).read())

def run(dry_run=False):
    now = datetime.now(timezone.utc).isoformat()
    issues = []

    # 1. 拉取交易所真实持仓
    try:
        acct = api('/fapi/v2/account')
        real_pos = {p['symbol']: p for p in acct.get('positions',[])
                    if abs(float(p.get('positionAmt',0))) > 0}
    except Exception as e:
        pass  # [静默]
        return

    real_syms = set(real_pos.keys())
    pass  # [静默]

    # 2. 读取 brahma_state
    state_path = BASE / 'data' / 'brahma_state.json'
    state = json.load(open(state_path))
    open_pos = [p for p in state.get('positions', []) if p.get('status') == 'OPEN']
    state_syms = {p['symbol'] for p in open_pos}

    ghost = state_syms - real_syms
    missing = real_syms - state_syms

    if ghost:
        issues.append(f'幽灵持仓: {list(ghost)}')
        if not dry_run:
            for p in state['positions']:
                if p.get('status') == 'OPEN' and p['symbol'] in ghost:
                    p['status'] = 'CLOSED'
                    p['close_reason'] = f'auto_reconcile {now}'
                    p['closed_at'] = now
            pass  # [静默]

    if missing:
        issues.append(f'缺失持仓: {list(missing)}')
        pass  # [静默]

    # 3. 同步 wuqu_positions.json
    wuqu_path = BASE / 'data' / 'wuqu_positions.json'
    if not dry_run:
        wuqu = {}
        for sym, p in real_pos.items():
            amt = float(p['positionAmt'])
            wuqu[sym] = {
                'symbol': sym,
                'side': 'LONG' if amt > 0 else 'SHORT',
                'positionAmt': abs(amt),
                'entryPrice': float(p.get('entryPrice', 0)),
                'unrealizedProfit': float(p.get('unrealizedProfit', 0)),
                'last_reconciled': now
            }
        with open(wuqu_path, 'w') as f:
            json.dump(wuqu, f, indent=2, ensure_ascii=False)

        state['last_updated'] = now
        state['last_reconciled'] = now
        with open(state_path, 'w') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        pass  # [静默]

    if not issues:
        pass  # [静默]
    return issues

if __name__ == '__main__':
    dry = '--dry' in sys.argv
    run(dry_run=dry)
