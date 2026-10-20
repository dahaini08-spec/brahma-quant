#!/usr/bin/env python3
"""
brahma_42_monitor.py — 42标的轻量监控引擎
[封印 2026-10-20 苏摩111]

设计原则：
  1. 轻量优先：纯数据扫描，零AI调用
  2. 分层触发：轻扫→方仓→深度AI（逐层升级）
  3. 内存安全：单轮峰值<50MB，不加载大模型
  4. 稳定性：任何标的失败不影响其他标的

三层架构：
  L1 快扫(15min): 42标的 price/taker/oi/lsr → 评分矩阵
  L2 方仓(30min): score≥35标的 → 方仓历史匹配 → 置信度
  L3 深度(按需):  score≥50+方仓≥65% → 深度AI分析（限2标的/次）
"""
import sys, json, time, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

# 42标的分组（A核心/B中型/C小币）
SYMBOL_POOLS = {
    'A': ['BTC','ETH','SOL','BNB','XRP','ADA','AVAX','DOGE','DOT','LINK'],
    'B': ['UNI','AAVE','ATOM','NEAR','FIL','ARB','OP','INJ','TIA','PYTH',
          'SUI','APT','SEI','WLD','BLUR','MANTA','STRK','ALT','PIXEL','PORTAL'],
    'C': ['PEPE','BONK','WIF','FLOKI','ORDI','BOME','PEOPLE','MEME','NEIRO',
          'BLUR','PIXEL','MANTA'],
}
ALL_42 = list(dict.fromkeys([s for g in SYMBOL_POOLS.values() for s in g]))[:42]

STATE_FILE = BASE / 'data' / 'monitor_42_state.json'
RESULT_FILE = BASE / 'data' / 'monitor_42_latest.json'


def _fetch(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def l1_quick_scan(sym: str) -> dict:
    """L1: 轻量快扫 — 纯数据，零AI，<0.5s/标的"""
    s = sym.upper() + 'USDT'
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {
            'price': ex.submit(_fetch, f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={s}'),
            'stats': ex.submit(_fetch, f'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={s}'),
            'taker': ex.submit(_fetch, f'https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={s}&period=5m&limit=3'),
            'lsr':   ex.submit(_fetch, f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={s}&period=5m&limit=1'),
            'oi_h':  ex.submit(_fetch, f'https://fapi.binance.com/futures/data/openInterestHist?symbol={s}&period=1h&limit=3'),
        }
        raw = {k: f.result(timeout=8) for k, f in futs.items()}

    price = raw['price']
    if not price or not price.get('price'):
        return {'sym': sym, 'error': 'no_data', 'score': 0, 'signal': 'SKIP'}

    px   = float(price['price'])
    chg  = float(raw['stats'].get('priceChangePercent', 0)) if raw['stats'] else 0
    vol  = float(raw['stats'].get('quoteVolume', 0)) / 1e6 if raw['stats'] else 0

    # Taker
    tak_list = raw['taker'] if isinstance(raw['taker'], list) else []
    tak_now  = round(float(tak_list[-1].get('buySellRatio', 1)), 3) if tak_list else 1.0
    tak_avg  = round(sum(float(x.get('buySellRatio',1)) for x in tak_list)/len(tak_list), 3) if tak_list else 1.0
    tak_trend = round(float(tak_list[-1].get('buySellRatio',1)) - float(tak_list[0].get('buySellRatio',1)), 3) if len(tak_list)>=2 else 0

    # LSR
    lsr_list = raw['lsr'] if isinstance(raw['lsr'], list) else []
    lsr  = round(float(lsr_list[-1]['longShortRatio']), 3) if lsr_list else 1.0

    # OI方向
    oi_h = raw['oi_h'] if isinstance(raw['oi_h'], list) else []
    oi_dir = 'FLAT'; oi_rate = 0.0
    if len(oi_h) >= 2:
        p = float(oi_h[-2].get('sumOpenInterest', 0))
        c = float(oi_h[-1].get('sumOpenInterest', 0))
        if p > 0:
            oi_rate = round((c-p)/p*100, 3)
            oi_dir = 'BUILD' if oi_rate > 0.3 else ('UNWIND' if oi_rate < -0.3 else 'FLAT')

    # 评分（纯规则，零AI）
    score = 0; reasons = []

    # RSI简化（用24H涨跌代替，快速估算动量方向）
    if chg <= -5:  score += 20; reasons.append(f'超卖chg{chg:.1f}%')
    elif chg <= -3: score += 12; reasons.append(f'回调chg{chg:.1f}%')
    elif chg >= 5:  score += 10; reasons.append(f'涨势chg{chg:.1f}%')

    if tak_now >= 1.5 and tak_trend > 0:
        score += 25; reasons.append(f'Taker强买{tak_now}↑')
    elif tak_now >= 1.2:
        score += 12; reasons.append(f'Taker偏买{tak_now}')
    elif tak_now <= 0.6:
        score -= 10; reasons.append(f'Taker卖{tak_now}')

    if oi_dir == 'BUILD' and tak_now >= 1.0:
        score += 20; reasons.append('OI+Taker共振')
    elif oi_dir == 'BUILD':
        score += 8;  reasons.append(f'OI BUILD{oi_rate:+.2f}%')
    elif oi_dir == 'UNWIND':
        score -= 8;  reasons.append('OI减仓')

    if lsr >= 3.0:
        score -= 15; reasons.append(f'LSR猎杀{lsr}')
    elif lsr >= 2.5:
        score -= 8;  reasons.append(f'LSR偏高{lsr}')
    elif lsr <= 0.8:
        score += 12; reasons.append(f'LSR偏空{lsr}')

    # 信号分级
    signal = 'S' if score >= 50 else ('A' if score >= 30 else ('B' if score >= 10 else 'WAIT'))

    return {
        'sym': sym, 'px': px, 'chg': round(chg,2), 'vol_m': round(vol,1),
        'tak': tak_now, 'tak_avg': tak_avg, 'tak_trend': tak_trend,
        'lsr': lsr, 'oi_dir': oi_dir, 'oi_rate': oi_rate,
        'score': score, 'signal': signal, 'reasons': reasons[:3],
        'ts': time.time(),
    }


def l2_fangcang_match(sym: str, score: int) -> dict:
    """L2: 方仓历史匹配 — 向量检索，零AI"""
    if score < 35:
        return {'fc_n': 0, 'fc_long': 0.5, 'fc_conf': 'LOW'}
    try:
        import fangcang_engine as fe
        fe._CACHE_LOADED = False; fe._FANGCANG_CACHE = []
        from fangcang_engine import fangcang_context_match as fcm
        # 用score估算bbw/rsi（轻量代理）
        bbw = max(0.5, 2.0 - score/50.0)
        rsi = 45 if score > 30 else 35
        fc = fcm(sym, bbw, rsi, 'CHOP_MID', 'LONG')
        n = fc.get('n_similar', 0)
        lp = fc.get('long_pct', 0.5)
        conf = 'HIGH' if n >= 15 and (lp >= 0.7 or lp <= 0.3) else ('MED' if n >= 8 else 'LOW')
        return {'fc_n': n, 'fc_long': round(lp, 2), 'fc_conf': conf}
    except Exception:
        return {'fc_n': 0, 'fc_long': 0.5, 'fc_conf': 'LOW'}


def run_l1_all(pools: list = None) -> dict:
    """并发跑L1快扫（所有标的）"""
    syms = []
    if pools:
        for p in pools:
            syms.extend(SYMBOL_POOLS.get(p, []))
    else:
        syms = ALL_42

    results = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(l1_quick_scan, sym): sym for sym in syms}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                r = fut.result(timeout=12)
                if r and not r.get('error'):
                    results[sym] = r
            except Exception:
                pass

    elapsed = round(time.time()-t0, 1)
    return {'_ts': time.time(), '_elapsed': elapsed, '_count': len(results), **results}


def run_monitor(mode: str = 'quick') -> str:
    """
    主入口
    mode='quick': 仅L1快扫（无AI，15分钟cron用）
    mode='deep':  L1+L2方仓（30分钟cron用）
    mode='signal': 只推送S/A级（有信号时推送）
    """
    now_str = time.strftime('%H:%M UTC')

    # L1全扫
    results = run_l1_all()
    count = results.get('_count', 0)
    elapsed = results.get('_elapsed', 0)

    # L2方仓补充（deep模式）
    if mode in ('deep', 'signal'):
        for sym, r in list(results.items()):
            if sym.startswith('_'): continue
            if r.get('score', 0) >= 35:
                fc = l2_fangcang_match(sym, r['score'])
                r.update(fc)

    # 保存
    json.dump(results, open(RESULT_FILE, 'w'), ensure_ascii=False)

    # 分级汇总
    s_list = [(s,v) for s,v in results.items() if not s.startswith('_') and v.get('signal')=='S']
    a_list = [(s,v) for s,v in results.items() if not s.startswith('_') and v.get('signal')=='A']
    b_list = [(s,v) for s,v in results.items() if not s.startswith('_') and v.get('signal')=='B']

    s_list.sort(key=lambda x: -x[1].get('score',0))
    a_list.sort(key=lambda x: -x[1].get('score',0))

    lines = [f"🏛️ 梵天42标的监控 · {now_str}",
             f"L1快扫: {count}/42  耗时{elapsed}s  零AI调用"]

    if s_list:
        lines.append(f"\n🔥 S级({len(s_list)}个):")
        for sym, r in s_list[:5]:
            fc_str = f" 方仓{r['fc_long']:.0%}(n={r['fc_n']})" if r.get('fc_n',0)>0 else ''
            lines.append(f"  {sym:<8} ${r['px']:.4f}  score={r['score']}{fc_str}")
            lines.append(f"           {'/'.join(r['reasons'][:2])}")

    if a_list:
        lines.append(f"\n✅ A级({len(a_list)}个):")
        for sym, r in a_list[:6]:
            lines.append(f"  {sym:<8} chg={r['chg']:+.1f}%  Taker={r['tak']}  OI={r['oi_dir']}  score={r['score']}")

    if b_list:
        lines.append(f"\n📊 B级({len(b_list)}个): {' '.join(s for s,_ in b_list[:8])}")

    lines.append(f"\n💡 S级→发标的名触发梵天深度分析")

    result_str = '\n'.join(lines)

    # 推送（有S/A级时）
    if (s_list or a_list) and mode == 'signal':
        try:
            sys.path.insert(0, str(BASE/'scripts'))
            from push_hub import push_jarvis
            push_jarvis(result_str, priority='P1',
                       dedup_key=f'monitor42_{int(time.time()//3600)}',
                       dedup_ttl=3600)
        except Exception as e:
            pass

    return result_str


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', default='quick', choices=['quick','deep','signal'])
    args = ap.parse_args()
    print(run_monitor(args.mode))
