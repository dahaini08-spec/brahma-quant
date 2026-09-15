#!/usr/bin/env python3
"""
prediction_recorder.py — 预测编码管道 Step 1
[2026-09-15 苏摩111] 果蝇Phase 2C 预测编码

原理：
- 每次分析完成后，记录"预期"到 prediction_log.jsonl
- 4h后由 prediction_verifier.py 验证 prediction error
- DAG校准器用 prediction accuracy 替代 WR 做权重调整

接入点：nerve_bus_writer.py emit_analysis_done() 之后调用
"""

import json, time
from pathlib import Path

LOG_FILE = Path(__file__).parent.parent / 'data' / 'prediction_log.jsonl'


def record_prediction(signal_id: str, symbol: str, direction: str,
                      entry_lo: float, entry_hi: float,
                      score: float, regime: str,
                      tp1: float = None, stop_loss: float = None,
                      extra: dict = None) -> dict:
    """记录一条预测，供4h后验证

    Args:
        signal_id: 信号唯一ID
        symbol: 交易对
        direction: LONG / SHORT
        entry_lo / entry_hi: 预期入场区间
        score: 当前score
        regime: 当前体制
        tp1: 止盈目标1
        stop_loss: 止损价
        extra: 附加数据（dims breakdown等）

    Returns:
        写入的记录dict
    """
    record = {
        'signal_id': signal_id,
        'ts': time.time(),
        'ts_iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'symbol': symbol,
        'direction': direction,
        'expected_entry_range': [entry_lo, entry_hi],
        'expected_direction': direction,
        'score': score,
        'regime': regime,
        'tp1': tp1,
        'stop_loss': stop_loss,
        'verified': False,
        'prediction_correct': None,
        'actual_price_4h': None,
        'extra': extra or {},
    }

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as e:
        import sys
        print(f'[prediction_recorder] 写入失败: {e}', file=sys.stderr)

    return record


def get_unverified(older_than_hours: float = 4.0) -> list:
    """获取N小时前未验证的预测（供verifier使用）"""
    cutoff = time.time() - older_than_hours * 3600
    results = []
    try:
        if not LOG_FILE.exists():
            return results
        with open(LOG_FILE) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if not r.get('verified') and r.get('ts', 0) < cutoff:
                        results.append(r)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        import sys
        print(f'[prediction_recorder] 读取失败: {e}', file=sys.stderr)
    return results


def mark_verified(signal_id: str, prediction_correct: bool, actual_price: float):
    """标记一条预测为已验证"""
    try:
        if not LOG_FILE.exists():
            return
        lines = []
        with open(LOG_FILE) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get('signal_id') == signal_id:
                        r['verified'] = True
                        r['prediction_correct'] = prediction_correct
                        r['actual_price_4h'] = actual_price
                        r['verified_ts'] = time.time()
                    lines.append(json.dumps(r, ensure_ascii=False))
                except json.JSONDecodeError:
                    continue
        with open(LOG_FILE, 'w') as f:
            for line in lines:
                f.write(line + '\n')
    except Exception as e:
        import sys
        print(f'[prediction_recorder] 标记失败: {e}', file=sys.stderr)


if __name__ == '__main__':
    # 自测
    r = record_prediction('test_001', 'BTCUSDT', 'LONG', 76000, 77000,
                         108, 'CHOP_MID', tp1=79000, stop_loss=74000)
    print(f'✅ 记录写入: {r["signal_id"]}')

    uv = get_unverified(0.001)  # 几乎立即可验证
    print(f'未验证(>0.001h): {len(uv)}条')

    mark_verified('test_001', True, 77500.0)
    print('✅ 标记验证完成')

    # 清理测试数据
    import os
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            lines = [l for l in f if 'test_001' not in l]
        with open(LOG_FILE, 'w') as f:
            f.writelines(lines)
        print('✅ 测试数据清理完成')
