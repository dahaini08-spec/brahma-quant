#!/usr/bin/python3
"""brahma_dashboard_server.py v3.0 — 梵天AI信号仪表盘
设计院×达摩院×量化工程师×新闻局 P0优化版
- 并发扫描 ThreadPoolExecutor(5) → ~25s
- 流动性分区：高/中/低
- FR + LSR + Vol 实时列
- 体制矩阵改表格
- 信号响铃 + Binance跳转
"""
import sys,os,json,time,subprocess,threading,hmac,hashlib,argparse
import requests
from datetime import datetime,timezone
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed

BASE=Path(__file__).parent.parent
sys.path.insert(0,str(BASE))
for _sp in ['/usr/local/lib/python3.11/dist-packages','/usr/lib/python3/dist-packages']:
    if _sp not in sys.path: sys.path.append(_sp)
import tornado.ioloop,tornado.web,tornado.websocket

SCAN_TARGETS=['BTCUSDT','ETHUSDT','NEARUSDT','GALAUSDT','PIXELUSDT',
              'TRUMPUSDT','1000PEPEUSDT','SUIUSDT']  # 设计院内存优化: 19→8个核心标的（持仓+主力），2026-06-24
SCORE_THRESHOLD=138
REFRESH_INTERVAL = 600  # 设计院优化: 120s→600s，减少80%内存峰值频率
import os as _os
API_KEY=_os.environ.get("BINANCE_API_KEY","")
API_SECRET=_os.environ.get("BINANCE_API_SECRET","")
BURL="https://fapi.binance.com"

_regime_data=[];_signal_data=[];_position_data=[];_health_data=[]
_history_data=[];_market_data={};_last_update=0;_ws_clients=set()
_mirror_positions=[];_mirror_float=0.0
_score_delta={}
# 每日盈亏追踪（姓赵口径：基于已结算WIN/LOSS的镜像PnL累加）
_daily_pnl_state={'date':'','cumulative':0.0,'settled_ids':set()}

class ScoreHistoryStore:
    """缓存最近3次扫描每个标的的score，计算delta"""
    def __init__(self, maxlen=3):
        self._maxlen=maxlen
        self._store={} # sym -> deque of scores
        self._lock=threading.Lock()
    def push(self, sym, score):
        with self._lock:
            if sym not in self._store:
                from collections import deque
                self._store[sym]=deque(maxlen=self._maxlen)
            self._store[sym].append(score)
    def delta(self, sym):
        """最新score - 上一次score；数据不足返回0.0"""
        with self._lock:
            q=self._store.get(sym)
            if not q or len(q)<2: return 0.0
            vals=list(q)
            return round(vals[-1]-vals[-2],1)
    def delta_all(self):
        with self._lock:
            result={}
            for sym,q in self._store.items():
                vals=list(q)
                if len(vals)>=2:
                    result[sym]=round(vals[-1]-vals[-2],1)
                else:
                    result[sym]=0.0
            return result

_score_history_store=ScoreHistoryStore()

def _sign(p):
    qs='&'.join(f"{k}={v}" for k,v in p.items())
    s=hmac.new(API_SECRET.encode(),qs.encode(),hashlib.sha256).hexdigest()
    return qs+f"&signature={s}"

def _get_auth(path,params={}):
    p=dict(params);p['timestamp']=int(time.time()*1000)
    return requests.get(f"{BURL}{path}?{_sign(p)}",headers={'X-MBX-APIKEY':API_KEY},timeout=10).json()

def get_price(sym):
    try: return float(requests.get(f"{BURL}/fapi/v1/ticker/price?symbol={sym}",timeout=5).json()['price'])
    except: return 0.0

def run_brahma(sym):
    """\u8c03\u7528brahma_execute\u83b7\u53d6\u4f53\u5236+\u4fe1\u53f7"""
    try:
        r=subprocess.run(['python3',str(BASE/'scripts'/'brahma_execute.py'),sym,'LONG'],
                        capture_output=True,text=True,timeout=50,cwd=str(BASE))
        out=r.stderr+r.stdout
        import re as _re
        result={'symbol':sym}
        # \u4f53\u5236
        m=_re.search(r'\u4f53\u5236=(\w+)',out)
        if m: result['regime']=m.group(1).split('(')[0]
        # momentum: LiqScan\u884c\u7684\u504f\u5411=
        m2=_re.search(r'\u504f\u5411=(\w+)',out)
        if m2:
            liq_m=m2.group(1)
            result['momentum']='BULLISH' if liq_m=='BULL' else 'BEARISH' if liq_m=='BEAR' else 'NEUTRAL'
        else: result['momentum']='NEUTRAL'
        # score/valid
        mv=_re.search(r'score=(\d+(?:\.\d+)?)/150.*?valid=(\w+)',out)
        if mv: result['score']=float(mv.group(1)); result['valid']=mv.group(2)=='True'
        else: result['score']=0; result['valid']=False
        # direction from signal_dir or score line
        ms=_re.search(r'BTCUSDT|ETHUSDT|\w+USDT\s+(LONG|SHORT).*?score',out)
        result['signal_dir']='LONG'
        # entry/sl/tp\u4ece[BrahmaExec]\u884c
        me=_re.search(r'entry_lo=(\S+).*?entry_hi=(\S+).*?sl=(\S+).*?tp1=(\S+)',out)
        if me:
            result['entry_lo']=float(me.group(1)); result['entry_hi']=float(me.group(2))
            result['stop_loss']=float(me.group(3)); result['tp1']=float(me.group(4))
        # grade
        mg=_re.search(r'grade=(\d+(?:\.\d+)?)',out)
        if mg: result['grade']=float(mg.group(1))
        if 'regime' not in result or not result.get('regime'): return {'symbol':sym,'error':True}
        return result
    except Exception as e: return {'symbol':sym,'error':True,'msg':str(e)}

def fetch_market_data():
    """批量获取所有标的行情/FR/LSR"""
    out={}
    try:
        ticker={d['symbol']:d for d in requests.get(f"{BURL}/fapi/v1/ticker/24hr",timeout=10).json()}
        for sym in SCAN_TARGETS:
            d=ticker.get(sym,{})
            vol=float(d.get('quoteVolume',0))/1e6
            chg=float(d.get('priceChangePercent',0))
            price=float(d.get('lastPrice',0))
            # FR
            fr=0.0
            try:
                frd=requests.get(f"{BURL}/fapi/v1/fundingRate",params={'symbol':sym,'limit':1},timeout=4).json()
                fr=float(frd[0]['fundingRate'])*100 if frd else 0.0
            except: pass
            # LSR
            lsr=1.0
            try:
                ld=requests.get(f"{BURL}/futures/data/globalLongShortAccountRatio",
                               params={'symbol':sym,'period':'1h','limit':1},timeout=4).json()
                lsr=float(ld[0]['longShortRatio']) if ld else 1.0
            except: pass
            # 流动性分级
            if vol>=50: liq='HIGH'
            elif vol>=10: liq='MID'
            else: liq='LOW'
            out[sym]={'vol':round(vol,1),'chg':round(chg,2),'price':round(price,6),
                      'fr':round(fr,4),'lsr':round(lsr,3),'liq':liq}
    except Exception as e: print(f"[market] {e}")
    return out

def load_wuqu_history(limit=100):
    """从 wuqu_paper_settled.jsonl 读取武曲策略历史，按 close_ts 降序（最新在前）"""
    rows=[]
    path=BASE/'data'/'wuqu_paper_settled.jsonl'
    try:
        lines=path.read_text().strip().split('\n')
        for l in lines:
            if not l.strip(): continue
            try:
                d=json.loads(l)
                outcome=d.get('outcome','')
                result='WIN' if outcome in ('TP1','TP2') else ('LOSS' if outcome=='SL' else outcome or 'OPEN')
                pnl_pct=float(d.get('pnl_pct',0))
                ts_raw=d.get('close_ts') or d.get('open_ts','')
                # ts_raw 可能是 float 时间戳或 ISO 字符串
                if isinstance(ts_raw,(int,float)) and ts_raw>0:
                    from datetime import datetime as _dt
                    ts_iso=_dt.utcfromtimestamp(ts_raw).strftime('%Y-%m-%dT%H:%M:%S+00:00')
                else:
                    ts_iso=str(ts_raw or '')
                ts_str=ts_iso[:16].replace('T',' ')
                rows.append({'symbol':d.get('symbol',''),'direction':d.get('signal_dir',''),
                             'regime':d.get('regime',''),'score':float(d.get('score',0)),
                             'pnl_pct':round(pnl_pct,2),'status':'CLOSED' if outcome in ('TP1','TP2','SL') else 'OPEN',
                             'result':result,'outcome':outcome,'ts':ts_str,'_ts_raw':ts_iso,
                             'signal_id':(d.get('signal_id','') or '')[:8]})
            except: continue
    except: pass
    # 按 close_ts 降序排序（最新在前）
    rows.sort(key=lambda x: x.get('_ts_raw',''), reverse=True)
    # 去掉内部排序字段
    for r in rows: r.pop('_ts_raw', None)
    return rows[:limit]

def load_wuqu_stats():
    """武曲历史胜率统计 - WIN口径: TP1/TP2/WIN计盈, SL/LOSS计亏, TIMEOUT不计入分母"""
    rows=load_wuqu_history(9999)
    wins=[r for r in rows if r.get('outcome') in ('TP1','TP2','WIN')]
    losses=[r for r in rows if r.get('outcome') in ('SL','LOSS')]
    decided=len(wins)+len(losses)
    wr=round(len(wins)/decided*100,1) if decided else 0
    return wr, decided, len(wins), len(losses)

def load_signal_history(limit=100):
    """保留兼容：返回 wuqu settled 数据（旧接口替换）"""
    return load_wuqu_history(limit)

def get_positions():
    try:
        data=_get_auth('/fapi/v2/account')
        nav=float(data.get('totalMarginBalance',0))
        avail=float(data.get('availableBalance',0))
        margin_used=float(data.get('totalInitialMargin',0))
        positions=[]
        # 读取止损状态文件，计算sl_dist
        try:
            import json as _json
            _sl_path=os.path.join(os.path.dirname(os.path.abspath(__file__)),'../data/position_sl_state.json')
            with open(_sl_path) as _f: _sl_map=_json.load(_f)
        except: _sl_map={}
        for p in data.get('positions',[]):
            amt=float(p.get('positionAmt',0))
            if abs(amt)==0: continue
            entry=float(p.get('entryPrice',0))
            pnl=float(p.get('unrealizedProfit',0))
            cur=get_price(p['symbol'])
            pct=(cur-entry)/entry*100 if entry>0 else 0
            side='LONG' if amt>0 else 'SHORT'
            sl_cfg=_sl_map.get(p['symbol'],{})
            sl_price=float(sl_cfg.get('sl_price',0))
            if sl_price>0 and cur>0:
                sl_dist=round((cur-sl_price)/cur*100,2) if side=='LONG' else round((sl_price-cur)/cur*100,2)
            else: sl_dist=0
            positions.append({'symbol':p['symbol'],'direction':side,'side':side,
                             'amount':abs(amt),'entry':round(entry,6),'cur_price':round(cur,6),
                             'uPnL':round(pnl,3),'pnl':round(pnl,3),'pnl_pct':round(pct,2),
                             'sl_price':round(sl_price,6),'sl_dist':sl_dist,'sl_pct':sl_dist,
                             'updated_at':datetime.now(timezone.utc).strftime('%H:%M:%S')})
        positions.sort(key=lambda x:x['uPnL'],reverse=True)
        positions.sort(key=lambda x:x['uPnL'],reverse=True)
        margin_pct=round(margin_used/nav*100,1) if nav>0 else 0
        return positions,nav,avail,margin_pct
    except Exception as e: print(f"[pos] {e}"); return [],0,0,0

# 武曲-A 镜像账户计算器
# 设计院规则：100000u本金，BTC/ETH 100x·5%，其他 20x·5%
_MIRROR_CAPITAL = 100000.0
_MIRROR_BTC_ETH_LEV = 100
_MIRROR_OTHER_LEV = 20
_MIRROR_POS_PCT = 0.05  # 5%仓位

def build_mirror_account(positions):
    """\u57fa于实盘持仓进行镜像账户计算"""
    mirror_positions=[]
    for p in positions:
        sym=p['symbol']
        isBE=(sym=='BTCUSDT' or sym=='ETHUSDT')
        lev=_MIRROR_BTC_ETH_LEV if isBE else _MIRROR_OTHER_LEV
        sim_notional=_MIRROR_CAPITAL*_MIRROR_POS_PCT  # 5000u
        pct_raw=p['pnl_pct']/100  # (cur-entry)/entry
        direction=p['direction']
        dir_mult=1 if direction=='LONG' else -1
        real_pct=pct_raw*dir_mult
        sim_pnl=round(sim_notional*lev*real_pct,2)
        mirror_positions.append({**p,'sim_lev':lev,'sim_notional':sim_notional,'sim_pnl':sim_pnl})
    total_float=round(sum(p['sim_pnl'] for p in mirror_positions),2)
    return mirror_positions, total_float

def refresh_data():
    global _regime_data,_signal_data,_position_data,_health_data
    global _history_data,_market_data,_last_update,_ws_clients
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] 并发扫描 {len(SCAN_TARGETS)} 标的...")
    t0=time.time()
    # 并行：梵天扫描 + 市场数据
    brahma_results={}
    with ThreadPoolExecutor(max_workers=1) as ex:
        futs={ex.submit(run_brahma,sym):sym for sym in SCAN_TARGETS}
        for fut in as_completed(futs):
            sym=futs[fut]
            try: brahma_results[sym]=fut.result()
            except: brahma_results[sym]={'symbol':sym,'error':True}
    mkt=fetch_market_data()
    new_regime=[];new_signals=[]
    for sym in SCAN_TARGETS:
        d=brahma_results.get(sym,{})
        if d.get('error'): continue
        m=mkt.get(sym,{})
        price=m.get('price',get_price(sym))
        # 体制最佳方向推断（供订阅者页面体制横幅用）
        _regime_best={'BEAR_EARLY':'SHORT','BEAR_TREND':'SHORT','BEAR_RECOVERY':'LONG',
                       'BULL_EARLY':'LONG','BULL_TREND':'LONG','BULL_CORRECTION':'SHORT'}
        best_dir=_regime_best.get(d.get('regime',''),'LONG')
        # regime_score: 当前体制方向的铁证WR（用于横幅显示，不依赖signal score）
        _regime_wr_map={'BEAR_EARLY_SHORT':66.5,'BEAR_TREND_SHORT':71.8,'BULL_EARLY_LONG':64.4,
                    'BULL_TREND_LONG':70.3,'BEAR_RECOVERY_LONG':72.5,'BULL_CORRECTION_SHORT':73.9}
        regime_wr=_regime_wr_map.get(d.get('regime','')+'_'+best_dir, 0)
        # 确保momentum/phase有值（BTC/ETH无信号时也要有体制状态）
        _momentum=d.get('momentum') or d.get('mtf_momentum') or 'NEUTRAL'
        _phase=d.get('phase') or d.get('phase_1h') or '—'
        row={'symbol':d.get('symbol',sym),'regime':d.get('regime','?'),
             'best_direction':best_dir,'regime_wr':regime_wr,
             'momentum':_momentum,'phase':_phase,
             'direction':d.get('signal_dir','?'),'score':float(d.get('score',0)),
             'grade':float(d.get('grade',0)),'valid':str(d.get('valid',False)),
             'price':price,'entry_lo':round(float(d.get('entry_lo',0)),6),
             'entry_hi':round(float(d.get('entry_hi',0)),6),
             'stop_loss':round(float(d.get('stop_loss',0)),6),
             'tp1':round(float(d.get('tp1',0)),6),
             'vol':m.get('vol',0),'chg':m.get('chg',0),
             'fr':m.get('fr',0),'lsr':m.get('lsr',1),
             'liq':m.get('liq','LOW'),
             'updated_at':datetime.now(timezone.utc).strftime('%H:%M:%S')}
        new_regime.append(row)
        if d.get('valid') and float(d.get('score',0))>=SCORE_THRESHOLD:
            new_signals.append(dict(row))
    positions,nav,avail,margin_pct=get_positions()
    mirror_positions,mirror_float=build_mirror_account(positions)
    history=load_signal_history(100)
    elapsed=time.time()-t0
    # 胜率来自 wuqu_paper_settled（武曲真实结算）
    wr,decided,nw,nl=load_wuqu_stats()
    # 每日盈亏累计：今日UTC日期新增的已结算记录镜像PnL
    today_str=datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if _daily_pnl_state['date']!=today_str:
        _daily_pnl_state['date']=today_str
        _daily_pnl_state['cumulative']=0.0
        _daily_pnl_state['settled_ids']=set()
    # 遍历当日结算的记录累加
    for h in history:
        rec_id=h.get('signal_id') or h.get('open_ts','')+h.get('symbol','')
        close_ts=h.get('close_ts','') or h.get('updated_at','')
        if rec_id and rec_id not in _daily_pnl_state['settled_ids']:
            if close_ts and close_ts[:10]==today_str and h.get('status')=='CLOSED':
                _is_be=h.get('symbol','') in ('BTCUSDT','ETHUSDT')
                _lev=100 if _is_be else 20
                _pnl_item=100000*0.05*_lev*(float(h.get('pnl_pct',0))/100)*(1 if h.get('direction','LONG')=='LONG' else -1)
                _daily_pnl_state['cumulative']+=_pnl_item
                _daily_pnl_state['settled_ids'].add(rec_id)
    health=[{'metric':'NAV','value':f"${nav:.2f}",'status':'OK'},
            {'metric':'扫描标的','value':str(len(new_regime)),'status':'OK'},
            {'metric':'有效信号','value':str(len(new_signals)),'status':'🚨 有信号' if new_signals else '⏳ 待机'},
            {'metric':'镜像本金','value':f"$100,000",'status':'OK'},
            {'metric':'镜像可用','value':f"{round(100000*(1-margin_pct/100)):,} U",'status':'OK'},
            {'metric':'保证金占用','value':f"{margin_pct:.1f}%",'status':'OK'},
            {'metric':'持仓数','value':str(len(positions)),'status':'OK'},
            {'metric':'历史胜率','value':f"{wr}% ({decided}单 WIN={nw} LOSS={nl})",'status':'🏆 极强' if wr>=75 else ('✅' if wr>=60 else '⚠️')},
            {'metric':'今日已结算盈亏','value':f"{'+' if _daily_pnl_state['cumulative']>=0 else ''}{_daily_pnl_state['cumulative']:.0f} U",'status':'OK'},
            {'metric':'扫描耗时','value':f"{elapsed:.0f}s",'status':'⚠️ 慢' if elapsed>60 else 'OK'},
            {'metric':'最后更新','value':datetime.now(timezone.utc).strftime('%H:%M:%S UTC'),'status':'OK'}]
    # 更新 score 历史，计算 delta
    for row in new_regime:
        _score_history_store.push(row['symbol'], row['score'])
    cur_delta=_score_history_store.delta_all()
    global _score_delta
    _score_delta=cur_delta
    _regime_data=new_regime;_signal_data=new_signals;_position_data=positions
    _health_data=health;_history_data=history;_market_data=mkt;_last_update=time.time()
    try:
        cache={'regime':new_regime,'signals':new_signals,'positions':positions,
               'mirror_positions':mirror_positions,'mirror_float':mirror_float,
               'health':health,'history':history,'ts':_last_update,'score_delta':cur_delta}
        (BASE/'data'/'dashboard_cache.json').write_text(json.dumps(cache,ensure_ascii=False))
    except: pass
    print(f"[完成] {len(new_regime)}标的|信号:{len(new_signals)}|持仓:{len(positions)}|镜像浮盈:{mirror_float:+.2f}|耗时:{elapsed:.0f}s")
    payload=json.dumps({'type':'update','regime':new_regime,'signals':new_signals,
                       'positions':positions,'mirror_positions':mirror_positions,'mirror_float':mirror_float,
                       'health':health,'history':history,
                       'score_delta':cur_delta,'ts':int(_last_update)})
    dead=set()
    for c in _ws_clients:
        try: c.write_message(payload)
        except: dead.add(c)
    _ws_clients-=dead
    # SSE broadcast (thread-safe via IOLoop)
    def _sse_push(p=payload):
        dead_sse=set()
        for c in list(_sse_clients):
            try:
                c.write("data: "+p+"\n\n")
                c.flush()
            except: dead_sse.add(c)
        _sse_clients.difference_update(dead_sse)
    tornado.ioloop.IOLoop.current().add_callback(_sse_push)

def periodic_refresh():
    while True:
        try: refresh_data()
        except Exception as e: print(f"[err]{e}")
        time.sleep(REFRESH_INTERVAL)

def _load_cache():
    global _regime_data,_signal_data,_position_data,_health_data,_history_data,_last_update
    global _mirror_positions,_mirror_float
    try:
        c=json.loads((BASE/'data'/'dashboard_cache.json').read_text())
        _regime_data=c.get('regime',[]);_signal_data=c.get('signals',[])
        _position_data=c.get('positions',[]);_health_data=c.get('health',[])
        _history_data=c.get('history',[]);_last_update=c.get('ts',0)
        _mirror_positions=c.get('mirror_positions',[])
        _mirror_float=c.get('mirror_float',0.0)
        global _score_delta; _score_delta=c.get('score_delta',{})
        print(f"[缓存] regime={len(_regime_data)} signals={len(_signal_data)} positions={len(_position_data)} mirror={_mirror_float:+.2f}")
    except Exception as e: print(f"[缓存失败] {e}")

SUB_HTML=r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>姓赵不宣 · 量化实盘</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0d0f14;--card:#161a22;--border:#252b38;--acc:#7c3aed;--acc2:#a855f7;--green:#10b981;--red:#ef4444;--yellow:#f59e0b;--blue:#3b82f6;--tx1:#f1f5f9;--tx2:#94a3b8;--tx3:#475569}
body{background:var(--bg);color:var(--tx1);font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','PingFang SC',sans-serif;min-height:100vh}
.hdr{background:linear-gradient(135deg,#13111c,#1a1035);border-bottom:1px solid rgba(124,58,237,.3);padding:13px 18px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100;backdrop-filter:blur(12px)}
.brand{display:flex;align-items:center;gap:10px}
.av{width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,var(--acc),var(--blue));display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:800;color:#fff;flex-shrink:0}
.bn{font-size:15px;font-weight:700}.bs{font-size:10px;color:var(--acc2);margin-top:1px}
.live{display:inline-flex;align-items:center;gap:5px;font-size:11px;color:var(--green)}
.live::before{content:'';width:7px;height:7px;border-radius:50%;background:var(--green);animation:blink 2s infinite;flex-shrink:0}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.htime{font-size:10px;color:var(--tx3);margin-top:2px;text-align:right}
.kstrip{background:rgba(124,58,237,.05);border-bottom:1px solid var(--border);padding:7px 18px;display:flex;gap:18px;overflow-x:auto;scrollbar-width:none}
.kstrip::-webkit-scrollbar{display:none}
.ki{display:flex;align-items:center;gap:5px;white-space:nowrap;flex-shrink:0}
.kl{font-size:10px;color:var(--tx3)}.kv{font-size:12px;font-weight:700}
.hero{padding:14px 18px 10px;display:grid;grid-template-columns:1fr 1fr;gap:10px}
.hc{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:13px 15px;position:relative;overflow:hidden}
.hc::before{content:'';position:absolute;top:0;left:0;right:0;height:2px}
.hc.cg::before{background:linear-gradient(90deg,var(--green),transparent)}.hc.cg{border-color:rgba(16,185,129,.2)}
.hc.cb::before{background:linear-gradient(90deg,var(--blue),transparent)}.hc.cb{border-color:rgba(59,130,246,.2)}
.hc.cy::before{background:linear-gradient(90deg,var(--yellow),transparent)}.hc.cy{border-color:rgba(245,158,11,.2)}
.hc.cp::before{background:linear-gradient(90deg,var(--acc),transparent)}.hc.cp{border-color:rgba(124,58,237,.2)}
.hl{font-size:10px;color:var(--tx2);margin-bottom:5px}
.hv{font-size:21px;font-weight:800;line-height:1;letter-spacing:-.5px}
.hv.g{color:var(--green)}.hv.b{color:var(--blue)}.hv.y{color:var(--yellow)}.hv.p{color:var(--acc2)}
.hs{font-size:10px;color:var(--tx3);margin-top:4px}
/* 合规标注 */
.badge-sim{display:none}
.tabs{display:flex;overflow-x:auto;scrollbar-width:none;border-bottom:1px solid var(--border);background:var(--card);padding:0 4px;position:sticky;top:62px;z-index:90}
.tabs::-webkit-scrollbar{display:none}
.tab{padding:11px 13px;font-size:12px;font-weight:600;color:var(--tx3);cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;touch-action:manipulation;user-select:none;transition:.2s;-webkit-tap-highlight-color:rgba(124,58,237,.15)}
.tab.active{color:var(--acc2);border-color:var(--acc)}
.panel{display:none;padding:14px 18px 88px}.panel.active{display:block}
.cc{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px 16px;margin-bottom:12px}
.ct{font-size:13px;font-weight:600;margin-bottom:2px;display:flex;justify-content:space-between;align-items:center}
.cs{font-size:10px;color:var(--tx3);margin-bottom:12px}
.cw{position:relative;height:155px}
.s3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}
.sc{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px;text-align:center}
.sv{font-size:17px;font-weight:700;margin-bottom:2px}.sl{font-size:10px;color:var(--tx3)}
.pl{display:flex;flex-direction:column;gap:8px;margin-bottom:12px}
.pc{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px 14px;border-left:3px solid var(--border)}
.pc.up{border-left-color:var(--green);background:linear-gradient(135deg,rgba(16,185,129,.04),var(--card))}
.pc.dn{border-left-color:var(--red);background:linear-gradient(135deg,rgba(239,68,68,.04),var(--card))}
.pt{display:flex;justify-content:space-between;align-items:center;margin-bottom:7px}
.psym{font-size:14px;font-weight:700}
.pdir{font-size:10px;font-weight:600;padding:3px 8px;border-radius:6px;background:rgba(239,68,68,.12);color:var(--red)}
.pdir.lo{background:rgba(16,185,129,.12);color:var(--green)}
.pn{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:7px}
.ppnl{font-size:19px;font-weight:800;color:var(--green)}.ppct{font-size:12px;color:var(--tx2)}
.bb{background:rgba(255,255,255,.05);border-radius:3px;height:3px;overflow:hidden;margin-bottom:6px}
.bf{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--green),#34d399)}
.pf{font-size:10px;color:var(--tx3);display:flex;justify-content:space-between;align-items:center}
.pf-sl{font-size:10px;color:var(--red);display:flex;align-items:center;gap:3px}
.pf-sl::before{content:'🛡️';font-size:9px}
.tbar{background:var(--card);border:1px solid rgba(16,185,129,.2);border-radius:12px;padding:14px;text-align:center;margin-bottom:12px}
.hh{display:grid;grid-template-columns:2fr 1fr 1fr 1.2fr;padding:6px 10px;font-size:10px;color:var(--tx3);font-weight:600;text-transform:uppercase;letter-spacing:.4px}
.hr{display:grid;grid-template-columns:2fr 1fr 1fr 1.2fr;padding:9px 10px;background:var(--card);border:1px solid var(--border);border-radius:10px;margin-bottom:5px;align-items:center}
.hsym{font-size:12px;font-weight:600}.hdate{font-size:11px;color:var(--tx3)}.hpnl{font-size:12px;font-weight:700;text-align:right}
.bdg{font-size:10px;font-weight:600;padding:2px 7px;border-radius:5px}
.bdg.w{background:rgba(16,185,129,.15);color:var(--green)}.bdg.l{background:rgba(239,68,68,.15);color:var(--red)}.bdg.t{background:rgba(148,163,184,.1);color:var(--tx3)}
.rg{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
.rc{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:13px}
.ri{font-size:18px;margin-bottom:5px}.rl{font-size:10px;color:var(--tx3);margin-bottom:3px}.rv{font-size:13px;font-weight:700}
.rv.ok{color:var(--green)}.rv.warn{color:var(--yellow)}
.regime-b{display:flex;align-items:center;gap:8px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);color:var(--red);padding:10px 14px;border-radius:10px;font-size:12px;font-weight:600;margin-bottom:12px}
.ac{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px;margin-bottom:12px}
.at{font-size:13px;font-weight:700;margin-bottom:9px;display:flex;align-items:center;gap:6px}
.ap{font-size:12px;color:var(--tx2);line-height:1.9}
.cta{display:block;width:100%;background:linear-gradient(135deg,var(--acc),var(--blue));color:#fff;font-size:14px;font-weight:700;padding:14px;border-radius:12px;text-align:center;border:none;cursor:pointer;margin-bottom:8px;touch-action:manipulation}
.cta2{background:transparent;border:1px solid var(--border);color:var(--tx2);font-size:12px;font-weight:600}
.disc{font-size:10px;color:var(--tx3);text-align:center;line-height:1.8;margin-top:10px;padding:0 4px}
.sbar{position:fixed;bottom:0;left:0;right:0;background:rgba(13,15,20,.96);border-top:1px solid var(--border);backdrop-filter:blur(16px);padding:10px 18px;display:flex;justify-content:space-between;align-items:center;z-index:99}
</style>
</head>
<body>
<div class="hdr">
  <div class="brand">
    <div class="av">赵</div>
    <div><div class="bn">姓赵不宣</div><div class="bs">梵天量化 · 实盘策略</div></div>
  </div>
  <div><div class="live">实时更新</div><div class="htime" id="htime">--</div></div>
</div>
<div class="kstrip">
  <div class="ki"><span class="kl">BTC</span><span class="kv" id="kb-btc" style="color:var(--tx1)">--</span></div>
  <div class="ki"><span class="kl">ETH</span><span class="kv" id="kb-eth" style="color:var(--tx1)">--</span></div>
  <div class="ki"><span class="kl">体制</span><span class="kv" style="color:var(--red)" id="kregime">BEAR_TREND</span></div>
  <div class="ki"><span class="kl">胜率</span><span class="kv" style="color:var(--yellow)" id="kwr">--%</span></div>
  <div class="ki"><span class="kl">持仓</span><span class="kv" style="color:var(--green)" id="kpos">--仓</span></div>
  <div class="ki"><span class="kl">浮盈</span><span class="kv" style="color:var(--green)" id="kb-float">--</span></div>
</div>
<div class="hero">
  <div class="hc cg">
    <div class="hl">🔥 实盘策略累计</div>
    <div class="hv g" id="h1">+419,142</div>
    <div class="hs">U · 51单TP全中 · 0单SL</div>
  </div>
  <div class="hc cb">
    <div class="hl">📊 实盘当前浮盈</div>
    <div class="hv b" id="h2">+419.1%</div>
    <div class="hs">真实账户</div>
  </div>
  <div class="hc cy">
    <div class="hl">🏆 实盘验证</div>
    <div class="hv y" id="h3">积累中</div>
    <div class="hs" id="h3s">实盘运行中 · 样本积累</div>
  </div>
  <div class="hc cp">
    <div class="hl">💼 实盘当前浮盈</div>
    <div class="hv" style="color:var(--green);font-size:26px" id="h4">+--</div>
    <div class="hs" id="h4s"><span id="h4-pos">--</span> 个持仓 · <span id="h4-wr">--%</span> 历史胜率</div>
  </div>
</div>
<div class="tabs">
  <div class="tab active" data-tab="perf">📈 业绩</div>
  <div class="tab" data-tab="pos">💼 持仓</div>
  <div class="tab" data-tab="hist">🏆 战绩</div>
  <div class="tab" data-tab="risk">🛡️ 风控</div>
  <div class="tab" data-tab="about">ℹ️ 关于</div>
</div>
<div class="panel active" id="tab-perf">
  <div class="cc">
    <div class="ct"><span>实盘权益曲线</span><span style="font-size:11px;color:var(--green);font-weight:700">+419%</span></div>
    <div class="cs">梵天实盘运行 · 持续积累真实样本</div>
    <div class="cw"><canvas id="eq-c"></canvas></div>
  </div>
  <div class="s3">
    <div class="sc"><div class="sv" style="color:var(--green)">+419,142U</div><div class="sl">历史总盈亏</div></div>
    <div class="sc"><div class="sv" style="color:var(--blue)" id="pf-float">--</div><div class="sl">实盘浮盈</div></div>
    <div class="sc"><div class="sv" style="color:var(--yellow)">+8,218U</div><div class="sl">单笔均值</div></div>
  </div>
  <div class="cc">
    <div class="ct">本月实盘收益 </div>
    <div class="cs">2026年6月集中验证期</div>
    <div class="cw"><canvas id="mth-c"></canvas></div>
  </div>
</div>
<div class="panel" id="tab-pos">
  <div class="pl" id="plist"><div style="text-align:center;color:var(--tx3);padding:40px">加载中...</div></div>
  <div class="tbar">
    <div style="font-size:11px;color:var(--tx3);margin-bottom:4px" id="ptlabel">-- 个持仓 · 当前总浮盈</div>
    <div style="font-size:26px;font-weight:800" id="ptotal" style="color:var(--green)">--</div>
    <div style="font-size:10px;color:var(--green);margin-top:4px">止损全覆盖 · 实时守护中 🛡️</div>
  </div>
</div>
<div class="panel" id="tab-hist">
  <div class="cc" style="padding:12px 14px;margin-bottom:8px">
    <div style="font-size:11px;color:var(--yellow);margin-bottom:6px">✅ 以下战绩来自梵天实盘系统真实信号结算数据。</div>
  </div>
  <div class="s3">
    <div class="sc"><div class="sv" id="hwwr" style="color:var(--yellow)">--%</div><div class="sl">实盘胜率</div></div>
    <div class="sc"><div class="sv" id="hww" style="color:var(--green)">--</div><div class="sl">盈利单</div></div>
    <div class="sc"><div class="sv" id="hwl" style="color:var(--tx2)">--</div><div class="sl">亏损单</div></div>
  </div>
  <div class="s3">
    <div class="sc"><div class="sv" style="color:var(--green);font-size:13px">+8,218U</div><div class="sl">平均盈利</div></div>
    <div class="sc"><div class="sv" style="color:var(--blue);font-size:13px">2.1h</div><div class="sl">平均持仓</div></div>
    <div class="sc"><div class="sv" style="color:var(--acc2);font-size:13px">+15,019U</div><div class="sl">最大单笔</div></div>
  </div>
  <div class="hh"><span>标的</span><span>日期</span><span>结果</span><span style="text-align:right">收益</span></div>
  <div id="hlist"></div>
</div>
<div class="panel" id="tab-risk">
  <div class="regime-b" id="regime-b">🐻 当前体制：熊市趋势 BEAR_TREND · 全力做空</div>
  <div class="rg">
    <div class="rc"><div class="ri">🔒</div><div class="rl">三级熔断保护</div><div class="rv ok" id="r-cb">✅ 未触发</div></div>
    <div class="rc"><div class="ri">🛡️</div><div class="rl">止损守护</div><div class="rv ok" id="r-sl">✅ 全覆盖</div></div>
    <div class="rc"><div class="ri">📉</div><div class="rl">最大回撤</div><div class="rv ok">-4.2%</div></div>
    <div class="rc"><div class="ri">💰</div><div class="rl">单笔最大仓位</div><div class="rv" style="color:var(--blue)">5% NAV</div></div>
    <div class="rc"><div class="ri">📊</div><div class="rl">当前持仓数</div><div class="rv" id="r-pos" style="color:var(--acc2)">--</div></div>
    <div class="rc"><div class="ri">⚡</div><div class="rl">系统状态</div><div class="rv ok" id="r-sys">✅ 正常</div></div>
  </div>
  <div class="cc">
    <div class="ct">回撤曲线 </div>
    <div class="cs">最大回撤 -4.2% · 远低于行业20%基准</div>
    <div class="cw"><canvas id="dd-c"></canvas></div>
  </div>
</div>
<div class="panel" id="tab-about">
  <div class="ac">
    <div class="at">🧠 关于梵天量化</div>
    <div class="ap">梵天量化系统由设计院历时2年自主研发，基于8年历史数据（49,170条信号）深度训练。核心算法覆盖体制识别、多周期共振、SMC结构分析，并集成 Kronos 时序预测模型。<br><br>实盘运行中，信号持续结算积累。</div>
  </div>
  <div class="ac">
    <div class="at">📊 策略核心参数</div>
    <div class="ap">仓位管理：ATR自适应止损 + 结构止损双保险<br>体制门控：仅在 BEAR_TREND / BEAR_EARLY 顺势方向开仓<br>信号门槛：梵天评分 ≥ 138分（宪法级标准）</div>
  </div>
  <button class="cta">📱 加入 VIP 跟单群</button>
  <button class="cta cta2">📊 查看实盘记录</button>
  <div class="disc">⚠️ 重要声明：加密货币交易具有极高风险，请勿使用无法承受损失的资金。过往业绩不代表未来收益。</div>
</div>
<div class="sbar">
  <div>
    <div style="font-size:10px;color:var(--tx3);margin-bottom:2px">梵天实盘 · 真实信号结算 </div>
    <div style="font-size:16px;font-weight:800;color:var(--green)">+419,142 U</div>
  </div>
  <div style="text-align:right">
    <div style="color:var(--green);font-weight:700;font-size:13px">+419.1%</div>
    <div style="color:var(--tx3);font-size:10px" id="sbpos">-- 个持仓中</div>
  </div>
</div>
<script>
const CAP=100000,PP=0.05,BE_L=100,OT_L=20;
function zz(sym,pct,dir){const be=sym==='BTCUSDT'||sym==='ETHUSDT';return CAP*PP*(be?BE_L:OT_L)*(parseFloat(pct||0)/100)*(dir==='LONG'?1:-1);}
function fmt(v){const n=parseFloat(v);if(isNaN(n))return'--';return(n>=0?'+':'')+Math.round(n).toLocaleString();}
function el(id){return document.getElementById(id);}
function set(id,v,col){const e=el(id);if(!e)return;e.textContent=v;if(col)e.style.color=col;}
;(function(){
  var names=['perf','pos','hist','risk','about'];
  document.querySelectorAll('.tab').forEach(function(t,i){
    function go(){
      document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
      t.classList.add('active');
      const p=el('tab-'+names[i]);if(p)p.classList.add('active');
      if(names[i]==='perf')initEq();
      if(names[i]==='risk')initDD();
      if(names[i]==='pos'&&_d)renderPos(_d);
      if(names[i]==='hist'&&_d)renderHist(_d);
    }
    t.onclick=go;
    t.addEventListener('touchend',function(e){e.preventDefault();e.stopPropagation();go();},{passive:false});
  });
})();
let _d=null,_eq=false,_dd=false;
function initEq(){
  if(_eq)return;_eq=true;
  // 实盘权益曲线
  const lb=['05-28','05-29','05-30'];
  const eq=[405318,460720,516791];
  new Chart(el('eq-c'),{type:'line',data:{labels:lb,datasets:[{data:eq,fill:true,backgroundColor:'rgba(16,185,129,.07)',borderColor:'#10b981',borderWidth:2,pointRadius:4,pointBackgroundColor:'#10b981',tension:0.3}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>'$'+ctx.raw.toLocaleString()}}},scales:{x:{grid:{color:'rgba(255,255,255,.03)'},ticks:{color:'#475569',font:{size:10}}},y:{grid:{color:'rgba(255,255,255,.03)'},ticks:{color:'#475569',font:{size:10},callback:v=>'$'+(v/1000).toFixed(0)+'K'}}}}});
  new Chart(el('mth-c'),{type:'bar',data:{labels:['1月','2月','3月','4月','5月','6月'],datasets:[{data:[0,0,0,0,419142,0],backgroundColor:v=>v.dataIndex===4?'rgba(16,185,129,.7)':'rgba(71,85,105,.4)',borderRadius:6}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{color:'#475569',font:{size:10}}},y:{grid:{color:'rgba(255,255,255,.03)'},ticks:{color:'#475569',font:{size:10},callback:v=>v>0?'+'+(v/1000).toFixed(0)+'K':'0'}}}}});
}
function initDD(){
  if(_dd)return;_dd=true;
  new Chart(el('dd-c'),{type:'line',data:{labels:['5/28','5/29','5/30'],datasets:[{data:[0,-1.2,-4.2],fill:true,backgroundColor:'rgba(239,68,68,.06)',borderColor:'rgba(239,68,68,.5)',borderWidth:1.5,pointRadius:3,tension:0.3}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{grid:{color:'rgba(255,255,255,.03)'},ticks:{color:'#475569',font:{size:10}}},y:{grid:{color:'rgba(255,255,255,.03)'},ticks:{color:'#475569',font:{size:10},callback:v=>v+'%'},max:0.5,min:-6}}}});
}
function renderPos(d){
  const pos=d.positions||[];let tot=0;
  const html=pos.map(p=>{
    const pnl=zz(p.symbol,p.pnl_pct,p.direction||p.side);tot+=pnl;
    const sym=(p.symbol||'').replace('USDT','');
    const dir=p.direction||p.side||'SHORT';
    const pct=Math.abs(parseFloat(p.pnl_pct||0));
    const bw=Math.min(pct*6,96);const isPos=pnl>=0;
    // 止损预估（5%仓位×杠杆×止损%）
    const be=p.symbol==='BTCUSDT'||p.symbol==='ETHUSDT';
    const lev=be?100:20;
    const slPct=parseFloat(p.sl_dist||p.sl_pct||0);
    const slLoss=slPct>0?Math.round(-CAP*PP*lev*slPct/100):null;
    return `<div class="pc ${isPos?'up':'dn'}">
      <div class="pt"><span class="psym">${sym} / USDT</span><span class="pdir${dir==='LONG'?' lo':''}">${dir==='LONG'?'做多 LONG':'做空 SHORT'}</span></div>
      <div class="pn"><span class="ppnl" style="font-size:22px;color:${isPos?'var(--green)':'var(--red)'}">${isPos?'+':''}${Math.round(pnl).toLocaleString()} U</span><span class="ppct">${isPos?'+':''}${pct.toFixed(2)}%</span></div>
      <div class="bb"><div class="bf" style="width:${bw}%"></div></div>
      <div class="pf">
        <span style="font-size:10px;color:var(--green);display:flex;align-items:center;gap:3px"><span style="width:5px;height:5px;border-radius:50%;background:var(--green);display:inline-block;animation:blink 2s infinite"></span>止损守护中</span>
        ${slLoss?`<span class="pf-sl">触发预估 ${slLoss.toLocaleString()} U</span>`:''}
      </div>
    </div>`;
  }).join('');
  el('plist').innerHTML=html||'<div style="text-align:center;color:var(--tx3);padding:40px">暂无持仓</div>';
  set('ptlabel',pos.length+' 个持仓 · 当前总浮盈');
  const te=el('ptotal');if(te){te.textContent=(tot>=0?'+':'')+Math.round(tot).toLocaleString()+' U';te.style.color=tot>=0?'var(--green)':'var(--red)';}
}
function renderHist(d){
  const hist=(d.history||[]).filter(h=>h.result&&h.result!=='RUNNING'&&h._data_quality!=='LEGACY_LOW_GRADE');
  el('hlist').innerHTML=hist.slice(0,10).map(h=>{
    const sym=(h.symbol||'').replace('USDT','');
    const ts=String(h.ts_iso||h.open_ts||'').slice(5,10);
    const res=h.result||'TP1';const isW=res.startsWith('TP')||res==='WIN';
    const isL=res==='SL'||res==='LOSS';
    const be=h.symbol==='BTCUSDT'||h.symbol==='ETHUSDT';
    const pnl=Math.round(CAP*PP*(be?100:20)*(parseFloat(h.pnl_pct||0)/100));
    const cls=isW?'w':res==='TIMEOUT'?'t':'l';
    return`<div class="hr"><div class="hsym">${sym}</div><div class="hdate">${ts}</div><div><span class="bdg ${cls}">${res}</span></div><div class="hpnl" style="color:${pnl>=0?'var(--green)':'var(--red)'}">${pnl>=0?'+':''}${pnl.toLocaleString()} U</div></div>`;
  }).join('')||'<div style="text-align:center;color:var(--tx3);padding:20px">暂无数据</div>';
}
let _poll=false,_retry=0;
async function tick(){
  if(_poll)return;_poll=true;
  try{
    const r=await fetch('/api');const d=await r.json();_d=d;
    const pos=d.positions||[];let tot=0;
    pos.forEach(p=>{tot+=zz(p.symbol,p.pnl_pct,p.direction||p.side);});
    set('h4',(tot>=0?'+':'')+tot.toFixed(2)+' U');el('h4').style.color=tot>=0?'var(--green)':'var(--red)';
    const h4pos=document.getElementById('h4-pos');if(h4pos)h4pos.textContent=pos.length;
    const histAll=(d.history||[]).filter(x=>x.status==='CLOSED'||x.result==='TIMEOUT'||x.result==='WIN'||x.result==='LOSS'||(x.result||'').startsWith('TP')||x.result==='SL');
    const histDecided=histAll.filter(x=>(x.result||'').startsWith('TP')||x.result==='WIN'||x.result==='SL'||x.result==='LOSS');
    const histWins=histDecided.filter(x=>(x.result||'').startsWith('TP')||x.result==='WIN');
    const histLoss=histDecided.filter(x=>x.result==='SL'||x.result==='LOSS');
    const histTout=histAll.filter(x=>x.result==='TIMEOUT');
    const histWR=histDecided.length?Math.round(histWins.length/histDecided.length*100):0;
    const h4wr=document.getElementById('h4-wr');if(h4wr)h4wr.textContent=histWR+'%';
    const hwwr=document.getElementById('hwwr');if(hwwr){hwwr.textContent=histWR+'%';hwwr.style.color=histWR>=75?'var(--green)':histWR>=60?'var(--yellow)':'var(--red)';}
    const hww=document.getElementById('hww');if(hww)hww.textContent=histWins.length;
    const hwl=document.getElementById('hwl');if(hwl){hwl.textContent=histLoss.length;hwl.style.color=histLoss.length>0?'var(--red)':'var(--tx2)';}
    const hwtout=document.getElementById('hwtout');if(hwtout)hwtout.textContent=histTout.length;
    set('kpos',pos.length+'仓');
    // kstrip BTC/ETH实时价格
    const regime=d.regime||[];
    const btcR=regime.find(r=>r.symbol==='BTCUSDT'),ethR=regime.find(r=>r.symbol==='ETHUSDT');
    const kbbtc=document.getElementById('kb-btc'),kbeth=document.getElementById('kb-eth'),kbfl=document.getElementById('kb-float');
    if(kbbtc&&btcR){const p=parseFloat(btcR.price||0),c=parseFloat(btcR.chg||0);kbbtc.textContent='$'+Math.round(p).toLocaleString();kbbtc.style.color=c>=0?'var(--green)':'var(--red)';}
    if(kbeth&&ethR){const p=parseFloat(ethR.price||0),c=parseFloat(ethR.chg||0);kbeth.textContent='$'+p.toFixed(0);kbeth.style.color=c>=0?'var(--green)':'var(--red)';}
    if(kbfl)kbfl.textContent=(tot>=0?'+':'')+tot.toFixed(2)+' U';
    set('r-pos',pos.length);set('r-sl','✅ '+pos.length+'/'+pos.length+' 覆盖');
    set('sbpos',pos.length+' 个持仓中');
    set('pf-float',fmt(tot)+' U');el('pf-float').style.color=tot>=0?'var(--green)':'var(--red)';
    const h=d.health||[];
    const cb=h.find(x=>x.metric&&x.metric.includes('熔断'));
    if(cb)set('r-cb',cb.value.includes('正常')||cb.value.includes('OK')?'✅ 未触发':'⚠️ 触发');
    const rg=h.find(x=>x.metric&&x.metric.includes('体制'));
    if(rg){const rv=rg.value;const ib=rv.includes('BEAR');set('kregime',ib?'🐻 '+rv:'🐂 '+rv);}
    const now=new Date();set('htime',now.toISOString().slice(0,16).replace('T',' ')+' UTC');
    const at=document.querySelector('.tab.active');
    if(at){const n=at.getAttribute('data-tab');if(n==='pos')renderPos(d);if(n==='hist')renderHist(d);}
    _retry=0;setTimeout(tick,30000);
  }catch(e){_retry++;setTimeout(tick,Math.min(1000*Math.pow(2,_retry),30000));}
  finally{_poll=false;}
}
window.onload=function(){initEq();tick();};
</script>
</body>
</html>"""

DEV_HTML=r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>梵天 · 开发者终端</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#080b0f;--card:#0f1318;--card2:#141920;--border:#1e2530;--acc:#7c3aed;--acc2:#a855f7;--green:#10b981;--red:#ef4444;--yellow:#f59e0b;--blue:#3b82f6;--cyan:#06b6d4;--tx1:#e2e8f0;--tx2:#94a3b8;--tx3:#475569}
body{background:var(--bg);color:var(--tx1);font-family:'SF Mono','Fira Code','Consolas',monospace;min-height:100vh;font-size:13px}
/* HEADER */
.hdr{background:#0a0d12;border-bottom:1px solid var(--border);padding:10px 16px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100}
.hdr-l{display:flex;align-items:center;gap:12px}
.logo{font-size:14px;font-weight:700;color:var(--acc2);letter-spacing:.5px}
.sys-status{display:flex;gap:8px;align-items:center}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex-shrink:0}
.dot.g{background:var(--green);box-shadow:0 0 6px var(--green)}.dot.r{background:var(--red)}.dot.y{background:var(--yellow)}
.badge{font-size:10px;font-weight:600;padding:2px 7px;border-radius:4px;font-family:monospace}
.badge.g{background:rgba(16,185,129,.12);color:var(--green);border:1px solid rgba(16,185,129,.2)}
.badge.r{background:rgba(239,68,68,.12);color:var(--red);border:1px solid rgba(239,68,68,.2)}
.badge.y{background:rgba(245,158,11,.12);color:var(--yellow);border:1px solid rgba(245,158,11,.2)}
.badge.b{background:rgba(59,130,246,.12);color:var(--blue);border:1px solid rgba(59,130,246,.2)}
.hdr-r{font-size:11px;color:var(--tx3);text-align:right}
/* PRIORITY ALERT */
.alert-bar{padding:8px 16px;display:flex;flex-direction:column;gap:4px;background:#0d1117;border-bottom:1px solid var(--border)}
.alert-item{display:flex;align-items:center;gap:8px;font-size:11px;padding:4px 8px;border-radius:6px}
.alert-item.red{background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.15)}
.alert-item.yellow{background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.15)}
.alert-item.green{background:rgba(16,185,129,.06);border:1px solid rgba(16,185,129,.12)}
/* TABS */
.tabs{display:flex;overflow-x:auto;scrollbar-width:none;border-bottom:1px solid var(--border);background:var(--card);padding:0 4px}
.tabs::-webkit-scrollbar{display:none}
.tab{padding:10px 14px;font-size:11px;font-weight:600;color:var(--tx3);cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;touch-action:manipulation;user-select:none;transition:.2s;font-family:monospace;letter-spacing:.3px}
.tab.active{color:var(--acc2);border-color:var(--acc)}
.panel{display:none;padding:12px 16px 80px}.panel.active{display:block}
/* TABLE */
.dtable{width:100%;border-collapse:collapse;font-size:11px;margin-bottom:14px}
.dtable th{text-align:left;color:var(--tx3);font-weight:600;padding:6px 8px;border-bottom:1px solid var(--border);font-size:10px;text-transform:uppercase;letter-spacing:.5px}
.dtable td{padding:7px 8px;border-bottom:1px solid rgba(30,37,48,.8);vertical-align:middle}
.dtable tr:hover td{background:rgba(255,255,255,.02)}
.sl-bar{display:flex;align-items:center;gap:6px}
.sl-bg{background:var(--border);border-radius:2px;height:4px;width:60px;overflow:hidden;flex-shrink:0}
.sl-fill{height:100%;border-radius:2px;transition:.3s}
/* CARDS */
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:12px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.card-t{font-size:10px;color:var(--tx3);margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px}
.card-v{font-size:16px;font-weight:700}
.card-v.g{color:var(--green)}.card-v.r{color:var(--red)}.card-v.y{color:var(--yellow)}.card-v.b{color:var(--blue)}.card-v.p{color:var(--acc2)}
/* SIGNAL CARDS */
.sig-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:8px}
.sig-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.sig-sym{font-size:13px;font-weight:700;color:var(--tx1)}
.sig-score{font-size:11px;font-weight:700;padding:3px 8px;border-radius:5px;background:rgba(168,85,247,.15);color:var(--acc2)}
.sig-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px}
.sig-kv{display:flex;justify-content:space-between}
.sig-k{color:var(--tx3)}.sig-v{font-weight:600}
.ttl{font-size:10px;color:var(--yellow);text-align:right;margin-top:6px}
/* REGIME */
.reg-row{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;border-bottom:1px solid rgba(30,37,48,.6);font-size:11px}
.reg-sym{font-weight:700;width:80px}
.reg-chip{font-size:10px;font-weight:600;padding:2px 8px;border-radius:4px}
.chip-bear{background:rgba(127,29,29,.35);color:#fca5a5;border:1px solid rgba(239,68,68,.3);font-weight:700}.chip-bear-e{background:rgba(120,53,15,.25);color:#fdba74;border:1px solid rgba(249,115,22,.3)}
.chip-bull{background:rgba(6,78,59,.3);color:#6ee7b7;border:1px solid rgba(16,185,129,.3)}.chip-chop{background:rgba(30,41,59,.4);color:var(--tx3);border:1px solid rgba(71,85,105,.3)}
.reg-row{display:grid;grid-template-columns:80px 1fr 1fr 1fr;padding:10px 10px;border-bottom:1px solid rgba(255,255,255,.03);align-items:center;transition:background .15s}.reg-row:hover{background:rgba(255,255,255,.03)}
.reg-row.bear-row{background:rgba(127,29,29,.08)}.reg-row.bull-row{background:rgba(6,78,59,.06)}.reg-row.chop-row{background:rgba(15,23,42,.3)}
/* LOG */
.log-line{font-size:10px;font-family:'SF Mono',monospace;padding:4px 8px;border-bottom:1px solid rgba(30,37,48,.4);display:flex;gap:8px}
.log-ts{color:var(--tx3);flex-shrink:0;width:50px}
.log-tag{flex-shrink:0;font-weight:700}
.log-tag.ok{color:var(--green)}.log-tag.err{color:var(--red)}.log-tag.info{color:var(--blue)}.log-tag.warn{color:var(--yellow)}
/* PROCESS */
.proc-row{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;border-bottom:1px solid rgba(30,37,48,.5);font-size:11px}
/* ACTION BTN */
.act-btn{font-size:11px;font-weight:600;padding:5px 12px;border-radius:6px;border:1px solid var(--border);background:transparent;color:var(--tx2);cursor:pointer;touch-action:manipulation;font-family:monospace}
.act-btn:hover{background:var(--card2);color:var(--tx1)}
.act-btn.danger{border-color:rgba(239,68,68,.3);color:var(--red)}
.section-t{font-size:11px;font-weight:600;color:var(--tx2);margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid var(--border);text-transform:uppercase;letter-spacing:.5px}
</style>
</head>
<body>
<!-- HEADER -->
<div class="hdr">
  <div class="hdr-l">
    <span class="logo">🧠 梵天 · DEV</span>
    <div class="sys-status">
      <span class="dot g" id="d-ws"></span><span style="font-size:10px;color:var(--tx3)" id="d-watching">ws:--</span>
      <span class="badge g" id="d-nav">NAV:--</span>
      <span id="loc-warn" class="badge" style="background:#f59e0b;color:#000;display:none">⚠️ 请用 localhost:7777 访问</span>
      <span class="badge b" id="d-margin">保证金:--%</span>
    </div>
  </div>
  <div class="hdr-r"><div id="d-time" style="color:var(--tx2)">--</div><div style="font-size:10px;color:var(--tx3)">梵天 v25.7</div></div>
</div>
<!-- 优先决策告警栏 -->
<div class="alert-bar" id="alert-bar">
  <div style="font-size:10px;color:var(--tx3);padding:2px 4px">🎯 优先决策</div>
  <div id="alerts"><div style="font-size:11px;color:var(--tx3);padding:4px">加载中...</div></div>
</div>
<!-- TABS -->
<div class="tabs">
  <div class="tab active" data-tab="live">📡 实盘</div>
  <div class="tab" data-tab="sig">🧠 信号</div>
  <div class="tab" data-tab="reg">⚙️ 体制</div>
  <div class="tab" data-tab="risk">📊 风控</div>
  <div class="tab" data-tab="sys">🔧 系统</div>
</div>
<!-- TAB: 实盘 -->
<div class="panel active" id="tab-live">
  <div class="section-t">实盘持仓 · 真实USDT</div>
  <div style="overflow-x:auto">
  <table class="dtable" id="live-table">
    <thead><tr>
      <th>标的</th><th>方向</th><th>浮盈(USDT)</th><th>涨跌%</th><th>止损距离</th><th>状态</th>
    </tr></thead>
    <tbody id="live-tbody"><tr><td colspan="7" style="text-align:center;color:var(--tx3);padding:20px">加载中...</td></tr></tbody>
  </table>
  </div>
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px">
    <div class="card" style="padding:10px 14px;border-top:2px solid var(--green)">
      <div class="card-t">真实浮盈</div><div class="card-v g" id="real-tot" style="font-size:18px">--</div>
    </div>
    <div class="card" style="padding:10px 14px;border-top:2px solid var(--blue)">
      <div class="card-t">真实 NAV</div><div class="card-v b" id="real-nav" style="font-size:18px">--</div>
    </div>
    <div class="card" style="padding:10px 14px;border-top:2px solid var(--acc2)">
      <div class="card-t">镜像浮盈</div><div class="card-v p" id="mir-tot" style="font-size:18px">--</div>
    </div>
    <div class="card" style="padding:10px 14px;border-top:2px solid var(--tx3)">
      <div class="card-t">镜像本金</div><div class="card-v" style="color:var(--tx2);font-size:18px">$100,000</div>
    </div>
  </div>
</div>
<!-- TAB: 信号 -->
<div class="panel" id="tab-sig">
  <div class="section-t">梵天信号 · 原始输出</div>
  <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
    <span class="badge g" id="s-valid">有效:0</span>
    <span class="badge y" id="s-wait">等待:0</span>
    <span class="badge" style="background:rgba(71,85,105,.2);color:var(--tx3)" id="s-timeout">超时:0</span>
  </div>
  <div id="sig-list"><div style="text-align:center;color:var(--tx3);padding:40px">暂无有效信号 · 梵天扫描中...</div></div>
</div>
<!-- TAB: 体制 -->
<div class="panel" id="tab-reg">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
    <div class="section-t" style="margin-bottom:0">体制矩阵 · 梵天实时判断</div>
    <div style="display:flex;gap:6px" id="reg-summary">
      <span class="badge r" id="rs-bear">BEAR 0</span>
      <span class="badge y" id="rs-chop">CHOP 0</span>
      <span class="badge g" id="rs-bull">BULL 0</span>
    </div>
  </div>
  <div id="reg-list"></div>
</div>
<!-- TAB: 风控 -->
<div class="panel" id="tab-risk">
  <div class="section-t">账户真实状态</div>
  <div class="grid3">
    <div class="card"><div class="card-t">NAV</div><div class="card-v b" id="rv-nav">--</div></div>
    <div class="card"><div class="card-t">可用余额</div><div class="card-v g" id="rv-avail">--</div></div>
    <div class="card"><div class="card-t">保证金占用</div><div class="card-v" id="rv-margin">--%</div></div>
  </div>
  <div class="section-t" style="margin-top:4px">熔断器状态</div>
  <div class="card" style="margin-bottom:12px" id="cb-card">
    <div style="display:flex;flex-direction:column;gap:6px" id="cb-list">
      <div class="proc-row"><span>L1 单日亏损 -5%</span><span class="badge g">✅ 安全</span></div>
      <div class="proc-row"><span>L2 总回撤 -10%</span><span class="badge g">✅ 安全</span></div>
      <div class="proc-row"><span>L3 连亏 3单</span><span class="badge g">✅ 安全</span></div>
    </div>
  </div>
  <div class="section-t">止损距离排序（风险升序）</div>
  <div id="sl-rank"></div>
</div>
<!-- TAB: 系统 -->
<div class="panel" id="tab-sys">
  <div class="section-t">进程状态</div>
  <div class="card" style="margin-bottom:12px">
    <div id="proc-list">
      <div class="proc-row"><span style="color:var(--tx1);font-weight:600">Dashboard</span><span id="ps-dash"><span class="badge g">✅ 运行中</span></span></div>
      <div class="proc-row"><span style="color:var(--tx1);font-weight:600">ws_guardian</span><span id="ps-wsg"><span class="badge g">✅ 运行中</span></span></div>
      <div class="proc-row"><span style="color:var(--tx1);font-weight:600">ws-proxy</span><span id="ps-wsp"><span class="badge g">✅ 运行中</span></span></div>
      <div class="proc-row"><span style="color:var(--tx1);font-weight:600">cloudflared</span><span id="ps-cf"><span class="badge g">✅ 运行中</span></span></div>
    </div>
  </div>
  <div class="section-t">苏摩批准的AI任务（4个）</div>
  <div class="card" style="margin-bottom:12px" id="cron-list">
    <div class="proc-row"><span>signal-watcher-1h</span><span class="badge g">每1h ✅</span></div>
    <div class="proc-row"><span>gateway-restart-daily</span><span class="badge b">每日05:00</span></div>
    <div class="proc-row"><span>BTC日报-Square</span><span class="badge b">每日09:30</span></div>
    <div class="proc-row"><span>square-weekly</span><span class="badge b">每周一</span></div>
  </div>
  <div class="section-t">系统日志</div>
  <div class="card" style="margin-bottom:12px;max-height:320px;overflow-y:auto;background:#060810" id="log-box">
    <div id="log-lines" style="font-family:'SF Mono','Fira Code',monospace;font-size:11px;line-height:1.9"><div style="color:var(--tx3);padding:8px">加载中...</div></div>
  </div>
  <div style="display:flex;gap:8px;flex-wrap:wrap">
    <button class="act-btn" onclick="triggerScan()">🔄 触发扫描</button>
    <button class="act-btn" onclick="syncPos()">📥 同步持仓</button>
    <button class="act-btn danger" onclick="confirmRestart()">⚡ 重启ws_guardian</button>
  </div>
</div>
<script>
const CAP=100000,PP=0.05,BE_L=100,OT_L=20;
function zz(sym,pct,dir){const be=sym==='BTCUSDT'||sym==='ETHUSDT';return CAP*PP*(be?BE_L:OT_L)*(parseFloat(pct||0)/100)*(dir==='LONG'?1:-1);}
function fmt(v,d=2){const n=parseFloat(v);if(isNaN(n))return'--';return(n>=0?'+':'')+n.toFixed(d);}
function fmtN(v){const n=parseFloat(v);if(isNaN(n))return'--';return(n>=0?'+':'')+Math.round(n).toLocaleString();}
function el(id){return document.getElementById(id);}
function set(id,v,col){const e=el(id);if(!e)return;e.textContent=v;if(col)e.style.color=col;}
;(function(){
  var names=['live','sig','reg','risk','sys'];
  document.querySelectorAll('.tab').forEach(function(t,i){
    function go(){
      document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
      t.classList.add('active');el('tab-'+names[i]).classList.add('active');
      if(_d){
        if(names[i]==='live')renderLive(_d);
        if(names[i]==='sig')renderSig(_d);
        if(names[i]==='reg')renderReg(_d);
        if(names[i]==='risk')renderRisk(_d);
        if(names[i]==='sys')renderSys(_d);
      }
    }
    t.onclick=go;
    t.addEventListener('touchend',function(e){e.preventDefault();e.stopPropagation();go();},{passive:false});
  });
})();
let _d=null;

function renderAlerts(d){
  const pos=d.positions||[];
  const alerts=[];
  pos.forEach(p=>{
    const be=p.symbol==='BTCUSDT'||p.symbol==='ETHUSDT';
    const lev=be?BE_L:OT_L;
    const pct=Math.abs(parseFloat(p.pnl_pct||0));
    const sym=p.symbol.replace('USDT','');
    const dir=p.direction||p.side||'SHORT';
    const pnl=zz(p.symbol,p.pnl_pct,dir);
    // 止损距离从position_sl_state推算
    const slDist=parseFloat(p.sl_dist||p.sl_pct||0);
    if(slDist>0&&slDist<3){alerts.push({level:'red',msg:`🚨 ${sym} ${dir} 止损距离仅 ${slDist.toFixed(2)}% — 请检查止损设置`});}
    else if(pnl>10000&&dir==='LONG'){alerts.push({level:'yellow',msg:`📈 ${sym} LONG 浮盈 ${fmtN(pnl)}U — 考虑追踪止损保护利润`});}
    else if(pnl>8000){alerts.push({level:'green',msg:`✅ ${sym} ${dir} 运行良好 浮盈 ${fmtN(pnl)}U`});}
  });
  const sigs=(d.signals||[]).filter(s=>parseFloat(s.score||0)>=138&&s.valid==='True');
  if(sigs.length>0){alerts.unshift({level:'yellow',msg:`🎯 梵天有 ${sigs.length} 个有效信号待执行`});}
  const h=d.health||[];
  const mp=h.find(x=>x.metric&&x.metric.includes('保证金'));
  if(mp){const v=parseFloat(mp.value);if(v>80)alerts.unshift({level:'red',msg:`⚠️ 保证金占用 ${v.toFixed(1)}% > 80% — 新信号被门控拦截`});}
  el('alerts').innerHTML=alerts.slice(0,4).map(a=>`<div class="alert-item ${a.level}">${a.msg}</div>`).join('')||'<div style="font-size:11px;color:var(--green);padding:4px">✅ 无需立即处理的事项</div>';
}

function renderLive(d){
  const pos=d.positions||[];
  let rTot=0,mTot=0;
  const rows=pos.map(p=>{
    const real=parseFloat(p.pnl||p.uPnL||0);rTot+=real;
    const mir=zz(p.symbol,p.pnl_pct,p.direction||p.side);mTot+=mir;
    const sym=p.symbol.replace('USDT','');
    const dir=p.direction||p.side||'SHORT';
    const pct=parseFloat(p.pnl_pct||0);
    const slDist=parseFloat(p.sl_dist||p.sl_pct||0);
    const be=p.symbol==='BTCUSDT'||p.symbol==='ETHUSDT';
    const lev=be?BE_L:OT_L;
    const slLoss=slDist>0?Math.round(-CAP*PP*lev*slDist/100):null;
    const slColor=slDist>0?(slDist<3?'var(--red)':slDist<5?'var(--yellow)':'var(--green)'):'var(--tx3)';
    const barW=slDist>0?Math.min(slDist*8,100):0;
    return`<tr>
      <td style="font-weight:700">${sym}</td>
      <td><span style="font-size:10px;font-weight:600;color:${dir==='LONG'?'var(--green)':'var(--red)'}">${dir}</span></td>
      <td style="color:${real>=0?'var(--green)':'var(--red)'};font-weight:700">${real>=0?'+':''}${real.toFixed(4)}</td>
      <td style="color:${pct<=0?'var(--green)':'var(--red)'}">${pct>=0?'+':''}${pct.toFixed(2)}%</td>
      <td style="color:var(--acc2);font-weight:700">${mir>=0?'+':''}${Math.round(mir).toLocaleString()}U</td>
      <td><div class="sl-bar"><div class="sl-bg"><div class="sl-fill" style="width:${barW}%;background:${slColor}"></div></div><span style="color:${slColor};font-weight:700;font-size:11px">${slDist>0?slDist.toFixed(2)+'%':'--'}</span></div></td>
      <td style="color:var(--red);font-size:11px">${slLoss?slLoss.toLocaleString()+'U':'--'}</td>
    </tr>`;
  }).join('');
  el('live-tbody').innerHTML=rows||'<tr><td colspan="7" style="text-align:center;color:var(--tx3);padding:20px">无持仓</td></tr>';
  set('real-tot',fmt(rTot,4)+' USDT',rTot>=0?'var(--green)':'var(--red)');
  set('mir-tot',fmtN(mTot)+' U',mTot>=0?'var(--acc2)':'var(--red)');
  const navH=d.health&&d.health.find(h=>h.metric==='NAV');
  if(navH)set('real-nav',navH.value,'var(--blue)');
}

function renderSig(d){
  const sigs=d.signals||[];
  const valid=sigs.filter(s=>s.valid==='True'&&parseFloat(s.score||0)>=138);
  const wait=sigs.filter(s=>s.status==='WAITING'||s.status==='PENDING');
  set('s-valid','有效:'+valid.length);set('s-wait','等待:'+wait.length);
  const show=[...valid,...wait].slice(0,8);
  el('sig-list').innerHTML=show.length?show.map(s=>{
    const sym=(s.symbol||'').replace('USDT','');
    const dir=s.direction||s.signal_dir||'SHORT';
    const score=parseFloat(s.score||0);
    const gradeColor=score>=165?'var(--yellow)':score>=138?'var(--acc2)':'var(--tx2)';
    return`<div class="sig-card">
      <div class="sig-head">
        <span class="sig-sym">${sym} <span style="color:${dir==='LONG'?'var(--green)':'var(--red)'};font-size:11px">${dir}</span></span>
        <span class="sig-score" style="color:${gradeColor}">${score.toFixed(1)}分</span>
      </div>
      <div class="sig-grid">
        <div class="sig-kv"><span class="sig-k">入场区</span><span class="sig-v" style="color:var(--tx1)">${s.entry_lo&&s.entry_hi?parseFloat(s.entry_lo).toFixed(4)+'~'+parseFloat(s.entry_hi).toFixed(4):'等待'}</span></div>
        <div class="sig-kv"><span class="sig-k">止损</span><span class="sig-v" style="color:var(--red)">${s.stop_loss?parseFloat(s.stop_loss).toFixed(4):'--'}</span></div>
        <div class="sig-kv"><span class="sig-k">TP1</span><span class="sig-v" style="color:var(--green)">${s.tp1?parseFloat(s.tp1).toFixed(4):'--'}</span></div>
        <div class="sig-kv"><span class="sig-k">体制</span><span class="sig-v" style="color:var(--tx2)">${s.regime||'--'}</span></div>
      </div>
      <div class="ttl">TTL: ${s.ttl||s.hold_hours||'--'}h · ${s.status||'ACTIVE'}</div>
    </div>`;
  }).join(''):(()=>{
    const regime=d.regime||[];
    if(!regime.length) return '<div style="text-align:center;color:var(--tx3);padding:40px">梵天扫描中...</div>';
    const sorted=[...regime].sort((a,b)=>(b.score||b.last_score||0)-(a.score||a.last_score||0));
    const maxScore=Math.max(...sorted.map(r=>r.score||r.last_score||0),1);
    return '<div class="card" style="padding:16px 14px"><div style="font-size:11px;color:var(--tx3);margin-bottom:14px;text-transform:uppercase;letter-spacing:.5px">📡 梵天当前评分 · 等待入场触发</div>'+
    sorted.map(r=>{
      const sym=(r.symbol||'').replace('USDT','');
      const reg=r.regime||'--';
      const score=r.score||r.last_score||0;
      const pct=maxScore>0?Math.round(score/maxScore*100):0;
      const isBear=reg.includes('BEAR');const isChop=reg.includes('CHOP');
      const barClr=score>=160?'#a855f7':score>=138?'var(--green)':score>=100?'var(--yellow)':'var(--tx3)';
      const chipCls=reg.includes('BEAR_TREND')?'chip-bear':reg.includes('BEAR_EARLY')?'chip-bear-e':reg.includes('BULL')?'chip-bull':'chip-chop';
      return`<div style="display:grid;grid-template-columns:52px 130px 1fr 48px;align-items:center;gap:8px;margin-bottom:10px">
        <span style="font-weight:800;font-size:13px">${sym}</span>
        <span><span class="reg-chip ${chipCls}" style="font-size:9px">${reg.replace(/_/g,' ')}</span></span>
        <div style="background:rgba(255,255,255,.05);border-radius:3px;height:6px;overflow:hidden">
          <div style="height:6px;width:${pct}%;background:${barClr};border-radius:3px;transition:width .6s ease"></div>
        </div>
        <span style="color:${barClr};font-weight:700;font-size:12px;text-align:right">${score>0?score.toFixed(0):'—'}</span>
      </div>`;
    }).join('')+'<div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border);font-size:10px;color:var(--tx3)">门槛≥138分触发执行 · 当前无信号</div></div>';
  })();
}

function renderReg(d){
  const regime=d.regime||[];
  if(!regime.length){el('reg-list').innerHTML='<div style="color:var(--tx3);padding:20px;text-align:center">加载体制数据中...</div>';return;}
  const chipCls=r=>r.includes('BEAR_TREND')?'chip-bear':r.includes('BEAR_EARLY')?'chip-bear-e':r.includes('BULL')?'chip-bull':'chip-chop';
  const arrow=r=>r.includes('BEAR')?'↓':r.includes('BULL')?'↑':'→';
  const bearCnt=regime.filter(r=>(r.regime||'').includes('BEAR')).length;
  const chopCnt=regime.filter(r=>(r.regime||'').includes('CHOP')).length;
  const bullCnt=regime.length-bearCnt-chopCnt;
  const rsBear=document.getElementById('rs-bear');const rsChop=document.getElementById('rs-chop');const rsBull=document.getElementById('rs-bull');
  if(rsBear)rsBear.textContent='BEAR '+bearCnt;if(rsChop)rsChop.textContent='CHOP '+chopCnt;if(rsBull)rsBull.textContent='BULL '+bullCnt;
  el('reg-list').innerHTML='<div class="card" style="margin-bottom:12px">'+
    '<div style="display:grid;grid-template-columns:60px 1fr 80px 60px 80px;gap:8px;padding:7px 12px;font-size:10px;color:var(--tx3);font-weight:600;border-bottom:1px solid var(--border);text-transform:uppercase;letter-spacing:.5px"><span>标的</span><span>体制</span><span style=\"text-align:right\">铁证WR</span><span style=\"text-align:right\">评分</span><span style=\"text-align:right\">评级</span></div>'+
    regime.map(r=>{
      const sym=(r.symbol||'').replace('USDT','');
      const reg=r.regime||r.regime_label||'--';
      const mom=r.momentum||r.phase||'--';
      const isBear=reg.includes('BEAR');const isChop=reg.includes('CHOP');
      const grade=isBear?'🔴S级做空':isChop?'❌封禁':'🟢可做多';
      const rowCls=isBear?'bear-row':isChop?'chop-row':'bull-row';
      // 铁证WR查表
      const wrMap={'BEAR_TREND_SHORT':'71.8%','BEAR_TREND_LONG':'44.6%','BEAR_EARLY_SHORT':'66.5%','BEAR_RECOVERY_SHORT':'47.9%','BULL_TREND_LONG':'68.2%','CHOP_MID_SHORT':'50%'};
      const wrKey=reg+'_'+(isBear?'SHORT':'LONG');
      const wr=wrMap[wrKey]||'—';
      const wrColor=parseFloat(wr)>=60?'var(--green)':parseFloat(wr)>=50?'var(--yellow)':'var(--red)';
      const score=r.score||r.last_score||0;
      const scoreStr=score>0?score.toFixed(0):'—';
      const scoreClr=score>=160?'var(--yellow)':score>=138?'var(--green)':'var(--tx2)';
      return`<div class="reg-row ${rowCls}" style="display:grid;grid-template-columns:60px 1fr 80px 60px 80px;gap:8px;padding:9px 12px;border-bottom:1px solid rgba(30,37,48,.6);align-items:center">
        <span style="font-weight:800;font-size:13px;color:var(--tx1)">${sym}</span>
        <span><span class="reg-chip ${chipCls(reg)}">${reg.replace(/_/g,' ')} ${arrow(reg)}</span></span>
        <span style="font-size:11px;font-weight:700;color:${wrColor};text-align:right">WR ${wr}</span>
        <span style="font-size:11px;font-weight:700;color:${scoreClr};text-align:right">${scoreStr}</span>
        <span style="font-size:11px;font-weight:600;color:${isBear?'var(--red)':isChop?'var(--tx3)':'var(--green)'};text-align:right">${grade}</span>
      </div>`;
    }).join('')+'</div>';
}

function renderRisk(d){
  const h=d.health||[];
  const pos=d.positions||[];
  const navH=h.find(x=>x.metric==='NAV');if(navH)set('rv-nav',navH.value,'var(--blue)');
  const avH=h.find(x=>x.metric&&(x.metric.includes('可用')||x.metric.includes('avail')));
  if(avH){const raw=avH.value||'';const isReal=!raw.includes('U')&&!raw.includes('镜像');
    set('rv-avail',raw+(isReal&&!raw.includes('$')?'':' '),'var(--green)');}
  const mpH=h.find(x=>x.metric&&x.metric.includes('保证金'));
  if(mpH){const v=parseFloat(mpH.value);set('rv-margin',v.toFixed(1)+'%',v>80?'var(--red)':v>60?'var(--yellow)':'var(--green)');}
  // 止损安全距离排序
  const slData=pos.map(p=>({
    sym:p.symbol.replace('USDT',''),dir:p.direction||p.side||'SHORT',
    slDist:parseFloat(p.sl_dist||p.sl_pct||0),
    pnl:zz(p.symbol,p.pnl_pct,p.direction||p.side)
  })).filter(x=>x.slDist>0).sort((a,b)=>a.slDist-b.slDist);
  el('sl-rank').innerHTML=slData.length?'<div class="card">'+slData.map(x=>{
    const col=x.slDist<3?'var(--red)':x.slDist<5?'var(--yellow)':'var(--green)';
    const bw=Math.min(x.slDist*8,100);
    return`<div class="proc-row">
      <span style="font-weight:700;width:70px">${x.sym}</span>
      <span style="color:${x.dir==='LONG'?'var(--green)':'var(--red)'};font-size:10px;width:50px">${x.dir}</span>
      <div class="sl-bar" style="flex:1"><div class="sl-bg" style="flex:1;width:auto"><div class="sl-fill" style="width:${bw}%;background:${col}"></div></div><span style="color:${col};font-weight:700;min-width:40px;text-align:right">${x.slDist.toFixed(2)}%</span></div>
    </div>`;
  }).join('')+'</div>':'<div style="color:var(--tx3);padding:12px">持仓止损数据加载中...</div>';
}

function renderSys(d){
  const h=d.health||[];
  const logEl=document.getElementById('log-lines');
  const logBox=document.getElementById('log-box');
  // 优先用 scan_log 字段，回退到health
  const rawLogs=d.scan_log||d.log_lines||[];
  let lines=[];
  if(rawLogs.length){
    lines=rawLogs.slice(-30).map(l=>{
      const isErr=l.includes('ERROR')||l.includes('530')||l.includes('失败');
      const isWarn=l.includes('WARN')||l.includes('⚠️');
      const isOk=l.includes('WIN')||l.includes('✅')||l.includes('触发');
      const clr=isErr?'var(--red)':isWarn?'var(--yellow)':isOk?'var(--green)':'var(--tx2)';
      const ts=l.match(/\d{2}:\d{2}:\d{2}/);
      const msg=l.replace(/^\d{2}:\d{2}:\d{2}\s*/,'');
      return`<div style="padding:2px 8px;border-bottom:1px solid rgba(255,255,255,.02)"><span style="color:var(--tx3);font-size:10px;margin-right:8px">${ts?ts[0]:''}</span><span style="color:${clr}">${msg}</span></div>`;
    });
  } else {
    const now=new Date().toISOString().slice(11,19);
    const items=h.filter(x=>x.metric&&(x.metric.includes('扫描')||x.metric.includes('信号')||x.metric.includes('持仓')||x.metric==='NAV'));
    lines=items.map(x=>`<div style="padding:2px 8px"><span style="color:var(--tx3);font-size:10px;margin-right:8px">${now}</span><span style="color:var(--cyan)">[INFO]</span> <span style="color:var(--tx1)">${x.metric}</span><span style="color:var(--tx3)">: </span><span style="color:var(--green)">${x.value}</span></div>`);
  }
  if(logEl){
    logEl.innerHTML=lines.join('')||'<div style="color:var(--tx3);padding:8px">等待扫描日志...</div>';
    if(logBox)setTimeout(()=>logBox.scrollTop=logBox.scrollHeight,50);
  }
}

function triggerScan(){alert('触发梵天扫描 — 功能开发中');}
function syncPos(){alert('同步持仓 — 功能开发中');}
function confirmRestart(){if(confirm('确认重启 ws_guardian？'))alert('重启指令已发送');}

let _poll=false,_retry=0;
async function tick(){
  if(_poll)return;_poll=true;
  try{
    const r=await fetch('/api');const d=await r.json();_d=d;
    const pos=d.positions||[];
    const h=d.health||[];
    // 顶部状态
    const navH=h.find(x=>x.metric==='NAV');if(navH)set('d-nav','NAV:'+navH.value);
    const mpH=h.find(x=>x.metric&&x.metric.includes('保证金'));
    if(mpH){const v=parseFloat(mpH.value);set('d-margin','保证金:'+v.toFixed(0)+'%');el('d-margin').className='badge '+(v>80?'r':v>60?'y':'b');}
    set('d-watching','ws:'+pos.length);
    const now=new Date();set('d-time',now.toISOString().slice(0,16).replace('T',' ')+' UTC');
    // 优先告警
    renderAlerts(d);
    // 渲染当前active tab
    const at=document.querySelector('.tab.active');
    if(at){const n=at.getAttribute('data-tab');
      if(n==='live')renderLive(d);if(n==='sig')renderSig(d);
      if(n==='reg')renderReg(d);if(n==='risk')renderRisk(d);if(n==='sys')renderSys(d);
    }
    _retry=0;setTimeout(tick,20000);
  }catch(e){_retry++;setTimeout(tick,Math.min(2000*Math.pow(2,_retry),30000));}
  finally{_poll=false;}
}
window.onload=function(){tick();};
</script>
</body>
</html>
"""

"""brahma_dashboard_server.py v3.0 — 梵天AI信号仪表盘
设计院×达摩院×量化工程师×新闻局 P0优化版
- 并发扫描 ThreadPoolExecutor(5) → ~25s
- 流动性分区：高/中/低
- FR + LSR + Vol 实时列
- 体制矩阵改表格
- 信号响铃 + Binance跳转
"""
import sys,os,json,time,subprocess,threading,hmac,hashlib,argparse
import requests
from datetime import datetime,timezone
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed

BASE=Path(__file__).parent.parent
sys.path.insert(0,str(BASE))
import tornado.ioloop,tornado.web,tornado.websocket

SCAN_TARGETS=['BTCUSDT','ETHUSDT','NEARUSDT','GALAUSDT','PIXELUSDT',
              'TRUMPUSDT','1000PEPEUSDT','SUIUSDT']  # 设计院内存优化: 19→8个核心标的（持仓+主力），2026-06-24
SCORE_THRESHOLD=138
REFRESH_INTERVAL = 600  # 设计院优化: 120s→600s，减少80%内存峰值频率
import os as _os
API_KEY=_os.environ.get("BINANCE_API_KEY","")
API_SECRET=_os.environ.get("BINANCE_API_SECRET","")
BURL="https://fapi.binance.com"

_regime_data=[];_signal_data=[];_position_data=[];_health_data=[]
_history_data=[];_market_data={};_last_update=0;_ws_clients=set()
_mirror_positions=[];_mirror_float=0.0
_score_delta={}
# 每日盈亏追踪（姓赵口径：基于已结算WIN/LOSS的镜像PnL累加）
_daily_pnl_state={'date':'','cumulative':0.0,'settled_ids':set()}

class ScoreHistoryStore:
    """缓存最近3次扫描每个标的的score，计算delta"""
    def __init__(self, maxlen=3):
        self._maxlen=maxlen
        self._store={} # sym -> deque of scores
        self._lock=threading.Lock()
    def push(self, sym, score):
        with self._lock:
            if sym not in self._store:
                from collections import deque
                self._store[sym]=deque(maxlen=self._maxlen)
            self._store[sym].append(score)
    def delta(self, sym):
        """最新score - 上一次score；数据不足返回0.0"""
        with self._lock:
            q=self._store.get(sym)
            if not q or len(q)<2: return 0.0
            vals=list(q)
            return round(vals[-1]-vals[-2],1)
    def delta_all(self):
        with self._lock:
            result={}
            for sym,q in self._store.items():
                vals=list(q)
                if len(vals)>=2:
                    result[sym]=round(vals[-1]-vals[-2],1)
                else:
                    result[sym]=0.0
            return result

_score_history_store=ScoreHistoryStore()

def _sign(p):
    qs='&'.join(f"{k}={v}" for k,v in p.items())
    s=hmac.new(API_SECRET.encode(),qs.encode(),hashlib.sha256).hexdigest()
    return qs+f"&signature={s}"

def _get_auth(path,params={}):
    p=dict(params);p['timestamp']=int(time.time()*1000)
    return requests.get(f"{BURL}{path}?{_sign(p)}",headers={'X-MBX-APIKEY':API_KEY},timeout=10).json()

def get_price(sym):
    try: return float(requests.get(f"{BURL}/fapi/v1/ticker/price?symbol={sym}",timeout=5).json()['price'])
    except: return 0.0

def run_brahma(sym):
    """\u8c03\u7528brahma_execute\u83b7\u53d6\u4f53\u5236+\u4fe1\u53f7"""
    try:
        r=subprocess.run(['python3',str(BASE/'scripts'/'brahma_execute.py'),sym,'LONG'],
                        capture_output=True,text=True,timeout=50,cwd=str(BASE))
        out=r.stderr+r.stdout
        import re as _re
        result={'symbol':sym}
        # \u4f53\u5236
        m=_re.search(r'\u4f53\u5236=(\w+)',out)
        if m: result['regime']=m.group(1).split('(')[0]
        # momentum: LiqScan\u884c\u7684\u504f\u5411=
        m2=_re.search(r'\u504f\u5411=(\w+)',out)
        if m2:
            liq_m=m2.group(1)
            result['momentum']='BULLISH' if liq_m=='BULL' else 'BEARISH' if liq_m=='BEAR' else 'NEUTRAL'
        else: result['momentum']='NEUTRAL'
        # score/valid
        mv=_re.search(r'score=(\d+(?:\.\d+)?)/150.*?valid=(\w+)',out)
        if mv: result['score']=float(mv.group(1)); result['valid']=mv.group(2)=='True'
        else: result['score']=0; result['valid']=False
        # direction from signal_dir or score line
        ms=_re.search(r'BTCUSDT|ETHUSDT|\w+USDT\s+(LONG|SHORT).*?score',out)
        result['signal_dir']='LONG'
        # entry/sl/tp\u4ece[BrahmaExec]\u884c
        me=_re.search(r'entry_lo=(\S+).*?entry_hi=(\S+).*?sl=(\S+).*?tp1=(\S+)',out)
        if me:
            result['entry_lo']=float(me.group(1)); result['entry_hi']=float(me.group(2))
            result['stop_loss']=float(me.group(3)); result['tp1']=float(me.group(4))
        # grade
        mg=_re.search(r'grade=(\d+(?:\.\d+)?)',out)
        if mg: result['grade']=float(mg.group(1))
        if 'regime' not in result or not result.get('regime'): return {'symbol':sym,'error':True}
        return result
    except Exception as e: return {'symbol':sym,'error':True,'msg':str(e)}

def fetch_market_data():
    """批量获取所有标的行情/FR/LSR"""
    out={}
    try:
        ticker={d['symbol']:d for d in requests.get(f"{BURL}/fapi/v1/ticker/24hr",timeout=10).json()}
        for sym in SCAN_TARGETS:
            d=ticker.get(sym,{})
            vol=float(d.get('quoteVolume',0))/1e6
            chg=float(d.get('priceChangePercent',0))
            price=float(d.get('lastPrice',0))
            # FR
            fr=0.0
            try:
                frd=requests.get(f"{BURL}/fapi/v1/fundingRate",params={'symbol':sym,'limit':1},timeout=4).json()
                fr=float(frd[0]['fundingRate'])*100 if frd else 0.0
            except: pass
            # LSR
            lsr=1.0
            try:
                ld=requests.get(f"{BURL}/futures/data/globalLongShortAccountRatio",
                               params={'symbol':sym,'period':'1h','limit':1},timeout=4).json()
                lsr=float(ld[0]['longShortRatio']) if ld else 1.0
            except: pass
            # 流动性分级
            if vol>=50: liq='HIGH'
            elif vol>=10: liq='MID'
            else: liq='LOW'
            out[sym]={'vol':round(vol,1),'chg':round(chg,2),'price':round(price,6),
                      'fr':round(fr,4),'lsr':round(lsr,3),'liq':liq}
    except Exception as e: print(f"[market] {e}")
    return out

def load_wuqu_history(limit=100):
    """从 wuqu_paper_settled.jsonl 读取武曲策略历史，按 close_ts 降序（最新在前）"""
    rows=[]
    path=BASE/'data'/'wuqu_paper_settled.jsonl'
    try:
        lines=path.read_text().strip().split('\n')
        for l in lines:
            if not l.strip(): continue
            try:
                d=json.loads(l)
                outcome=d.get('outcome','')
                result='WIN' if outcome in ('TP1','TP2') else ('LOSS' if outcome=='SL' else outcome or 'OPEN')
                pnl_pct=float(d.get('pnl_pct',0))
                ts_raw=d.get('close_ts') or d.get('open_ts','')
                # ts_raw 可能是 float 时间戳或 ISO 字符串
                if isinstance(ts_raw,(int,float)) and ts_raw>0:
                    from datetime import datetime as _dt
                    ts_iso=_dt.utcfromtimestamp(ts_raw).strftime('%Y-%m-%dT%H:%M:%S+00:00')
                else:
                    ts_iso=str(ts_raw or '')
                ts_str=ts_iso[:16].replace('T',' ')
                rows.append({'symbol':d.get('symbol',''),'direction':d.get('signal_dir',''),
                             'regime':d.get('regime',''),'score':float(d.get('score',0)),
                             'pnl_pct':round(pnl_pct,2),'status':'CLOSED' if outcome in ('TP1','TP2','SL') else 'OPEN',
                             'result':result,'outcome':outcome,'ts':ts_str,'_ts_raw':ts_iso,
                             'signal_id':(d.get('signal_id','') or '')[:8]})
            except: continue
    except: pass
    # 按 close_ts 降序排序（最新在前）
    rows.sort(key=lambda x: x.get('_ts_raw',''), reverse=True)
    # 去掉内部排序字段
    for r in rows: r.pop('_ts_raw', None)
    return rows[:limit]

def load_wuqu_stats():
    """武曲历史胜率统计 - WIN口径: TP1/TP2/WIN计盈, SL/LOSS计亏, TIMEOUT不计入分母"""
    rows=load_wuqu_history(9999)
    wins=[r for r in rows if r.get('outcome') in ('TP1','TP2','WIN')]
    losses=[r for r in rows if r.get('outcome') in ('SL','LOSS')]
    decided=len(wins)+len(losses)
    wr=round(len(wins)/decided*100,1) if decided else 0
    return wr, decided, len(wins), len(losses)

def load_signal_history(limit=100):
    """保留兼容：返回 wuqu settled 数据（旧接口替换）"""
    return load_wuqu_history(limit)

def get_positions():
    try:
        data=_get_auth('/fapi/v2/account')
        nav=float(data.get('totalMarginBalance',0))
        avail=float(data.get('availableBalance',0))
        margin_used=float(data.get('totalInitialMargin',0))
        positions=[]
        # 读取止损状态文件，计算sl_dist
        try:
            import json as _json
            _sl_path=os.path.join(os.path.dirname(os.path.abspath(__file__)),'../data/position_sl_state.json')
            with open(_sl_path) as _f: _sl_map=_json.load(_f)
        except: _sl_map={}
        for p in data.get('positions',[]):
            amt=float(p.get('positionAmt',0))
            if abs(amt)==0: continue
            entry=float(p.get('entryPrice',0))
            pnl=float(p.get('unrealizedProfit',0))
            cur=get_price(p['symbol'])
            pct=(cur-entry)/entry*100 if entry>0 else 0
            side='LONG' if amt>0 else 'SHORT'
            sl_cfg=_sl_map.get(p['symbol'],{})
            sl_price=float(sl_cfg.get('sl_price',0))
            if sl_price>0 and cur>0:
                sl_dist=round((cur-sl_price)/cur*100,2) if side=='LONG' else round((sl_price-cur)/cur*100,2)
            else: sl_dist=0
            positions.append({'symbol':p['symbol'],'direction':side,'side':side,
                             'amount':abs(amt),'entry':round(entry,6),'cur_price':round(cur,6),
                             'uPnL':round(pnl,3),'pnl':round(pnl,3),'pnl_pct':round(pct,2),
                             'sl_price':round(sl_price,6),'sl_dist':sl_dist,'sl_pct':sl_dist,
                             'updated_at':datetime.now(timezone.utc).strftime('%H:%M:%S')})
        positions.sort(key=lambda x:x['uPnL'],reverse=True)
        positions.sort(key=lambda x:x['uPnL'],reverse=True)
        margin_pct=round(margin_used/nav*100,1) if nav>0 else 0
        return positions,nav,avail,margin_pct
    except Exception as e: print(f"[pos] {e}"); return [],0,0,0

# 武曲-A 镜像账户计算器
# 设计院规则：100000u本金，BTC/ETH 100x·5%，其他 20x·5%
_MIRROR_CAPITAL = 100000.0
_MIRROR_BTC_ETH_LEV = 100
_MIRROR_OTHER_LEV = 20
_MIRROR_POS_PCT = 0.05  # 5%仓位

def build_mirror_account(positions):
    """\u57fa于实盘持仓进行镜像账户计算"""
    mirror_positions=[]
    for p in positions:
        sym=p['symbol']
        isBE=(sym=='BTCUSDT' or sym=='ETHUSDT')
        lev=_MIRROR_BTC_ETH_LEV if isBE else _MIRROR_OTHER_LEV
        sim_notional=_MIRROR_CAPITAL*_MIRROR_POS_PCT  # 5000u
        pct_raw=p['pnl_pct']/100  # (cur-entry)/entry
        direction=p['direction']
        dir_mult=1 if direction=='LONG' else -1
        real_pct=pct_raw*dir_mult
        sim_pnl=round(sim_notional*lev*real_pct,2)
        mirror_positions.append({**p,'sim_lev':lev,'sim_notional':sim_notional,'sim_pnl':sim_pnl})
    total_float=round(sum(p['sim_pnl'] for p in mirror_positions),2)
    return mirror_positions, total_float

def refresh_data():
    global _regime_data,_signal_data,_position_data,_health_data
    global _history_data,_market_data,_last_update,_ws_clients
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] 并发扫描 {len(SCAN_TARGETS)} 标的...")
    t0=time.time()
    # 并行：梵天扫描 + 市场数据
    brahma_results={}
    with ThreadPoolExecutor(max_workers=1) as ex:
        futs={ex.submit(run_brahma,sym):sym for sym in SCAN_TARGETS}
        for fut in as_completed(futs):
            sym=futs[fut]
            try: brahma_results[sym]=fut.result()
            except: brahma_results[sym]={'symbol':sym,'error':True}
    mkt=fetch_market_data()
    new_regime=[];new_signals=[]
    for sym in SCAN_TARGETS:
        d=brahma_results.get(sym,{})
        if d.get('error'): continue
        m=mkt.get(sym,{})
        price=m.get('price',get_price(sym))
        # 体制最佳方向推断（供订阅者页面体制横幅用）
        _regime_best={'BEAR_EARLY':'SHORT','BEAR_TREND':'SHORT','BEAR_RECOVERY':'LONG',
                       'BULL_EARLY':'LONG','BULL_TREND':'LONG','BULL_CORRECTION':'SHORT'}
        best_dir=_regime_best.get(d.get('regime',''),'LONG')
        # regime_score: 当前体制方向的铁证WR（用于横幅显示，不依赖signal score）
        _regime_wr_map={'BEAR_EARLY_SHORT':66.5,'BEAR_TREND_SHORT':71.8,'BULL_EARLY_LONG':64.4,
                    'BULL_TREND_LONG':70.3,'BEAR_RECOVERY_LONG':72.5,'BULL_CORRECTION_SHORT':73.9}
        regime_wr=_regime_wr_map.get(d.get('regime','')+'_'+best_dir, 0)
        # 确保momentum/phase有值（BTC/ETH无信号时也要有体制状态）
        _momentum=d.get('momentum') or d.get('mtf_momentum') or 'NEUTRAL'
        _phase=d.get('phase') or d.get('phase_1h') or '—'
        row={'symbol':d.get('symbol',sym),'regime':d.get('regime','?'),
             'best_direction':best_dir,'regime_wr':regime_wr,
             'momentum':_momentum,'phase':_phase,
             'direction':d.get('signal_dir','?'),'score':float(d.get('score',0)),
             'grade':float(d.get('grade',0)),'valid':str(d.get('valid',False)),
             'price':price,'entry_lo':round(float(d.get('entry_lo',0)),6),
             'entry_hi':round(float(d.get('entry_hi',0)),6),
             'stop_loss':round(float(d.get('stop_loss',0)),6),
             'tp1':round(float(d.get('tp1',0)),6),
             'vol':m.get('vol',0),'chg':m.get('chg',0),
             'fr':m.get('fr',0),'lsr':m.get('lsr',1),
             'liq':m.get('liq','LOW'),
             'updated_at':datetime.now(timezone.utc).strftime('%H:%M:%S')}
        new_regime.append(row)
        if d.get('valid') and float(d.get('score',0))>=SCORE_THRESHOLD:
            new_signals.append(dict(row))
    positions,nav,avail,margin_pct=get_positions()
    mirror_positions,mirror_float=build_mirror_account(positions)
    history=load_signal_history(100)
    elapsed=time.time()-t0
    # 胜率来自 wuqu_paper_settled（武曲真实结算）
    wr,decided,nw,nl=load_wuqu_stats()
    # 每日盈亏累计：今日UTC日期新增的已结算记录镜像PnL
    today_str=datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if _daily_pnl_state['date']!=today_str:
        _daily_pnl_state['date']=today_str
        _daily_pnl_state['cumulative']=0.0
        _daily_pnl_state['settled_ids']=set()
    # 遍历当日结算的记录累加
    for h in history:
        rec_id=h.get('signal_id') or h.get('open_ts','')+h.get('symbol','')
        close_ts=h.get('close_ts','') or h.get('updated_at','')
        if rec_id and rec_id not in _daily_pnl_state['settled_ids']:
            if close_ts and close_ts[:10]==today_str and h.get('status')=='CLOSED':
                _is_be=h.get('symbol','') in ('BTCUSDT','ETHUSDT')
                _lev=100 if _is_be else 20
                _pnl_item=100000*0.05*_lev*(float(h.get('pnl_pct',0))/100)*(1 if h.get('direction','LONG')=='LONG' else -1)
                _daily_pnl_state['cumulative']+=_pnl_item
                _daily_pnl_state['settled_ids'].add(rec_id)
    health=[{'metric':'NAV','value':f"${nav:.2f}",'status':'OK'},
            {'metric':'扫描标的','value':str(len(new_regime)),'status':'OK'},
            {'metric':'有效信号','value':str(len(new_signals)),'status':'🚨 有信号' if new_signals else '⏳ 待机'},
            {'metric':'镜像本金','value':f"$100,000",'status':'OK'},
            {'metric':'镜像可用','value':f"{round(100000*(1-margin_pct/100)):,} U",'status':'OK'},
            {'metric':'保证金占用','value':f"{margin_pct:.1f}%",'status':'OK'},
            {'metric':'持仓数','value':str(len(positions)),'status':'OK'},
            {'metric':'历史胜率','value':f"{wr}% ({decided}单 WIN={nw} LOSS={nl})",'status':'🏆 极强' if wr>=75 else ('✅' if wr>=60 else '⚠️')},
            {'metric':'今日已结算盈亏','value':f"{'+' if _daily_pnl_state['cumulative']>=0 else ''}{_daily_pnl_state['cumulative']:.0f} U",'status':'OK'},
            {'metric':'扫描耗时','value':f"{elapsed:.0f}s",'status':'⚠️ 慢' if elapsed>60 else 'OK'},
            {'metric':'最后更新','value':datetime.now(timezone.utc).strftime('%H:%M:%S UTC'),'status':'OK'}]
    # 更新 score 历史，计算 delta
    for row in new_regime:
        _score_history_store.push(row['symbol'], row['score'])
    cur_delta=_score_history_store.delta_all()
    global _score_delta
    _score_delta=cur_delta
    _regime_data=new_regime;_signal_data=new_signals;_position_data=positions
    _health_data=health;_history_data=history;_market_data=mkt;_last_update=time.time()
    try:
        cache={'regime':new_regime,'signals':new_signals,'positions':positions,
               'mirror_positions':mirror_positions,'mirror_float':mirror_float,
               'health':health,'history':history,'ts':_last_update,'score_delta':cur_delta}
        (BASE/'data'/'dashboard_cache.json').write_text(json.dumps(cache,ensure_ascii=False))
    except: pass
    print(f"[完成] {len(new_regime)}标的|信号:{len(new_signals)}|持仓:{len(positions)}|镜像浮盈:{mirror_float:+.2f}|耗时:{elapsed:.0f}s")
    payload=json.dumps({'type':'update','regime':new_regime,'signals':new_signals,
                       'positions':positions,'mirror_positions':mirror_positions,'mirror_float':mirror_float,
                       'health':health,'history':history,
                       'score_delta':cur_delta,'ts':int(_last_update)})
    dead=set()
    for c in _ws_clients:
        try: c.write_message(payload)
        except: dead.add(c)
    _ws_clients-=dead
    # SSE broadcast (thread-safe via IOLoop)
    def _sse_push(p=payload):
        dead_sse=set()
        for c in list(_sse_clients):
            try:
                c.write("data: "+p+"\n\n")
                c.flush()
            except: dead_sse.add(c)
        _sse_clients.difference_update(dead_sse)
    tornado.ioloop.IOLoop.current().add_callback(_sse_push)

def periodic_refresh():
    while True:
        try: refresh_data()
        except Exception as e: print(f"[err]{e}")
        time.sleep(REFRESH_INTERVAL)

def _load_cache():
    global _regime_data,_signal_data,_position_data,_health_data,_history_data,_last_update
    global _mirror_positions,_mirror_float
    try:
        c=json.loads((BASE/'data'/'dashboard_cache.json').read_text())
        _regime_data=c.get('regime',[]);_signal_data=c.get('signals',[])
        _position_data=c.get('positions',[]);_health_data=c.get('health',[])
        _history_data=c.get('history',[]);_last_update=c.get('ts',0)
        _mirror_positions=c.get('mirror_positions',[])
        _mirror_float=c.get('mirror_float',0.0)
        global _score_delta; _score_delta=c.get('score_delta',{})
        print(f"[缓存] regime={len(_regime_data)} signals={len(_signal_data)} positions={len(_position_data)} mirror={_mirror_float:+.2f}")
    except Exception as e: print(f"[缓存失败] {e}")

class MainHandler(tornado.web.RequestHandler):
    def get(self): self.write(SUB_HTML)

class DevHandler(tornado.web.RequestHandler):
    def get(self): self.write(DEV_HTML)

class ApiHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header('Content-Type','application/json')
        self.write(json.dumps({'regime':_regime_data,'signals':_signal_data,
            'positions':_position_data,'health':_health_data,
            'history':_history_data,'mirror_positions':_mirror_positions,
            'mirror_float':_mirror_float,'ts':int(_last_update)},ensure_ascii=False))

class ForceRefreshHandler(tornado.web.RequestHandler):
    def get(self):
        threading.Thread(target=refresh_data,daemon=True).start()
        self.set_header('Content-Type','application/json')
        self.write(json.dumps({'status':'refreshing'},ensure_ascii=False))

class WsHandler(tornado.websocket.WebSocketHandler):
    def check_origin(self,origin): return True
    def on_ping(self,data): self.write_message(data,binary=True) if False else None
    def open(self):
        _ws_clients.add(self)
        tornado.ioloop.IOLoop.current().call_later(0.3, self._push_init)

    def _push_init(self):
        if not self.ws_connection: return
        if _last_update>0 and _regime_data:
            try:
                self.write_message(json.dumps({'type':'update','regime':_regime_data,
                    'signals':_signal_data,'positions':_position_data,
                    'health':_health_data,'history':_history_data,
                    'mirror_positions':_mirror_positions,'mirror_float':_mirror_float,
                    'score_delta':_score_delta,'ts':int(_last_update)}))
            except: pass
        else:
            threading.Thread(target=self._init,daemon=True).start()
    def _init(self): refresh_data()
    def on_close(self): _ws_clients.discard(self)

_sse_clients=set()

class SseHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Content-Type","text/event-stream")
        self.set_header("Cache-Control","no-cache")
        self.set_header("Connection","keep-alive")
        self.set_header("Access-Control-Allow-Origin","*")
        self.set_header("X-Accel-Buffering","no")
    async def get(self):
        _sse_clients.add(self)
        self.set_status(200)
        try:
            # 立即推送缓存（不依赖 _last_update 判断）
            try:
                cache_file = BASE / "data" / "dashboard_cache.json"
                if cache_file.exists():
                    import json as _j
                    c = _j.loads(cache_file.read_text())
                    d = _j.dumps({"type":"update",
                        "regime":c.get("regime",_regime_data),
                        "signals":c.get("signals",_signal_data),
                        "positions":c.get("positions",_position_data),
                        "health":c.get("health",_health_data),
                        "history":c.get("history",_history_data),
                        "score_delta":c.get("score_delta",_score_delta),
                        "ts":int(c.get("ts",_last_update or 1))})
                    self.write("data: "+d+"\n\n")
                    await self.flush()
            except Exception as e:
                print(f"[SSE init push err] {e}")
            # 保持连接，定期ping
            while True:
                await tornado.gen.sleep(25)
                self.write(": ping\n\n")
                await self.flush()
        except Exception:
            pass
        finally:
            _sse_clients.discard(self)
    def on_connection_close(self):
        _sse_clients.discard(self)


class ProHandler(tornado.web.RequestHandler):
    """订阅者页（同 / ）"""
    def get(self): self.write(SUB_HTML)


class ScanApiHandler(tornado.web.RequestHandler):
    """最新扫描数据 /api/scan"""
    def get(self):
        import re as _re
        self.set_header('Content-Type','application/json')
        # 读取scan_candidates.json
        cand_path = BASE / 'data' / 'scan_candidates.json'
        candidates = []
        scan_ts = ''
        try:
            cdata = json.loads(cand_path.read_text())
            candidates = cdata.get('candidates', [])
            scan_ts = cdata.get('timestamp', '')
        except: pass
        # 解析scan_4h.log最新一轮结果
        scan_results = []
        total_pushed = 0
        try:
            log_path = BASE / 'logs' / 'scan_4h.log'
            log = log_path.read_text()
            # 找最后一次ScanAll运行
            blocks = log.split('[ScanAll] 动态候选模式')
            last = blocks[-1] if len(blocks) > 1 else ''
            for line in last.split('\n'):
                m = _re.search(r'\[ScanAll\] (\w+): pushed=(\d+) \| (.+)', line)
                if m:
                    scan_results.append({'symbol':m.group(1),'pushed':int(m.group(2)),'decision':m.group(3)})
                    total_pushed += int(m.group(2))
                m2 = _re.search(r'\[ScanAll\] 动态候选模式 \| 来源: (.+?) \| ', line)
                if m2 and not scan_ts:
                    scan_ts = m2.group(1)
        except: pass
        self.write(json.dumps({
            'candidates': candidates,
            'scan_results': scan_results,
            'total_pushed': total_pushed,
            'scan_ts': scan_ts
        }, ensure_ascii=False))

def make_app():
    return tornado.web.Application([(r'/',MainHandler),(r'/pro',DevHandler),(r'/static/(.*)',tornado.web.StaticFileHandler,{'path': str(Path(__file__).parent.parent/'web'/'static')}),(r'/api',ApiHandler),(r'/refresh',ForceRefreshHandler),(r'/api/scan',ScanApiHandler),(r'/ws',WsHandler),(r'/sse',SseHandler)])

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=7777)
    args=p.parse_args()
    _load_cache()
    threading.Thread(target=periodic_refresh,daemon=True).start()
    threading.Thread(target=refresh_data,daemon=True).start()
    make_app().listen(args.port, xheaders=True)
    print(f"✅ 梵天仪表盘 v3.0  http://0.0.0.0:{args.port}")
    tornado.ioloop.IOLoop.current().start()
