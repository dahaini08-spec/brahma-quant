#!/usr/bin/env python3
"""
cvd_ws_collector.py — 实时CVD采集器（REST高频模式）
设计院三方封印 2026-09-04 苏摩111

注意：此服务器安全策略限制原始socket，无法使用WebSocket。
改为每10秒REST轮询aggTrades（比原来30秒轮询精度+3x）
+ 增量去重（按aggId跳过已处理数据），实现近实时CVD累积。

接入位置：
  1. supercronic @reboot 常驻启动
  2. brahma_brain/volume_unified.py _aggTrades_cvd() 优先读此快照
"""
import json, time, signal, sys, urllib.request, ssl
from pathlib import Path
from datetime import datetime, timezone

BASE   = Path(__file__).parent.parent
DATA   = BASE / 'data'
LOGDIR = BASE / 'logs'
LOGDIR.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

SYMBOLS        = ['BTCUSDT', 'ETHUSDT']
POLL_INTERVAL  = 10    # 秒（高频模式）
WINDOW_SECONDS = 3600  # 保留1H
SNAPSHOT_EVERY = 3     # 每3次轮询（30s）写一次快照
FAPI           = 'https://fapi.binance.com'

_running = True
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

# 每个symbol的状态
_state: dict = {
    sym: {
        'trades':   [],     # [(ts, qty, is_buy)]
        'last_id':  0,      # 最后处理的aggId（增量去重）
        'poll_cnt': 0,
    }
    for sym in SYMBOLS
}


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, flush=True)
    try:
        with open(LOGDIR / 'cvd_ws.log', 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


def _fetch(url, timeout=8):
    try:
        return json.loads(urllib.request.urlopen(url, timeout=timeout, context=_ctx).read())
    except Exception:
        return None


def _poll_symbol(sym: str):
    st = _state[sym]
    url = f'{FAPI}/fapi/v1/aggTrades?symbol={sym}&limit=1000'
    trades = _fetch(url)
    if not trades:
        return

    now = time.time()
    new_cnt = 0
    for t in trades:
        aid = int(t.get('a', 0))
        if aid <= st['last_id']:
            continue
        st['last_id'] = max(st['last_id'], aid)
        qty    = float(t['q'])
        is_buy = not t['m']  # maker=True → seller=maker → buyer=taker
        ts_sec = t['T'] / 1000.0
        st['trades'].append((ts_sec, qty, is_buy))
        new_cnt += 1

    # 时间窗口裁剪
    cutoff = now - WINDOW_SECONDS
    st['trades'] = [(ts, q, b) for ts, q, b in st['trades'] if ts >= cutoff]
    st['poll_cnt'] += 1

    return new_cnt


def _cvd_window(trades, seconds):
    cut = time.time() - seconds
    r = [(q, b) for ts, q, b in trades if ts >= cut]
    buy  = sum(q for q, b in r if b)
    sell = sum(q for q, b in r if not b)
    return buy - sell, buy, sell


def _dir(v, total):
    if total < 0.001:
        return 'NEUTRAL'
    pct = abs(v) / total
    if pct < 0.05:
        return 'NEUTRAL'
    return 'BUY_DOMINANT' if v > 0 else 'SELL_DOMINANT'


def _signal(c1m, c5m, c1h):
    bull = sum(1 for v in [c1m, c5m, c1h] if v > 0)
    bear = sum(1 for v in [c1m, c5m, c1h] if v < 0)
    if bull == 3: return 'STRONG_BUY'
    if bear == 3: return 'STRONG_SELL'
    if bull == 2: return 'MILD_BUY'
    if bear == 2: return 'MILD_SELL'
    return 'MIXED'


def _write_snapshot(sym: str):
    st = _state[sym]
    buf = st['trades']
    if not buf:
        return

    now = time.time()
    c1h, b1h, s1h = _cvd_window(buf, 3600)
    c5m, b5m, s5m = _cvd_window(buf, 300)
    c1m, b1m, s1m = _cvd_window(buf, 60)

    total1h = b1h + s1h
    snapshot = {
        'symbol':     sym,
        'ts':         now,
        'updated':    datetime.now(timezone.utc).isoformat(),
        'source':     'rest_hf',  # 高频REST模式
        'poll_interval': POLL_INTERVAL,
        'n_trades':   len(buf),
        'window_sec': WINDOW_SECONDS,
        'cvd_1h':     round(c1h, 4),
        'buy_vol_1h': round(b1h, 4),
        'sell_vol_1h':round(s1h, 4),
        'dir_1h':     _dir(c1h, total1h),
        'cvd_5m':     round(c5m, 4),
        'dir_5m':     _dir(c5m, b5m + s5m),
        'cvd_1m':     round(c1m, 4),
        'dir_1m':     _dir(c1m, b1m + s1m),
        'signal':     _signal(c1m, c5m, c1h),
    }

    out = DATA / f'cvd_realtime_{sym.lower()}.json'
    tmp = DATA / f'cvd_realtime_{sym.lower()}.tmp'
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False))
    tmp.rename(out)
    return snapshot


def main():
    global _running

    def _stop(sig, frame):
        global _running
        _log('收到SIGTERM，停止')
        _running = False
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    _log(f'CVD高频采集器启动 symbols={SYMBOLS} interval={POLL_INTERVAL}s')

    # 初始化last_id（避免第一次全量重复计数）
    for sym in SYMBOLS:
        trades = _fetch(f'{FAPI}/fapi/v1/aggTrades?symbol={sym}&limit=1')
        if trades:
            _state[sym]['last_id'] = int(trades[-1].get('a', 0)) - 1

    loop = 0
    while _running:
        loop += 1
        for sym in SYMBOLS:
            new_cnt = _poll_symbol(sym)

        # 每SNAPSHOT_EVERY次轮询写一次快照
        if loop % SNAPSHOT_EVERY == 0:
            parts = []
            for sym in SYMBOLS:
                snap = _write_snapshot(sym)
                if snap:
                    parts.append(f'{sym[:3]}_cvd5m={snap["cvd_5m"]:+.1f}[{snap["signal"]}]')
            _log('快照: ' + ' | '.join(parts))

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
