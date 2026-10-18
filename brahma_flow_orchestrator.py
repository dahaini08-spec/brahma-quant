#!/usr/bin/env python3
"""
brahma_flow_orchestrator.py — 梵天多Agent并发Flows架构
[封印 2026-10-18 苏摩111] claude-quickstarts Flows方案落地

架构: Orchestrator + 4路并发Agent
  Data Agent  — U本位API实时拉取
  SMC Agent   — FVG/OB结构分析
  Quant Agent — 方仓/RSI/ATR量化
  Macro Agent — DXY/宏观/GEX状态

目标: 串行6~17s → 并发2~3s
接入: brahma_manual_analysis.py run_analysis() 可选替代入口
"""
import asyncio, json, time, sys, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

# ── 工具函数 ──────────────────────────────────────────────────────────────

def fetch_fapi(path: str, params: str = '') -> dict:
    """U本位合约专属API（最高宪法）"""
    url = f'https://fapi.binance.com{path}?{params}' if params else f'https://fapi.binance.com{path}'
    try:
        with urllib.request.urlopen(url, timeout=7) as r:
            return json.loads(r.read())
    except Exception as e:
        return {'_error': str(e)}

# ── 4个并发Agent ──────────────────────────────────────────────────────────

def data_agent(sym: str) -> dict:
    """Data Agent: U本位实时数据拉取"""
    s = sym.upper() + 'USDT'
    t0 = time.time()
    price  = fetch_fapi(f'/fapi/v1/ticker/price', f'symbol={s}')
    stats  = fetch_fapi(f'/fapi/v1/ticker/24hr', f'symbol={s}')
    k1h    = fetch_fapi(f'/fapi/v1/klines', f'symbol={s}&interval=1h&limit=14')
    k4h    = fetch_fapi(f'/fapi/v1/klines', f'symbol={s}&interval=4h&limit=10')
    fr     = fetch_fapi(f'/fapi/v1/premiumIndex', f'symbol={s}')
    oi     = fetch_fapi(f'/fapi/v1/openInterest', f'symbol={s}')
    oi_h   = fetch_fapi(f'/futures/data/openInterestHist', f'symbol={s}&period=1h&limit=3')
    lsr    = fetch_fapi(f'/futures/data/globalLongShortAccountRatio', f'symbol={s}&period=5m&limit=1')
    taker  = fetch_fapi(f'/futures/data/takerlongshortRatio', f'symbol={s}&period=5m&limit=1')

    px = float(price.get('price', 0)) if isinstance(price, dict) else 0
    chg= float(stats.get('priceChangePercent', 0)) if isinstance(stats, dict) else 0
    mark = float(fr.get('markPrice', px)) if isinstance(fr, dict) else px
    fr_v = float(fr.get('lastFundingRate', 0)) * 100 if isinstance(fr, dict) else 0
    oi_cur = float(oi.get('openInterest', 0)) if isinstance(oi, dict) else 0

    # OI方向
    oi_dir = 'FLAT'
    if isinstance(oi_h, list) and len(oi_h) >= 2:
        p = float(oi_h[-2].get('sumOpenInterest', 0))
        c = float(oi_h[-1].get('sumOpenInterest', oi_cur))
        chg_oi = (c - p) / p * 100 if p > 0 else 0
        oi_dir = 'SHORT_BUILD' if chg_oi > 0.3 else ('LONG_UNWIND' if chg_oi < -0.3 else 'FLAT')

    lsr_v = float(lsr[0].get('longShortRatio', 1)) if isinstance(lsr, list) and lsr else 1
    tak_v = float(taker[0].get('buySellRatio', 1)) if isinstance(taker, list) and taker else 1

    # RSI 1H
    rsi_1h = 50
    if isinstance(k1h, list) and len(k1h) >= 3:
        closes = [float(k[4]) for k in k1h]
        diffs  = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = [max(d, 0) for d in diffs]
        losses = [max(-d, 0) for d in diffs]
        ag = sum(gains) / len(gains) or 0.001
        al = sum(losses) / len(losses) or 0.001
        rsi_1h = round(100 - 100 / (1 + ag / al), 1)

    # ATR 4H
    atr4h = 0
    if isinstance(k4h, list) and len(k4h) >= 2:
        trs = [max(float(k[2]) - float(k[3]),
                   abs(float(k[2]) - float(k4h[i-1][4])),
                   abs(float(k[3]) - float(k4h[i-1][4]))) for i, k in enumerate(k4h[1:], 1)]
        atr4h = round(sum(trs) / len(trs), 1)

    return dict(_agent='data', _elapsed=round(time.time()-t0,2),
        price=px, mark=mark, chg=chg, fr=fr_v, oi_cur=oi_cur, oi_dir=oi_dir,
        lsr=lsr_v, taker=tak_v, rsi_1h=rsi_1h, atr4h=atr4h,
        k1h=k1h if isinstance(k1h, list) else [],
        k4h=k4h if isinstance(k4h, list) else [])

def smc_agent(sym: str, k1h: list, k4h: list, price: float) -> dict:
    """SMC Agent: FVG/OB结构分析"""
    t0 = time.time()
    fvg_1h_bull = fvg_1h_bear = fvg_4h_bull = None
    ob_4h_bull = ob_4h_bear = None

    if k1h and len(k1h) >= 3:
        for i in range(len(k1h)-1, 1, -1):
            p2h = float(k1h[i-2][2]); cl = float(k1h[i][3])
            p2l = float(k1h[i-2][3]); ch = float(k1h[i][2])
            if cl > p2h and not fvg_1h_bull:
                fvg_1h_bull = {'mid': (p2h+cl)/2, 'lo': p2h, 'hi': cl,
                               'dist_pct': ((p2h+cl)/2 - price) / price * 100}
            if ch < p2l and not fvg_1h_bear:
                fvg_1h_bear = {'mid': (ch+p2l)/2, 'lo': ch, 'hi': p2l,
                               'dist_pct': ((ch+p2l)/2 - price) / price * 100}
            if fvg_1h_bull and fvg_1h_bear: break

    if k4h and len(k4h) >= 3:
        for i in range(len(k4h)-1, 1, -1):
            p2h = float(k4h[i-2][2]); cl = float(k4h[i][3])
            if cl > p2h and not fvg_4h_bull:
                fvg_4h_bull = {'mid': (p2h+cl)/2, 'dist_pct': ((p2h+cl)/2-price)/price*100}
                break
        for i in range(len(k4h)-2, 0, -1):
            k = k4h[i]; kn = k4h[i+1]
            if float(kn[4]) > float(k[2]) and not ob_4h_bull:
                ob_4h_bull = {'lo': float(k[3]), 'hi': float(k[2])}
            if float(kn[4]) < float(k[3]) and not ob_4h_bear:
                ob_4h_bear = {'lo': float(k[3]), 'hi': float(k[2])}
            if ob_4h_bull and ob_4h_bear: break

    return dict(_agent='smc', _elapsed=round(time.time()-t0, 2),
        fvg_1h_bull=fvg_1h_bull, fvg_1h_bear=fvg_1h_bear,
        fvg_4h_bull=fvg_4h_bull, ob_4h_bull=ob_4h_bull, ob_4h_bear=ob_4h_bear)

def quant_agent(sym: str, rsi_1h: float, atr4h: float, price: float) -> dict:
    """Quant Agent: 方仓历史检索 + 量化指标"""
    t0 = time.time()
    try:
        import fangcang_engine as fe
        fe._CACHE_LOADED = False; fe._FANGCANG_CACHE = []
        from fangcang_engine import fangcang_context_match as fcm
        bbw = atr4h / price * 100 if price > 0 else 2.0
        sig = 'LONG' if rsi_1h >= 50 else 'SHORT'
        fc  = fcm(sym.upper(), bbw, rsi_1h, 'CHOP_MID', sig)
        return dict(_agent='quant', _elapsed=round(time.time()-t0, 2),
            fc_n=fc.get('n_similar', 0), fc_long=fc.get('long_pct', 0),
            fc_short=fc.get('short_pct', 0), fc_genuine=fc.get('genuine_rate', 0),
            fc_burst=fc.get('avg_burst_atr_mult', 0), fc_sig=sig, bbw=round(bbw, 3))
    except Exception as e:
        return dict(_agent='quant', _elapsed=round(time.time()-t0, 2), _error=str(e))

def macro_agent() -> dict:
    """Macro Agent: 宏观+GEX状态"""
    t0 = time.time()
    try:
        import json as _j
        from pathlib import Path as _P
        gex = {}
        gf = _P(__file__).parent / 'data' / 'gex_state.json'
        if gf.exists(): gex = _j.load(open(gf))
        macro = {}
        mf = _P(__file__).parent / 'data' / 'macro_state.json'
        if mf.exists(): macro = _j.load(open(mf))
        btc_zf = gex.get('btc', {}).get('zero_flip', 0) or 0
        eth_zf = gex.get('eth', {}).get('zero_flip', 0) or 0
        return dict(_agent='macro', _elapsed=round(time.time()-t0, 2),
            btc_zf=btc_zf, eth_zf=eth_zf,
            dxy=macro.get('dxy', 94), fear_greed=macro.get('fear_greed', 50),
            event_risk=macro.get('event_risk', 0),
            macro_ok=macro.get('conclusion', 'OK') == 'OK')
    except Exception as e:
        return dict(_agent='macro', _elapsed=round(time.time()-t0, 2), _error=str(e))

# ── Orchestrator ──────────────────────────────────────────────────────────

def run_flows(sym: str) -> dict:
    """
    并发4路Agent，汇总结果
    接入位置: brahma_manual_analysis.py 可作为 run_analysis() 快速入口
    """
    sym = sym.upper()
    t_total = time.time()

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_data  = ex.submit(data_agent, sym)
        f_macro = ex.submit(macro_agent)
        # data必须先完成才能给smc/quant
        data  = f_data.result(timeout=15)
        f_smc   = ex.submit(smc_agent, sym, data.get('k1h',[]), data.get('k4h',[]), data.get('price',0))
        f_quant = ex.submit(quant_agent, sym, data.get('rsi_1h',50), data.get('atr4h',0), data.get('price',0))
        smc   = f_smc.result(timeout=10)
        quant = f_quant.result(timeout=15)
        macro = f_macro.result(timeout=10)

    elapsed = round(time.time() - t_total, 2)
    result = dict(sym=sym, elapsed=elapsed,
        price=data.get('price', 0), mark=data.get('mark', 0),
        chg=data.get('chg', 0), fr=data.get('fr', 0),
        rsi_1h=data.get('rsi_1h', 50), atr4h=data.get('atr4h', 0),
        oi_dir=data.get('oi_dir', 'FLAT'), lsr=data.get('lsr', 1),
        taker=data.get('taker', 1),
        fvg_1h_bull=smc.get('fvg_1h_bull'), fvg_1h_bear=smc.get('fvg_1h_bear'),
        fvg_4h_bull=smc.get('fvg_4h_bull'), ob_4h_bull=smc.get('ob_4h_bull'),
        fc_n=quant.get('fc_n', 0), fc_long=quant.get('fc_long', 0),
        fc_short=quant.get('fc_short', 0), fc_burst=quant.get('fc_burst', 0),
        btc_zf=macro.get('btc_zf', 0), eth_zf=macro.get('eth_zf', 0),
        macro_ok=macro.get('macro_ok', True), fear_greed=macro.get('fear_greed', 50),
        _agent_times={
            'data': data.get('_elapsed', 0), 'smc': smc.get('_elapsed', 0),
            'quant': quant.get('_elapsed', 0), 'macro': macro.get('_elapsed', 0)
        })
    return result

def run_flows_dual() -> dict:
    """BTC + ETH 双标的并发"""
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=2) as ex:
        fb = ex.submit(run_flows, 'BTC')
        fe = ex.submit(run_flows, 'ETH')
        btc = fb.result(timeout=20)
        eth = fe.result(timeout=20)
    total = round(time.time() - t0, 2)
    return {'BTC': btc, 'ETH': eth, '_total_elapsed': total}

# ── 格式化输出 ────────────────────────────────────────────────────────────

def format_flows_result(r: dict) -> str:
    sym = r.get('sym', '?')
    fvg4 = r.get('fvg_4h_bull')
    fvg4_str = f"${fvg4['mid']:,.1f}({fvg4['dist_pct']:+.2f}%)" if fvg4 else '无'
    lines = [
        f"【{sym} Flows】${r.get('price',0):,.2f}  {r.get('chg',0):+.2f}%  耗时={r.get('elapsed',0)}s",
        f"  RSI_1H={r.get('rsi_1h',0):.1f}  ATR4H=${r.get('atr4h',0):.0f}  FR={r.get('fr',0):+.4f}%",
        f"  OI={r.get('oi_dir','?')}  LSR={r.get('lsr',1):.3f}  Taker={r.get('taker',1):.3f}",
        f"  FVG4H={fvg4_str}",
        f"  方仓: n={r.get('fc_n',0)} LONG={r.get('fc_long',0):.0%} SHORT={r.get('fc_short',0):.0%} burst={r.get('fc_burst',0):.2f}x",
        f"  宏观: OK={r.get('macro_ok')}  恐贪={r.get('fear_greed',50)}",
        f"  Agent耗时: {r.get('_agent_times',{})}",
    ]
    return '\n'.join(lines)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--sym', default='BTC', help='BTC/ETH/BOTH')
    args = parser.parse_args()

    if args.sym.upper() == 'BOTH':
        res = run_flows_dual()
        print(f"双标的并发总耗时: {res['_total_elapsed']}s\n")
        print(format_flows_result(res['BTC']))
        print()
        print(format_flows_result(res['ETH']))
    else:
        res = run_flows(args.sym)
        print(format_flows_result(res))
