#!/usr/bin/env python3
"""
持仓看护系统 position_guardian.py  [设计院封印 2026-07-17 苏摩111授权]

职责：
  1. 拉账户所有实盘持仓
  2. 检查每个持仓是否触及止损（-2%）或TP1（+3%）
  3. 检查梵天信号追踪的纸面仓位（BTC/ETH）的SL/TP状态
  4. 有触发则主动推送 Jarvis 警报
  5. 无触发则 HEARTBEAT_OK 静默

触发级别：
  P0 🚨 止损触及   → 立即推送，附带平仓建议
  P1 🎯 TP1触及    → 推送止盈建议
  P2 ⚠️ 浮亏>5%   → 推送风控警告
"""
import sys, os
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import json, time, subprocess, requests
from pathlib import Path

BASE  = _ROOT
DIR   = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, 'data', 'guardian_state.json')

# 推送阈值
SL_PCT_DEFAULT  = 2.0   # 未设止损时默认-2%警报
TP1_PCT_DEFAULT = 3.0   # 未设TP1时默认+3%提示
DRAWDOWN_WARN   = 5.0   # 浮亏超5%发P2警告

# 信号追踪文件（梵天纸面仓位）
SIGNAL_LOG = os.path.join(BASE, 'data', 'live_signal_log.jsonl')


def _jarvis_target():
    try:
        sys.path.insert(0, os.path.join(BASE, 'scripts'))
        from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
        return f'{JARVIS_USER_ID}:t:{JARVIS_THREAD_ID}'
    except Exception:
        return '73295708:t:019f8768-6731-777d-8924-2426a5abd10f'


def send_jarvis(msg: str):
    subprocess.run(
        ['openclaw', 'message', 'send',
         '--channel', 'jarvis', '--to', _jarvis_target(),
         '--message', msg],
        capture_output=True, timeout=15
    )


def get_price(sym: str) -> float:
    try:
        r = requests.get(
            f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}',
            timeout=5
        )
        return float(r.json()['price'])
    except Exception:
        return 0.0


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE))
        except Exception:
            pass
    return {}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(state, open(STATE_FILE, 'w'), indent=2)


def get_positions() -> list:
    """拉账户实盘持仓"""
    try:
        r = subprocess.run(
            ['binance-cli', 'futures-usds', 'account-information-v3'],
            capture_output=True, text=True, timeout=15
        )
        acct = json.loads(r.stdout)
        return [p for p in acct.get('positions', [])
                if abs(float(p.get('positionAmt', 0))) > 0]
    except Exception as e:
        print(f'[guardian] 持仓查询失败: {e}')
        return []


def get_signal_positions() -> list:
    """从 live_signal_log 读梵天纸面信号仓位"""
    seen = {}
    try:
        now = time.time()
        cutoff = now - 7 * 86400  # 7天内的信号
        with open(SIGNAL_LOG) as f:
            for line in f:
                try:
                    s = json.loads(line.strip())
                    if not (s.get('valid') or s.get('valid_signal')):
                        continue
                    ts = float(s.get('ts', s.get('timestamp', 0)) or 0)
                    if ts < cutoff:
                        continue
                    sym = s.get('symbol', '')
                    sc = float(s.get('score_final', s.get('score', 0)) or 0)
                    prev = float(seen[sym].get('score', 0)) if sym in seen else -1
                    if sc > prev:
                        seen[sym] = s
                except Exception:
                    pass
    except Exception:
        pass
    return list(seen.values())


def check_real_positions(positions: list, state: dict, now: float) -> list:
    """检查实盘持仓的止损/TP状态"""
    alerts = []
    for p in positions:
        sym = p['symbol']
        amt = float(p['positionAmt'])
        notional = abs(float(p.get('notional', 0)))
        if notional < 0.1:
            continue

        cp = get_price(sym)
        if cp <= 0:
            continue

        # 估算入场价（用 notional / |amt|）
        entry_price = notional / abs(amt) if abs(amt) > 0 else 0
        if entry_price <= 0:
            continue

        direction = 'LONG' if amt > 0 else 'SHORT'
        pnl_pct = (cp - entry_price) / entry_price * 100 if direction == 'LONG' else \
                  (entry_price - cp) / entry_price * 100

        # 检查止损（-SL_PCT_DEFAULT）
        sl_triggered = pnl_pct <= -SL_PCT_DEFAULT
        tp1_triggered = pnl_pct >= TP1_PCT_DEFAULT
        dd_warned = pnl_pct <= -DRAWDOWN_WARN

        # 去重：同一标的同一级别8H内不重复推送
        state_key_sl  = f'{sym}_sl'
        state_key_tp  = f'{sym}_tp1'
        state_key_dd  = f'{sym}_dd'
        last_sl  = state.get(state_key_sl, 0)
        last_tp  = state.get(state_key_tp, 0)
        last_dd  = state.get(state_key_dd, 0)

        if sl_triggered and now - last_sl > 8 * 3600:
            alerts.append({
                'level': 'P0', 'sym': sym, 'trigger': 'SL',
                'cp': cp, 'entry': round(entry_price, 4),
                'pnl': round(pnl_pct, 2), 'dir': direction,
                'notional': round(notional, 2),
                'state_key': state_key_sl,
            })
        elif tp1_triggered and now - last_tp > 8 * 3600:
            alerts.append({
                'level': 'P1', 'sym': sym, 'trigger': 'TP1',
                'cp': cp, 'entry': round(entry_price, 4),
                'pnl': round(pnl_pct, 2), 'dir': direction,
                'notional': round(notional, 2),
                'state_key': state_key_tp,
            })
        elif dd_warned and not sl_triggered and now - last_dd > 12 * 3600:
            alerts.append({
                'level': 'P2', 'sym': sym, 'trigger': 'DD',
                'cp': cp, 'entry': round(entry_price, 4),
                'pnl': round(pnl_pct, 2), 'dir': direction,
                'notional': round(notional, 2),
                'state_key': state_key_dd,
            })

    return alerts


def check_signal_positions(sig_positions: list, state: dict, now: float) -> list:
    """
    [已废弃 2026-07-17 苏摩宣告：纸面信号追踪体系完全取消]
    梵天只监控实盘持仓，不再追踪纸面信号
    """
    return []  # 永久返回空列表，不产生任何信号追踪告警


def format_alert(a: dict) -> str:
    level_icon = {'P0': '🚨', 'P1': '🎯', 'P2': '⚠️'}.get(a['level'], '📊')
    trigger_name = {'SL': '止损触及', 'TP1': 'TP1触及', 'DD': '深度亏损警告'}.get(a['trigger'], a['trigger'])
    source = '【信号追踪】' if a.get('source') == 'signal' else '【实盘持仓】'
    msg = (
        f"{level_icon} {a['level']} 梵天看护警报 {source}\n"
        f"标的: {a['sym']}  方向: {a['dir']}\n"
        f"触发: {trigger_name}  当前价: {a['cp']:.4g}\n"
        f"入场: {a['entry']:.4g}  盈亏: {a['pnl']:+.2f}%\n"
    )
    if a.get('sl'):
        msg += f"止损线: {a['sl']:.4g}  TP1: {a.get('tp1', 'N/A'):.4g}\n"
    if a.get('score'):
        msg += f"信号评分: {a['score']:.0f}\n"
    # 建议操作
    if a['trigger'] == 'SL':
        msg += f"\n📌 建议: 立即平仓止损，信号已失效"
    elif a['trigger'] == 'TP1':
        msg += f"\n📌 建议: 50%止盈，剩余仓位移动止损至入场价保本"
    elif a['trigger'] == 'DD':
        msg += f"\n📌 建议: 评估是否加仓平均或减仓止损，浮亏已达{abs(a['pnl']):.1f}%"
    return msg


def main():
    now   = time.time()
    state = load_state()

    # 拉持仓
    real_pos   = get_positions()
    sig_pos    = get_signal_positions()

    print(f'[guardian] 实盘持仓={len(real_pos)}  信号追踪={len(sig_pos)}')

    # 检查
    real_alerts   = check_real_positions(real_pos, state, now)
    signal_alerts = check_signal_positions(sig_pos, state, now)
    all_alerts    = real_alerts + signal_alerts

    # P0 优先排序
    all_alerts.sort(key=lambda x: {'P0': 0, 'P1': 1, 'P2': 2}.get(x['level'], 3))

    if not all_alerts:
        print('HEARTBEAT_OK')
        return

    # 推送
    for a in all_alerts:
        msg = format_alert(a)
        send_jarvis(msg)
        print(f"[guardian] 推送 {a['level']} {a['sym']} {a['trigger']}")
        # 更新去重状态
        state[a['state_key']] = now

    save_state(state)
    print(f'[guardian] 共推送 {len(all_alerts)} 条警报')


if __name__ == '__main__':
    main()
