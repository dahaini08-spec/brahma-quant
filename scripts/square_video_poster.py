#!/usr/bin/env python3
"""
square_video_poster.py — 视频帖生成引擎 [2026-10-03 苏摩111]
=========================================================================
视频帖推流权重 >> 图文帖 >> 纯文字帖（Binance Square算法实测）

技术路线（无需录屏/出镜）:
  1. 梵天分析数据 → Pillow生成逐帧字幕图（512×512，每帧停留3秒）
  2. gTTS生成中文语音（Google TTS，本地wav）
  3. imageio_ffmpeg合成帧序列+音频 → MP4
  4. 发布到Square videos字段

帧设计（交易员风格）:
  帧1: BTC/ETH价格 + 体制标签（3s）
  帧2: FVG磁铁 + 止损墙 + 支撑池（3s）
  帧3: OI趋势 + 聪明钱（3s）
  帧4: VIP策略（4s）
  帧5: 邀请码 + CTA（3s）

接入位置: cron 0 8 * * * (北京16:00，每日一次视频帖)
"""
import json, sys, ssl, time, io, os, tempfile, hashlib, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

CST     = timezone(timedelta(hours=8))
LATEST  = BASE / 'data' / 'auto_analysis_latest.json'
DEDUP   = BASE / 'data' / 'square_post_dedup.json'
LOG     = BASE / 'data' / 'square_post_log.jsonl'

from square_key_router import get_square_key as _get_sq_key
SQUARE_KEY = _get_sq_key('auto_post')
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
_ctx = ssl.create_default_context()

# ── 配色 ──
BG     = (13, 17, 23)
WHITE  = (230, 237, 243)
GREEN  = (63, 185, 80)
RED    = (248, 81, 73)
YELLOW = (210, 153, 34)
BLUE   = (88, 166, 255)
GRAY   = (139, 148, 158)
ORANGE = (255, 166, 87)


def _load_fonts():
    try:
        from PIL import ImageFont
        bold = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf', 22)
        med  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 16)
        sm   = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 13)
        return bold, med, sm
    except Exception:
        from PIL import ImageFont
        d = ImageFont.load_default()
        return d, d, d


def make_frame(lines: list, size=(512, 512)) -> 'Image':
    """生成单帧图片，lines=[(text, color, font_size_key)]"""
    from PIL import Image, ImageDraw
    img  = Image.new('RGB', size, BG)
    draw = ImageDraw.Draw(img)
    bold, med, sm = _load_fonts()

    font_map = {'bold': bold, 'med': med, 'sm': sm}
    y = 30
    for item in lines:
        if isinstance(item, str):
            text, color, fkey = item, WHITE, 'med'
        elif len(item) == 2:
            text, color = item; fkey = 'med'
        else:
            text, color, fkey = item

        font = font_map.get(fkey, med)
        draw.text((24, y), text, fill=color, font=font)
        # 估算行高
        try:
            bbox = font.getbbox(text) if hasattr(font, 'getbbox') else (0, 0, 200, 18)
            y += (bbox[3] - bbox[1]) + 8
        except Exception:
            y += 28

    # 底部水印
    draw.text((24, size[1] - 24), 'ZhaobuXuan | Not Advice', fill=tuple(GRAY), font=sm)
    return img


def build_frames(btc: dict, eth: dict) -> list:
    """构建视频帧序列，返回[(Image, duration_s, tts_text)]"""
    frames = []
    date_s = datetime.now(CST).strftime('%m/%d %H:%M')
    reg_map = {'BEAR_TREND': 'BEAR', 'BULL_TREND': 'BULL',
               'CHOP_MID': 'CHOP', 'BEAR_RECOVERY': 'RECOV'}

    btc_reg = reg_map.get(btc.get('regime', ''), 'CHOP')
    eth_reg = reg_map.get(eth.get('regime', ''), 'CHOP')
    btc_col = GREEN if btc.get('bias') == 'LONG' else RED if btc.get('bias') == 'SHORT' else YELLOW
    eth_col = GREEN if eth.get('bias') == 'LONG' else RED if eth.get('bias') == 'SHORT' else YELLOW

    # 帧1: 双币价格概览
    frames.append((make_frame([
        (f'BTC/ETH  {date_s}', GRAY, 'sm'),
        ('', WHITE, 'sm'),
        (f'BTC  ${btc.get("price", 0):>10,.0f}', btc_col, 'bold'),
        (f'Mode: {btc_reg:<8}  Bias: {btc.get("bias","?"):<6}', GRAY, 'med'),
        ('', WHITE, 'sm'),
        (f'ETH  ${eth.get("price", 0):>10,.2f}', eth_col, 'bold'),
        (f'Mode: {eth_reg:<8}  Bias: {eth.get("bias","?"):<6}', GRAY, 'med'),
    ]), 3,
    f'比特币当前{btc.get("price",0):,.0f}美元，以太坊{eth.get("price",0):,.0f}美元。'))

    # 帧2: 关键价位
    frames.append((make_frame([
        ('KEY LEVELS', BLUE, 'bold'),
        ('', WHITE, 'sm'),
        ('BTC', GRAY, 'med'),
        (f'  WALL  ${btc.get("wall", 0):>10,.0f}', RED,   'med'),
        (f'  POOL  ${btc.get("pool", 0):>10,.0f}', GREEN, 'med'),
        (f'  FVG   ${btc.get("fvg_mid", 0):>10,.0f}', ORANGE, 'med'),
        ('ETH', GRAY, 'med'),
        (f'  WALL  ${eth.get("wall", 0):>10,.2f}', RED,   'med'),
        (f'  POOL  ${eth.get("pool", 0):>10,.2f}', GREEN, 'med'),
    ]), 4,
    f'比特币上方空头止损墙{btc.get("wall",0):,.0f}，下方支撑池{btc.get("pool",0):,.0f}。以太坊止损墙{eth.get("wall",0):,.0f}。'))

    # 帧3: 策略判断
    btc_action = 'WAIT FOR BREAKOUT' if btc.get('bias') not in ('LONG','SHORT') else \
                 f'SHORT near ${btc.get("wall",0):,.0f}' if btc.get('bias') == 'SHORT' else \
                 f'LONG near ${btc.get("pool",0):,.0f}'
    eth_action = 'WAIT FOR BREAKOUT' if eth.get('bias') not in ('LONG','SHORT') else \
                 f'SHORT near ${eth.get("wall",0):,.2f}' if eth.get('bias') == 'SHORT' else \
                 f'LONG near ${eth.get("pool",0):,.2f}'

    frames.append((make_frame([
        ('STRATEGY', BLUE, 'bold'),
        ('', WHITE, 'sm'),
        ('BTC ACTION:', GRAY, 'med'),
        (f'  {btc_action}', btc_col, 'med'),
        ('', WHITE, 'sm'),
        ('ETH ACTION:', GRAY, 'med'),
        (f'  {eth_action}', eth_col, 'med'),
        ('', WHITE, 'sm'),
        ('Wait for structure confirm', GRAY, 'sm'),
        ('SL at structure failure zone', GRAY, 'sm'),
    ]), 4,
    '等待结构确认后入场，止损放在结构失效位。'))

    # 帧4: 邀请码CTA
    frames.append((make_frame([
        ('FOLLOW FOR SIGNALS', BLUE, 'bold'),
        ('', WHITE, 'sm'),
        ('Daily 21:00 Live Stream', WHITE, 'med'),
        ('SMC Education Included', WHITE, 'med'),
        ('', WHITE, 'sm'),
        ('Register Binance:', GRAY, 'med'),
        ('bsmkweb.cc/register', ORANGE, 'bold'),
        ('?ref=XZBX666', ORANGE, 'bold'),
        ('', WHITE, 'sm'),
        ('Not Investment Advice | 仅供参考', GRAY, 'sm'),
    ]), 4,
    '关注我，每晚21:00直播，SMC教学。注册币安领交易奖励。'))

    return frames


def generate_tts(text: str, lang='zh') -> str | None:
    """生成语音文件，返回路径"""
    try:
        from gtts import gTTS
        tts = gTTS(text=text, lang=lang, slow=False)
        tmp = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
        tts.save(tmp.name)
        return tmp.name
    except Exception as e:
        print(f'[video] TTS失败: {e}')
        return None


def frames_to_video(frames: list, output_path: str,
                    with_audio=True) -> bool:
    """
    frames: [(Image, duration_s, tts_text), ...]
    合成MP4
    """
    try:
        import imageio_ffmpeg
        import subprocess
        import numpy as np

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        tmpdir = tempfile.mkdtemp()

        # 1. 生成帧图片
        frame_files = []
        for i, (img, dur, _) in enumerate(frames):
            # 每帧重复 dur*fps 张（fps=1，简单方案）
            fp = os.path.join(tmpdir, f'frame_{i:03d}.png')
            img.save(fp)
            frame_files.append((fp, dur))

        # 2. 生成帧列表文件（ffmpeg concat格式）
        concat_file = os.path.join(tmpdir, 'frames.txt')
        with open(concat_file, 'w') as f:
            for fp, dur in frame_files:
                f.write(f"file '{fp}'\n")
                f.write(f"duration {dur}\n")
            # 最后一帧重复（ffmpeg要求）
            if frame_files:
                f.write(f"file '{frame_files[-1][0]}'\n")

        # 3. 生成TTS音频（合并所有文本）
        audio_file = None
        if with_audio:
            all_text = '。'.join(t for _, _, t in frames if t)
            audio_file = generate_tts(all_text)

        # 4. ffmpeg合成
        if audio_file and os.path.exists(audio_file):
            cmd = [ffmpeg_exe, '-y',
                   '-f', 'concat', '-safe', '0', '-i', concat_file,
                   '-i', audio_file,
                   '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                   '-c:a', 'aac', '-shortest',
                   '-movflags', '+faststart',
                   output_path]
        else:
            cmd = [ffmpeg_exe, '-y',
                   '-f', 'concat', '-safe', '0', '-i', concat_file,
                   '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                   '-movflags', '+faststart',
                   output_path]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f'[video] ffmpeg错误: {result.stderr[-500:]}')
            return False

        print(f'[video] ✅ 视频生成: {output_path} ({os.path.getsize(output_path)//1024}KB)')
        return True

    except Exception as e:
        print(f'[video] 视频合成失败: {e}')
        return False
    finally:
        # 清理临时文件
        import shutil
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass


def post_video_to_square(video_path: str, caption: str, dry_run=False) -> bool:
    """发布视频帖到Square（video字段=base64）"""
    if dry_run:
        sz = os.path.getsize(video_path) // 1024
        print(f'[video DRY-RUN] 视频{sz}KB, caption={caption[:80]}')
        return True

    import base64
    with open(video_path, 'rb') as f:
        vid_b64 = 'data:video/mp4;base64,' + base64.b64encode(f.read()).decode()

    h = hashlib.md5(caption[:200].encode()).hexdigest()[:12]
    payload = json.dumps({
        'bodyTextOnly': caption,
        'video': vid_b64,
    }).encode()
    req = urllib.request.Request(SQUARE_URL, data=payload,
        headers={'X-Square-OpenAPI-Key': SQUARE_KEY,
                 'Content-Type': 'application/json',
                 'clienttype': 'binanceSkill'})
    # [Fix 2026-10-04 苏摩111] 3次重试
    resp = None
    for _retry in range(3):
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=30, context=_ctx).read())
            if resp.get('code') == '000000' or resp.get('success'):
                break
            if _retry < 2:
                print(f'[video] 第{_retry+1}次失败，5s后重试: {resp.get("message","")}')
                time.sleep(5)
        except Exception as _re:
            if _retry < 2:
                print(f'[video] 第{_retry+1}次异常，5s后重试: {_re}')
                time.sleep(5)
            else:
                print(f'[video] ❌ 3次均失败: {_re}')
                return False
    if not resp: return False
    if resp.get('code') == '000000' or resp.get('success'):
        post_id = resp.get('data', {}).get('id', '')
        print(f'[video] ✅ 视频帖发布成功 id={post_id}')
        try:
            d = json.loads(DEDUP.read_text()) if DEDUP.exists() else {}
            d[h] = time.time()
            DEDUP.write_text(json.dumps(d))
        except Exception:
            pass
        with open(LOG, 'a') as f:
            f.write(json.dumps({
                'ts': time.time(), 'post_type': 'video_post',
                'id': post_id, 'chars': len(caption)
            }, ensure_ascii=False) + '\n')
        return True
    else:
        print(f'[video] ❌ 发布失败: {resp}')
        return False


def run(dry_run=False):
    # 读数据
    from square_chart_poster import load_analysis_data
    btc = load_analysis_data('BTC')
    eth = load_analysis_data('ETH')

    if not btc.get('price') or not eth.get('price'):
        print('[video] 数据不足，退出')
        return False

    # 生成帧
    frames = build_frames(btc, eth)

    # 生成视频
    tmp_video = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False).name
    ok = frames_to_video(frames, tmp_video, with_audio=True)
    if not ok:
        print('[video] 视频生成失败，降级到图文帖')
        return False

    # 字幕文字（放bodyTextOnly）
    date_s = datetime.now(CST).strftime('%m/%d')
    caption = (
        f'BTC+ETH 日播 | {date_s}\n\n'
        f'BTC ${btc["price"]:,.0f}  止损墙${btc.get("wall",0):,.0f}  支撑池${btc.get("pool",0):,.0f}\n'
        f'ETH ${eth["price"]:,.2f}  止损墙${eth.get("wall",0):,.2f}  支撑池${eth.get("pool",0):,.2f}\n\n'
        f'关注我，每晚21:00直播+SMC教学\n'
        f'🔗 www.bsmkweb.cc/register?ref=XZBX666\n'
        f'🌿 姓赵不宣 | 不是建议\n'
        f'#BTC #ETH #合约交易 #视频'
    )

    result = post_video_to_square(tmp_video, caption, dry_run=dry_run)
    try:
        os.unlink(tmp_video)
    except Exception:
        pass
    return result


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--preview', action='store_true', help='只生成视频文件不发布')
    args = ap.parse_args()

    if args.preview:
        from square_chart_poster import load_analysis_data
        btc = load_analysis_data('BTC')
        eth = load_analysis_data('ETH')
        frames = build_frames(btc, eth)
        out = str(BASE / 'data' / 'chart_preview' / 'preview_video.mp4')
        Path(out).parent.mkdir(exist_ok=True)
        frames_to_video(frames, out, with_audio=True)
        print(f'预览视频: {out}')
    else:
        run(dry_run=args.dry_run)
