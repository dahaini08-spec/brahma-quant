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
# 标的池（含XAU黄金/CL原油U本位合约）
SYMBOL_POOLS = {
    'A': ['BTC','ETH','SOL','BNB','XRP','ADA','AVAX','DOGE','DOT','LINK'],
    'B': ['UNI','AAVE','ATOM','NEAR','FIL','ARB','OP','INJ','TIA','PYTH',
          'SUI','APT','SEI','WLD','BLUR','MANTA','STRK','ALT','PIXEL','PORTAL'],
    'C': ['PEPE','BONK','WIF','FLOKI','ORDI','BOME','PEOPLE','MEME','NEIRO',
          'BLUR','PIXEL','MANTA'],
    'MACRO': ['XAU','CL'],  # 黄金+原油U本位合约
}
ALL_42 = list(dict.fromkeys([s for g in list(SYMBOL_POOLS.values()) for s in g]))[:44]

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

    # ── 预判型评分矩阵（找蓄力完成但未爆发的结构）──────────────────
    # 核心原则: 埋伏 = 价格还没动 但结构已经就绪
    score = 0; reasons = []; pre_signals = []

    # P1: BB蓄力信号（最强预判：收窄=即将爆发）
    # 用价格振幅估算BB状态（轻量代理，无需K线）
    hi24 = float(raw['stats'].get('highPrice', px)) if raw['stats'] else px
    lo24 = float(raw['stats'].get('lowPrice', px)) if raw['stats'] else px
    amplitude = (hi24 - lo24) / lo24 * 100 if lo24 > 0 else 0
    if amplitude < 1.5 and vol > 10:
        score += 30; pre_signals.append(f'低振幅{amplitude:.1f}%蓄力⚡')
    elif amplitude < 2.5:
        score += 15; pre_signals.append(f'振幅收窄{amplitude:.1f}%')

    # P2: OI BUILD + Taker未追（主力已建仓但散户未知）
    if oi_dir == 'BUILD' and 0.8 <= tak_now <= 1.3:
        score += 30; pre_signals.append(f'OI建仓{oi_rate:+.2f}%+Taker中性=主力埋伏中')
    elif oi_dir == 'BUILD' and tak_now > 1.3:
        score += 15; pre_signals.append(f'OI建仓+Taker{tak_now}已启动')
    elif oi_dir == 'BUILD':
        score += 10; pre_signals.append(f'OI BUILD{oi_rate:+.2f}%')

    # P3: LSR极值（猎杀前结构 = 触发在即）
    if lsr >= 3.0:
        score += 25; pre_signals.append(f'LSR={lsr}猎杀结构满溢⚡')
    elif lsr >= 2.5:
        score += 15; pre_signals.append(f'LSR={lsr}猎杀结构积累')
    elif lsr <= 0.7:
        score += 20; pre_signals.append(f'LSR={lsr}大户偏空=逼空结构')

    # P4: Taker方向翻转信号（从卖→买 = 主力开始推）
    if tak_trend > 0.4 and tak_now > 1.0:
        score += 20; pre_signals.append(f'Taker翻转{tak_trend:+.2f}↑买盘启动')
    elif tak_trend < -0.4 and tak_now < 0.8:
        score += 15; pre_signals.append(f'Taker翻转{tak_trend:+.2f}↓卖盘启动')

    # P5: 价格回调但OI未减（主力没跑 = 回调是机会）
    if chg <= -3 and oi_dir != 'UNWIND':
        score += 20; pre_signals.append(f'价格回调{chg:.1f}%但OI未减=埋伏位')
    elif chg <= -5:
        score += 10; pre_signals.append(f'超跌{chg:.1f}%')

    # 减分：滞后信号（已经涨了再说，不是预判）
    if chg >= 8 and oi_dir == 'BUILD':
        score -= 15; pre_signals.append(f'已涨{chg:.1f}%追高风险')
    if oi_dir == 'UNWIND':
        score -= 15; pre_signals.append('OI减仓=主力出场')

    reasons = pre_signals[:3]
    # 信号分级（预判型阈值更严格）
    signal = 'S' if score >= 55 else ('A' if score >= 35 else ('B' if score >= 15 else 'WAIT'))

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
        # 复用进程级缓存，避免每次重载40913案例
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

    # ── 断裂点③修复：S级信号自动触发deep_analysis推送VIP ─────────
    if s_list and mode in ('deep','signal'):
        # S级标的：自动跑深度分析获取精准VIP策略
        s_syms = [sym for sym, _ in s_list[:2]]  # 最多2个防内存峰值
        try:
            from brahma_deep_analysis import run_deep_analysis
            deep_out = run_deep_analysis(s_syms)
            # 提取VIP卡片行
            vip_lines = [l for l in deep_out.splitlines() if '姓赵' in l or '止损' in l or '多单' in l or '空单' in l]
            if vip_lines:
                lines.append('[S级自动深度策略]')
                lines.extend(vip_lines[:8])
                result_str = '\n'.join(lines)
        except Exception as e:
            pass

    # ── 免费AI增强层（Groq qwen3.8-27b全力利用）──────────────────
    if s_list and mode in ('deep','signal'):
        # S级标的：用Groq一句话摘要（免费，0.24s/次）
        try:
            from free_llm_client import chat
            ai_lines = ["\n[Groq AI增强摘要]"]
            for sym, r in s_list[:3]:  # 最多3个S级
                prompt = (f"{sym} 价格{r['px']:.4f} Taker={r['tak']} OI={r['oi_dir']} "
                         f"振幅={r.get('amplitude',0):.1f}% LSR={r['lsr']} "
                         f"信号:{'/'.join(r['reasons'][:2])} "
                         f"用10字以内判断合约方向和埋伏策略")
                ai_r = chat(prompt, task='vip', max_tokens=30)
                if ai_r:
                    ai_lines.append(f"  {sym}: {ai_r[:40]}")
            if len(ai_lines) > 1:
                lines.extend(ai_lines)
                result_str = '\n'.join(lines)
        except Exception:
            pass

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
