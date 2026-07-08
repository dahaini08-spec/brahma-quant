#!/usr/bin/env python3
import requests, time
from datetime import datetime, timezone

def get_klines(sym, limit=1100):
    r = requests.get('https://fapi.binance.com/fapi/v1/klines',
        params={'symbol': sym, 'interval': '1h', 'limit': limit}, timeout=10)
    d = r.json()
    return d if isinstance(d, list) else []

def score_at(klines, idx):
    if idx < 30 or idx >= len(klines): return None
    c = [float(x[4]) for x in klines[:idx+1]]
    h = [float(x[2]) for x in klines[:idx+1]]
    l = [float(x[3]) for x in klines[:idx+1]]
    v = [float(x[5]) for x in klines[:idx+1]]
    px = c[-1]

    n7 = min(168, len(h))
    t7  = (max(h[-n7:]) - min(l[-n7:])) / max(min(l[-n7:]), 1e-9) * 100
    t24 = (max(h[-24:]) - min(l[-24:])) / max(min(l[-24:]), 1e-9) * 100
    t8  = (max(h[-8:])  - min(l[-8:]))  / max(min(l[-8:]),  1e-9) * 100

    diffs = [c[i+1]-c[i] for i in range(max(0,len(c)-16), len(c)-1)]
    gains = [d for d in diffs if d > 0]
    loss  = [-d for d in diffs if d < 0]
    ag = sum(gains)/len(diffs) if diffs else 0
    al = sum(loss)/len(diffs)  if diffs else 0
    rsi = 100 - 100/(1 + ag/al) if al > 0 else 99

    sh = 0
    for i in range(len(v)-2, max(0,len(v)-20), -1):
        if v[i] < v[i+1]: sh += 1
        else: break

    ema20 = sum(c[-20:])/20 if len(c)>=20 else sum(c)/len(c)
    trend = 'DWN' if px < ema20*0.995 else ('UP ' if px > ema20*1.005 else 'SID')

    sc = 0; rr = []
    if t7 < 15:  sc += 40; rr.append('T7<15pct(%d)' % int(t7))
    elif t7 < 30: sc += 20; rr.append('T7<30pct(%d)' % int(t7))
    elif t7 < 50: sc += 8;  rr.append('T7<50pct(%d)' % int(t7))

    if t24 < 5:  sc += 15; rr.append('T24<5pct(%.1f)' % t24)
    elif t24 < 10: sc += 8; rr.append('T24<10pct(%.1f)' % t24)

    if rsi < 25:  sc += 30; rr.append('RSI%.0f(深超卖)' % rsi)
    elif rsi < 35: sc += 20; rr.append('RSI%.0f(超卖)' % rsi)
    elif rsi < 45: sc += 10; rr.append('RSI%.0f(偏低)' % rsi)

    if sh >= 13:  sc += 25; rr.append('缩量%dH(铁证)' % sh)
    elif sh >= 8:  sc += 15; rr.append('缩量%dH' % sh)
    elif sh >= 5:  sc += 8;  rr.append('缩量%dH' % sh)

    if trend == 'DWN': sc += 10; rr.append('略跌')

    vol48 = sum(v[-48:])/48 * 24 / 1e6  # 估算24H成交额(M)
    return {'sc': sc, 't7': round(t7,1), 't24': round(t24,1), 'rsi': round(rsi,1),
            'sh': sh, 'trend': trend, 'rr': rr, 'px': px, 'vol24_m': round(vol48,1)}

TARGETS = [
    ('2026-06-26','AGLDUSDT',   75.1),
    ('2026-06-27','VELVETUSDT',129.8),
    ('2026-06-28','ACTUSDT',    40.3),
    ('2026-06-29','TACUSDT',   171.7),
    ('2026-07-01','TAIKOUSDT', 495.7),
    ('2026-07-02','BIRBUSDT',   65.8),
    ('2026-07-03','TLMUSDT',    55.1),
    ('2026-07-04','LABUSDT',   160.9),
    ('2026-07-05','VANRYUSDT',  58.1),
]

print('梵天设计院 · 暴涨猎手10天TOP涨幅榜 / 30天信号回溯')
print('分析时间: %s UTC' % datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M'))
print()

for pump_date, sym, pct in TARGETS:
    k = get_klines(sym)
    print('=' * 68)
    print('[%s] %s  +%.1f%%' % (pump_date, sym, pct))

    if not k or len(k) < 72:
        print('  数据不足(%d)' % len(k)); print(); continue

    pump_ms = int(datetime.strptime(pump_date,'%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()*1000)
    p_idx = len(k)-1
    for i,kl in enumerate(k):
        if int(kl[0]) >= pump_ms: p_idx = i; break

    # 候选池检查
    if p_idx >= 48:
        vol_avg24 = sum(float(k[i][7]) for i in range(p_idx-48, p_idx)) / 48 * 24
        in_pool = vol_avg24 >= 50_000_000
        pool_str = '✅ 候选池内' if in_pool else '❌ 候选池外(成交额不足5000万USDT)'
        print('  爆发前48H估算24H成交额: %.1fM  %s' % (vol_avg24/1e6, pool_str))

    # 爆发前最高评分
    best = 0; best_r = None
    for days in range(1, 49):
        idx = p_idx - days
        s = score_at(k, idx)
        if s and s['sc'] > best: best = s['sc']; best_r = s

    captured = best >= 75
    print('  爆发前48H最高评分: %d/100  应被预判: %s' % (best, 'YES ✅' if captured else 'NO ❌(系统盲区)'))
    if best_r:
        print('  最高分状态: T7=%.1f%% RSI=%.1f 缩量=%dH  原因: %s' % (
            best_r['t7'], best_r['rsi'], best_r['sh'], ', '.join(best_r['rr'][:3])))
    print()

    # 30天轨迹
    print('  %-6s %-5s %-4s  %-8s %-6s %-5s %-4s  关键信号' % ('日期','T-天','分数','T7D(%)','RSI','缩量H','趋势'))
    print('  ' + '-' * 65)
    for db in [30,25,20,15,12,10,7,5,4,3,2,1]:
        idx = p_idx - db*24
        s = score_at(k, idx)
        if not s: continue
        dt_s = datetime.fromtimestamp(int(k[idx][0])/1000, tz=timezone.utc).strftime('%m-%d')
        tag = '[🚨]' if s['sc'] >= 75 else ('[⚠ ]' if s['sc'] >= 50 else '    ')
        signals = ' | '.join(s['rr'][:3]) if s['rr'] else '-'
        print('  %s %s T-%2d  %3d   %7.1f%%  %5.1f  %4dH  %s  %s' % (
            tag, dt_s, db, s['sc'], s['t7'], s['rsi'], s['sh'], s['trend'], signals))
    print()
    time.sleep(0.3)

print('=== 报告完成 ===')
