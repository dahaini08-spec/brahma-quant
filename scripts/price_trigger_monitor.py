#!/usr/bin/env python3
"""
import sys as _sys, json as _json
from pathlib import Path as _Path
_cfg_files = [
    _Path(__file__).parent.parent / 'data' / 'price_trigger_config.json',
    _Path(__file__).parent.parent / 'data' / 'price_triggers.json',
]
_has_triggers = False
for _cf in _cfg_files:
    if _cf.exists():
        try:
            _triggers = _json.loads(_cf.read_text())
            if _triggers:
                _has_triggers = True
                break
        except Exception: as _audit_e:
            import sys as _sys; print(f"[WARN] scripts/price_trigger_monitor.py:18 silenced: {type(_audit_e).__name__}: {_audit_e}", file=_sys.stderr)
if not _has_triggers:
    print('HEARTBEAT_OK (no triggers configured)', file=_sys.stderr)
    _sys.exit(0)


price_trigger_monitor.py — 条件触发监控 [9.18苏摩111 Phase 5]
每5min检查关键条件，满足时自动触发分析+推送
"""
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import json, os, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

TRIGGER_FILE = os.path.join(ROOT, 'data', 'price_triggers.json')
STATE_FILE = os.path.join(ROOT, 'data', 'price_trigger_state.json')

def get_price(sym):
    try:
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}USDT'
        return float(json.loads(urllib.request.urlopen(url, timeout=5).read())['price'])
    except Exception:
        return 0

def load_triggers():
    if not os.path.exists(TRIGGER_FILE):
        return {}
    return json.load(open(TRIGGER_FILE))

def main():
    # ══ [P1 D-10决策生命周期 tick 2026-09-28 苏摩111] ══
    # 决策包时钟推进（5min周期）：WAIT_TRACKING→ARMED→TRIGGERED→INVALIDATED
    # 影子运行：只产出决策包与事件，不触任何执行（9.26 A/B分离铁律）
    try:
        from brahma_decision_lifecycle import tick_all as _dec_tick
        _r = _dec_tick(['BTCUSDT', 'ETHUSDT'])
        for _s in _r:
            for _a in _s.get('actions', []):
                print(f"[decision] {_s['symbol']} {_a.get('decision_id','')} {_a.get('action','')} {_a.get('reason','')[:60]}")
    except Exception as _e:
        print(f'[decision] tick失败: {_e}', file=sys.stderr)

    triggers = load_triggers()
    if not triggers:
        print('[trigger] 无触发条件配置')
        return
    
    state = {}
    if os.path.exists(STATE_FILE):
        state = json.load(open(STATE_FILE))
    
    for sym, cfg in triggers.items():
        price = get_price(sym)
        if price == 0:
            continue
        
        stop_wall = cfg.get('stop_wall', 0)
        support_pool = cfg.get('support_pool', 0)
        liquidation = cfg.get('liquidation', 0)
        
        triggered = []
        
        # 条件1: 价格到达止损墙±0.5%
        if stop_wall > 0 and abs(price - stop_wall) / stop_wall < 0.005:
            triggered.append(f'价格${price:,.0f}到达止损墙${stop_wall:,.0f}')
        
        # 条件2: 价格到达清算区±0.5%
        if liquidation > 0 and abs(price - liquidation) / liquidation < 0.005:
            triggered.append(f'价格${price:,.0f}到达清算区${liquidation:,.0f}')
        
        # 条件3: 价格到达支撑池±0.5%
        if support_pool > 0 and abs(price - support_pool) / support_pool < 0.005:
            triggered.append(f'价格${price:,.0f}到达支撑池${support_pool:,.0f}')
        
        # 去重：同条件30min内不重复触发
        _key = f'{sym}_last_trigger'
        _last_ts = state.get(_key, 0)
        if triggered and (time.time() - _last_ts) > 1800:
            state[_key] = time.time()
            print(f'[trigger] {sym} {" | ".join(triggered)}')
            # 触发分析
            _result = subprocess.run(
                ['python3', 'scripts/brahma_manual_analysis.py', '--symbols', sym, '--push'],
                cwd=str(ROOT), timeout=95, capture_output=True, text=True
            )
            if _result.returncode != 0:
                print(f'[ERROR] {sym} 分析失败 RC={_result.returncode}: {_result.stderr[:200]}')
            else:
                print(f'[OK] {sym} 分析完成')
        elif triggered:
            print(f'[trigger] {sym} 触发但30min内已触发过，跳过')
        else:
            print(f'[trigger] {sym} ${price:,.0f} 未触发任何条件')
    
    _tmp = pathlib.Path(str(STATE_FILE) + '.tmp')
    _tmp.write_text(json.dumps(state, ensure_ascii=False))
    _tmp.replace(STATE_FILE)

if __name__ == '__main__':
    main()
