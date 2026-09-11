#!/usr/bin/env python3
"""
梵天行情分析视频 v4 — 专业博客风格
核心升级：
1. 3D立体头像（PIL光影效果+圆形遮罩+渐变阴影）
2. 语音-图表自动匹配（说到BTC显示BTC图表，说到ETH显示ETH图表）
3. K线图+FVG/OB/价位标注
4. 全中文文字
5. 底部滚动价格条
6. 场景淡入淡出
"""
import asyncio, json, os, sys, time, io, subprocess, math
from pathlib import Path

import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
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

W, H, FPS = 884, 480, 24

# 提亮的配色
BG = (18, 24, 40)
PANEL = (28, 34, 52)
PANEL_LIGHT = (38, 44, 62)
GREEN = (0, 210, 110)
RED = (240, 80, 80)
WHITE = (245, 245, 250)
YELLOW = (255, 210, 30)
CYAN = (0, 190, 255)
ORANGE = (255, 150, 50)
GRAY = (120, 120, 140)
BORDER = (60, 66, 86)
GOLD = (255, 200, 80)

def get_font(size, bold=False):
    p = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try: return ImageFont.truetype(p, size)
    except: return ImageFont.load_default()

def fetch_klines(symbol, interval='1h', limit=48):
    r = requests.get(f'https://api.binance.com/api/v3/klines',
        params={'symbol': symbol, 'interval': interval, 'limit': limit})
    return [{'time':d[0],'open':float(d[1]),'high':float(d[2]),'low':float(d[3]),'close':float(d[4]),'volume':float(d[5])} for d in r.json()]

def render_chart_annotated(candles, symbol, w=560, h=280, annotations=None):
    """渲染K线图+FVG/OB/价位标注"""
    df = pd.DataFrame(candles)
    df['datetime'] = pd.to_datetime(df['time'], unit='ms')
    df.set_index('datetime', inplace=True)
    df = df[['open','high','low','close','volume']]
    
    mc = mpf.make_marketcolors(up='#00d060', down='#f05050',
        edge={'up':'#00d060','down':'#f05050'},
        wick={'up':'#00d060','down':'#f05050'})
    style = mpf.make_mpf_style(marketcolors=mc, figcolor='#1c2840', facecolor='#1c2840',
        edgecolor='#3a4060', gridstyle='--', gridcolor='#2a3050', y_on_right=True,
        rc={'font.family':'DejaVu Sans','axes.labelcolor':'#999','xtick.color':'#999','ytick.color':'#999'})
    
    # 添加附加图（FVG/OB标注用）
    addplots = []
    
    if annotations:
        for ann in annotations:
            if ann['type'] == 'fvg':
                # FVG半透明矩形
                idx_start = ann.get('idx_start', 0)
                idx_end = ann.get('idx_end', len(candles)-1)
                top = ann['top']
                bot = ann['bottom']
                fvg_data = [top if i >= idx_start and i <= idx_end else float('nan') for i in range(len(candles))]
                fvg_fill = [bot if i >= idx_start and i <= idx_end else float('nan') for i in range(len(candles))]
                ap = mpf.make_addplot(fvg_data, panel=0, type='line', color='#ff8800', alpha=0.3)
                addplots.append(ap)
            elif ann['type'] == 'hline':
                # 水平线（入场/止损/目标）
                level = ann['level']
                color = ann.get('color', '#ffcc00')
                ls = ann.get('linestyle', '--')
                line_data = [level] * len(candles)
                ap = mpf.make_addplot(line_data, panel=0, type='line', color=color, linestyle=ls, alpha=0.7)
                addplots.append(ap)
    
    kwargs = dict(type='candle', style=style, volume=False,
        figsize=(w/100, h/100), returnfig=True, tight_layout=True)
    
    if addplots:
        kwargs['addplot'] = addplots
    
    fig, axes = mpf.plot(df, **kwargs)
    axes[0].set_title(f'{symbol} 1H', color='white', fontsize=12, pad=8)
    
    # 标注文字
    if annotations:
        for ann in annotations:
            if ann['type'] == 'hline':
                axes[0].annotate(ann.get('label', ''), 
                    xy=(len(candles)-1, ann['level']),
                    xytext=(len(candles)-5, ann['level']),
                    color=ann.get('color', '#ffcc00'), fontsize=8,
                    arrowprops=dict(arrowstyle='->', color=ann.get('color', '#ffcc00'), lw=0.5))
    
    buf = io.BytesIO()
    fig.savefig(buf, format='png', facecolor='#1c2840', bbox_inches='tight', pad_inches=0.05, dpi=150)
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert('RGB')


def create_3d_avatar(avatar_img, size=110):
    """3D立体头像：圆形遮罩+渐变阴影+高光+边框"""
    if avatar_img is None:
        return Image.new('RGB', (size, size), PANEL)
    
    # 裁剪为正方形
    sz = min(avatar_img.size)
    avatar = avatar_img.crop((0, 0, sz, sz)).resize((size, size))
    
    # 圆形遮罩
    mask = Image.new('L', (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([0, 0, size-1, size-1], fill=255)
    
    # 应用遮罩
    result = Image.new('RGB', (size, size), PANEL)
    result.paste(avatar, (0, 0), mask)
    
    # 加高光（左上角光效）
    highlight = Image.new('L', (size, size), 0)
    hd = ImageDraw.Draw(highlight)
    for r in range(size//2, 0, -1):
        alpha = int(40 * (1 - r / (size//2)))
        hd.ellipse([size//4 - r//2, size//4 - r//2, size//4 + r//2, size//4 + r//2], fill=alpha)
    highlight = highlight.filter(ImageFilter.GaussianBlur(radius=15))
    result = Image.composite(
        ImageEnhance.Brightness(result).enhance(1.3),
        result, highlight)
    
    # 加底部阴影（3D深度感）
    shadow = Image.new('RGBA', (size+10, size+10), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.ellipse([5, 5, size+4, size+4], fill=(0, 0, 0, 80))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=8))
    
    # 合成
    final = Image.new('RGB', (size+10, size+12), (0, 0, 0, 0))
    final.paste(shadow.convert('RGB'), (0, 0))
    final.paste(result, (5, 2), mask)
    
    # 金色边框环
    ring = Image.new('RGBA', (size+10, size+12), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    rd.ellipse([3, 0, size+6, size+3], outline=GOLD, width=2)
    
    final_rgba = final.convert('RGBA')
    final_rgba = Image.alpha_composite(final_rgba, ring)
    
    return final_rgba


def make_scroll_text(text, x_offset, y, font, color, width):
    """滚动文字"""
    # 简单实现：返回带偏移的文字
    return text, x_offset


def compose_frame_v4(chart_img, avatar_3d, scene_data, title, scroll_offset, fade_alpha=0, show_avatar=True):
    """v4帧合成 — 专业博客风格"""
    frame = Image.new('RGB', (W, H), BG)
    draw = ImageDraw.Draw(frame)
    
    f_title = get_font(17, bold=True)
    f_sub = get_font(11)
    f_data = get_font(14, bold=True)
    f_text = get_font(13, bold=True)
    f_small = get_font(11)
    f_micro = get_font(9)
    f_price = get_font(13, bold=True)
    
    # === 顶部标题栏 (30px) ===
    draw.rectangle([0, 0, W, 30], fill=(20, 28, 48))
    draw.line([0, 30, W, 30], fill=GOLD, width=1)
    draw.text((8, 6), title, fill=GOLD, font=f_title)
    ts = time.strftime("%m-%d %H:%M UTC", time.gmtime())
    ts_w = draw.textlength(ts, font=f_sub)
    draw.text((W - ts_w - 8, 9), ts, fill=(140, 150, 170), font=f_sub)
    
    y = 34
    
    # === 左侧面板：3D头像+数据 (130px宽) ===
    left_w = 240
    left_panel_h = H - 34 - 22  # 减去标题和底部
    draw.rectangle([4, y, 4+left_w, y+left_panel_h], fill=PANEL, outline=BORDER, width=1)
    
    # 3D头像
    if show_avatar and avatar_3d:
        av_size = 120
        frame.paste(avatar_3d, (4 + (left_w - av_size - 10)//2, y + 8), avatar_3d)
        
        # 名字
        name = "姓赵不宣"
        # 中文字符用ASCII近似显示不了，用拼音
        name_en = "Zhao Bu Xuan"
        nw = draw.textlength(name_en, font=get_font(13, bold=True))
        draw.text((4 + (left_w - nw)//2, y + 135), name_en, fill=GOLD, font=get_font(13, bold=True))
        
        # 分隔线
        draw.line([12, y + 156, 4+left_w-8, y + 156], fill=BORDER, width=1)
        
        # 关键数据（逐行显示）
        dy = y + 162
        data_lines = scene_data.get('data_lines', [])
        visible = scene_data.get('visible_data', len(data_lines))
        for i, line in enumerate(data_lines[:visible]):
            color = line.get('color', WHITE)
            text = line['text']
            # 价格用大字
            if '$' in text and i < 2:
                draw.text((12, dy), text, fill=color, font=f_data)
                dy += 20
            else:
                draw.text((12, dy), text, fill=color, font=f_small)
                dy += 16
    
    # === 右侧：K线图 ===
    chart_x = left_w + 8
    chart_w = W - chart_x - 6
    chart_y = y
    chart_h = left_panel_h
    
    # 图表背景框
    draw.rectangle([chart_x, chart_y, chart_x+chart_w-1, chart_y+chart_h-1], fill=PANEL, outline=BORDER, width=1)
    
    # 缩放图表填充
    chart_resized = chart_img.resize((chart_w - 2, chart_h - 2))
    frame.paste(chart_resized, (chart_x + 1, chart_y + 1))
    
    # === 底部策略文字条 (58px) ===
    strat_y = H - 60
    draw.rectangle([4, strat_y, W-4, H-4], fill=PANEL, outline=BORDER, width=1)
    
    strat_lines = scene_data.get('strategy_lines', [])
    vis_strat = scene_data.get('visible_strat', len(strat_lines))
    sx = 10
    sy = strat_y + 5
    
    for i, line in enumerate(strat_lines[:vis_strat]):
        color = line.get('color', WHITE)
        font = f_text if line.get('size') == 'lg' else f_small
        text = line['text']
        draw.text((sx, sy), text, fill=color, font=font)
        sy += line.get('spacing', 14)
        if sy > H - 8:
            break
    
    # === 底部滚动价格条 (4px) ===
    scroll_y = H - 4
    draw.rectangle([0, scroll_y, W, H], fill=(20, 28, 48))
    
    # 滚动文字
    scroll_text = f"BTC ${scene_data.get('btc_price', 0):,.0f}  |  ETH ${scene_data.get('eth_price', 0):,.0f}  |  OI $82B  |  CPI 20:30 UTC  |  Brahma System  |  Data-Driven Not Advice  |  "
    st_w = draw.textlength(scroll_text, font=f_micro)
    # 循环滚动
    sx_scroll = W - (scroll_offset % (st_w + W))
    draw.text((sx_scroll, scroll_y + 1), scroll_text, fill=(100, 110, 130), font=f_micro)
    # 第二段（无缝循环）
    draw.text((sx_scroll - st_w, scroll_y + 1), scroll_text, fill=(100, 110, 130), font=f_micro)
    
    # === 淡入淡出遮罩 ===
    if fade_alpha > 0:
        overlay = Image.new('RGB', (W, H), BG)
        frame = Image.blend(frame, overlay, fade_alpha)
    
    return np.array(frame)


async def gen_tts_segments(script_segments, audio_paths):
    """分段TTS生成，返回每段时长"""
    durations = []
    for i, (text, path) in enumerate(zip(script_segments, audio_paths)):
        c = edge_tts.Communicate(text, TTS_VOICE)
        await c.save(path)
        dur = MP3(path).info.length
        durations.append(dur)
        print(f"  Seg {i}: {dur:.1f}s")
    return durations


def merge_audio(audio_paths, output_path):
    """合并多段音频"""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    # 用concat demuxer
    list_file = TEMP_DIR / "audio_list.txt"
    with open(list_file, 'w') as f:
        for p in audio_paths:
            f.write(f"file '{p}'\n")
    cmd = [ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', str(list_file),
           '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-b:a', '128k', output_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0


def main():
    print("=== Brahma Market Video v4 (3D Avatar + Auto-Chart) ===\n")
    
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
    
    print(f"BTC: ${btc_price:.0f} ({btc_chg:+.1f}%)")
    print(f"ETH: ${eth_price:.0f} ({eth_chg:+.1f}%)")
    
    # === 语音稿分段（每段对应一个场景+图表）===
    segments = [
        # Scene 1: 开场+BTC概览
        """早上好，这里是梵天早间战场，我是苏摩。今天是9月10号，CPI数据日特别版。
比特币当前价格{0}，24小时{1}0.{2}%。日内最高{3}，最低{4}。
波动率IV仅1.1%，创一年新低。市场在憋着等数据。""".format(
            int(btc_price), "跌" if btc_chg < 0 else "涨", abs(int(btc_chg*10)%10), int(btc_h), int(btc_l)),
        
        # Scene 2: BTC K线+关键价位
        """4小时K线放量下跌后缩量反弹，上方卖压强。
上方80000到81000是空头止损墙，做市商被迫卖出。
下方76268是多头止损最密集位，超过50亿。
大户67.9%做多但Taker买卖比0.818，大户在悄悄出货。
BTC判断：CPI前维持震荡，80000以上是空单区不是追多区。""".format(),
        
        # Scene 3: ETH分析
        """以太坊当前{0}，24小时跌0.{1}%，跌幅是BTC三倍。
Bear FVG中点2470是反弹第一阻力。2487到2510是空单入场区。
ETH未平仓量55.6亿，占山寨67%，是清算核心。
ETH判断：反弹到2487到2510做空，目标2400到2350。""".format(
            int(eth_price), abs(int(eth_chg*10)%10)),
        
        # Scene 4: 山寨+CPI策略
        """山寨未平仓量超过BTC，86亿对82亿。上次这个信号是2024年12月，之后单日清算15.8亿。山寨不碰。
CPI今晚8点半。偏鹰40%概率，跌破77700跟空到76268。符合预期45%震荡不动。低于预期15%反弹到空单区。
四条铁律：CPI前不赌方向，数据后跟15分钟结构，仓位减半，18号日本央行才是主菜。
我是苏摩，梵天系统数据驱动，不是建议。明天见。""".format(),
    ]
    
    # TTS分段生成
    ts = int(time.time())
    audio_paths = [str(TEMP_DIR / f"tts_{ts}_{i}.mp3") for i in range(len(segments))]
    print("[1/5] TTS segments...")
    durations = asyncio.run(gen_tts_segments(segments, audio_paths))
    total_duration = sum(durations)
    print(f"  Total: {total_duration:.1f}s")
    
    # 合并音频
    merged_audio = str(TEMP_DIR / f"tts_merged_{ts}.aac")
    print("[2/5] Merging audio...")
    if not merge_audio(audio_paths, merged_audio):
        print("  Audio merge failed!")
        return
    
    total_frames = int(total_duration * FPS) + 1
    
    # 渲染图表（带标注）
    print("[3/5] Rendering charts...")
    btc_annotations = [
        {'type': 'hline', 'level': 79500, 'color': '#f05050', 'linestyle': '--', 'label': 'SHORT $79,500'},
        {'type': 'hline', 'level': 80000, 'color': '#ff8800', 'linestyle': '--', 'label': 'SL $80,000'},
        {'type': 'hline', 'level': 78000, 'color': '#00d060', 'linestyle': '--', 'label': 'TP $78,000'},
        {'type': 'hline', 'level': 76268, 'color': '#00bfff', 'linestyle': ':', 'label': 'Liq $76,268'},
    ]
    eth_annotations = [
        {'type': 'hline', 'level': 2510, 'color': '#f05050', 'linestyle': '--', 'label': 'SHORT $2,510'},
        {'type': 'hline', 'level': 2546, 'color': '#ff8800', 'linestyle': '--', 'label': 'SL $2,546'},
        {'type': 'hline', 'level': 2400, 'color': '#00d060', 'linestyle': '--', 'label': 'TP $2,400'},
        {'type': 'hline', 'level': 2350, 'color': '#00d060', 'linestyle': ':', 'label': 'TP2 $2,350'},
    ]
    
    btc_chart = render_chart_annotated(btc, "BTCUSDT", 560, 300, btc_annotations)
    eth_chart = render_chart_annotated(eth, "ETHUSDT", 560, 300, eth_annotations)
    print(f"  BTC: {btc_chart.size}, ETH: {eth_chart.size}")
    
    # 3D头像
    print("[4/5] Creating 3D avatar...")
    avatar_raw = Image.open(AVATAR_PATH).convert('RGB') if AVATAR_PATH.exists() else None
    avatar_3d = create_3d_avatar(avatar_raw, 120) if avatar_raw else None
    
    # 场景时间戳计算
    scene_starts = []
    cum = 0
    for d in durations:
        scene_starts.append((cum, cum + d))
        cum += d
    
    # 场景数据定义
    btc_color = GREEN if btc_chg >= 0 else RED
    eth_color = GREEN if eth_chg >= 0 else RED
    
    scenes = [
        {  # Scene 1: BTC概览
            'chart': btc_chart,
            'title': 'BTC Analysis | CPI Day',
            'data_lines': [
                {'text': f'BTC ${btc_price:,.0f}', 'color': btc_color},
                {'text': f'24H {btc_chg:+.1f}%', 'color': btc_color},
                {'text': f'H ${btc_h:,.0f}', 'color': CYAN},
                {'text': f'L ${btc_l:,.0f}', 'color': CYAN},
                {'text': 'IV 1.1% (1yr low)', 'color': YELLOW},
            ],
            'strategy_lines': [
                {'text': 'BTC | CPI Day Special', 'color': GOLD, 'size': 'lg', 'spacing': 14},
                {'text': 'Range: 77,700 - 79,700', 'color': WHITE, 'spacing': 12},
                {'text': '> 80,000 = SHORT ZONE', 'color': RED, 'spacing': 12},
            ],
            'btc_price': btc_price, 'eth_price': eth_price,
        },
        {  # Scene 2: BTC策略
            'chart': btc_chart,
            'title': 'BTC Strategy | Key Levels',
            'data_lines': [
                {'text': 'Taker 0.818 (bearish)', 'color': RED},
                {'text': 'Whales 67.9% -> selling', 'color': YELLOW},
                {'text': 'Retail 53.6% long', 'color': GRAY},
                {'text': 'FR +0.0048% (mild)', 'color': CYAN},
                {'text': 'GEX +$113M (sell zone)', 'color': ORANGE},
            ],
            'strategy_lines': [
                {'text': 'BTC | Key Levels', 'color': GOLD, 'size': 'lg', 'spacing': 14},
                {'text': 'SHORT $79,500 -> $80,200 SL', 'color': RED, 'spacing': 12},
                {'text': 'TP: $78,000 -> $76,268', 'color': GREEN, 'spacing': 12},
                {'text': 'FVG: DOWN | OB: valid', 'color': YELLOW, 'spacing': 12},
            ],
            'btc_price': btc_price, 'eth_price': eth_price,
        },
        {  # Scene 3: ETH
            'chart': eth_chart,
            'title': 'ETH Analysis | 3x Weaker',
            'data_lines': [
                {'text': f'ETH ${eth_price:,.0f}', 'color': eth_color},
                {'text': f'24H {eth_chg:+.1f}% (3x BTC)', 'color': RED},
                {'text': f'H ${eth_h:,.0f} / L ${eth_l:,.0f}', 'color': CYAN},
                {'text': 'OI $5.56B (67% alts)', 'color': YELLOW},
                {'text': 'FVG mid: $2,470', 'color': ORANGE},
            ],
            'strategy_lines': [
                {'text': 'ETH | Short Setup', 'color': GOLD, 'size': 'lg', 'spacing': 14},
                {'text': 'SHORT $2,487 - $2,510', 'color': RED, 'spacing': 12},
                {'text': 'SL: $2,546 (stop hunt)', 'color': ORANGE, 'spacing': 12},
                {'text': 'TP: $2,400 -> $2,350', 'color': GREEN, 'spacing': 12},
            ],
            'btc_price': btc_price, 'eth_price': eth_price,
        },
        {  # Scene 4: CPI
            'chart': btc_chart,
            'title': 'CPI Strategy | 4 Iron Rules',
            'data_lines': [
                {'text': 'Altcoin OI > BTC OI', 'color': RED},
                {'text': 'Alt $8.6B vs BTC $8.2B', 'color': YELLOW},
                {'text': 'CPI 20:30 UTC TODAY', 'color': GOLD},
                {'text': 'Hawkish 40% / Neutral 45%', 'color': WHITE},
                {'text': 'BOJ Sep 18 = main event', 'color': ORANGE},
            ],
            'strategy_lines': [
                {'text': 'CPI | 4 Iron Rules', 'color': GOLD, 'size': 'lg', 'spacing': 14},
                {'text': '1. No pre-CPI bets', 'color': YELLOW, 'spacing': 12},
                {'text': '2. Follow 15m CHoCH', 'color': YELLOW, 'spacing': 12},
                {'text': '3. Half position size', 'color': YELLOW, 'spacing': 12},
                {'text': '4. BOJ Sep 18 = main', 'color': RED, 'spacing': 12},
            ],
            'btc_price': btc_price, 'eth_price': eth_price,
        },
    ]
    
    # 渲染帧
    print(f"[5/5] Rendering {total_frames} frames...")
    silent_path = str(TEMP_DIR / f"silent_{ts}.mp4")
    writer = imageio.get_writer(silent_path, fps=FPS, codec='libx264', quality=10,
        macro_block_size=1)  # 强制884宽
    
    fade_frames = int(0.3 * FPS)  # 0.3秒淡入淡出
    
    for fi in range(total_frames):
        t = fi / FPS  # 当前时间秒
        
        # 确定当前场景
        scene_idx = 0
        for si, (start, end) in enumerate(scene_starts):
            if start <= t < end:
                scene_idx = si
                break
            elif t >= scene_starts[-1][1]:
                scene_idx = len(scenes) - 1
        
        scene = scenes[scene_idx]
        
        # 场景内进度
        s_start, s_end = scene_starts[scene_idx]
        scene_progress = (t - s_start) / max(0.1, s_end - s_start)
        scene_progress = min(1.0, max(0.0, scene_progress))
        
        # 文字逐行显示
        vis_data = int(len(scene['data_lines']) * min(1.0, scene_progress * 2))
        vis_strat = int(len(scene['strategy_lines']) * min(1.0, scene_progress * 1.5))
        scene['visible_data'] = vis_data
        scene['visible_strat'] = vis_strat
        
        # 淡入淡出
        fade_alpha = 0
        if fi < fade_frames and scene_idx == 0:
            fade_alpha = 1 - fi / fade_frames
        elif t < s_start + fade_frames / FPS and scene_idx > 0:
            fade_alpha = 1 - (t - s_start) / (fade_frames / FPS)
        elif t > s_end - fade_frames / FPS and scene_idx < len(scenes) - 1:
            fade_alpha = (t - (s_end - fade_frames / FPS)) / (fade_frames / FPS)
        fade_alpha = min(1.0, max(0.0, fade_alpha))
        
        # 滚动偏移
        scroll_offset = (fi * 3) % 600
        
        frame = compose_frame_v4(
            scene['chart'], avatar_3d, scene, scene['title'],
            scroll_offset, fade_alpha
        )
        writer.append_data(frame)
        
        if fi % 600 == 0:
            print(f"  {fi}/{total_frames} ({fi/total_frames*100:.0f}%) scene={scene_idx}")
    
    writer.close()
    print(f"  All {total_frames} frames done")
    
    # 合并音频+视频
    print("Merging audio+video...", end=" ", flush=True)
    output = str(OUTPUT_DIR / f"v4_{ts}.mp4")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg, '-y', '-i', silent_path, '-i', merged_audio,
           '-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
           '-c:a', 'aac', '-b:a', '128k', '-shortest', output]
    r = subprocess.run(cmd, capture_output=True, text=True)
    
    # 清理
    for p in audio_paths:
        try: os.remove(p)
        except: pass
    try: os.remove(silent_path)
    except: pass
    try: os.remove(merged_audio)
    except: pass
    
    if r.returncode == 0:
        sz = os.path.getsize(output)
        print(f"Done!")
        print(f"\n=== v4 COMPLETE ===")
        print(f"Duration: {total_duration:.1f}s | Size: {sz/1024/1024:.1f}MB")
        print(f"Path: {output}")
        print(f"Scenes: {len(scenes)} | Frames: {total_frames}")
        print(f"Audio: 48kHz stereo AAC 128kbps")
    else:
        print(f"Error: {r.stderr[:300]}")

if __name__ == "__main__":
    main()
