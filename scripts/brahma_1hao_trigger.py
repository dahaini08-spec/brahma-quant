#!/usr/bin/env python3
"""
brahma_1hao_trigger.py — 1号工程分析触发器
设计院 2026-08-23 苏摩111封印（重建）

功能：每2H触发BTC+ETH梵天全能力分析
根因：旧脚本缺失导致主动分析链路断裂
逻辑：run_analysis(BTC+ETH) → score>=100 → 推送信号卡
"""
import sys, os, json, time, logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'brahma_brain'))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger('1hao_trigger')

CORE_SYMBOLS = ['BTCUSDT', 'ETHUSDT']
PUSH_THRESHOLD = 100  # 1号工程推送门槛

def run_1hao_trigger():
    from brahma_brain.brahma_analysis_runner import run_analysis

    ts_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    pushed = []

    for sym in CORE_SYMBOLS:
        try:
            r = run_analysis(sym)
            c = r.get('confluence') or {}
            p = r.get('params') or {}
            score = float(c.get('total') or 0)
            action = c.get('action', '')
            valid = r.get('valid_signal', False)
            regime = r.get('regime', '')
            price = r.get('price', 0)
            rr1 = p.get('rr1', 0)
            sl_pct = p.get('sl_pct', 0)
            tp1 = p.get('tp1', 0)
            entry_lo = p.get('entry_lo', 0)
            entry_hi = p.get('entry_hi', 0)
            timing = r.get('timing_status', '')

            if score >= PUSH_THRESHOLD or valid:
                pushed.append(dict(sym=sym, score=score, action=action,
                                   valid=valid, regime=regime, price=price,
                                   rr1=rr1, sl_pct=sl_pct, tp1=tp1,
                                   entry_lo=entry_lo, entry_hi=entry_hi,
                                   timing=timing))
        except Exception as e:
            logger.warning(f'[1hao_trigger] {sym} error: {e}')
            continue

    if pushed:
        print(f'[1hao_trigger] {ts_str} 1号工程触发 {len(pushed)}个信号:')
        for t in pushed:
            flag = '✅' if t['valid'] else '📊'
            print(f'  {flag} {t["sym"]} {t["regime"]} score={t["score"]:.1f} {t["action"]} '
                  f'entry={t["entry_lo"]:.0f}~{t["entry_hi"]:.0f} '
                  f'tp1={t["tp1"]:.0f} sl={t["sl_pct"]}% rr={t["rr1"]} timing={t["timing"]}')
    else:
        print(f'[1hao_trigger] {ts_str} BTC+ETH score<{PUSH_THRESHOLD} 无推送')

    return len(pushed)

if __name__ == '__main__':
    run_1hao_trigger()
