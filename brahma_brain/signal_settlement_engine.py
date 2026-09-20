#!/usr/bin/env python3
"""
signal_settlement_engine.py — 信号结算闭环引擎
设计院 2026-09-18 苏摩111封印

功能：
1. 每次分析产生的信号自动创建模拟入场（paper position）
2. 跟踪价格直到TP或SL触发
3. 结算后写入WR反馈到live_signal_log.jsonl
4. 用实际结算结果验证score有效性

接入位置：brahma_manual_analysis.py 每次分析后调用 settle_pending() + record_signal()
数据文件：
  - data/paper_positions.jsonl  — 活跃模拟持仓
  - data/live_signal_log.jsonl  — 已结算信号（含实际WR）
"""
import sys
import json
import time
import os
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
PAPER_FILE = DATA / 'paper_positions.jsonl'
SIGNAL_LOG = DATA / 'live_signal_log.jsonl'


def settle_pending(current_price: float, symbol: str) -> dict:
    """检查所有pending的paper position，用当前价格结算"""
    if not PAPER_FILE.exists():
        return {'settled': 0, 'pending': 0}

    positions = []
    settled = []
    with open(PAPER_FILE) as f:
        for line in f:
            try:
                p = json.loads(line.strip())
                if p.get('symbol') != symbol:
                    positions.append(p)
                    continue
                if p.get('status') != 'OPEN':
                    positions.append(p)
                    continue

                entry = (p['entry_lo'] + p['entry_hi']) / 2
                tp = p.get('tp1', 0)
                sl = p.get('sl', 0)
                direction = p.get('direction', 'LONG')

                # 结算逻辑
                hit_tp = False
                hit_sl = False
                if direction == 'LONG':
                    hit_tp = current_price >= tp if tp > 0 else False
                    hit_sl = current_price <= sl if sl > 0 else False
                else:  # SHORT
                    hit_tp = current_price <= tp if tp > 0 else False
                    hit_sl = current_price >= sl if sl > 0 else False

                # 超时结算（24h后强制结算）
                age_h = (time.time() - p.get('ts', 0)) / 3600
                timeout = age_h > 24

                if hit_tp or hit_sl or timeout:
                    if hit_tp:
                        outcome = 'WIN'
                        pnl_pct = abs(tp - entry) / entry * 100
                    elif hit_sl:
                        outcome = 'LOSS'
                        pnl_pct = -abs(sl - entry) / entry * 100
                    else:
                        # 超时：用当前价格结算
                        if direction == 'LONG':
                            pnl_pct = (current_price - entry) / entry * 100
                        else:
                            pnl_pct = (entry - current_price) / entry * 100
                        outcome = 'WIN' if pnl_pct > 0 else 'LOSS'

                    p['status'] = 'SETTLED'
                    p['outcome'] = outcome
                    p['pnl_pct'] = round(pnl_pct, 3)
                    p['settle_price'] = current_price
                    p['settle_ts'] = time.time()

                    settled.append(p)
                    # 写入signal log
                    with open(SIGNAL_LOG, 'a') as sf:
                        sf.write(json.dumps(p, ensure_ascii=False) + '\n')
                else:
                    positions.append(p)
            except Exception as _e:
                print(f"[WARN] signal_settlement_engine: _e", file=sys.stderr)

    # 回写未结算的
    with open(PAPER_FILE, 'w') as f:
        for p in positions:
            f.write(json.dumps(p, ensure_ascii=False) + '\n')

    return {
        'settled': len(settled),
        'pending': len([p for p in positions if p.get('status') == 'OPEN']),
        'settled_details': settled,
    }


def record_signal(symbol: str, direction: str, entry_lo: float, entry_hi: float,
                  sl: float, tp1: float, score: float, regime: str,
                  signal_id: str = None) -> dict:
    """记录新信号到paper positions"""
    if not signal_id:
        signal_id = f"{symbol}_{int(time.time())}"

    position = {
        'signal_id': signal_id,
        'symbol': symbol,
        'direction': direction,
        'entry_lo': entry_lo,
        'entry_hi': entry_hi,
        'sl': sl,
        'tp1': tp1,
        'score': score,
        'regime': regime,
        'ts': time.time(),
        'status': 'OPEN',
        'created_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }

    with open(PAPER_FILE, 'a') as f:
        f.write(json.dumps(position, ensure_ascii=False) + '\n')

    return position


def get_wr_stats() -> dict:
    """从已结算信号计算WR统计"""
    if not SIGNAL_LOG.exists():
        return {'total': 0, 'wins': 0, 'losses': 0, 'wr': 0.0, 'by_regime': {}}

    from collections import defaultdict
    stats = defaultdict(lambda: {'win': 0, 'lose': 0, 'scores': []})

    with open(SIGNAL_LOG) as f:
        for line in f:
            try:
                d = json.loads(line.strip())
                if d.get('status') != 'SETTLED':
                    continue
                key = f"{d.get('regime','?')}:{d.get('direction','?')}"
                if d.get('outcome') == 'WIN':
                    stats[key]['win'] += 1
                elif d.get('outcome') == 'LOSS':
                    stats[key]['lose'] += 1
                stats[key]['scores'].append(d.get('score', 0))
            except Exception as _e:
                print(f"[WARN] signal_settlement_engine: _e", file=sys.stderr)

    result = {}
    for key, v in stats.items():
        total = v['win'] + v['lose']
        if total > 0:
            result[key] = {
                'wr': round(v['win'] / total, 3),
                'win': v['win'],
                'lose': v['lose'],
                'n': total,
                'avg_score': round(sum(v['scores']) / len(v['scores']), 1) if v['scores'] else 0,
            }

    total_wins = sum(v['win'] for v in stats.values())
    total_losses = sum(v['lose'] for v in stats.values())
    total = total_wins + total_losses

    return {
        'total': total,
        'wins': total_wins,
        'losses': total_losses,
        'wr': round(total_wins / total, 3) if total > 0 else 0.0,
        'by_regime': result,
    }


def get_dynamic_threshold() -> int:
    """[改革4] 根据实盘WR自动调整score门槛"""
    stats = get_wr_stats()
    if stats['total'] < 30:
        # 样本不足，用默认门槛
        return 80

    # 找到WR>55%的最低score区间
    # 简化：如果整体WR>60%，门槛可以降到60；WR>55%降到70；否则保持80
    overall_wr = stats['wr']
    if overall_wr >= 0.60:
        return 60
    elif overall_wr >= 0.55:
        return 70
    else:
        return 80


if __name__ == '__main__':
    # 测试
    r = settle_pending(78000, 'BTCUSDT')
    print(f"结算: {r['settled']} | 待结算: {r['pending']}")
    wr = get_wr_stats()
    print(f"WR: {wr['total']}笔 | 胜={wr['wins']} 负={wr['losses']} WR={wr['wr']:.1%}")
    print(f"动态门槛: {get_dynamic_threshold()}")
