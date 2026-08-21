#!/usr/bin/env python3
"""
scripts/push_chart.py — 梵天图表推送出口
[设计院封印 2026-08-13 苏摩111]

功能：
  将 chart_renderer 生成的PNG图表推送到Jarvis线程
  
用法：
  from push_chart import push_oi_fr, push_liqmap, push_dashboard

  # 信号触发时附带图表
  push_oi_fr('BTCUSDT')
  push_liqmap('ETHUSDT')
  push_dashboard('BTCUSDT')  # OI+FR + LiqMap 组合图
"""

import sys, os, subprocess
from pathlib import Path

# 路径设置
_SCRIPTS_DIR = Path(__file__).parent
_BRAIN_DIR   = _SCRIPTS_DIR.parent / 'brahma_brain'
_ROOT        = _SCRIPTS_DIR.parent

for p in [str(_SCRIPTS_DIR), str(_BRAIN_DIR.parent)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# 推送配置（SSOT from system_config）
try:
    from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID, JARVIS_CHANNEL
    _TARGET  = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"
    _CHANNEL = JARVIS_CHANNEL
except Exception:
    _TARGET  = "73295708:thread:019fd70a-0942-72b1-aeb9-1bd4fc11b30d"
    _CHANNEL = "jarvis"


def _push_image(png_path: str, caption: str = '') -> bool:
    """通过openclaw message发送图片到Jarvis"""
    if not png_path or not os.path.exists(png_path):
        return False
    try:
        cmd = [
            'openclaw', 'message', 'send',
            '--channel', _CHANNEL,
            '--target',  _TARGET,
        ]
        if caption:
            cmd += ['--message', caption]
        cmd += ['--file', png_path]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            # fallback: MEDIA路径方式（通过文本消息）
            rel_path = _to_relative(png_path)
            msg = f'{caption}\nMEDIA:{rel_path}' if caption else f'MEDIA:{rel_path}'
            r2 = subprocess.run(
                ['openclaw', 'message', 'send',
                 '--channel', _CHANNEL, '--target', _TARGET, '--message', msg],
                capture_output=True, text=True, timeout=15
            )
            return r2.returncode == 0
        return True
    except Exception as e:
        print(f'[push_chart] 推送失败: {e}')
        return False


def _to_relative(abs_path: str) -> str:
    """转为相对workspace路径"""
    workspace = os.path.expanduser('~/.openclaw/workspace')
    try:
        rel = os.path.relpath(abs_path, workspace)
        return rel
    except Exception:
        return abs_path


def push_oi_fr(symbol: str = 'BTCUSDT', caption: str = '') -> bool:
    """推送OI+FR趋势图"""
    try:
        sys.path.insert(0, str(_ROOT))
        from brahma_brain.chart_renderer import render_oi_fr
        coin = symbol.replace('USDT', '')
        path = render_oi_fr(symbol)
        if not path:
            return False
        cap = caption or f'📊 {coin}/USDT OI + Funding Rate'
        return _push_image(path, cap)
    except Exception as e:
        print(f'[push_chart] push_oi_fr error: {e}')
        return False


def push_liqmap(symbol: str = 'BTCUSDT', caption: str = '') -> bool:
    """推送清算热力图"""
    try:
        sys.path.insert(0, str(_ROOT))
        from brahma_brain.chart_renderer import render_liqmap
        coin = symbol.replace('USDT', '')
        path = render_liqmap(symbol)
        if not path:
            return False
        cap = caption or f'🗺 {coin}/USDT Liquidation Map'
        return _push_image(path, cap)
    except Exception as e:
        print(f'[push_chart] push_liqmap error: {e}')
        return False


def push_dashboard(symbol: str = 'BTCUSDT', caption: str = '', score: float = 0,
                   include_gex: bool = True) -> bool:
    """推送信号仪表盘（OI+FR + GEX散点 + LiqMap 三图组合）"""
    try:
        sys.path.insert(0, str(_ROOT))
        from brahma_brain.chart_renderer import render_signal_dashboard, cleanup_old_charts
        coin = symbol.replace('USDT', '')
        path = render_signal_dashboard(symbol, include_gex=include_gex)
        if not path:
            return False
        score_str = f' score={score:.0f}' if score else ''
        cap = caption or f'📈 {coin}/USDT Signal Dashboard{score_str}'
        ok = _push_image(path, cap)
        # 推送后清理旧图，保留最新20个
        cleanup_old_charts(keep_n=20)
        return ok
    except Exception as e:
        print(f'[push_chart] push_dashboard error: {e}')
        return False


def push_kingfisher(symbol: str = 'BTCUSDT', caption: str = '') -> bool:
    """推送 Kingfisher 风格三图组合（OI+FR / GEX散点 / LiqMap）高清晰版"""
    try:
        sys.path.insert(0, str(_ROOT))
        from brahma_brain.chart_renderer import render_kingfisher, cleanup_old_charts
        coin = symbol.replace('USDT', '')
        path = render_kingfisher(symbol)
        if not path:
            return False
        cap = caption or f'\U0001f4ca {coin}/USDT Kingfisher | {__import__("datetime").datetime.utcnow().strftime("%H:%M UTC")}'
        ok = _push_image(path, cap)
        cleanup_old_charts(keep_n=20)
        return ok
    except Exception as e:
        print(f'[push_chart] push_kingfisher error: {e}')
        return False


def push_gex_hist(currency: str = 'BTC') -> bool:
    """推送Historical GEX图（需要积累足够历史数据）"""
    try:
        sys.path.insert(0, str(_ROOT))
        from brahma_brain.chart_renderer import render_gex_hist
        path = render_gex_hist(currency)
        if not path:
            print(f'[push_chart] GEX历史数据不足，跳过')
            return False
        return _push_image(path, f'📉 {currency} Historical GEX')
    except Exception as e:
        print(f'[push_chart] push_gex_hist error: {e}')
        return False


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol',   default='BTCUSDT')
    parser.add_argument('--currency', default='BTC')
    parser.add_argument('--type',     choices=['oi_fr', 'liqmap', 'dashboard', 'gex_hist', 'all'],
                        default='dashboard')
    args = parser.parse_args()

    if args.type in ('oi_fr', 'all'):
        ok = push_oi_fr(args.symbol)
        print(f'push_oi_fr: {"OK" if ok else "FAIL"}')
    if args.type in ('liqmap', 'all'):
        ok = push_liqmap(args.symbol)
        print(f'push_liqmap: {"OK" if ok else "FAIL"}')
    if args.type in ('dashboard', 'all'):
        ok = push_dashboard(args.symbol)
        print(f'push_dashboard: {"OK" if ok else "FAIL"}')
    if args.type in ('gex_hist', 'all'):
        ok = push_gex_hist(args.currency)
        print(f'push_gex_hist: {"OK" if ok else "FAIL"}')
