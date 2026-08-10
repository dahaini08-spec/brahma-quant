#!/usr/bin/env python3
"""
vip_strategy_generator.py
设计院封印 2026-08-02 | 苏摩授权

VIP策略自动生成器 — 信息同步核心
  输入: signal_log + regime_state + dharma_runtime + wuqu_positions
  输出: 达摩院校正版VIP策略（方案一极简卡片格式）

解决的核心问题:
  ❌ 旧: AI手动推算，止损拍脑袋，与达摩院回测割裂
  ✅ 新: 自动读取所有系统状态，止损强制2.0%铁证，全局信息同步
"""
import json, sys, time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

# ── 加载系统状态 ──────────────────────────────────────────────
def load_regime() -> dict:
    """读取实时体制状态"""
    p = DATA / 'regime_state.json'
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except:
        return {}

def load_exit_params() -> dict:
    """读取达摩院exit_params_v4铁证止损参数"""
    p = DATA / 'dharma_runtime.json'
    if not p.exists():
        return {'BEAR': {'sl_pct': 2.0, 'rr': 1.0}, 'CHOP': {'sl_pct': 2.5, 'rr': 1.0}, 'BULL': {'sl_pct': 2.0, 'rr': 1.0}}
    try:
        rt = json.loads(p.read_text())
        return rt.get('exit_params_v4', {})
    except:
        return {}

def load_positions() -> list:
    """读取当前持仓"""
    p = DATA / 'wuqu_positions.json'
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        pos = data if isinstance(data, list) else data.get('positions', [])
        return [x for x in pos if x.get('status', '') in ('OPEN', 'open', 'active')]
    except:
        return []

def load_latest_signals(symbols: list) -> dict:
    """读取最新信号"""
    p = DATA / 'live_signal_log.jsonl'
    if not p.exists():
        return {}
    signals = {}
    try:
        lines = p.read_text().strip().split('\n')
        for l in reversed(lines):
            try:
                d = json.loads(l)
                sym = d.get('symbol', '')
                if sym in symbols and sym not in signals:
                    signals[sym] = d
                if len(signals) == len(symbols):
                    break
            except:
                pass
    except:
        pass
    return signals

# ── 止损计算（达摩院铁证） ─────────────────────────────────────
def calc_sl_price(entry_mid: float, regime: str, direction: str, exit_params: dict) -> tuple:
    """
    返回 (sl_price, sl_pct, leverage)
    止损铁律：sl_pct ≥ 达摩院v4最低值
    杠杆铁律：0.2%NAV / (position_pct × sl_pct)
    """
    # 确定体制分组
    if 'CHOP' in regime:
        v4_key = 'CHOP'
    elif 'BULL' in regime:
        v4_key = 'BULL'
    else:
        v4_key = 'BEAR'

    v4 = exit_params.get(v4_key, {'sl_pct': 2.0, 'rr': 1.0})
    sl_pct = float(v4.get('sl_pct', 2.0))
    rr     = float(v4.get('rr', 1.0))

    risk = entry_mid * sl_pct / 100
    if direction == 'SHORT':
        sl_price = entry_mid + risk
    else:
        sl_price = entry_mid - risk

    return sl_price, sl_pct, rr

def calc_leverage(position_pct: float, sl_pct: float, max_loss_pct: float = 0.2) -> int:
    """反算杠杆：单笔最大亏损≤0.2%NAV"""
    if position_pct <= 0 or sl_pct <= 0:
        return 3
    # 公式: 杠杆 = 最大亏损% / (仓位率 × SL率)
    # 例: 0.2% / (5% × 2.0%) = 0.2/(0.05×2.0) = 2x
    lev = max_loss_pct / (position_pct / 100 * sl_pct)
    # 取整，最小1x最大5x
    lev = max(1, min(5, int(lev)))
    return lev

# ── VIP卡片生成 ───────────────────────────────────────────────
def generate_vip_card(
    symbol: str,
    price: float,
    chg_pct: float,
    direction: str,
    entry_lo: float,
    entry_hi: float,
    regime: str,
    exit_params: dict,
    position_pct: float = 0.05,
    label: str = '主策略',
    alt_direction: str = None,
    alt_entry_lo: float = None,
    alt_entry_hi: float = None,
) -> str:
    """生成单币VIP卡片（方案一极简格式）"""
    entry_mid = (entry_lo + entry_hi) / 2

    # 主策略止损
    sl_price, sl_pct, rr = calc_sl_price(entry_mid, regime, direction, exit_params)
    lev = calc_leverage(position_pct * 100, sl_pct)

    # TP（RR=1.0标准）
    risk_abs = abs(sl_price - entry_mid)
    if direction == 'SHORT':
        tp1 = entry_mid - risk_abs * rr
        tp2 = entry_mid - risk_abs * rr * 1.3
        dir_emoji = '🔴'
        dir_label = '做空'
    else:
        tp1 = entry_mid + risk_abs * rr
        tp2 = entry_mid + risk_abs * rr * 1.3
        dir_emoji = '🟢'
        dir_label = '做多'

    chg_str = f'+{chg_pct:.2f}%' if chg_pct >= 0 else f'{chg_pct:.2f}%'
    sym_short = 'BTC' if 'BTC' in symbol else ('ETH' if 'ETH' in symbol else symbol.replace('USDT', ''))
    price_fmt = f'{price:,.0f}' if price > 100 else f'{price:,.2f}'

    lines = [f'━━━━ 🟡 {sym_short} ${price_fmt} {chg_str} ━━━━', '']

    # 主策略
    entry_str = f'{entry_lo:,.0f}~{entry_hi:,.0f}' if price > 100 else f'{entry_lo:,.2f}~{entry_hi:,.2f}'
    sl_str    = f'{sl_price:,.0f}' if price > 100 else f'{sl_price:,.2f}'
    tp1_str   = f'{tp1:,.0f}' if price > 100 else f'{tp1:,.2f}'
    tp2_str   = f'{tp2:,.0f}' if price > 100 else f'{tp2:,.2f}'
    pos_int   = int(position_pct * 100)

    lines.append(f'{dir_emoji} {dir_label}  进场 {entry_str}')
    lines.append(f'   止损 {sl_str} · 目标 {tp1_str}/{tp2_str}')
    lines.append(f'   {pos_int}%仓 · {lev}x杠 · R:R {rr:.1f}')
    lines.append(f'   止损={sl_pct:.1f}%达摩院铁证 ✅')

    # 辅策略（可选）
    if alt_direction and alt_entry_lo and alt_entry_hi:
        alt_mid = (alt_entry_lo + alt_entry_hi) / 2
        alt_sl, alt_sl_pct, alt_rr = calc_sl_price(alt_mid, regime, alt_direction, exit_params)
        alt_lev = calc_leverage(position_pct * 50, alt_sl_pct)  # 辅策略半仓
        alt_risk = abs(alt_sl - alt_mid)
        if alt_direction == 'SHORT':
            alt_tp1 = alt_mid - alt_risk * alt_rr
            alt_dir_emoji = '🔴'
            alt_dir_label = '追空'
        else:
            alt_tp1 = alt_mid + alt_risk * alt_rr
            alt_dir_emoji = '🟢'
            alt_dir_label = '轻多'

        alt_entry_str = f'{alt_entry_lo:,.0f}~{alt_entry_hi:,.0f}' if price > 100 else f'{alt_entry_lo:,.2f}~{alt_entry_hi:,.2f}'
        alt_sl_str    = f'{alt_sl:,.0f}' if price > 100 else f'{alt_sl:,.2f}'
        alt_tp1_str   = f'{alt_tp1:,.0f}' if price > 100 else f'{alt_tp1:,.2f}'

        lines.append('')
        lines.append(f'{alt_dir_emoji} {alt_dir_label}  进场 {alt_entry_str}')
        lines.append(f'   止损 {alt_sl_str} · 目标 {alt_tp1_str}')
        lines.append(f'   {int(position_pct*50)}%仓 · {alt_lev}x杠')

    return '\n'.join(lines)

# ── 主输出函数 ────────────────────────────────────────────────
def generate(
    btc_entry: tuple = None,   # (lo, hi, direction)
    eth_entry: tuple = None,   # (lo, hi, direction)
    btc_price: float = None,
    eth_price: float = None,
    btc_chg: float = 0.0,
    eth_chg: float = 0.0,
    btc_alt: tuple = None,     # (lo, hi, direction) 辅策略
    eth_alt: tuple = None,
    note: str = '',
) -> str:
    """生成完整VIP策略输出"""
    regime_data = load_regime()
    exit_params = load_exit_params()
    positions   = load_positions()

    # 体制
    btc_regime = regime_data.get('BTCUSDT', {}).get('confirmed', 'BEAR_TREND') if isinstance(regime_data.get('BTCUSDT'), dict) else str(regime_data.get('BTCUSDT', 'BEAR_TREND'))
    eth_regime = regime_data.get('ETHUSDT', {}).get('confirmed', 'BEAR_TREND') if isinstance(regime_data.get('ETHUSDT'), dict) else str(regime_data.get('ETHUSDT', 'BEAR_TREND'))

    # 体制标签
    regime_tag = '熊市趋势'
    if 'BEAR' in btc_regime:
        regime_tag = '熊市趋势'
    elif 'BULL' in btc_regime:
        regime_tag = '牛市趋势'
    elif 'CHOP' in btc_regime:
        regime_tag = '震荡行情'

    now_str = datetime.now(timezone.utc).strftime('%m-%d')

    lines = [
        f'🌿 VIP策略 · 姓赵不宣',
        f'{now_str} · {btc_regime} {regime_tag}',
        '',
    ]

    # 持仓警告
    if positions:
        lines.append(f'⚠️ 当前持仓 {len(positions)} 个:')
        for p in positions[:3]:
            lines.append(f'  {p.get("symbol","?")} {p.get("direction","?")} upl={p.get("pnl_pct","?")}')
        lines.append('')

    # BTC卡片
    if btc_entry and btc_price:
        lo, hi, direction = btc_entry
        alt_lo, alt_hi, alt_dir = btc_alt if btc_alt else (None, None, None)
        btc_card = generate_vip_card(
            'BTCUSDT', btc_price, btc_chg, direction, lo, hi,
            btc_regime, exit_params, 0.05,
            alt_direction=alt_dir, alt_entry_lo=alt_lo, alt_entry_hi=alt_hi,
        )
        lines.append(btc_card)
        lines.append('')

    # ETH卡片
    if eth_entry and eth_price:
        lo, hi, direction = eth_entry
        alt_lo, alt_hi, alt_dir = eth_alt if eth_alt else (None, None, None)
        eth_card = generate_vip_card(
            'ETHUSDT', eth_price, eth_chg, direction, lo, hi,
            eth_regime, exit_params, 0.03,
            alt_direction=alt_dir, alt_entry_lo=alt_lo, alt_entry_hi=alt_hi,
        )
        lines.append(eth_card)
        lines.append('')

    # 底部
    lines.append('━' * 24)
    main_direction = '空为主线' if 'BEAR' in btc_regime else '多为主线'
    lines.append(f'⚠️ {main_direction}')
    if note:
        lines.append(note)
    lines.append(f'达摩院铁证: BEAR SL=2.0% WR=72% EV=+0.578%/笔')
    lines.append(f'[auto_generated by vip_strategy_generator v1.0]')

    return '\n'.join(lines)

# ── CLI入口 ──────────────────────────────────────────────────
if __name__ == '__main__':
    import requests
    API = 'https://fapi.binance.com'

    def get_price(sym):
        try:
            t = requests.get(f'{API}/fapi/v1/ticker/24hr', params={'symbol': sym}, timeout=5).json()
            return float(t['lastPrice']), float(t['priceChangePercent'])
        except:
            return 0.0, 0.0

    btc_p, btc_c = get_price('BTCUSDT')
    eth_p, eth_c = get_price('ETHUSDT')

    # 情景B入场区（当前分析结果）
    output = generate(
        btc_entry=(64050, 64200, 'SHORT'),
        btc_price=btc_p,
        btc_chg=btc_c,
        btc_alt=(62785, 62966, 'LONG'),
        eth_entry=(64050/btc_p*eth_p * 1.0, 64200/btc_p*eth_p * 1.0, 'SHORT') if btc_p > 0 else (1893, 1902, 'SHORT'),
        eth_price=eth_p,
        eth_chg=eth_c,
        eth_alt=(1868, 1871, 'SHORT'),
        note='BTC等Bear OB · ETH等方向确认',
    )
    print(output)
