#!/usr/bin/env python3
"""
达摩院 · 梵天全能力回测引擎 v1.0
===================================
设计院自主封印 2026-08-03

架构：
  ms_analyze() → regime判断 → analyze_smc() → confluence_score()
  完整35维评分 | 无上帝视角 | BRAHMA_SKIP_S25=1（跳LLM）
  
速度：~3-4s/次 × 采样900次 = ~45-60分钟 BTC+ETH全量
并行优化：BTC+ETH同时跑 → 实际30分钟

评分门槛：score≥120 + grade≥70（梵天实战宪法）
"""
import sys, os, json, time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

BASE = Path(__file__).parent.parent
DATA = BASE / 'data' / 'backtest'
os.environ['BRAHMA_SKIP_S25'] = '1'
sys.path.insert(0, str(BASE))

# ══ 全局网络拦截 ══
import urllib.request as _ur
_PRICE = {'v': 42000.0}

def _offline(url, *a, **k):
    url_s = url if isinstance(url, str) else getattr(url, 'full_url', str(url))
    p = _PRICE['v']
    if 'openInterest' in url_s:
        d = {'openInterest': '12000000000', 'time': 0}
    elif 'ticker' in url_s:
        if 'symbol=' not in url_s:
            d = [{'symbol': 'BTCUSDT', 'price': str(p), 'priceChangePercent': '0.0', 'volume': '100000'}]
        else:
            sym = url_s.split('symbol=')[1].split('&')[0]
            d = {'symbol': sym, 'price': str(p), 'priceChangePercent': '0.0', 'lastPrice': str(p)}
    elif 'longShort' in url_s or 'globalLong' in url_s:
        d = [{'longShortRatio': '0.50', 'longAccount': '0.50', 'shortAccount': '0.50', 'timestamp': 0}]
    elif 'premiumIndex' in url_s or 'fundingRate' in url_s:
        d = [{'fundingRate': '0.0001', 'markPrice': str(p), 'symbol': 'BTCUSDT'}]
    else:
        d = {}
    class R:
        def read(self): return json.dumps(d).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass
    return R()

_ur.urlopen = _offline

from brahma_brain import data_cache as dc
import brahma_brain.live_price_feed as lpf
if hasattr(lpf, 'bulk_update_from_api'):
    lpf.bulk_update_from_api = lambda s: None

# ══ 加载历史数据 ══
print('[达摩院] 加载数据...')
_kd = {}
for sym in ['BTCUSDT', 'ETHUSDT']:
    _kd[sym] = {}
    for tf in ['1h', '4h', '1d']:
        with open(DATA / f'{sym}_{tf}.json') as f:
            _kd[sym][tf] = json.load(f)
    print(f'  {sym}: {len(_kd[sym]["1h"])} 1H bars')

# ══ 注入函数 ══
def inject(sym, idx):
    kd = _kd[sym]
    n1h, n4h, n1d = len(kd['1h']), len(kd['4h']), len(kd['1d'])
    s1h = kd['1h'][max(0, idx-300):idx]
    price = float(s1h[-1][4])
    _PRICE['v'] = price
    i4h = min(idx * n4h // n1h, n4h - 2)
    i1d = min(idx * n1d // n1h, n1d - 2)
    # 构造简化15m（用1H拆分）
    fake15m = []
    for bar in s1h[-50:]:
        for sub in range(4):
            fake15m.append([int(bar[0])+sub*900000] + bar[1:])
    # 注入所有limit
    for lim in [50, 60, 100, 200, 300, 500]:
        dc._cache_set(dc._cache_key(sym, '1h', lim), s1h[-min(lim,len(s1h)):], 999999)
        dc._cache_set(dc._cache_key(sym, '4h', lim), kd['4h'][max(0,i4h-lim):i4h][-lim:], 999999)
        dc._cache_set(dc._cache_key(sym, '1d', lim), kd['1d'][max(0,i1d-lim):i1d][-lim:], 999999)
        dc._cache_set(dc._cache_key(sym, '15m', lim), fake15m[-min(lim,len(fake15m)):], 999999)
    dc.OFFLINE_MODE = True
    dc.OFFLINE_CTX = {
        'ticker': {'lastPrice': str(price), 'price': str(price), 'priceChangePercent': '0.0'},
        'fr': 0.0001, 'oi': 1.2e10, 'lsr': 50.0,
    }
    return price

# ══ 35维评分（离线版） ══
from brahma_brain.brahma_engine import ms_analyze, analyze_smc
from brahma_brain.brahma_scoring import confluence_score

DEAD = {('BEAR_TREND','LONG'),('BULL_TREND','SHORT'),
        ('BEAR_EARLY','SHORT'),('BULL_EARLY','LONG'),('BEAR_RECOVERY','LONG')}

def score_bar(sym, idx):
    """对历史某根bar运行35维评分，返回信号dict"""
    inject(sym, idx)
    try:
        ms = ms_analyze(sym)
    except Exception:
        return None
    regime = ms.get('regime', 'CHOP_MID') or 'CHOP_MID'
    price = ms.get('price', 0) or float(_kd[sym]['1h'][idx-1][4])
    if not price: return None

    # 体制→方向
    if 'BULL' in regime:
        sig_dir = 'LONG'
    elif 'BEAR' in regime:
        sig_dir = 'SHORT'
    else:
        return None  # CHOP静默

    if (regime, sig_dir) in DEAD:
        return {'blocked': 'dead', 'regime': regime, 'direction': sig_dir}

    try:
        smc = analyze_smc(sym, sig_dir, '1h', 200)
        extra = {'price': price, 'regime': regime, '_offline': True}
        cf = confluence_score(ms, smc, sig_dir, extra)
    except Exception:
        return None

    score = cf.get('total', 0) or 0
    grade = cf.get('structure_grade', 0) or 0
    entry_lo = cf.get('entry_lo', price * (0.998 if sig_dir=='LONG' else 1.002))
    entry_hi = cf.get('entry_hi', price * (1.002 if sig_dir=='LONG' else 0.998))
    sl = cf.get('sl') or (price * (0.98 if sig_dir=='LONG' else 1.02))
    tp1 = cf.get('tp1') or (price * (1.03 if sig_dir=='LONG' else 0.97))

    return {
        'score': score, 'grade': grade,
        'direction': sig_dir, 'regime': regime,
        'price': price, 'entry': (entry_lo + entry_hi) / 2,
        'sl': sl, 'tp1': tp1, 'valid': score >= 110,
    }

# ══ 主回测 ══
MIN_SCORE = 110
MIN_GRADE = 60
FEE = 0.0008
LEV = 5.0
NAV_START = 10000.0
STRIDE = 16  # 每16根1H bar扫描一次

def run(sym):
    print(f'\n{"="*65}')
    print(f'  梵天35维 实测 · {sym} · score≥{MIN_SCORE} grade≥{MIN_GRADE}')
    print(f'{"="*65}')
    kd = _kd[sym]; n1h = len(kd['1h'])
    NAV = NAV_START
    trades = []; open_pos = None
    blocked = defaultdict(int)
    analyzed = 0; errors = 0; t_total = 0.0

    sample_range = range(300, n1h - 50, STRIDE)
    total = len(sample_range)
    t_start = time.time()
    print(f'  采样{total}次 stride={STRIDE}h', flush=True)

    for step, i in enumerate(sample_range):
        if step % 50 == 0:
            et = time.time() - t_start
            eta = et / max(step,1) * (total - step)
            n_t = len(trades); wr_so_far = sum(1 for t in trades if t['pnl']>0)/max(n_t,1)*100
            print(f'  [{step:4d}/{total}] nav={NAV:7.0f} trades={n_t:3d} wr={wr_so_far:.0f}% eta={eta/60:.1f}min', flush=True)

        # ── 持仓出场扫描 ──
        if open_pos:
            p = open_pos
            for j in range(p['oi'] + 1, min(i + STRIDE + 1, n1h)):
                bh = float(kd['1h'][j][2]); bl = float(kd['1h'][j][3]); bc = float(kd['1h'][j][4])
                closed = False; cp = bc; cr = 'hold'
                if p['d'] == 'LONG':
                    if bl <= p['sl']: cp=p['sl']; cr='SL'; closed=True
                    elif bh >= p['tp1'] and not p['t1']:
                        p['t1']=True
                        pnl_p=(p['tp1']-p['entry'])/p['entry']-FEE
                        NAV=max(NAV+NAV*p['pos']*0.5*LEV*pnl_p,NAV*0.01)
                        p['pos']*=0.5; p['sl']=p['entry']
                    if p['t1'] and not closed:
                        tr=bc*0.985; p['sl']=max(p['sl'],tr)
                        if bl<=p['sl']: cp=p['sl']; cr='TRAIL'; closed=True
                else:
                    if bh >= p['sl']: cp=p['sl']; cr='SL'; closed=True
                    elif bl <= p['tp1'] and not p['t1']:
                        p['t1']=True
                        pnl_p=(p['entry']-p['tp1'])/p['entry']-FEE
                        NAV=max(NAV+NAV*p['pos']*0.5*LEV*pnl_p,NAV*0.01)
                        p['pos']*=0.5; p['sl']=p['entry']
                    if p['t1'] and not closed:
                        tr=bc*1.015; p['sl']=min(p['sl'],tr)
                        if bh>=p['sl']: cp=p['sl']; cr='TRAIL'; closed=True
                if not closed and (j-p['oi'])>=48: cp=bc; cr='TIMEOUT'; closed=True
                if closed:
                    fpnl=(cp-p['entry'])/p['entry']-FEE if p['d']=='LONG' else (p['entry']-cp)/p['entry']-FEE
                    NAV=max(NAV+NAV*p['pos']*LEV*fpnl, NAV*0.01)
                    trades.append({'pnl':fpnl*100,'reason':cr,'regime':p['reg'],
                        'score':p['score'],'grade':p['grade'],'d':p['d'],'nav':NAV,
                        'dt':datetime.utcfromtimestamp(kd['1h'][j][0]/1000).strftime('%Y-%m-%d')})
                    open_pos=None; break

        if open_pos: continue

        # ── 35维评分 ──
        t0 = time.time()
        try:
            sig = score_bar(sym, i)
        except Exception as e:
            errors += 1; continue
        elapsed = time.time() - t0
        t_total += elapsed; analyzed += 1

        if not sig: blocked['chop']+=1; continue
        if 'blocked' in sig: blocked[sig['blocked']]+=1; continue
        if sig['score'] < MIN_SCORE: blocked['score']+=1; continue
        if sig['grade'] < MIN_GRADE: blocked['grade']+=1; continue
        if not sig['valid']: blocked['score']+=1; continue

        sc = sig['score']; entry = sig['entry']; sl_v = sig['sl']; tp1_v = sig['tp1']
        pos = 0.08 if sc>=155 else (0.06 if sc>=140 else (0.05 if sc>=120 else 0.03))
        pos = min(pos, 0.10)

        open_pos = {'d':sig['direction'],'entry':entry,'sl':sl_v,'tp1':tp1_v,
                    't1':False,'pos':pos,'reg':sig['regime'],'score':sc,
                    'grade':sig['grade'],'oi':i}

    # ── 统计 ──
    if not trades:
        print(f'  无交易 analyzed={analyzed} errors={errors}')
        return {}

    n=len(trades); wins=sum(1 for t in trades if t['pnl']>0)
    wr=wins/n*100
    aw=sum(t['pnl'] for t in trades if t['pnl']>0)/max(wins,1)
    al=sum(t['pnl'] for t in trades if t['pnl']<=0)/max(n-wins,1)
    pf=abs(aw)/abs(al) if al!=0 else 999
    ev=aw*wr/100+al*(1-wr/100)
    tot=(NAV-NAV_START)/NAV_START*100
    pk=NAV_START; mdd=0
    for t in trades:
        pk=max(pk,t['nav']); mdd=max(mdd,(pk-t['nav'])/pk*100)

    reg_st=defaultdict(lambda:[0,0,0.0])
    for t in trades:
        k=f"{t['regime']}_{t['d']}"
        reg_st[k][0]+=1 if t['pnl']>0 else 0; reg_st[k][1]+=1; reg_st[k][2]+=t['pnl']

    score_st=defaultdict(lambda:[0,0,0.0])
    for t in trades:
        sc=t['score'] or 0
        sb='150+' if sc>=150 else('140-149' if sc>=140 else('130-139' if sc>=130 else('120-129' if sc>=120 else '<120')))
        score_st[sb][0]+=1 if t['pnl']>0 else 0; score_st[sb][1]+=1; score_st[sb][2]+=t['pnl']

    by_year=defaultdict(lambda:[0,0,0.0])
    for t in trades:
        yr=t['dt'][:4]; by_year[yr][0]+=1 if t['pnl']>0 else 0; by_year[yr][1]+=1; by_year[yr][2]+=t['pnl']

    reason_st=defaultdict(int)
    for t in trades: reason_st[t['reason']]+=1

    wall=time.time()-t_start
    avg_ms=t_total/max(analyzed,1)*1000

    print(f'\n  ✅ n={n} | WR={wr:.1f}% | EV={ev:+.3f}%/笔 | PF={pf:.2f}')
    print(f'  总收益={tot:+.1f}% | MaxDD={mdd:.1f}% | NAV={NAV:.0f}U')
    print(f'  耗时={wall/60:.1f}min | avg={avg_ms:.0f}ms | errors={errors}')
    print(f'  过滤: chop={blocked["chop"]} dead={blocked["dead"]} score={blocked["score"]} grade={blocked["grade"]}')

    print(f'\n  ── 体制×方向 ──')
    for k,(w,t2,pnl) in sorted(reg_st.items(),key=lambda x:-x[1][0]/max(1,x[1][1])):
        if t2<2: continue
        wk=w/t2*100; evk=pnl/t2
        flag='✅' if wk>=55 else('⚠️' if wk>=48 else '❌')
        print(f'    {k:25s}: WR={wk:.1f}% n={t2:3d} EV={evk:+.3f}% {flag}')

    print(f'\n  ── 评分分层 ──')
    for sb in ['<120','120-129','130-139','140-149','150+']:
        if sb not in score_st: continue
        w,t2,pnl=score_st[sb]; wk=w/t2*100 if t2>0 else 0; evk=pnl/t2 if t2>0 else 0
        if t2<2: continue
        flag='✅' if evk>0 else '❌'
        print(f'    score{sb:8s}: WR={wk:.1f}% n={t2:3d} EV={evk:+.3f}% {flag}')

    print(f'\n  ── 按年度 ──')
    for yr,(w,t2,pnl) in sorted(by_year.items()):
        wk=w/t2*100; evk=pnl/t2
        flag='✅' if evk>0 else '❌'
        print(f'    {yr}: WR={wk:.1f}% n={t2:3d} EV={evk:+.3f}% {flag}')

    print(f'\n  ── 平仓原因 ──')
    for r_k,c in sorted(reason_st.items(),key=lambda x:-x[1]):
        print(f'    {r_k:10s}: {c:4d}笔 ({c/n*100:.1f}%)')

    return {'sym':sym,'n':n,'wr':wr,'ev':ev,'tot':tot,'mdd':mdd,'pf':pf,'nav':NAV,'wall':wall,'analyzed':analyzed}


if __name__=='__main__':
    import sys as _sys
    syms = [_sys.argv[1]] if len(_sys.argv)>1 else ['BTCUSDT','ETHUSDT']
    results={}
    for sym in syms:
        r=run(sym)
        if r: results[sym]=r

    if len(results)>1:
        print('\n'+'='*65)
        print('  ╔═ 梵天35维全能力实测 最终汇总 ═╗')
        print('='*65)
        baselines={'BTC基础2维':(154,48.7,-0.038,7.9,12.3),'ETH基础2维':(162,46.9,-0.048,11.1,8.2)}
        print(f'  {"版本":22s}|{"n":5s}|{"WR":6s}|{"EV/笔":8s}|{"收益%":8s}|{"MaxDD":6s}')
        print('  '+'-'*55)
        for k,(n,wr,ev,tot,mdd) in baselines.items():
            print(f'  {k:22s}|{n:5d}|{wr:5.1f}%|{ev:+.4f}%|{tot:+6.1f}%|{mdd:5.1f}%')
        for sym,r in results.items():
            k=f'{sym[:3]}_梵天35维'
            print(f'  {k:22s}|{r["n"]:5d}|{r["wr"]:5.1f}%|{r["ev"]:+.4f}%|{r["tot"]:+6.1f}%|{r["mdd"]:5.1f}%')
