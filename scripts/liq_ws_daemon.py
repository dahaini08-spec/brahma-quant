#!/usr/bin/env python3
"""
liq_ws_daemon.py — Binance !forceOrder@arr WebSocket 清算流守护进程
设计院 2026-08-05

功能:
  - 订阅 Binance Futures WebSocket !forceOrder@arr (全市场强平流)
  - 持续写入 data/liq_flow_cache.json (TTL 4小时滚动窗口)
  - 支持断线自动重连 (指数退避, 最大30s)
  - 内存上限: 每币种最多 2000 条记录

用法:
  python3 scripts/liq_ws_daemon.py           # 前台运行
  python3 scripts/liq_ws_daemon.py --bg      # 写入PID文件后台运行

缓存格式 data/liq_flow_cache.json:
  {
    "BTCUSDT": [
      {"price": 64500.0, "qty": 0.5, "usd": 32250, "side": "SELL",
       "pos_side": "long", "source": "binance_ws", "ts": 1785898000},
      ...
    ],
    "_meta": {"last_update": 1785898123, "total_events": 5832}
  }
"""

import sys
import os
import json
import time
import signal
import logging
import argparse
import threading
from pathlib import Path
from collections import defaultdict, deque

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

CACHE_FILE  = BASE / 'data' / 'liq_flow_cache.json'
PID_FILE    = BASE / 'data' / 'liq_ws_daemon.pid'
LOG_FILE    = BASE / 'logs' / 'liq_ws_daemon.log'
MAX_PER_SYM = 2000      # 每币种最多条数
TTL_SECONDS = 4 * 3600  # 4小时 TTL
WS_URL      = 'wss://fstream.binance.com/ws/!forceOrder@arr'

# 监控币种白名单（节省内存）
WATCH_SYMBOLS = {
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT',
    'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT',
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [liq_ws] %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('liq_ws')

# ── 内存缓冲 ────────────────────────────────────────────────────────────────
_buffer: dict[str, deque] = defaultdict(lambda: deque(maxlen=MAX_PER_SYM))
_meta = {'last_update': 0, 'total_events': 0, 'connected': False}
_lock = threading.Lock()
_shutdown = threading.Event()


def _flush_cache():
    """将内存缓冲写入磁盘，清理过期记录"""
    now = time.time()
    cutoff = now - TTL_SECONDS
    out = {}
    with _lock:
        for sym, dq in _buffer.items():
            fresh = [r for r in dq if r.get('ts', 0) >= cutoff]
            if fresh:
                out[sym] = fresh
        out['_meta'] = dict(_meta)
        out['_meta']['last_update'] = now
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(out, ensure_ascii=False))
    tmp.replace(CACHE_FILE)


def _on_message(raw: str):
    """处理单条 forceOrder 事件"""
    try:
        msg = json.loads(raw)
        # Binance forceOrder 格式: {"e":"forceOrder","E":ts,"o":{...}}
        order = msg.get('o', msg)  # 有时顶层就是 order
        sym  = order.get('s', '')
        if sym not in WATCH_SYMBOLS:
            return
        price = float(order.get('ap', order.get('p', 0)))  # avg price
        qty   = float(order.get('q', 0))
        usd   = price * qty
        side  = order.get('S', 'SELL')  # SELL=多头被清算, BUY=空头被清算
        pos_side = 'long' if side == 'SELL' else 'short'

        rec = {
            'price':    round(price, 4),
            'qty':      qty,
            'usd':      round(usd, 2),
            'side':     side,
            'pos_side': pos_side,
            'source':   'binance_ws',
            'ts':       time.time(),
        }
        with _lock:
            _buffer[sym].append(rec)
            _meta['total_events'] += 1
            _meta['last_update'] = time.time()
    except Exception:
        pass


# ── WebSocket 主循环 ─────────────────────────────────────────────────────────
def run_ws():
    try:
        import websocket
    except ImportError:
        log.error('websocket-client not installed. Run: pip install websocket-client')
        return

    retry_delay = 1.0
    flush_interval = 30  # 每30秒flush一次
    last_flush = time.time()

    while not _shutdown.is_set():
        ws = None
        try:
            log.info(f'Connecting to {WS_URL}')
            ws = websocket.create_connection(WS_URL, timeout=15)
            _meta['connected'] = True
            retry_delay = 1.0
            log.info('Connected. Listening for forceOrder events...')

            while not _shutdown.is_set():
                ws.settimeout(5)
                try:
                    raw = ws.recv()
                    _on_message(raw)
                except websocket.WebSocketTimeoutException:
                    pass  # 正常超时，继续循环

                # 定期flush
                if time.time() - last_flush >= flush_interval:
                    _flush_cache()
                    last_flush = time.time()
                    log.info(f'Cache flushed. total_events={_meta["total_events"]}')

        except Exception as e:
            _meta['connected'] = False
            log.warning(f'WS error: {e}. Retry in {retry_delay:.0f}s')
            _flush_cache()
            _shutdown.wait(retry_delay)
            retry_delay = min(retry_delay * 2, 30.0)
        finally:
            try:
                if ws:
                    ws.close()
            except Exception:
                pass


def run_bg_flush():
    """后台定期 flush 线程"""
    while not _shutdown.is_set():
        _shutdown.wait(60)
        if not _shutdown.is_set():
            _flush_cache()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bg', action='store_true', help='Write PID and detach')
    args = parser.parse_args()

    # 写入 PID
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    log.info(f'PID={os.getpid()} written to {PID_FILE}')

    # 优雅退出
    def _sigterm(*_):
        log.info('SIGTERM received, shutting down...')
        _shutdown.set()

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT,  _sigterm)

    # 后台 flush 线程
    t_flush = threading.Thread(target=run_bg_flush, daemon=True)
    t_flush.start()

    # 主 WS 循环
    log.info('liq_ws_daemon starting')
    run_ws()

    # 最终 flush
    _flush_cache()
    PID_FILE.unlink(missing_ok=True)
    log.info('liq_ws_daemon stopped')


if __name__ == '__main__':
    main()
