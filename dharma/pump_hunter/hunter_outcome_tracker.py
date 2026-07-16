#!/usr/bin/env python3
"""
暴涨猎手 结果追踪系统 v1.0
[设计院封印 2026-07-16 苏摩111授权]

功能：
  - 推送后24H自动检查价格是否达到TP1/止损
  - 写入 hunter_outcomes.jsonl（永久历史记录）
  - 定期生成胜率统计（按评分段/体制/因子）

调用方式（cron每小时运行）：
  python3 dharma/pump_hunter/hunter_outcome_tracker.py

输出文件：
  hunter_outcomes.jsonl — 每条信号的最终结果
  hunter_win_rate.json  — 胜率统计摘要
"""
import sys, os
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import json, time, requests
from pathlib import Path

DIR          = os.path.dirname(os.path.abspath(__file__))
EXPIRY_FILE  = os.path.join(DIR, 'signal_expiry.json')
PUSH_RECORD  = os.path.join(DIR, 'signal_push_record.json')
OUTCOMES_LOG = os.path.join(DIR, 'hunter_outcomes.jsonl')
WIN_RATE_FILE= os.path.join(DIR, 'hunter_win_rate.json')

# 追踪窗口：24H后判定结果
OUTCOME_WINDOW_H = 24
# TP1达标定义：价格从推送价上涨≥2.5%
TP1_TRIGGER_PCT  = 2.5
# 止损定义：价格从推送价下跌≥2.0%
SL_TRIGGER_PCT   = 2.0


def get_price(sym: str) -> float:
    try:
        r = requests.get(
            f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}',
            timeout=5
        )
        return float(r.json()['price'])
    except Exception:
        return 0.0


def load_outcomes() -> list:
    outcomes = []
    if os.path.exists(OUTCOMES_LOG):
        with open(OUTCOMES_LOG) as f:
            for line in f:
                try: outcomes.append(json.loads(line.strip()))
                except: pass
    return outcomes


def already_resolved(sym: str, push_ts: float, outcomes: list) -> bool:
    """检查该推送是否已有结果记录"""
    for o in outcomes:
        if o.get('symbol') == sym and abs(o.get('push_ts', 0) - push_ts) < 60:
            return True
    return False


def compute_win_rate(outcomes: list) -> dict:
    """计算各维度胜率"""
    if not outcomes:
        return {}

    resolved = [o for o in outcomes if o.get('result') in ('WIN', 'LOSS', 'NEUTRAL')]
    if not resolved:
        return {'total': 0}

    wins   = [o for o in resolved if o.get('result') == 'WIN']
    losses = [o for o in resolved if o.get('result') == 'LOSS']
    total  = len(resolved)

    wr = len(wins) / total * 100 if total > 0 else 0

    # 按评分段
    buckets = {'70-77': [], '78-84': [], '85-89': [], '90+': []}
    for o in resolved:
        sc = o.get('score', 0)
        if sc >= 90: buckets['90+'].append(o)
        elif sc >= 85: buckets['85-89'].append(o)
        elif sc >= 78: buckets['78-84'].append(o)
        else: buckets['70-77'].append(o)

    by_score = {}
    for bucket, items in buckets.items():
        if items:
            w = sum(1 for i in items if i.get('result') == 'WIN')
            by_score[bucket] = {
                'n': len(items),
                'wins': w,
                'win_rate': round(w / len(items) * 100, 1)
            }

    # 按是否有催化剂（FR+空头共现）
    with_catalyst    = [o for o in resolved if o.get('squeeze_catalyst')]
    without_catalyst = [o for o in resolved if not o.get('squeeze_catalyst')]
    catalyst_wr = (
        round(sum(1 for o in with_catalyst if o['result']=='WIN') / len(with_catalyst) * 100, 1)
        if with_catalyst else None
    )
    no_catalyst_wr = (
        round(sum(1 for o in without_catalyst if o['result']=='WIN') / len(without_catalyst) * 100, 1)
        if without_catalyst else None
    )

    return {
        'total':           total,
        'wins':            len(wins),
        'losses':          len(losses),
        'overall_win_rate': round(wr, 1),
        'by_score':        by_score,
        'catalyst_wr':     catalyst_wr,
        'no_catalyst_wr':  no_catalyst_wr,
        'updated_at':      time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }


def main():
    now_ts   = time.time()
    outcomes = load_outcomes()
    resolved_syms = set()

    # 加载推送记录
    push_rec = {}
    if os.path.exists(PUSH_RECORD):
        try:
            push_rec = json.load(open(PUSH_RECORD))
        except Exception:
            pass

    # 加载expiry（含推送时的价格、TP1、SL）
    expiry = {}
    if os.path.exists(EXPIRY_FILE):
        try:
            expiry = json.load(open(EXPIRY_FILE))
        except Exception:
            pass

    new_outcomes = []

    for sym, rec in push_rec.items():
        push_ts = rec.get('last_push_ts', 0)
        if push_ts <= 0:
            continue

        age_h = (now_ts - push_ts) / 3600

        # 24H后才判定
        if age_h < OUTCOME_WINDOW_H:
            continue

        # 已经记录过
        if already_resolved(sym, push_ts, outcomes):
            continue

        # 获取推送时的价格信息
        exp_info = expiry.get(sym, {})
        push_price = exp_info.get('price', 0)
        tp1_price  = exp_info.get('tp1_price', 0)
        sl_price   = exp_info.get('sl_price', 0)
        score      = rec.get('last_score', 0)

        if push_price <= 0:
            continue  # 没有基准价格，无法判定

        # 获取当前价（用于估算，严格需要历史OHLC）
        cur_price = get_price(sym)
        if cur_price <= 0:
            continue

        pnl_pct = (cur_price - push_price) / push_price * 100

        # 判定结果（简化：用当前价 vs 推送价的涨跌判断）
        if tp1_price > 0 and cur_price >= tp1_price:
            result = 'WIN'
            result_note = f'达到TP1 cur={cur_price:.4g} tp1={tp1_price:.4g}'
        elif sl_price > 0 and cur_price <= sl_price:
            result = 'LOSS'
            result_note = f'触及止损 cur={cur_price:.4g} sl={sl_price:.4g}'
        elif pnl_pct >= TP1_TRIGGER_PCT:
            result = 'WIN'
            result_note = f'+{pnl_pct:.1f}%≥{TP1_TRIGGER_PCT}%'
        elif pnl_pct <= -SL_TRIGGER_PCT:
            result = 'LOSS'
            result_note = f'{pnl_pct:.1f}%≤-{SL_TRIGGER_PCT}%'
        else:
            result = 'NEUTRAL'
            result_note = f'{pnl_pct:.1f}% 未触发任一边'

        entry = {
            'symbol':          sym,
            'score':           score,
            'push_ts':         push_ts,
            'push_at':         rec.get('last_push_at', ''),
            'push_price':      push_price,
            'tp1_price':       tp1_price,
            'sl_price':        sl_price,
            'cur_price':       cur_price,
            'pnl_pct':         round(pnl_pct, 2),
            'result':          result,
            'result_note':     result_note,
            'squeeze_catalyst': exp_info.get('squeeze_catalyst', False),
            'resolved_at':     time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }
        new_outcomes.append(entry)
        print(f'[outcome] {sym} score={score} → {result} ({result_note})')

    # 写入 hunter_outcomes.jsonl
    if new_outcomes:
        with open(OUTCOMES_LOG, 'a') as f:
            for o in new_outcomes:
                f.write(json.dumps(o, ensure_ascii=False) + '\n')

    # 重新计算胜率
    all_outcomes = outcomes + new_outcomes
    win_rate_data = compute_win_rate(all_outcomes)
    json.dump(win_rate_data, open(WIN_RATE_FILE, 'w'), indent=2, ensure_ascii=False)

    # 打印摘要
    print(f'\n── 暴涨猎手 胜率统计 ──')
    print(f'总已结算: {win_rate_data.get("total", 0)}')
    print(f'总体胜率: {win_rate_data.get("overall_win_rate", "N/A")}%')
    print(f'有催化剂(FR/空头)胜率: {win_rate_data.get("catalyst_wr", "N/A")}%')
    print(f'无催化剂胜率:          {win_rate_data.get("no_catalyst_wr", "N/A")}%')
    if win_rate_data.get('by_score'):
        for bucket, d in win_rate_data['by_score'].items():
            print(f'  score {bucket}: WR={d["win_rate"]}% (n={d["n"]})')
    print(f'本次新结算: {len(new_outcomes)}条')

    return len(new_outcomes)


if __name__ == '__main__':
    main()
