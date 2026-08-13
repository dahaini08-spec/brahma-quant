#!/usr/bin/env python3
"""
trajectory_distiller.py — 梵天轨迹经验蒸馏器 v1.0
[设计院封印 2026-08-13 苏摩111]

功能：
  - 读取 data/trajectories/settled.jsonl
  - 按 task_family(体制) + direction 聚合
  - 提取成功模式和失败模式
  - 输出 data/trajectories/experience_docs.jsonl（可接入Qdrant方仓）
  - 规则：2条以上非TIMEOUT支持 → 正式经验；1条 → 草案

用法：
  python3 scripts/trajectory_distiller.py
  python3 scripts/trajectory_distiller.py --min-samples 3
"""
import json
import argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
TRAJ_FILE = BASE / 'data' / 'trajectories' / 'settled.jsonl'
EXP_FILE  = BASE / 'data' / 'trajectories' / 'experience_docs.jsonl'


def load_trajectories() -> list[dict]:
    if not TRAJ_FILE.exists():
        return []
    records = []
    for line in TRAJ_FILE.read_text().strip().splitlines():
        try:
            records.append(json.loads(line))
        except Exception:
            pass
    return records


def distill(trajectories: list[dict], min_samples: int = 2) -> list[dict]:
    """按体制+方向聚合，提取经验文档"""
    groups = defaultdict(list)
    for t in trajectories:
        key = f"{t.get('regime','?')}:{t.get('direction','?')}"
        groups[key].append(t)

    docs = []
    for key, records in groups.items():
        regime, direction = key.split(':', 1)
        tp_records  = [r for r in records if r.get('outcome') in ('TP1','TP','WIN')]
        sl_records  = [r for r in records if r.get('outcome') in ('SL','LOSS')]
        to_records  = [r for r in records if r.get('outcome') not in ('TP1','TP','WIN','SL','LOSS')]
        valid_n = len(tp_records) + len(sl_records)
        total_n = valid_n + len(to_records)

        if total_n == 0:
            continue

        wr = len(tp_records) / valid_n if valid_n > 0 else None
        avg_pnl = sum(r.get('pnl_pct', 0) for r in records) / len(records)
        avg_score = sum(r.get('matrix_score', 0) for r in records) / len(records)

        # 提取成功/失败模式
        success_scores = [r.get('matrix_score', 0) for r in tp_records]
        fail_scores    = [r.get('matrix_score', 0) for r in sl_records]

        doc = {
            'id':                   f"exp_{regime}_{direction}_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
            'task_family':          regime,
            'direction':            direction,
            'n_total':              len(records),
            'n_tp':                 len(tp_records),
            'n_sl':                 len(sl_records),
            'n_timeout':            len(to_records),
            'win_rate':             round(wr, 3) if wr is not None else None,
            'avg_pnl_pct':          round(avg_pnl, 4),
            'avg_matrix_score':     round(avg_score, 1),
            'status':               'confirmed' if valid_n >= min_samples else ('pending' if to_records else 'draft'),
            'applies_when':         [f"regime={regime}", f"direction={direction}"],
            'observed_strategies':  [f"WR={wr:.1%} n={valid_n}" if wr is not None else f"n={total_n} pending(no settled)"],
            'success_score_range':  [round(min(success_scores),1), round(max(success_scores),1)] if success_scores else [],
            'failure_score_range':  [round(min(fail_scores),1), round(max(fail_scores),1)] if fail_scores else [],
            'exceptions':           ['low_liquidity_hours'] if to_records else [],
            'last_updated':         datetime.now(timezone.utc).isoformat(),
        }
        docs.append(doc)
        status_tag = '✅确认' if doc['status'] == 'confirmed' else ('⏳待结算' if doc['status'] == 'pending' else '📝草案')
        wr_str = f'{wr:.1%}' if wr is not None else 'pending'
        print(f"  {status_tag} {regime}:{direction} WR={wr_str} n={valid_n}(+{len(to_records)}expired) avgPnL={avg_pnl:.3f}%")

    return docs


def main():
    parser = argparse.ArgumentParser(description='梵天轨迹经验蒸馏器')
    parser.add_argument('--min-samples', type=int, default=2, help='最小样本数（默认2）')
    args = parser.parse_args()

    trajectories = load_trajectories()
    print(f"[轨迹蒸馏] 读取 {len(trajectories)} 条结算记录")

    if not trajectories:
        print("[INFO] 暂无轨迹数据，等待信号结算后再运行")
        return

    docs = distill(trajectories, min_samples=args.min_samples)

    EXP_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXP_FILE.write_text('\n'.join(json.dumps(d, ensure_ascii=False) for d in docs) + '\n')
    print(f"[完成] 输出 {len(docs)} 条经验文档 → {EXP_FILE}")


if __name__ == '__main__':
    main()
