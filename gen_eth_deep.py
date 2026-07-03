from PIL import Image, ImageDraw, ImageFont
import requests, numpy as np, secrets, time, json

def get_klines(symbol, interval, limit=80):
    r = requests.get('https://api.binance.com/api/v3/klines',
        params={'symbol': symbol, 'interval': interval, 'limit': limit}, timeout=10)
    return [(float(x[1]),float(x[2]),float(x[3]),float(x[4]),float(x[5])) for x in r.json()]

def ema_series(closes, period):
    k=2/(period+1); e=closes[0]; out=[e]
    for v in closes[1:]: e=v*k+e*(1-k); out.append(e)
    return out

def rsi_series(closes, period=14):
    out=[50]*period
    for i in range(period, len(closes)):
        d=np.diff(closes[:i+1])
        g=np.where(d>0,d,0); lo=np.where(d<0,-d,0)
        ag=np.mean(g[-period:]); al=np.mean(lo[-period:])
        out.append(100 if al==0 else 100-(100/(1+ag/al)))
    return out

W,H = 1000, 980
img = Image.new('RGB',(W,H),(13,15,25))
d = ImageDraw.Draw(img)

try:
    fb  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 18)
    fm  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 12)
    fs  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 11)
    fxl = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 22)
    fxs = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 10)
except:
    fb=fm=fs=fxl=fxs=ImageFont.load_default()

# header
d.rectangle([(0,0),(W,54)], fill=(20,24,45))
d.text((14,6),  'ETH/USDT  DEEP ANALYSIS', font=fxl, fill=(120,180,255))
d.text((14,32), 'BEAR_RECOVERY | RSI4H=82.3 | BB+1.81% | 4H Bearish Div | v7.0', font=fs, fill=(100,140,200))
d.text((W-200,32),'2026-06-15  13:10 UTC', font=fs, fill=(80,180,120))

PAD=14
kl4 = get_klines('ETHUSDT','4h',60)
kl1 = get_klines('ETHUSDT','1h',80)
price = kl4[-1][3]

# ── MAIN CHART (4H) ──────────────────────────────────────────
CX=PAD; CY=62; CW=W-PAD*2; CH=310

closes4=[k[3] for k in kl4]; highs4=[k[1] for k in kl4]
lows4=[k[2] for k in kl4]; opens4=[k[0] for k in kl4]
e20=ema_series(closes4,20); e50=ema_series(closes4,50)
rsi4=rsi_series(closes4,14)

# key levels
BULL_OB_HI=1682.00; BULL_OB_LO=1652.09
BEAR_FVG_LO=1874.11; BEAR_FVG_HI=1881.01
FIB_1272=1845.40; FIB_1618=1911.22
FIB_382=1721.00; FIB_236=1748.77
BB_UP=1759.99; BB_LO=1620.73
SL_SHORT=1845.00
TP1_SHORT=1720.00; TP2_SHORT=1676.00; TP3_SHORT=1638.00

all_v=[BULL_OB_LO*0.997]+closes4+[FIB_1618*1.005]
lo_p=min(all_v); hi_p=max(all_v); rng=hi_p-lo_p

def py(v): return int(CY+CH-(v-lo_p)/rng*CH)
cw=CW/len(kl4)
def px(i): return int(CX+i*cw)

d.rectangle([(CX,CY),(CX+CW,CY+CH)], fill=(15,17,28))
d.rectangle([(CX,CY),(CX+CW,CY+CH)], outline=(35,42,70), width=1)

# grid
for pct in [0.2,0.4,0.6,0.8]:
    yg=int(CY+CH*pct)
    d.line([(CX,yg),(CX+CW,yg)], fill=(20,25,45), width=1)

# zones
y_bull_ob_lo=py(BULL_OB_LO); y_bull_ob_hi=py(BULL_OB_HI)
d.rectangle([(CX,min(y_bull_ob_lo,y_bull_ob_hi)),(CX+CW,max(y_bull_ob_lo,y_bull_ob_hi))], fill=(15,50,25))
d.line([(CX,min(y_bull_ob_lo,y_bull_ob_hi)),(CX+CW,min(y_bull_ob_lo,y_bull_ob_hi))], fill=(40,200,80), width=2)
d.text((CX+4,min(y_bull_ob_lo,y_bull_ob_hi)+2),'LONG ZONE OB', font=fxs, fill=(40,200,80))

y_bf_lo=py(BEAR_FVG_LO); y_bf_hi=py(BEAR_FVG_HI)
d.rectangle([(CX,min(y_bf_lo,y_bf_hi)),(CX+CW,max(y_bf_lo,y_bf_hi))], fill=(50,20,20))
d.text((CX+4,min(y_bf_lo,y_bf_hi)+2),'BEAR FVG', font=fxs, fill=(200,80,80))

# fib lines
for fval, flbl, fcol in [(FIB_1272,'Fib 1.272  $1,845',(255,140,0)),
                          (FIB_1618,'Fib 1.618  $1,911',(255,80,80)),
                          (FIB_236, 'Fib 0.236  $1,749',(180,180,60)),
                          (FIB_382, 'Fib 0.382  $1,721',(140,140,200))]:
    yf=py(fval)
    for xi in range(CX,CX+CW,10): d.line([(xi,yf),(xi+5,yf)],fill=fcol,width=1)
    d.text((CX+CW-160,yf-12),flbl,font=fxs,fill=fcol)

# BB upper
y_bbu=py(BB_UP)
for xi in range(CX,CX+CW,8): d.line([(xi,y_bbu),(xi+4,y_bbu)],fill=(100,100,200),width=1)
d.text((CX+4,y_bbu+2),'BB upper $1,760',font=fxs,fill=(100,100,200))

# EMA
for i in range(1,len(kl4)):
    cx0=px(i-1)+int(cw/2); cx1=px(i)+int(cw/2)
    d.line([(cx0,py(e20[i-1])),(cx1,py(e20[i]))],fill=(255,160,30),width=2)
    d.line([(cx0,py(e50[i-1])),(cx1,py(e50[i]))],fill=(80,100,255),width=2)

# candles
bw=max(2,int(cw)-2)
for i,(o,h,l,c,v) in enumerate(kl4):
    x=px(i); col=(45,200,90) if c>=o else (200,55,70); cx2=x+bw//2
    d.line([(cx2,py(h)),(cx2,py(l))],fill=col,width=1)
    yh=min(py(o),py(c)); yl=max(py(o),py(c))
    d.rectangle([(x,yh),(x+bw,max(yh+1,yl))],fill=col)

# price line
yp=py(price)
for xi in range(CX,CX+CW,8): d.line([(xi,yp),(xi+4,yp)],fill=(255,220,0),width=1)
d.text((CX+CW-130,yp-14),'NOW $%s'%f'{price:,.1f}',font=fm,fill=(255,220,0))

# title
d.rectangle([(CX,CY-28),(CX+CW,CY-1)],fill=(22,28,50))
d.text((CX+8,CY-22),'ETH/USDT  4H  CHART',font=fb,fill=(255,255,255))
d.text((CX+240,CY-20),'EMA20 (orange)  EMA50 (blue)',font=fxs,fill=(180,180,180))
d.text((CX+CW-160,CY-20),'RSI4H=82.3  BB+1.81%',font=fm,fill=(220,80,70))

# ── RSI PANEL ────────────────────────────────────────────────
RY=CY+CH+8; RH=70
d.rectangle([(CX,RY),(CX+CW,RY+RH)],fill=(14,16,26))
d.rectangle([(CX,RY),(CX+CW,RY+RH)],outline=(30,38,60),width=1)
# RSI 70 / 30 lines
y70=int(RY+RH*(1-70/100)); y30=int(RY+RH*(1-30/100))
d.line([(CX,y70),(CX+CW,y70)],fill=(180,60,60),width=1)
d.line([(CX,y30),(CX+CW,y30)],fill=(60,180,60),width=1)
d.text((CX+2,y70-12),'70',font=fxs,fill=(180,60,60))
d.text((CX+2,y30+2),'30',font=fxs,fill=(60,180,60))
# RSI line
for i in range(1,len(rsi4)):
    r1v=rsi4[i-1]; r2v=rsi4[i]
    y1=int(RY+RH*(1-r1v/100)); y2=int(RY+RH*(1-r2v/100))
    col=(220,80,70) if r2v>70 else (80,200,120) if r2v<30 else (120,160,220)
    d.line([(px(i-1)+int(cw/2),y1),(px(i)+int(cw/2),y2)],fill=col,width=2)
d.text((CX+4,RY+2),'RSI 14',font=fxs,fill=(160,160,200))

# ── SIGNAL CARDS ─────────────────────────────────────────────
SY=RY+RH+14
SW=(W-PAD*3)//2

def draw_card(bx,by,bw,title,lines,title_col):
    rh=20; bh=32+len(lines)*rh+6
    d.rectangle([(bx,by),(bx+bw,by+bh)],fill=(16,20,38))
    d.rectangle([(bx,by),(bx+bw,by+bh)],outline=(45,65,140),width=1)
    d.rectangle([(bx,by),(bx+bw,by+28)],fill=(22,35,70))
    d.text((bx+10,by+7),title,font=fb,fill=title_col)
    colors=[(220,80,80),(255,200,50),(200,80,80),(100,200,100),(50,230,100),(50,210,120),(160,180,230),(130,150,185)]
    for i,(lbl,val) in enumerate(lines):
        c2=colors[i%len(colors)]
        d.text((bx+10,by+32+i*rh),lbl,font=fm,fill=(140,155,185))
        d.text((bx+130,by+32+i*rh),val,font=fm,fill=c2)

# SHORT card
draw_card(PAD,SY,SW,'ETH SHORT  --  SNIPE',[
    ('Regime','BEAR_RECOVERY  WR SHORT=47.9%  ⚠️ 轻仓'),
    ('Zone-A 50%','$1,793~$1,810  (当前价+ATH区)'),
    ('Zone-B 50%','$1,840~$1,845  (Fib1.272=$1,845)'),
    ('止损','$1,870  (Bear FVG底部上方)'),
    ('TP1','$1,748  (-2.5%  RR 1:1.8)  减仓50%'),
    ('TP2','$1,720  (-4.1%  RR 1:2.9)  减仓30%'),
    ('TP3','$1,676  (-6.6%  RR 1:4.5)  全平'),
    ('触发','4H顶背离已现 + 1H看跌吞噬 + 量萎缩'),
],(255,120,120))

# LONG card
draw_card(PAD*2+SW,SY,SW,'ETH LONG  --  AMBUSH',[
    ('Regime','BEAR_RECOVERY  WR LONG=72.5%  ✅ 主力'),
    ('Zone-A 50%','$1,662~$1,673  (4H OB实体)'),
    ('Zone-B 50%','$1,638~$1,652  (FVG底部+结构)'),
    ('止损','$1,603  (结构最低点下方)'),
    ('TP1','$1,760  (+5.9%  RR 1:2.0)  减仓50%'),
    ('TP2','$1,845  (+10.7% RR 1:4.2)  减仓30%'),
    ('TP3','$1,910  (+14.5% RR 1:6.0)  全平'),
    ('触发','RSI4H<55 + 1H看涨吞噬 + 量放大'),
],(80,200,120))

# ── DATA BAR ─────────────────────────────────────────────────
card_h=32+8*20+6
DY=SY+card_h+10
d.rectangle([(0,DY),(W,H)],fill=(14,17,32))
d.line([(PAD,DY+6),(W-PAD,DY+6)],fill=(35,45,80),width=1)
row1='Price: $1,791.86  |  RSI: 15m=99.6  1H=81.6  4H=82.3  1D=36.8  |  BB upper: $1,760 (+1.81%)'
row2='OI=4.13B(+0.97%)  |  L/S=1.793(UP)  TopTrader=2.241  |  FR=-0.0007%  |  Vol=1.26x'
row3='4H BEARISH DIVERGENCE: price 1682->1726(高) RSI 66.0->50.5(低)  |  EMA200_1H=1,668 (支撑)'
row4='Fib1.272=$1,845 = 近期目标阻力  |  Bear FVG: $1,874~$1,881  |  无上方OB直至 $2,004'
d.text((PAD+8,DY+12),row1,font=fxs,fill=(160,175,210))
d.text((PAD+8,DY+26),row2,font=fxs,fill=(140,160,190))
d.text((PAD+8,DY+40),row3,font=fxs,fill=(220,160,60))
d.text((PAD+8,DY+54),row4,font=fxs,fill=(180,140,80))

epoch=int(time.time()); hexs=secrets.token_hex(4)
path='../openclaw-media/jarvis-image-%d-%s.png'%(epoch,hexs)
img.save(path); print(path)
