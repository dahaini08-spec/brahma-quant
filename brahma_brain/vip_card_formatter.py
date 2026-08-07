"""
vip_card_formatter.py — 梵天VIP策略卡片标准模版 v1.0
设计院封印 2026-08-07 · 苏摩111批准

模版标准：
  - 简洁明了，突出重点
  - 精确入场区（FVG/OB定位，宽度≤0.3%）
  - 方仓经验引擎极简摘要
  - 固定格式，直接推送
"""
import urllib.request, json, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

def _fetch(url, t=5):
    try:
        with urllib.request.urlopen(url, timeout=t) as r: return json.loads(r.read())
    except: return None

def _rsi(c, p=14):
    if len(c)<p+1: return 50.0
    g,l=[],[]
    for i in range(1,len(c)):
        d=c[i]-c[i-1]; g.append(max(d,0)); l.append(max(-d,0))
    ag=sum(g[-p:])/p; al=sum(l[-p:])/p
    return 50.0 if al==0 else 100-100/(1+ag/al)

def _ema(c, p):
    k=2/(p+1); e=c[0]
    for x in c[1:]: e=x*k+e*(1-k)
    return e

def _klines(sym, tf, lim):
    d=_fetch("https://fapi.binance.com/fapi/v1/klines?symbol="+sym+"&interval="+tf+"&limit="+str(lim))
    if not d: return []
    return [{'o':float(x[1]),'h':float(x[2]),'l':float(x[3]),'c':float(x[4]),'v':float(x[5])} for x in d]

def _find_fvg(bars, price):
    fvgs=[]
    for i in range(2, len(bars)):
        if bars[i-2]['h'] < bars[i]['l']:
            lo=bars[i-2]['h']; hi=bars[i]['l']
            if lo < price:
                fvgs.append({'lo':round(lo,2),'hi':round(hi,2),'mid':round((lo+hi)/2,2),
                             'gap_pct':round((hi-lo)/price*100,3),'age':len(bars)-i})
    fvgs.sort(key=lambda x:x['age'])
    return fvgs[:4]

def _find_ob(bars, price):
    obs=[]; seen=set()
    for i in range(3,len(bars)):
        if bars[i]['c']>bars[i]['o']:
            for j in range(i-1,max(i-4,0),-1):
                if bars[j]['c']<bars[j]['o']:
                    blo=min(bars[j]['o'],bars[j]['c'])
                    bhi=max(bars[j]['o'],bars[j]['c'])
                    if bhi<price and str(round(bhi,2)) not in seen:
                        seen.add(str(round(bhi,2)))
                        obs.append({'lo':round(bars[j]['l'],2),'blo':round(blo,2),
                                    'bhi':round(bhi,2),'age':len(bars)-i})
                    break
    obs.sort(key=lambda x:x['age'])
    return obs[:3]

def _calc_entry(fvgs, obs, e20_1h, price):
    """精确入场区：FVG > OB > EMA20，宽度≤0.3%"""
    big=[f for f in fvgs if f['gap_pct']>0.12]
    if big:
        f=big[0]
        return round(f['lo'],2), round(f['hi'],2), "FVG"
    elif fvgs:
        f=fvgs[0]
        return round(f['hi']*0.9985,2), round(f['hi'],2), "FVG上沿"
    elif obs:
        o=obs[0]
        return round(o['blo'],2), round(o['bhi'],2), "OB"
    else:
        return round(e20_1h*0.999,2), round(e20_1h,2), "EMA20"

def build_vip_card(sym: str, direction: str = 'LONG') -> str:
    """
    生成标准VIP策略卡片
    sym: 'BTCUSDT' | 'ETHUSDT'
    direction: 'LONG' | 'SHORT'
    """
    label = sym.replace('USDT','')
    dir_label = "做多 🟢" if direction=='LONG' else "做空 🔴"

    pd = _fetch("https://fapi.binance.com/fapi/v1/ticker/price?symbol="+sym)
    if not pd: return "❌ 价格获取失败"
    p = float(pd['price'])

    k4h = _klines(sym,'4h',60); k1h = _klines(sym,'1h',100)
    if not k4h or not k1h: return "❌ K线获取失败"

    c4h=[x['c'] for x in k4h]; c1h=[x['c'] for x in k1h]
    rsi4h=_rsi(c4h); rsi7=_rsi(c1h,7)
    e20_4h=_ema(c4h,20); e50_4h=_ema(c4h,50); e20_1h=_ema(c1h,20)

    fvgs=_find_fvg(k1h[-60:],p); obs=_find_ob(k1h[-40:],p)
    lows=sorted([x['l'] for x in k1h[-60:]])
    highs=sorted([x['h'] for x in k1h[-60:]],reverse=True)

    # 精确入场区
    e_lo, e_hi, e_src = _calc_entry(fvgs, obs, e20_1h, p)
    e_mid = (e_lo+e_hi)/2

    # 止损：使用止损池密集区（方仓铁证：SL=2%）
    if direction=='LONG':
        sl = round(e_lo*(1-0.020),2)  # 入场下沿 -2%（宪法铁证）
        tp1= round(e_mid*1.019,2)     # +1.9%
        tp2= round(e_mid*1.047,2)     # +4.7%
    else:
        sl = round(e_hi*(1+0.020),2)
        tp1= round(e_mid*0.981,2)
        tp2= round(e_mid*0.953,2)

    risk=abs(e_mid-sl)
    rr=round(abs(tp1-e_mid)/risk,1) if risk>0 else 1.5

    ls=_fetch("https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol="+sym+"&period=5m&limit=1")
    long_p=float(ls[0]['longAccount'])*100 if ls else 50
    crowd="⚠️ 多头拥挤" if long_p>65 else "✅ 均衡"
    regime='BULL_TREND' if e20_4h>e50_4h else 'BEAR_TREND'

    timing="⏳ 过热等回踩" if rsi7>80 else ("✅ 入场" if rsi4h<60 else "⚖️ 待确认")
    dist=round((e_hi-p)/p*100,2)  # 正=在入场区下方待回踩
    width=round(e_hi-e_lo,2)

    # 方仓
    try:
        from brahma_brain.fangcang_engine import get_fangcang_context
        # regime已在上方定义
        fc=get_fangcang_context(sym, regime)
        pm=fc['prob_matrix']
        top3=fc['top_similar'][:3]
        fc_line="🏛 方仓 ↑"+str(round(pm['p_up']*100))+"% EV="+str(round(pm['ev'],2))+"% 尾部="+str(round(pm['tail_down_risk']*100))+"%"
        fc_cases=[("    "+s['dt'][:7]+" "+("↑" if s['future_ret']>0 else "↓")+
                   str(abs(round(s['future_ret'],1)))+"% 最高+"+str(round(s['future_max'],1))+"%")
                  for s in top3]
    except:
        fc_line="🏛 方仓 (不可用)"
        fc_cases=[]

    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    SEP="─"*41

    lines=[
        "",
        "▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓",
        "  ᯤ 姓赵不宣  "+label+"/USDT  "+dir_label,
        "▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓",
        "  "+timing+"  |  RSI4H="+str(round(rsi4h,1))+"  RSI7="+str(round(rsi7,1)),
        "  体制: "+str(regime)+"  多空="+str(round(long_p,1))+"%",
        "  当前价 $"+str(round(p,2))+"   EMA20=$"+str(round(e20_4h,1)),
        "  "+SEP,
        "  📍 入场区   $"+str(e_lo)+" ~ $"+str(e_hi)+"  ["+e_src+"]",
        "     宽度 $"+str(width)+"  距当前 "+("-" if dist>0 else "+")+str(abs(dist))+"%",
        "  🛡  止  损   $"+str(sl)+"  (-"+str(round((p-sl)/p*100 if direction=='LONG' else (sl-p)/p*100,1))+"%)",
        "  🎯 TP1      $"+str(tp1)+"  (+"+str(round(abs(tp1-p)/p*100,1))+"%)",
        "  🎯 TP2      $"+str(tp2)+"  (+"+str(round(abs(tp2-p)/p*100,1))+"%)",
        "  ⚖  RR       "+str(rr)+"x   "+crowd,
        "  "+SEP,
    ]

    # 结构（最关键2条）
    struct=[]
    if fvgs:
        f=fvgs[0]
        struct.append("  FVG $"+str(f['lo'])+"~$"+str(f['hi'])+" (gap "+str(f['gap_pct'])+"%)")
    if obs:
        o=obs[0]
        struct.append("  OB  $"+str(o['blo'])+"~$"+str(o['bhi'])+" 影线$"+str(o['lo']))
    lines+=struct[:2]+["  "+SEP, fc_line]+fc_cases+["▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓","  ᯤ @姓赵不宣   "+ts,""]

    return '\n'.join(lines)


if __name__ == '__main__':
    print(build_vip_card('BTCUSDT','LONG'))
    print(build_vip_card('ETHUSDT','LONG'))
