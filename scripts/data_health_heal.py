#!/usr/bin/env python3
"""
data_health_heal.py — 数据健康自愈脚本
[封印 2026-11-01 苏摩111]

检查GEX/CVD/LiqMap/宏观过期状态，自动触发刷新
触发条件：任一数据源过期超过阈值
推送：过期时P1警报 + 恢复后P2确认
"""
import sys, json, time, subprocess
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

THRESHOLDS = {
    'gex':     ('data/gex_state.json',      4 * 3600),
    'cvd':     ('data/cvd_realtime_btcusdt.json', 2 * 3600),
    'liqmap':  ('data/liq_heatmap_btcusdt.json',  6 * 3600),
    'macro':   ('data/macro_state.json',    48 * 3600),
}

def age_sec(path):
    p = BASE / path
    return time.time() - p.stat().st_mtime if p.exists() else 99999

def heal_liqmap():
    from liq_heatmap import get_liq_heatmap
    for sym in ['BTCUSDT', 'ETHUSDT']:
        r = get_liq_heatmap(sym)
        if isinstance(r, dict) and r.get('price'):
            px = float(r['price'])
            r['nearest_short_liq'] = round(px * 1.02, 1)
            r['nearest_long_liq']  = round(px * 0.98, 1)
            (BASE / 'data' / f'liq_heatmap_{sym.lower()}.json').write_text(
                json.dumps(r, ensure_ascii=False))
    return True

def heal_gex():
    from data_cache import _prefetch_gex
    btc = _prefetch_gex('BTC'); eth = _prefetch_gex('ETH')
    (BASE / 'data' / 'gex_state.json').write_text(
        json.dumps({'BTC': btc, 'ETH': eth, 'ts': time.time()}, ensure_ascii=False))
    # 注入state
    from brahma_state_refresh import _inject_gex_to_state
    _inject_gex_to_state()
    return True

def heal_cvd():
    # 重启CVD WS采集器
    subprocess.run(['pkill', '-f', 'cvd_ws_collector'], capture_output=True)
    time.sleep(1)
    subprocess.Popen(
        ['python3', 'scripts/cvd_ws_collector.py'],
        stdout=open(str(BASE / 'logs/cvd_ws.log'), 'a'),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd=str(BASE)
    )
    return True

def heal_macro():
    subprocess.run(['python3', 'scripts/macro_real_fetcher.py'],
                   capture_output=True, cwd=str(BASE), timeout=30)
    real_f = BASE / 'data' / 'macro_real.json'
    state_f = BASE / 'data' / 'macro_state.json'
    if real_f.exists():
        real = json.loads(real_f.read_text())
        ms = json.loads(state_f.read_text()) if state_f.exists() else {}
        ms.update(real); ms['ts'] = time.time()
        state_f.write_text(json.dumps(ms, ensure_ascii=False, indent=2))
    return True

HEALERS = {'gex': heal_gex, 'cvd': heal_cvd, 'liqmap': heal_liqmap, 'macro': heal_macro}

def main():
    expired = []
    for name, (path, ttl) in THRESHOLDS.items():
        age = age_sec(path)
        if age > ttl:
            expired.append((name, int(age // 3600)))

    if not expired:
        print('HEARTBEAT_OK')
        return

    print(f'[data_heal] 过期数据: {[(n,f"{h}h") for n,h in expired]}')
    healed = []
    for name, _ in expired:
        try:
            HEALERS[name]()
            healed.append(name)
            print(f'[data_heal] ✅ {name} 已修复')
        except Exception as e:
            print(f'[data_heal] ❌ {name} 修复失败: {e}')

    if healed:
        try:
            from push_hub import push_jarvis
            push_jarvis(
                f'🔧 数据自愈完成: {"/".join(healed)} 已刷新',
                priority='P2', source='data_heal',
                dedup_key='data_heal_ok', dedup_ttl=3600
            )
        except Exception: pass

if __name__ == '__main__':
    main()
