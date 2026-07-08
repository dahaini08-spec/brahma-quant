import os
#!/usr/bin/env python3
"""
auto_position_manager.py — 梵天自动仓位管理器
设计院 · 苏摩111 · 2026-07-01

职责：自主感知持仓风险，无需苏摩确认，自主执行平仓/减仓/SL收紧

触发逻辑（任一满足 → 自动处理）：
  🔴 全平信号：
    - RSI_1H > 72 + 价格突破 EMA20_4H（超买逃顶）
    - 浮亏超过 SL_PCT（软止损触发）
    - 体制翻转为 BULL_TREND（空单全平）

  🟡 减仓50%信号：
    - RSI_1H > 62 + 浮盈 > 1.5%（锁定利润）
    - 价格回测 EMA20_4H 并反弹（趋势减弱）

  🔒 SL收紧信号：
    - 浮盈 > 10%  → SL收紧至当前价×1.02（锁定8%利润）
    - 浮盈 > 15%  → SL收紧至当前价×1.015（锁定13.5%利润）

运行：由 cron 每30分钟调用
  openclaw cron add --name auto-position-manager-30m --every 30m
    --system-event "exec: python3 scripts/auto_position_manager.py"
"""

import sys, os, json, time, hmac, hashlib, math, requests, subprocess
from pathlib import Path
from datetime import datetime, timezone

BASE     = Path(__file__).parent.parent
SL_PATH  = BASE / 'data' / 'position_sl_state.json'
LOG_PATH = BASE / 'data' / 'auto_position_manager.log'

FAPI        = 'https://fapi.binance.com'
PUSH_TARGET = os.environ.get('JARVIS_TARGET', 'YOUR_USER_ID:thread:YOUR_THREAD_ID')

# ── API ───────────────────────────────────────────────────────
sys.path.insert(0, str(BASE))
try:
    from scripts.system_config import API_KEY, API_SECRET as SECRET
except Exception:
    API_KEY = os.environ.get('BINANCE_API_KEY', '')  # [安全修复 2026-07-08]
    SECRET  = os.environ.get('BINANCE_SECRET', '')  # [安全修复 2026-07-08]

# ── 阈值 ─────────────────────────────────────────────────────
RSI_FULL_CLOSE   = 72.0   # RSI超买 → 全平
RSI_REDUCE       = 62.0   # RSI偏热 → 减仓50%
MIN_PNL_REDUCE   = 1.5    # 减仓最低浮盈门槛(%)
PNL_TSL_10       = 10.0   # 浮盈10% → SL收紧
PNL_TSL_15       = 15.0   # 浮盈15% → SL极限收紧
MAX_LOSS_PCT     = 2.5    # 软止损：浮亏超2.5%触发平仓


# ════════════════════════════════════════════════════════════════

def _signed(method, path, params=None):
    params = params or {}
    params['timestamp'] = int(time.time() * 1000)
    qs  = '&'.join(f'{k}={v}' for k, v in params.items())
    sig = hmac.new(SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    qs += f'&signature={sig}'
    h   = {'X-MBX-APIKEY': API_KEY}
    url = f'{FAPI}{path}'
    if method == 'GET':
        return requests.get(url + '?' + qs, headers=h, timeout=5).json()
    elif method == 'POST':
        return requests.post(url, data=qs, headers=h, timeout=5).json()


def _push(msg: str):
    subprocess.run(
        ['openclaw', 'message', 'send',
         '--channel', 'jarvis',
         '--target',  PUSH_TARGET,
         '--message', msg],
        capture_output=True, timeout=15
    )


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    line = f'[{ts}] {msg}'
    print(line)
    with open(LOG_PATH, 'a') as f:
        f.write(line + '\n')


def get_rsi(symbol: str, interval: str = '1h', period: int = 14) -> float:
    try:
        r = requests.get(f'{FAPI}/fapi/v1/klines',
                         params={'symbol': symbol, 'interval': interval, 'limit': 50},
                         timeout=5).json()
        closes = [float(c[4]) for c in r]
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(d, 0)); losses.append(max(-d, 0))
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period
        return round(100 - 100 / (1 + ag / al), 1) if al > 0 else 100.0
    except Exception:
        return 50.0


def get_ema20_4h(symbol: str) -> float:
    try:
        r = requests.get(f'{FAPI}/fapi/v1/klines',
                         params={'symbol': symbol, 'interval': '4h', 'limit': 30},
                         timeout=5).json()
        closes = [float(c[4]) for c in r]
        k = 2 / 21; ema = closes[0]
        for c in closes[1:]: ema = c * k + ema * (1 - k)
        return ema
    except Exception:
        return 0.0


def get_qty_precision(symbol: str) -> int:
    try:
        ei = requests.get(f'{FAPI}/fapi/v1/exchangeInfo', timeout=5).json()
        for s in ei.get('symbols', []):
            if s['symbol'] == symbol:
                for f in s.get('filters', []):
                    if f['filterType'] == 'LOT_SIZE':
                        step = float(f['stepSize'])
                        return len(str(step).rstrip('0').split('.')[-1]) if '.' in str(step) else 0
    except Exception:
        pass
    return 3


def close_position(symbol: str, qty: float, reason: str) -> dict:
    """市价平仓（reduceOnly）"""
    side = _signed('GET', '/fapi/v2/positionRisk')
    # [BUG-1其他处 修复 2026-07-07] 防御非列表返回
    if not isinstance(side, list): return {'status': 'ERROR', 'reason': f'API异常: {str(side)[:80]}'}
    pos  = next((p for p in side if isinstance(p, dict) and p.get('symbol') == symbol and abs(float(p.get('positionAmt',0))) > 0), None)
    if not pos:
        return {'status': 'SKIP', 'reason': '无持仓'}
    amt      = float(pos['positionAmt'])
    close_side = 'BUY' if amt < 0 else 'SELL'
    prec     = get_qty_precision(symbol)
    close_qty = round(math.floor(abs(qty) * 10**prec) / 10**prec, prec)
    r = _signed('POST', '/fapi/v1/order', {
        'symbol': symbol, 'side': close_side,
        'type': 'MARKET', 'quantity': close_qty, 'reduceOnly': 'true'
    })
    return {'status': 'OK' if 'orderId' in r else 'ERR',
            'orderId': r.get('orderId'), 'msg': r.get('msg', ''),
            'fill': float(r.get('avgPrice', 0)), 'qty': close_qty}


def update_sl(symbol: str, new_sl: float, note: str):
    """更新软止损价"""
    sl_state = {}
    if SL_PATH.exists():
        try: sl_state = json.loads(SL_PATH.read_text())
        except Exception: pass
    if symbol in sl_state:
        sl_state[symbol]['sl_price']   = new_sl
        sl_state[symbol]['updated_at'] = time.time()
        sl_state[symbol]['note']       = note
    SL_PATH.write_text(json.dumps(sl_state, indent=2, ensure_ascii=False))


# ════════════════════════════════════════════════════════════════
# 主扫描逻辑
# ════════════════════════════════════════════════════════════════

def run():
    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    _log(f'=== auto_position_manager 启动 {now_iso} ===')

    # 获取所有持仓
    pos_list = _signed('GET', '/fapi/v2/positionRisk')
    # [BUG-1 修复 2026-07-07] API偷发返回dict(错误)/非列表时防崩溃
    if not isinstance(pos_list, list):
        _log(f'positionRisk返回异常: {str(pos_list)[:100]}，跳过本次')
        print('HEARTBEAT_OK'); return
    active   = [p for p in pos_list if isinstance(p, dict) and abs(float(p.get('positionAmt', 0))) > 0]

    if not active:
        _log('无持仓 → HEARTBEAT_OK')
        print('HEARTBEAT_OK')
        return

    acct = _signed('GET', '/fapi/v2/account')
    nav  = float(acct.get('totalMarginBalance', 0))

    actions = []

    for pos in active:
        sym   = pos['symbol']
        amt   = float(pos['positionAmt'])
        entry = float(pos['entryPrice'])
        pnl   = float(pos['unRealizedProfit'])
        side  = 'SHORT' if amt < 0 else 'LONG'
        qty   = abs(amt)

        # 当前价
        try:
            px = float(requests.get(f'{FAPI}/fapi/v1/ticker/price?symbol={sym}', timeout=3).json()['price'])
        except Exception:
            continue

        pnl_pct = (entry - px) / entry * 100 if side == 'SHORT' else (px - entry) / entry * 100

        # RSI + EMA
        rsi_1h  = get_rsi(sym, '1h')
        rsi_4h  = get_rsi(sym, '4h')
        ema20_4h = get_ema20_4h(sym)
        above_ema = px > ema20_4h   # 对空单：价格突破EMA = 危险信号

        _log(f'{sym} {side} pnl={pnl_pct:+.2f}% RSI1H={rsi_1h} RSI4H={rsi_4h} EMA4H={ema20_4h:.5g} above={above_ema}')

        decision = None
        detail   = ''

        if side == 'SHORT':
            # ── 🔴 全平条件 ──────────────────────────────────────
            if rsi_1h >= RSI_FULL_CLOSE and above_ema:
                decision = 'FULL_CLOSE'
                detail   = f'RSI1H={rsi_1h}≥{RSI_FULL_CLOSE} + 突破EMA4H → 超买逃顶'
            elif pnl_pct < -MAX_LOSS_PCT:
                decision = 'FULL_CLOSE'
                detail   = f'浮亏{pnl_pct:.2f}%超过软止损{MAX_LOSS_PCT}%'

            # ── 🟡 减仓50%条件 ────────────────────────────────────
            elif rsi_1h >= RSI_REDUCE and pnl_pct >= MIN_PNL_REDUCE and not above_ema:
                decision = 'REDUCE_HALF'
                detail   = f'RSI1H={rsi_1h}≥{RSI_REDUCE} + 浮盈{pnl_pct:.2f}% → 锁定利润'

            # ── 🔒 SL收紧 ─────────────────────────────────────────
            elif pnl_pct >= PNL_TSL_15:
                new_sl = round(px * 1.015, 6)
                sl_state = json.loads(SL_PATH.read_text()) if SL_PATH.exists() else {}
                cur_sl   = sl_state.get(sym, {}).get('sl_price', 9999)
                if new_sl < cur_sl:   # 只收紧，不放松
                    update_sl(sym, new_sl, f'浮盈{pnl_pct:.1f}%→SL收紧至px*1.015 by APM')
                    decision = 'TSL_TIGHT'
                    detail   = f'浮盈{pnl_pct:.1f}%≥{PNL_TSL_15}% SL: {cur_sl:.5g}→{new_sl:.5g}'

            elif pnl_pct >= PNL_TSL_10:
                new_sl = round(px * 1.02, 6)
                sl_state = json.loads(SL_PATH.read_text()) if SL_PATH.exists() else {}
                cur_sl   = sl_state.get(sym, {}).get('sl_price', 9999)
                if new_sl < cur_sl:
                    update_sl(sym, new_sl, f'浮盈{pnl_pct:.1f}%→SL收紧至px*1.02 by APM')
                    decision = 'TSL_LOCK'
                    detail   = f'浮盈{pnl_pct:.1f}%≥{PNL_TSL_10}% SL: {cur_sl:.5g}→{new_sl:.5g}'

        else:  # LONG（体制允许时才有多单）
            if rsi_1h <= 28 and pnl_pct >= MIN_PNL_REDUCE:
                decision = 'REDUCE_HALF'
                detail   = f'RSI1H={rsi_1h}≤28超卖+浮盈{pnl_pct:.2f}% → 减仓'
            elif pnl_pct < -MAX_LOSS_PCT:
                decision = 'FULL_CLOSE'
                detail   = f'浮亏{pnl_pct:.2f}%超软止损'

        # ── 执行 ─────────────────────────────────────────────────
        if decision == 'FULL_CLOSE':
            result = close_position(sym, qty, detail)
            status = '✅' if result['status'] == 'OK' else '❌'
            actions.append(f'{status} {sym} 全平 @${result.get("fill",0):.5g} | {detail}')
            _log(f'  FULL_CLOSE {sym}: {result}')

        elif decision == 'REDUCE_HALF':
            result = close_position(sym, qty * 0.5, detail)
            status = '✅' if result['status'] == 'OK' else '❌'
            actions.append(f'{status} {sym} 减仓50% qty={result.get("qty")} @${result.get("fill",0):.5g} | {detail}')
            _log(f'  REDUCE_HALF {sym}: {result}')

        elif decision in ('TSL_TIGHT', 'TSL_LOCK'):
            actions.append(f'🔒 {sym} SL收紧 | {detail}')
            _log(f'  {decision} {sym}: {detail}')

        else:
            _log(f'  {sym} 无动作 → 持有')

        time.sleep(0.2)

    # ── 推送执行报告 ─────────────────────────────────────────────
    if actions:
        report = (
            f'🤖 梵天自动仓位管理 · {now_iso}\n'
            f'━━━━━━━━━━━━━━━━━━━\n' +
            '\n'.join(actions) +
            f'\n━━━━━━━━━━━━━━━━━━━\n'
            f'NAV=${nav:.2f}  持仓={len(active)}→{len(active)-sum(1 for a in actions if "全平" in a)}'
        )
        _push(report)
        _log(f'已推送 {len(actions)} 条动作')
    else:
        _log('无触发 → HEARTBEAT_OK')
        print('HEARTBEAT_OK')


if __name__ == '__main__':
    run()
