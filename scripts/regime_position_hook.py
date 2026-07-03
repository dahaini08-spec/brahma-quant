#!/usr/bin/env python3
"""
体制切换→持仓自动减仓钩子 (P0修复 2026-07-03)
由 regime_switch_monitor.py 在检测到体制切换时调用

宪法规则：
  BULL_TREND   → SHORT单全平 (死穴)
  BEAR_RECOVERY→ SHORT单减50% + 告警
  CHOP_MID     → 任意方向减至50%仓位
  BEAR_TREND   → LONG单全平 (死穴)
  BULL_TREND   → SHORT单全平 (死穴)
"""
import json, time, hmac, hashlib, requests, re
from pathlib import Path

BASE    = Path(__file__).parent.parent
TOOLS   = Path('/root/.openclaw/workspace/TOOLS.md').read_text()
_ak     = re.search(r'API Key:\s*(\S+)', TOOLS)
_sk     = re.search(r'Secret:\s*(\S+)', TOOLS)
API_KEY = _ak.group(1) if _ak else ''
SECRET  = _sk.group(1) if _sk else ''

# 体制→持仓行动映射
REGIME_ACTION = {
    'BULL_TREND':    {'SHORT': 'CLOSE_ALL',  'LONG': 'KEEP'},
    'BEAR_TREND':    {'SHORT': 'KEEP',        'LONG': 'CLOSE_ALL'},
    'BEAR_RECOVERY': {'SHORT': 'REDUCE_HALF', 'LONG': 'KEEP'},
    'BULL_CORRECTION':{'SHORT': 'REDUCE_HALF','LONG': 'KEEP'},
    'CHOP_MID':      {'SHORT': 'REDUCE_HALF', 'LONG': 'REDUCE_HALF'},
    'CHOP_HIGH':     {'SHORT': 'REDUCE_HALF', 'LONG': 'REDUCE_HALF'},
    'BEAR_EARLY':    {'SHORT': 'KEEP',        'LONG': 'REDUCE_HALF'},
    'BULL_EARLY':    {'SHORT': 'REDUCE_HALF', 'LONG': 'KEEP'},
}

def _sign(params: dict) -> str:
    qs = '&'.join(f'{k}={v}' for k,v in sorted(params.items()))
    return hmac.new(SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()

def get_positions() -> list:
    ts = int(time.time()*1000)
    params = {'timestamp': ts}
    sig = _sign(params)
    r = requests.get('https://fapi.binance.com/fapi/v2/positionRisk',
        params={**params,'signature':sig},
        headers={'X-MBX-APIKEY':API_KEY}, timeout=8)
    return [p for p in r.json() if abs(float(p.get('positionAmt',0))) > 0.0001]

def market_close(symbol: str, side: str, qty: float) -> dict:
    """市价平仓，side='BUY'平空，'SELL'平多"""
    ts = int(time.time()*1000)
    params = {
        'symbol': symbol, 'side': side, 'type': 'MARKET',
        'quantity': round(qty, 3), 'reduceOnly': 'true', 'timestamp': ts
    }
    sig = _sign(params)
    r = requests.post('https://fapi.binance.com/fapi/v1/order',
        data={**params,'signature':sig},
        headers={'X-MBX-APIKEY':API_KEY,'Content-Type':'application/x-www-form-urlencoded'},
        timeout=10)
    return r.json()

def apply_regime_hook(new_regime: str, dry_run: bool = False) -> list:
    """
    检查当前持仓，根据新体制执行减仓/平仓
    返回执行动作列表
    """
    actions_taken = []
    action_map = REGIME_ACTION.get(new_regime, {})
    if not action_map:
        return actions_taken

    positions = get_positions()
    for p in positions:
        sym   = p['symbol']
        amt   = float(p['positionAmt'])
        entry = float(p['entryPrice'])
        mark  = float(p.get('markPrice', entry))
        pos_side = 'SHORT' if amt < 0 else 'LONG'
        qty   = abs(amt)

        action = action_map.get(pos_side, 'KEEP')
        pct = (entry-mark)/entry*100 if pos_side=='SHORT' else (mark-entry)/entry*100

        if action == 'KEEP':
            continue

        elif action == 'CLOSE_ALL':
            close_side = 'BUY' if pos_side == 'SHORT' else 'SELL'
            msg = f'🚨 体制={new_regime} → {sym} {pos_side} 死穴！全平 qty={qty:.4f} pnl={pct:+.2f}%'
            if not dry_run:
                result = market_close(sym, close_side, qty)
                ok = 'orderId' in result
                msg += f' → {"✅" if ok else "❌"+str(result.get("msg",""))}'
            actions_taken.append(msg)

        elif action == 'REDUCE_HALF':
            half_qty = round(qty * 0.5, 4)
            if half_qty < 0.001:
                continue
            close_side = 'BUY' if pos_side == 'SHORT' else 'SELL'
            msg = f'⚠️ 体制={new_regime} → {sym} {pos_side} 减半 qty={half_qty:.4f} (原{qty:.4f}) pnl={pct:+.2f}%'
            if not dry_run:
                result = market_close(sym, close_side, half_qty)
                ok = 'orderId' in result
                msg += f' → {"✅" if ok else "❌"+str(result.get("msg",""))}'
            actions_taken.append(msg)

    # 写入执行日志
    if actions_taken and not dry_run:
        log_file = BASE / 'logs' / 'regime_hook.log'
        log_file.parent.mkdir(exist_ok=True)
        with open(log_file, 'a') as f:
            for a in actions_taken:
                f.write(f'[{time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}] {a}\n')

    return actions_taken

if __name__ == '__main__':
    import sys
    new_regime = sys.argv[1] if len(sys.argv) > 1 else 'BEAR_RECOVERY'
    dry = '--dry-run' in sys.argv
    print(f'[RegimeHook] 体制={new_regime} dry_run={dry}')
    actions = apply_regime_hook(new_regime, dry_run=dry)
    if actions:
        for a in actions: print(a)
    else:
        print('[RegimeHook] 无需操作（KEEP）')
