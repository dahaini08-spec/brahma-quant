#!/usr/bin/env python3
"""
generate_backtest_chart.py — 回测vs实盘同屏对比曲线
设计院 2026-09-11 苏摩111封印

Minara Harness启发：把回测和实盘两条曲线同屏画，警告过拟合
数据源：
  - 回测：data/brahma_ic_stats.json (IC/EV/WR by regime×direction×score_bucket)
  - 实盘：data/wr_matrix_live.json (signal_settler实时结算)
  - 信号日志：data/live_signal_log.jsonl (逐笔信号)

输出：PNG图片 → Jarvis推送
"""
import json, sys, os
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
OUT  = BASE / 'data' / 'charts'
OUT.mkdir(exist_ok=True)

def load_backtest_stats():
    """加载回测IC/EV统计"""
    f = DATA / 'brahma_ic_stats.json'
    if not f.exists():
        return {}
    return json.loads(f.read_text())

def load_live_wr():
    """加载实盘WR矩阵"""
    f = DATA / 'wr_matrix_live.json'
    if not f.exists():
        return {}
    return json.loads(f.read_text())

def load_signal_log():
    """加载逐笔信号日志，按时间排序"""
    f = DATA / 'live_signal_log.jsonl'
    if not f.exists():
        return []
    sigs = []
    for line in f.read_text().splitlines():
        if not line.strip():
            continue
        try:
            sigs.append(json.loads(line))
        except Exception:
            pass
    return sigs

def _parse_ts(sig):
    """提取信号时间戳"""
    for k in ('ts', 'timestamp', 'created_at'):
        v = sig.get(k)
        if v:
            try:
                return float(v)
            except (TypeError, ValueError):
                try:
                    return datetime.fromisoformat(str(v).replace('Z','+00:00')).timestamp()
                except Exception:
                    pass
    return 0

def _parse_pnl(sig):
    """提取PnL百分比"""
    for k in ('pnl_pct', 'pnl', 'pnl_percent'):
        v = sig.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0

def _is_settled(sig):
    return sig.get('outcome') is not None

def _is_win(sig):
    o = sig.get('outcome', '')
    return o in ('TP1_HIT', 'TP2_HIT', 'TP3_HIT', 'WIN')

def generate_chart():
    """用Pillow画回测WR vs 实盘WR对比图"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print('ERROR: Pillow not installed')
        return None

    bt = load_backtest_stats()
    live = load_live_wr()
    sigs = load_signal_log()

    # 按体制×方向汇总
    # 回测数据
    bt_buckets = bt.get('ev_by_bucket', {})
    bt_rows = []
    for key, v in sorted(bt_buckets.items()):
        # key格式: "BULL_TREND:LONG:<120"
        parts = key.split(':')
        if len(parts) < 3:
            continue
        regime, direction, score = parts[0], parts[1], parts[2]
        n = v.get('n', 0)
        wr = v.get('wr', 0) or 0
        ev = v.get('ev', 0)
        bt_rows.append({
            'label': f'{regime[:8]}:{direction[:4]}:{score}',
            'regime': regime,
            'direction': direction,
            'score': score,
            'n': n,
            'wr': wr,
            'ev': ev,
        })

    # 实盘数据
    live_matrix = live.get('matrix', {})
    live_rows = []
    for key, v in sorted(live_matrix.items()):
        # key格式: "BULL_TREND|LONG"
        parts = key.split('|')
        if len(parts) < 2:
            continue
        regime, direction = parts[0], parts[1]
        total = v.get('total', 0)
        wr = v.get('wr', 0) or 0
        live_rows.append({
            'regime': regime,
            'direction': direction,
            'total': total,
            'wr': wr,
            'expired': v.get('expired', 0),
        })

    # 逐笔实盘净值曲线
    settled = [s for s in sigs if _is_settled(s)]
    settled.sort(key=_parse_ts)
    equity = 100.0  # 基准100
    equity_curve = [(0, 100.0)]
    cum_pnl = 0
    for s in settled:
        pnl = _parse_pnl(s)
        cum_pnl += pnl
        eq = 100 * (1 + cum_pnl / 100)
        equity_curve.append((_parse_ts(s), eq))

    # ── 画图 ───────────────────────────────────────────────────
    W, H = 800, 500
    MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 60, 30, 40, 50
    PLOT_W = W - MARGIN_L - MARGIN_R
    PLOT_H = H - MARGIN_T - MARGIN_B

    img = Image.new('RGB', (W, H), '#1a1a2e')
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
        font_s = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 10) if Path('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf').exists() else font
        font_m = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 12) if Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf').exists() else font
    except Exception:
        font_s = font_m = font

    # 标题
    draw.text((W//2 - 120, 8), 'Backtest WR vs Live WR', fill='#e0e0e0', font=font_m)
    draw.text((W//2 - 100, 24), f'Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")} UTC', fill='#888', font=font_s)

    # ── 左图：按体制×方向的WR柱状对比 ─────────────────────────
    all_keys = sorted(set(
        [(r['regime'], r['direction']) for r in live_rows] +
        [(r['regime'], r['direction']) for r in bt_rows]
    ))

    n_keys = len(all_keys)
    if n_keys == 0:
        draw.text((W//2 - 80, H//2), 'No data available', fill='#ff4444', font=font_m)
    else:
        bar_w = min(50, PLOT_W // (n_keys * 2 + 1))
        gap = bar_w // 3
        group_w = bar_w * 2 + gap
        start_x = MARGIN_L + (PLOT_W - group_w * n_keys) // 2

        max_wr = 100
        y0 = MARGIN_T + 20
        y1 = MARGIN_T + PLOT_H - 10

        # Y轴
        for pct in [0, 25, 50, 75, 100]:
            y = y1 - int((pct / max_wr) * (y1 - y0))
            draw.line([(MARGIN_L, y), (W - MARGIN_R, y)], fill='#333355')
            draw.text((MARGIN_L - 30, y - 5), f'{pct}%', fill='#888', font=font_s)

        # X轴
        draw.line([(MARGIN_L, y1), (W - MARGIN_R, y1)], fill='#555')

        for i, (regime, direction) in enumerate(all_keys):
            gx = start_x + i * group_w

            # 回测WR（蓝色柱）
            bt_wr = 0
            for r in bt_rows:
                if r['regime'] == regime and r['direction'] == direction:
                    bt_wr = max(bt_wr, r['wr'] * 100)

            # 实盘WR（绿色柱）
            live_wr = 0
            for r in live_rows:
                if r['regime'] == regime and r['direction'] == direction:
                    live_wr = r['wr'] * 100

            # 画柱
            bh_bt = int((bt_wr / max_wr) * (y1 - y0))
            bh_live = int((live_wr / max_wr) * (y1 - y0))
            draw.rectangle([gx, y1 - bh_bt, gx + bar_w, y1], fill='#4488ff')
            draw.rectangle([gx + bar_w + gap, y1 - bh_live, gx + bar_w + gap + bar_w, y1], fill='#44ff88')

            # 数值标注
            draw.text((gx, y1 - bh_bt - 12), f'{bt_wr:.0f}%', fill='#4488ff', font=font_s)
            draw.text((gx + bar_w + gap, y1 - bh_live - 12), f'{live_wr:.0f}%', fill='#44ff88', font=font_s)

            # X标签
            label = f'{regime[:6]}|{direction[:4]}'
            draw.text((gx - 5, y1 + 5), label, fill='#aaa', font=font_s)

        # 图例
        lx = W - MARGIN_R - 120
        ly = MARGIN_T + 2
        draw.rectangle([lx, ly, lx + 10, ly + 10], fill='#4488ff')
        draw.text((lx + 14, ly), 'Backtest WR', fill='#4488ff', font=font_s)
        draw.rectangle([lx, ly + 14, lx + 10, ly + 24], fill='#44ff88')
        draw.text((lx + 14, ly + 14), 'Live WR', fill='#44ff88', font=font_s)

    # ── 底部：实盘净值曲线 ─────────────────────────────────────
    if len(equity_curve) > 1:
        ey0 = H - MARGIN_B + 15
        ey1 = H - 5
        # 不可行空间太小，改为底部文字摘要
        pass

    # 底部摘要文字
    total_settled = live.get('total_settled', 0)
    bt_ic = bt.get('ic_by_regime', {})
    summary = (f'Live settled: {total_settled} | '
               f'BT IC: ' + ' '.join(f'{k}={v:.3f}' for k,v in list(bt_ic.items())[:3]))
    draw.text((MARGIN_L, H - 18), summary, fill='#666', font=font_s)

    # 警告文字
    draw.text((W//2 - 100, H - 32), 'BT != Future | Overfitting Warning', fill='#ff6644', font=font_s)

    out_path = OUT / f'bt_vs_live_{int(datetime.now(timezone.utc).timestamp())}.png'
    img.save(str(out_path))
    print(f'Chart saved: {out_path}')
    return out_path


if __name__ == '__main__':
    p = generate_chart()
    if p:
        print(str(p))
    else:
        print('FAILED')
