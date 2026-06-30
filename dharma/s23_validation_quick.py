#!/usr/bin/env python3
"""
dharma/s23_validation_quick.py — s23 快速离线验证脚本
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 × 达摩院 · 2026-06-17

目标：
  用现有 parquet 数据验证 s23 在不同体制下的分布
  不跑完整回放（耗时），只验证"s23是否方向正确"

验证逻辑：
  1. 读取历史信号（live_signal_log 或 replay 结果）
  2. 对每条信号，用当时的K线计算 s23
  3. 对比 s23>0 的信号和 s23<0 的信号的历史WR
  4. 如果 s23>0 对应更高WR：有效

苏摩约束：
  - 纯离线，不接触实盘
  - 不修改任何系统参数
  - 结果仅供达摩院参考，n<100不得作为修参依据
"""

import sys, json
from pathlib import Path
from datetime import datetime, timezone

BASE = Path('/root/.openclaw/workspace/trading-system')
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

import numpy as np

TAG = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')


def load_live_signals():
    """加载 live_signal_log 中的已结算信号"""
    log_path = BASE / 'data' / 'live_signal_log.jsonl'
    if not log_path.exists():
        log_path = BASE / 'signals' / 'live_signal_log.jsonl'
    if not log_path.exists():
        return []

    signals = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                sig = json.loads(line)
                # 只要已结算的（有outcome）且非LEGACY
                dq = sig.get('_data_quality')
                outcome = sig.get('outcome') or sig.get('result')
                if outcome and dq is None:
                    signals.append(sig)
            except Exception:
                pass
    return signals


def load_parquet_klines(symbol: str, interval: str = '15m', n: int = 200):
    """
    从 parquet 加载最近 n 根 K线（用于离线验证）
    返回 OHLCV list
    """
    try:
        import pandas as pd
        fixed_dir = BASE / 'data' / 'backtest' / 'fixed'
        # 找对应文件
        files = list(fixed_dir.glob(f'{symbol}_{interval}*.parquet'))
        if not files:
            files = list(fixed_dir.glob(f'{symbol.lower()}_{interval}*.parquet'))
        if not files:
            return []
        df = pd.read_parquet(sorted(files)[-1])
        # 确保有OHLCV列
        cols_map = {}
        for c in df.columns:
            cl = c.lower()
            if 'open' in cl:   cols_map['o'] = c
            elif 'high' in cl: cols_map['h'] = c
            elif 'low' in cl:  cols_map['l'] = c
            elif 'close' in cl and 'open' not in cl: cols_map['c'] = c
            elif 'vol' in cl:  cols_map['v'] = c
        if len(cols_map) < 4:
            return []
        rows = df[[cols_map.get('o', df.columns[0]),
                   cols_map.get('h', df.columns[1]),
                   cols_map.get('l', df.columns[2]),
                   cols_map.get('c', df.columns[3]),
                   cols_map.get('v', df.columns[4] if len(df.columns) > 4 else df.columns[0])]].values[-n:]
        return rows.tolist()
    except Exception as e:
        print(f'  [parquet] 加载失败: {e}')
        return []


def validate_s23_distribution():
    """
    主验证函数：
    检验 Kronos-Lite s23 在已知结果信号上的方向有效性
    """
    print("\n" + "="*60)
    print("  达摩院 · s23 快速离线验证")
    print(f"  时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("="*60 + "\n")

    from kronos_lite import get_s23_score, _CACHE

    # ① 加载历史信号
    signals = load_live_signals()
    print(f"加载已结算信号: {len(signals)} 条")

    if len(signals) < 10:
        print("⚠️  信号数量不足，尝试从replay结果加载...\n")
        # 尝试从最新replay报告加载
        results_dir = BASE / 'dharma' / 'results'
        replay_files = sorted(results_dir.glob('replay_report_*.json'))
        if replay_files:
            with open(replay_files[-1]) as f:
                data = json.load(f)
            print(f"使用离线回放报告: {replay_files[-1].name}")
            print(f"回放信号数: {data.get('total_signals', 'N/A')}")
            # 从报告中提取体制×方向WR矩阵
            regime_stats = data.get('regime_direction_stats', {})
            if regime_stats:
                print("\n体制×方向 WR矩阵（离线回放铁证）:")
                for k, v in sorted(regime_stats.items()):
                    wr = v.get('wr', 0)
                    n  = v.get('n', 0)
                    ev = v.get('avg_pnl', 0)
                    bar = '█' * min(int(wr/5), 20)
                    print(f"  {k:<30} WR={wr:5.1f}%  n={n:5d}  EV={ev:+.3f}%  {bar}")
        return

    # ② 对每条信号计算 s23
    print("\n计算 s23 分布...\n")

    buckets = {
        's23_positive': {'wins': 0, 'total': 0},   # s23>0 时的胜负
        's23_negative': {'wins': 0, 'total': 0},   # s23<0 时的胜负
        's23_zero':     {'wins': 0, 'total': 0},   # s23=0 时的胜负
    }
    regime_buckets = {}

    for sig in signals[:500]:  # 最多500条，避免太慢
        symbol    = sig.get('symbol', 'BTCUSDT')
        direction = sig.get('signal_dir') or sig.get('direction', 'LONG')
        regime    = sig.get('regime', '')
        outcome   = sig.get('outcome') or sig.get('result', '')
        is_win    = outcome.upper() in ('WIN', 'TP', 'PROFIT', 'TP1', 'TP2')

        # 获取 K线（用 parquet 历史数据近似）
        _CACHE.clear()  # 清缓存确保独立计算
        klines = load_parquet_klines(symbol, '15m', 200)
        if len(klines) < 60:
            continue

        score, meta = get_s23_score(symbol, direction, klines, regime)

        # 分桶统计
        if score > 0:
            bucket_key = 's23_positive'
        elif score < 0:
            bucket_key = 's23_negative'
        else:
            bucket_key = 's23_zero'

        buckets[bucket_key]['total'] += 1
        if is_win:
            buckets[bucket_key]['wins'] += 1

        # 体制分桶
        rk = f"{regime}_{direction}"
        if rk not in regime_buckets:
            regime_buckets[rk] = {'wins': 0, 'total': 0,
                                   's23_sum': 0, 's23_count': 0}
        regime_buckets[rk]['total'] += 1
        if is_win:
            regime_buckets[rk]['wins'] += 1
        if score != 0:
            regime_buckets[rk]['s23_sum'] += score
            regime_buckets[rk]['s23_count'] += 1

    # ③ 打印结果
    print("━"*50)
    print("  s23 方向有效性验证结果")
    print("━"*50)
    total_processed = sum(b['total'] for b in buckets.values())
    print(f"  处理信号数: {total_processed}\n")

    for bk, bv in buckets.items():
        n = bv['total']
        if n == 0:
            continue
        wr = bv['wins'] / n * 100
        label = {'s23_positive': 's23>0（方向支持）',
                 's23_negative': 's23<0（方向反对）',
                 's23_zero':     's23=0（中性）'}[bk]
        bar = '█' * min(int(wr/5), 20)
        print(f"  {label:<24} n={n:3d}  WR={wr:5.1f}%  {bar}")

    # 关键指标：s23>0的WR是否>s23<0的WR？
    pos = buckets['s23_positive']
    neg = buckets['s23_negative']
    if pos['total'] > 0 and neg['total'] > 0:
        wr_pos = pos['wins'] / pos['total'] * 100
        wr_neg = neg['wins'] / neg['total'] * 100
        diff = wr_pos - wr_neg
        verdict = "✅ s23有效（正向信号WR更高）" if diff > 2 else \
                  "⚠️  s23弱效（差异<2%，继续观察）" if diff >= 0 else \
                  "❌ s23无效（正向信号WR反而更低，需检查）"
        print(f"\n  达摩院裁定：{verdict}")
        print(f"  WR差值：{diff:+.1f}% (s23>0 vs s23<0)")

    print("\n━"*50)
    print("  体制分布摘要（n≥5才显示）")
    print("━"*50)
    for rk, rv in sorted(regime_buckets.items()):
        n = rv['total']
        if n < 5:
            continue
        wr = rv['wins'] / n * 100
        avg_s23 = rv['s23_sum'] / rv['s23_count'] if rv['s23_count'] > 0 else 0
        print(f"  {rk:<30} n={n:3d}  WR={wr:5.1f}%  avg_s23={avg_s23:+.1f}")

    # ④ 达摩院样本分级报告
    n_total = total_processed
    level = "❌无效(n<30)" if n_total < 30 else \
            "⚠️参考(n<100)" if n_total < 100 else \
            "🟡次级(n<500)" if n_total < 500 else \
            "✅次铁证(n<1000)" if n_total < 1000 else "🏆铁证(n≥1000)"
    print(f"\n  样本分级: {level}")
    print(f"  → 当前仅供观察，不得修改系统参数")
    print(f"  → 达到n≥100后可作为'次级'参考\n")

    # ⑤ 保存报告
    report = {
        "timestamp": TAG,
        "total_processed": total_processed,
        "buckets": buckets,
        "regime_stats": {k: {**v, "wr": v['wins']/v['total']*100 if v['total']>0 else 0}
                         for k, v in regime_buckets.items()},
        "verdict": "s23 快速验证完成",
    }
    out_path = BASE / 'dharma' / 'results' / f's23_validation_{TAG}.json'
    with open(out_path, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"  报告保存: {out_path}")


if __name__ == '__main__':
    validate_s23_distribution()
