#!/usr/bin/env python3
"""
Signal Card Renderer — generates a 512x512 PNG "tweet-style" signal card.
Run: python examples/signal_card_render.py
"""
import sys, os, json, time, secrets
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Installing Pillow..."); os.system("pip3 install Pillow -q")
    from PIL import Image, ImageDraw, ImageFont

# ── Sample signal (or load from live_signal_log.jsonl) ───────────────
def get_latest_signal():
    log = BASE / 'data' / 'live_signal_log.jsonl'
    if log.exists():
        lines = log.read_text().strip().splitlines()
        for line in reversed(lines):
            try:
                s = json.loads(line)
                if s.get('score', 0) >= 120:
                    return s
            except: pass
    # fallback demo signal
    return {
        'symbol': 'BTCUSDT', 'signal_dir': 'SHORT',
        'score': 162, 'regime': 'BEAR_TREND',
        'price': 61270, 'entry_lo': 61500, 'entry_hi': 62000,
        'stop_loss': 63200, 'tp1': 59500, 'tp2': 57800,
        'rr1': 1.8, 'sl_pct': 2.1, 'grade': '🔴高分',
        'ts_iso': datetime.now(timezone.utc).isoformat(),
    }

def render_signal_card(sig: dict, out_path: str = None) -> str:
    W, H = 512, 512
    img = Image.new('RGB', (W, H), '#0a0a14')
    draw = ImageDraw.Draw(img)

    is_long = sig.get('signal_dir','').upper() == 'LONG'
    accent  = '#00e676' if is_long else '#ff5252'
    dim     = '#1a1a2e'

    # Header bar
    draw.rectangle([(0,0),(W,56)], fill=dim)
    draw.line([(0,56),(W,56)], fill=accent, width=2)

    sym     = sig.get('symbol','?')
    dirn    = sig.get('signal_dir','?')
    regime  = sig.get('regime','?')
    score   = sig.get('score', 0)
    price   = sig.get('price', 0)
    entry_lo= sig.get('entry_lo', 0)
    entry_hi= sig.get('entry_hi', 0)
    sl      = sig.get('stop_loss', 0)
    tp1     = sig.get('tp1', 0)
    tp2     = sig.get('tp2', 0)
    rr      = sig.get('rr1', 0)
    sl_pct  = sig.get('sl_pct', 0)
    ts      = sig.get('ts_iso','')[:16].replace('T',' ')

    def txt(x, y, text, size=14, color='#e0e0e0', bold=False):
        try:
            font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold
                                       else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', size)
        except:
            font = ImageFont.load_default()
        draw.text((x, y), text, fill=color, font=font)

    # Title
    txt(14, 10, 'BRAHMA-QUANT', 13, '#666688')
    txt(14, 28, f'{ts} UTC', 11, '#444466')
    # Score badge
    score_col = '#ff5252' if score >= 155 else '#ffd740' if score >= 130 else '#69f0ae'
    draw.rectangle([(W-90, 8),(W-8, 48)], fill=score_col)
    txt(W-82, 12, 'SCORE', 11, '#000000', bold=True)
    txt(W-72, 26, str(int(score)), 18, '#000000', bold=True)

    # Symbol + direction
    dir_icon = 'LONG  ▲' if is_long else 'SHORT ▼'
    txt(14, 70, sym.replace('USDT',''), 38, '#ffffff', bold=True)
    draw.rectangle([(180, 75),(310, 108)], fill=accent)
    txt(190, 80, dir_icon, 20, '#000000', bold=True)

    # Regime pill
    draw.rounded_rectangle([(14, 120),(260, 144)], radius=8, fill='#1e1e3a')
    txt(22, 125, f'Regime: {regime}', 13, '#aaaacc')

    # Price info
    txt(14, 160, f'Current   ${price:>10,.2f}', 15, '#aaaaaa')
    txt(14, 185, f'Entry     ${entry_lo:>10,.2f} - ${entry_hi:,.2f}', 15, '#ffffff', bold=True)
    draw.line([(12,205),(W-12,205)], fill='#222244', width=1)

    # Trade params
    txt(14, 215, f'Stop Loss ${sl:>10,.2f}  ({sl_pct:.1f}%)', 14, '#ff7043')
    txt(14, 238, f'Target 1  ${tp1:>10,.2f}', 14, '#69f0ae')
    txt(14, 260, f'Target 2  ${tp2:>10,.2f}', 14, '#40c4ff')
    txt(14, 285, f'R:R Ratio  {rr:.1f}x', 16, accent, bold=True)

    # Visual R:R bar
    draw.rectangle([(14,310),(W-14,326)], fill='#1a1a2e')
    bar_w = min(int((W-28) * min(rr/3.0, 1.0)), W-28)
    draw.rectangle([(14,310),(14+bar_w,326)], fill=accent)
    txt(14, 330, 'R:R Scale (max 3x)', 11, '#444466')

    # 9-layer protection
    draw.rectangle([(0,360),(W,362)], fill='#1a1a2e')
    txt(14, 368, '9-LAYER CIRCUIT BREAKER', 11, '#444466')
    gates = ['StructureGate','GapGate','CausalVerifier','TimingFilter',
             'RegimeBlock','SeasonalFilter','CorrelRisk','GEXExpiry','Kronos']
    cols = ['#00e676','#00e676','#00e676','#ffd740','#00e676','#00e676','#00e676','#00e676','#00e676']
    for i, (g, c) in enumerate(zip(gates, cols)):
        x = 14 + (i % 5) * 98
        y = 385 + (i // 5) * 22
        draw.rounded_rectangle([(x, y),(x+92, y+17)], radius=4, fill='#0d1117')
        draw.rounded_rectangle([(x, y),(x+6, y+17)], radius=2, fill=c)
        txt(x+10, y+2, g[:12], 9, '#aaaaaa')

    # Footer
    draw.rectangle([(0,465),(W,H)], fill='#050510')
    txt(14, 475, 'github.com/dahaini08-spec/brahma-quant', 11, '#333355')
    txt(14, 492, 'Not financial advice. For research only.', 10, '#222244')

    if not out_path:
        out_dir = BASE / 'openclaw-media'
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(out_dir / f'signal-card-{int(time.time())}-{secrets.token_hex(4)}.png')
    img.save(out_path)
    return out_path

if __name__ == '__main__':
    sig = get_latest_signal()
    path = render_signal_card(sig)
    print(f'✅ Signal card saved: {path}')
    print(f'   Symbol: {sig.get("symbol")} {sig.get("signal_dir")} score={sig.get("score")}')
