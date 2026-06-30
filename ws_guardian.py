#!/usr/bin/env python3
"""
【架构说明 2026-06-11 · Phase3拆分计划】
当前职责（58KB，待拆分）：
  Layer-1  ws_core          WebSocket连接/重连/心跳
  Layer-2  position_monitor  持仓监控（读brahma_state）
  Layer-3  tp_sl_engine      TP1/TP2/SL触发与执行
  Layer-4  dynamic_sl_sync   动态SL同步（每30s）
  Layer-5  liq_flow          清算流监控（s7维度数据源）

Phase3拆分目标（武曲Paper≥200条后）：
  brahma_brain/ws_core.py        ← Layer-1
  scripts/position_monitor.py    ← Layer-2+3+4
  scripts/liq_flow_monitor.py    ← Layer-5
  ws_guardian.py保留为协调入口（薄层）

⚠️ 拆分前提：回归测试14/14 PASS，且无持仓
"""

"""
⚡ 梵天 v11 · WebSocket 实时止损守护  ws_guardian.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院裁决（P2-1）：
  替代 stop_loss_engine.py 的 60s 轮询
  响应延迟：60s → <500ms

架构：
  每个持仓 → 独立 WebSocket 线程订阅 markPrice
  markPrice 触碰止损价 → 立即触发平仓

用法：
  python3 ws_guardian.py            # 守护所有当前持仓
  python3 ws_guardian.py --status   # 查看守护状态
  python3 ws_guardian.py --test     # 测试连接（不下单）

注意：当 WebSocket 断连时自动降级到 60s 轮询（keep-alive fallback）
"""

VERSION = 'v1.0'  # 设计院 2026-05-20
import json, os, sys, time, threading, subprocess

# 末梢神经接入
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from nerve_system.nerve_emitter import get_nerve as _get_nerve
    _nerve = _get_nerve("ws_guardian")
except Exception:
    class _FallbackNerve:
        def emit(self, *a, **kw): pass
        def sl_triggered(self, *a, **kw): pass
        def tp_triggered(self, *a, **kw): pass
        def position_ghost(self, *a, **kw): pass
    _nerve = _FallbackNerve()
import atexit
import traceback as _traceback
import urllib.request
import sys as _sys
try:
    _sys.path.insert(0, '/root/.openclaw/workspace/trading-system/lana')
    from exit_engine import TrailingStop as _TrailingStop
    _TRAILING_ENABLED = True
except Exception as _te_import:
    _TRAILING_ENABLED = False
from datetime import datetime, timezone
from tz_utils import now_cst_hms, now_cst, now_utc_iso
from typing import Dict, Optional

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

SIGNAL_LOG = os.path.join(_DIR, 'signals', 'signal_history.json')
GUARDIAN_STATE = os.path.join(_DIR, 'data', 'ws_guardian_state.json')
_HEARTBEAT_FILE = os.path.join(_DIR, 'data', 'ws_guardian_state.json')
_heartbeat_stop = threading.Event()

def _heartbeat_writer():
    """独立心跳线程：每5秒写一次心跳，完全脱离主循环，不受REST/WS阻塞影响"""
    import json as _hj
    while not _heartbeat_stop.is_set():
        try:
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
            with _lock:
                watching = len([v for v in _watchers.values() if v.get('thread') and v['thread'].is_alive()])
            _ts_now = int(time.time())
            payload = {
                'pid':        os.getpid(),
                'status':     'active',
                'ts':         _ts_now,          # cron读取字段（unix时间戳）
                'last_ping':  now_iso,
                'watching':   watching,
                'updated_at': now_iso,
            }
            # 原子写：先写tmp再rename，避免读到半截文件
            tmp = _HEARTBEAT_FILE + '.tmp'
            with open(tmp, 'w') as _f:
                _hj.dump(payload, _f, indent=2)
            os.replace(tmp, _HEARTBEAT_FILE)
        except Exception:
            pass
        # 每30s批量更新主要标的实时价格到 live_price_feed
        try:
            import sys as _slpf3, os as _olpf3
            _BASE_DIR = _olpf3.path.dirname(_olpf3.path.abspath(__file__))
            _slpf3.path.insert(0, _olpf3.path.join(_BASE_DIR, 'brahma_brain'))
            from live_price_feed import bulk_update_from_api as _bulk3
            _bulk3(['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','DOGEUSDT',
                    'LTCUSDT','XRPUSDT','ADAUSDT','AVAXUSDT','DOTUSDT',
                    'LINKUSDT','XAUUSDT','XAGUSDT'])
        except Exception:
            pass
        _heartbeat_stop.wait(5)  # 每5秒一次

# 启动独立心跳线程（守护线程，随主进程自动退出）
# OFFLINE_REPLAY=1 时跳过，防止 replay 进程被 WS 线程干扰
if not os.environ.get('OFFLINE_REPLAY'):
    _hb_thread = threading.Thread(target=_heartbeat_writer, name='heartbeat', daemon=True)
    _hb_thread.start()

FAPI_WS  = 'wss://fstream.binance.com/ws'
FAPI_REST = 'https://fapi.binance.com'

os.makedirs(os.path.dirname(GUARDIAN_STATE), exist_ok=True)

# ─── 状态存储 ─────────────────────────────────────────────────────

_watchers: Dict[str, dict] = {}   # symbol → {thread, price, sl, triggered}
_lock = threading.Lock()


def _log(msg: str):
    ts = now_cst_hms()  # 北京时间
    line = f"[WS-GUARD {ts}] {msg}"
    print(line)
    try:
        log_path = os.path.join(_DIR, 'logs', 'ws_guardian.log')
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'a') as f:
            f.write(f"{now_cst().date()} {line}\n")  # 北京日期
    except Exception as _exc:
        _ = None  # 非致命


def _cli(*args, timeout=15):
    try:
        r = subprocess.run(['binance-cli'] + list(args),
                           capture_output=True, text=True, timeout=timeout)
        out = r.stdout.strip()
        return (True, json.loads(out)) if out else (False, r.stderr.strip())
    except Exception as e:
        return False, str(e)


# ─── 持仓读取 ─────────────────────────────────────────────────────


def _log_json(event: str, data: dict):
    """[稳定层 2026-05-22] 结构化JSON日志，供后续分析"""
    try:
        from pathlib import Path as _Plj
        import json as _jlj
        record = {
            'ts': datetime.now(timezone.utc).isoformat()[:19]+'Z',
            'event': event,
            **data
        }
        log_path = _Plj(_DIR) / 'logs' / 'ws_guardian_events.jsonl'
        log_path.parent.mkdir(exist_ok=True)
        with open(log_path, 'a', encoding='utf-8') as _f:
            _f.write(_jlj.dumps(record, ensure_ascii=False) + '\n')
    except Exception:
        pass


# ─── 清算流监控 ──────────────────────────────────────────────────
# 星枢引擎 · 设计院 2026-06-09
# 订阅全市场强平流：!forceOrder@arr
# 落盘到 logs/liquidation_stream.jsonl，每条带时间戳/标的/方向/价格/金额

LIQ_LOG = os.path.join(_DIR, 'logs', 'liquidation_stream.jsonl')
LIQ_SYMBOLS = {'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT', 'BNBUSDT',
               'LINKUSDT', 'LTCUSDT', 'ZKCUSDT'}  # 梵天关注标的

# 24H 滚动统计（内存，用于 liq_scanner 补充）
_liq_stats: dict = {}  # symbol → {long_usd, short_usd, last_ts, events:[]}
_liq_lock = threading.Lock()


def _log_liquidation(event_data: dict):
    """落盘一条强平事件 + 更新内存统计"""
    try:
        o = event_data.get('o', {})
        sym    = o.get('s', '')
        side   = o.get('S', '')   # BUY=空头被清, SELL=多头被清
        price  = float(o.get('ap', 0) or o.get('p', 0))
        qty    = float(o.get('q', 0))
        usd    = price * qty
        ts_ms  = o.get('T', 0)
        ts_iso = datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).isoformat()[:19]+'Z'

        if usd < 1000:  # 过滤小额噪音（<$1000）
            return

        direction = 'LONG_LIQ' if side == 'SELL' else 'SHORT_LIQ'
        record = {
            'ts':        ts_iso,
            'symbol':    sym,
            'direction': direction,
            'price':     round(price, 4),
            'qty':       round(qty, 4),
            'usd':       round(usd, 2),
            'side':      side,
        }

        # 落盘 JSONL
        os.makedirs(os.path.dirname(LIQ_LOG), exist_ok=True)
        with open(LIQ_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

        # 内存滚动统计（仅关注标的）
        if sym in LIQ_SYMBOLS:
            with _liq_lock:
                if sym not in _liq_stats:
                    _liq_stats[sym] = {'long_usd': 0.0, 'short_usd': 0.0,
                                       'last_ts': '', 'events': []}
            st = _liq_stats[sym]
            st['last_ts'] = ts_iso
            st['events'].append(record)
            # 只保留近1小时事件
            cutoff = time.time() - 3600
            st['events'] = [e for e in st['events']
                            if datetime.fromisoformat(e['ts'].replace('Z','+00:00')).timestamp() > cutoff]
            if direction == 'LONG_LIQ':
                st['long_usd'] += usd
            else:
                st['short_usd'] += usd

            # 大额清算告警（>$500K 写入 ws_guardian 主日志）
            if usd >= 500_000:
                _log(f"🚨 大额清算 {direction} {sym} ${usd/1e6:.2f}M @ ${price:,.2f}")

    except Exception:
        pass


def get_liq_stats(symbol: str) -> dict:
    """供 liq_scanner 调用：获取近1H真实清算统计"""
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'
    with _liq_lock:
        st = _liq_stats.get(sym, {})
        if not st:
            return {'available': False, 'long_usd_1h': 0, 'short_usd_1h': 0, 'events': 0}
        return {
            'available':    True,
            'long_usd_1h':  round(st.get('long_usd', 0), 2),
            'short_usd_1h': round(st.get('short_usd', 0), 2),
            'events':       len(st.get('events', [])),
            'last_ts':      st.get('last_ts', ''),
        }


def _liq_stream_thread():
    """独立守护线程：订阅 !forceOrder@arr 全市场强平流"""
    _log("📡 清算流线程启动 (!forceOrder@arr)")
    reconnect_wait = 5
    _conn_start = None  # 连接开始时间（用于检测闪断）

    while True:
        try:
            import websocket as _ws_mod  # noqa
            import time as _time_liq
            ws_url = f"{FAPI_WS}/!forceOrder@arr"
            _conn_start = _time_liq.time()

            def on_msg(ws, message):
                try:
                    data = json.loads(message)
                    # 单条事件: {"e":"forceOrder","E":...,"o":{...}}
                    # 批量数组: [{"e":"forceOrder",...}, ...]
                    if isinstance(data, list):
                        for item in data:
                            _log_liquidation(item)
                    else:
                        _log_liquidation(data)
                except Exception:
                    pass

            def on_error(ws, error):
                _log(f"⚠️ 清算流WS错误: {error}")

            def on_close(ws, *args):
                _log("🔌 清算流WS断开，5秒后重连")

            def on_open(ws):
                _log("✅ 清算流WS已连接 !forceOrder@arr")

            app = _ws_mod.WebSocketApp(
                ws_url,
                on_message=on_msg,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open,
            )
            app.run_forever(ping_interval=20, ping_timeout=8)
            # 连接正常退出后，检查持续时间
            _dur = _time_liq.time() - (_conn_start or _time_liq.time())
            if _dur < 10:  # 闪断：10秒内断开 → 延长等待
                reconnect_wait = min(reconnect_wait * 2, 120)
                _log(f"⚡ 清算流闪断(持续{_dur:.0f}s)，下次等待{reconnect_wait}s")
            else:
                reconnect_wait = 5  # 正常断开：重置等待时间

        except ImportError:
            _log("⚠️ websocket-client 未安装，清算流不可用")
            time.sleep(60)
            return
        except Exception as e:
            _log(f"⚠️ 清算流异常: {e}，{reconnect_wait}秒后重连")

        time.sleep(reconnect_wait)
        reconnect_wait = min(reconnect_wait * 2, 60)


# 启动清算流守护线程
# OFFLINE_REPLAY=1 时跳过
if not os.environ.get('OFFLINE_REPLAY'):
    _liq_thread = threading.Thread(target=_liq_stream_thread, name='liq_stream', daemon=True)
    _liq_thread.start()


def get_open_signals() -> list:
    """优先从 brahma_state.positions 读取 OPEN 持仓（新系统），兜底读 signal_history"""
    result = []
    # 1. brahma_state（新格式，唯一真相）
    try:
        import pathlib as _pl
        state_file = _pl.Path(_DIR) / 'data' / 'brahma_state.json'
        state = json.loads(state_file.read_text())
        positions = state.get('positions', [])
        for p in positions:
            if p.get('status') != 'OPEN':
                continue
            sl = p.get('sl_price') or p.get('stop_loss')
            if not sl or not p.get('symbol'):
                continue
            # 标准化为 ws_guardian 期望的格式（含TP价格，供TP监控使用）
            result.append({
                'signal_id':   p.get('signal_id', ''),
                'symbol':      p.get('symbol'),
                'direction':   p.get('direction', 'LONG'),
                'stop_loss':   float(sl),
                'tp1':         float(p.get('tp1_price') or p.get('tp1') or 0) or None,
                'tp2':         float(p.get('tp2_price') or p.get('tp2') or 0) or None,
                'entry_price': p.get('entry_price', 0),
                'qty':         p.get('qty', 0),
                'tf_aligned':  int(p.get('tf_aligned', 0) or 0),   # [P0修复 2026-05-22]
                'open_ts':     p.get('open_ts', ''),
                'regime':      p.get('regime', '') or 'UNKNOWN',
                'score':       p.get('score', 0),
            })
        if result:
            return result
    except Exception as _exc:
        _ = None  # 非致命
    # 2. 兜底：signal_history（旧格式）
    try:
        sigs = json.load(open(SIGNAL_LOG))
        return [s for s in sigs if s.get('status') in ('PENDING', 'OPEN')
                and s.get('stop_loss') and s.get('symbol')
                and s.get('signal_id')]  # 必须有 signal_id，防幽灵
    except Exception:
        return []


def get_live_price(symbol: str) -> float:
    """REST轮询价格（WebSocket降级备用）"""
    try:
        url = f'{FAPI_REST}/fapi/v1/ticker/price?symbol={symbol.upper()}'
        data = json.loads(urllib.request.urlopen(url, timeout=5).read())
        return float(data.get('price', 0))
    except Exception:
        return 0.0


# ─── 止损触发 ─────────────────────────────────────────────────────

def trigger_stop_loss(sig: dict, current_price: float):
    """触发平仓（市价止损）"""
    sym       = sig['symbol'].upper()
    direction = sig.get('direction', 'LONG')
    sl_price  = sig.get('stop_loss', 0)
    qty       = sig.get('qty', 0)

    _log(f"🛑 止损触发: {sym} {direction}  当前={current_price:.6f}  止损={sl_price:.6f}")
    _nerve.sl_triggered(sym, current_price)

    # ── EventBus 广播止损事件（vnpy借鉴，苏摩111批准）──
    try:
        import sys as _sys_eb; import os as _os_eb
        _eb_path = _os_eb.path.join(_os_eb.path.dirname(__file__), 'brahma_brain')
        if _eb_path not in _sys_eb.path: _sys_eb.path.insert(0, _os_eb.path.dirname(__file__))
        from brahma_brain.brahma_event_bus import bus as _eb_bus
        _eb_bus.emit_sl_triggered(
            symbol=sym, trigger_price=current_price,
            sl_price=sl_price, signal_id=sig.get('signal_id', '')
        )
    except Exception as _eb_err:
        _log(f"  [EventBus] SL广播失败（非阻断）: {_eb_err}")

    if not qty or qty <= 0:
        _log(f"  ⚠️ qty={qty}，尝试从持仓查询")
        ok, pos = _cli('futures-usds', 'get-position-risk-v3')
        if ok:
            for p in (pos if isinstance(pos, list) else []):
                if p.get('symbol', '').upper() == sym:
                    qty = abs(float(p.get('positionAmt', 0)))
                    break

    if not qty or qty <= 0:
        _log(f"  ❌ 无法获取持仓量，跳过")
        return False

    side = 'SELL' if direction in ('LONG', '多', 'BUY') else 'BUY'
    ok, result = _cli(
        'futures-usds', 'new-order',
        '--symbol', sym,
        '--side', side,
        '--type', 'MARKET',
        '--quantity', str(round(qty, 3)),
        '--reduceOnly', 'true'
    )

    if ok:
        _log(f"  ✅ 市价平仓成功  orderId={result.get('orderId','')}")
        # TODO-002: 同步国库官
        try:
            import sys as _tg_sys; _tg_sys.path.insert(0, _DIR)
            from treasury_gate import get_treasury as _get_tg
            _pos_id = sig.get('_treasury_position_id') or sig.get('position_id','')
            if _pos_id:
                _ep = float(sig.get('entry_price', 0) or 0)
                _pnl = ((current_price-_ep)/_ep*100*(-1 if sig.get('direction','') in ('SHORT','做空') else 1)) if _ep > 0 else 0
                _get_tg().request_close(_pos_id, 'WS_STOP_LOSS', _pnl)
                _log(f"  🏦 国库官已同步平仓: {_pos_id} pnl={_pnl:+.2f}%")
        except Exception as _tge: _log(f"  ⚠️ 国库官同步失败: {_tge}")
        # 更新 signal_history
        _update_signal_closed(sig.get('id', ''), current_price, 'WS_STOP_LOSS')
        # 写入 trade_records.jsonl（止损结局）
        try:
            import os as _os_sl, json as _j_sl
            entry_p = float(sig.get('entry_price', 0) or 0)
            avg_price = float(result.get('avgPrice') or result.get('price') or current_price)
            dir_str   = sig.get('direction', 'LONG')
            if entry_p > 0 and avg_price > 0:
                if dir_str in ('LONG', '做多', 'BUY'):
                    pnl_pct = (avg_price - entry_p) / entry_p * 100
                else:
                    pnl_pct = (entry_p - avg_price) / entry_p * 100
                pnl_usdt = round(entry_p * float(qty) * pnl_pct / 100, 4)
            else:
                pnl_pct = 0.0; pnl_usdt = 0.0
            _sl_record = {
                'signal_id':    sig.get('signal_id', sig.get('id', '')),
                'symbol':       sym,
                'direction':    dir_str,
                'regime':       sig.get('regime', '') or 'UNKNOWN',
                'tf_aligned':   int(sig.get('tf_aligned', 0) or 0),  # [P0修复 2026-05-22]
                'score':        sig.get('score', 0),
                'entry_price':  entry_p,
                'close_price':  avg_price,
                'qty':          float(qty),
                'pnl_pct':      round(pnl_pct, 4),
                'pnl_usdt':     pnl_usdt,
                'realized_pnl': pnl_usdt,
                'result':       'LOSS' if pnl_pct < 0 else ('BREAK_EVEN' if pnl_pct == 0 else 'WIN'),
                'close_reason': 'WS_STOP_LOSS',
                'close_ts':     datetime.now(timezone.utc).isoformat(),
                'open_ts':      sig.get('open_ts', ''),
                'nav_verified': entry_p > 0 and avg_price > 0,
                'data_quality': 'clean' if (entry_p > 0 and avg_price > 0) else 'suspect',
                'dharma_eligible': entry_p > 0 and avg_price > 0,
                'source':       'ws_guardian_sl',
                '_result_auto':  True,
            }
            _tr_path = _os_sl.path.join(_DIR, 'data', 'trade_records.jsonl')
            with open(_tr_path, 'a', encoding='utf-8') as _tf:
                _tf.write(_j_sl.dumps(_sl_record, ensure_ascii=False) + '\n')
            _log(f"  📝 止损结局写入: pnl={pnl_pct:+.2f}% result=LOSS")
            # [I7复盘] 即时复盘写入review_queue
            try:
                import sys as _sys_rv; _sys_rv.path.insert(0, _DIR)
                from brahma_brain.auto_review import review_single as _rv
                _rv(_sl_record)
            except Exception as _rv_e:
                _log(f"  ℹ️ auto_review跳过: {_rv_e}")
            # 触发 signal_settler 快速结算
            try:
                import sys as _sys_sl
                _sys_sl.path.insert(0, _DIR)
                from signal_settler import settle as _settle
                _settle(dry_run=False)
            except Exception as _se:
                _log(f"  ⚠️ signal_settler自动结算失败: {_se}")
        except Exception as _e_sl:
            _log(f"  ⚠️ trade_records止损写入失败: {_e_sl}")
        return True
    else:
        _log(f"  ❌ 平仓失败: {result}")
        return False


def _update_signal_closed(sig_id: str, exit_price: float, reason: str):
    """更新信号历史：标记为已平仓"""
    try:
        sigs = json.load(open(SIGNAL_LOG))
        for s in sigs:
            if s.get('id') == sig_id:
                s['status']       = 'CLOSED'
                s['exit_price']   = exit_price
                s['close_time']   = datetime.now(timezone.utc).isoformat()
                s['close_reason'] = reason
                break
        with open(SIGNAL_LOG, 'w') as f:
            json.dump(sigs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log(f"  ⚠️ 更新信号历史失败: {e}")

def _close_partial(sig: dict, current_price: float, qty_ratio: float = 0.5, reason: str = 'TP部分平仓'):
    """
    TP部分平仓：按 qty_ratio 比例平掉持仓，同时回写 brahma_state + trade_records
    qty_ratio=0.5 → 平50%（TP1标准减仓）
    """
    sym       = sig['symbol'].upper()
    direction = sig.get('direction', 'LONG')
    qty_total = float(sig.get('qty', 0))
    qty_close = round(qty_total * qty_ratio, 3)
    if qty_close <= 0:
        _log(f"  ⚠️ {sym} 部分平仓qty={qty_close}，跳过")
        return False

    side = 'SELL' if direction in ('LONG', '做多', 'BUY') else 'BUY'
    _log(f"  🎯 {sym} 部分平仓 {int(qty_ratio*100)}%: qty={qty_close} reason={reason}")

    ok, result = _cli(
        'futures-usds', 'new-order',
        '--symbol', sym,
        '--side', side,
        '--type', 'MARKET',
        '--quantity', str(qty_close),
        '--reduceOnly', 'true'
    )
    if ok:
        avg_price = float(result.get('avgPrice') or result.get('price') or current_price)
        _log(f"  ✅ 部分平仓成功 qty={qty_close} avgPrice={avg_price:.6g}")
        # 更新 brahma_state：剩余数量
        try:
            import pathlib as _pl2
            state_file = _pl2.Path(_DIR) / 'data' / 'brahma_state.json'
            state = json.loads(state_file.read_text())
            for p in state.get('positions', []):
                if p.get('signal_id') == sig.get('signal_id') and p.get('status') == 'OPEN':
                    p['qty'] = round(qty_total - qty_close, 3)
                    p['_tp1_hit'] = True
                    p['_tp1_price'] = avg_price
                    break
            state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        except Exception as _e2:
            _log(f"  ⚠️ brahma_state更新失败: {_e2}")
        # 写入 trade_records.jsonl（部分平仓记录）
        try:
            import os as _os3
            entry_p = float(sig.get('entry_price', 0) or 0)
            direction_str = sig.get('direction', 'LONG')
            if entry_p > 0 and avg_price > 0:
                if direction_str in ('LONG', '做多', 'BUY'):
                    pnl_pct = (avg_price - entry_p) / entry_p * 100
                else:
                    pnl_pct = (entry_p - avg_price) / entry_p * 100
                kelly = float(sig.get('kelly_pct', 0.03))
                pnl_usdt = round(entry_p * qty_close * pnl_pct / 100, 4)
            else:
                pnl_pct = 0.0; pnl_usdt = 0.0
            record = {
                'signal_id':    sig.get('signal_id', ''),
                'symbol':       sym,
                'direction':    direction_str,
                'regime':       sig.get('regime','') or 'UNKNOWN',
                'tf_aligned':   int(sig.get('tf_aligned',0) or 0),  # [P0修复 2026-05-22]
                'entry_price':  entry_p,
                'close_price':  avg_price,
                'qty':          qty_close,
                'pnl_pct':      round(pnl_pct, 4),
                'pnl_usdt':     pnl_usdt,
                'realized_pnl': pnl_usdt,
                'close_reason': reason,
                'close_ts':     datetime.now(timezone.utc).isoformat(),
                'open_ts':      sig.get('open_ts', ''),
                'nav_verified': entry_p > 0 and avg_price > 0,
                'nav_at_open':  sig.get('nav', 0),
                'data_quality': 'clean' if (entry_p > 0 and avg_price > 0) else 'suspect',
                'dharma_eligible': entry_p > 0 and avg_price > 0,
                'source':       'ws_guardian_tp',
                'partial':      True,
                'qty_ratio':    qty_ratio,
                'result':       'WIN_T1' if 'tp1' in reason.lower() else ('WIN_T2' if 'tp2' in reason.lower() else ('WIN' if pnl_pct > 0 else 'LOSS')),
                '_result_auto':  True,
            }
            tr_path = _os3.path.join(_DIR, 'data', 'trade_records.jsonl')
            with open(tr_path, 'a', encoding='utf-8') as _tf:
                _tf.write(json.dumps(record, ensure_ascii=False) + '\n')
            _log(f"  📝 部分平仓记录写入: pnl={pnl_pct:+.2f}% {pnl_usdt:+.4f}U")
            # TODO-002: 全部平仓时同步国库官
            if abs(qty_ratio - 1.0) < 0.01:  # 100%平仓
                try:
                    from treasury_gate import get_treasury as _get_tg2
                    _pos_id2 = sig.get('_treasury_position_id') or sig.get('position_id','')
                    if _pos_id2:
                        _get_tg2().request_close(_pos_id2, reason, pnl_pct)
                        _log(f"  🏦 国库官已同步TP平仓: {_pos_id2}")
                except Exception as _tge2: _log(f"  ⚠️ 国库官TP同步失败: {_tge2}")
            # [I7复盘] TP复盘
            try:
                import sys as _sys_rv2; _sys_rv2.path.insert(0, _DIR)
                from brahma_brain.auto_review import review_single as _rv2
                _rv2(record)
            except Exception as _rv_e2:
                _log(f"  ℹ️ auto_review(tp)跳过: {_rv_e2}")
        except Exception as _e3:
            _log(f"  ⚠️ trade_records写入失败: {_e3}")
        return True
    else:
        _log(f"  ❌ 部分平仓失败: {result}")
        return False


# ─── WebSocket 守护线程 ────────────────────────────────────────────

def _ws_watcher(sig: dict, fallback_only=False):
    """
    单个持仓的 WebSocket 守护线程
    fallback_only=True: 跳过 WebSocket，直接使用 REST 轮询
    """
    sym      = sig['symbol'].upper()
    sl_price = float(sig.get('stop_loss', 0))
    direction = sig.get('direction', 'LONG')
    sig_id   = sig.get('id', '')

    if not sl_price:
        _log(f"  ⚠️ {sym} 无止损价，跳过守护")
        return

    _log(f"  👁️  开始守护: {sym} {direction}  止损={sl_price:.6f}")

    if not fallback_only:
        # ── WebSocket 路径 ────────────────────────────────────────
        try:
            import websocket  # 需要 websocket-client

            triggered = False
            # [P0修复 2026-05-22] WebSocket路径补充TP检测
            _ws_tp1_price = float(sig['tp1']) if sig.get('tp1') else None
            _ws_tp2_price = float(sig['tp2']) if sig.get('tp2') else None
            _ws_tp1_hit   = False

            def on_message(ws, message):
                nonlocal triggered, sl_price, _ws_tp1_hit
                if triggered:
                    return
                try:
                    data = json.loads(message)
                    price = float(data.get('p', 0) or data.get('markPrice', 0))
                    if price <= 0:
                        return

                    # 更新状态 + 写入实时价格中枢
                    with _lock:
                        if sym in _watchers:
                            _watchers[sym]['price'] = price
                    try:
                        import sys as _slpf, os as _olpf
                        _slpf.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'brahma_brain'))
                        from live_price_feed import update_price as _up
                        _up(sym, price, source='ws_markprice')
                    except Exception:
                        pass

                    # ── TP1检测（WebSocket路径）──
                    if _ws_tp1_price and not _ws_tp1_hit:
                        tp1_hit_now = (price <= _ws_tp1_price) if direction not in ('LONG','多','BUY') else (price >= _ws_tp1_price)
                        if tp1_hit_now:
                            _ws_tp1_hit = True
                            _log(f"  🎯 [WS] TP1触达: {sym} price={price:.6f} tp1={_ws_tp1_price:.6f}")
                            _nerve.tp_triggered(sym, price)
                            ws.close()  # 切换到REST路径处理部分平仓
                            return

                    # ── TP2检测（WebSocket路径，TP1已触达后）──
                    if _ws_tp2_price and _ws_tp1_hit:
                        tp2_hit_now = (price <= _ws_tp2_price) if direction not in ('LONG','多','BUY') else (price >= _ws_tp2_price)
                        if tp2_hit_now:
                            triggered = True
                            _log(f"  🏆 [WS] TP2触达: {sym} price={price:.6f} tp2={_ws_tp2_price:.6f}")
                            ws.close()
                            return

                    # ── SL检测 ──
                    if direction in ('LONG', '多'):
                        hit = price <= sl_price
                    else:
                        hit = price >= sl_price

                    if hit:
                        triggered = True
                        _log(f"  ⚡ WebSocket止损触发: {sym} price={price:.6f}")
                        _nerve.sl_triggered(sym, price)
                        ws.close()
                        trigger_stop_loss(sig, price)
                except Exception as _exc:
                    _ = None  # 非致命

            def on_error(ws, error):
                _log(f"  ⚠️ {sym} WS错误: {error}，降级到REST轮询")

            def on_close(ws, *args):
                if not triggered:
                    _log(f"  ℹ️ {sym} WS断连，切换REST轮询")

            stream = f"{sym.lower()}@markPrice"
            ws_url = f"{FAPI_WS}/{stream}"
            ws_app = websocket.WebSocketApp(ws_url,
                                            on_message=on_message,
                                            on_error=on_error,
                                            on_close=on_close)
            # ── 自动重连循环（最多5次，每次间隔30s）────────────────
            _ws_retry = 0
            _WS_MAX_RETRY = 5
            while _ws_retry < _WS_MAX_RETRY and not triggered:
                ws_app = None  # [修复] 每次循环重置，避免连接泄漏
                try:
                    ws_app = websocket.WebSocketApp(ws_url,
                                                    on_message=on_message,
                                                    on_error=on_error,
                                                    on_close=on_close)
                    ws_app.run_forever(ping_interval=20, ping_timeout=8)
                except Exception as _ws_loop_err:
                    _log(f"  ⚠️ {sym} WS循环异常: {_ws_loop_err}")
                finally:
                    # [修复] 显式关闭WS连接，释放socket资源
                    try:
                        if ws_app and hasattr(ws_app, 'close'):
                            ws_app.close()
                    except Exception:
                        pass
                    ws_app = None
                    import gc; gc.collect()
                if triggered:
                    break
                _ws_retry += 1
                if _ws_retry < _WS_MAX_RETRY:
                    _log(f"  🔄 {sym} WS重连({_ws_retry}/{_WS_MAX_RETRY})，30s后重试...")
                    time.sleep(30)
                else:
                    _log(f"  ⚠️ {sym} WS重连耗尽，降级REST轮询")

            if triggered:
                return  # WebSocket 已处理完毕

        except ImportError:
            _log(f"  ℹ️ websocket-client 未安装，使用REST轮询")
        except Exception as e:
            _log(f"  ⚠️ WebSocket异常: {e}，降级REST")

    # ── REST 轮询降级（60s → 5s 改善版）────────────────────────
    _log(f"  🔄 {sym} REST轮询守护（5s间隔）")
    sl_price  = float(sig.get('stop_loss', 0))
    tp1_price = float(sig['tp1']) if sig.get('tp1') else None
    tp2_price = float(sig['tp2']) if sig.get('tp2') else None
    tp1_hit   = False  # TP1已触达标志
    _be_locked = False  # 保本移止损已激活标志（浮盈≥0.8%触发，早于TP1）
    _BREAKEVEN_TRIGGER_PCT = 0.5  # [v12.9c] 0.8%→0.5%: 更早锁定浮盈
    _BREAKEVEN_BUFFER      = 0.001  # 保本价在entry上留0.1%缓冲，避免极端滑点被扫
    # 阿周那专属：更敏感的保本触发（快车道快锁）
    _ARJUNA_BREAKEVEN_PCT  = 0.3   # [v12.9c] 阿周那持仓浮盈0.3%即触发
    try:
        _bp_cfg = json.load(open(os.path.join(os.path.dirname(__file__), '..', 'FANTAN_BLUEPRINT_V3.json')))
        _be_cfg  = _bp_cfg.get('_runtime_config', {}).get('breakeven', {})
        _BREAKEVEN_TRIGGER_PCT = float(_be_cfg.get('trigger_pct', _BREAKEVEN_TRIGGER_PCT))
        _BREAKEVEN_BUFFER      = float(_be_cfg.get('buffer',      _BREAKEVEN_BUFFER))
    except Exception:
        _ = None  # 非致命
    _last_bs_sl_check = 0  # 上次从brahma_state同步SL的时间戳
    while True:
        # ── [v12.9c] 动态SL同步：每30s从brahma_state读取最新SL ─────────
        import time as _ts_mod
        _now_ts = _ts_mod.time()
        if _now_ts - _last_bs_sl_check > 30:
            _last_bs_sl_check = _now_ts
            try:
                _bs_latest = json.load(open(os.path.join(_DIR, 'data', 'brahma_state.json')))
                for _bp in _bs_latest.get('positions', []):
                    if _bp.get('symbol') == sym and _bp.get('status') == 'OPEN':
                        _new_sl = float(_bp.get('sl_price') or _bp.get('stop_loss') or 0)
                        if _new_sl > 0 and abs(_new_sl - sl_price) > sl_price * 0.0005:
                            # 空单SL必须只能向下移动（收紧）；多单只能向上
                            _sl_valid = (
                                (direction not in ('LONG','多','BUY') and _new_sl < sl_price) or
                                (direction in ('LONG','多','BUY') and _new_sl > sl_price)
                            )
                            if _sl_valid:
                                _log(f"  🔄 [{sym}] SL动态同步: {sl_price:.5g} → {_new_sl:.5g}")
                                sl_price = _new_sl
                                with _lock:
                                    if sym in _watchers:
                                        _watchers[sym]['sl'] = _new_sl
            except Exception as _sl_sync_e:  # [R4-fix audit-2026-06-17] 保本移止损同步异常
                _log(f'[WS-Guardian] ⚠️ 保本移止损读取异常 {sym}: {_sl_sync_e}')
        if price > 0:
            with _lock:
                if sym in _watchers:
                    _watchers[sym]['price'] = price

            # ── SL 检测
            if direction in ('LONG', '多', 'BUY'):
                sl_hit = price <= sl_price
            else:
                sl_hit = price >= sl_price
            if sl_hit:
                _log(f"  ⚡ REST止损触发: {sym} price={price:.6f}")
                _nerve.sl_triggered(sym, price)
                trigger_stop_loss(sig, price)
                break

            # ── TP1 检测
            if tp1_price and not tp1_hit:
                if direction in ('LONG', '多', 'BUY'):
                    tp1_check = price >= tp1_price
                else:
                    tp1_check = price <= tp1_price
                if tp1_check:
                    tp1_hit = True
                    _log(f"  🎯 TP1触达: {sym} price={price:.6f} tp1={tp1_price:.6f}")
                    _nerve.tp_triggered(sym, price)
                    _close_partial(sig, price, qty_ratio=0.5, reason='TP1触达_50%减仓')
                    # 止损移至保本
                    entry_p = float(sig.get('entry_price', 0) or 0)
                    if entry_p > 0:
                        sl_price = entry_p * 1.001 if direction in ('LONG', '多', 'BUY') else entry_p * 0.999
                        _log(f"  📈 {sym} 止损移至保本: {sl_price:.6g}")
                    if not tp2_price:
                        _log(f"  ✅ {sym} TP1全额出场（无TP2），守护退出")
                        break

            # ── 浮盈保本移止损（无需等TP1，主动锁定） ────────────────────────
            if not _be_locked and price > 0:
                entry_p = float(sig.get('entry_price', 0) or sig.get('entry', 0))
                if entry_p > 0:
                    # 阿周那持仓用更敏感的触发阈值（快车道快锁）
                    _active_be_pct = _BREAKEVEN_TRIGGER_PCT  # [2026-05-20] arjuna已归档，统一用标准参数
                    if direction in ('LONG', '做多', 'BUY'):
                        pnl_pct_now = (price - entry_p) / entry_p * 100
                        be_sl = entry_p * (1 + _BREAKEVEN_BUFFER)
                    else:
                        pnl_pct_now = (entry_p - price) / entry_p * 100
                        be_sl = entry_p * (1 - _BREAKEVEN_BUFFER)
                    if pnl_pct_now >= _active_be_pct and be_sl != sl_price:
                        # 只向盆方向移动（长单止损只向上移，短单止损只向下移）
                        if direction in ('LONG', '做多', 'BUY') and be_sl > sl_price:
                            _be_locked = True
                            sl_price   = be_sl
                            _log(f"  💰 [{sym}] 浮盈保本激活: pnl={pnl_pct_now:+.2f}% 止损移至保本 {be_sl:.5g}")
                        elif direction not in ('LONG', '做多', 'BUY') and be_sl < sl_price:
                            _be_locked = True
                            sl_price   = be_sl
                            _log(f"  💰 [{sym}] 浮盈保本激活: pnl={pnl_pct_now:+.2f}% 止损移至保本 {be_sl:.5g}")

            # ── TP2 检测（TP1已触达后）
            if tp2_price and tp1_hit:
                if direction in ('LONG', '多', 'BUY'):
                    tp2_check = price >= tp2_price
                else:
                    tp2_check = price <= tp2_price
                if tp2_check:
                    _log(f"  🌟 TP2触达: {sym} price={price:.6f} tp2={tp2_price:.6f}")
                    _nerve.tp_triggered(sym, price)
                    trigger_stop_loss(sig, price)  # 剩余仓位市价全平
                    _update_signal_closed(sig.get('signal_id', ''), price, 'TP2触达_全平')
                    break

        # 检查信号是否已被外部平仓
        try:
            sigs = json.load(open(SIGNAL_LOG))
            matched = next((s for s in sigs if s.get('id') == sig_id), None)
            if matched and matched.get('status') == 'CLOSED':
                _log(f"  ✅ {sym} 已外部平仓，守护退出")
                break
        except Exception as _exc:
            _ = None  # 非致命

        # ── TrailingStop：浮盈保护 ──────────────────────────────
        if price > 0 and _TRAILING_ENABLED:
            try:
                entry_p = float(sig.get('entry_price', 0) or sig.get('entry', 0))
                if entry_p > 0:
                    dir_str = '做多' if direction in ('LONG','做多','BUY') else '做空'
                    ts = _TrailingStop(entry=entry_p, direction=dir_str,
                                      leverage=5.0, sl_price=sl_price, initial_sl=sl_price)
                    _r = ts.update(price)
                    new_sl = _r.get('new_sl') if _r.get('sl_moved') else None
                    if new_sl and abs(new_sl - sl_price) > sl_price * 0.001:
                        old_sl = sl_price
                        sl_price = new_sl
                        # 写回 signal_history
                        try:
                            _sigs = json.load(open(SIGNAL_LOG))
                            for _s in _sigs:
                                if _s.get('signal_id') == sig.get('signal_id') or _s.get('id') == sig.get('id'):
                                    _s['stop_loss'] = new_sl
                                    _s['_trailing_updated'] = datetime.now(timezone.utc).isoformat()
                                    break
                            with open(SIGNAL_LOG,'w') as _f:
                                json.dump(_sigs, _f, ensure_ascii=False, indent=2)
                            _log(f"  📈 TrailingStop {sym}: {old_sl:.5g} → {new_sl:.5g}")
                        except Exception as _e:
                            _log(f"  ⚠️ TrailingStop写入失败: {_e}")
            except Exception as _te:
                _log(f"  ⚠️ TrailingStop异常({sym}): {_te}")
        time.sleep(10)  # 10s 轮询（OOM优化：降低API调用频率）


# ─── 主守护循环 ───────────────────────────────────────────────────

def start_guardian(dry_run=False):
    """启动所有持仓的守护线程"""
    import gc; gc.collect()  # 启动时强制GC，降低内存基线
    _log("=" * 50)
    _log(f"⚡ WS Guardian 启动  {'[DRY-RUN]' if dry_run else '[LIVE]'}")
    _log("=" * 50)

    while True:
        try:
            open_sigs = get_open_signals()

            if not open_sigs:
                _log("✓ 无持仓，待机中...")
                # 待机时也写入心跳，避免神经系统误报超时
                # idle状态：心跳线程自动写入，watching=0（_watchers为空时自动反映）
                time.sleep(30)
                continue

            # 为每个持仓启动守护线程（如果还没有的话）
            for sig in open_sigs:
                sym = sig['symbol'].upper()
                with _lock:
                    if sym not in _watchers or not _watchers[sym].get('thread', None) or \
                       not _watchers[sym]['thread'].is_alive():

                        t = threading.Thread(
                            target=_ws_watcher,
                            args=(sig, dry_run),
                            daemon=True
                        )
                        t.start()
                        _watchers[sym] = {
                            'thread': t,
                            'price': 0,
                            'sl': sig.get('stop_loss', 0),
                            'direction': sig.get('direction', ''),
                            'started': datetime.now(timezone.utc).isoformat(),
                        }
                        _log(f"  🚀 守护线程启动: {sym}")

            # 保存状态（FIX: 从子线程实时读取price，避免price永远=0）
            state = {}
            for k, v in _watchers.items():
                entry = {j: v[j] for j in v if j != 'thread'}
                # 如果子线程price还是0，主动拉一次REST价格
                if entry.get('price', 0) == 0:
                    try:
                        live_p = get_live_price(k)
                        if live_p > 0:
                            entry['price'] = live_p
                            with _lock:
                                if k in _watchers:
                                    _watchers[k]['price'] = live_p
                    except Exception as _exc:
                        _ = None  # 非致命
                state[k] = entry
            now_iso = datetime.now(timezone.utc).isoformat()
            with open(GUARDIAN_STATE, 'w') as f:
                json.dump({
                    'time':       now_iso,
                    'ts':         int(time.time()),   # 心跳兼容字段
                    'pid':        os.getpid(),
                    'watching':   len(state),
                    'watchers':   state,
                }, f, indent=2)
            # ── 心跳由独立线程写入，此处仅更新watching计数供心跳线程读取（无需直接写文件）
            # （_heartbeat_writer 每5秒自动从 _watchers 读取 watching 数量）

            # ── 同步更新 brahma_state.ws_guardian 副本（避免副本过期误报）──
            try:
                import json as _j4
                from pathlib import Path as _P4
                _bs_path = _P4(_DIR) / 'data' / 'brahma_state.json'
                if _bs_path.exists():
                    _bs = _j4.loads(_bs_path.read_text())
                    _bs['ws_guardian'] = {
                        'pid':       os.getpid(),
                        'status':    'active',
                        'last_ping': now_iso,
                        'watching':  len(state),
                    }
                    _bs_path.write_text(_j4.dumps(_bs, ensure_ascii=False, indent=2))
            except Exception as _e4:
                _ = None  # 非致命，不中断心跳

            # ── 清理已结束的守护线程（防线程泄漏）────────────────
            dead_syms = [k for k,v in list(_watchers.items())
                         if not v.get('thread') or not v['thread'].is_alive()]
            for k in dead_syms:
                del _watchers[k]
                _log(f"  🧹 {k} 守护线程已结束，清理")

            # [修复] 周期性GC + 线程数检查，防内存泄漏累积崩溃
            import gc; gc.collect()
            live_threads = threading.active_count()
            if live_threads > 50:
                _log(f"  ⚠️ 活跃线程过多({live_threads})，可能存在泄漏")

            # ── [稳定层 2026-05-22] Gateway/进程内存告警 ──────────
            try:
                import resource as _res
                _rss_mb = _res.getrusage(_res.RUSAGE_SELF).ru_maxrss / 1024
                if _rss_mb > 600:
                    _log(f"  ⚠️ [内存告警] ws_guardian RSS={_rss_mb:.0f}MB > 600MB，建议重启")
                # 检查Gateway进程内存
                import subprocess as _sp_mem
                _gw = _sp_mem.run(['pgrep','-f','openclaw-gateway'],capture_output=True,text=True)
                if _gw.stdout.strip():
                    _gw_pid = _gw.stdout.strip().split()[0]
                    _gw_stat = _sp_mem.run(['cat',f'/proc/{_gw_pid}/status'],capture_output=True,text=True)
                    for _line in _gw_stat.stdout.splitlines():
                        if _line.startswith('VmRSS:'):
                            _gw_rss = int(_line.split()[1]) / 1024
                            if _gw_rss > 800:
                                _log(f"  ⚠️ [Gateway内存] PID={_gw_pid} RSS={_gw_rss:.0f}MB > 800MB，每日重启将在04:00UTC执行")
                            break
            except Exception: pass
            # ─────────────────────────────────────────────────────


            # ── [P1 2026-05-22] NAV实时回撤熔断 ─────────────────────────────
            # 规则: NAV较峰值回撤≥5% → 停止新开仓 + 发送告警（不强平现有仓）
            try:
                import hmac as _hmac, hashlib as _hashlib, urllib.request as _ureq
                from pathlib import Path as _PNav
                import sys as _sysNav; _sysNav.path.insert(0, _DIR)
                import config as _cfg
                _ak, _sk = _cfg.binance_keys()
                _ts_nav = int(time.time() * 1000)
                _p_nav  = f'timestamp={_ts_nav}&recvWindow=5000'
                _sig_nav = _hmac.new(_sk.encode(), _p_nav.encode(), _hashlib.sha256).hexdigest()
                _acct = json.loads(_ureq.urlopen(_ureq.Request(
                    f'https://fapi.binance.com/fapi/v2/account?{_p_nav}&signature={_sig_nav}',
                    headers={'X-MBX-APIKEY': _ak}), timeout=5).read())
                _nav_now = float(_acct.get('totalWalletBalance', 0))

                # 读取/更新 NAV 峰值
                _nav_state_path = _PNav(_DIR) / 'data' / 'nav_peak.json'
                if _nav_state_path.exists():
                    _nav_data = json.loads(_nav_state_path.read_text())
                else:
                    _nav_data = {'peak': _nav_now, 'peak_ts': datetime.now(timezone.utc).isoformat(),
                                 'fuse_triggered': False}
                _peak = float(_nav_data.get('peak', _nav_now))
                if _nav_now > _peak:
                    _nav_data['peak'] = _nav_now
                    _nav_data['peak_ts'] = datetime.now(timezone.utc).isoformat()
                    _nav_data['fuse_triggered'] = False
                    _peak = _nav_now

                _dd_pct = (_peak - _nav_now) / _peak * 100 if _peak > 0 else 0
                _nav_data['nav_now'] = _nav_now
                _nav_data['dd_pct']  = round(_dd_pct, 3)
                _nav_data['updated'] = datetime.now(timezone.utc).isoformat()

                # 熔断阈值: 5%
                _DD_FUSE = 5.0
                if _dd_pct >= _DD_FUSE and not _nav_data.get('fuse_triggered'):
                    _nav_data['fuse_triggered'] = True
                    _log(f"  🚨 [NAV熔断] 回撤{_dd_pct:.2f}%≥{_DD_FUSE}% 峰值=${_peak:.2f} 当前=${_nav_now:.2f}")
                    _log(f"  🛑 [NAV熔断] 已激活 — 停止新开仓信号，现有仓位正常守护")
                    # 写入熔断标志到brahma_state（executor读取后跳过新开仓）
                    try:
                        _bs_fuse = json.loads((_PNav(_DIR)/'data'/'brahma_state.json').read_text())
                        _bs_fuse['nav_fuse'] = {
                            'triggered': True,
                            'dd_pct': round(_dd_pct, 3),
                            'peak': _peak,
                            'nav_now': _nav_now,
                            'ts': datetime.now(timezone.utc).isoformat(),
                        }
                        (_PNav(_DIR)/'data'/'brahma_state.json').write_text(
                            json.dumps(_bs_fuse, ensure_ascii=False, indent=2))
                    except Exception: pass
                elif _dd_pct < _DD_FUSE * 0.8 and _nav_data.get('fuse_triggered'):
                    # 回撤降回4%以下自动解除熔断
                    _nav_data['fuse_triggered'] = False
                    _log(f"  ✅ [NAV熔断] 已解除 回撤{_dd_pct:.2f}%<{_DD_FUSE*0.8:.1f}%")
                    try:
                        _bs_fuse = json.loads((_PNav(_DIR)/'data'/'brahma_state.json').read_text())
                        _bs_fuse['nav_fuse'] = {'triggered': False, 'dd_pct': round(_dd_pct,3)}
                        (_PNav(_DIR)/'data'/'brahma_state.json').write_text(
                            json.dumps(_bs_fuse, ensure_ascii=False, indent=2))
                    except Exception: pass

                _nav_state_path.write_text(json.dumps(_nav_data, ensure_ascii=False, indent=2))
            except Exception as _nav_e:
                pass  # 熔断检测失败不中断守护
            # ─────────────────────────────────────────────────────────────────

            time.sleep(10)  # 每10s检查一次是否有新持仓

        except KeyboardInterrupt:
            _log("Guardian 已停止")
            break
        except Exception as e:
            _log(f"Guardian 异常: {e}")
            _log(f"Traceback: {_traceback.format_exc()[-500:]}")
            time.sleep(10)


def show_status():
    try:
        state = json.load(open(GUARDIAN_STATE))
        print(f"\n⚡ WS Guardian 状态  {state['time']}")
        print(f"{'─'*45}")
        watchers = state.get('watchers', {})
        if not watchers:  # noqa: P1 list-check
            print("  无活跃守护")
        for sym, info in watchers.items():
            print(f"  {sym:<15} 当前={info.get('price',0):.5f}  "
                  f"止损={info.get('sl',0):.5f}  {info.get('direction','')}")
        print(f"{'─'*45}\n")
    except Exception:
        print("  Guardian 未运行或无状态文件")


def _on_exit():
    """进程退出时记录原因（含stack trace，供 watchdog 读取）"""
    import os as _os, traceback as _tb, sys as _sys
    crash_file = os.path.join(_DIR, 'logs', 'ws_guardian_crash.log')
    try:
        # 捕获当前异常 stack trace（如果有）
        exc_info = _sys.exc_info()
        stack = ''
        exit_type = 'NORMAL'
        if exc_info[0] is not None:
            exit_type = exc_info[0].__name__
            stack = ''.join(_tb.format_exception(*exc_info))
        else:
            # 记录当前调用栈帮助定位退出点
            stack = ''.join(_tb.format_stack())
        with open(crash_file, 'a') as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} [EXIT/{exit_type}] pid={_os.getpid()}\n")
            if stack:
                f.write(f"  STACK:\n")
                for line in stack.strip().split('\n')[-12:]:
                    f.write(f"    {line}\n")
            # 记录内存/线程状态
            try:
                tc = threading.active_count()
                wc = len(_watchers)
                f.write(f"  threads={tc}  watchers={wc}\n")
                # 记录每个 watcher 线程状态
                for sym, info in list(_watchers.items()):
                    t = info.get('thread')
                    alive = t.is_alive() if t else False
                    f.write(f"    watcher: {sym} alive={alive}\n")
            except Exception:
                pass
    except Exception as _exc:
        _ = None  # 非致命

atexit.register(_on_exit)

# ─── 信号捕获：记录崩溃来源，防止 SIGTERM 无声退出 ──────────────────
import signal as _signal

def _handle_sigterm(signum, frame):
    """[v13.1] SIGTERM 处理策略修复
    原逻因: SIGTERM直接sys.exit(0) → IMMORTAL重启循环 → 旧PID消失心跳文件更新斑 → watchdog误判为宕机
    修复: 有持仓时忽略SIGTERM，记录日志并继续运行；无持仓才退出
    """
    crash_file = os.path.join(_DIR, 'logs', 'ws_guardian_crash.log')
    try:
        import traceback as _tb2
        stack = ''.join(_tb2.format_stack(frame))
        with open(crash_file, 'a') as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} [SIGTERM] pid={os.getpid()}\n")
            f.write(f"  threads={threading.active_count()}  watchers={len(_watchers)}\n")
            for line in stack.strip().split('\n')[-6:]:
                f.write(f"    {line}\n")
    except Exception:
        pass

    # [v13.1] 有持仓时不退出，只记录日志
    if _watchers:
        _log(f"[SIGNAL] SIGTERM忽略：有持仓({len(_watchers)}个)，继续守护")
        return   # 不调用sys.exit，IMMORTAL循环不中断
    # [v13.3] 无持仓时正常退出，避免supervisor无法关闭导致进程泄漏
    import sys as _sys
    _log("[SIGNAL] SIGTERM收到：无持仓，正常退出（v13.3修复）")
    _sys.exit(0)

def _handle_sigint(signum, frame):
    """捕获 SIGINT，记录日志（原 KeyboardInterrupt 路径）"""
    crash_file = os.path.join(_DIR, 'logs', 'ws_guardian_crash.log')
    try:
        with open(crash_file, 'a') as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} [SIGINT] pid={os.getpid()}\n")
            f.write(f"  threads={threading.active_count()}  watchers={len(_watchers)}\n")
    except Exception:
        pass
    _log("[SIGNAL] 收到 SIGINT，退出")
    raise KeyboardInterrupt

_signal.signal(_signal.SIGTERM, _handle_sigterm)
_signal.signal(_signal.SIGINT,  _handle_sigint)

if __name__ == '__main__':
    args = sys.argv[1:]
    if '--status' in args:
        show_status()
    elif '--test' in args:
        _log("测试模式：检查持仓 + 连接验证")
        sigs = get_open_signals()
        _log(f"当前持仓信号: {len(sigs)}个")
        for s in sigs:
            p = get_live_price(s['symbol'])
            _log(f"  {s['symbol']}: 止损={s.get('stop_loss')}  当前={p:.5f}")
    else:
        dry_run = '--dry' in args
        # 永生循环：start_guardian 无论因何退出，都自动重启
        # 唯一退出条件：KeyboardInterrupt（Ctrl+C / SIGTERM 由 atexit 处理）
        _restart_count = 0
        while True:
            try:
                start_guardian(dry_run=dry_run)
                # start_guardian 正常返回（理论上不应发生）
                _log(f"[IMMORTAL] Guardian 意外退出，2秒后重启（第{_restart_count+1}次）")
            except KeyboardInterrupt:
                _log("[IMMORTAL] 收到 KeyboardInterrupt，停止")
                break
            except SystemExit as _se:
                # [修复 2026-05-22] 不允许 SystemExit 干净退出
                # 外部 cron/agent 发出 sys.exit 时曾导致进程静默消失
                # 现改为记录并继续重启，只有 code=0 且无持仓时才真正退出
                _restart_count += 1
                _log(f"[IMMORTAL] 收到 SystemExit(code={_se.code})，检查是否可安全退出...")
                try:
                    _open = get_open_signals()
                    if _se.code == 0 and not _open:
                        _log("[IMMORTAL] 无持仓 + SystemExit(0)，安全退出")
                        break
                    _log(f"[IMMORTAL] 有持仓或非零退出码，忽略 SystemExit，继续守护（持仓={len(_open)}个）")
                    _nerve.emit('PROCESS_CRASH',
                                {'error': f'SystemExit({_se.code})', 'restart': str(_restart_count)},
                                message=f'⚠️ ws_guardian 收到SystemExit({_se.code})但有持仓，已忽略继续运行')
                except Exception:
                    _log("[IMMORTAL] SystemExit + 持仓查询失败，继续守护")
            except Exception as _ie:
                _restart_count += 1
                _log(f"[IMMORTAL] 主循环崩溃 ({_restart_count}): {_ie}")
                _log(f"[IMMORTAL] Traceback: {_traceback.format_exc()[-800:]}")
                _nerve.emit('PROCESS_CRASH', {'error': str(_ie), 'restart': str(_restart_count)},
                            message=f'ws_guardian主循环崩溃(第{_restart_count}次): {_ie}')
            _log("[IMMORTAL] 5秒后重启...")
            time.sleep(5)
    assert VERSION == 'v1.0', 'ws_guardian version ok'