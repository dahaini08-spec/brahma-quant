#!/usr/bin/env python3
"""
brahma_mcp_server.py — 梵天 MCP Server
[P1封印 2026-10-18 苏摩111] claude-quickstarts MCP方案落地

把梵天9个核心分析工具打包为MCP接口：
  brahma_analyze   — BTC/ETH全维度分析
  brahma_vip       — 生成VIP策略卡片
  brahma_fangcang  — 方仓40年历史检索
  brahma_lsr       — LSR猎杀评估
  brahma_fvg       — FVG磁铁地图
  brahma_liq       — 清算地图
  brahma_gex       — GEX ZeroFlip状态
  brahma_oi        — OI趋势分析
  brahma_macro     — 宏观守门状态

接入: stdio协议（标准MCP协议）
使用: claude mcp add brahma python3 brahma_mcp_server.py
"""
import json, sys, time, os, urllib.request
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

# ── MCP协议基础 ──────────────────────────────────────────────────────────

def _send(obj: dict):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + '\n')
    sys.stdout.flush()

def _error(code: int, msg: str):
    return {"error": {"code": code, "message": msg}}

# ── 工具函数 ──────────────────────────────────────────────────────────────

def fetch_fapi(path: str, params: str = '') -> dict:
    """U本位合约专属API（最高宪法）"""
    url = f'https://fapi.binance.com{path}?{params}' if params else f'https://fapi.binance.com{path}'
    try:
        with urllib.request.urlopen(url, timeout=7) as r:
            return json.loads(r.read())
    except Exception as e:
        return {'error': str(e)}

def load_state(sym: str) -> dict:
    f = BASE / 'data' / f'brahma_state_{sym.lower()}.json'
    if f.exists():
        d = json.load(open(f))
        age = (time.time() - d.get('_snapshot_written_epoch', 0)) / 60
        d['_state_age_min'] = round(age, 1)
        return d
    return {}

# ── 9个工具实现 ──────────────────────────────────────────────────────────

def tool_brahma_analyze(sym: str = 'BTC') -> str:
    """BTC/ETH全维度分析快照"""
    sym = sym.upper()
    d = load_state(sym)
    if not d:
        return f'{sym} state不可用，请先运行 brahma_manual_analysis.py'
    lines = [
        f'【{sym}USDT】${d.get("price",0):,.2f}  score={d.get("score_final",0):.0f}  '
        f'{d.get("regime","?")}  Hurst={d.get("hurst",0):.3f}  age={d.get("_state_age_min",0):.0f}min',
        f'RSI: 1H={d.get("rsi_1h",0):.1f} / 4H={d.get("rsi_4h",0):.1f} / 1D={d.get("rsi_1d",0):.1f}',
        f'CVD={d.get("cvd_1h",0):.2f}  OI={d.get("oi_direction","?")}  LSR散户={d.get("lsr_retail",0):.1f}%',
        f'GEX ZF=${d.get("zero_flip",0):,.0f}  IV={d.get("iv_rank",0):.0f}%  κ={d.get("kappa",0):.3f}',
        f'FVG4H=${d.get("fvg_4h_magnet",0):,.0f}  清算↑${d.get("liq_short",0):,.0f}↓${d.get("liq_long",0):,.0f}',
        f'ATR4H=${d.get("atr_4h",0):.0f}  HAR=[${d.get("harv_lo",0):,.0f}~${d.get("harv_hi",0):,.0f}]',
        f'Step11={d.get("step11_verdict","?")}  EV={d.get("ev_pct",0):+.3f}%  RR={d.get("rr",0):.2f}',
    ]
    return '\n'.join(lines)

def tool_brahma_vip(sym: str = 'BTC') -> str:
    """生成VIP策略卡片"""
    d = load_state(sym.upper())
    vip = d.get('vip_card', '')
    if not vip:
        return f'{sym.upper()} 当前无VIP策略（Step11=WAIT或数据过期）'
    return vip

def tool_brahma_fangcang(sym: str = 'BTC', regime: str = 'CHOP_MID', rsi: float = 50) -> str:
    """方仓40年历史检索"""
    try:
        import fangcang_engine as fe
        fe._CACHE_LOADED = False; fe._FANGCANG_CACHE = []
        from fangcang_engine import fangcang_context_match as fcm, _load_fangcang_cases
        cases = _load_fangcang_cases()
        d = load_state(sym.upper())
        bbw = float(d.get('bb_width', 2.0))
        fc = fcm(sym.upper(), bbw, rsi, regime, 'LONG' if rsi >= 50 else 'SHORT')
        return (
            f'方仓检索: {sym.upper()} BBW={bbw:.2f}% RSI={rsi} {regime}\n'
            f'n={fc.get("n_similar",0)}  LONG={fc.get("long_pct",0):.0%}  SHORT={fc.get("short_pct",0):.0%}\n'
            f'genuine={fc.get("genuine_rate",0):.0%}  burst={fc.get("avg_burst_atr_mult",0):.2f}x\n'
            f'总库={len(cases):,}案例'
        )
    except Exception as e:
        return f'方仓检索失败: {e}'

def tool_brahma_lsr(sym: str = 'BTC') -> str:
    """LSR猎杀评估（U本位三层）"""
    s = sym.upper() + 'USDT'
    g = fetch_fapi('/futures/data/globalLongShortAccountRatio', f'symbol={s}&period=5m&limit=1')
    t = fetch_fapi('/futures/data/topLongShortAccountRatio', f'symbol={s}&period=5m&limit=1')
    p = fetch_fapi('/futures/data/topLongShortPositionRatio', f'symbol={s}&period=5m&limit=1')
    tk = fetch_fapi('/futures/data/takerlongshortRatio', f'symbol={s}&period=5m&limit=1')
    g_r = float(g[0].get('longShortRatio', 1)) if isinstance(g, list) and g else 1
    t_r = float(t[0].get('longShortRatio', 1)) if isinstance(t, list) and t else 1
    p_r = float(p[0].get('longShortRatio', 1)) if isinstance(p, list) and p else 1
    tk_r= float(tk[0].get('buySellRatio', 1)) if isinstance(tk, list) and tk else 1
    hunt = '⚡猎杀结构' if g_r > 2.5 else ('偏多' if g_r > 1.2 else '中性')
    return (
        f'LSR三层 {sym.upper()}\n'
        f'全局={g_r:.3f} {hunt}\n'
        f'大户账户={t_r:.3f}  大户仓位={p_r:.3f}\n'
        f'Taker买卖比={tk_r:.3f} {"主动买" if tk_r>=1 else "主动卖⚡"}'
    )

def tool_brahma_fvg(sym: str = 'BTC') -> str:
    """FVG磁铁地图（4TF）"""
    d = load_state(sym.upper())
    bd = d.get('confluence', {}).get('breakdown', {})
    fvg_map = bd.get('_fvg_map', {})
    if not fvg_map:
        return f'{sym.upper()} FVG数据不可用'
    lines = [f'FVG磁铁地图 {sym.upper()} @${d.get("price",0):,.0f}']
    for tf in ['15m', '1h', '4h', '1d']:
        for f in fvg_map.get(tf, [])[:1]:
            dist = f.get('dist_pct', 0)
            lines.append(f'  {tf} {f.get("type","")} mid=${f.get("mid",0):,.1f} ({dist:+.2f}%)')
    return '\n'.join(lines)

def tool_brahma_liq(sym: str = 'BTC') -> str:
    """清算地图"""
    d = load_state(sym.upper())
    px = d.get('price', 0)
    lu = d.get('liq_short', 0)
    ld = d.get('liq_long', 0)
    if not lu or not ld:
        return f'{sym.upper()} 清算数据不可用'
    up_pct = (lu - px) / px * 100 if px else 0
    dn_pct = (ld - px) / px * 100 if px else 0
    return (
        f'清算地图 {sym.upper()} @${px:,.2f}\n'
        f'空单止损墙 ↑${lu:,.0f} ({up_pct:+.2f}%)\n'
        f'多单止损池 ↓${ld:,.0f} ({dn_pct:+.2f}%)\n'
        f'ATR4H=${d.get("atr_4h",0):.0f}  SL最小≥${d.get("atr_4h",0)*1.5:.0f}'
    )

def tool_brahma_gex(sym: str = 'BTC') -> str:
    """GEX ZeroFlip状态"""
    d = load_state(sym.upper())
    zf = d.get('zero_flip', 0)
    px = d.get('price', 0)
    iv = d.get('iv_rank', 0)
    kappa = d.get('kappa', 0)
    if not zf:
        return f'{sym.upper()} GEX数据不可用'
    pos = '上方' if px > zf else '下方'
    dist = abs(px - zf)
    return (
        f'GEX {sym.upper()}\n'
        f'ZeroFlip=${zf:,.0f}  当前价在ZF{pos}${dist:,.0f}\n'
        f'IV={iv:.0f}%  κ={kappa:.3f} {"Put偏强" if kappa<0 else "Call偏强" if kappa>0.1 else "中性"}\n'
        f'{"站上ZF→做市商减空头对冲✅" if px>zf else "ZF下方→空头对冲压力⚡"}'
    )

def tool_brahma_oi(sym: str = 'BTC') -> str:
    """OI趋势分析（U本位）"""
    s = sym.upper() + 'USDT'
    oi_cur = fetch_fapi('/fapi/v1/openInterest', f'symbol={s}')
    oi_his = fetch_fapi('/futures/data/openInterestHist', f'symbol={s}&period=1h&limit=6')
    cur = float(oi_cur.get('openInterest', 0)) if isinstance(oi_cur, dict) else 0
    d = load_state(sym.upper())
    px = d.get('price', 1)
    oi_usd = cur * px
    chgs = []
    if isinstance(oi_his, list) and len(oi_his) >= 2:
        for i in range(1, len(oi_his)):
            p = float(oi_his[i-1].get('sumOpenInterest', 0))
            c = float(oi_his[i].get('sumOpenInterest', 0))
            if p > 0: chgs.append(round((c-p)/p*100, 2))
    trend = 'SHORT_BUILD' if chgs and chgs[-1]>0.3 else ('LONG_UNWIND' if chgs and chgs[-1]<-0.3 else 'FLAT')
    return (
        f'OI分析 {sym.upper()}\n'
        f'当前OI={cur:,.0f} {sym} ≈${oi_usd/1e9:.2f}B\n'
        f'逐小时变化={chgs}\n'
        f'趋势判断={trend}'
    )

def tool_brahma_macro() -> str:
    """宏观守门状态"""
    try:
        from plugins import run_all_plugins
        d = load_state('BTC')
        out = run_all_plugins({'price': d.get('price', 81000), 'symbol': 'BTCUSDT', 'regime': d.get('regime', 'CHOP_MID')})
        return out
    except Exception as e:
        return f'宏观数据: {e}'

# ── 工具注册表 ────────────────────────────────────────────────────────────

TOOLS = {
    'brahma_analyze':  {'fn': tool_brahma_analyze,  'desc': 'BTC/ETH全维度分析快照', 'args': [{'name':'sym','type':'string','desc':'BTC或ETH','default':'BTC'}]},
    'brahma_vip':      {'fn': tool_brahma_vip,       'desc': '生成VIP策略卡片',        'args': [{'name':'sym','type':'string','desc':'BTC或ETH','default':'BTC'}]},
    'brahma_fangcang': {'fn': tool_brahma_fangcang,  'desc': '方仓40年历史检索',       'args': [{'name':'sym'},{'name':'regime','default':'CHOP_MID'},{'name':'rsi','type':'number','default':50}]},
    'brahma_lsr':      {'fn': tool_brahma_lsr,       'desc': 'LSR猎杀评估（U本位三层）','args': [{'name':'sym','default':'BTC'}]},
    'brahma_fvg':      {'fn': tool_brahma_fvg,       'desc': 'FVG磁铁地图（4TF）',     'args': [{'name':'sym','default':'BTC'}]},
    'brahma_liq':      {'fn': tool_brahma_liq,       'desc': '清算地图',               'args': [{'name':'sym','default':'BTC'}]},
    'brahma_gex':      {'fn': tool_brahma_gex,       'desc': 'GEX ZeroFlip状态',       'args': [{'name':'sym','default':'BTC'}]},
    'brahma_oi':       {'fn': tool_brahma_oi,        'desc': 'OI趋势分析（U本位）',     'args': [{'name':'sym','default':'BTC'}]},
    'brahma_macro':    {'fn': tool_brahma_macro,     'desc': '宏观守门状态',           'args': []},
}

# ── MCP协议主循环 ─────────────────────────────────────────────────────────

def handle_request(req: dict) -> dict:
    method = req.get('method', '')
    rid    = req.get('id')

    if method == 'initialize':
        return {'jsonrpc':'2.0','id':rid,'result':{
            'protocolVersion':'2024-11-05',
            'capabilities':{'tools':{}},
            'serverInfo':{'name':'brahma-mcp-server','version':'1.0.0'}
        }}

    if method == 'tools/list':
        tools = []
        for name, meta in TOOLS.items():
            props = {}
            required = []
            for a in meta.get('args', []):
                props[a['name']] = {'type': a.get('type','string'), 'description': a.get('desc','')}
                if 'default' not in a: required.append(a['name'])
            tools.append({'name': name, 'description': meta['desc'],
                          'inputSchema': {'type':'object','properties':props,'required':required}})
        return {'jsonrpc':'2.0','id':rid,'result':{'tools':tools}}

    if method == 'tools/call':
        name   = req.get('params', {}).get('name', '')
        args   = req.get('params', {}).get('arguments', {})
        if name not in TOOLS:
            return {'jsonrpc':'2.0','id':rid,'error':{'code':-32601,'message':f'Tool {name} not found'}}
        try:
            result = TOOLS[name]['fn'](**args)
            return {'jsonrpc':'2.0','id':rid,'result':{'content':[{'type':'text','text':result}]}}
        except Exception as e:
            return {'jsonrpc':'2.0','id':rid,'error':{'code':-32000,'message':str(e)}}

    return {'jsonrpc':'2.0','id':rid,'error':{'code':-32601,'message':f'Method {method} not found'}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        try:
            req  = json.loads(line)
            resp = handle_request(req)
            _send(resp)
        except Exception as e:
            _send({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':str(e)}})


if __name__ == '__main__':
    main()
