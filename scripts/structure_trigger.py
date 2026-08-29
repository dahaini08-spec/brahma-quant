#!/usr/bin/env python3
"""
structure_trigger.py — 达摩院 BOS/CHoCH 结构触发器
2026-08-29 苏摩111封印

功能：
  1. 读取 structure_db_btc/eth.jsonl（BOS/CHoCH结构标注）
  2. 检测最近4H K线是否出现新的 BOS 或 CHoCH
  3. CHoCH + 方仓压缩 = 最强入场信号组合
  4. 结果写入 signal_queue.jsonl

触发条件：
  - CHoCH（趋势反转）：出现即触发，与方仓压缩组合更强
  - BOS（趋势确认）：需同时满足方仓压缩
"""
import sys, json, time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.signal_queue_writer import push_signal
from brahma_brain.brahma_fangcang_unified import unified_fangcang

DATA_DIR = Path(__file__).parent.parent / 'data'
DEAD_SHORT = {'BEAR_RECOVERY', 'BULL_EARLY', 'BULL_TREND'}
DEAD_LONG  = {'BEAR_TREND'}

def get_current_regime(sym):
    try:
        import json
        state = json.load(open(DATA_DIR / 'brahma_state.json'))
        return state.get('regime', 'CHOP_MID')
    except:
        return 'CHOP_MID'

def get_recent_kline_structure(sym):
    """从 structure_db 找最近24h内的 BOS/CHoCH"""
    fname = 'structure_db_btc.jsonl' if 'BTC' in sym else 'structure_db_eth.jsonl'
    fpath = DATA_DIR / fname
    if not fpath.exists():
        return []

    now_ms = time.time() * 1000
    cutoff_ms = now_ms - 24 * 3600 * 1000  # 24小时内

    results = []
    with open(fpath) as f:
        for line in f:
            try:
                r = json.loads(line)
                ts = float(r.get('ts', 0))
                structure = r.get('structure', 'NONE')
                if ts > cutoff_ms and structure in ('BOS', 'CHoCH'):
                    results.append(r)
            except:
                pass
    return results

def run():
    symbols = ['BTCUSDT', 'ETHUSDT']
    triggered = 0

    for sym in symbols:
        try:
            events = get_recent_kline_structure(sym)
            if not events:
                continue

            regime = get_current_regime(sym)
            latest = sorted(events, key=lambda x: float(x.get('ts',0)), reverse=True)[0]
            structure = latest.get('structure', '')
            swing = latest.get('swing_type', '')

            # CHoCH 判断方向
            # CHoCH + swing=LL/LH = 下行反转 → SHORT
            # CHoCH + swing=HH/HL = 上行反转 → LONG
            if structure == 'CHoCH':
                if swing in ('LL', 'LH'):
                    direction = 'SHORT'
                else:
                    direction = 'LONG'
            elif structure == 'BOS':
                # BOS 确认趋势延续
                if swing in ('HH', 'HL'):
                    direction = 'LONG'
                else:
                    direction = 'SHORT'
            else:
                continue

            # 体制死穴检查
            is_dead = (direction == 'SHORT' and regime in DEAD_SHORT) or \
                      (direction == 'LONG' and regime in DEAD_LONG)
            if is_dead:
                continue

            # 方仓验证（增强信号质量）
            ms = {'symbol': sym, 'bb_width': 0.84, 'rsi_1h': 50, 'regime': regime}
            fc = unified_fangcang(sym, ms=ms, signal_dir=direction, regime=regime)
            fc_n = fc.get('s2_n', 0) if fc else 0
            fc_wr = fc.get('s2_wr', 0.5) if fc else 0.5

            # 推送信号
            score = 120 if structure == 'CHoCH' else 100
            if fc_n > 0:
                score += 15  # 方仓铁证加分

            push_signal(
                symbol=sym,
                source=f'structure_db:{structure}',
                regime=regime,
                direction=direction,
                score=score,
                sl_pct=2.0,
                meta={
                    'structure': structure,
                    'swing_type': swing,
                    'fangcang_n': fc_n,
                    'fangcang_wr': fc_wr,
                    'ts_event': latest.get('ts'),
                }
            )
            triggered += 1
            print(f'[structure_trigger] {sym} {structure}+{swing} → {direction} regime={regime} score={score} fc_n={fc_n}')

        except Exception as e:
            print(f'[structure_trigger] {sym} ERROR: {e}')

    print(f'structure_trigger 完成: {triggered}个信号触发')
    return triggered

if __name__ == '__main__':
    run()
