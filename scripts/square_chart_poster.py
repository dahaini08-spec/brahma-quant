#!/usr/bin/env python3
"""
square_chart_poster.py — 图文帖引擎 [2026-10-03 苏摩111]
================================================================
核心发现：Square images字段支持 data:image/png;base64,... 格式
无需外部图床，Pillow生成→base64编码→直接发布。

帖型：图文战场报告（替代bodyTextOnly快照帖）
图表内容：K线位置 + FVG区间 + 止损墙/支撑池 + 体制标签
算法推流权重：图文帖 ≈ 纯文字帖的3~5倍

接入位置：
  1. square_auto_post.py run()（快照帖cron 01:30/09:30 UTC）
     直接调用 build_and_post_chart() 替换纯文字帖
  2. cron单独调用（每6小时图文深度帖）

字体说明：Pillow默认字体无CJK，图内全用ASCII/数字
中文内容放bodyTextOnly，图片做纯数据可视化
"""
import json
import io
import base64
import ssl
import sys
import time
import os
import urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except ImportError:
    PIL_OK = False

CST = timezone(timedelta(hours=8))
DEDUP_FILE = BASE / 'data' / 'square_post_dedup.json'
LOG_FILE   = BASE / 'data' / 'square_post_log.jsonl'
LATEST     = BASE / 'data' / 'auto_analysis_latest.json'

from square_key_router import get_square_key as _get_sq_key
SQUARE_KEY = _get_sq_key('auto_post')
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
_ctx = ssl.create_default_context()

# ── 配色方案（交易员风格，暗色背景）────────────────────────────────
C = {
    'bg':       '#0d1117',   # 深黑背景
    'bg2':      '#161b22',   # 卡片背景
    'border':   '#30363d',   # 边框
    'text':     '#e6edf3',   # 主文字
    'muted':    '#8b949e',   # 次要文字
    'green':    '#3fb950',   # 多/涨
    'red':      '#f85149',   # 空/跌
    'yellow':   '#d29922',   # 警告/中性
    'blue':     '#58a6ff',   # 高亮
    'purple':   '#bc8cff',   # FVG
    'orange':   '#ffa657',   # 止损墙
}


def _hex(c: str):
    """hex color → (R,G,B) tuple"""
    c = c.lstrip('#')
    return tuple(int(c[i:i+2], 16) for i in (0, 2, 4))


def draw_chart(sym: str, price: float, wall: float, pool: float,
               fvg_mid: float, regime: str, bias: str,
               size: int = 512) -> Image.Image:
    """
    生成512×512交易图表：
    - 价格区间竖轴（pool底部 ~ wall顶部）
    - 当前价格横线（高亮）
    - FVG区间色块
    - 止损墙/支撑池标注
    - 左侧价格刻度，右侧信息栏
    图内全ASCII，CJK放bodyTextOnly
    """
    W = H = size
    img = Image.new('RGB', (W, H), _hex(C['bg']))
    draw = ImageDraw.Draw(img)

    # 尝试加载等宽字体，失败用默认
    try:
        fnt_lg = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf', 18)
        fnt_md = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 14)
        fnt_sm = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 11)
    except Exception:
        fnt_lg = fnt_md = fnt_sm = ImageFont.load_default()

    # ── 布局参数 ──
    pad_l, pad_r = 80, 160   # 左右留白（价格轴 / 信息栏）
    pad_t, pad_b = 50, 50    # 上下留白
    chart_l = pad_l
    chart_r = W - pad_r
    chart_t = pad_t
    chart_b = H - pad_b
    chart_h = chart_b - chart_t
    chart_w = chart_r - chart_l

    # ── 价格范围（稍超出 wall/pool 10%）──
    margin = (wall - pool) * 0.12 if wall > pool else price * 0.03
    y_max = wall + margin
    y_min = pool - margin
    y_range = y_max - y_min if y_max > y_min else price * 0.1

    def price_to_y(p: float) -> int:
        """价格 → 像素Y（顶=高价）"""
        return int(chart_t + (y_max - p) / y_range * chart_h)

    # ── 背景网格 ──
    levels = 5
    for i in range(levels + 1):
        y = chart_t + i * chart_h // levels
        draw.line([(chart_l, y), (chart_r, y)], fill=_hex(C['border']), width=1)
    draw.rectangle([chart_l, chart_t, chart_r, chart_b], outline=_hex(C['border']), width=1)

    # ── FVG区间（价格±0.5%的色块）──
    if fvg_mid > 0:
        fvg_lo = fvg_mid * 0.995
        fvg_hi = fvg_mid * 1.005
        y_fvg_t = price_to_y(fvg_hi)
        y_fvg_b = price_to_y(fvg_lo)
        if chart_t <= y_fvg_t <= chart_b and chart_t <= y_fvg_b <= chart_b:
            fvg_color = _hex(C['green']) + (40,)  # 透明度
            overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            od.rectangle([chart_l + 2, y_fvg_t, chart_r - 2, y_fvg_b],
                         fill=(*_hex(C['purple']), 35))
            img = img.convert('RGBA')
            img = Image.alpha_composite(img, overlay)
            img = img.convert('RGB')
            draw = ImageDraw.Draw(img)

    # ── 止损墙横线 ──
    y_wall = price_to_y(wall)
    if chart_t <= y_wall <= chart_b:
        draw.line([(chart_l, y_wall), (chart_r, y_wall)],
                  fill=_hex(C['red']), width=2)
        draw.text((chart_r + 4, y_wall - 8), f'${wall:,.0f}',
                  font=fnt_sm, fill=_hex(C['red']))

    # ── 支撑池横线 ──
    y_pool = price_to_y(pool)
    if chart_t <= y_pool <= chart_b:
        draw.line([(chart_l, y_pool), (chart_r, y_pool)],
                  fill=_hex(C['green']), width=2)
        draw.text((chart_r + 4, y_pool - 8), f'${pool:,.0f}',
                  font=fnt_sm, fill=_hex(C['green']))

    # ── FVG磁铁横线 ──
    if fvg_mid > 0:
        y_fvg = price_to_y(fvg_mid)
        if chart_t <= y_fvg <= chart_b:
            draw.line([(chart_l, y_fvg), (chart_r, y_fvg)],
                      fill=_hex(C['purple']), width=1)
            draw.text((chart_r + 4, y_fvg - 8), f'FVG',
                      font=fnt_sm, fill=_hex(C['purple']))

    # ── 当前价格（粗线）──
    y_price = price_to_y(price)
    y_price = max(chart_t + 2, min(chart_b - 2, y_price))
    price_color = C['green'] if bias == 'LONG' else C['red'] if bias == 'SHORT' else C['yellow']
    draw.line([(chart_l, y_price), (chart_r, y_price)],
              fill=_hex(price_color), width=3)
    draw.text((chart_r + 4, y_price - 8), f'${price:,.0f}',
              font=fnt_sm, fill=_hex(price_color))

    # ── 左侧价格刻度 ──
    for i in range(6):
        p = y_min + (y_max - y_min) * i / 5
        y = price_to_y(p)
        if chart_t <= y <= chart_b:
            draw.line([(chart_l - 4, y), (chart_l, y)], fill=_hex(C['muted']), width=1)
            label = f'{p:,.0f}' if p >= 1000 else f'{p:.2f}'
            draw.text((2, y - 7), label, font=fnt_sm, fill=_hex(C['muted']))

    # ── 顶部标题 ──
    regime_map = {
        'BEAR_TREND': 'BEAR', 'BULL_TREND': 'BULL',
        'CHOP_MID': 'CHOP', 'CHOP_HIGH': 'CHOP+',
        'BEAR_RECOVERY': 'RECOV', 'BEAR_EARLY': 'BREV',
    }
    regime_short = regime_map.get(regime, regime[:6] if regime else '?')
    regime_color = C['red'] if 'BEAR' in regime else C['green'] if 'BULL' in regime else C['yellow']

    title = f'{sym}/USDT  {regime_short}'
    draw.text((chart_l, 12), title, font=fnt_lg, fill=_hex(C['text']))

    # 日期
    date_str = datetime.now(CST).strftime('%m/%d %H:%M')
    draw.text((chart_r - 80, 12), date_str, font=fnt_sm, fill=_hex(C['muted']))

    # ── 右侧信息栏 ──
    info_x = W - pad_r + 8
    info_items = [
        ('PRICE', f'${price:,.0f}', price_color),
        ('WALL',  f'${wall:,.0f}',  C['red']),
        ('POOL',  f'${pool:,.0f}',  C['green']),
        ('FVG',   f'${fvg_mid:,.0f}' if fvg_mid > 0 else 'N/A', C['purple']),
        ('MODE',  regime_short, regime_color),
        ('BIAS',  bias[:5] if bias else '-',
                  C['green'] if bias == 'LONG' else C['red'] if bias == 'SHORT' else C['yellow']),
    ]
    for idx, (label, val, color) in enumerate(info_items):
        y_item = chart_t + idx * 38
        draw.text((info_x, y_item), label, font=fnt_sm, fill=_hex(C['muted']))
        draw.text((info_x, y_item + 14), val, font=fnt_sm, fill=_hex(color))

    # ── 底部水印 ──
    watermark = 'ZhaobuXuan | Not Advice'
    draw.text((chart_l, H - 28), watermark, font=fnt_sm, fill=_hex(C['border']))

    return img


def chart_to_base64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f'data:image/png;base64,{b64}'


def load_analysis_data(sym: str) -> dict:
    """从auto_analysis_latest读取图表所需字段"""
    import re
    if not LATEST.exists():
        return {}
    try:
        d = json.loads(LATEST.read_text())
        output = d.get('output', '')
        # 找sym专属段（从'梵天XX维全能力分析 | SYM'开始到下一个梵天标题）
        seg_m = re.search(rf'梵天\d+维.+?\| {re.escape(sym)}', output)
        if seg_m:
            start = seg_m.start()
            # 找下一个梵天标题或结尾
            next_m = re.search(r'梵天\d+维', output[start+10:])
            end = start + 10 + next_m.start() if next_m else start + 8000
            seg = output[start:end]
        else:
            seg = output

        def _find(patterns, text, default=0.0):
            for pat in patterns:
                m = re.search(pat, text)
                if m:
                    try:
                        return float(m.group(1).replace(',', ''))
                    except Exception:
                        pass
            return default

        price = _find([r'基准\$([0-9,]+)', r'实时\$([0-9,]+)'], seg)
        wall  = _find([r'上方空头止损墙: \$([0-9,]+)',
                       r'上方空头止损墙:\$([0-9,]+)',
                       r'空头止损墙[:：]\s*\$([0-9,]+)'], seg)
        pool  = _find([r'下方多头支撑池: \$([0-9,]+)',
                       r'多头支撑池:\$([0-9,]+)',
                       r'支撑池[:：]\s*\$([0-9,]+)'], seg)
        fvg   = _find([r'主磁铁[：:]\s*(?:BULL|BEAR)@\$([0-9,]+)',
                       r'磁铁[：:][^\$]*\$([0-9,]+)',
                       r'FVG.*?中点\$([0-9,]+)'], seg)
        regime_m = re.search(r'CHOP_MID|BULL_TREND|BEAR_TREND|CHOP_HIGH|BEAR_RECOVERY|BEAR_EARLY', seg)
        bias_m   = re.search(r'交易员大脑[=：:]\s*(SHORT|LONG|WATCH|WAIT)', seg)
        if not bias_m:
            bias_m = re.search(r'方向[：:]\s*(SHORT|LONG|WATCH|WAIT)', seg)
        return {
            'price': price, 'wall': wall, 'pool': pool, 'fvg_mid': fvg,
            'regime': regime_m.group(0) if regime_m else 'CHOP_MID',
            'bias': bias_m.group(1) if bias_m else 'WATCH',
        }
    except Exception as e:
        print(f'[chart] 解析{sym}数据失败: {e}', file=sys.stderr)
        return {}


def is_duplicate(content: str) -> bool:
    import hashlib
    h = hashlib.md5(content[:200].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP_FILE.read_text()) if DEDUP_FILE.exists() else {}
        cutoff = time.time() - 86400
        if h in d and float(d[h]) > cutoff:
            return True
    except Exception:
        pass
    return False


def mark_posted(content: str):
    import hashlib
    h = hashlib.md5(content[:200].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP_FILE.read_text()) if DEDUP_FILE.exists() else {}
        d[h] = time.time()
        DEDUP_FILE.write_text(json.dumps(d))
    except Exception:
        pass


def build_and_post_chart(syms=('BTC', 'ETH'), dry_run=False) -> bool:
    """
    主入口：生成双币图表+图文帖，发布到Square。
    返回True=发布成功。
    接入位置: square_auto_post.py run() 替换纯文字快照帖。
    """
    if not PIL_OK:
        print('[chart] PIL不可用，跳过图文帖', file=sys.stderr)
        return False

    from square.square_template import build_battlefield_report_combined, audit_post

    # 收集数据
    analysis_by_sym = {}
    images_b64 = []

    for sym in syms:
        data = load_analysis_data(sym)
        if not data.get('price'):
            print(f'[chart] {sym}数据不足，跳过图表')
            continue

        # 生成图表
        try:
            img = draw_chart(
                sym=sym,
                price=data['price'],
                wall=data.get('wall', data['price'] * 1.02),
                pool=data.get('pool', data['price'] * 0.98),
                fvg_mid=data.get('fvg_mid', 0),
                regime=data.get('regime', 'CHOP_MID'),
                bias=data.get('bias', 'WATCH'),
            )
            images_b64.append(chart_to_base64(img))
            print(f'[chart] {sym} 图表生成OK ({data["price"]:,.0f})')
        except Exception as e:
            print(f'[chart] {sym} 图表生成失败: {e}')

        # 为文字内容准备数据
        analysis_by_sym[sym] = {
            'price': data.get('price', 0),
            'bias': data.get('bias', 'NONE'),
            'liq_wall': data.get('wall', 0),
            'liq_pool': data.get('pool', 0),
            'vip_status': 'WAIT',
            'failure_state': 'GREEN',
            'regime': data.get('regime', 'CHOP_MID'),
            'score': 0,
        }

    if not images_b64:
        print('[chart] 无有效图表，退出')
        return False

    # 生成文字内容
    text_content = build_battlefield_report_combined(analysis_by_sym)
    ok, issues = audit_post(text_content)
    if not ok:
        print(f'[chart] 文字审计失败: {issues}')
        return False

    if is_duplicate(text_content):
        print('[chart] 24h内重复，跳过')
        return False

    if dry_run:
        print('[chart DRY-RUN]')
        print(f'  图表数量: {len(images_b64)}')
        print(f'  文字长度: {len(text_content)}字')
        print(f'  文字预览: {text_content[:120]}...')
        print('[chart DRY-RUN] ✅')
        return True

    # 发布图文帖
    payload = json.dumps({
        'bodyTextOnly': text_content,
        'images': images_b64[:4],  # Square最多4张
    }).encode()
    req = urllib.request.Request(
        SQUARE_URL, data=payload,
        headers={
            'X-Square-OpenAPI-Key': SQUARE_KEY,
            'Content-Type': 'application/json',
            'clienttype': 'binanceSkill',
        })
    # [Fix 2026-10-04 苏摩111] 3次重试，应对Network error 10004瞬时抖动
    resp = None
    for _retry in range(3):
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=20, context=_ctx).read())
            if resp.get('code') == '000000' or resp.get('success'):
                break
            if _retry < 2:
                print(f'[chart] 第{_retry+1}次失败，3s后重试: {resp.get("message","")}')
                time.sleep(3)
        except Exception as _re:
            if _retry < 2:
                print(f'[chart] 第{_retry+1}次异常，3s后重试: {_re}')
                time.sleep(3)
            else:
                print(f'[chart] ❌ 3次均失败: {_re}')
                return False
    if not resp: return False
    if resp.get('code') == '000000' or resp.get('success'):
        post_id = resp.get('data', {}).get('id', '')
        mark_posted(text_content)
        print(f'[chart] ✅ 图文帖发布成功 id={post_id}')
        entry = {
            'ts': time.time(), 'post_type': 'chart_post',
            'syms': list(syms), 'images': len(images_b64),
            'chars': len(text_content), 'id': post_id
        }
        with open(LOG_FILE, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        return True
    else:
        print(f'[chart] ❌ 发布失败: {resp}')
        return False


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='图文战场报告发布')
    ap.add_argument('--syms', nargs='+', default=['BTC', 'ETH'])
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--preview', action='store_true', help='生成图片预览到本地')
    args = ap.parse_args()

    if args.preview:
        out_dir = BASE / 'data' / 'chart_preview'
        out_dir.mkdir(exist_ok=True)
        for sym in args.syms:
            data = load_analysis_data(sym)
            if data.get('price'):
                img = draw_chart(sym, data['price'],
                                 data.get('wall', data['price']*1.02),
                                 data.get('pool', data['price']*0.98),
                                 data.get('fvg_mid', 0),
                                 data.get('regime', 'CHOP_MID'),
                                 data.get('bias', 'WATCH'))
                path = out_dir / f'{sym}_chart.png'
                img.save(str(path))
                print(f'预览已保存: {path}')
        sys.exit(0)

    success = build_and_post_chart(syms=args.syms, dry_run=args.dry_run)
    sys.exit(0 if success else 1)
