#!/usr/bin/env python3
"""
liq_heatmap_viz.py — 清算热力图 PNG 可视化
设计院 2026-08-05

功能:
  - 读取 liq_density_engine 四所数据
  - 生成 Kingfisher 风格横向柱状清算图
  - 输出 PNG 文件路径 (供 Jarvis MEDIA: 推送)

使用:
  python3 scripts/liq_heatmap_viz.py BTCUSDT
  python3 scripts/liq_heatmap_viz.py ETHUSDT --push

输出: ./openclaw-media/liq_heatmap_<SYM>_<ts>.png
"""
import sys
import os
import time
import json
import math
import secrets
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))


def _color_hex(r, g, b):
    return (r, g, b)


# Exchange color map  (R, G, B) — Kingfisher style
EX_COLORS = {
    'binance':          (139, 105, 255),   # purple
    'binance_proxy':    (139, 105, 255),
    'bybit':            (255, 213,  59),   # yellow
    'bybit_proxy':      (255, 213,  59),
    'okx':              (255,  59, 120),   # pink/red
    'hyperliquid':      (255, 160,  50),   # orange
    'hyperliquid_proxy':(255, 160,  50),
    'other':            (120, 120, 120),
}

WIDTH      = 900
HEIGHT     = 480
MARGIN_L   = 60
MARGIN_R   = 70
MARGIN_T   = 60
MARGIN_B   = 60
CHART_W    = WIDTH  - MARGIN_L - MARGIN_R
CHART_H    = HEIGHT - MARGIN_T - MARGIN_B
BG_COLOR   = (15, 17, 23)
GRID_COLOR = (35, 40, 50)
TEXT_COLOR = (200, 200, 210)
PRICE_COLOR= (80, 220, 120)
CUM_COLOR  = (220, 60, 80)


def _make_heatmap(symbol: str, push: bool = False) -> str:
    """生成清算热力图 PNG，返回文件路径"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print('Pillow not installed: pip install Pillow')
        return ''

    from liq_density_engine import get_liq_density, _get_hyperliquid_liquidations

    # ── 1. 数据收集 ────────────────────────────────────────────────────────
    import requests as _req
    price_r = _req.get(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}', timeout=5)
    current_price = float(price_r.json()['price'])

    density = get_liq_density(symbol, current_price)
    all_walls = density.get('above_walls', []) + density.get('below_walls', [])

    if not all_walls:
        print(f'No liquidation data for {symbol}')
        return ''

    # ── 2. 价格范围计算 (±8%) ──────────────────────────────────────────────
    price_lo = current_price * 0.92
    price_hi = current_price * 1.08
    price_range = price_hi - price_lo

    # Build per-bucket data by exchange
    # Use raw orders from each exchange
    from liq_density_engine import (
        _get_binance_force_orders, _get_bybit_liquidations,
        _get_okx_liquidations
    )
    sym_base = symbol.replace('USDT', '').replace('BUSD', '')

    all_orders = (
        _get_binance_force_orders(symbol)
        + _get_bybit_liquidations(symbol)
        + _get_okx_liquidations(sym_base)
        + _get_hyperliquid_liquidations(sym_base)
    )

    # Filter to price range
    all_orders = [o for o in all_orders
                  if price_lo <= o['price'] <= price_hi]

    # Bucket size: 0.25% of current price
    bucket_size = current_price * 0.0025
    n_buckets   = int(math.ceil(price_range / bucket_size)) + 1

    # Build buckets: {bucket_price: {exchange: usd_volume}}
    from collections import defaultdict
    buckets = defaultdict(lambda: defaultdict(float))
    for o in all_orders:
        bkt = round((o['price'] - price_lo) / bucket_size)
        bkt = max(0, min(bkt, n_buckets - 1))
        src = o.get('source', 'other').split('_')[0]  # binance_proxy → binance
        buckets[bkt][src] += o.get('usd', 0)

    if not buckets:
        # No orders in range — use above/below walls from density
        print(f'No orders in ±8% range, using wall data...')
        for p, v in all_walls:
            if price_lo <= p <= price_hi:
                bkt = round((p - price_lo) / bucket_size)
                bkt = max(0, min(bkt, n_buckets - 1))
                buckets[bkt]['okx'] += v

    # Max bucket height
    max_vol = max((sum(ex.values()) for ex in buckets.values()), default=1)
    if max_vol == 0:
        max_vol = 1

    # ── 3. 绘图 ────────────────────────────────────────────────────────────
    img  = Image.new('RGB', (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Try to load a font; fall back to default
    try:
        fnt_sm  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 11)
        fnt_med = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 13)
        fnt_lg  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 16)
    except Exception:
        fnt_sm = fnt_med = fnt_lg = ImageFont.load_default()

    # Title
    sources_str = density.get('sources', '')
    draw.text((MARGIN_L, 10), f'LiqMap  {symbol}  @${current_price:,.0f}',
              fill=TEXT_COLOR, font=fnt_lg)
    draw.text((MARGIN_L, 30), f'Sources: {sources_str}',
              fill=(130, 140, 160), font=fnt_sm)

    # Grid lines (vertical price labels)
    n_labels = 8
    for i in range(n_labels + 1):
        x = MARGIN_L + int(CHART_W * i / n_labels)
        p = price_lo + price_range * i / n_labels
        draw.line([(x, MARGIN_T), (x, MARGIN_T + CHART_H)], fill=GRID_COLOR, width=1)
        label = f'${p/1000:.2f}K' if p >= 1000 else f'${p:.0f}'
        draw.text((x - 18, MARGIN_T + CHART_H + 5), label,
                  fill=TEXT_COLOR, font=fnt_sm)

    # Bars (price → x, volume → height)
    bar_w = max(2, int(CHART_W / n_buckets) - 1)
    # Cumulative line data
    cum_points = []
    cum_total  = 0.0
    total_all  = sum(sum(ex.values()) for ex in buckets.values()) or 1

    exchanges_order = ['binance', 'bybit', 'okx', 'hyperliquid']

    for bkt_idx in range(n_buckets):
        bkt_data = buckets.get(bkt_idx, {})
        total_vol = sum(bkt_data.values())
        if total_vol == 0:
            x_center = MARGIN_L + int(CHART_W * bkt_idx / n_buckets)
            cum_points.append((x_center, MARGIN_T + CHART_H))
            continue

        bar_h   = int(CHART_H * 0.85 * total_vol / max_vol)
        x_left  = MARGIN_L + int(CHART_W * bkt_idx / n_buckets)
        y_base  = MARGIN_T + CHART_H

        # Stacked bars by exchange
        y_cur = y_base
        for ex in exchanges_order:
            vol = bkt_data.get(ex, 0)
            if vol <= 0:
                continue
            seg_h = max(1, int(bar_h * vol / total_vol))
            color = EX_COLORS.get(ex, EX_COLORS['other'])
            draw.rectangle(
                [(x_left, y_cur - seg_h), (x_left + bar_w, y_cur)],
                fill=color
            )
            y_cur -= seg_h

        cum_total += total_vol
        cum_pct = cum_total / total_all
        cum_y   = MARGIN_T + CHART_H - int(CHART_H * 0.7 * cum_pct)
        cum_points.append((x_left + bar_w // 2, cum_y))

    # Cumulative line
    if len(cum_points) >= 2:
        draw.line(cum_points, fill=CUM_COLOR, width=2)

    # Current price vertical line (dashed)
    px_x = MARGIN_L + int(CHART_W * (current_price - price_lo) / price_range)
    for y in range(MARGIN_T, MARGIN_T + CHART_H, 6):
        draw.line([(px_x, y), (px_x, y + 3)], fill=PRICE_COLOR, width=2)
    draw.text((px_x - 20, MARGIN_T - 18),
              f'${current_price:,.0f}',
              fill=PRICE_COLOR, font=fnt_med)

    # Legend
    lx = MARGIN_L
    for ex in exchanges_order:
        color = EX_COLORS[ex]
        draw.rectangle([(lx, HEIGHT - 20), (lx + 10, HEIGHT - 10)], fill=color)
        draw.text((lx + 13, HEIGHT - 22), ex[:8], fill=TEXT_COLOR, font=fnt_sm)
        lx += 80

    # Bias label
    bias = density.get('liq_bias', 'NEUTRAL')
    bias_color = (220, 80, 80) if 'BELOW' in bias else (80, 200, 120)
    draw.text((WIDTH - MARGIN_R - 120, MARGIN_T + 5),
              f'Bias: {bias}', fill=bias_color, font=fnt_med)

    # ── 4. 保存 ────────────────────────────────────────────────────────────
    # Jarvis outbound media path (required for MEDIA: delivery)
    out_dir = Path.home() / '.openclaw' / 'media' / 'outbound'
    out_dir.mkdir(parents=True, exist_ok=True)
    ts   = int(time.time())
    hex8 = secrets.token_hex(4)
    out_path = out_dir / f'liq_heatmap_{symbol}_{ts}_{hex8}.png'
    img.save(str(out_path), 'PNG')
    print(f'Saved: {out_path}')

    return str(out_path)  # absolute path for MEDIA: delivery


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('symbol', nargs='?', default='BTCUSDT')
    parser.add_argument('--push', action='store_true', help='Print MEDIA: line for Jarvis')
    args = parser.parse_args()

    path = _make_heatmap(args.symbol, push=args.push)
    if path:
        if args.push:
            print(f'\nMEDIA:{path}')
        else:
            print(f'Chart ready: {path}')
    else:
        print('Failed to generate chart')


if __name__ == '__main__':
    main()
