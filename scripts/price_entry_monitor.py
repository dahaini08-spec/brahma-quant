#!/usr/bin/env python3
"""
price_entry_monitor.py — 入场区触发监控器 v2.0
设计院 · 2026-06-18 | 武曲自动开单接入 2026-06-20（苏摩111批准）

功能：
  扫描 live_signal_log.jsonl 中所有 OPEN 信号
  对比 Binance 实时价格，若价格进入入场区：
    - VIP_STRATEGY 信号 → 调用 auto_execute_gate 自动开单
    - 梵天系统信号 → 推送提醒等待人工确认
  苏摩宪法：auto_execute仅对 source='VIP_STRATEGY' 或 score≥138 的信号生效
"""
import json, time, urllib.request, sys
from pathlib import Path

BASE = Path(__file__).parent.parent
LOG_PATH = BASE / 'data' / 'live_signal_log.jsonl'
ALERT_STATE = BASE / 'data' / 'entry_monitor_state.json'

MAX_HOLD_HOURS = 16

def get_price(symbol):
    url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
    return float(json.loads(urllib.request.urlopen(url, timeout=6).read())['price'])

def load_signals():
    signals = []
    if not LOG_PATH.exists():
        return signals
    with open(LOG_PATH) as f:
        for line in f:
            s = line.strip()
            if s:
                try: signals.append(json.loads(s))
                except: pass
    return signals

def load_state():
    if ALERT_STATE.exists():
        try: return json.load(open(ALERT_STATE))
        except: pass
    return {'alerted': {}}

def save_state(state):
    with open(ALERT_STATE, 'w') as f:
        json.dump(state, f, indent=2)

def main():
    signals = load_signals()
    state = load_state()
    now = time.time()
    alerted = state.get('alerted', {})

    opens = [s for s in signals if s.get('status') == 'OPEN']
    if not opens:
        pass  # [静默]
        return

    alerts = []
    for s in opens:
        sym = s.get('symbol')
        d = s.get('direction')
        ts_raw = float(s.get('ts') or s.get('timestamp', now))
        age_h = (now - ts_raw) / 3600
        
        # TTL检查
        if age_h >= MAX_HOLD_HOURS:
            continue

        lo = float(s.get('entry_lo', 0))
        hi = float(s.get('entry_hi', 0))
        sl = float(s.get('stop_loss', 0))
        tp1 = float(s.get('tp1', 0))
        score = s.get('score', 0)
        regime = s.get('regime', '')
        
        # 信号唯一key
        sig_key = f"{sym}_{d}_{int(ts_raw)}"
        
        # 已提醒过且在15min内，跳过
        last_alert = alerted.get(sig_key, 0)
        if now - last_alert < 14400:  # 4H内同一信号不重复推送
            continue

        try:
            cur = get_price(sym)
            in_zone = lo <= cur <= hi
            
            if in_zone:
                # 计算RR
                if d == 'SHORT':
                    rr = (cur - tp1) / (sl - cur) if sl > cur else 0
                else:
                    rr = (tp1 - cur) / (cur - sl) if cur > sl else 0
                
                alerts.append({
                    'symbol': sym,
                    'direction': d,
                    'cur': cur,
                    'lo': lo,
                    'hi': hi,
                    'sl': sl,
                    'tp1': tp1,
                    'rr': round(rr, 2),
                    'score': score,
                    'regime': regime,
                    'age_h': round(age_h, 1),
                    'sig_key': sig_key
                })
                alerted[sig_key] = now
        except Exception as e:
            pass

    if not alerts:
        pass  # [静默]
        alerted = {k: v for k, v in alerted.items() if now - v < 14400}
        save_state({'alerted': alerted})
        return

    # ── 推送 + 自动开单 ───────────────────────────────────────────
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    try:
        from push_hub import _jarvis as _pj
    except Exception:
        _pj = None

    # 加载 auto_execute_gate
    try:
        from auto_execute_gate import auto_execute as _auto_execute
        _auto_exec_available = True
    except Exception as _ae:
        _auto_exec_available = False
        _auto_exec_err = str(_ae)

    for a in alerts:
        dir_emoji = '🔴' if a['direction'] == 'SHORT' else '🟢'
        source    = a.get('source', '')
        score     = float(a.get('score', 0))
        is_vip    = (source == 'VIP_STRATEGY')
        is_auto   = is_vip or score >= 138

        # ── 自动开单（VIP策略 或 高分梵天信号）──────────────────
        exec_result = None
        if is_auto and _auto_exec_available:
            # 构建完整信号字典传入 auto_execute
            full_signal = {
                'symbol':     a['symbol'],
                'direction':  a['direction'],
                'score':      score,
                'regime':     a['regime'],
                'entry_lo':   a['lo'],
                'entry_hi':   a['hi'],
                'stop_loss':  a['sl'],
                'tp1':        a['tp1'],
                'tp2':        a.get('tp2', a['tp1']),
                'pos_pct':    a.get('pos_pct', 5),
                'leverage':   a.get('leverage', 3),
                'source':     source,
                'note':       a.get('note', ''),
            }
            try:
                exec_result = _auto_execute(full_signal, dry_run=False)
            except Exception as _ee:
                exec_result = {'executed': False, 'reason': str(_ee)}

        # ── 推送消息 ──────────────────────────────────────────────
        if exec_result and exec_result.get('executed'):
            order = exec_result.get('order', {})
            orders_list = order.get('orders', [])
            entry_o = next((x for x in orders_list if x.get('type') == 'LIMIT'), {})
            qty    = entry_o.get('qty', '?')
            e_px   = entry_o.get('price', a['lo'])
            sl_px  = next((x['price'] for x in orders_list if 'STOP' in x.get('type','')), a['sl'])
            tp_px  = next((x['price'] for x in orders_list if 'PROFIT' in x.get('type','')), a['tp1'])
            msg = (
                f"✅ 武曲自动开单成功 | {a['symbol']} {dir_emoji}{a['direction']}\n"
                f"入场=${e_px:.4g} | 数量={qty} | SL=${sl_px:.4g} | TP1=${tp_px:.4g}\n"
                f"体制={a['regime']} | score={score:.0f} | RR={a['rr']}\n"
                f"现价=${a['cur']:.4g} | 来源={'⚜️VIP' if is_vip else '梵天'}"
            )
        elif exec_result and not exec_result.get('executed'):
            reason = exec_result.get('reason', '未知')
            msg = (
                f"⚠️ 武曲门控拦截 | {a['symbol']} {dir_emoji}{a['direction']}\n"
                f"原因: {reason}\n"
                f"现价=${a['cur']:.4g} | 区间=${a['lo']:.4g}~${a['hi']:.4g}\n"
                f"SL=${a['sl']:.4g} | TP1=${a['tp1']:.4g} | RR={a['rr']}\n"
                f"如需手动执行请回复「执行」"
            )
        else:
            # 非自动信号 → 推送通知等待人工
            msg = (
                f"⚡ 入场区触发 | {a['symbol']} {dir_emoji}{a['direction']}\n"
                f"体制={a['regime']} | score={score:.0f} | RR={a['rr']}\n"
                f"现价=${a['cur']:.4g} | 区间=${a['lo']:.4g}~${a['hi']:.4g}\n"
                f"SL=${a['sl']:.4g} | TP1=${a['tp1']:.4g}\n"
                f"已等待 {a['age_h']}h | 回复「执行」确认下单"
            )

        if _pj:
            _pj(msg, dedup_ttl=14400)
        else:
            print(msg)

    pass  # [静默]
    save_state({'alerted': alerted})

if __name__ == '__main__':
    main()
