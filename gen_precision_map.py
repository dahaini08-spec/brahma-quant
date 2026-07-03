from PIL import Image, ImageDraw, ImageFont
import requests, numpy as np, secrets, time

def get_klines(symbol, interval, limit=80):
    r = requests.get('https://api.binance.com/api/v3/klines',
        params={'symbol': symbol, 'interval': interval, 'limit': limit}, timeout=10)
    return [(float(x[1]),float(x[2]),float(x[3]),float(x[4])) for x in r.json()]

def ema_series(closes, period):
    k=2/(period+1); e=closes[0]; out=[e]
    for v in closes[1:]: e=v*k+e*(1-k); out.append(e)
    return out

W,H = 960, 900
img = Image.new('RGB',(W,H),(13,15,25))
d = ImageDraw.Draw(img)

try:
    fb  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 19)
    fm  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 12)
    fs  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 11)
    fxl = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 23)
    fbig= ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 15)
except:
    fb=fm=fs=fxl=fbig=ImageFont.load_default()

# header
d.rectangle([(0,0),(W,56)], fill=(20,24,45))
d.text((14,7),  'BRAHMA PRECISION ENTRY  --  SNIPING MODE', font=fxl, fill=(255,200,50))
d.text((14,34), 'Dual-zone precision  |  BEAR_RECOVERY  |  WR=72.5%  |  v7.0', font=fs, fill=(100,140,200))
d.text((W-200,34), '2026-06-15 04:19 UTC', font=fs, fill=(80,180,120))


def draw_panel(ox, oy, pw, ph, sym, tf,
               zoneA_lo, zoneA_hi,   # primary tight zone
               zoneB_lo, zoneB_hi,   # secondary snipe zone
               sl, tp1, tp2, info):

    kl = get_klines(sym, tf, 60)
    closes=[k[3] for k in kl]; highs=[k[1] for k in kl]
    lows=[k[2] for k in kl]; opens=[k[0] for k in kl]
    e20=ema_series(closes,20); e50=ema_series(closes,50)
    price=closes[-1]

    all_v = closes + [sl*0.997, tp2*1.002]
    lo_p=min(all_v); hi_p=max(all_v); rng=hi_p-lo_p if hi_p!=lo_p else 1
    def py(v): return int(oy + ph - (v-lo_p)/rng*ph)
    cw=pw/len(kl)
    def px(i): return int(ox + i*cw)

    d.rectangle([(ox,oy),(ox+pw,oy+ph)], fill=(16,18,30))
    d.rectangle([(ox,oy),(ox+pw,oy+ph)], outline=(35,42,70), width=1)
    for pct in [0.2,0.4,0.6,0.8]:
        yg=int(oy+ph*pct)
        d.line([(ox,yg),(ox+pw,yg)], fill=(22,28,48), width=1)

    # Zone A (primary) — brighter green
    yA_lo=py(zoneA_lo); yA_hi=py(zoneA_hi)
    d.rectangle([(ox,min(yA_lo,yA_hi)),(ox+pw,max(yA_lo,yA_hi))], fill=(20,70,30))
    d.line([(ox,min(yA_lo,yA_hi)),(ox+pw,min(yA_lo,yA_hi))], fill=(60,230,100), width=2)
    d.line([(ox,max(yA_lo,yA_hi)),(ox+pw,max(yA_lo,yA_hi))], fill=(60,230,100), width=2)
    d.text((ox+pw-72, min(yA_lo,yA_hi)+2), 'ZONE-A', font=fs, fill=(60,230,100))

    # Zone B (snipe) — gold
    yB_lo=py(zoneB_lo); yB_hi=py(zoneB_hi)
    d.rectangle([(ox,min(yB_lo,yB_hi)),(ox+pw,max(yB_lo,yB_hi))], fill=(50,40,10))
    d.line([(ox,min(yB_lo,yB_hi)),(ox+pw,min(yB_lo,yB_hi))], fill=(255,200,30), width=2)
    d.line([(ox,max(yB_lo,yB_hi)),(ox+pw,max(yB_lo,yB_hi))], fill=(255,200,30), width=2)
    d.text((ox+pw-72, min(yB_lo,yB_hi)+2), 'ZONE-B', font=fs, fill=(255,200,30))

    # SL / TP lines
    y_sl=py(sl); y_tp1=py(tp1); y_tp2=py(tp2)
    d.line([(ox,y_sl),(ox+pw,y_sl)], fill=(200,50,50), width=2)
    d.line([(ox,y_tp1),(ox+pw,y_tp1)], fill=(80,180,80), width=1)
    d.line([(ox,y_tp2),(ox+pw,y_tp2)], fill=(50,220,100), width=2)
    d.text((ox+pw-22,y_sl-14),  'SL',  font=fs, fill=(220,70,70))
    d.text((ox+pw-26,y_tp1+2),  'TP1', font=fs, fill=(100,200,100))
    d.text((ox+pw-26,y_tp2+2),  'TP2', font=fs, fill=(50,230,100))

    # EMA
    for i in range(1,len(kl)):
        cx0=px(i-1)+int(cw/2); cx1=px(i)+int(cw/2)
        d.line([(cx0,py(e20[i-1])),(cx1,py(e20[i]))], fill=(255,160,30), width=2)
        d.line([(cx0,py(e50[i-1])),(cx1,py(e50[i]))], fill=(80,100,255), width=2)

    # candles
    bw=max(2,int(cw)-2)
    for i,(o,h,l,c) in enumerate(kl):
        x=px(i); col=(45,200,90) if c>=o else (200,55,70); cx=x+bw//2
        d.line([(cx,py(h)),(cx,py(l))],fill=col,width=1)
        yh=min(py(o),py(c)); yl=max(py(o),py(c))
        d.rectangle([(x,yh),(x+bw,max(yh+1,yl))],fill=col)

    # price dash line
    yp=py(price)
    for xi in range(ox, ox+pw, 8):
        d.line([(xi,yp),(xi+4,yp)], fill=(255,220,0), width=1)

    # title bar
    d.rectangle([(ox,oy-32),(ox+pw,oy-1)], fill=(22,28,50))
    d.text((ox+8,oy-26), sym.replace('USDT','/USDT'), font=fb, fill=(255,255,255))
    d.text((ox+160,oy-24), tf.upper(), font=fm, fill=(120,140,200))
    d.text((ox+pw-230,oy-24), 'Price: %s' % f'{price:,.2f}', font=fm, fill=(255,220,60))
    rsi_col=(220,80,70) if info['rsi4']>70 else (255,160,40)
    d.text((ox+pw-100,oy-24), info['rsi_str'], font=fm, fill=rsi_col)

    # stats bar
    d.rectangle([(ox,oy+ph),(ox+pw,oy+ph+16)], fill=(18,22,40))
    d.text((ox+6, oy+ph+2), info['note'], font=fs, fill=(140,170,220))


PAD=14; PH=300
pw_half=(W-PAD*3)//2

# BTC
draw_panel(PAD, 68, pw_half, PH, 'BTCUSDT', '4h',
    zoneA_lo=64087, zoneA_hi=64250,   # EMA50 + FVG base — 163pt
    zoneB_lo=63802, zoneB_hi=63862,   # 1H OB body — 60pt SNIPE
    sl=63480, tp1=65800, tp2=67500,
    info={'rsi4':74.5,'rsi_str':'RSI4H=74.5',
          'note':'Zone-A: EMA50+FVG base 163pt | Zone-B: 1H OB body 60pt | SL below OB cluster'})

# ETH
draw_panel(PAD*2+pw_half, 68, pw_half, PH, 'ETHUSDT', '4h',
    zoneA_lo=1669, zoneA_hi=1676,     # FVG bottom + POC — 7pt
    zoneB_lo=1658, zoneB_hi=1663,     # 1H OB cluster — 5pt SNIPE
    sl=1652, tp1=1720, tp2=1760,
    info={'rsi4':73.0,'rsi_str':'RSI4H=73.0',
          'note':'Zone-A: FVG bottom+POC 7pt | Zone-B: 1H OB cluster 5pt | SL below all OBs'})

# ── Signal cards ─────────────────────────────────────────────
cy = 68+PH+28
d.rectangle([(0,cy-2),(W,cy-1)], fill=(50,60,110))

def draw_signal_card(bx, by, bw, title, rows):
    row_h = 22
    bh = 34 + len(rows)*row_h + 8
    d.rectangle([(bx,by),(bx+bw,by+bh)], fill=(16,20,38))
    d.rectangle([(bx,by),(bx+bw,by+bh)], outline=(45,65,140), width=1)
    d.rectangle([(bx,by),(bx+bw,by+30)], fill=(22,35,75))
    d.text((bx+12,by+7), title, font=fbig, fill=(255,210,50))
    color_map = {
        'regime': (70,210,100),
        'zone_a': (60,230,100),
        'zone_b': (255,200,30),
        'sl':     (220,75,75),
        'tp1':    (100,200,100),
        'tp2':    (50,230,100),
        'trig':   (180,180,230),
        'note':   (130,155,190),
    }
    for i,(key,label,val) in enumerate(rows):
        col = color_map.get(key,(200,210,230))
        d.text((bx+12, by+34+i*row_h), label, font=fm, fill=(140,155,185))
        d.text((bx+140, by+34+i*row_h), val,   font=fm, fill=col)

draw_signal_card(PAD, cy, pw_half, 'BTC  LONG  --  PRECISION SNIPE', [
    ('regime', 'Regime',   'BEAR_RECOVERY  WR=72.5%  n=430'),
    ('zone_a', 'Zone-A',   '$64,087 ~ $64,250   (EMA50 + FVG base)   163pt'),
    ('zone_b', 'Zone-B',   '$63,802 ~ $63,862   (1H OB body snipe)    60pt'),
    ('sl',     'Stop',     '$63,480   (below 1H OB cluster)'),
    ('tp1',    'TP1',      '$65,800   (+2.4%  /  RR ~1:1.3)'),
    ('tp2',    'TP2',      '$67,500   (+5.1%  /  RR ~1:2.6)'),
    ('trig',   'Trigger',  'RSI4H < 55  +  1H engulf confirm'),
    ('note',   'Strategy', 'Zone-A = 50% size  /  Zone-B = 50% size'),
])

draw_signal_card(PAD*2+pw_half, cy, pw_half, 'ETH  LONG  --  PRECISION SNIPE', [
    ('regime', 'Regime',   'BEAR_RECOVERY  WR=72.5%  n=430'),
    ('zone_a', 'Zone-A',   '$1,669 ~ $1,676   (FVG bottom + POC)       7pt'),
    ('zone_b', 'Zone-B',   '$1,658 ~ $1,663   (1H OB cluster snipe)    5pt'),
    ('sl',     'Stop',     '$1,652   (below all 1H OBs)'),
    ('tp1',    'TP1',      '$1,720   (+2.7%  /  RR ~1:1.5)'),
    ('tp2',    'TP2',      '$1,760   (+5.9%  /  RR ~1:3.2)'),
    ('trig',   'Trigger',  'RSI4H < 55  +  1H engulf confirm'),
    ('note',   'Strategy', 'Zone-A = 50% size  /  Zone-B = 50% size'),
])

# ── Legend ───────────────────────────────────────────────────
card_h = 34 + 8*22 + 8
ly = cy + card_h + 14
d.rectangle([(0,ly),(W,H)], fill=(14,17,32))
d.line([(PAD,ly+6),(W-PAD,ly+6)], fill=(35,45,80), width=1)
d.text((PAD+8,ly+12), 'GREEN zone (Zone-A) = Primary entry  |  GOLD zone (Zone-B) = Snipe entry  |  Split 50/50 size', font=fs, fill=(140,155,195))
d.text((PAD+8,ly+28), 'EMA20 (orange)  EMA50 (blue)  |  Zone-A: first fill when price enters  |  Zone-B: deeper limit order', font=fs, fill=(140,155,195))
d.text((PAD+8,ly+44), 'Do NOT enter now -- RSI4H overbought -- wait for pullback  |  Cancel if price breaks above $66,600 (BTC)', font=fs, fill=(200,160,60))

epoch=int(time.time()); hexs=secrets.token_hex(4)
path='../openclaw-media/jarvis-image-%d-%s.png' % (epoch, hexs)
img.save(path)
print(path)
