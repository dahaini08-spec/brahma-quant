#!/usr/bin/env python3
"""
liq_ws_multi.py — 多所清算 WebSocket 守护进程 v2
设计院 2026-08-05

订阅:
  Binance  wss://fstream.binance.com/ws/!forceOrder@arr  ← 全市场强平
  BitMEX   wss://www.bitmex.com/realtime?subscribe=liquidation:XBTUSD,liquidation:XBTUSDT
  Bitget   wss://ws.bitget.com/v2/ws/public              ← liquidation channel

持续写入: data/liq_flow_cache.json (TTL 4h, 每所独立缓冲)
"""
import sys, os, json, time, signal, threading, logging
from pathlib import Path
from collections import defaultdict, deque

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

CACHE_FILE   = BASE / 'data' / 'liq_flow_cache.json'
LOG_FILE     = BASE / 'logs' / 'liq_ws_multi.log'
MAX_PER_SYM  = 3000
TTL_SECONDS  = 4 * 3600

WATCH_SYMBOLS = {'BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT'}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [liq_ws] %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('liq_ws')

_buffer : dict[str, deque] = defaultdict(lambda: deque(maxlen=MAX_PER_SYM))
_meta   = {'total_events': 0, 'last_update': 0, 'connected': {}}
_lock   = threading.Lock()
_stop   = threading.Event()


def _append(sym: str, rec: dict):
    with _lock:
        _buffer[sym].append(rec)
        _meta['total_events'] += 1
        _meta['last_update'] = time.time()


def _flush():
    now = time.time(); cutoff = now - TTL_SECONDS
    out = {}
    with _lock:
        for sym, dq in _buffer.items():
            fresh = [r for r in dq if r.get('ts',0) >= cutoff]
            if fresh:
                out[sym] = fresh
        out['_meta'] = {**_meta, 'last_update': now}
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(out, ensure_ascii=False))
    tmp.replace(CACHE_FILE)


# ── Binance ──────────────────────────────────────────────────────────────────
def _run_binance():
    import websocket as ws_lib
    url = 'wss://fstream.binance.com/ws/!forceOrder@arr'
    delay = 1.0
    while not _stop.is_set():
        try:
            ws = ws_lib.create_connection(url, timeout=15)
            _meta['connected']['binance'] = True
            log.info('Binance WS connected')
            delay = 1.0
            while not _stop.is_set():
                ws.settimeout(5)
                try:
                    raw = ws.recv()
                    msg  = json.loads(raw)
                    order = msg.get('o', {})
                    sym   = order.get('s','')
                    if sym not in WATCH_SYMBOLS: continue
                    price = float(order.get('ap', order.get('p', 0)))
                    qty   = float(order.get('q', 0))
                    side  = order.get('S','SELL')
                    _append(sym, {
                        'price': round(price,2), 'qty': qty,
                        'usd': round(price*qty,2),
                        'side': side,
                        'pos_side': 'long' if side=='SELL' else 'short',
                        'source': 'binance_ws', 'ts': time.time()
                    })
                except ws_lib.WebSocketTimeoutException:
                    pass
        except Exception as e:
            _meta['connected']['binance'] = False
            log.warning(f'Binance WS error: {e}, retry {delay:.0f}s')
            _stop.wait(delay); delay = min(delay*2, 30)
        finally:
            try: ws.close()
            except: pass


# ── BitMEX ───────────────────────────────────────────────────────────────────
_BITMEX_SYM_MAP = {
    'XBTUSD':  'BTCUSDT',
    'XBTUSDT': 'BTCUSDT',
    'ETHUSD':  'ETHUSDT',
    'ETHUSDT': 'ETHUSDT',
}

def _run_bitmex():
    import websocket as ws_lib
    url = 'wss://www.bitmex.com/realtime'
    sub = json.dumps({'op':'subscribe','args':[
        'liquidation:XBTUSD','liquidation:XBTUSDT',
        'liquidation:ETHUSD','liquidation:ETHUSDT'
    ]})
    delay = 1.0
    while not _stop.is_set():
        try:
            ws = ws_lib.create_connection(url, timeout=15)
            ws.send(sub)
            _meta['connected']['bitmex'] = True
            log.info('BitMEX WS connected')
            delay = 1.0
            while not _stop.is_set():
                ws.settimeout(5)
                try:
                    raw = ws.recv()
                    msg = json.loads(raw)
                    if msg.get('table') != 'liquidation': continue
                    for item in msg.get('data', []):
                        bm_sym   = item.get('symbol','')
                        sym      = _BITMEX_SYM_MAP.get(bm_sym)
                        if not sym: continue
                        price    = float(item.get('price', 0))
                        qty      = float(item.get('leavesQty', 0))
                        side     = item.get('side','')
                        bn_side  = 'SELL' if side=='Sell' else 'BUY'
                        pos_side = 'long' if bn_side=='SELL' else 'short'
                        _append(sym, {
                            'price': price, 'qty': qty,
                            'usd': round(price*qty,2),
                            'side': bn_side, 'pos_side': pos_side,
                            'source': 'bitmex_ws', 'ts': time.time()
                        })
                except ws_lib.WebSocketTimeoutException:
                    pass
        except Exception as e:
            _meta['connected']['bitmex'] = False
            log.warning(f'BitMEX WS error: {e}, retry {delay:.0f}s')
            _stop.wait(delay); delay = min(delay*2, 30)
        finally:
            try: ws.close()
            except: pass


# ── Bitget ───────────────────────────────────────────────────────────────────
def _run_bitget():
    import websocket as ws_lib
    url = 'wss://ws.bitget.com/v2/ws/public'
    sub = json.dumps({'op':'subscribe','args':[
        {'instType':'USDT-FUTURES','channel':'liquidation','instId':'BTCUSDT'},
        {'instType':'USDT-FUTURES','channel':'liquidation','instId':'ETHUSDT'},
    ]})
    delay = 1.0
    while not _stop.is_set():
        try:
            ws = ws_lib.create_connection(url, timeout=15)
            ws.send(sub)
            _meta['connected']['bitget'] = True
            log.info('Bitget WS connected')
            delay = 1.0
            while not _stop.is_set():
                ws.settimeout(5)
                try:
                    raw = ws.recv()
                    msg = json.loads(raw)
                    if msg.get('action') != 'snapshot': continue
                    for item in msg.get('data', []):
                        sym  = item.get('instId','').replace('-','')
                        if not sym: continue
                        sym  = sym + 'USDT' if not sym.endswith('USDT') else sym
                        price = float(item.get('fillPx', item.get('px', 0)))
                        qty   = float(item.get('fillSz', item.get('sz', 0)))
                        side  = item.get('posSide','long')
                        bn_side = 'SELL' if side=='long' else 'BUY'
                        _append(sym, {
                            'price': price, 'qty': qty,
                            'usd': round(price*qty,2),
                            'side': bn_side, 'pos_side': side,
                            'source': 'bitget_ws', 'ts': time.time()
                        })
                except ws_lib.WebSocketTimeoutException:
                    pass
        except Exception as e:
            _meta['connected']['bitget'] = False
            log.warning(f'Bitget WS error: {e}, retry {delay:.0f}s')
            _stop.wait(delay); delay = min(delay*2, 30)
        finally:
            try: ws.close()
            except: pass


# ── 主程序 ────────────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--exchanges', default='binance,bitmex,bitget')
    args = parser.parse_args()

    pid_file = BASE / 'data' / 'liq_ws_multi.pid'
    pid_file.parent.mkdir(exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    def _sigterm(*_):
        log.info('Shutting down...'); _stop.set()
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT,  _sigterm)

    exchanges = [e.strip() for e in args.exchanges.split(',')]
    threads   = []

    runners = {'binance': _run_binance, 'bitmex': _run_bitmex, 'bitget': _run_bitget}
    for ex in exchanges:
        if ex in runners:
            t = threading.Thread(target=runners[ex], daemon=True, name=f'ws_{ex}')
            t.start(); threads.append(t)
            log.info(f'Started {ex} WS thread')

    # flush loop
    while not _stop.is_set():
        _stop.wait(30)
        _flush()
        alive = sum(1 for t in threads if t.is_alive())
        log.info(f'Flush OK | total_events={_meta["total_events"]} | threads_alive={alive}/{len(threads)} | connected={_meta["connected"]}')

    _flush()
    pid_file.unlink(missing_ok=True)
    log.info('liq_ws_multi stopped')


if __name__ == '__main__':
    main()
