"""
liqmap_renderer.py · 梵天清算热图渲染器 v1.0
设计院封印 · 2026-07-09

功能：
  P1 - 历史清算密度累计存储（每次分析写入 liqmap_history/{sym}.jsonl）
  P2 - 多所融合 LiqMap PNG 图形渲染（Matplotlib）
  P3 - MAX标注 + 历史密度层叠加
"""

import os
import json
import time
import math
import secrets
from pathlib import Path
from collections import defaultdict
from typing import Optional
import requests
import numpy as np

# ── 路径配置 ─────────────────────────────────────────
_BASE = Path(__file__).parent.parent
_HISTORY_DIR = _BASE / 'data' / 'liqmap_history'
_MEDIA_DIR   = _BASE.parent / 'openclaw-media'  # workspace/openclaw-media
_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
_MEDIA_DIR.mkdir(parents=True, exist_ok=True)

_H = {'Accept': 'application/json', 'User-Agent': 'BrahmaLiqRenderer/1.0'}
_MAX_HISTORY_ROWS = 2000  # 每个币种最多保留N条历史记录


def _sg(url, p=None, t=7):
    try:
        r = requests.get(url, params=p, headers=_H, timeout=t)
        return r.json()
    except:
        return None


def _fv(x, d=0.0):
    try:
        return float(x) if x not in (None, '', 'null') else d
    except:
        return d


# ══════════════════════════════════════════════════════
# P1: 历史清算密度累计存储
# ══════════════════════════════════════════════════════

def fetch_all_liq(symbol: str) -> list:
    """
    从 OKX + Binance 拉取所有可用清算单。
    返回 [(price, qty, side, exchange, ts)]
    side: 'LONG_LIQ'=多头被爆  'SHORT_LIQ'=空头被爆
    """
    result = []

    # OKX（主要数据源）
    uly = 'BTC-USDT' if 'BTC' in symbol else 'ETH-USDT'
    d = _sg('https://www.okx.com/api/v5/public/liquidation-orders',
            {'instType': 'SWAP', 'uly': uly, 'state': 'filled', 'limit': '100'})
    if isinstance(d, dict) and isinstance(d.get('data'), list):
        for group in d['data']:
            for item in (group.get('details') or []):
                price = _fv(item.get('bkPx', 0))
                qty   = _fv(item.get('sz', 0))
                side  = item.get('side', '')
                ts    = int(item.get('ts', item.get('time', 0)))
                if price > 0:
                    liq_side = 'LONG_LIQ' if side == 'sell' else 'SHORT_LIQ'
                    result.append((price, qty, liq_side, 'OKX', ts))

    # Binance
    d2 = _sg('https://fapi.binance.com/fapi/v1/allForceOrders',
             {'symbol': symbol, 'limit': 200})
    if isinstance(d2, list):
        for x in d2:
            price = _fv(x.get('price', 0))
            qty   = _fv(x.get('origQty', 0))
            side  = x.get('side', '')
            ts    = int(x.get('time', 0))
            if price > 0:
                liq_side = 'LONG_LIQ' if side == 'SELL' else 'SHORT_LIQ'
                result.append((price, qty, liq_side, 'Binance', ts))

    return result


def append_history(symbol: str, liq_data: list) -> int:
    """将清算数据追加写入历史文件，返回写入条数"""
    hist_file = _HISTORY_DIR / f'{symbol}.jsonl'
    written = 0
    with open(hist_file, 'a', encoding='utf-8') as f:
        for price, qty, side, exchange, ts in liq_data:
            row = {
                'ts': ts or int(time.time() * 1000),
                'p':  round(price, 4),
                'q':  round(qty, 4),
                's':  side,
                'ex': exchange,
            }
            f.write(json.dumps(row) + '\n')
            written += 1

    # 修剪历史（保留最新N条）
    _trim_history(hist_file)
    return written


def _trim_history(path: Path):
    if not path.exists():
        return
    lines = path.read_text().strip().split('\n')
    if len(lines) > _MAX_HISTORY_ROWS:
        path.write_text('\n'.join(lines[-_MAX_HISTORY_ROWS:]) + '\n')


def load_history(symbol: str) -> list:
    """加载历史清算记录"""
    hist_file = _HISTORY_DIR / f'{symbol}.jsonl'
    if not hist_file.exists():
        return []
    result = []
    for line in hist_file.read_text().strip().split('\n'):
        if not line:
            continue
        try:
            result.append(json.loads(line))
        except:
            pass
    return result


def build_density(records: list, price: float, bin_pct: float = 0.5,
                  bins_above: int = 12, bins_below: int = 14):
    """
    根据历史+实时记录构建密度分布。
    返回：
      below_bins: list[dict]  多头被爆区间（下方）
      above_bins: list[dict]  空头被爆区间（上方）
    """
    step = price * bin_pct / 100

    below_ranges = [(price - step * i, price - step * (i - 1))
                    for i in range(1, bins_below + 1)]
    above_ranges = [(price + step * i, price + step * (i + 1))
                    for i in range(bins_above)]

    def stat(lo, hi, side_filter):
        n, vol = 0, 0.0
        for rec in records:
            p = rec.get('p') or rec[0] if isinstance(rec, tuple) else _fv(rec.get('p'))
            q = rec.get('q') or rec[1] if isinstance(rec, tuple) else _fv(rec.get('q'))
            s = rec.get('s') or rec[2] if isinstance(rec, tuple) else rec.get('s', '')
            if lo <= p < hi and s == side_filter:
                n   += 1
                vol += p * q
        return n, vol

    below_bins = []
    for lo, hi in below_ranges:
        n, vol = stat(lo, hi, 'LONG_LIQ')
        below_bins.append({'lo': lo, 'hi': hi, 'n': n, 'vol': vol,
                           'ex': 'multi'})

    above_bins = []
    for lo, hi in above_ranges:
        n, vol = stat(lo, hi, 'SHORT_LIQ')
        above_bins.append({'lo': lo, 'hi': hi, 'n': n, 'vol': vol,
                           'ex': 'multi'})

    return below_bins, above_bins


# ══════════════════════════════════════════════════════
# P2: PNG 图形渲染
# ══════════════════════════════════════════════════════

def render_liqmap_png(symbol: str, price: float,
                      below_bins: list, above_bins: list,
                      long_total: float, short_total: float,
                      sources: list) -> str:
    """
    生成 LiqMap PNG 图像。
    返回相对路径（相对于 workspace）
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    sym_short = 'BTC' if 'BTC' in symbol else 'ETH'

    # ── 数据准备 ─────────────────────────────
    # 合并所有区间，below=多头爆仓（红），above=空头爆仓（绿）
    all_bins   = []
    all_vols   = []
    all_colors = []
    all_labels = []
    all_prices = []  # 区间中心价格

    max_n_below = max((b['n'] for b in below_bins), default=1) or 1
    max_n_above = max((b['n'] for b in above_bins), default=1) or 1
    global_max  = max(max_n_below, max_n_above) or 1

    # 上方（从高到低排列，绘图从上往下）
    for b in reversed(above_bins):
        all_bins.append(b)
        all_vols.append(b['vol'])
        all_colors.append('#26a69a')  # 空头爆仓=青绿
        all_labels.append(f"${b['lo']:,.0f}")
        all_prices.append((b['lo'] + b['hi']) / 2)

    # 当前价（分隔线）
    all_bins.append({'lo': price, 'hi': price, 'n': -1, 'vol': 0})
    all_vols.append(0)
    all_colors.append('#ffffff00')
    all_labels.append(f'▶ ${price:,.2f}')
    all_prices.append(price)

    # 下方（从高到低排列）
    for b in below_bins:
        all_bins.append(b)
        all_vols.append(b['vol'])
        all_colors.append('#ef5350')  # 多头爆仓=红
        all_labels.append(f"${b['lo']:,.0f}")
        all_prices.append((b['lo'] + b['hi']) / 2)

    n_rows = len(all_bins)
    fig_h  = max(8, n_rows * 0.38)
    fig, ax = plt.subplots(figsize=(11, fig_h), facecolor='#0d1117')
    ax.set_facecolor('#0d1117')

    y_positions = list(range(n_rows))

    for i, (b, color) in enumerate(zip(all_bins, all_colors)):
        n = b['n']
        if n == -1:
            # 当前价分隔线
            ax.axhline(y=i, color='#ffd700', linewidth=1.5,
                       linestyle='--', alpha=0.9)
            ax.text(global_max * 1.02, i, f'  ${price:,.2f}  ◀ 当前价',
                    va='center', ha='left', color='#ffd700',
                    fontsize=8.5, fontweight='bold')
            continue

        bar_len = (n / global_max) * global_max if global_max > 0 else 0
        alpha   = 0.55 + 0.45 * (n / global_max) if global_max > 0 else 0.6

        ax.barh(i, n, color=color, alpha=alpha, height=0.72,
                left=0, edgecolor='none')

        # MAX标注
        is_max_below = (b in below_bins and n == max_n_below and max_n_below > 0)
        is_max_above = (b in above_bins and n == max_n_above and max_n_above > 0)

        label_color = '#ffd700' if (is_max_below or is_max_above) else '#b0bec5'
        suffix = '  ← MAX ⭐' if (is_max_below or is_max_above) else ''

        vol_m = b['vol'] / 1e6
        ax.text(n + global_max * 0.01, i,
                f"  n={n}  ${vol_m:.1f}M{suffix}",
                va='center', ha='left', color=label_color,
                fontsize=7.5)

        # 价格标签（左侧）
        ax.text(-global_max * 0.01, i,
                f"${b['lo']:,.0f}~${b['hi']:,.0f}",
                va='center', ha='right', color='#78909c', fontsize=7)

    # ── 装饰 ────────────────────────────────
    ax.set_xlim(-global_max * 0.35, global_max * 1.45)
    ax.set_ylim(-0.8, n_rows - 0.2)
    ax.set_yticks([])
    ax.set_xticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    ratio = long_total / short_total if short_total > 0 else 0
    src_str = '/'.join(sources) if sources else 'OKX'
    title = (f'LiqMap™  {sym_short}/USDT  |  来源: {src_str}\n'
             f'多头清算 ${long_total/1e6:.1f}M  |  空头清算 ${short_total/1e6:.1f}M  '
             f'|  比值 {ratio:.2f}x')
    ax.set_title(title, color='#eceff1', fontsize=9.5, pad=10,
                 fontfamily='DejaVu Sans')

    # 图例
    p1 = mpatches.Patch(color='#ef5350', alpha=0.8, label='多头被清算（止损密集区）')
    p2 = mpatches.Patch(color='#26a69a', alpha=0.8, label='空头被清算（止损密集区）')
    ax.legend(handles=[p1, p2], loc='lower right',
              facecolor='#1a1f2e', edgecolor='#37474f',
              labelcolor='#eceff1', fontsize=8)

    plt.tight_layout(pad=1.2)

    # ── 保存 ────────────────────────────────
    epoch    = int(time.time())
    hex8     = secrets.token_hex(4)
    filename = f'jarvis-image-{epoch}-{hex8}.png'
    rel_path = f'./openclaw-media/{filename}'
    abs_path = _MEDIA_DIR / filename

    plt.savefig(str(abs_path), dpi=130, bbox_inches='tight',
                facecolor='#0d1117')
    plt.close(fig)

    return rel_path


# ══════════════════════════════════════════════════════
# P3: 主入口 - 完整流程
# ══════════════════════════════════════════════════════

def generate_liqmap(symbol: str, price: float,
                    bin_pct: float = 0.5,
                    bins_above: int = 10,
                    bins_below: int = 12,
                    render_png: bool = True) -> dict:
    """
    完整流程：
    1. 拉取实时清算数据
    2. 写入历史库
    3. 加载历史+实时合并
    4. 构建密度分布
    5. 渲染 PNG
    6. 返回完整结果

    Returns:
        {
          formatted: str,     # 文字版LiqMap
          png_path:  str,     # PNG相对路径（如生成）
          long_total: float,
          short_total: float,
          asymmetry: float,
          max_below: dict,
          max_above: dict,
          sources: list,
          history_count: int,
        }
    """
    # 1. 拉取实时数据
    live_data = fetch_all_liq(symbol)
    sources   = list({ex for _, _, _, ex, _ in live_data}) if live_data else ['OKX']

    # 2. 写入历史
    hist_records_live = [
        {'ts': ts, 'p': p, 'q': q, 's': s, 'ex': ex}
        for p, q, s, ex, ts in live_data
    ]
    if hist_records_live:
        append_history(symbol, live_data)

    # 3. 加载历史
    hist = load_history(symbol)

    # 4. 合并（历史+实时，去重用ts+price）
    seen  = set()
    merged = []
    for rec in hist:
        key = (rec.get('ts', 0), round(rec.get('p', 0), 2))
        if key not in seen:
            seen.add(key)
            merged.append(rec)

    # 5. 构建密度
    below_bins, above_bins = build_density(
        merged, price, bin_pct=bin_pct,
        bins_above=bins_above, bins_below=bins_below
    )

    long_total  = sum(b['vol'] for b in below_bins)
    short_total = sum(b['vol'] for b in above_bins)
    ratio       = long_total / short_total if short_total > 0 else 0.0

    max_below = max(below_bins, key=lambda x: x['n']) if below_bins else None
    max_above = max(above_bins, key=lambda x: x['n']) if above_bins else None

    # 6. 生成文字版
    sym_short = 'BTC' if 'BTC' in symbol else 'ETH'
    lines = []
    lines.append(f'【LiqMap · {sym_short} · {"/".join(sources)}  历史{len(merged)}条】')
    lines.append(f'当前价 ${price:,.2f}  步长={bin_pct}%')
    lines.append('')

    max_n = max(
        max((b['n'] for b in above_bins), default=0),
        max((b['n'] for b in below_bins), default=0)
    ) or 1

    def bar(n, w=18):
        f = int(n / max_n * w)
        return '█' * f + '░' * (w - f)

    lines.append('▲ 上方（空头止损区）:')
    for b in reversed(above_bins):
        is_max = (max_above and b['lo'] == max_above['lo'])
        tag    = ' ← MAX' if is_max else ''
        vol_m  = b['vol'] / 1e6
        lines.append(
            f"  ${b['lo']:>10,.0f}~${b['hi']:>10,.0f}  {bar(b['n'])} "
            f"n={b['n']:4d}  ${vol_m:.1f}M{tag}"
        )

    lines.append(f'  {"─" * 62}')
    lines.append(f'  当前价 → ${price:,.2f}')
    lines.append(f'  {"─" * 62}')

    lines.append('▼ 下方（多头止损区）:')
    for b in below_bins:
        is_max = (max_below and b['lo'] == max_below['lo'])
        tag    = ' ← MAX' if is_max else ''
        vol_m  = b['vol'] / 1e6
        lines.append(
            f"  ${b['lo']:>10,.0f}~${b['hi']:>10,.0f}  {bar(b['n'])} "
            f"n={b['n']:4d}  ${vol_m:.1f}M{tag}"
        )

    lines.append('')
    lines.append(
        f'多头清算: ${long_total/1e6:.1f}M  空头清算: ${short_total/1e6:.1f}M  '
        f'下/上={ratio:.2f}x'
    )
    if ratio >= 2.5:
        lines.append(f'⚠️ 下方清算高度集中({ratio:.1f}x) → 猎杀多头止损动力强')
    elif 0 < ratio <= 0.4:
        lines.append(f'⚠️ 上方清算集中({1/ratio:.1f}x) → 猎杀空头止损动力强')
    else:
        lines.append('清算分布相对均衡')

    if max_below and max_below['n'] > 0:
        lines.append(
            f'MAX多头止损: ${max_below["lo"]:,.0f}~${max_below["hi"]:,.0f}'
            f'  (n={max_below["n"]}  ${max_below["vol"]/1e6:.1f}M)'
        )
    if max_above and max_above['n'] > 0:
        lines.append(
            f'MAX空头止损: ${max_above["lo"]:,.0f}~${max_above["hi"]:,.0f}'
            f'  (n={max_above["n"]}  ${max_above["vol"]/1e6:.1f}M)'
        )

    formatted = '\n'.join(lines)

    # 7. 渲染PNG
    png_path = None
    if render_png and (long_total > 0 or short_total > 0):
        try:
            png_path = render_liqmap_png(
                symbol, price, below_bins, above_bins,
                long_total, short_total, sources
            )
        except Exception as e:
            png_path = None

    return {
        'formatted':     formatted,
        'png_path':      png_path,
        'long_total':    long_total,
        'short_total':   short_total,
        'asymmetry':     ratio,
        'max_below':     max_below,
        'max_above':     max_above,
        'below_bins':    below_bins,
        'above_bins':    above_bins,
        'sources':       sources,
        'history_count': len(merged),
        'symbol':        symbol,
        'price':         price,
    }


if __name__ == '__main__':
    import requests as _r

    for sym in ['ETHUSDT', 'BTCUSDT']:
        p = float(_r.get('https://fapi.binance.com/fapi/v1/ticker/price',
                         params={'symbol': sym}, timeout=5).json()['price'])
        print(f'\n{"="*55}')
        print(f'生成 {sym} LiqMap @ ${p:.2f}')
        res = generate_liqmap(sym, p, render_png=True)
        print(res['formatted'])
        if res['png_path']:
            print(f'\nPNG生成: {res["png_path"]}')
        print(f'历史记录: {res["history_count"]} 条')
