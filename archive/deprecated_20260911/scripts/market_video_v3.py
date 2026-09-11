#!/usr/bin/env python3
"""
梵天行情分析视频 v3 — 方案B布局 884×480 对标参考视频
精华版：3分钟完整BTC+ETH+CPI分析
"""
import asyncio, json, os, sys, time, io, subprocess, re
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

# 对标参考视频 884×480
W, H, FPS = 884, 480, 24

BG = (12, 12, 18)
PANEL = (22, 22, 32)
GREEN = (0, 200, 100)
RED = (235, 70, 70)
WHITE = (240, 240, 245)
YELLOW = (255, 200, 0)
CYAN = (0, 180, 255)
GRAY = (100, 100, 120)
BORDER = (55, 55, 70)

def get_font(size, bold=False):
    p = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try: return ImageFont.truetype(p, size)
    except: return ImageFont.load_default()

def fetch_klines(symbol, interval='1h', limit=48):
    r = requests.get(f'https://api.binance.com/api/v3/klines',
        params={'symbol': symbol, 'interval': interval, 'limit': limit})
    return [{'time':d[0],'open':float(d[1]),'high':float(d[2]),'low':float(d[3]),'close':float(d[4]),'volume':float(d[5])} for d in r.json()]

def render_chart(candles, symbol, w=560, h=260):
    df = pd.DataFrame(candles)
    df['datetime'] = pd.to_datetime(df['time'], unit='ms')
    df.set_index('datetime', inplace=True)
    df = df[['open','high','low','close','volume']]
    mc = mpf.make_marketcolors(up='#00c864', down='#eb4646',
        edge={'up':'#00c864','down':'#eb4646'},
        wick={'up':'#00c864','down':'#eb4646'})
    style = mpf.make_mpf_style(marketcolors=mc, figcolor='#16161f', facecolor='#16161f',
        edgecolor='#333340', gridstyle='--', gridcolor='#2a2a3a', y_on_right=True,
        rc={'font.family':'DejaVu Sans','axes.labelcolor':'#888','xtick.color':'#888','ytick.color':'#888'})
    fig, axes = mpf.plot(df, type='candle', style=style, volume=False,
        figsize=(w/100, h/100), returnfig=True, tight_layout=True)
    axes[0].set_title(f'{symbol} 1H', color='white', fontsize=11, pad=6)
    # 最新价格线
    lp = candles[-1]['close']
    axes[0].axhline(y=lp, color='#ffcc00', linestyle='--', linewidth=0.6, alpha=0.6)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', facecolor='#16161f', bbox_inches='tight', pad_inches=0.03)
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert('RGB')

async def gen_tts(text, path):
    c = edge_tts.Communicate(text, TTS_VOICE)
    await c.save(path)
    return MP3(path).info.length

# ── 场景定义 ──
# 每个场景：图表 + 文字行 + 语音段
# 根据语音时间戳划分场景

def make_scene_frame(chart_img, avatar, title, text_lines, visible_lines, show_avatar=True):
    """方案B布局：上讲者下图表+策略"""
    frame = Image.new('RGB', (W, H), BG)
    draw = ImageDraw.Draw(frame)
    
    f_title = get_font(18, bold=True)
    f_sub = get_font(11)
    f_text = get_font(13, bold=True)
    f_small = get_font(11)
    f_micro = get_font(10)
    
    # 顶部标题栏 (28px)
    draw.rectangle([0, 0, W, 28], fill=(18, 18, 28))
    draw.text((8, 5), title, fill=YELLOW, font=f_title)
    ts = time.strftime("%m-%d %H:%M UTC", time.gmtime())
    draw.text((W-110, 8), ts, fill=(130,130,145), font=f_sub)
    
    y_cursor = 32
    
    # 上半区：头像 + 关键数据 (110px)
    av_zone_h = 100
    draw.rectangle([5, y_cursor, W-5, y_cursor+av_zone_h], fill=PANEL, outline=BORDER, width=1)
    
    if show_avatar and avatar:
        av = avatar.copy()
        sz = min(av.size)
        av = av.crop((0,0,sz,sz)).resize((80, 80))
        frame.paste(av, (12, y_cursor+10))
    
    # 头像右侧：关键数据
    data_x = 100
    data_y = y_cursor + 8
    
    for i, line in enumerate(text_lines.get('data_lines', [])[:5]):
        if i >= visible_lines.get('data', 999):
            break
        color = line.get('color', WHITE)
        draw.text((data_x, data_y + i*16), line['text'], fill=color, font=f_small)
    
    y_cursor += av_zone_h + 4
    
    # 中部：K线图 (200px)
    chart_h = 200
    chart_x = 5
    chart_w = W - 10
    chart_resized = chart_img.resize((chart_w, chart_h))
    frame.paste(chart_resized, (chart_x, y_cursor))
    draw.rectangle([chart_x, y_cursor, chart_x+chart_w-1, y_cursor+chart_h-1], outline=BORDER, width=1)
    
    y_cursor += chart_h + 4
    
    # 底部：策略文字 (剩余空间)
    strat_h = H - y_cursor - 18
    draw.rectangle([5, y_cursor, W-5, y_cursor+strat_h], fill=PANEL, outline=BORDER, width=1)
    
    sy = y_cursor + 6
    for i, line in enumerate(text_lines.get('strategy_lines', [])):
        if i >= visible_lines.get('strategy', 999):
            break
        color = line.get('color', WHITE)
        font = f_text if line.get('size') == 'lg' else f_small
        draw.text((10, sy), line['text'], fill=color, font=font)
        sy += line.get('spacing', 15)
        if sy > y_cursor + strat_h - 5:
            break
    
    # 底部水印
    draw.text((W//2-55, H-14), "Brahma System", fill=(60,60,75), font=f_micro)
    
    return np.array(frame)


def main():
    print("=== Brahma Market Video v3 (884x480) ===\n")
    
    # 拉取数据
    btc = fetch_klines('BTCUSDT', '1h', 48)
    eth = fetch_klines('ETHUSDT', '1h', 48)
    btc_price = btc[-1]['close']
    eth_price = eth[-1]['close']
    btc_h = max(c['high'] for c in btc)
    btc_l = min(c['low'] for c in btc)
    eth_h = max(c['high'] for c in eth)
    eth_l = min(c['low'] for c in eth)
    btc_chg = (btc_price - btc[0]['open']) / btc[0]['open'] * 100
    eth_chg = (eth_price - eth[0]['open']) / eth[0]['open'] * 100
    
    print(f"BTC: ${btc_price:.0f} ({btc_chg:+.1f}%) H=${btc_h:.0f} L=${btc_l:.0f}")
    print(f"ETH: ${eth_price:.0f} ({eth_chg:+.1f}%) H=${eth_h:.0f} L=${eth_l:.0f}")
    
    # 精华版逐字稿
    script = f"""早上好，这里是梵天早间战场，我是苏摩。今天是9月10号，CPI数据日特别版。
比特币当前{int(btc_price)}，24小时跌{abs(btc_chg):.1f}%。日内最高{int(btc_h)}，最低{int(btc_l)}。波动率IV仅1.1%，创一年新低。
4小时K线放量下跌后缩量反弹，上方卖压强。上方80000到81000是空头止损墙，做市商被迫卖出。下方76268是多头止损最密集位，超过50亿。
大户67.9%做多，但Taker买卖比0.818，大户在悄悄出货。散户53.6%做多，大户卖散户买。
BTC判断：CPI前77700到79700震荡，80000以上是空单区不是追多区。
以太坊当前{int(eth_price)}，24小时跌{abs(eth_chg):.1f}%，跌幅是BTC三倍。Bear FVG中点2470是反弹第一阻力。
2487到2510是空单入场区。ETH未平仓量55.6亿，占山寨67%，是清算核心。
ETH判断：反弹到2487到2510做空，目标2400到2350。
山寨未平仓量超过BTC，86亿对82亿。上次这个信号是2024年12月，之后单日清算15.8亿。山寨不碰。
CPI今晚8点半。偏鹰40%概率，跌破77700跟空到76268。符合预期45%，震荡不动。低于预期15%，反弹到空单区。
四条铁律：CPI前不赌方向，数据后跟15分钟结构，仓位减半，18号日本央行才是主菜。
我是苏摩，梵天系统数据驱动，不是建议。明天见。"""
    
    print(f"\nScript: {len(script)} chars")
    
    # TTS
    ts = int(time.time())
    audio_path = str(TEMP_DIR / f"tts_{ts}.mp3")
    print("[1/4] TTS...", end=" ", flush=True)
    duration = asyncio.run(gen_tts(script, audio_path))
    total_frames = int(duration * FPS) + 1
    print(f"{duration:.1f}s, {total_frames} frames")
    
    # 图表
    print("[2/4] Charts...", end=" ", flush=True)
    btc_chart = render_chart(btc, "BTCUSDT")
    eth_chart = render_chart(eth, "ETHUSDT")
    print("done")
    
    # 头像
    avatar = Image.open(AVATAR_PATH).convert('RGB') if AVATAR_PATH.exists() else None
    
    # 场景划分（按语音时间比例）
    # Scene 1: 开场+BTC (0-40%)
    # Scene 2: ETH (40-65%)
    # Scene 3: 山寨+CPI+收尾 (65-100%)
    s1_end = int(total_frames * 0.40)
    s2_end = int(total_frames * 0.65)
    
    btc_color = GREEN if btc_chg >= 0 else RED
    eth_color = GREEN if eth_chg >= 0 else RED
    
    scene1_text = {
        'data_lines': [
            {'text': f'BTC ${btc_price:,.0f} ({btc_chg:+.1f}%)', 'color': btc_color},
            {'text': f'24H H ${btc_h:,.0f} / L ${btc_l:,.0f}', 'color': CYAN},
            {'text': 'IV 1.1% (1yr low)', 'color': YELLOW},
            {'text': 'Taker 0.818 (bearish)', 'color': RED},
            {'text': 'Whales 67.9% long -> selling', 'color': YELLOW},
        ],
        'strategy_lines': [
            {'text': '=== BTC STRATEGY ===', 'color': YELLOW, 'size': 'lg', 'spacing': 16},
            {'text': f'Resistance: $80,000-$81,000', 'color': RED, 'spacing': 13},
            {'text': f'Support: $77,706 / $76,268', 'color': CYAN, 'spacing': 13},
            {'text': '', 'spacing': 6},
            {'text': f'Range: $77,700 - $79,700', 'color': WHITE, 'spacing': 13},
            {'text': f'> $80,000 = SHORT ZONE', 'color': RED, 'spacing': 13},
            {'text': f'FVG magnet: DOWN', 'color': YELLOW, 'spacing': 13},
            {'text': f'OB: valid, not breached', 'color': GREEN, 'spacing': 13},
            {'text': f'Liq: $50B+ stops above 80K', 'color': CYAN, 'spacing': 13},
        ]
    }
    
    scene2_text = {
        'data_lines': [
            {'text': f'ETH ${eth_price:,.0f} ({eth_chg:+.1f}%)', 'color': eth_color},
            {'text': f'24H H ${eth_h:,.0f} / L ${eth_l:,.0f}', 'color': CYAN},
            {'text': 'Beta 3x vs BTC (weaker)', 'color': RED},
            {'text': 'OI $5.56B (67% altcoin)', 'color': YELLOW},
            {'text': 'FVG midpoint: $2,470', 'color': CYAN},
        ],
        'strategy_lines': [
            {'text': '=== ETH STRATEGY ===', 'color': YELLOW, 'size': 'lg', 'spacing': 16},
            {'text': f'SHORT $2,487-$2,510', 'color': RED, 'spacing': 13},
            {'text': f'SL: $2,546 (stop hunt)', 'color': WHITE, 'spacing': 13},
            {'text': f'TP1: $2,400 / TP2: $2,350', 'color': GREEN, 'spacing': 13},
            {'text': '', 'spacing': 6},
            {'text': f'Bear FVG: magnet DOWN', 'color': YELLOW, 'spacing': 13},
            {'text': f'Resonance: $2,421-$2,458', 'color': CYAN, 'spacing': 13},
            {'text': f'Whales 61.5% -> reducing', 'color': YELLOW, 'spacing': 13},
        ]
    }
    
    scene3_text = {
        'data_lines': [
            {'text': 'Altcoin OI > BTC OI', 'color': RED},
            {'text': 'Alt $8.6B vs BTC $8.2B', 'color': YELLOW},
            {'text': 'Last signal: Dec 2024', 'color': CYAN},
            {'text': 'Result: $1.58B liquidation', 'color': RED},
            {'text': 'CPI 20:30 UTC TODAY', 'color': YELLOW},
        ],
        'strategy_lines': [
            {'text': '=== CPI DAY PLAN ===', 'color': YELLOW, 'size': 'lg', 'spacing': 16},
            {'text': 'Hawkish 40%: short to 76268', 'color': RED, 'spacing': 13},
            {'text': 'Neutral 45%: hold range', 'color': WHITE, 'spacing': 13},
            {'text': 'Dovish 15%: short bounce', 'color': GREEN, 'spacing': 13},
            {'text': '', 'spacing': 6},
            {'text': 'RULE 1: No pre-CPI bets', 'color': YELLOW, 'spacing': 13},
            {'text': 'RULE 2: Follow 15m CHoCH', 'color': YELLOW, 'spacing': 13},
            {'text': 'RULE 3: Half position size', 'color': YELLOW, 'spacing': 13},
            {'text': 'RULE 4: BOJ Sep 18 = main event', 'color': RED, 'spacing': 13},
        ]
    }
    
    # 渲染帧
    print("[3/4] Rendering frames...", flush=True)
    silent_path = str(TEMP_DIR / f"silent_{ts}.mp4")
    writer = imageio.get_writer(silent_path, fps=FPS, codec='libx264', quality=8)
    
    for i in range(total_frames):
        # 场景选择
        if i < s1_end:
            chart = btc_chart
            scene = scene1_text
            title = "Zhao Bu Xuan | BTC Analysis"
        elif i < s2_end:
            chart = eth_chart
            scene = scene2_text
            title = "Zhao Bu Xuan | ETH Analysis"
        else:
            chart = btc_chart  # 最后场景用BTC图
            scene = scene3_text
            title = "Zhao Bu Xuan | CPI Strategy"
        
        # 文字逐行显示（每个场景内独立进度）
        if i < s1_end:
            progress = (i + 1) / s1_end
        elif i < s2_end:
            progress = (i + 1 - s1_end) / (s2_end - s1_end)
        else:
            progress = (i + 1 - s2_end) / (total_frames - s2_end)
        
        vis_data = int(len(scene['data_lines']) * min(1.0, progress * 1.5))
        vis_strat = int(len(scene['strategy_lines']) * min(1.0, progress))
        
        frame = make_scene_frame(chart, avatar, title, scene,
            {'data': vis_data, 'strategy': vis_strat})
        writer.append_data(frame)
        
        if i % 300 == 0:
            print(f"  {i}/{total_frames} ({i/total_frames*100:.0f}%)")
    
    writer.close()
    print(f"  All {total_frames} frames done")
    
    # 合并音频
    print("[4/4] Merging audio...", end=" ", flush=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    output = str(OUTPUT_DIR / f"morning_brief_{ts}.mp4")
    cmd = [ffmpeg, '-y', '-i', silent_path, '-i', audio_path,
           '-c:v', 'libx264', '-preset', 'fast', '-c:a', 'aac', '-shortest', output]
    r = subprocess.run(cmd, capture_output=True, text=True)
    os.remove(silent_path)
    
    if r.returncode == 0:
        sz = os.path.getsize(output)
        print(f"Done: {sz/1024/1024:.1f}MB -> {output}")
        print(f"\n=== COMPLETE ===")
        print(f"Duration: {duration:.1f}s | Size: {sz/1024/1024:.1f}MB | Frames: {total_frames}")
        print(f"Path: {output}")
    else:
        print(f"Error: {r.stderr[:300]}")

if __name__ == "__main__":
    main()
