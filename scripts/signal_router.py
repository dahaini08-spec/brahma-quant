#!/usr/bin/env python3
"""
signal_router.py v1.0 — 统一信号漏斗
设计院 2026-06-04

所有扫描源（multi_scan/lana/on_demand/manual）的唯一写入入口
职责：格式标准化 → 门槛过滤 → 去重 → 写pipeline_watch → 推送
"""
import json, os, time
from pathlib import Path
import time
from datetime import datetime, timezone

BASE       = Path(__file__).parent.parent
WATCH_FILE = BASE / 'data' / 'pipeline_watch.json'
ZONES_FILE = BASE / 'data' / 'price_zones.json'

def _load_watch():
    try:
        return json.load(open(WATCH_FILE)) if WATCH_FILE.exists() else {}
    except:
        return {}

def _save_watch(d):
    tmp = str(WATCH_FILE) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(WATCH_FILE))

def _is_duplicate(watch, symbol, direction, entry_lo):
    """同标的+同方向+入场区偏差<0.5% = 重复"""
    for v in watch.values():
        if (v.get('symbol') == symbol
                and v.get('direction') == direction
                and v.get('status') in ('watching', 'near', 'triggered')
                and abs(v.get('entry_lo', 0) - entry_lo) / max(entry_lo, 1) < 0.005):
            return True
    return False

def _update_price_zones(symbol, entry_lo, entry_hi, score, regime):
    """更新价格关注区间（供signal_trigger使用）"""
    zones = {}
    try:
        zones = json.load(open(ZONES_FILE)) if ZONES_FILE.exists() else {}
    except:
        pass

    zones[symbol] = {
        'last_analyze_ts':  datetime.now(timezone.utc).isoformat(),
        'last_score':       score,
        'last_entry_lo':    entry_lo,
        'last_entry_hi':    entry_hi,
        # 关注区间 = 入场区±3%（紧贴入场区，捕捉接近动作）
        'watch_lo':         round(entry_lo * 0.97, 8),
        'watch_hi':         round(entry_hi * 1.03, 8),
        'regime':           regime,
        'last_trigger_ts':  zones.get(symbol, {}).get('last_trigger_ts'),
        'last_price':       None,
    }
    tmp = str(ZONES_FILE) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(zones, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(ZONES_FILE))

def _notify(msg):
    try:
        import sys
        sys.path.insert(0, str(BASE / 'scripts'))
        from notify_jarvis import send
        send(msg)
    except:
        print(msg)

def route_signal(symbol, direction, score, entry_lo, entry_hi,
                 stop_loss, tp1, tp2=None, regime='', source='unknown', note=''):
    """
    统一信号入口。返回：QUEUED / DUPLICATE / BELOW_THRESHOLD / NO_ENTRY
    """
    # 格式规范化
    symbol    = symbol.upper() if not symbol.upper().endswith('USDT') else symbol.upper()
    direction = direction.upper()
    score     = float(score)
    entry_lo  = float(entry_lo)
    entry_hi  = float(entry_hi) if entry_hi else entry_lo
    stop_loss = float(stop_loss)
    tp1       = float(tp1)

    # 1. 门槛过滤
    if score < 140:
        return 'BELOW_THRESHOLD'

    if not entry_lo or not stop_loss or not tp1:
        return 'NO_ENTRY'

    # 2. 更新价格关注区间（无论是否入队，都更新zones供触发器使用）
    _update_price_zones(symbol, entry_lo, entry_hi, score, regime)

    # 3. 去重检查
    watch = _load_watch()
    if _is_duplicate(watch, symbol, direction, entry_lo):
        return 'DUPLICATE'

    # 4. 写入pipeline_watch
    sig_id = f'{symbol}_{direction}_{int(time.time())}'
    watch[sig_id] = {
        'symbol':    symbol,
        'direction': direction,
        'score':     score,
        'entry_lo':  entry_lo,
        'entry_hi':  entry_hi,
        'stop_loss': stop_loss,
        'tp1':       tp1,
        'tp2':       tp2,
        'regime':    regime,
        'source':    source,
        'note':      note,
        'status':    'watching',
        'triggered': False,
        'added_at':  datetime.now(timezone.utc).isoformat(),
        'queued_ts':  time.time(),
    }
    _save_watch(watch)

    # 5. 推送通知
    rr = round(abs(entry_lo - tp1) / abs(entry_lo - stop_loss), 1) if stop_loss != entry_lo else 0
    _notify(
        f'📡 新信号入队 [{source}]\n'
        f'  {symbol} {direction} score={score:.0f}\n'
        f'  入场={entry_lo}~{entry_hi}  止损={stop_loss}  目标={tp1}  R:R={rr}'
    )
    pass  # [静默]
    return 'QUEUED'


if __name__ == '__main__':
    # 测试
    r = route_signal('ETH', 'SHORT', 145.4, 1781.26, 1801.44,
                     1858.56, 1623.33, 1510.0, 'BEAR_TREND', 'test')
    print(f'测试结果: {r}')


def update_watch_entry(symbol: str = None) -> dict:
    """
    动态入场区追踪 — P0-A优化 2026-06-05
    对watching超过2H且未触发的信号，根据价格位置动态调整入场区
    最大平移幅度：原始入场区宽度的50%（防止追太远）
    返回：{symbol: 'UPDATED'|'VALID'|'EXPIRED'|'SKIP'}
    """
    import urllib.request, time
    from datetime import datetime, timezone

    watch = _load_watch()
    if not watch:
        return {}

    results = {}
    now_ts = time.time()

    for key, sig in list(watch.items()):
        sym = sig.get('symbol', '')
        if symbol and sym != symbol:
            continue
        status = sig.get('status', '')
        if status not in ('watching', 'near'):
            continue

        # fallback to added_at if queued_ts missing
        _added = sig.get('added_at','')
        _fallback_ts = now_ts
        if _added:
            try:
                from datetime import datetime as _dt, timezone as _tz
                _fallback_ts = _dt.fromisoformat(_added).timestamp()
            except: pass
        queued_ts = sig.get('queued_ts', _fallback_ts)
        age_h = (now_ts - queued_ts) / 3600

        # 不足1H不追踪（给原始入场区机会）
        if age_h < 1.0:
            results[sym] = 'SKIP'
            continue

        direction = sig.get('direction', 'SHORT')
        entry_lo  = float(sig.get('entry_lo', 0))
        entry_hi  = float(sig.get('entry_hi', entry_lo))
        orig_lo   = float(sig.get('orig_entry_lo', entry_lo))
        orig_hi   = float(sig.get('orig_entry_hi', entry_hi))
        zone_width = orig_hi - orig_lo if orig_hi > orig_lo else entry_hi - entry_lo
        max_shift  = zone_width * 2.0  # 最大平移距离 = 区间宽度×2

        # 获取实时价格
        try:
            r = urllib.request.urlopen(
                f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}',
                timeout=3)
            price = float(json.loads(r.read())['price'])
        except Exception:
            results[sym] = 'SKIP'
            continue

        gap = (price - entry_lo) / entry_lo if entry_lo else 0
        if direction == 'LONG':
            gap = (entry_lo - price) / entry_lo if entry_lo else 0

        # 价格已在入场区内 → 不需要追踪
        if entry_lo <= price <= entry_hi:
            results[sym] = 'VALID'
            continue

        # 价格距入场区 < 1% → 保持不动，快到了
        if abs(gap) < 0.01:
            results[sym] = 'NEAR'
            continue

        # 计算已平移距离
        total_shifted = abs(entry_lo - orig_lo)
        if total_shifted >= max_shift:
            # 已达最大追踪距离，不再追踪
            results[sym] = 'MAX_SHIFT'
            continue

        # SHORT: 价格在入场区下方（被跌穿）→ 向下追踪入场区
        # LONG:  价格在入场区上方（被涨穿）→ 向上追踪入场区
        shift = 0
        if direction == 'SHORT' and price < entry_lo * 0.99:
            # 价格在入场区下方：入场区向下平移，跟上价格
            new_lo = price * 1.003   # 新入场区 = 当前价上方0.3%
            new_hi = price * 1.008
            shift = entry_lo - new_lo
        elif direction == 'LONG' and price > entry_hi * 1.01:
            # 价格在入场区上方：入场区向上平移
            new_lo = price * 0.992
            new_hi = price * 0.997
            shift = new_lo - entry_lo
        else:
            results[sym] = 'VALID'
            continue

        if shift <= 0:
            results[sym] = 'VALID'
            continue

        # 保存原始入场区（首次追踪时）
        if 'orig_entry_lo' not in sig:
            sig['orig_entry_lo'] = entry_lo
            sig['orig_entry_hi'] = entry_hi

        # 更新入场区 + 同步watch_lo/hi
        sig['entry_lo']  = round(new_lo, 8)
        sig['entry_hi']  = round(new_hi, 8)
        sig['watch_lo']  = round(new_lo * 0.97, 8)
        sig['watch_hi']  = round(new_hi * 1.03, 8)
        sig['tracked_at'] = datetime.now(timezone.utc).isoformat()
        sig['track_count'] = sig.get('track_count', 0) + 1
        watch[key] = sig

        pass  # [静默]
        results[sym] = 'UPDATED'

    if any(v == 'UPDATED' for v in results.values()):
        _save_watch(watch)

    return results
