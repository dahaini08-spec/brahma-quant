#!/usr/bin/env python3
"""
bbw_squeeze_monitor.py · BBW压缩预警 v1.0
设计院 2026-08-02 | 从agentTurn内嵌代码迁移为独立脚本
功能：检测BTC/ETH 15M BBW压缩（<0.55%），自动push_hub推送
正常时静默退出0
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import urllib.request, json, math
from pathlib import Path

BASE = Path(__file__).parent.parent

def get_bbw(sym):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=15m&limit=25"
    with urllib.request.urlopen(url, timeout=8) as r:
        kl = json.loads(r.read())
    c = [float(k[4]) for k in kl]
    m = sum(c[-20:]) / 20
    std = math.sqrt(sum((x - m) ** 2 for x in c[-20:]) / 20)
    return (4 * std / m) * 100, c[-1]

def main():
    alerts = []
    for sym in ["BTCUSDT", "ETHUSDT"]:
        try:
            bbw, price = get_bbw(sym)
            if bbw < 0.35:
                alerts.append(f"⚡ {sym} 15M BBW={bbw:.2f}%(<0.35%) 极度压缩蓄力 价格=${price:.2f}")
            elif bbw < 0.55:
                alerts.append(f"🔔 {sym} 15M BBW={bbw:.2f}%(<0.55%) 压缩蓄力 价格=${price:.2f}")
        except Exception as e:
            pass  # 静默，数据拉取失败不告警

    if not alerts:
        sys.exit(0)  # 静默

    msg = "【梵天BBW压缩预警】\n" + "\n".join(alerts) + "\n突破方向将决定下一步策略，注意放量确认"
    try:
        from scripts.push_hub import _jarvis
        _jarvis(msg, dedup_ttl=1800)  # 30min去重
    except Exception:
        # fallback: openclaw CLI
        import subprocess
        subprocess.run([
            'openclaw', 'message', 'send',
            '--channel', 'jarvis',
            '--to', '73295708:thread:019fd9dd-4b0f-71db-87fb-1e192ccb2291',
            '--message', msg
        ], capture_output=True)
    print(msg)

if __name__ == '__main__':
    main()
