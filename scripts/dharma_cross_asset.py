#!/usr/bin/env python3
"""dharma_cross_asset.py — 跨标的验证：用BTC regime代理跑山寨币回测
2026-09-10 苏摩111批准

方案：
  1. 下载SOL/BNB/ADA/XRP/DOGE的1h+15m K线（从Binance fapi）
  2. 用BTC regime labels作为代理regime
  3. ms=110 + RR=2.0 + maker成本(0.0002+0.0003)
  4. 标准：≥3/5标的 WR≥52% = alpha普适

输出：data/dharma_cross_asset_result.json
"""
import gzip, json, math, time, sys, os, requests
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
DATA = BASE / "data" / "historical"
OUT  = BASE / "data"
FAPI = "https://fapi.binance.com"

# ── 工具函数 ──
def ts_ms(date_str):
    return int(datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()*1000)

def download_klines(symbol, interval, start_ms, end_ms=None):
    """分页下载K线"""
    if end_ms is None:
        end_ms = int(time.time()*1000)
    all_bars = []
    cur = start_ms
    while cur < end_ms:
        url = f"{FAPI}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": 1500}
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"    ⚠️ {symbol} {interval} download error: {e}")
            time.sleep(0.5)
            continue
        if not data:
            break
        for k in data:
            all_bars.append({"ts": k[0], "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[5])})
        cur = data[-1][0] + 1
        time.sleep(0.05)
        if len(data) < 1500:
            break
    return sorted(all_bars, key=lambda x: x['ts'])

def save_klines(symbol, tf, bars):
    fname = DATA / f"{symbol}_{tf}.jsonl.gz"
    with gzip.open(fname, 'wt') as f:
        for b in bars:
            f.write(json.dumps(b) + "\n")
    return len(bars)

def load_klines(symbol, tf):
    fname = DATA / f"{symbol}_{tf}.jsonl.gz"
    if not fname.exists(): return []
    with gzip.open(fname, 'rt') as f:
        return sorted([json.loads(l) for l in f if l.strip()], key=lambda x: x['ts'])

def load_regime_labels(symbol):
    fname = DATA / f"{symbol}_regime_labels.jsonl.gz"
    if not fname.exists(): return {}
    with gzip.open(fname, 'rt') as f:
        labels = [json.loads(l) for l in f if l.strip()]
    return {l['ts']: l['regime'] for l in labels}

def calc_atr(bars, period=14):
    atrs = [0.0]*len(bars)
    for i in range(period, len(bars)):
        tr = max(bars[i]['h']-bars[i]['l'], abs(bars[i]['h']-bars[i-1]['c']), abs(bars[i]['l']-bars[i-1]['c']))
        if atrs[i-1] == 0:
            atrs[i] = sum(max(bars[j]['h']-bars[j]['l'], abs(bars[j]['h']-bars[j-1]['c']), abs(bars[j]['l']-bars[j-1]['c'])) for j in range(i-period+1, i+1))/period
        else:
            atrs[i] = (atrs[i-1]*(period-1)+tr)/period
    return atrs

def calc_rsi(bars, period=14):
    rsis = [0.0]*len(bars)
    for i in range(period, len(bars)):
        gains = [max(bars[j]['c']-bars[j-1]['c'], 0) for j in range(i-period+1, i+1)]
        losses = [max(bars[j-1]['c']-bars[j]['c'], 0) for j in range(i-period+1, i+1)]
        avg_g = sum(gains)/period
        avg_l = sum(losses)/period
        rsis[i] = 100 - 100/(1+avg_g/avg_l) if avg_l > 0 else 100
    return rsis

def detect_fvg(bars, idx):
    if idx < 3: return None
    b1, b2, b3 = bars[idx-3], bars[idx-2], bars[idx-1]
    if b3['l'] > b1['h']:
        return {"type": "BULL", "low": b1['h'], "high": b3['l'], "mid": (b1['h']+b3['l'])/2}
    if b3['h'] < b1['l']:
        return {"type": "BEAR", "low": b3['h'], "high": b1['l'], "mid": (b3['h']+b1['l'])/2}
    return None

def detect_ob(bars, idx, lookback=20, max_age=50):
    for i in range(max(2, idx-lookback), idx):
        body = abs(bars[i]['c']-bars[i]['o'])
        rng = bars[i]['h']-bars[i]['l']
        if body > rng*0.6:
            ob_top = max(bars[i]['h'], bars[i]['o']) if bars[i]['c']>bars[i]['o'] else bars[i]['h']
            ob_bot = min(bars[i]['l'], bars[i]['o']) if bars[i]['c']>bars[i]['o'] else bars[i]['l']
            age = idx - i
            if age < max_age:
                for j in range(i+1, idx):
                    if bars[j]['l'] < ob_bot or bars[j]['h'] > ob_top:
                        return None
                return {"top": ob_top, "bot": ob_bot, "age": age}
    return None

def detect_resonance(bars, idx, fvg, ob, liq, atr):
    score = 0
    if fvg: score += 1
    if ob: score += 1
    if liq and len(liq) > 0: score += 1
    return {"score": score}

# ── 评分器（与dharma_direction_ab.py一致） ──
def score_slim(regime, direction, rsi, res, bb_w):
    s = 50.0
    bonus = {('BULL_TREND','LONG'):30,('BULL_TREND','SHORT'):-20,
        ('BEAR_TREND','SHORT'):30,('BEAR_TREND','LONG'):-20,
        ('BULL_EARLY','LONG'):25,('BULL_EARLY','SHORT'):-15,
        ('BEAR_RECOVERY','LONG'):15,('BEAR_RECOVERY','SHORT'):-25,
        ('CHOP_MID','LONG'):0,('CHOP_MID','SHORT'):0}
    s += bonus.get((regime,direction),0)
    if direction=='LONG':
        if rsi and rsi<30: s+=15
        elif rsi and rsi>70: s-=15
    else:
        if rsi and rsi>70: s+=15
        elif rsi and rsi<30: s-=15
    if bb_w<1.0: s+=12
    elif bb_w>5.0: s-=8
    if res['score']>=3: s+=10
    elif res['score']==2: s+=5
    elif res['score']==0: s-=3
    return max(0, min(150, s))

def sim_exit(bars, idx, d, ep, atr, tf, rr=2.0, cost=0.0002, slip=0.0003):
    sl_pct = 0.02
    hold = {'15m':48, '1h':24}.get(tf, 24)
    min_sl = 1.5*atr if atr else ep*0.01
    sl_d = max(ep*sl_pct, min_sl)
    if d=='LONG':
        sl = ep - sl_d
        tp = ep + sl_d * rr
    else:
        sl = ep + sl_d
        tp = ep - sl_d * rr
    for i in range(idx+1, min(idx+hold+1, len(bars))):
        b = bars[i]
        if d=='LONG':
            if b['l'] <= sl:
                g = (sl-ep)/ep
                return {'is_win': g>0, 'pnl_net': (g-cost-slip)*100, 'exit': 'SL'}
            if b['h'] >= tp:
                g = (tp-ep)/ep
                return {'is_win': True, 'pnl_net': (g-cost-slip)*100, 'exit': 'TP'}
        else:
            if b['h'] >= sl:
                g = (ep-sl)/ep
                return {'is_win': g>0, 'pnl_net': (g-cost-slip)*100, 'exit': 'SL'}
            if b['l'] <= tp:
                g = (ep-tp)/ep
                return {'is_win': True, 'pnl_net': (g-cost-slip)*100, 'exit': 'TP'}
    c = bars[min(idx+hold, len(bars)-1)]['c']
    g = ((c-ep)/ep) if d=='LONG' else ((ep-c)/ep)
    return {'is_win': g>0, 'pnl_net': (g-cost-slip)*100, 'exit': 'TIME'}

def calc_ic(sigs):
    if len(sigs) < 10: return 0.0
    s = [x['score'] for x in sigs]
    o = [1.0 if x['is_win'] else 0.0 for x in sigs]
    n = len(s)
    ms = sum(s)/n; mo = sum(o)/n
    cov = sum((s[i]-ms)*(o[i]-mo) for i in range(n))/n
    ss = math.sqrt(sum((x-ms)**2 for x in s)/n)
    so = math.sqrt(sum((x-mo)**2 for x in o)/n)
    return round(cov/(ss*so), 4) if ss and so else 0.0

def run_single(symbol, bars, regime_map, min_score=110, rr=2.0, cost=0.0002, slip=0.0003):
    """对单个标的运行回测，返回统计"""
    STRONG = {('BULL_TREND','LONG'), ('BEAR_TREND','SHORT'), ('BULL_EARLY','LONG'), ('BEAR_RECOVERY','LONG')}
    all_sigs = []
    for tf in ['15m', '1h']:
        tf_bars = bars[tf]
        if len(tf_bars) < 200: continue
        atrs = calc_atr(tf_bars, 14)
        rsis = calc_rsi(tf_bars, 14)
        min_gap = {'15m': 4*15*60*1000, '1h': 4*3600*1000}.get(tf, 4*3600*1000)
        last_ts = 0
        for i in range(100, len(tf_bars)-50):
            b = tf_bars[i]
            ts = b['ts']
            if ts - last_ts < min_gap: continue
            # 用BTC regime作为代理
            ts_4h = (ts // (4*3600*1000)) * (4*3600*1000) - 4*3600*1000
            regime = regime_map.get(ts_4h)
            if not regime:
                for off in range(-3, 4):
                    regime = regime_map.get(ts_4h + off*4*3600*1000)
                    if regime: break
            if not regime: continue
            atr = atrs[i]; rsi = rsis[i]
            if not atr or not rsi: continue
            # BB width
            if i >= 20:
                cl = [tf_bars[j]['c'] for j in range(i-20, i)]
                mc = sum(cl)/20
                sc = math.sqrt(sum((c-mc)**2 for c in cl)/20)
                bb_w = sc*2/mc*100 if mc else 0
            else: bb_w = 0
            fvg = detect_fvg(tf_bars, i)
            ob = detect_ob(tf_bars, i)
            res = detect_resonance(tf_bars, i, fvg, ob, None, atr)
            for d in ['LONG', 'SHORT']:
                if (regime, d) not in STRONG: continue
                sc_val = score_slim(regime, d, rsi, res, bb_w)
                if sc_val < min_score: continue
                ni = i + 1
                if ni >= len(tf_bars): continue
                ep = tf_bars[ni]['o']
                er = sim_exit(tf_bars, ni, d, ep, atr, tf, rr=rr, cost=cost, slip=slip)
                all_sigs.append({'ts': ts, 'score': round(sc_val, 1), 'direction': d, 'regime': regime, 'tf': tf, **er})
                last_ts = ts
    n = len(all_sigs)
    if n == 0:
        return {'symbol': symbol, 'n': 0, 'wr': 0, 'ic': 0, 'pnl_net': 0, 'sigs': []}
    wr = sum(1 for s in all_sigs if s['is_win']) / n * 100
    ic = calc_ic(all_sigs)
    pnl = sum(s['pnl_net'] for s in all_sigs) / n
    return {'symbol': symbol, 'n': n, 'wr': round(wr, 1), 'ic': ic, 'pnl_net': round(pnl, 3), 'sigs': all_sigs}

def main():
    t0 = time.time()
    SYMBOLS = ['SOLUSDT', 'BNBUSDT', 'ADAUSDT', 'XRPUSDT', 'DOGEUSDT']
    # 先确保数据存在
    start_ms = ts_ms('2021-01-01')  # 从2021年开始（3.5年数据，够统计）
    for sym in SYMBOLS:
        for tf in ['15m', '1h']:
            fname = DATA / f"{sym}_{tf}.jsonl.gz"
            if fname.exists():
                print(f"  ✅ {sym} {tf} 已存在")
                continue
            print(f"  ⬇️ 下载 {sym} {tf}...")
            bars = download_klines(sym, tf, start_ms)
            if bars:
                cnt = save_klines(sym, tf, bars)
                print(f"     保存 {cnt} 根K线")
            else:
                print(f"     ❌ 无数据")

    # 加载BTC regime labels
    btc_regime = load_regime_labels('BTCUSDT')
    print(f"\n📊 BTC regime labels: {len(btc_regime)} 个")

    # 加载所有标的数据
    all_bars = {}
    for sym in SYMBOLS:
        all_bars[sym] = {}
        for tf in ['15m', '1h']:
            bars = load_klines(sym, tf)
            if bars:
                all_bars[sym][tf] = bars
                print(f"  {sym} {tf}: {len(bars)} 根")
            else:
                all_bars[sym][tf] = []
                print(f"  {sym} {tf}: ❌ 无数据")

    # 运行跨标的回测
    print("\n" + "="*70)
    print("🔥 跨标的验证: ms=110 + RR=2.0 + maker成本")
    print("="*70)

    results = {}
    pass_count = 0
    for sym in SYMBOLS:
        if not all_bars[sym].get('15m') and not all_bars[sym].get('1h'):
            print(f"\n  ❌ {sym}: 无K线数据，跳过")
            results[sym] = {'symbol': sym, 'n': 0, 'wr': 0, 'ic': 0, 'pnl_net': 0}
            continue
        r = run_single(sym, all_bars[sym], btc_regime, min_score=110, rr=2.0, cost=0.0002, slip=0.0003)
        results[sym] = r
        if r['n'] > 0:
            flag = '✅' if r['wr'] >= 52 else ('⚠️' if r['wr'] >= 50 else '❌')
            ev_flag = '✅' if r['pnl_net'] > 0 else '❌'
            print(f"\n  {flag} {sym}: n={r['n']} WR={r['wr']:.1f}% IC={r['ic']:+.4f} net EV={r['pnl_net']:+.3f}% {ev_flag}")
            if r['wr'] >= 52:
                pass_count += 1
        else:
            print(f"\n  ❌ {sym}: 无信号")

    # 汇总
    print("\n" + "="*70)
    print("📊 跨标的汇总")
    print("="*70)
    print(f"\n{'标的':<12} {'n':>6} {'WR':>6} {'IC':>8} {'netEV':>8} {'通过':>4}")
    print("-"*55)
    for sym in SYMBOLS:
        r = results[sym]
        if r['n'] > 0:
            flag = '✅' if r['wr'] >= 52 else '❌'
            print(f"{sym:<12} {r['n']:>6} {r['wr']:>5.1f}% {r['ic']:>+8.4f} {r['pnl_net']:>+7.3f}% {flag:>4}")
        else:
            print(f"{sym:<12} {0:>6} {'N/A':>6} {'N/A':>8} {'N/A':>8} {'❌':>4}")

    total_valid = sum(1 for r in results.values() if r['n'] > 0)
    print(f"\n  通过标的数: {pass_count}/{total_valid} 有效标的")
    print(f"  标准: ≥3/5通过 = alpha普适")
    if pass_count >= 3:
        print(f"  🟢 结论: alpha普适！跨标的验证通过")
    elif pass_count >= 1:
        print(f"  ⚠️ 结论: alpha部分普适，需进一步分析")
    else:
        print(f"  🔴 结论: alpha可能是BTC/ETH局部现象")

    elapsed = time.time() - t0
    print(f"\n耗时: {elapsed:.1f}s")

    # 保存结果
    summary = {
        'ts': datetime.utcnow().isoformat() + 'Z',
        'config': {'ms': 110, 'rr': 2.0, 'cost': 0.0002, 'slip': 0.0003, 'regime_proxy': 'BTCUSDT'},
        'pass_count': pass_count,
        'total_valid': total_valid,
        'results': {k: {kk: vv for kk, vv in v.items() if kk != 'sigs'} for k, v in results.items()}
    }
    with open(OUT / 'dharma_cross_asset_result.json', 'w') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"结果已保存: data/dharma_cross_asset_result.json")

if __name__ == '__main__':
    main()
