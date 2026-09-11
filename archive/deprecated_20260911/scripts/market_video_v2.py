#!/usr/bin/env python3
"""
梵天行情分析视频生成器 v2 — 快速版
K线图(静态) + 文字动画 + 头像 + Edge-TTS语音 → MP4
优化：只渲染一次图表，PIL合成帧，速度提升10x
"""

import asyncio, json, os, sys, time, io, subprocess
from pathlib import Path

import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
import edge_tts
from mutagen.mp3 import MP3
import imageio
import imageio_ffmpeg

WORKSPACE = Path(__file__).parent.parent
AVATAR_PATH = WORKSPACE / "avatar_zhaobuxuan.jpg"
OUTPUT_DIR = WORKSPACE / "data" / "videos"
TEMP_DIR = Path("/tmp")
TTS_VOICE = "zh-CN-YunyangNeural"

WIDTH, HEIGHT, FPS = 1280, 720, 24

# Colors
BG = (15, 15, 20)
PANEL = (25, 25, 35)
GREEN = (0, 200, 100)
RED = (235, 70, 70)
WHITE = (255, 255, 255)
YELLOW = (255, 200, 0)
CYAN = (0, 180, 255)
GRAY = (100, 100, 120)

def get_font(size, bold=False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(path, size)
    except:
        return ImageFont.load_default()

def fetch_klines(symbol, interval='1h', limit=48):
    resp = requests.get(f'https://api.binance.com/api/v3/klines',
        params={'symbol': symbol, 'interval': interval, 'limit': limit})
    return [{'time': d[0], 'open': float(d[1]), 'high': float(d[2]),
             'low': float(d[3]), 'close': float(d[4]), 'volume': float(d[5])}
            for d in resp.json()]

def render_chart(candles, symbol, w=760, h=580):
    """渲染K线图为PIL Image（一次渲染）"""
    df = pd.DataFrame(candles)
    df['datetime'] = pd.to_datetime(df['time'], unit='ms')
    df.set_index('datetime', inplace=True)
    df = df[['open', 'high', 'low', 'close', 'volume']]

    mc = mpf.make_marketcolors(
        up='#00c864', down='#eb4646',
        edge={'up': '#00c864', 'down': '#eb4646'},
        wick={'up': '#00c864', 'down': '#eb4646'})
    style = mpf.make_mpf_style(
        marketcolors=mc, figcolor='#191923', facecolor='#191923',
        edgecolor='#333340', gridstyle='--', gridcolor='#2a2a3a',
        y_on_right=True,
        rc={'font.family': 'DejaVu Sans', 'axes.labelcolor': '#888',
            'xtick.color': '#888', 'ytick.color': '#888'})

    fig, axes = mpf.plot(df, type='candle', style=style, volume=False,
        figsize=(w/100, h/100), returnfig=True, tight_layout=True)
    axes[0].set_title(f'{symbol} 1H', color='white', fontsize=14, pad=10)

    # 标注最新价格
    last_price = candles[-1]['close']
    axes[0].axhline(y=last_price, color='#ffcc00', linestyle='--', linewidth=0.8, alpha=0.7)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', facecolor='#191923', bbox_inches='tight', pad_inches=0.05)
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert('RGB')

async def gen_tts(text, path):
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    await communicate.save(path)
    return MP3(path).info.length

def make_frame(btc_chart, eth_chart, avatar, lines_state, title, show_btc=True, frame_idx=0, total=1):
    """合成一帧"""
    frame = Image.new('RGB', (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(frame)

    f_title = get_font(26, bold=True)
    f_sub = get_font(16)
    f_text = get_font(18, bold=True)
    f_small = get_font(14)

    # 顶部标题栏
    draw.rectangle([0, 0, WIDTH, 55], fill=(20, 20, 30))
    draw.text((20, 12), title, fill=YELLOW, font=f_title)
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    draw.text((WIDTH - 190, 18), ts, fill=(150,150,160), font=f_sub)

    # 左列：头像 + 策略文字
    left_x = 15
    # 头像面板
    av_w, av_h = 440, 240
    draw.rectangle([left_x, 70, left_x+av_w, 70+av_h], fill=PANEL, outline=(60,60,80), width=2)
    if avatar:
        av_img = avatar.copy()
        size = min(av_img.size)
        av_img = av_img.crop((0,0,size,size)).resize((av_h-20, av_h-20))
        frame.paste(av_img, (left_x+15, 80))
    draw.text((left_x+av_w-140, 70+av_h-30), "Zhao Bu Xuan", fill=WHITE, font=f_sub)

    # 策略文字面板
    txt_y = 70 + av_h + 10
    txt_h = HEIGHT - txt_y - 25
    draw.rectangle([left_x, txt_y, left_x+av_w, txt_y+txt_h], fill=PANEL, outline=(60,60,80), width=2)

    # 逐行显示文字
    y = txt_y + 12
    for line_info in lines_state:
        if not line_info['visible']:
            break
        color = line_info.get('color', WHITE)
        font = f_text if line_info.get('size') == 'lg' else f_small
        draw.text((left_x+12, y), line_info['text'], fill=color, font=font)
        y += line_info.get('spacing', 22)

    # 右列：图表
    chart_x = 470
    chart_w = WIDTH - chart_x - 15
    chart_y = 70
    chart_h = HEIGHT - chart_y - 25

    chart = btc_chart if show_btc else eth_chart
    chart_resized = chart.resize((chart_w, chart_h))
    frame.paste(chart_resized, (chart_x, chart_y))

    # 图表边框
    draw.rectangle([chart_x, chart_y, chart_x+chart_w, chart_y+chart_h], outline=(60,60,80), width=2)

    # 底部水印
    draw.text((WIDTH//2-60, HEIGHT-20), "Brahma System", fill=(70,70,90), font=f_sub)

    return np.array(frame)

def generate_video(script, btc_candles, eth_candles, output_path, title="Zhao Bu Xuan | Market Analysis"):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())

    # TTS
    print("[1/4] TTS...")
    audio_path = str(TEMP_DIR / f"tts_{ts}.mp3")
    duration = asyncio.run(gen_tts(script, audio_path))
    total_frames = int(duration * FPS) + 1
    print(f"  {duration:.1f}s, {total_frames} frames")

    # 渲染图表（只渲染一次！）
    print("[2/4] Charts...")
    btc_chart = render_chart(btc_candles, "BTCUSDT")
    eth_chart = render_chart(eth_candles, "ETHUSDT")
    print(f"  BTC: {btc_chart.size}, ETH: {eth_chart.size}")

    # 头像
    avatar = Image.open(AVATAR_PATH).convert('RGB') if AVATAR_PATH.exists() else None

    # 文字内容
    btc_price = btc_candles[-1]['close']
    eth_price = eth_candles[-1]['close']
    btc_h = max(c['high'] for c in btc_candles)
    btc_l = min(c['low'] for c in btc_candles)
    eth_h = max(c['high'] for c in eth_candles)
    eth_l = min(c['low'] for c in eth_candles)
    btc_chg = (btc_price - btc_candles[0]['open']) / btc_candles[0]['open'] * 100
    eth_chg = (eth_price - eth_candles[0]['open']) / eth_candles[0]['open'] * 100

    all_lines = [
        {'text': '=== VIP STRATEGY ===', 'color': YELLOW, 'size': 'lg', 'spacing': 28},
        {'text': '', 'spacing': 8},
        {'text': f'BTC ${btc_price:,.0f} ({btc_chg:+.1f}%)', 'color': GREEN if btc_chg >= 0 else RED, 'spacing': 24},
        {'text': f'H ${btc_h:,.0f} / L ${btc_l:,.0f}', 'color': CYAN, 'spacing': 20},
        {'text': '', 'spacing': 8},
        {'text': f'ETH ${eth_price:,.0f} ({eth_chg:+.1f}%)', 'color': GREEN if eth_chg >= 0 else RED, 'spacing': 24},
        {'text': f'H ${eth_h:,.0f} / L ${eth_l:,.0f}', 'color': CYAN, 'spacing': 20},
        {'text': '', 'spacing': 8},
        {'text': '--- SMC Analysis ---', 'color': YELLOW, 'spacing': 24},
        {'text': 'Step1: FVG Magnet', 'color': WHITE, 'spacing': 20},
        {'text': 'Step2: OB Validation', 'color': WHITE, 'spacing': 20},
        {'text': 'Step3: Liq Map', 'color': WHITE, 'spacing': 20},
        {'text': 'Step4: Resonance', 'color': WHITE, 'spacing': 20},
        {'text': '', 'spacing': 8},
        {'text': 'Brahma System', 'color': GRAY, 'spacing': 20},
    ]

    # 帧渲染
    print("[3/4] Composing frames...")
    btc_end = total_frames // 2  # 前半BTC，后半ETH
    text_reveal_frames = int(total_frames * 0.6)  # 60%时间内逐行显示

    writer = imageio.get_writer(str(TEMP_DIR / f"silent_{ts}.mp4"), fps=FPS, codec='libx264', quality=8)

    for i in range(total_frames):
        # 文字逐行显示
        progress = min(1.0, (i + 1) / text_reveal_frames)
        visible_count = int(len(all_lines) * progress)
        lines_state = []
        for j, line in enumerate(all_lines):
            lines_state.append({**line, 'visible': j < visible_count})

        show_btc = i < btc_end
        frame = make_frame(btc_chart, eth_chart, avatar, lines_state, title, show_btc, i, total_frames)
        writer.append_data(frame)

        if i % 120 == 0:
            print(f"  {i}/{total_frames}")

    writer.close()
    print(f"  All {total_frames} frames done")

    # 合并音频
    print("[4/4] Merging audio...")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    silent = str(TEMP_DIR / f"silent_{ts}.mp4")
    cmd = [ffmpeg, '-y', '-i', silent, '-i', audio_path,
           '-c:v', 'libx264', '-preset', 'fast', '-c:a', 'aac', '-shortest', output_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    os.remove(silent)

    if r.returncode == 0:
        size = os.path.getsize(output_path)
        print(f"  Done: {size/1024/1024:.1f}MB -> {output_path}")
        return {"path": output_path, "duration": duration, "size": size}
    else:
        print(f"  Error: {r.stderr[:300]}")
        return None

def main():
    print("=== Brahma Market Video v2 ===\n")
    btc = fetch_klines('BTCUSDT', '1h', 48)
    eth = fetch_klines('ETHUSDT', '1h', 48)
    print(f"BTC: ${btc[-1]['close']:.0f} | ETH: ${eth[-1]['close']:.0f}")

    script = f"Zhao Bu Xuan, today market analysis. "
    script += f"Bitcoin current price {int(btc[-1]['close'])} US dollars. "
    script += f"24 hour high {int(max(c['high'] for c in btc))}, low {int(min(c['low'] for c in btc))}. "
    script += f"Ethereum current price {int(eth[-1]['close'])} US dollars. "
    script += f"Data speaks, structure is king. Brahma system."

    out = str(OUTPUT_DIR / f"market_{int(time.time())}.mp4")
    result = generate_video(script, btc, eth, out)
    if result:
        print(f"\n=== COMPLETE ===\n{json.dumps(result, indent=2)}")

if __name__ == "__main__":
    main()
