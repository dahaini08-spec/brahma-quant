#!/usr/bin/env python3
"""
brahma_scan_guard.py — 兜底全量扫描保底
设计院 2026-08-23 苏摩111封印（重建）

功能：每12H全量扫描主力币，防止信号断崖
根因：旧脚本缺失导致4.1天零信号
逻辑：scan_all → filter score>=110 → 推送有效信号
"""
import sys, os, json, time, logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'brahma_brain'))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger('scan_guard')

# 主力币扫描列表（30个）
FAST_SYMBOLS = [
    'BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT',
    'DOGEUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT',
    'MATICUSDT','UNIUSDT','ATOMUSDT','LTCUSDT','ETCUSDT',
    'APTUSDT','ARBUSDT','OPUSDT','INJUSDT','SUIUSDT',
    'SEIUSDT','TIAUSDT','FETUSDT','RNDRUSDT','WLDUSDT',
    'JUPUSDT','PENDLEUSDT','EIGENUSDT','ENAUSDT','PYTHUSDT',
]

SCORE_THRESHOLD = 110  # 扫描保底门槛（低于主流程120，确保不遗漏）

def run_scan_guard():
    from brahma_brain.brahma_analysis_runner import run_analysis

    results = []
    triggered = []
    ts_start = time.time()

    for sym in FAST_SYMBOLS:
        try:
            r = run_analysis(sym)
            score = float((r.get('confluence') or {}).get('total') or 0)
            action = (r.get('confluence') or {}).get('action', '')
            valid = r.get('valid_signal', False)
            regime = r.get('regime', '')
            price = r.get('price', 0)

            results.append(dict(sym=sym, score=score, action=action,
                                valid=valid, regime=regime, price=price))

            if score >= SCORE_THRESHOLD or valid:
                triggered.append(dict(sym=sym, score=score, action=action,
                                      valid=valid, regime=regime, price=price))
        except Exception as e:
            logger.warning(f'[scan_guard] {sym} error: {e}')
            continue

    elapsed = round(time.time() - ts_start, 1)
    ts_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

    if triggered:
        print(f'[scan_guard] {ts_str} 扫描完成 {len(results)}个标的 耗时{elapsed}s')
        print(f'[scan_guard] 发现 {len(triggered)} 个有效信号:')
        for t in triggered:
            flag = '✅' if t['valid'] else '⭐'
            print(f'  {flag} {t["sym"]} {t["regime"]} score={t["score"]:.1f} {t["action"]} price={t["price"]}')
    else:
        print(f'[scan_guard] {ts_str} 扫描完成 {len(results)}个标的 耗时{elapsed}s 无触发信号')

    return len(triggered)

if __name__ == '__main__':
    run_scan_guard()
