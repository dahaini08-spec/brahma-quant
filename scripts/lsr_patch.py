#!/usr/bin/env python3
"""[封印修复④ 2026-10-05 苏摩111] LSR实时拉取并更新brahma_state"""
import urllib.request, json, pathlib, time

for sym in ['BTCUSDT', 'ETHUSDT']:
    try:
        r = {}
        sl = sym.lower().replace('usdt', '')
        for ep, k in [
            (f'https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={sym}&period=5m&limit=1', 'pos'),
            (f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={sym}&period=5m&limit=1', 'global'),
        ]:
            try:
                d = json.loads(urllib.request.urlopen(ep, timeout=5).read())
                if d: r[k] = d[-1]
            except Exception as e2:
                print(f'[lsr_patch] {sym} {k}: {e2}')

        p = pathlib.Path(__file__).parent.parent / 'data' / f'brahma_state_{sl}.json'
        if p.exists() and r:
            st = json.loads(p.read_text())
            if 'global' in r:
                la = float(r['global'].get('longAccount', 0))
                sa = float(r['global'].get('shortAccount', 0))
                st['lsr_retail'] = round(la / (la + sa) * 100, 1) if la < 5 and sa > 0 else la
            if 'pos' in r:
                la = float(r['pos'].get('longAccount', 0))
                sa = float(r['pos'].get('shortAccount', 0))
                st['lsr_big'] = round(la / (la + sa) * 100, 1) if la < 5 and sa > 0 else la
            st['_lsr_updated'] = time.time()
            p.write_text(json.dumps(st, ensure_ascii=False))
            print(f'[lsr_patch] {sym}: big={st["lsr_big"]:.1f}% retail={st["lsr_retail"]:.1f}%')
    except Exception as e:
        print(f'[lsr_patch] {sym} ERROR: {e}')
