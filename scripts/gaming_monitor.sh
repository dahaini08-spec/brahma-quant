#!/bin/bash
# 游戏板块补涨监控 - BIGTIME/IMX/GMT
# 体制切换 或 梵天score提升时推送

cd /root/.openclaw/workspace/trading-system

OUT=$(python3 << 'PYEOF'
import subprocess, json, requests

def analyze(sym):
    r = subprocess.run(['python3', 'brahma_analyze.py', sym, '--json'],
                      capture_output=True, text=True, timeout=30)
    try:
        return json.loads(r.stdout.strip())
    except:
        return {}

def price(sym):
    r = requests.get(f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}", timeout=5)
    return float(r.json()['price'])

targets = {'BIGTIMEUSDT': {'watch_regime': 'BEAR_RECOVERY', 'watch_dir': 'LONG'},
           'IMXUSDT':     {'watch_regime': ['BEAR_RECOVERY','BEAR_EARLY'], 'watch_dir': 'LONG'},
           'GMTUSDT':     {'watch_regime': ['BEAR_RECOVERY','BEAR_EARLY'], 'watch_dir': 'LONG'}}

alerts = []
status_lines = []

for sym, cfg in targets.items():
    d = analyze(sym)
    regime = d.get('regime', '?')
    score = d.get('score', 0)
    signal_dir = d.get('signal_dir', '?')
    grade = d.get('grade', 0)
    cur_price = price(sym)

    watch_regimes = cfg['watch_regime'] if isinstance(cfg['watch_regime'], list) else [cfg['watch_regime']]
    
    # 触发条件：体制正确 且 方向=LONG
    regime_ok = regime in watch_regimes
    dir_ok = signal_dir == 'LONG'
    score_ok = score >= 80
    
    status = f"{sym}: regime={regime} score={score:.0f} dir={signal_dir} price={cur_price:.5f}"
    status_lines.append(status)
    
    if regime_ok and dir_ok:
        if score_ok:
            alerts.append(f"🚨 SIGNAL: {sym} {regime} LONG score={score:.0f} price={cur_price:.5f}")
        else:
            alerts.append(f"⚡ WATCH: {sym} {regime} LONG dir已对齐 score={score:.0f}(待≥80)")

if alerts:
    for a in alerts:
        print(a)
else:
    print("NO_ALERT")
    for s in status_lines:
        print(s)
PYEOF
)

echo "$OUT"
