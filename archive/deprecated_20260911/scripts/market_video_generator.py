#!/usr/bin/env python3
"""
梵天行情分析视频生成器
K线图表 + 文字叠加 + 头像 + Edge-TTS语音 → MP4视频
"""

import asyncio
import json
import os
import sys
import time
import io
from pathlib import Path

import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import mplfinance as mpf
import edge_tts
from mutagen.mp3 import MP3
import imageio
import imageio_ffmpeg
import subprocess

# ── 配置 ──
WORKSPACE = Path(__file__).parent.parent
AVATAR_PATH = WORKSPACE / "avatar_zhaobuxuan.jpg"
OUTPUT_DIR = WORKSPACE / "data" / "videos"
TEMP_DIR = Path("/tmp")
TTS_VOICE = "zh-CN-YunyangNeural"
TTS_RATE = "+0%"

# 视频参数
WIDTH = 1280
HEIGHT = 720
FPS = 24

# 颜色
BG_COLOR = (15, 15, 20)
PANEL_COLOR = (25, 25, 35)
GREEN = (0, 200, 100)
RED = (235, 70, 70)
WHITE = (255, 255, 255)
YELLOW = (255, 200, 0)
CYAN = (0, 180, 255)

# 字体（用系统默认，ASCII only）
plt.rcParams['font.family'] = 'DejaVu Sans'


def fetch_klines(symbol: str, interval: str = '1h', limit: int = 48):
    """拉取K线数据"""
    resp = requests.get(
        f'https://api.binance.com/api/v3/klines',
        params={'symbol': symbol, 'interval': interval, 'limit': limit}
    )
    data = resp.json()
    candles = []
    for d in data:
        candles.append({
            'time': d[0],
            'open': float(d[1]),
            'high': float(d[2]),
            'low': float(d[3]),
            'close': float(d[4]),
            'volume': float(d[5]),
        })
    return candles


def render_candlestick_chart(candles, symbol, width_px=800, height_px=400, visible_bars=None):
    """渲染K线图为PIL Image"""
    if visible_bars is None:
        visible_bars = len(candles)

    # 只显示最近的visible_bars根
    show = candles[-visible_bars:]

    # 用mplfinance渲染
    import pandas as pd
    df = pd.DataFrame(show)
    df['datetime'] = pd.to_datetime(df['time'], unit='ms')
    df.set_index('datetime', inplace=True)
    df = df[['open', 'high', 'low', 'close', 'volume']]

    # 自定义样式
    mc = mpf.make_marketcolors(
        up='#00c864', down='#eb4646',
        edge={'up': '#00c864', 'down': '#eb4646'},
        wick={'up': '#00c864', 'down': '#eb4646'},
        volume={'up': '#00c86480', 'down': '#eb464680'},
    )
    style = mpf.make_mpf_style(
        marketcolors=mc,
        figcolor='#191923',
        facecolor='#191923',
        edgecolor='#333340',
        gridstyle='--',
        gridcolor='#2a2a3a',
        y_on_right=True,
        rc={
            'font.family': 'DejaVu Sans',
            'axes.labelcolor': '#888',
            'xtick.color': '#888',
            'ytick.color': '#888',
        }
    )

    fig, axes = mpf.plot(
        df, type='candle', style=style,
        volume=False,
        figsize=(width_px / 100, height_px / 100),
        returnfig=True,
        tight_layout=True,
    )

    # 标题
    axes[0].set_title(f'{symbol} 1H', color='white', fontsize=14, pad=10)

    # 转PIL Image
    buf = io.BytesIO()
    fig.savefig(buf, format='png', facecolor='#191923', bbox_inches='tight', pad_inches=0.1)
    plt.close(fig)
    buf.seek(0)
    img = Image.open(buf).convert('RGB')
    return img


def render_candlestick_progressive(candles, symbol, total_frames, fps, width_px=800, height_px=400):
    """渐进式K线渲染：从右往左逐步展开"""
    max_bars = len(candles)
    images = []
    # 前30%帧：图表从少到多展开
    expand_frames = int(total_frames * 0.3)
    # 后70%帧：完整图表（静止）
    static_frames = total_frames - expand_frames

    for i in range(expand_frames):
        bars = max(5, int(max_bars * (i + 1) / expand_frames))
        img = render_candlestick_chart(candles, symbol, width_px, height_px, visible_bars=bars)
        images.append(img)

    # 静态部分用同一张图
    full_img = render_candlestick_chart(candles, symbol, width_px, height_px)
    for i in range(static_frames):
        images.append(full_img)

    return images


def create_text_panel(lines, width=440, height=400, progress=1.0):
    """创建文字面板（VIP策略文字）"""
    img = Image.new('RGB', (width, height), PANEL_COLOR)
    draw = ImageDraw.Draw(img)

    # 边框
    draw.rectangle([0, 0, width-1, height-1], outline=(60, 60, 80), width=2)

    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except:
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()

    y = 15
    total_lines = len(lines)
    visible_lines = int(total_lines * progress)

    for i, line in enumerate(lines[:visible_lines]):
        color = WHITE
        if 'RED' in line.get('color', ''):
            color = RED
        elif 'GREEN' in line.get('color', ''):
            color = GREEN
        elif 'YELLOW' in line.get('color', ''):
            color = YELLOW
        elif 'CYAN' in line.get('color', ''):
            color = CYAN

        font = font_large if line.get('size') == 'large' else font_medium
        draw.text((15, y), line['text'], fill=color, font=font)
        y += line.get('spacing', 24)

    return img


def create_avatar_panel(width=440, height=280):
    """创建头像面板"""
    img = Image.new('RGB', (width, height), PANEL_COLOR)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width-1, height-1], outline=(60, 60, 80), width=2)

    if AVATAR_PATH.exists():
        avatar = Image.open(AVATAR_PATH).convert('RGB')
        # 裁剪为正方形并缩放
        size = min(avatar.size)
        avatar = avatar.crop((0, 0, size, size))
        avatar_size = min(width - 40, height - 60)
        avatar = avatar.resize((avatar_size, avatar_size))
        x = (width - avatar_size) // 2
        img.paste(avatar, (x, 10))

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
    except:
        font = ImageFont.load_default()
    draw.text((width // 2 - 60, height - 35), "Zhao Bu Xuan", fill=WHITE, font=font)

    return img


def compose_frame(chart_img, text_img, avatar_img, title_text, frame_w=1280, frame_h=720):
    """合成一帧"""
    frame = Image.new('RGB', (frame_w, frame_h), BG_COLOR)
    draw = ImageDraw.Draw(frame)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    # 顶部标题栏
    draw.rectangle([0, 0, frame_w, 60], fill=(20, 20, 30))
    draw.text((20, 15), title_text, fill=YELLOW, font=font_title)
    # 时间戳
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    draw.text((frame_w - 200, 20), ts, fill=(150, 150, 160), font=font_sub)

    # 布局
    # 左列：头像 + 文字
    left_x = 20
    # 右列：图表
    right_x = 480
    chart_w = frame_w - right_x - 20

    # 头像面板
    avatar_y = 80
    avatar_w = 440
    avatar_h = 280
    frame.paste(avatar_img, (left_x, avatar_y))

    # 文字面板
    text_y = avatar_y + avatar_h + 15
    text_w = 440
    text_h = frame_h - text_y - 20
    frame.paste(text_img, (left_x, text_y))

    # 图表面板
    chart_y = 80
    chart_h = frame_h - chart_y - 20
    # 缩放图表到合适尺寸
    chart_resized = chart_img.resize((chart_w, chart_h))
    frame.paste(chart_resized, (right_x, chart_y))

    # 底部水印
    draw.text((frame_w // 2 - 80, frame_h - 25), "Brahma System", fill=(80, 80, 100), font=font_sub)

    return frame


async def generate_tts(text, output_path):
    """Edge-TTS语音合成"""
    communicate = edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE)
    await communicate.save(output_path)
    audio = MP3(output_path)
    return audio.info.length


def generate_video(script_text, btc_candles, eth_candles, output_path, title_text="Zhao Bu Xuan | Market Analysis"):
    """完整视频生成链路"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())

    # Step 1: TTS
    print("[1/5] TTS generating...")
    audio_path = str(TEMP_DIR / f"tts_{ts}.mp3")
    duration = asyncio.run(generate_tts(script_text, audio_path))
    print(f"  TTS: {duration:.1f}s")

    total_frames = int(duration * FPS) + 1
    print(f"  Total frames: {total_frames}")

    # Step 2: 渲染K线图（BTC + ETH）
    print("[2/5] Rendering charts...")
    btc_chart_imgs = render_candlestick_progressive(btc_candles, "BTCUSDT", total_frames, FPS, 800, 600)
    print(f"  BTC charts: {len(btc_chart_imgs)} frames")

    # ETH charts (复用同一帧列表，后半段显示ETH)
    eth_chart_imgs = render_candlestick_progressive(eth_candles, "ETHUSDT", total_frames, FPS, 800, 600)
    print(f"  ETH charts: {len(eth_chart_imgs)} frames")

    # Step 3: 文字面板内容
    text_lines = [
        {"text": "=== VIP STRATEGY ===", "color": "YELLOW", "size": "large", "spacing": 30},
        {"text": "", "spacing": 10},
        {"text": f"BTC: ${btc_candles[-1]['close']:.0f}", "color": "WHITE", "spacing": 24},
        {"text": f"24H H: ${max(c['high'] for c in btc_candles):.0f}", "color": "CYAN", "spacing": 20},
        {"text": f"24H L: ${min(c['low'] for c in btc_candles):.0f}", "color": "CYAN", "spacing": 20},
        {"text": "", "spacing": 10},
        {"text": f"ETH: ${eth_candles[-1]['close']:.0f}", "color": "WHITE", "spacing": 24},
        {"text": f"24H H: ${max(c['high'] for c in eth_candles):.0f}", "color": "CYAN", "spacing": 20},
        {"text": f"24H L: ${min(c['low'] for c in eth_candles):.0f}", "color": "CYAN", "spacing": 20},
        {"text": "", "spacing": 10},
        {"text": "FVG: Check magnet", "color": "YELLOW", "spacing": 22},
        {"text": "OB: Validate age<50", "color": "YELLOW", "spacing": 22},
        {"text": "Liq: Map stops", "color": "YELLOW", "spacing": 22},
        {"text": "Resonance: FVG+OB+Liq", "color": "YELLOW", "spacing": 22},
    ]

    # Step 4: 渲染帧
    print("[3/5] Composing frames...")
    avatar_img = create_avatar_panel()

    # 前半段显示BTC，后半段显示ETH
    btc_end = total_frames // 2

    frames = []
    for i in range(total_frames):
        # 图表切换
        if i < btc_end:
            chart = btc_chart_imgs[i] if i < len(btc_chart_imgs) else btc_chart_imgs[-1]
        else:
            chart = eth_chart_imgs[i] if i < len(eth_chart_imgs) else eth_chart_imgs[-1]

        # 文字渐进显示
        progress = min(1.0, (i + 1) / (total_frames * 0.7))
        text_img = create_text_panel(text_lines, progress=progress)

        frame = compose_frame(chart, text_img, avatar_img, title_text)
        frames.append(np.array(frame))

        if i % 60 == 0:
            print(f"  Frame {i}/{total_frames}")

    print(f"  All {total_frames} frames composed")

    # Step 5: 写视频（无音频）
    print("[4/5] Writing silent video...")
    silent_path = str(TEMP_DIR / f"silent_{ts}.mp4")
    writer = imageio.get_writer(silent_path, fps=FPS, codec='libx264', quality=8)
    for frame in frames:
        writer.append_data(frame)
    writer.close()

    # Step 6: 合并音频
    print("[5/5] Merging audio...")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg, '-y',
        '-i', silent_path,
        '-i', audio_path,
        '-c:v', 'libx264', '-preset', 'fast',
        '-c:a', 'aac',
        '-shortest',
        output_path
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        size = os.path.getsize(output_path)
        print(f"  Done: {size/1024/1024:.1f}MB -> {output_path}")
    else:
        print(f"  Error: {r.stderr[:500]}")
        return None

    # 清理
    os.remove(silent_path)

    return {
        "path": output_path,
        "duration": duration,
        "size": os.path.getsize(output_path),
        "frames": total_frames,
    }


def main():
    print("=== Brahma Market Analysis Video Generator ===\n")

    # 拉取数据
    print("Fetching market data...")
    btc_candles = fetch_klines('BTCUSDT', '1h', 48)
    eth_candles = fetch_klines('ETHUSDT', '1h', 48)
    btc_price = btc_candles[-1]['close']
    eth_price = eth_candles[-1]['close']
    btc_24h_high = max(c['high'] for c in btc_candles)
    btc_24h_low = min(c['low'] for c in btc_candles)

    print(f"BTC: ${btc_price:.0f} | 24H H/L: ${btc_24h_high:.0f}/${btc_24h_low:.0f}")
    print(f"ETH: ${eth_price:.0f}")

    # 文字稿
    script = f"Zhao Bu Xuan, today market analysis. "
    script += f"Bitcoin current price {int(btc_price)} US dollars. "
    script += f"24 hour high {int(btc_24h_high)}, low {int(btc_24h_low)}. "
    script += f"Ethereum current price {int(eth_price)} US dollars. "
    script += f"Data speaks, structure is king. Brahma system."

    # 生成视频
    output = str(OUTPUT_DIR / f"market_analysis_{int(time.time())}.mp4")
    result = generate_video(script, btc_candles, eth_candles, output)

    if result:
        print(f"\n=== COMPLETE ===")
        print(json.dumps(result, indent=2))
    else:
        print("\n=== FAILED ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
