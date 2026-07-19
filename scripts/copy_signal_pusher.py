#!/usr/bin/env python3
"""
公域跟单推送模块 copy_signal_pusher.py
[设计院封印 2026-07-17 苏摩111授权]

职责：
  梵天主账户开仓后，同步推送「公域跟单卡片」
  格式极简，苏摩30秒内可完成手动跟单

调用方式：
  from copy_signal_pusher import push_copy_signal
  push_copy_signal(record)  # 传入 brahma_lifecycle 的仓位记录
"""
import sys, os
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'scripts'))

import json, time, subprocess, requests

# 公域账户跟单比例（相对主账户）
COPY_RATIO  = 1.0   # 1:1 等比例跟单
COPY_LEV    = 3     # 公域账户默认杠杆

# 公域账户 NAV 估算（手动维护，影响跟单仓位大小）
COPY_NAV_USDT = 100.0   # 公域账户约100U NAV，苏摩可调整

def _jarvis_target() -> str:
    try:
        from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
        return f'{JARVIS_USER_ID}:t:{JARVIS_THREAD_ID}'
    except Exception:
        return '73295708:t:019f5e0f-7d13-7392-a4e1-262e1cfc2dc2'

def push(msg: str):
    subprocess.run(
        ['openclaw', 'message', 'send', '--channel', 'jarvis',
         '--to', _jarvis_target(), '--message', msg],
        capture_output=True, timeout=15
    )

def calc_copy_qty(sym: str, entry: float, notional_main: float) -> str:
    """
    计算公域跟单数量
    公域NAV × 5% × COPY_LEV / 现价
    """
    try:
        import math
        # 按公域NAV的5%仓位
        pos_usdt = COPY_NAV_USDT * 0.05 * COPY_LEV
        raw_qty  = pos_usdt / entry if entry > 0 else 0

        # 获取精度
        ei = requests.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=6).json()
        step = 1.0
        for s in ei.get('symbols', []):
            if s['symbol'] == sym:
                for f in s.get('filters', []):
                    if f['filterType'] == 'LOT_SIZE':
                        step = float(f['stepSize'])
                        break
                break
        decimals = max(0, round(-math.log10(step))) if step < 1 else 0
        qty = round(math.floor(raw_qty / step) * step, decimals)
        return str(qty) if qty > 0 else '—'
    except Exception:
        return '—'


def push_copy_signal(record: dict):
    """
    主账户开仓后调用，推送公域跟单卡片
    record: brahma_lifecycle.py 的仓位记录
    """
    sym    = record.get('symbol', '')
    dr     = record.get('direction', 'LONG')
    entry  = float(record.get('entry_price', 0))
    sl     = float(record.get('sl', 0))
    tp1    = float(record.get('tp1', 0))
    tp2    = float(record.get('tp2', 0))
    score  = record.get('score', 0)
    regime = record.get('regime', '')
    lev    = record.get('lev', COPY_LEV)
    notional = float(record.get('notional', 0))
    order_id = record.get('order_id', '')

    # 计算公域跟单参数
    copy_qty     = calc_copy_qty(sym, entry, notional)
    copy_notional= round(float(copy_qty) * entry, 2) if copy_qty != '—' else 0
    side_cn      = '做多 🟢' if dr == 'LONG' else '做空 🔴'
    sl_pct       = round(abs(sl - entry) / entry * 100, 1) if entry > 0 else 2.0
    tp1_pct      = round(abs(tp1 - entry) / entry * 100, 1) if entry > 0 else 3.0
    tp2_pct      = round(abs(tp2 - entry) / entry * 100, 1) if entry > 0 else 6.0

    # 操作步骤（极简，3步完成）
    msg = f"""
📡 梵天公域跟单信号

━━━━━━━━━━━━━━━━━━━━
标的：{sym}
方向：{side_cn}
杠杆：{lev}x
━━━━━━━━━━━━━━━━━━━━
入场价：{entry:.4g}
数 量：{copy_qty}（建议）
名 义：${copy_notional}
━━━━━━━━━━━━━━━━━━━━
止 损：{sl:.4g}  (-{sl_pct}%)
TP1：{tp1:.4g}  (+{tp1_pct}%)
TP2：{tp2:.4g}  (+{tp2_pct}%)
━━━━━━━━━━━━━━━━━━━━
📊 score={score:.0f}  {regime}
🔗 梵天主单已成交 orderId={order_id}

【公域操作步骤】
① 币安App → 合约 → {sym}
② 调杠杆至 {lev}x → 市价{('买入' if dr=='LONG' else '卖出')}
③ 数量填 {copy_qty}，挂止损 {sl:.4g}
⏱ 请在3分钟内完成（避免价差过大）
""".strip()

    push(msg)
    print(f'[copy_pusher] 推送公域跟单卡片: {sym} {dr}')
    return msg


def push_copy_close(record: dict, reason: str, close_price: float, pnl_pct: float):
    """
    主账户平仓后，提醒公域账户同步平仓
    """
    sym  = record.get('symbol', '')
    dr   = record.get('direction', 'LONG')
    close_side_cn = '卖出平多' if dr == 'LONG' else '买入平空'
    reason_cn = {
        'SL':  '🚨 止损触发',
        'TP1': '🎯 TP1止盈(50%)',
        'TP2': '🎯 TP2止盈(全平)',
    }.get(reason, reason)

    msg = f"""
⚡ 梵天公域平仓信号

标的：{sym}  {reason_cn}
平仓价：{close_price:.4g}
主账户盈亏：{pnl_pct:+.2f}%

【公域操作】
币安App → 合约 → {sym} → {close_side_cn}
{"→ 平仓50%（与主账户同步）" if reason == "TP1" else "→ 全部平仓"}

⏱ 请立即操作
""".strip()

    push(msg)
    print(f'[copy_pusher] 推送公域平仓提醒: {sym} {reason}')
    return msg


if __name__ == '__main__':
    # 测试推送
    test_record = dict(
        symbol='BTCUSDT', direction='LONG', entry_price=63500,
        sl=62230, tp1=65455, tp2=67410, score=162, regime='BULL_TREND',
        lev=3, notional=952.5, order_id='647693399'
    )
    print("=== 测试开仓推送 ===")
    print(push_copy_signal(test_record))
    print("\n=== 测试平仓推送 ===")
    print(push_copy_close(test_record, 'TP1', 65455, 3.0))
