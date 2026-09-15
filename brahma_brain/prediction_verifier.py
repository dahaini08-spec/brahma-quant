#!/usr/bin/env python3
"""
prediction_verifier.py — 预测编码管道 Step 2: 延迟验证器
[2026-09-15 苏摩111] 果蝇Phase 2C 预测编码

原理：
- 每30分钟扫描 prediction_log.jsonl
- 找4h前未验证的 prediction
- 拉取当前价格，判断方向是否正确
- 标记 prediction_correct

运行方式：crontab 每30分钟运行
接入点：scripts/prediction_verify_cron.py 调用本模块
"""

import json, time, sys
from pathlib import Path

# 添加项目根目录到path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from brahma_brain.prediction_recorder import get_unverified, mark_verified


def _fetch_price(symbol: str) -> float:
    """从Binance API拉取当前价格"""
    try:
        import urllib.request
        sym = symbol.upper()
        if not sym.endswith('USDT'):
            sym += 'USDT'
        url = f'https://api.binance.com/api/v3/ticker/price?symbol={sym}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return float(data.get('price', 0))
    except Exception as e:
        print(f'[prediction_verifier] 价格拉取失败 {symbol}: {e}', file=sys.stderr)
        return 0.0


def verify_predictions(delay_hours: float = 4.0) -> dict:
    """验证所有超过delay_hours未验证的预测

    Returns:
        {verified: int, correct: int, incorrect: int, errors: int}
    """
    unverified = get_unverified(delay_hours)
    stats = {'verified': 0, 'correct': 0, 'incorrect': 0, 'errors': 0}

    for pred in unverified:
        symbol = pred.get('symbol', '')
        direction = pred.get('direction', '')
        entry_range = pred.get('expected_entry_range', [0, 0])
        signal_id = pred.get('signal_id', '')

        if not symbol or not direction or not entry_range:
            continue

        # 拉取当前价格
        current_price = _fetch_price(symbol)
        if current_price <= 0:
            stats['errors'] += 1
            continue

        # 判断方向是否正确
        entry_mid = (entry_range[0] + entry_range[1]) / 2
        if direction == 'LONG':
            correct = current_price > entry_mid
        elif direction == 'SHORT':
            correct = current_price < entry_mid
        else:
            correct = False

        # 标记
        mark_verified(signal_id, correct, current_price)
        stats['verified'] += 1
        if correct:
            stats['correct'] += 1
        else:
            stats['incorrect'] += 1

    return stats


def get_prediction_accuracy(min_samples: int = 20) -> dict:
    """获取预测准确率统计（供DAG校准器使用）

    Returns:
        {total: int, correct: int, accuracy: float, by_regime: {regime: {correct, total}}}
    """
    log_file = ROOT / 'data' / 'prediction_log.jsonl'
    if not log_file.exists():
        return {'total': 0, 'correct': 0, 'accuracy': 0.0, 'by_regime': {}}

    verified = []
    with open(log_file) as f:
        for line in f:
            try:
                r = json.loads(line)
                if r.get('verified') and r.get('prediction_correct') is not None:
                    verified.append(r)
            except json.JSONDecodeError:
                continue

    total = len(verified)
    correct = sum(1 for r in verified if r['prediction_correct'])
    accuracy = correct / total if total > 0 else 0.0

    # 按体制分组
    by_regime = {}
    for r in verified:
        regime = r.get('regime', 'UNKNOWN')
        if regime not in by_regime:
            by_regime[regime] = {'correct': 0, 'total': 0}
        by_regime[regime]['total'] += 1
        if r['prediction_correct']:
            by_regime[regime]['correct'] += 1

    return {
        'total': total,
        'correct': correct,
        'accuracy': accuracy,
        'by_regime': by_regime,
    }


if __name__ == '__main__':
    stats = verify_predictions(delay_hours=4.0)
    print(f'验证完成: {stats}')

    acc = get_prediction_accuracy()
    print(f'预测准确率: {acc["accuracy"]:.1%} ({acc["correct"]}/{acc["total"]})')
    for regime, s in acc['by_regime'].items():
        r_acc = s['correct'] / s['total'] * 100 if s['total'] > 0 else 0
        print(f'  {regime}: {r_acc:.1f}% ({s["correct"]}/{s["total"]})')
