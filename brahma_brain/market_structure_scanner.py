import os
#!/usr/bin/env python3
"""
market_structure_scanner.py — 四维市场结构扫描器
设计院 · 苏摩111 · 2026-07-01

扫描四大结构层：
  1. OB  (Order Block)    — SMC订单块，最强压力/支撑区
  2. LIQ (清算群)         — 多空强制平仓密集区
  3. GEX (期权伽马)       — 做市商对冲压力/磁铁位
  4. FVG (Fair Value Gap) — 公允价值缺口，回补磁铁

调用：
  python3 brahma_brain/market_structure_scanner.py
  python3 brahma_brain/market_structure_scanner.py --symbol ETHUSDT
  from brahma_brain.market_structure_scanner import scan_structure
"""

import sys, os, json, time
from pathlib import Path

_DIR  = Path(__file__).parent
_ROOT = _DIR.parent
sys.path.insert(0, str(_ROOT))

from brahma_brain.smc_engine     import analyze_smc
from brahma_brain.data_cache     import get_klines
from brahma_brain.brahma_bus     import bus
from brahma_brain.liq_scanner    import get_liq_snapshot

GEX_STATE_FILE = _ROOT / 'data' / 'gex_state.json'


# ════════════════════════════════════════════════════════════════
# 核心扫描函数
# ════════════════════════════════════════════════════════════════

def scan_structure(symbol: str = 'BTCUSDT') -> dict:
    """
    扫描 OB / LIQ / GEX / FVG 四维结构，返回综合结果字典
    """
    coin = symbol.replace('USDT', '').upper()
    result = {'symbol': symbol, 'coin': coin, 'ts': time.time()}

    # ── 1. 当前价 ─────────────────────────────────────────────
    try:
        px = bus.price(symbol)
    except Exception:
        import requests
        px = float(requests.get(
            f'https://fapi.binance.com/fapi/v1/ticker/price',
            params={'symbol': symbol}, timeout=5).json()['price'])
    result['price'] = px

    # ── 2. OB — 订单块 ────────────────────────────────────────
    ob_data = {'bear_ob': {}, 'bull_ob': {}, 'bear_ob_4h': {}, 'bull_ob_4h': {}}
    try:
        k1h = get_klines(symbol, '1h', 200)
        k4h = get_klines(symbol, '4h', 100)
        smc_1h = analyze_smc(symbol, k1h)
        smc_4h = analyze_smc(symbol, k4h)

        ob_1h = smc_1h.get('order_blocks', {})
        ob_4h = smc_4h.get('order_blocks', {})
        fvg_1h = smc_1h.get('fvg', {})
        fvg_4h = smc_4h.get('fvg', {})

        ob_data['bear_ob']    = ob_1h.get('nearest_bear_ob', {})
        ob_data['bull_ob']    = ob_1h.get('nearest_bull_ob', {})
        ob_data['bear_ob_4h'] = ob_4h.get('nearest_bear_ob', {})
        ob_data['bull_ob_4h'] = ob_4h.get('nearest_bull_ob', {})
        ob_data['bear_obs_all'] = ob_1h.get('bear_obs', [])[:3]
        ob_data['bull_obs_all'] = ob_1h.get('bull_obs', [])[:3]

        # FVG
        fvg_data = {
            'bear_fvg':    fvg_1h.get('nearest_bear', {}),
            'bull_fvg':    fvg_1h.get('nearest_bull', {}),
            'bear_fvg_4h': fvg_4h.get('nearest_bear', {}),
            'bull_fvg_4h': fvg_4h.get('nearest_bull', {}),
            'bear_fvgs':   fvg_1h.get('bear_gaps', [])[:3],
            'bull_fvgs':   fvg_1h.get('bull_gaps', [])[:3],
        }
        result['fvg'] = fvg_data
    except Exception as e:
        ob_data['error'] = str(e)
        result['fvg'] = {}
    result['ob'] = ob_data

    # ── 3. LIQ — 清算群 ───────────────────────────────────────
    liq_data = {}
    try:
        snap = get_liq_snapshot(symbol)
        liq_data = {
            'long_pct':        snap.get('long_pct', 0),
            'short_pct':       snap.get('short_pct', 0),
            'liq_short_5pct':  snap.get('liq_short_5pct', 0),   # 空头+5%清算密集位
            'liq_short_10pct': snap.get('liq_short_10pct', 0),
            'liq_long_5pct':   snap.get('liq_long_5pct', 0),    # 多头-5%清算密集位
            'liq_long_10pct':  snap.get('liq_long_10pct', 0),
            'fund_rate':       snap.get('fund_rate', 0),
            'fund_bias':       snap.get('fund_bias', ''),
            'liq_bias':        snap.get('liq_bias', ''),
            'liq_risk':        snap.get('liq_risk', ''),
            'oi_b':            snap.get('oi_b', 0),
            'oi_chg4h':        snap.get('oi_chg4h', 0),
        }
        # Tardis清算墙（最重要的精确数据）
        tardis = snap.get('tardis_walls', {})
        if tardis.get('available'):
            long_walls  = tardis.get('long_walls', [])[:3]
            short_walls = tardis.get('short_walls', [])[:3]
            liq_data['tardis_long_walls']  = long_walls
            liq_data['tardis_short_walls'] = short_walls
            liq_data['tardis_bias']        = tardis.get('bias', '')
    except Exception as e:
        liq_data['error'] = str(e)
    result['liq'] = liq_data

    # ── 4. GEX — 期权伽马暴露 ────────────────────────────────
    gex_data = {}
    try:
        if GEX_STATE_FILE.exists():
            gex_state = json.loads(GEX_STATE_FILE.read_text())
            g = gex_state.get(coin, {})
            gex_data = {
                'max_strike':   g.get('max_gex_strike', 0),
                'min_strike':   g.get('min_gex_strike', 0),
                'zero_flip':    g.get('zero_flip', 0),
                'direction':    g.get('gex_direction', ''),
                'dist_max_pct': g.get('dist_to_max_pct', 0),
                'dist_min_pct': g.get('dist_to_min_pct', 0),
                'net_at_spot':  g.get('net_gex_at_spot', 0),
                'fib_786':      g.get('fib_786', 0),
                'fib_618':      g.get('fib_618', 0),
                'fib_500':      g.get('fib_500', 0),
                'fib_382':      g.get('fib_382', 0),
            }
    except Exception as e:
        gex_data['error'] = str(e)
    result['gex'] = gex_data

    return result


# ════════════════════════════════════════════════════════════════
# 格式化输出（供AI播报）
# ════════════════════════════════════════════════════════════════

def format_report(r: dict) -> str:
    """格式化为标准监控卡片"""
    sym   = r.get('coin', '?')
    px    = r.get('price', 0)
    ob    = r.get('ob', {})
    liq   = r.get('liq', {})
    gex   = r.get('gex', {})
    fvg   = r.get('fvg', {})

    def pf(v, is_usd=True):
        if not v: return 'N/A'
        if is_usd:
            if v > 1000: return f'${v:,.0f}'
            if v > 1:    return f'${v:,.2f}'
            return f'${v:.5f}'
        return str(v)

    lines = [f'【{sym}/USDT · ${px:,.1f}】']

    # OB
    bear_ob = ob.get('bear_ob', {})
    bull_ob = ob.get('bull_ob', {})
    bear_ob_4h = ob.get('bear_ob_4h', {})
    bull_ob_4h = ob.get('bull_ob_4h', {})

    if bear_ob or bull_ob:
        lines.append('📦 OB订单块')
        if bear_ob and bear_ob.get('high'):
            dist = (bear_ob['high'] - px) / px * 100
            lines.append(f'  空头OB 1H: {pf(bear_ob.get("low"))}~{pf(bear_ob.get("high"))} ({dist:+.2f}%)')
        if bull_ob and bull_ob.get('high'):
            dist = (px - bull_ob['low']) / px * 100
            lines.append(f'  多头OB 1H: {pf(bull_ob.get("low"))}~{pf(bull_ob.get("high"))} ({dist:+.2f}%)')
        if bear_ob_4h and bear_ob_4h.get('high') and bear_ob_4h != bear_ob:
            lines.append(f'  空头OB 4H: {pf(bear_ob_4h.get("low"))}~{pf(bear_ob_4h.get("high"))}')
        if bull_ob_4h and bull_ob_4h.get('high') and bull_ob_4h != bull_ob:
            lines.append(f'  多头OB 4H: {pf(bull_ob_4h.get("low"))}~{pf(bull_ob_4h.get("high"))}')

    # 清算
    if liq and not liq.get('error'):
        lines.append('💥 清算群')
        tardis_long  = liq.get('tardis_long_walls',  [])
        tardis_short = liq.get('tardis_short_walls', [])
        if tardis_long:
            top = tardis_long[0]
            px_w, val = top[0], top[1]
            lines.append(f'  多头清算墙: ${px_w:,.0f}  (${val/1e6:.1f}M)')
        elif liq.get('liq_long_5pct'):
            lines.append(f'  多头清算密集: ${liq["liq_long_5pct"]:,.0f} (~5%下方)')
        if tardis_short:
            top = tardis_short[0]
            px_w, val = top[0], top[1]
            lines.append(f'  空头清算墙: ${px_w:,.0f}  (${val/1e6:.1f}M)')
        elif liq.get('liq_short_5pct'):
            lines.append(f'  空头清算密集: ${liq["liq_short_5pct"]:,.0f} (~5%上方)')
        bias = liq.get('tardis_bias') or liq.get('liq_bias','')
        if bias:
            lines.append(f'  方向偏向: {bias}')
        long_pct = liq.get('long_pct', 0)
        if long_pct:
            lines.append(f'  多空比: 多{long_pct:.0f}% / 空{liq.get("short_pct",0):.0f}%')

    # GEX
    if gex and not gex.get('error') and gex.get('max_strike'):
        direction = '🔴负伽马(放大)' if gex.get('direction') == 'NEGATIVE' else '🟢正伽马(压制)'
        lines.append(f'⚡ GEX伽马  {direction}')
        lines.append(f'  MAX: ${gex["max_strike"]:,.0f} (+{gex["dist_max_pct"]:.1f}%)')
        lines.append(f'  ZeroFlip: ${gex["zero_flip"]:,.0f}')
        lines.append(f'  MIN: ${gex["min_strike"]:,.0f} (-{gex["dist_min_pct"]:.1f}%)')

    # FVG
    bear_fvg = fvg.get('bear_fvg', {})
    bull_fvg = fvg.get('bull_fvg', {})
    if bear_fvg or bull_fvg:
        lines.append('🕳️  FVG公允缺口')
        if bear_fvg and bear_fvg.get('top'):
            gap = bear_fvg.get('gap_pct', 0)
            dist = (bear_fvg['bottom'] - px) / px * 100
            lines.append(f'  空头FVG: {pf(bear_fvg.get("bottom"))}~{pf(bear_fvg.get("top"))} ({gap:.2f}%, {dist:+.1f}%)')
        if bull_fvg and bull_fvg.get('top'):
            gap = bull_fvg.get('gap_pct', 0)
            dist = (px - bull_fvg['top']) / px * 100
            lines.append(f'  多头FVG: {pf(bull_fvg.get("bottom"))}~{pf(bull_fvg.get("top"))} ({gap:.2f}%, {dist:+.1f}%)')

    return '\n'.join(lines)


# ════════════════════════════════════════════════════════════════
# 自推送：直接推送到 Jarvis，绕开AI渲染层
# ════════════════════════════════════════════════════════════════

JARVIS_TARGET  = os.environ.get('JARVIS_TARGET', 'YOUR_USER_ID:thread:YOUR_THREAD_ID')
JARVIS_CHANNEL = 'jarvis'


def _push(message: str):
    """openclaw message send 直接推送，保留换行格式"""
    import subprocess
    subprocess.run(
        ['openclaw', 'message', 'send',
         '--channel', JARVIS_CHANNEL,
         '--target',  JARVIS_TARGET,
         '--message', message],
        capture_output=True, timeout=15
    )


# ════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════

def main():
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description='四维市场结构扫描器')
    parser.add_argument('--symbol',  default='BTCUSDT', help='交易对（默认BTCUSDT）')
    parser.add_argument('--json',    action='store_true', help='输出原始JSON')
    parser.add_argument('--both',    action='store_true', help='同时扫描BTC+ETH')
    parser.add_argument('--push',    action='store_true', help='自推送到Jarvis（绕开AI渲染）')
    args = parser.parse_args()

    symbols = ['BTCUSDT', 'ETHUSDT'] if args.both else [args.symbol]

    # 扫描所有标的
    reports = []
    for sym in symbols:
        result = scan_structure(sym)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        else:
            reports.append(format_report(result))

    if args.json:
        return

    # 拼接完整播报内容
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    sep = '━' * 19
    body = f'\n\n'.join(reports)
    full_msg = f'📊 市场结构 {now_utc}\n{sep}\n{body}\n{sep}'

    if args.push:
        # 自推送模式：直接发到Jarvis，保留换行
        _push(full_msg)
        print(f'[market-structure] 推送完成 → {JARVIS_TARGET}')
        print(full_msg)  # 同时输出到log
    else:
        # 直接输出（供手动调用）
        print(full_msg)


if __name__ == '__main__':
    main()
