#!/usr/bin/env python3
"""[9.27HAR-RV cron挂回 苏摩111] 独立刷新HAR-RV缓存，防分析链懒加载失活。
接入位置: brahma_crontab.txt (每4h, 错峰minute 11) + har_rv_engine.py缓存原子写。
BTC+ETH串行刷新（原子写已修并发根因，串行更稳），失败exit1供cron告警。
"""
import sys, os, time, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    from brahma_brain.har_rv_engine import get_har_rv
    ok = []
    for sym in ['BTCUSDT', 'ETHUSDT']:
        try:
            t0 = time.time()
            r = get_har_rv(sym)
            ok.append(f"{sym}: {time.time()-t0:.1f}s")
            print(f"[OK] {sym} har_rv refreshed ({time.time()-t0:.1f}s)")
        except Exception as e:
            print(f"[FAIL] {sym}: {e}", file=sys.stderr)
            traceback.print_exc()
    if len(ok) < 2:
        sys.exit(1)
    print("[DONE] har_rv_cache refreshed:", ", ".join(ok))

if __name__ == '__main__':
    main()
