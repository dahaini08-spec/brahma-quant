#!/usr/bin/env python3
"""
onchain_ws.py — 链上大单 WebSocket 实时感知引擎
Phase B-4: 实时监控大单流入/流出，无需轮询

方案：
  - Binance FAPI aggTrades WebSocket（逐笔成交）
  - 本地聚合：5秒内累计大单（>阈值）
  - 输出：买卖大单净流入、鲸鱼动向
  - 评分贡献：-5~+8分
  - 后台守护：ws_guardian 管理生命周期
"""
import json, os, sys, time, math, threading, socket
import urllib.request
from pathlib import Path
from collections import deque

DATA_DIR   = Path(__file__).parent.parent / 'data'
WS_CACHE_F = DATA_DIR / 'onchain_ws_cache.json'
DATA_DIR.mkdir(exist_ok=True)

# 大单阈值（USDT名义价值）
WHALE_THRESHOLD = {
    'BTCUSDT': 500_000,
    'ETHUSDT': 200_000,
    'DEFAULT': 100_000,
}

# 内存缓存（最近30秒大单）
_order_cache: dict[str, deque] = {}
_cache_lock = threading.Lock()
_ws_threads: dict[str, threading.Thread] = {}

def _get_threshold(symbol: str) -> float:
    return WHALE_THRESHOLD.get(symbol.upper(), WHALE_THRESHOLD['DEFAULT'])

def _save_cache():
    """将最新聚合数据写入磁盘（供主进程读取）"""
    try:
        snapshot = {}
        with _cache_lock:
            for sym, orders in _order_cache.items():
                now = time.time()
                recent = [o for o in orders if now - o['ts'] <= 30]
                buy_vol  = sum(o['notional'] for o in recent if o['side'] == 'BUY')
                sell_vol = sum(o['notional'] for o in recent if o['side'] == 'SELL')
                snapshot[sym] = {
                    'buy_vol_30s':    round(buy_vol, 0),
                    'sell_vol_30s':   round(sell_vol, 0),
                    'net_flow':       round(buy_vol - sell_vol, 0),
                    'whale_count':    len(recent),
                    'last_updated':   time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                }
        WS_CACHE_F.write_text(json.dumps(snapshot, indent=2))
    except Exception:
        pass

def _ws_connect(symbol: str):
    """WebSocket 连接（手写 WS 客户端，零依赖）"""
    import hashlib, base64, struct, random
    host = 'fstream.binance.com'
    path = f'/ws/{symbol.lower()}@aggTrade'
    key  = base64.b64encode(bytes(random.getrandbits(8) for _ in range(16))).decode()
    handshake = (
        f'GET {path} HTTP/1.1\r\n'
        f'Host: {host}\r\n'
        f'Upgrade: websocket\r\n'
        f'Connection: Upgrade\r\n'
        f'Sec-WebSocket-Key: {key}\r\n'
        f'Sec-WebSocket-Version: 13\r\n\r\n'
    ).encode()

    import ssl
    ctx = ssl.create_default_context()
    sock = ctx.wrap_socket(socket.create_connection((host, 443), timeout=10), server_hostname=host)
    sock.sendall(handshake)
    # 读取握手响应
    resp = b''
    while b'\r\n\r\n' not in resp:
        resp += sock.recv(1024)
    return sock

def _ws_read_frame(sock):
    """读取一个 WebSocket 帧"""
    header = b''
    while len(header) < 2:
        header += sock.recv(2 - len(header))
    fin    = (header[0] & 0x80) != 0
    opcode = header[0] & 0x0f
    masked = (header[1] & 0x80) != 0
    length = header[1] & 0x7f
    if length == 126:
        ext = b''
        while len(ext) < 2: ext += sock.recv(2 - len(ext))
        length = int.from_bytes(ext, 'big')
    elif length == 127:
        ext = b''
        while len(ext) < 8: ext += sock.recv(8 - len(ext))
        length = int.from_bytes(ext, 'big')
    payload = b''
    while len(payload) < length:
        chunk = sock.recv(min(4096, length - len(payload)))
        if not chunk: break
        payload += chunk
    return opcode, payload.decode('utf-8', errors='ignore')

def _ws_worker(symbol: str, stop_event: threading.Event):
    """WS 后台线程"""
    threshold = _get_threshold(symbol)
    with _cache_lock:
        if symbol not in _order_cache:
            _order_cache[symbol] = deque(maxlen=500)
    while not stop_event.is_set():
        try:
            sock = _ws_connect(symbol)
            sock.settimeout(60)
            while not stop_event.is_set():
                opcode, data = _ws_read_frame(sock)
                if opcode == 8: break  # close
                if opcode != 1: continue
                try:
                    msg = json.loads(data)
                    price    = float(msg.get('p', 0))
                    qty      = float(msg.get('q', 0))
                    notional = price * qty
                    if notional < threshold:
                        continue
                    side = 'SELL' if msg.get('m', False) else 'BUY'
                    with _cache_lock:
                        _order_cache[symbol].append({
                            'ts':       time.time(),
                            'price':    price,
                            'qty':      qty,
                            'notional': notional,
                            'side':     side,
                        })
                    _save_cache()
                except Exception:
                    pass
            sock.close()
        except Exception:
            if not stop_event.is_set():
                time.sleep(5)  # 重连等待

def start_ws(symbol: str) -> bool:
    """启动 WS 监控（后台线程）"""
    if symbol in _ws_threads and _ws_threads[symbol].is_alive():
        return False  # 已在运行
    stop_ev = threading.Event()
    t = threading.Thread(target=_ws_worker, args=(symbol, stop_ev), daemon=True)
    t.start()
    _ws_threads[symbol] = t
    return True

def get_whale_signal(symbol: str) -> dict:
    """
    读取磁盘缓存（供主进程调用，不依赖WS线程存活）
    返回: {net_flow, buy_vol_30s, sell_vol_30s, score, note}
    """
    result = {'net_flow': 0, 'buy_vol_30s': 0, 'sell_vol_30s': 0,
              'score': 0, 'note': '无WS数据', 'whale_count': 0}
    try:
        if WS_CACHE_F.exists():
            cache = json.loads(WS_CACHE_F.read_text())
            sym_data = cache.get(symbol.upper(), {})
            if sym_data:
                threshold = _get_threshold(symbol)
                net = float(sym_data.get('net_flow', 0))
                result.update(sym_data)
                # 评分
                if abs(net) > threshold * 3:
                    score = 8 if net < 0 else -5  # 空：大卖净流出 +8
                elif abs(net) > threshold:
                    score = 5 if net < 0 else -3
                else:
                    score = 0
                result['score'] = score
                result['note'] = f'净流={"卖" if net<0 else "买"}${abs(net)/1000:.0f}k'
    except Exception as e:
        result['note'] = f'读取失败:{str(e)[:40]}'
    return result

# REST 回退（WS未启动时用）
def get_whale_from_rest(symbol: str) -> dict:
    """从 REST API 获取近期大单（WS 备用）"""
    try:
        import sys as _ws_sys, os as _ws_os
        _bd = _ws_os.path.join(_ws_os.path.dirname(_ws_os.path.abspath(__file__)))
        if _bd not in _ws_sys.path: _ws_sys.path.insert(0, _bd)
        from onchain_engine import analyze as _oc_run
        oc = _oc_run(symbol)
        flow = float(oc.get('exchange_net_flow_usd', oc.get('net_flow_24h', 0)) or 0)
        score = 4 if flow < -1e6 else (2 if flow < 0 else (0 if flow < 1e6 else -3))
        return {'net_flow': flow, 'score': score, 'source': 'rest',
                'note': f'REST净流=${flow/1e6:.1f}M'}
    except Exception as e:
        # 最后备用：用大单聚合接口
        try:
            import urllib.request as _ur, json as _jj
            data = _jj.loads(_ur.urlopen(
                f'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol.upper()}',
                timeout=5).read())
            volume = float(data.get('volume', 0))
            buy_vol = float(data.get('quoteVolume', 0))
            score = 2 if volume > 1e9 else 0
            return {'net_flow': 0, 'score': score, 'source': 'rest_fallback',
                    'note': f'24H量=${buy_vol/1e9:.2f}B'}
        except Exception as e2:
            return {'net_flow': 0, 'score': 0, 'source': 'rest', 'note': str(e2)[:40]}

def analyze(symbol: str, direction: str) -> dict:
    """
    综合链上大单分析（WS优先，REST备用）
    direction: SHORT | LONG
    """
    ws_data  = get_whale_signal(symbol)
    if ws_data.get('whale_count', 0) > 0:
        data = ws_data
        data['source'] = 'websocket'
    else:
        data = get_whale_from_rest(symbol)

    # 方向对齐（空单：卖大于买 = 有利）
    net = data.get('net_flow', 0)
    if direction in ('SHORT', '做空'):
        score = data.get('score', 0)
    else:
        score = -data.get('score', 0)

    data['direction_score'] = max(-8, min(score, 8))
    return data

if __name__ == '__main__':
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else 'ETHUSDT'
    print(f'启动 {sym} WS监控...')
    start_ws(sym)
    time.sleep(10)
    r = get_whale_signal(sym)
    print(json.dumps(r, indent=2))
