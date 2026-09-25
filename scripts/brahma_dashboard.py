#!/usr/bin/env python3
"""
梵天终端仪表盘 — Brahma TUI Dashboard
[2026-09-22 苏摩111] 基于rich的实时监控面板

启动: python3 scripts/brahma_dashboard.py          # 静态快照
启动: python3 scripts/brahma_dashboard.py --live   # 实时刷新(2s)
退出: Ctrl+C
"""

import json, time, os, sys, re, subprocess
from pathlib import Path
from datetime import datetime, timezone

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.align import Align
    from rich.columns import Columns
    from rich.live import Live
except ImportError:
    print("请先安装: pip install rich"); sys.exit(1)

_BASE = Path(__file__).parent.parent
_DATA = _BASE / 'data'
_LATEST = _DATA / 'auto_analysis_latest.json'
_SIGNAL_LOG = _DATA / 'live_signal_log.jsonl'

def _sf(v, d=0):
    try: return float(v) if v is not None else d
    except: return d

def load_latest():
    if not _LATEST.exists(): return {}
    try: return json.loads(_LATEST.read_text())
    except: return {}

def load_signals(n=10):
    if not _SIGNAL_LOG.exists(): return []
    signals = []
    for line in reversed(_SIGNAL_LOG.read_text().strip().split('\n')):
        if len(signals) >= n: break
        try: signals.append(json.loads(line))
        except: pass
    return signals

def count_signals():
    if not _SIGNAL_LOG.exists(): return 0
    return sum(1 for l in _SIGNAL_LOG.read_text().strip().split('\n') if l.strip())

def check_procs():
    procs = {'supercronic': False, 'cvd': False, 'liqmap': False, 'watchdog': False}
    try:
        out = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5).stdout
        if 'supercronic' in out: procs['supercronic'] = True
        if 'cvd_ws_collector' in out: procs['cvd'] = True
        if 'liq_multi_exchange' in out or 'liqmap' in out: procs['liqmap'] = True
        if 'independent_watchdog' in out: procs['watchdog'] = True
    except: pass
    return procs

def parse_output(d):
    out = d.get('output', '')
    results = {}
    for sym in ['BTC', 'ETH']:
        marker = f'{sym}/USDT'
        idx = out.find(marker)
        if idx < 0:
            results[sym] = {'price': 0, 'score': 0, 'regime': '?', 'hurst': 0,
                           'direction': '?', 'action': '?', 'cross_val': '?',
                           'liq_short': 0, 'liq_long': 0, 'fvg': 0}
            continue
        if sym == 'BTC':
            next_idx = out.find('ETH/USDT', idx + 1)
            section = out[idx:next_idx] if next_idx > 0 else out[idx:idx+5000]
        else:
            section = out[idx:idx+5000]

        price_m = re.search(r'\$([\d,]+\.?\d*)', section[:200])
        score_m = re.search(r'score[=: ]+(\d+\.?\d*)', section)
        regime_m = re.search(r'(CHOP_MID|BULL_TREND|BEAR_TREND|BEAR_EARLY|BULL_EARLY|BEAR_RECOVERY)', section)
        hurst_m = re.search(r'Hurst[=: ]*0\.(\d+)', section) or re.search(r'H=(\d\.\d+)', section)
        dir_m = re.search(r'方向[=: ]*(LONG|SHORT)', section)
        act_m = re.search(r'(ENTER|WATCH|EXECUTE|REJECT)', section)
        cv_m = re.search(r'(\d)/4', section)
        ls_m = re.search(r'止损墙[：: ]*\$([\d,]+)', section)
        ll_m = re.search(r'支撑池[：: ]*\$([\d,]+)', section)
        fvg_m = re.search(r'FVG磁铁[：: ]*\$([\d,]+)', section) or re.search(r'主磁铁.*?\$([\d,]+)', section)

        results[sym] = {
            'price': _sf(price_m.group(1).replace(',','')) if price_m else 0,
            'score': _sf(score_m.group(1)) if score_m else 0,
            'regime': regime_m.group(1) if regime_m else '?',
            'hurst': float('0.' + hurst_m.group(1)) if hurst_m else 0,
            'direction': dir_m.group(1) if dir_m else '?',
            'action': act_m.group(1) if act_m else '?',
            'cross_val': cv_m.group(1) if cv_m else '?',
            'liq_short': _sf(ls_m.group(1).replace(',','')) if ls_m else 0,
            'liq_long': _sf(ll_m.group(1).replace(',','')) if ll_m else 0,
            'fvg': _sf(fvg_m.group(1).replace(',','')) if fvg_m else 0,
        }
    return results

def build_panels(console_width=120):
    d = load_latest()
    signals = load_signals(10)
    procs = check_procs()
    parsed = parse_output(d)
    btc = parsed.get('BTC', {})
    eth = parsed.get('ETH', {})
    
    # === Header ===
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    header = Panel(Align.center(Text(f"🏛️ 梵天系统仪表盘  |  {now}  |  Ctrl+C退出", style="bold cyan")),
                   style="cyan", height=3)

    # === BTC卡片 ===
    btc_color = "green" if btc.get('action') == 'ENTER' else "yellow"
    btc_text = Text()
    btc_text.append(f"BTC  ${btc.get('price', 0):,.0f}\n", style="bold white")
    btc_text.append(f"体制: {btc.get('regime', '?')}\n", style="cyan")
    btc_text.append(f"score: {btc.get('score', 0):.0f}  ", style="bold")
    btc_text.append(f"Hurst: {btc.get('hurst', 0):.3f}\n", style="magenta")
    btc_text.append(f"方向: {btc.get('direction', '?')}  ", style="yellow")
    btc_text.append(f"交叉: {btc.get('cross_val', '?')}/4\n", style="yellow")
    btc_text.append(f"决策: {btc.get('action', '?')}", style=f"bold {btc_color}")
    btc_panel = Panel(btc_text, title="BTC", border_style=btc_color, height=8)

    # === ETH卡片 ===
    eth_color = "green" if eth.get('action') == 'ENTER' else "yellow"
    eth_text = Text()
    eth_text.append(f"ETH  ${eth.get('price', 0):,.0f}\n", style="bold white")
    eth_text.append(f"体制: {eth.get('regime', '?')}\n", style="cyan")
    eth_text.append(f"score: {eth.get('score', 0):.0f}  ", style="bold")
    eth_text.append(f"Hurst: {eth.get('hurst', 0):.3f}\n", style="magenta")
    eth_text.append(f"方向: {eth.get('direction', '?')}  ", style="yellow")
    eth_text.append(f"交叉: {eth.get('cross_val', '?')}/4\n", style="yellow")
    eth_text.append(f"决策: {eth.get('action', '?')}", style=f"bold {eth_color}")
    eth_panel = Panel(eth_text, title="ETH", border_style=eth_color, height=8)

    # === 系统状态 ===
    proc_count = sum(1 for v in procs.values() if v)
    proc_color = "green" if proc_count == 4 else "red"
    status_text = Text()
    status_text.append(f"进程: {proc_count}/4\n", style=f"bold {proc_color}")
    for name, ok in procs.items():
        status_text.append(f"  {'✅' if ok else '❌'} {name}\n", style="green" if ok else "red")
    status_text.append(f"\n信号总数: {count_signals()}\n", style="dim")
    status_text.append(f"分析: {d.get('timestamp', '?')}\n", style="dim")
    status_text.append(f"耗时: {d.get('elapsed_s', '?')}s\n", style="dim")
    status_panel = Panel(status_text, title="系统状态", border_style=proc_color, height=8)

    # === 信号历史 ===
    sig_table = Table(expand=True, show_lines=False, title="📊 最近10条信号")
    sig_table.add_column("时间", style="dim", width=16)
    sig_table.add_column("标的", style="cyan", width=8)
    sig_table.add_column("方向", width=6)
    sig_table.add_column("score", style="bold", width=6)
    sig_table.add_column("体制", width=12)
    sig_table.add_column("action", width=8)
    sig_table.add_column("特征", style="dim", width=6)
    for s in signals:
        sig_table.add_row(
            s.get('ts_iso', '?')[:16],
            s.get('symbol', '?')[:6],
            s.get('direction', '?')[:5],
            f"{_sf(s.get('score', 0)):.0f}",
            s.get('regime', '?')[:11],
            s.get('action', '?')[:7],
            str(len(s.get('features', {}))),
        )
    sig_panel = Panel(sig_table, border_style="blue")

    # === 关键价位 ===
    level_text = Text()
    for sym in ['BTC', 'ETH']:
        p = parsed.get(sym, {})
        price = p.get('price', 0)
        level_text.append(f"{sym} ${price:,.0f}\n", style="bold white")
        if p.get('liq_short'):
            pct = (p['liq_short'] - price) / price * 100 if price else 0
            level_text.append(f"  止损墙: ${p['liq_short']:,.0f} ({pct:+.1f}%)\n", style="red")
        if p.get('liq_long'):
            pct = (p['liq_long'] - price) / price * 100 if price else 0
            level_text.append(f"  支撑池: ${p['liq_long']:,.0f} ({pct:+.1f}%)\n", style="green")
        if p.get('fvg'):
            level_text.append(f"  FVG磁铁: ${p['fvg']:,.0f}\n", style="magenta")
        level_text.append("\n")
    level_panel = Panel(level_text, title="🎯 关键价位", border_style="magenta", height=10)

    # === 交易员大脑 ===
    brain_text = Text()
    for sym in ['BTC', 'ETH']:
        p = parsed.get(sym, {})
        action = p.get('action', '?')
        color = "green" if action == 'ENTER' else "yellow" if action == 'WATCH' else "red"
        brain_text.append(f"{sym}: ", style="bold white")
        brain_text.append(f"{action} {p.get('direction', '?')} {p.get('cross_val', '?')}/4\n", style=color)
    nano_count = sum(1 for s in signals if s.get('features'))
    brain_text.append(f"\nNanoJev: {nano_count}条带特征\n", style="cyan")
    brain_panel = Panel(brain_text, title="🧠 交易员大脑", border_style="yellow", height=10)

    # === 组装 ===
    top_row = Columns([btc_panel, eth_panel, status_panel], expand=True, equal=True)
    mid_row = Columns([sig_panel], expand=True)
    bottom_row = Columns([level_panel, brain_panel], expand=True, equal=True)

    return [header, top_row, mid_row, bottom_row]


def main():
    console = Console()
    if '--live' not in sys.argv:
        # 静态模式
        for panel in build_panels():
            console.print(panel)
        return

    # 实时模式
    try:
        with Live(console=console, refresh_per_second=0.5, screen=True) as live:
            while True:
                panels = build_panels()
                from rich.group import Group
                live.update(Group(*panels))
                time.sleep(2)
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()
