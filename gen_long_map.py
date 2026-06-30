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

W,H = 960, 820
img = Image.new('RGB',(W,H),(13,15,25))
d = ImageDraw.Draw(img)

try:
    fb  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 20)
    fm  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 12)
    fs  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 11)
    fxl = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 24)
except:
    fb=fm=fs=fxl=ImageFont.load_default()

# header
d.rectangle([(0,0),(W,56)], fill=(20,24,45))
d.text((14,8),  'BRAHMA PRECISION LONG MAP', font=fxl, fill=(255,200,50))
d.text((14,34), 'BTC + ETH  |  BEAR_RECOVERY Regime  |  WR=72.5%  |  v7.0', font=fs, fill=(100,140,200))
d.text((W-200,34),'2026-06-15 04:13 UTC', font=fs, fill=(80,180,120))


def draw_panel(ox, oy, pw, ph, sym, tf, entry_lo, entry_hi, sl, tp1, tp2, info):
    kl = get_klines(sym, tf, 70)
    closes=[k[3] for k in kl]; highs=[k[1] for k in kl]
    lows=[k[2] for k in kl];   opens=[k[0] for k in kl]
    e20=ema_series(closes,20); e50=ema_series(closes,50)
    price=closes[-1]

    all_v = closes + [entry_lo*0.996, tp2*1.002]
    lo_p=min(all_v); hi_p=max(all_v); rng=hi_p-lo_p if hi_p!=lo_p else 1
    def py(v): return int(oy + ph - (v-lo_p)/rng*ph)
    cw = pw/len(kl)
    def px(i): return int(ox + i*cw)

    d.rectangle([(ox,oy),(ox+pw,oy+ph)], fill=(16,18,30))
    d.rectangle([(ox,oy),(ox+pw,oy+ph)], outline=(35,42,70), width=1)
    for pct in [0.25,0.5,0.75]:
        yg = int(oy + ph*pct)
        d.line([(ox,yg),(ox+pw,yg)], fill=(25,30,50), width=1)

    ye_lo=py(entry_lo); ye_hi=py(entry_hi)
    y_sl=py(sl); y_tp1=py(tp1); y_tp2=py(tp2)
    d.rectangle([(ox,min(ye_lo,ye_hi)),(ox+pw,max(ye_lo,ye_hi))], fill=(15,55,25))
    d.line([(ox,min(ye_lo,ye_hi)),(ox+pw,min(ye_lo,ye_hi))], fill=(40,200,80), width=2)
    d.line([(ox,max(ye_lo,ye_hi)),(ox+pw,max(ye_lo,ye_hi))], fill=(40,200,80), width=1)
    d.line([(ox,y_sl),(ox+pw,y_sl)], fill=(200,50,50), width=2)
    d.line([(ox,y_tp1),(ox+pw,y_tp1)], fill=(80,180,80), width=1)
    d.line([(ox,y_tp2),(ox+pw,y_tp2)], fill=(50,220,100), width=2)

    d.text((ox+pw-52, min(ye_lo,ye_hi)+2),'ENTRY', font=fs, fill=(60,220,90))
    d.text((ox+pw-22, y_sl-14),           'SL',    font=fs, fill=(220,70,70))
    d.text((ox+pw-26, y_tp1+2),           'TP1',   font=fs, fill=(100,200,100))
    d.text((ox+pw-26, y_tp2+2),           'TP2',   font=fs, fill=(50,230,100))

    for i in range(1,len(kl)):
        d.line([(px(i-1)+int(cw/2),py(e20[i-1])),(px(i)+int(cw/2),py(e20[i]))], fill=(255,160,30), width=2)
        d.line([(px(i-1)+int(cw/2),py(e50[i-1])),(px(i)+int(cw/2),py(e50[i]))], fill=(80,100,255), width=2)

    bw=max(2,int(cw)-2)
    for i,(o,h,l,c) in enumerate(kl):
        x=px(i); color=(45,200,90) if c>=o else (200,55,70); cx=x+bw//2
        d.line([(cx,py(h)),(cx,py(l))],fill=color,width=1)
        yh=min(py(o),py(c)); yl=max(py(o),py(c))
        d.rectangle([(x,yh),(x+bw,max(yh+1,yl))],fill=color)

    yp=py(price)
    for xi in range(ox, ox+pw, 8):
        d.line([(xi,yp),(xi+4,yp)], fill=(255,220,0), width=1)

    d.rectangle([(ox,oy-32),(ox+pw,oy-1)], fill=(22,28,50))
    sym_lbl = sym.replace('USDT','/USDT')
    d.text((ox+8, oy-26), sym_lbl, font=fb, fill=(255,255,255))
    d.text((ox+150,oy-24), tf.upper(), font=fm, fill=(120,140,200))
    price_str = f'Price: {price:,.2f}'
    d.text((ox+pw-230,oy-24), price_str, font=fm, fill=(255,220,60))
    rsi_col = (220,80,70) if info['rsi4']>70 else (255,160,40)
    d.text((ox+pw-100,oy-24), info['rsi_str'], font=fm, fill=rsi_col)

    d.rectangle([(ox,oy+ph),(ox+pw,oy+ph+34)], fill=(18,22,40))
    rr_val = info['rr']
    line1 = 'Entry %s~%s  SL %s  TP1 %s  TP2 %s  RR %sx' % (
        f'{entry_lo:,.0f}', f'{entry_hi:,.0f}', f'{sl:,.0f}',
        f'{tp1:,.0f}', f'{tp2:,.0f}', rr_val)
    d.text((ox+6, oy+ph+4),  line1,        font=fs, fill=(160,190,240))
    d.text((ox+6, oy+ph+18), info['extra'], font=fs, fill=(100,180,140))


PAD=14; PH=290
pw_half = (W-PAD*3)//2

draw_panel(PAD, 68, pw_half, PH, 'BTCUSDT','4h',
    entry_lo=63679, entry_hi=64452, sl=62872, tp1=67500, tp2=70073,
    info={'rsi4':74.5,'rsi_str':'RSI4H=74.5','rr':'3.6',
          'extra':'OB: 63679~64563 | FVG: 64209~65354 | EMA20=64320 EMA50=64088'})

draw_panel(PAD*2+pw_half, 68, pw_half, PH, 'ETHUSDT','4h',
    entry_lo=1663, entry_hi=1694, sl=1638, tp1=1760, tp2=1842,
    info={'rsi4':73.0,'rsi_str':'RSI4H=73.0','rr':'2.7',
          'extra':'OB: 1663~1682 | FVG: 1669~1710 | EMA20=1683 EMA50=1690'})

# signal cards
cy = 68+PH+50
d.rectangle([(0,cy-4),(W,cy-3)], fill=(40,50,90))

cards = [
    (PAD, cy, pw_half, 'BTC LONG SIGNAL CARD',
     ['Regime:  BEAR_RECOVERY   WR=72.5%  n=430',
      'Entry:   $63,679 ~ $64,452  (EMA20 + OB cluster)',
      'Stop:    $62,872  (below OB + 1.5x ATR)',
      'TP1:     $67,500  (+4.8%  /  RR 1:1.8)',
      'TP2:     $70,073  (+8.0%  /  RR 1:3.6)',
      'Trigger: RSI4H < 55  +  1H bullish OB confirm',
      'FR:+0.003%  L/S=1.35  OI=6.77B  Vol+41%']),
    (PAD*2+pw_half, cy, pw_half, 'ETH LONG SIGNAL CARD',
     ['Regime:  BEAR_RECOVERY   WR=72.5%  n=430',
      'Entry:   $1,663 ~ $1,694  (EMA20 + OB cluster)',
      'Stop:    $1,638  (below OB + 1.5x ATR)',
      'TP1:     $1,760  (+3.9%  /  RR 1:1.5)',
      'TP2:     $1,842  (+8.3%  /  RR 1:2.7)',
      'Trigger: RSI4H < 55  +  1H bullish OB confirm',
      'FR:+0.003%  L/S=1.78  OI=3.99B  Vol+80%']),
]

for (bx,by,bw,title,lines) in cards:
    bh = 28 + len(lines)*20 + 8
    d.rectangle([(bx,by),(bx+bw,by+bh)], fill=(18,24,42))
    d.rectangle([(bx,by),(bx+bw,by+bh)], outline=(50,80,160), width=1)
    d.rectangle([(bx,by),(bx+bw,by+28)], fill=(25,40,80))
    d.text((bx+10,by+6), title, font=fb, fill=(255,200,50))
    cols = [(80,210,110),(200,220,255),(220,80,80),(100,200,100),(50,230,100),(180,180,220),(130,150,180)]
    for i,line in enumerate(lines):
        d.text((bx+10, by+32+i*20), line, font=fm, fill=cols[i%len(cols)])

# legend
ly = cy + 200
d.rectangle([(0,ly),(W,H)], fill=(16,20,38))
d.line([(PAD,ly+8),(W-PAD,ly+8)], fill=(35,45,80), width=1)
d.text((PAD+8,ly+14), 'EMA20 (orange)  EMA50 (blue)  Green zone = ENTRY  Red line = SL  Green lines = TP1/TP2', font=fs, fill=(140,155,195))
d.text((PAD+8,ly+32), 'BEAR_RECOVERY LONG: WR=72.5% avg_pnl=+0.255 (n=430) -- Wait for pullback into green zone', font=fs, fill=(200,170,60))
d.text((PAD+8,ly+50), 'Current RSI4H=74~75 (overbought) -- Do NOT chase -- entry triggers when RSI4H returns to 48~56', font=fs, fill=(180,100,80))

epoch=int(time.time()); hexs=secrets.token_hex(4)
path=f'../openclaw-media/jarvis-image-{epoch}-{hexs}.png'
img.save(path)
print(path)
