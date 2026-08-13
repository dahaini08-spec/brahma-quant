#!/usr/bin/env python3
"""
brahma_brain/chart_renderer.py — 梵天图表渲染引擎
[设计院封印 2026-08-13 苏摩111]

功能：
  将梵天实时数据渲染为PNG图表，通过MEDIA:路径推送至Jarvis

图表类型：
  render_oi_fr(symbol)      → 图一：OI + FR 趋势图（双Y轴）
  render_liqmap(symbol)     → 图四：清算热力图（多所叠加）
  render_gex_hist(currency) → 图三：Historical GEX 时序图
  render_gex_scatter(currency) → 图二：GEX / IV 散点图

设计原则：
  - 总线优先：所有数据通过 BrahmaBus 获取（TTL缓存）
  - 无头渲染：matplotlib Agg后端，无需display
  - ASCII文字：图内标注全部ASCII（避免matplotlib CJK乱码）
  - 输出路径：~/.openclaw/workspace/openclaw-media/brahma-chart-<ts>-<hex8>.png
  - fail-safe：任何渲染失败返回None，不影响主流程

使用：
  from brahma_brain.chart_renderer import render_oi_fr, render_liqmap
  png_path = render_oi_fr('BTCUSDT')
  # 在消息中: MEDIA:<png_path>
"""

import os, sys, json, time, secrets
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# ── 路径配置 ──────────────────────────────────────────────────
_DIR   = Path(__file__).parent
_ROOT  = _DIR.parent
_DATA  = _ROOT / 'data'
_MEDIA_DIR = Path(os.path.expanduser('~/.openclaw/workspace/openclaw-media'))
_MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# ── matplotlib 无头模式 ───────────────────────────────────────
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
from matplotlib.gridspec import GridSpec
import warnings
warnings.filterwarnings('ignore')

# ── 颜色主题（深色交易风格）────────────────────────────────────
THEME = {
    'bg':       '#0d1117',
    'bg_panel': '#161b22',
    'grid':     '#21262d',
    'text':     '#e6edf3',
    'text_dim': '#8b949e',
    'green':    '#3fb950',
    'red':      '#f85149',
    'orange':   '#d29922',
    'blue':     '#58a6ff',
    'purple':   '#bc8cff',
    'yellow':   '#e3b341',
    'teal':     '#39d353',
    'binance':  '#f0b90b',
    'bybit':    '#f7a600',
    'okx':      '#0064fe',
    'hl':       '#7b68ee',
}


def _out_path(tag: str) -> str:
    """生成输出文件路径（相对路径，供MEDIA:使用）"""
    ts   = int(time.time())
    hex8 = secrets.token_hex(4)
    fname = f'brahma-chart-{tag}-{ts}-{hex8}.png'
    return str(_MEDIA_DIR / fname)


def _apply_dark_theme(fig, axes):
    """应用深色交易主题"""
    fig.patch.set_facecolor(THEME['bg'])
    for ax in (axes if hasattr(axes, '__iter__') else [axes]):
        ax.set_facecolor(THEME['bg_panel'])
        ax.tick_params(colors=THEME['text_dim'], labelsize=8)
        ax.xaxis.label.set_color(THEME['text_dim'])
        ax.yaxis.label.set_color(THEME['text_dim'])
        for spine in ax.spines.values():
            spine.set_color(THEME['grid'])
        ax.grid(True, color=THEME['grid'], linewidth=0.5, alpha=0.7)


# ════════════════════════════════════════════════════════════════
# 图一：OI + FR 趋势图
# ════════════════════════════════════════════════════════════════

def render_oi_fr(symbol: str = 'BTCUSDT', limit: int = 96) -> Optional[str]:
    """
    渲染 OI + FR 双轴趋势图（类Kingfisher图一）
    limit: 15min周期数量（96 = 24H）
    返回: PNG文件路径（相对），失败返回None
    """
    try:
        from brahma_brain.brahma_bus import BrahmaBus
        bus = BrahmaBus()

        # 数据采集
        oi_hist = bus.oi_history(symbol, period='15m', limit=limit)
        if not oi_hist:
            return None

        import urllib.request
        # FR历史（最近8期结算）
        url = f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=8'
        with urllib.request.urlopen(url, timeout=8) as r:
            fr_hist = json.loads(r.read())

        # 当前价
        price = bus.price(symbol)
        coin  = symbol.replace('USDT', '')

        # 解析OI数据
        oi_times = []
        oi_vals  = []
        oi_usd   = []
        for o in oi_hist:
            ts = o['timestamp'] / 1000
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            oi_times.append(dt)
            oi_vals.append(float(o['sumOpenInterest']))
            oi_usd.append(float(o['sumOpenInterestValue']) / 1e9)

        # OI变化率（4H）
        if len(oi_usd) >= 16:
            oi_chg_4h = (oi_usd[-1] - oi_usd[-16]) / oi_usd[-16] * 100
        else:
            oi_chg_4h = 0.0

        # 解析FR历史
        fr_times = []
        fr_vals  = []
        for f in fr_hist:
            ts = f['fundingTime'] / 1000
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            fr_times.append(dt)
            fr_vals.append(float(f['fundingRate']) * 100)

        # ── 绘图 ──────────────────────────────────────────────
        fig, axes = plt.subplots(2, 1, figsize=(12, 7), height_ratios=[3, 1])
        _apply_dark_theme(fig, axes)
        ax1, ax2 = axes

        fig.suptitle(
            f'{coin}/USDT  OI + Funding Rate  |  Price: ${price:,.1f}',
            color=THEME['text'], fontsize=11, fontweight='bold', y=0.98
        )

        # -- 上图：OI (USD Billion) --
        oi_color = THEME['green'] if oi_chg_4h >= 0 else THEME['red']
        ax1.fill_between(oi_times, oi_usd, alpha=0.3, color=oi_color)
        ax1.plot(oi_times, oi_usd, color=oi_color, linewidth=1.5, label=f'OI ${oi_usd[-1]:.2f}B')

        # OI变化率标注
        ax1.annotate(
            f'OI 4H: {oi_chg_4h:+.2f}%',
            xy=(oi_times[-1], oi_usd[-1]),
            xytext=(-80, 10), textcoords='offset points',
            color=oi_color, fontsize=9, fontweight='bold',
            arrowprops=dict(arrowstyle='->', color=oi_color, lw=1.2)
        )
        ax1.set_ylabel('OI (Billion USD)', color=THEME['text_dim'], fontsize=9)
        ax1.yaxis.set_major_formatter(mticker.FormatStrFormatter('$%.2fB'))
        ax1.legend(loc='upper left', facecolor=THEME['bg_panel'],
                   edgecolor=THEME['grid'], labelcolor=THEME['text'], fontsize=8)

        # -- 下图：FR历史 --
        fr_colors = [THEME['green'] if v >= 0 else THEME['red'] for v in fr_vals]
        ax2.bar(fr_times, fr_vals, color=fr_colors, width=0.07, alpha=0.85)
        ax2.axhline(0, color=THEME['grid'], linewidth=0.8)
        ax2.axhline(0.01, color=THEME['orange'], linewidth=0.6, linestyle='--', alpha=0.6)
        ax2.axhline(-0.01, color=THEME['orange'], linewidth=0.6, linestyle='--', alpha=0.6)

        if fr_vals:
            ax2.annotate(
                f'FR: {fr_vals[-1]:+.4f}%',
                xy=(fr_times[-1], fr_vals[-1]),
                xytext=(-60, 15), textcoords='offset points',
                color=THEME['yellow'], fontsize=8,
                arrowprops=dict(arrowstyle='->', color=THEME['yellow'], lw=1.0)
            )
        ax2.set_ylabel('Funding Rate %', color=THEME['text_dim'], fontsize=9)
        ax2.yaxis.set_major_formatter(mticker.FormatStrFormatter('%.4f%%'))

        # 时间轴格式
        import matplotlib.dates as mdates
        for ax in axes:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha='right', fontsize=7)

        # 水印
        fig.text(0.99, 0.01, 'Brahma Engine', ha='right', va='bottom',
                 color=THEME['text_dim'], fontsize=7, alpha=0.5)

        plt.tight_layout(rect=[0, 0, 1, 0.96])
        out = _out_path(f'oi-fr-{coin.lower()}')
        fig.savefig(out, dpi=130, bbox_inches='tight', facecolor=THEME['bg'])
        plt.close(fig)
        return out

    except Exception as e:
        try:
            plt.close('all')
        except Exception:
            pass
        return None


# ════════════════════════════════════════════════════════════════
# 图四：LiqMap 清算热力图
# ════════════════════════════════════════════════════════════════

def render_liqmap(symbol: str = 'BTCUSDT') -> Optional[str]:
    """
    渲染多所清算热力图（类Kingfisher图四）
    返回: PNG文件路径，失败返回None
    """
    try:
        from brahma_brain.brahma_bus import BrahmaBus
        from brahma_brain.liq_density_engine import get_liq_density
        from brahma_brain.liq_scanner import get_liq_snapshot

        bus   = BrahmaBus()
        price = bus.price(symbol)
        coin  = symbol.replace('USDT', '')

        snap  = get_liq_snapshot(symbol)
        density = get_liq_density(symbol, price)

        # 构建价格层级 → 清算量映射
        above = density.get('above_walls', [])  # [(price, usd_val), ...]
        below = density.get('below_walls', [])

        # 合并所有集群
        all_clusters = []
        for p_lvl, usd in above:
            all_clusters.append({'price': p_lvl, 'usd': usd, 'side': 'SHORT'})
        for p_lvl, usd in below:
            all_clusters.append({'price': p_lvl, 'usd': usd, 'side': 'LONG'})

        if not all_clusters:
            return None

        # 排序
        all_clusters.sort(key=lambda x: x['price'])

        prices_plot = [c['price'] for c in all_clusters]
        usds_plot   = [c['usd'] / 1e6 for c in all_clusters]  # 转M USD
        colors_plot = [THEME['red'] if c['side'] == 'SHORT' else THEME['green']
                       for c in all_clusters]

        # 额外：HL清算线
        hl_50x_long  = snap.get('hl_liq_50x_long')
        hl_50x_short = snap.get('hl_liq_50x_short')
        hl_25x_long  = snap.get('hl_liq_25x_long')
        hl_25x_short = snap.get('hl_liq_25x_short')

        # ── 绘图 ──────────────────────────────────────────────
        fig, ax = plt.subplots(figsize=(10, 8))
        _apply_dark_theme(fig, [ax])

        fig.suptitle(
            f'{coin}/USDT  Liquidation Map  |  ${price:,.1f}',
            color=THEME['text'], fontsize=11, fontweight='bold'
        )

        # 水平柱状图（价格为Y轴，清算量为X轴）
        bar_colors = colors_plot
        bars = ax.barh(
            [f'${p:,.0f}' for p in prices_plot],
            usds_plot,
            color=bar_colors, alpha=0.85, height=0.6
        )

        # 数值标注
        for bar, val in zip(bars, usds_plot):
            ax.text(val + 0.5, bar.get_y() + bar.get_height() / 2,
                    f'${val:.1f}M', va='center', ha='left',
                    color=THEME['text'], fontsize=7.5)

        # 当前价标线
        price_str = f'${price:,.1f}'
        # 找最近的Y轴位置
        nearest_idx = min(range(len(prices_plot)),
                          key=lambda i: abs(prices_plot[i] - price))
        ax.axhline(y=nearest_idx, color=THEME['yellow'], linewidth=1.5,
                   linestyle='--', alpha=0.9, label=f'Price {price_str}')

        # HL清算线标注
        legend_items = [
            mpatches.Patch(color=THEME['red'],   label='Short Liq (above)'),
            mpatches.Patch(color=THEME['green'], label='Long Liq  (below)'),
            mpatches.Patch(color=THEME['yellow'],label=f'Current {price_str}'),
        ]
        if hl_50x_long:
            legend_items.append(mpatches.Patch(color=THEME['hl'],
                label=f'HL 50x Long ${hl_50x_long:,.0f}'))
        if hl_50x_short:
            legend_items.append(mpatches.Patch(color=THEME['purple'],
                label=f'HL 50x Short ${hl_50x_short:,.0f}'))

        ax.legend(handles=legend_items, loc='lower right',
                  facecolor=THEME['bg_panel'], edgecolor=THEME['grid'],
                  labelcolor=THEME['text'], fontsize=8)

        ax.set_xlabel('Liquidation (Million USD)', color=THEME['text_dim'], fontsize=9)
        ax.set_ylabel('Price Level', color=THEME['text_dim'], fontsize=9)

        # 数据源标注
        sources = density.get('sources', '')
        fig.text(0.01, 0.01, f'Sources: {sources}', ha='left', va='bottom',
                 color=THEME['text_dim'], fontsize=6, alpha=0.6)
        fig.text(0.99, 0.01, 'Brahma Engine', ha='right', va='bottom',
                 color=THEME['text_dim'], fontsize=7, alpha=0.5)

        plt.tight_layout()
        out = _out_path(f'liqmap-{coin.lower()}')
        fig.savefig(out, dpi=130, bbox_inches='tight', facecolor=THEME['bg'])
        plt.close(fig)
        return out

    except Exception as e:
        try:
            plt.close('all')
        except Exception:
            pass
        return None


# ════════════════════════════════════════════════════════════════
# 图二：GEX / IV 散点图（实时，类Kingfisher图二）
# ════════════════════════════════════════════════════════════════

def render_gex_scatter(currency: str = 'BTC') -> Optional[str]:
    """
    渲染 GEX + IV 散点图（类Kingfisher图二）
    横轴 = IV隐含波动率，纵轴 = 行权价，颜色 = GEX值（正/负）
    返回: PNG路径，失败返回None
    """
    try:
        import math
        from brahma_brain.gex_scanner import (
            get_spot_price, get_book_summary, black_scholes_gamma,
            scan_gex, GAMMA_FALLBACK_IV
        )
        from datetime import datetime, timezone

        currency = currency.upper()
        spot     = get_spot_price(currency)
        books    = get_book_summary(currency)
        if not spot or not books:
            return None

        # GEX state (用缓存，已有实时数据)
        gex_state  = scan_gex(currency, force=False)
        zero_flip  = gex_state.get('zero_flip')
        max_strike = gex_state.get('max_gex_strike')
        min_strike = gex_state.get('min_gex_strike')
        net_at_spot= gex_state.get('net_gex_at_spot', 0)
        direction  = gex_state.get('gex_direction', '?')

        now_ts = datetime.now(timezone.utc).timestamp()
        points = []  # (iv, strike, gex, cp)

        for book in books:
            inst = book.get('instrument_name', '')
            parts = inst.split('-')
            if len(parts) < 4:
                continue
            try:
                exp_str = parts[1]
                strike  = float(parts[2])
                cp      = parts[3]
                exp_dt  = datetime.strptime(exp_str + ' 08:00', '%d%b%y %H:%M')
                exp_dt  = exp_dt.replace(tzinfo=timezone.utc)
                T = (exp_dt.timestamp() - now_ts) / (365 * 24 * 3600)
                if T <= 0 or T > 60/365:  # 只看60天内
                    continue

                sigma = book.get('mid_iv', book.get('mark_iv', 0)) / 100.0
                if sigma <= 0.01:
                    sigma = GAMMA_FALLBACK_IV

                oi = book.get('open_interest', 0)
                if oi < 0.1:
                    continue

                gamma = black_scholes_gamma(spot, strike, T, 0.05, sigma)
                gex   = gamma * oi * spot**2 * 0.01
                if cp == 'P':
                    gex = -gex

                points.append({
                    'iv':     round(sigma * 100, 2),
                    'strike': strike,
                    'gex':    gex,
                    'cp':     cp,
                })
            except Exception:
                pass

        if len(points) < 5:
            return None

        ivs     = [p['iv']     for p in points]
        strikes = [p['strike'] for p in points]
        gexs    = [p['gex']    for p in points]

        # 颜色映射：正GEX=绿，负GEX=红，大小∝|GEX|
        max_abs = max(abs(g) for g in gexs) or 1
        colors  = [THEME['green'] if g >= 0 else THEME['red'] for g in gexs]
        sizes   = [max(20, min(300, abs(g) / max_abs * 300)) for g in gexs]

        # 找MAX/MIN点
        max_idx = gexs.index(max(gexs))
        min_idx = gexs.index(min(gexs))
        cur_iv  = sum(ivs) / len(ivs)  # 近似当前IV均值

        # ── 绘图 ──────────────────────────────────────────────
        fig, ax = plt.subplots(figsize=(11, 9))
        _apply_dark_theme(fig, [ax])

        dir_emoji = '▲' if direction == 'POSITIVE' else '▼'
        fig.suptitle(
            f'{currency}  GEX+ / IV Scatter  |  ${spot:,.0f}  {dir_emoji}{direction}',
            color=THEME['text'], fontsize=11, fontweight='bold'
        )

        # 散点
        sc = ax.scatter(ivs, strikes, c=colors, s=sizes, alpha=0.75, edgecolors='none')

        # 当前价横线
        ax.axhline(spot, color=THEME['yellow'], linewidth=1.5, linestyle='--',
                   label=f'Spot ${spot:,.0f}')

        # Zero Flip横线
        if zero_flip:
            ax.axhline(zero_flip, color=THEME['orange'], linewidth=1.2, linestyle=':',
                       label=f'Zero Flip ${zero_flip:,.0f}')

        # MAX GEX标注
        ax.annotate(
            f'MAX\n${strikes[max_idx]:,.0f}',
            xy=(ivs[max_idx], strikes[max_idx]),
            xytext=(10, 10), textcoords='offset points',
            color=THEME['teal'], fontsize=8, fontweight='bold',
            arrowprops=dict(arrowstyle='->', color=THEME['teal'], lw=1.2)
        )

        # MIN GEX标注
        ax.annotate(
            f'MIN\n${strikes[min_idx]:,.0f}',
            xy=(ivs[min_idx], strikes[min_idx]),
            xytext=(10, -20), textcoords='offset points',
            color=THEME['red'], fontsize=8, fontweight='bold',
            arrowprops=dict(arrowstyle='->', color=THEME['red'], lw=1.2)
        )

        # 当前IV竖线（近似）
        ax.axvline(cur_iv, color=THEME['blue'], linewidth=1.0, linestyle='-.',
                   alpha=0.6, label=f'Avg IV {cur_iv:.1f}%')

        # 轴格式
        ax.set_xlabel('Implied Volatility (%)', color=THEME['text_dim'], fontsize=9)
        ax.set_ylabel('Strike Price (USD)',     color=THEME['text_dim'], fontsize=9)
        ax.yaxis.set_major_formatter(mticker.FormatStrFormatter('$%.0f'))
        ax.xaxis.set_major_formatter(mticker.FormatStrFormatter('%.1f%%'))

        # 图例
        legend_items = [
            mpatches.Patch(color=THEME['green'],  label='Positive GEX (Call)'),
            mpatches.Patch(color=THEME['red'],    label='Negative GEX (Put)'),
            mpatches.Patch(color=THEME['yellow'], label=f'Spot ${spot:,.0f}'),
        ]
        if zero_flip:
            legend_items.append(
                mpatches.Patch(color=THEME['orange'], label=f'Zero Flip ${zero_flip:,.0f}')
            )
        ax.legend(handles=legend_items, loc='upper right',
                  facecolor=THEME['bg_panel'], edgecolor=THEME['grid'],
                  labelcolor=THEME['text'], fontsize=8)

        # 信息框
        info = (
            f'Net GEX @ Spot: ${net_at_spot/1e6:.2f}M\n'
            f'Direction: {direction}\n'
            f'Contracts: {len(points)}'
        )
        ax.text(0.02, 0.97, info, transform=ax.transAxes,
                va='top', ha='left', fontsize=8,
                color=THEME['text'], bbox=dict(
                    boxstyle='round,pad=0.4', facecolor=THEME['bg_panel'],
                    edgecolor=THEME['grid'], alpha=0.9
                ))

        fig.text(0.99, 0.01, 'Brahma Engine', ha='right', va='bottom',
                 color=THEME['text_dim'], fontsize=7, alpha=0.5)

        plt.tight_layout()
        out = _out_path(f'gex-scatter-{currency.lower()}')
        fig.savefig(out, dpi=130, bbox_inches='tight', facecolor=THEME['bg'])
        plt.close(fig)
        return out

    except Exception as e:
        try:
            plt.close('all')
        except Exception:
            pass
        return None


# ════════════════════════════════════════════════════════════════
# 图三：Historical GEX 时序图
# ════════════════════════════════════════════════════════════════

def render_gex_hist(currency: str = 'BTC') -> Optional[str]:
    """
    渲染 Historical GEX + 价格 时序图（类Kingfisher图三）
    需要 data/gex_history.jsonl 有足够历史记录
    返回: PNG路径，不足数据返回None
    """
    try:
        _GEX_HISTORY = _DATA / 'gex_history.jsonl'
        if not _GEX_HISTORY.exists():
            return None

        records = []
        with open(_GEX_HISTORY) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get('currency', '').upper() == currency.upper():
                        records.append(r)
                except Exception:
                    pass

        if len(records) < 4:
            return None  # 数据不足，等积累

        # 排序
        records.sort(key=lambda x: x['ts'])

        times    = [datetime.fromtimestamp(r['ts'], tz=timezone.utc) for r in records]
        spots    = [r['spot'] for r in records]
        net_gex  = [r['net_gex_at_spot'] / 1e6 for r in records]  # M USD
        pos_gex  = [r['total_positive_gex'] / 1e6 for r in records]
        neg_gex  = [r['total_negative_gex'] / 1e6 for r in records]
        dirs     = [r['gex_direction'] for r in records]
        zflips   = [r.get('zero_flip') for r in records]

        # ── 绘图 ──────────────────────────────────────────────
        fig = plt.figure(figsize=(13, 8))
        gs  = GridSpec(3, 1, figure=fig, height_ratios=[2, 1.5, 1], hspace=0.08)
        ax_price = fig.add_subplot(gs[0])
        ax_gex   = fig.add_subplot(gs[1], sharex=ax_price)
        ax_net   = fig.add_subplot(gs[2], sharex=ax_price)

        _apply_dark_theme(fig, [ax_price, ax_gex, ax_net])
        fig.suptitle(
            f'{currency}  Historical GEX  ({len(records)} scans)',
            color=THEME['text'], fontsize=11, fontweight='bold'
        )

        # -- 价格 --
        ax_price.plot(times, spots, color=THEME['blue'], linewidth=1.5, label=f'{currency} Price')
        # Zero Flip 覆盖线
        valid_zf = [(t, z) for t, z in zip(times, zflips) if z]
        if valid_zf:
            tz_, zz_ = zip(*valid_zf)
            ax_price.plot(tz_, zz_, color=THEME['orange'], linewidth=1.0,
                          linestyle='--', alpha=0.7, label='Zero Flip')
        ax_price.set_ylabel('Price (USD)', color=THEME['text_dim'], fontsize=8)
        ax_price.legend(loc='upper left', facecolor=THEME['bg_panel'],
                        edgecolor=THEME['grid'], labelcolor=THEME['text'], fontsize=7)
        ax_price.yaxis.set_major_formatter(mticker.FormatStrFormatter('$%.0f'))

        # -- Positive / Negative GEX --
        ax_gex.fill_between(times, pos_gex, 0, alpha=0.4, color=THEME['green'], label='Positive GEX')
        ax_gex.fill_between(times, neg_gex, 0, alpha=0.4, color=THEME['red'],   label='Negative GEX')
        ax_gex.plot(times, pos_gex, color=THEME['green'], linewidth=1.0)
        ax_gex.plot(times, neg_gex, color=THEME['red'],   linewidth=1.0)
        ax_gex.axhline(0, color=THEME['grid'], linewidth=0.8)
        ax_gex.set_ylabel('GEX ($M)', color=THEME['text_dim'], fontsize=8)
        ax_gex.legend(loc='upper left', facecolor=THEME['bg_panel'],
                      edgecolor=THEME['grid'], labelcolor=THEME['text'], fontsize=7)

        # -- Net GEX (柱状) --
        bar_colors = [THEME['green'] if v >= 0 else THEME['red'] for v in net_gex]
        ax_net.bar(times, net_gex, color=bar_colors, alpha=0.8, width=0.08)
        ax_net.axhline(0, color=THEME['grid'], linewidth=0.8)
        ax_net.set_ylabel('Net GEX ($M)', color=THEME['text_dim'], fontsize=8)

        # 时间轴
        import matplotlib.dates as mdates
        for ax in [ax_price, ax_gex, ax_net]:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=12))
        plt.setp(ax_net.xaxis.get_majorticklabels(), rotation=30, ha='right', fontsize=7)
        plt.setp(ax_price.xaxis.get_majorticklabels(), visible=False)
        plt.setp(ax_gex.xaxis.get_majorticklabels(), visible=False)

        fig.text(0.99, 0.01, 'Brahma Engine', ha='right', va='bottom',
                 color=THEME['text_dim'], fontsize=7, alpha=0.5)

        out = _out_path(f'gex-hist-{currency.lower()}')
        fig.savefig(out, dpi=130, bbox_inches='tight', facecolor=THEME['bg'])
        plt.close(fig)
        return out

    except Exception as e:
        try:
            plt.close('all')
        except Exception:
            pass
        return None


# ════════════════════════════════════════════════════════════════
# 组合图：信号卡片（OI+FR + LiqMap 双图拼合）
# ════════════════════════════════════════════════════════════════

def render_signal_dashboard(symbol: str = 'BTCUSDT') -> Optional[str]:
    """
    渲染信号仪表盘：左OI+FR，右LiqMap（触发信号时附带）
    """
    p1 = render_oi_fr(symbol)
    p2 = render_liqmap(symbol)
    if not p1 or not p2:
        return p1 or p2  # 返回可用的那个

    try:
        from PIL import Image
        img1 = Image.open(p1)
        img2 = Image.open(p2)

        # 统一高度，横向拼接
        h = max(img1.height, img2.height)
        w = img1.width + img2.width

        combined = Image.new('RGB', (w, h), (13, 17, 23))
        combined.paste(img1, (0, 0))
        combined.paste(img2, (img1.width, 0))

        out = _out_path(f'dashboard-{symbol.replace("USDT","").lower()}')
        combined.save(out, 'PNG', dpi=(130, 130))

        # 清理临时图
        os.remove(p1)
        os.remove(p2)
        return out
    except Exception:
        return p1  # 拼图失败返回OI图


# ════════════════════════════════════════════════════════════════
# CLI 入口
# ════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Brahma Chart Renderer')
    parser.add_argument('--symbol',   default='BTCUSDT')
    parser.add_argument('--currency', default='BTC')
    parser.add_argument('--type',     choices=['oi_fr', 'liqmap', 'gex_scatter', 'gex_hist', 'dashboard', 'all'],
                        default='all')
    args = parser.parse_args()

    results = {}
    if args.type in ('oi_fr', 'all'):
        p = render_oi_fr(args.symbol)
        results['oi_fr'] = p
        print(f'[OI+FR]   {"OK" if p else "FAIL"}: {p}')

    if args.type in ('liqmap', 'all'):
        p = render_liqmap(args.symbol)
        results['liqmap'] = p
        print(f'[LiqMap]  {"OK" if p else "FAIL"}: {p}')

    if args.type in ('gex_scatter', 'all'):
        p = render_gex_scatter(args.currency)
        results['gex_scatter'] = p
        print(f'[GEXScatter] {"OK" if p else "FAIL"}: {p}')

    if args.type in ('gex_hist', 'all'):
        p = render_gex_hist(args.currency)
        results['gex_hist'] = p
        print(f'[GEXHist] {"OK" if p else "FAIL"}: {p or "Need more data"}')

    if args.type == 'dashboard':
        p = render_signal_dashboard(args.symbol)
        print(f'[Dashboard] {"OK" if p else "FAIL"}: {p}')
