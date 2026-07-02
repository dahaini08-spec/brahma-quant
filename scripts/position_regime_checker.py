import os
#!/usr/bin/env python3
"""
position_regime_checker.py · 梵天P1 · 持仓体制匹配器 v1.0
[设计院落地 · 2026-06-29 · 苏摩授权]

功能：
  检测所有当前持仓的方向 vs 市场体制是否匹配
  若发现「逆势持仓」→ 立即推送告警（无需苏摩询问）

触发逻辑：
  SHORT + BULL_TREND/BULL_EARLY   → 🔴 危险告警
  LONG  + BEAR_TREND/BEAR_EARLY   → 🔴 危险告警
  SHORT + BULL_CORRECTION         → 🟡 注意告警
  BTC RSI_1H 穿越 50 阈值         → 🔔 体制变化广播

执行路径：brahma-monitor-30m cron → 本脚本 → 条件推送
静默原则：无逆势持仓 → 完全静默（exit 0）
"""

import sys, os, json, time, subprocess, requests, hmac, hashlib, urllib.parse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brahma_brain'))

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, 'data', 'position_regime_state.json')

# ── 推送目标 ──────────────────────────────────────────────
PUSH_TARGET  = os.environ.get('JARVIS_TARGET', 'YOUR_USER_ID:thread:YOUR_THREAD_ID')
PUSH_CHANNEL = 'jarvis'

# ── API配置 ───────────────────────────────────────────────
API_KEY = 'sDqoRAyeYHHzevKNxSj5JfkWpNUd6v8qPAhVy0Y8wbWGwC48eC7uhFOENAlVqV7b'
SECRET  = 'hXQnzQco9SNVgKgF2m3xvBGlJjOHBVtlzqRlxOTkp0kiJAwAOTeUiGLQSAopqIj7'
FAPI    = 'https://fapi.binance.com'

# ── 体制风险定义 ──────────────────────────────────────────
# (持仓方向, 市场体制) → 风险等级
RISK_MAP = {
    ('SHORT', 'BULL_TREND'):      ('🔴', '危险', '牛市趋势做空 = 死穴级逆势'),
    ('SHORT', 'BULL_EARLY'):      ('🔴', '危险', '牛市初期做空 = 高风险逆势'),
    ('SHORT', 'BULL_CORRECTION'): ('🟡', '注意', '牛市回调做空 = 短期可行但需止盈'),
    ('SHORT', 'BEAR_RECOVERY'):   ('🟡', '注意', '熊市反弹期做空 = 反弹风险上升'),
    ('LONG',  'BEAR_TREND'):      ('🔴', '危险', '熊市趋势做多 = 铁证封禁(WR=45%)'),
    ('LONG',  'BEAR_EARLY'):      ('🔴', '危险', '熊市初期做多 = 高风险逆势'),
}

# ── BTC RSI阈值广播 ───────────────────────────────────────
RSI_THRESHOLDS = [45, 50, 55]  # 穿越时广播


def sign(params):
    ts = int(time.time() * 1000)
    params['timestamp'] = ts
    qs = urllib.parse.urlencode(params)
    sig = hmac.new(SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    params['signature'] = sig
    return params


def get_all_positions():
    """获取所有有仓位的持仓"""
    try:
        params = sign({})
        r = requests.get(f"{FAPI}/fapi/v2/positionRisk", params=params,
                         headers={'X-MBX-APIKEY': API_KEY}, timeout=10)
        return [p for p in r.json() if float(p.get('positionAmt', 0)) != 0]
    except Exception as e:
        return []


def get_btc_rsi1h():
    """获取BTC RSI_1H"""
    try:
        r = requests.get(f"{FAPI}/fapi/v1/klines",
                         params={'symbol': 'BTCUSDT', 'interval': '1h', 'limit': 30},
                         timeout=10)
        closes = [float(k[4]) for k in r.json()]
        if len(closes) < 15:
            return None
        # RSI计算
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        n = 14
        avg_g = sum(gains[-n:]) / n
        avg_l = sum(losses[-n:]) / n
        if avg_l == 0:
            return 100.0
        rs = avg_g / avg_l
        return round(100 - 100 / (1 + rs), 1)
    except Exception:
        return None


def get_symbol_regime(sym):
    """快速获取标的体制（基于RSI+EMA）"""
    try:
        # 获取4H K线
        r = requests.get(f"{FAPI}/fapi/v1/klines",
                         params={'symbol': sym, 'interval': '4h', 'limit': 60},
                         timeout=10)
        klines = r.json()
        closes = [float(k[4]) for k in klines]
        if len(closes) < 55:
            return 'UNKNOWN'

        # RSI_4H
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        n = 14
        avg_g = sum(gains[-n:]) / n
        avg_l = sum(losses[-n:]) / n
        rsi_4h = 100 - 100/(1 + avg_g/avg_l) if avg_l > 0 else 100

        # EMA20/EMA50
        def ema_calc(data, period):
            k = 2 / (period + 1)
            result = data[0]
            for v in data[1:]:
                result = v * k + result * (1 - k)
            return result

        ema20 = ema_calc(closes, 20)
        ema50 = ema_calc(closes, 50)
        price = closes[-1]

        # 简化体制判断
        if price > ema20 > ema50 and rsi_4h > 55:
            return 'BULL_TREND'
        elif price > ema20 > ema50 and rsi_4h > 45:
            return 'BULL_EARLY'
        elif price < ema20 < ema50 and rsi_4h < 45:
            return 'BEAR_TREND'
        elif price < ema20 < ema50 and rsi_4h < 55:
            return 'BEAR_EARLY'
        elif price > ema20 and rsi_4h > 50:
            return 'BULL_CORRECTION'
        elif price < ema50 and rsi_4h > 45:
            return 'BEAR_RECOVERY'
        else:
            return 'CHOP_MID'
    except Exception:
        return 'UNKNOWN'


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {'btc_rsi_last': None, 'last_alerts': {}, 'last_run': 0}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def push(msg):
    try:
        result = subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', PUSH_CHANNEL,
             '--target',  PUSH_TARGET,
             '--message', msg],
            capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0
    except Exception:
        return False


def main():
    state = load_state()
    now   = int(time.time())
    alerts = []

    # ── 1. BTC RSI_1H 阈值穿越检测 ── 已删除 ─────────────
    # [苏摩裁决 2026-06-30] RSI动能描述不推送，只推关键价格位穿越
    # RSI广播已迁移至 btc_regime_watcher.py v2.0，此处彻底移除防止重复
    btc_rsi = get_btc_rsi1h()   # 仍计算RSI供持仓检测使用
    btc_price_r = requests.get(f"{FAPI}/fapi/v1/ticker/price",
                                params={'symbol': 'BTCUSDT'}, timeout=8)
    btc_price = float(btc_price_r.json().get('price', 0))
    state['btc_rsi_last'] = btc_rsi  # 保存状态备用

    # ── 2. 持仓体制匹配检测 ───────────────────────────────
    positions = get_all_positions()
    if not positions:
        save_state(state)
        return  # 无持仓，静默退出

    mismatch_lines = []
    last_alerts = state.get('last_alerts', {})

    for pos in positions:
        sym   = pos['symbol']
        amt   = float(pos['positionAmt'])
        entry = float(pos['entryPrice'])
        mark  = float(pos['markPrice'])
        pnl   = float(pos['unRealizedProfit'])
        side  = 'SHORT' if amt < 0 else 'LONG'

        regime = get_symbol_regime(sym)
        risk_key = (side, regime)

        if risk_key in RISK_MAP:
            icon, level, reason = RISK_MAP[risk_key]
            pnl_pct = (mark - entry) / entry * 100 * (-1 if side == 'SHORT' else 1)

            # 防重复：同一标的+同一体制 30分钟内只推一次
            alert_key = f"{sym}:{side}:{regime}"
            last_ts = last_alerts.get(alert_key, 0)
            if now - last_ts < 1800:
                continue  # 30分钟内已推过，跳过

            last_alerts[alert_key] = now
            mismatch_lines.append(
f"""  {icon} {sym} {side} | 体制={regime}
     入场={entry:.4f} 现价={mark:.4f} 浮盈=${pnl:.2f}({pnl_pct:+.1f}%)
     风险={level}: {reason}""")

    if mismatch_lines:
        pos_block = '\n'.join(mismatch_lines)
        alerts.append(
f"""⚠️ 梵天P1 · 持仓体制逆势告警

检测到 {len(mismatch_lines)} 个逆势持仓：
{pos_block}

🔴 建议立即评估平仓或减仓
梵天自动感知 · 无需苏摩主动询问""")

    state['last_alerts'] = last_alerts
    state['last_run'] = now
    save_state(state)

    # ── 3. 推送 ────────────────────────────────────────────
    if alerts:
        full_msg = '\n\n─────────────────\n\n'.join(alerts)
        push(full_msg)
        print(f"[P1 PositionRegimeChecker] ✅ 推送 {len(alerts)} 条告警")
    # 无告警 → 完全静默


if __name__ == '__main__':
    main()
