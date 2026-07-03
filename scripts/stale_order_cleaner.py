#!/usr/bin/env python3
"""
stale_order_cleaner.py — 挂单超龄自动撤单
设计院 P1加固 2026-07-03

逻辑：
  - 读取所有未成交开仓挂单
  - 对比对应信号的 expires_at（或挂单时间+STALE_TIMEOUT_MIN）
  - 超龄 → 自动撤单 + 推送告警
  - 单标的挂单数 > MAX_ORDERS_PER_SYM → 立即撤多余的
"""
import sys, os, json, time, requests, hmac, hashlib
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

try:
    from scripts.system_config import API_KEY, API_SECRET, JARVIS_USER_ID, JARVIS_THREAD_ID
except Exception:
    import importlib.util
    spec = importlib.util.spec_from_file_location('sc', BASE/'scripts/system_config.py')
    cfg = importlib.util.module_from_spec(spec); spec.loader.exec_module(cfg)
    API_KEY = cfg.API_KEY; API_SECRET = cfg.API_SECRET
    JARVIS_USER_ID = getattr(cfg,'JARVIS_USER_ID','73295708')
    JARVIS_THREAD_ID = getattr(cfg,'JARVIS_THREAD_ID','')

FAPI = 'https://fapi.binance.com'
STALE_TIMEOUT_MIN   = 90    # 无对应信号时，挂单超过90min自动撤销
MAX_ORDERS_PER_SYM  = 4     # 单标的最大挂单数（超过立即撤多余）
SIGNAL_LOG          = BASE / 'data/live_signal_log.jsonl'

def _signed(method, path, params={}):
    p = dict(params); p['timestamp'] = int(time.time()*1000)
    qs = '&'.join(f'{k}={v}' for k,v in p.items())
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    qs += f'&signature={sig}'; h = {'X-MBX-APIKEY': API_KEY}
    if method == 'GET':    return requests.get(f'{FAPI}{path}?{qs}', headers=h, timeout=8).json()
    if method == 'DELETE': return requests.delete(f'{FAPI}{path}?{qs}', headers=h, timeout=8).json()

def _push(msg: str):
    to = f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}' if JARVIS_THREAD_ID else JARVIS_USER_ID
    os.system(f'openclaw message send --channel jarvis --to "{to}" --message "{msg}"')

def _load_signal_expires() -> dict:
    """读取信号有效期 {symbol: expires_ts}"""
    expires = {}
    try:
        for line in SIGNAL_LOG.read_text().splitlines():
            try:
                s = json.loads(line)
                sym = s.get('symbol','')
                exp = s.get('expires_at','')
                if sym and exp and s.get('valid'):
                    # 取最新的
                    ts = datetime.fromisoformat(exp.replace('Z','+00:00')).timestamp()
                    expires[sym] = max(expires.get(sym, 0), ts)
            except: pass
    except: pass
    return expires

def run():
    now = time.time()
    orders = _signed('GET', '/fapi/v1/openOrders')
    if not isinstance(orders, list) or not orders:
        print('HEARTBEAT_OK')
        return

    # 只处理开仓挂单（非reduceOnly）
    open_orders = [o for o in orders if not o.get('reduceOnly', False)
                   and o.get('status') in ('NEW', 'PARTIALLY_FILLED')]

    if not open_orders:
        print('HEARTBEAT_OK')
        return

    signal_expires = _load_signal_expires()
    from collections import defaultdict
    by_sym = defaultdict(list)
    for o in open_orders:
        by_sym[o['symbol']].append(o)

    cancelled = []
    anomalies = []

    for sym, sym_orders in by_sym.items():
        # ── 检查1：单标的挂单数超上限 ──────────────────────────
        if len(sym_orders) > MAX_ORDERS_PER_SYM:
            # 保留按价格最优的N张，撤其余
            sym_orders_sorted = sorted(sym_orders, key=lambda o: float(o['price']))
            to_cancel = sym_orders_sorted[MAX_ORDERS_PER_SYM:]
            for o in to_cancel:
                r = _signed('DELETE', '/fapi/v1/order', {'symbol': sym, 'orderId': o['orderId']})
                if r.get('status') == 'CANCELED':
                    cancelled.append(f"{sym} qty={o['origQty']} @{o['price']} [超上限]")
                    print(f'[StaleClean] ✅ 撤销超量挂单 {sym} @{o["price"]}')

            anomalies.append(f'🚨 {sym} 挂单{len(sym_orders)}张超上限{MAX_ORDERS_PER_SYM}，已撤{len(to_cancel)}张')
            sym_orders = sym_orders_sorted[:MAX_ORDERS_PER_SYM]

        # ── 检查2：挂单超龄 ──────────────────────────────────────
        sig_exp = signal_expires.get(sym, 0)
        for o in sym_orders:
            order_time = int(o.get('time', 0)) / 1000  # ms→s
            age_min = (now - order_time) / 60

            # 有效期判断：取信号expires_at 和 固定超时 中较早的
            if sig_exp > 0:
                is_expired = now > sig_exp + 1800  # 信号过期+30min宽限
            else:
                is_expired = age_min > STALE_TIMEOUT_MIN

            if is_expired:
                r = _signed('DELETE', '/fapi/v1/order', {'symbol': sym, 'orderId': o['orderId']})
                if r.get('status') == 'CANCELED':
                    cancelled.append(f"{sym} qty={o['origQty']} @{o['price']} [超龄{age_min:.0f}min]")
                    print(f'[StaleClean] ✅ 撤销超龄挂单 {sym} @{o["price"]} ({age_min:.0f}min)')

    if anomalies:
        msg = '⚠️ 梵天挂单异常清理:\\n' + '\\n'.join(anomalies)
        if cancelled:
            msg += '\\n已撤销:\\n' + '\\n'.join(cancelled)
        _push(msg)
        print(f'[StaleClean] 推送告警: {len(anomalies)}个异常')
    elif cancelled:
        print(f'[StaleClean] 静默撤销超龄挂单 {len(cancelled)}张')
    else:
        print('HEARTBEAT_OK')

if __name__ == '__main__':
    run()
