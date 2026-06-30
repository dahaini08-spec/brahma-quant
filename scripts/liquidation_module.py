#!/usr/bin/env python3
"""清算数据模块 — OI/FR/LSR/ATR/清算层估算"""
import json, urllib.request, ssl, sys
from pathlib import Path

BASE = Path(__file__).parent.parent
CTX  = ssl.create_default_context()

def _fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'brahma/1.0'})
    return json.loads(urllib.request.urlopen(req, context=CTX, timeout=5).read())

def get_liq_data(sym: str) -> dict:
    """获取单品种清算相关数据"""
    out = {'symbol': sym}
    try:
        # OI（合约张数）
        oi = _fetch(f'https://fapi.binance.com/fapi/v1/openInterest?symbol={sym}')
        out['oi'] = float(oi['openInterest'])
    except: out['oi'] = None

    try:
        # OI变化（本地状态追踪，免去403限制接口）
        import time as _time
        oi_state_path = BASE / 'data' / 'oi_state.json'
        oi_state = {}
        if oi_state_path.exists():
            try: oi_state = json.loads(oi_state_path.read_text())
            except: pass
        sym_state = oi_state.get(sym, {})
        oi_now = out.get('oi')
        now_ts = _time.time()
        if oi_now:
            oi_1h_val  = sym_state.get('oi_1h')
            oi_24h_val = sym_state.get('oi_24h')
            ts_1h  = sym_state.get('ts_1h', 0)
            ts_24h = sym_state.get('ts_24h', 0)
            # 计算变化
            if oi_1h_val and (now_ts - ts_1h) < 7200:
                out['oi_1h_chg']  = (oi_now - oi_1h_val) / oi_1h_val * 100
            if oi_24h_val and (now_ts - ts_24h) < 90000:
                out['oi_24h_chg'] = (oi_now - oi_24h_val) / oi_24h_val * 100
            # 更新状态
            if now_ts - ts_1h > 3600:
                sym_state['oi_1h'] = oi_now; sym_state['ts_1h'] = now_ts
            if now_ts - ts_24h > 86400:
                sym_state['oi_24h'] = oi_now; sym_state['ts_24h'] = now_ts
            if not sym_state.get('oi_1h'):
                sym_state['oi_1h'] = oi_now; sym_state['ts_1h'] = now_ts
            if not sym_state.get('oi_24h'):
                sym_state['oi_24h'] = oi_now; sym_state['ts_24h'] = now_ts
            oi_state[sym] = sym_state
            try: oi_state_path.write_text(json.dumps(oi_state))
            except: pass
    except: pass

    try:
        # 资金费率
        fr = _fetch(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&limit=1')
        out['fr'] = float(fr[0]['fundingRate']) * 100
    except: out['fr'] = None

    try:
        # 多空比
        lsr = _fetch(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={sym}&period=1h&limit=1')
        out['lsr'] = float(lsr[0]['longShortRatio'])
        out['long_pct']  = float(lsr[0]['longAccount'])  * 100
        out['short_pct'] = float(lsr[0]['shortAccount']) * 100
    except: out['lsr'] = None

    try:
        # ATR 1H（用最近24根1H K线）
        klines = _fetch(f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1h&limit=24')
        trs = [max(float(k[2])-float(k[3]), abs(float(k[2])-float(k[4])), abs(float(k[3])-float(k[4]))) for k in klines]
        out['atr_1h'] = sum(trs) / len(trs) if trs else None
        out['price']  = float(klines[-1][4])
    except: out['atr_1h'] = None

    # 清算层估算：当前价格 ±1.5×ATR（多空强平密集区）
    if out.get('atr_1h') and out.get('price'):
        out['liq_long']  = round(out['price'] - 1.5 * out['atr_1h'], 4)  # 多单强平（价格下行）
        out['liq_short'] = round(out['price'] + 1.5 * out['atr_1h'], 4)  # 空单强平（价格上行）
        out['liq_long_pct']  = (out['liq_long']  - out['price']) / out['price'] * 100
        out['liq_short_pct'] = (out['liq_short'] - out['price']) / out['price'] * 100

    return out

def fmt_liq_block(syms=None, entry_zones=None) -> str:
    """格式化清算数据输出块（手机友好）"""
    if syms is None:
        syms = ['BTCUSDT', 'ETHUSDT']

    lines = ['⚡ 清算数据']
    for sym in syms:
        d = get_liq_data(sym)
        s = sym.replace('USDT', '')
        price = d.get('price', 0)

        # OI行
        oi = d.get('oi')
        c1h  = d.get('oi_1h_chg')
        c24h = d.get('oi_24h_chg')
        oi_s = f'{oi:,.0f}' if oi else '--'
        c1h_s  = f'{c1h:+.2f}%'  if c1h  is not None else '--'
        c24h_s = f'{c24h:+.2f}%' if c24h is not None else '--'
        lines.append(f'{s}  OI={oi_s}  1H={c1h_s}  24H={c24h_s}')

        # FR + LSR行
        fr  = d.get('fr')
        lsr = d.get('lsr')
        lp  = d.get('long_pct')
        sp  = d.get('short_pct')
        fr_s  = f'FR{fr:+.4f}%' if fr is not None else ''
        lsr_s = f'LSR={lsr:.2f}(多{lp:.0f}%空{sp:.0f}%)' if lsr else ''
        if fr_s or lsr_s:
            lines.append(f'   {fr_s}  {lsr_s}'.rstrip())

        # ATR + 清算层
        atr = d.get('atr_1h')
        ll  = d.get('liq_long')
        ls  = d.get('liq_short')
        llp = d.get('liq_long_pct')
        lsp = d.get('liq_short_pct')
        if atr and ll and ls:
            import sys; sys.path.insert(0,str(Path(__file__).parent))
            from signal_dashboard import fmt_price
            atr_s = f'{atr:.2f}' if atr > 1 else f'{atr:.4f}'
            lines.append(f'   ATR={atr_s}  清算层 ↓${fmt_price(ll)}({llp:.1f}%)  ↑${fmt_price(ls)}({lsp:+.1f}%)')

        # 接近入场区预警
        if entry_zones and sym in entry_zones and price:
            ez = entry_zones[sym]
            gap = (ez - price) / price * 100
            if abs(gap) < 2.0:
                icon = '⚡' if abs(gap) < 0.5 else '⚠️'
                dir_s = '接近空单区' if gap > 0 else '已过入场区'
                lines.append(f'   {icon} {s}{dir_s}${fmt_price(ez)} gap{gap:+.2f}%')

        lines.append('')  # 品种间空行

    return '\n'.join(lines).rstrip()


if __name__ == '__main__':
    syms = sys.argv[1:] if len(sys.argv) > 1 else ['BTCUSDT', 'ETHUSDT']
    print(fmt_liq_block(syms))
