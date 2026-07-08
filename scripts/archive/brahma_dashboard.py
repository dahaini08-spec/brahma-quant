#!/usr/bin/env python3
"""
brahma_dashboard.py — 梵天系统面板 v2.0
设计院 2026-06-23 · 三模块重构

模块一：梵天信号（体制 + 市场状态）
模块二：武曲账户持仓（实盘持仓 + 浮盈）
模块三：武曲策略历史（wuqu_paper_settled 胜率，非 live_signal_log）
"""
import json, time, os, sys, hmac, hashlib, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

# ── Binance API ──────────────────────────────
_API_KEY = os.environ.get('BINANCE_API_KEY', '')
_SECRET  = os.environ.get('BINANCE_SECRET',  '')

def _fetch_pub(url, timeout=6):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except:
        return {}

def _fetch_signed(path, params=None, timeout=8):
    params = params or {}
    params['timestamp'] = int(time.time() * 1000)
    qs = urllib.parse.urlencode(params)
    sig = hmac.new(_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f'https://fapi.binance.com{path}?{qs}&signature={sig}'
    req = urllib.request.Request(url, headers={'X-MBX-APIKEY': _API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except:
        return None

def _load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except:
        return default or {}

def _load_jsonl(path):
    try:
        lines = Path(path).read_text().strip().split('\n')
        return [json.loads(l) for l in lines if l.strip()]
    except:
        return []

# ══════════════════════════════════════════════
# 模块一：梵天信号数据
# ══════════════════════════════════════════════
def collect_brahma():
    d = {}
    # 市场行情
    btc = _fetch_pub('https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=BTCUSDT')
    eth = _fetch_pub('https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=ETHUSDT')
    d['btc_price'] = float(btc.get('lastPrice', 0))
    d['btc_pct']   = float(btc.get('priceChangePercent', 0))
    d['eth_price'] = float(eth.get('lastPrice', 0))
    d['eth_pct']   = float(eth.get('priceChangePercent', 0))

    # 体制
    bs = _load_json(DATA / 'brahma_state.json')
    dr = _load_json(DATA / 'dharma_runtime.json')
    d['regime']      = bs.get('regime', 'UNKNOWN')
    d['sys_version'] = dr.get('system_version', 'v7.0')
    last_upd = bs.get('last_update') or bs.get('last_updated', '')
    if last_upd:
        try:
            from datetime import timezone
            ts = datetime.fromisoformat(last_upd.replace('Z', '+00:00'))
            d['state_age'] = (datetime.now(timezone.utc) - ts).total_seconds() / 60
        except:
            d['state_age'] = 0
    else:
        d['state_age'] = 0

    # 健康 / 熔断
    d['health']   = bs.get('health', 'UNKNOWN')
    cb = bs.get('circuit_breaker', {})
    d['circuit']  = cb.get('active', False)
    d['circuit_reason'] = cb.get('reason', '')

    return d

# ══════════════════════════════════════════════
# 模块二：武曲账户持仓
# ══════════════════════════════════════════════
def collect_positions():
    d = {}
    # 账户权益（signed）
    acc = _fetch_signed('/fapi/v2/account')
    if acc:
        d['nav']       = float(acc.get('totalMarginBalance', 0))
        d['wallet']    = float(acc.get('totalWalletBalance', 0))
        d['upnl']      = float(acc.get('totalUnrealizedProfit', 0))
        d['avail']     = float(acc.get('availableBalance', 0))
        d['margin_used'] = float(acc.get('totalInitialMargin', 0))
        d['margin_pct']  = d['margin_used'] / d['nav'] * 100 if d['nav'] > 0 else 0
    else:
        d['nav'] = d['wallet'] = d['upnl'] = d['avail'] = d['margin_used'] = d['margin_pct'] = 0

    # 实盘持仓（signed）
    pos_data = _fetch_signed('/fapi/v2/positionRisk')
    sl_state = _load_json(DATA / 'position_sl_state.json')

    positions = []
    if isinstance(pos_data, list):
        for p in pos_data:
            amt = float(p.get('positionAmt', 0))
            if amt == 0:
                continue
            sym  = p['symbol']
            ep   = float(p['entryPrice'])
            mp   = float(p['markPrice'])
            upnl = float(p['unRealizedProfit'])
            side = 'LONG' if amt > 0 else 'SHORT'
            pct  = (mp - ep) / ep * 100 if ep > 0 else 0
            if side == 'SHORT':
                pct = -pct

            # 止损/止盈来自 sl_state
            sl_info = sl_state.get(sym, {})
            sl = sl_info.get('sl_price', None)
            tp = sl_info.get('tp_price', None)

            # SL距离
            if sl and mp > 0:
                sl_dist = abs(mp - sl) / mp * 100
            else:
                sl_dist = None

            positions.append({
                'symbol': sym,
                'side':   side,
                'amt':    amt,
                'entry':  ep,
                'mark':   mp,
                'upnl':   upnl,
                'pct':    pct,
                'sl':     sl,
                'tp':     tp,
                'sl_dist': sl_dist,
            })

    # 按 upnl 排序：盈利大的在前
    positions.sort(key=lambda x: -x['upnl'])
    d['positions'] = positions
    return d

# ══════════════════════════════════════════════
# 模块三：武曲策略历史（wuqu_paper_settled）
# ══════════════════════════════════════════════
def collect_wuqu_history():
    d = {}
    settled = _load_jsonl(DATA / 'wuqu_paper_settled.jsonl')

    # 只统计有效结果（排除无 outcome 的）
    wins     = [s for s in settled if s.get('outcome') in ('TP1', 'TP2')]
    losses   = [s for s in settled if s.get('outcome') == 'SL']
    timeouts = [s for s in settled if s.get('outcome') == 'TIMEOUT']
    total    = len(settled)
    decided  = len(wins) + len(losses)

    d['total']    = total
    d['wins']     = len(wins)
    d['losses']   = len(losses)
    d['timeouts'] = len(timeouts)
    d['wr']       = len(wins) / decided if decided > 0 else 0
    d['tp1_count'] = sum(1 for s in wins if s.get('outcome') == 'TP1')
    d['tp2_count'] = sum(1 for s in wins if s.get('outcome') == 'TP2')
    d['timeout_pct'] = len(timeouts) / total * 100 if total > 0 else 0

    # 体制胜率矩阵
    regime_map = {}
    for s in settled:
        if s.get('outcome') in ('TP1', 'TP2', 'SL'):
            r = s.get('regime', 'UNKNOWN')
            regime_map.setdefault(r, {'win': 0, 'loss': 0})
            if s.get('outcome') in ('TP1', 'TP2'):
                regime_map[r]['win'] += 1
            else:
                regime_map[r]['loss'] += 1
    d['regime_map'] = regime_map

    # 近30天
    cutoff = time.time() - 30 * 86400
    recent = []
    for s in settled:
        ts_raw = s.get('close_ts') or s.get('open_ts')
        if ts_raw:
            try:
                from datetime import timezone
                ts = datetime.fromisoformat(str(ts_raw).replace('Z', '+00:00'))
                if ts.timestamp() >= cutoff:
                    recent.append(s)
            except:
                pass
    r_wins   = sum(1 for s in recent if s.get('outcome') in ('TP1', 'TP2'))
    r_losses = sum(1 for s in recent if s.get('outcome') == 'SL')
    r_dec    = r_wins + r_losses
    d['wr_30d']  = r_wins / r_dec if r_dec > 0 else 0
    d['cnt_30d'] = len(recent)

    return d

# ══════════════════════════════════════════════
# 渲染
# ══════════════════════════════════════════════
REGIME_CN = {
    'BULL_TREND': '牛市趋势', 'BULL_EARLY': '牛市初期',
    'BULL_CORRECTION': '牛市回调', 'BEAR_TREND': '熊市趋势',
    'BEAR_EARLY': '熊市初期', 'BEAR_RECOVERY': '熊市反弹',
    'CHOP_MID': '弱震荡', 'CHOP_HIGH': '强震荡', 'CHOP_LOW': '低波震荡',
}
REGIME_EMOJI = {
    'BULL_TREND': '🟢', 'BULL_EARLY': '🟢', 'BULL_CORRECTION': '🟡',
    'BEAR_TREND': '🔴', 'BEAR_EARLY': '🔴', 'BEAR_RECOVERY': '🟠',
    'CHOP_MID': '⚪', 'CHOP_HIGH': '⚪', 'CHOP_LOW': '⚪',
}

def wr_tag(wr):
    if wr >= 0.80: return '🏆 神级'
    if wr >= 0.70: return '🏆 极强'
    if wr >= 0.65: return '✅ 强'
    if wr >= 0.55: return '🟡 中'
    if wr >= 0.45: return '⚠️ 弱'
    return '❌ 差'

def render_jarvis(brahma: dict, pos: dict, wuqu: dict) -> str:
    now       = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    regime_cn = REGIME_CN.get(brahma['regime'], brahma['regime'])
    regime_em = REGIME_EMOJI.get(brahma['regime'], '⚪')
    circuit   = '🔴 熔断中' if brahma['circuit'] else '✅ 正常'

    lines = [
        f'📊 梵天 · 武曲系统面板 · {now}',
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━',
        f'',
        f'【梵天信号 · 市场体制】',
        f'BTC  ${brahma["btc_price"]:>10,.2f}  {brahma["btc_pct"]:>+.2f}%',
        f'ETH  ${brahma["eth_price"]:>10,.2f}  {brahma["eth_pct"]:>+.2f}%',
        f'体制  {regime_em} {brahma["regime"]} ({regime_cn})',
        f'系统  {circuit}  状态刷新 {brahma["state_age"]:.0f}min前',
        f'',
    ]

    # ── 模块二：武曲持仓 ──
    lines += [
        f'【武曲账户 · 实盘持仓】',
        f'权益  ${pos["nav"]:>8.2f}  浮盈 {pos["upnl"]:>+.2f} USDT',
        f'保证金 ${pos["margin_used"]:>7.2f}  占比 {pos["margin_pct"]:.1f}%  可用 ${pos["avail"]:>8.2f}',
    ]

    if pos['positions']:
        lines.append(f'')
        for p in pos['positions']:
            side_em = '🟢' if p['side'] == 'LONG' else '🔴'
            sl_str  = f"SL={p['sl']:.5g}({p['sl_dist']:.1f}%距)" if p['sl'] and p['sl_dist'] is not None else 'SL=--'
            tp_str  = f"TP={p['tp']:.5g}" if p['tp'] else ''
            lines.append(
                f"  {side_em}{p['symbol']:<14} {p['side']:<6} "
                f"EP={p['entry']:.5g} → {p['mark']:.5g} "
                f"{p['pct']:>+.2f}%  uPnL={p['upnl']:>+.2f}"
            )
            if sl_str or tp_str:
                lines.append(f"    {sl_str}  {tp_str}")
    else:
        lines.append('  暂无持仓')

    lines.append('')

    # ── 模块三：武曲策略历史 ──
    decided = wuqu['wins'] + wuqu['losses']
    lines += [
        f'【武曲策略 · 历史战绩】',
        f'已结算  {decided:>4} 条  WIN={wuqu["wins"]}(TP1={wuqu["tp1_count"]} TP2={wuqu["tp2_count"]}) LOSS={wuqu["losses"]}',
        f'胜率    {wuqu["wr"]:>6.1%}  {wr_tag(wuqu["wr"])}',
        f'TIMEOUT {wuqu["timeouts"]:>4} 条  占比={wuqu["timeout_pct"]:.1f}%',
        f'近30天  {wuqu["cnt_30d"]:>4} 条  WR={wuqu["wr_30d"]:.1%}',
        f'',
        f'体制胜率矩阵：',
    ]
    for regime, stats in sorted(wuqu['regime_map'].items(), key=lambda x: -(x[1]['win']+x[1]['loss'])):
        n  = stats['win'] + stats['loss']
        wr = stats['win'] / n if n > 0 else 0
        cn = REGIME_CN.get(regime, regime)
        em = REGIME_EMOJI.get(regime, '⚪')
        lines.append(f'  {em} {cn:<10}  W={stats["win"]:>3} L={stats["loss"]:>3}  WR={wr:.1%}  n={n}')

    lines += [
        f'',
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━',
        f'系统版本 {brahma["sys_version"]}  |  梵天达摩院 · 武曲执行层',
    ]
    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--format', choices=['jarvis', 'terminal'], default='terminal')
    ap.add_argument('--push', action='store_true', help='推送到Jarvis')
    args = ap.parse_args()

    print('[brahma_dashboard v2] 收集数据中...', file=sys.stderr)
    brahma = collect_brahma()
    pos    = collect_positions()
    wuqu   = collect_wuqu_history()

    output = render_jarvis(brahma, pos, wuqu)
    print(output)

    if args.push:
        import subprocess
        subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', 'jarvis',
             '--to', 'YOUR_USER_ID:thread:YOUR_THREAD_ID',
             '--message', output],
            capture_output=True
        )
        print('[brahma_dashboard] ✅ 已推送到Jarvis', file=sys.stderr)
